#!/usr/bin/env python
# encoding: utf-8
#    Copyright 2016 Andrei Macavei <andrei.macavei89@gmail.com>
#
#    This file is part of GNOME Keysign.
#
#    GNOME Keysign is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    GNOME Keysign is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with GNOME Keysign.  If not, see <http://www.gnu.org/licenses/>.

import sys
import logging
import os
import re

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')

from gi.repository import Gtk, Gst, GdkPixbuf
from gi.repository import GLib, GObject


if  __name__ == "__main__" and __package__ is None:
    logging.getLogger().error("You seem to be trying to execute " +
                              "this script directly which is discouraged. " +
                              "Try python -m instead.")
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.sys.path.insert(0, parent_dir)
    import keysign
    #mod = __import__('keysign')
    #sys.modules["keysign"] = mod
    __package__ = str('keysign')

from .scan_barcode import BarcodeReaderGTK
from . import camera_portal

log = logging.getLogger(__name__)

# Matches "IR", "infrared" or "infra-red" as whole words, so we don't
# misfire on ordinary camera names that merely happen to contain the
# letters "ir", e.g. "Wireless Webcam" or "Circle View Camera".
IR_CAMERA_NAME_RE = re.compile(r'\b(ir|infra-?red)\b', re.IGNORECASE)



class KeyFprScanWidget(Gtk.Box):
    """A widget for obtaining a key fingerprint.

    The fingerprint can be obtain by inserting it into
    a text entry, or by scanning a barcode with the
    built-in camera.
    """

    __gsignals__ = {
        # This is the Gtk widget signal's name
        str('changed'): (GObject.SignalFlags.RUN_LAST, None,
                        (GObject.TYPE_PYOBJECT,)),
        # It's probably not the best name for that signal.
        # While "barcode_scanned" might be better, it is probably
        # unnecessarily specific.
        str('barcode'): (GObject.SignalFlags.RUN_LAST, None,
                        (str, # The barcode string
                         Gst.Message.__gtype__, # The GStreamer message itself
                         GdkPixbuf.Pixbuf.__gtype__,),) # The pixbuf which caused
                                              # the above string to be decoded
    }

    def __init__(self, builder=None):
        log.debug("Init KFSW %r %r", self, builder)
        super(KeyFprScanWidget, self).__init__(orientation=Gtk.Orientation.VERTICAL)
        log.debug("Inited parent KFSW %r", self)

        widget_name = 'scanner_widget'
        if not builder:
            thisdir = os.path.dirname(os.path.abspath(__file__))
            builder = Gtk.Builder()
            builder.add_objects_from_file(os.path.join(thisdir, 'receive4.ui'),
                [widget_name])
        widget = builder.get_object(widget_name)
        parent = widget.get_parent()
        if parent:
            parent.remove(widget)
        self.append(widget)
        

        self.scanner = builder.get_object("scanner")

        if not Gst.is_initialized():
            log.error("Gst does not seem to be initialised. Call Gst.init()!")
            # This needs to be called before creating a BarcodeReaderGTK
            Gst.init(None)
        reader = BarcodeReaderGTK()
        reader.set_size_request(150,150)
        reader.connect('barcode', self.on_barcode)
        reader.connect('stream-stalled', self._on_stream_stalled)
        self.scanner.append(reader)
        # We keep a reference here to not "lose" the object.
        # If we don't, Gtk crashes. With a segfault. Probably
        # because the object is freed but still used.
        # Somebody should look at that...
        self.reader = reader

        self.camera_selector = builder.get_object("comboboxtext1")
        if self.camera_selector:
            self.camera_selector.connect("changed", self.on_camera_changed)
        self.camera_box = builder.get_object("box40")
        self.camera_devices = {}
        
        self._using_portal = False
        self._portal_requested = False
        self._pipewire_fd = None
        if camera_portal._using_flatpak() and camera_portal.is_camera_portal_available():
            log.info("Running in Flatpak and Camera Portal is available")
            self._using_portal = True
            # Hidden until the portal says what there is to choose from.
            if self.camera_box:
                self.camera_box.set_visible(False)
            self.connect('map', self._on_map)
        else:
            # Legacy path: enumerate devices with Gst.DeviceMonitor
            if self.camera_selector:
                self.populate_cameras()

        self.fpr_entry = builder.get_object("fingerprint_entry")
        self.fpr_entry.connect('changed', self.on_text_changed)

        self.set_hexpand(True)
        self.set_vexpand(True)

        # Temporary measure...
        self.barcode_scanner = self

    def _on_map(self, *args):
        if self._portal_requested:
            return
        window = self.get_root()
        if not self._request_camera_access(window):
            window.connect('notify::is-active', self._on_window_active)

    def _on_window_active(self, window, pspec):
        if self._request_camera_access(window):
            window.disconnect_by_func(self._on_window_active)

    def _request_camera_access(self, window):
        """Ask the portal for camera access, if we can do so right now.

        GNOME Shell shows the dialog only for the focused app and otherwise
        refuses with a denial that looks exactly like the user saying no.
        """
        if self._portal_requested or not window.is_active():
            return False
        self._portal_requested = True
        log.info("Window is focused, requesting Camera Portal access")
        camera_portal.request_camera_access(self._on_camera_portal_response)
        return True

    def _on_camera_portal_response(self, success, pipewire_fd):
        """Called when the Camera Portal responds to our access request."""
        if success and pipewire_fd is not None:
            log.info("Camera Portal granted access, fd=%d", pipewire_fd)
            GLib.idle_add(self._setup_portal_cameras, pipewire_fd)
        else:
            log.warning("Camera Portal denied or failed, falling back to "
                        "direct device access.")
            self._using_portal = False
            GLib.idle_add(self._fallback_to_device_monitor)

    def _setup_portal_cameras(self, fd):
        """Offer the cameras reachable through the portal's PipeWire fd."""
        self._pipewire_fd = fd
        cameras = self._portal_cameras(fd)
        if not cameras:
            log.warning("The portal exposed no camera we can name, "
                        "letting PipeWire pick one")
            self.reader.set_pipewire_fd(fd)
            return
        if self.camera_box:
            self.camera_box.set_visible(True)
        self._fill_camera_selector(cameras)

    def _portal_cameras(self, fd):
        """List the cameras on the portal's PipeWire connection.

        The portal hands out a connection restricted to cameras but does
        not choose one for us. Capturing without naming a node gets us
        PipeWire's default, which is ordered by priority.session and is
        happy to rank an infrared camera first.
        """
        factory = Gst.DeviceProviderFactory.find('pipewiredeviceprovider')
        provider = factory.get()
        provider.set_property('fd', fd)
        provider.start()
        devices = provider.get_devices()
        provider.stop()

        cameras = []
        seen = set()
        for device in devices:
            props = device.get_properties()
            node_name = props.get_string('node.name') if props else None
            # The provider lists every node twice.
            if not node_name or node_name in seen:
                continue
            seen.add(node_name)
            cameras.append((device.get_display_name(),
                            props.get_string('api.v4l2.path') or node_name,
                            node_name))
        return cameras

    def _on_stream_stalled(self, reader):
        """Open a device ourselves, the portal's stream having stayed blank.

        The stream negotiates a format, delivers a frame or two and then
        nothing, so take the camera the ordinary way instead.
        """
        if not self._using_portal:
            return
        log.warning("Camera Portal stream stalled, falling back to "
                    "direct device access.")
        self._using_portal = False
        self._fallback_to_device_monitor()

    def _select_camera(self, value):
        if self._using_portal:
            self.reader.set_pipewire_fd(self._pipewire_fd, value)
        else:
            self.reader.set_device(value)

    def _fallback_to_device_monitor(self):
        """Fall back to legacy Gst.DeviceMonitor enumeration."""
        if self.camera_box:
            self.camera_box.set_visible(True)
        if self.camera_selector:
            self.populate_cameras()

    def populate_cameras(self):
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter("Video/Source", None)
        monitor.start()
        devices = monitor.get_devices()
        monitor.stop()

        cameras = []
        for device in devices:
            display_name = device.get_display_name()
            props = device.get_properties()
            device_path = None
            # On a PipeWire-enabled desktop (the default since Ubuntu 22.04),
            # Video/Source devices come from GStreamer's pipewire device
            # provider, which reports the actual v4l2 devnode under
            # "api.v4l2.path" (not the "device.path" key a standalone v4l2
            # provider would use). "object.path" is a fallback for older or
            # differently configured setups: it holds a "v4l2:"-prefixed
            # URI rather than a plain devnode, so we strip that prefix.
            # Requiring device.api == "v4l2" keeps out any non-v4l2 source
            # (e.g. a purely virtual PipeWire camera) that happens to expose
            # one of these keys without actually being backed by v4l2.
            if props and props.get_string("device.api") == "v4l2":
                device_path = (props.get_string("api.v4l2.path")
                                or props.get_string("device.path"))
                if not device_path:
                    object_path = props.get_string("object.path")
                    if object_path and object_path.startswith("v4l2:"):
                        device_path = object_path[len("v4l2:"):]

            if not device_path:
                continue

            cameras.append((display_name, device_path, device_path))

        self._fill_camera_selector(cameras)

    def _fill_camera_selector(self, cameras):
        """Fill the dropdown and pre-select the most promising camera.

        Each camera is a (display name, v4l2 path, value) triple, where
        the value is what identifies it to the reader afterwards: a device
        path when we open it ourselves, a PipeWire node name when the
        portal does.
        """
        self.camera_selector.remove_all()

        self.camera_devices = {}
        best_suitable_idx = -1
        best_suitable_v4l2 = -1

        best_unsuitable_idx = -1
        best_unsuitable_v4l2 = -1

        def get_v4l2_index(path):
            if not path:
                return -1
            m = re.search(r'\d+$', path)
            return int(m.group(0)) if m else -1

        for display_name, device_path, value in cameras:
            is_unsuitable = bool(IR_CAMERA_NAME_RE.search(display_name))

            v4l2_num = get_v4l2_index(device_path)
            current_idx = len(self.camera_devices)
            item_id = str(current_idx)
            self.camera_devices[item_id] = value

            if is_unsuitable:
                label = f"⚠️ {display_name} ({device_path}) [IR / Unsuitable]"
                if v4l2_num > best_unsuitable_v4l2:
                    best_unsuitable_v4l2 = v4l2_num
                    best_unsuitable_idx = current_idx
            else:
                label = f"{display_name} ({device_path})"
                if v4l2_num > best_suitable_v4l2:
                    best_suitable_v4l2 = v4l2_num
                    best_suitable_idx = current_idx
            
            self.camera_selector.append(item_id, label)
            
        if self.camera_devices:
            if best_suitable_idx != -1:
                default_index = best_suitable_idx
            elif best_unsuitable_idx != -1:
                default_index = best_unsuitable_idx
            else:
                default_index = 0
            self.camera_selector.set_active(default_index)
            default_value = self.camera_devices.get(str(default_index))
            if default_value:
                self._select_camera(default_value)

    def on_camera_changed(self, combo):
        active_id = combo.get_active_id()
        if active_id and active_id in self.camera_devices:
            value = self.camera_devices[active_id]
            log.info("Camera changed in dropdown to ID %s: %s", active_id, value)
            self._select_camera(value)

    def on_text_changed(self, entryObject, *args):
        self.emit('changed', entryObject, *args)

    def on_barcode(self, sender, barcode, message, image):
        self.emit('barcode', barcode, message, image)

    def get_text(self):
        "Returns the text present in the Entry"
        text = self.fpr_entry.get_text()
        return text

class KeyScanApp(Gtk.Application):
    def __init__(self, *args, **kwargs):
        super(KeyScanApp, self).__init__(*args, **kwargs)
        self.connect('activate', self.on_activate)
        self.scanwidget = None

        self.log = logging.getLogger(__name__)

    def on_activate(self, app):
        window = Gtk.ApplicationWindow()
        window.set_title("Key Fingerprint Scanner Widget")
        window.set_size_request(600, 400)

        if not self.scanwidget:
            self.scanwidget = KeyFprScanWidget()
        self.scanwidget.connect('changed', self.on_text_changed)
        self.scanwidget.connect('barcode', self.on_barcode)
        window.set_child(self.scanwidget)
        window.present()
        self.add_window(window)

    def on_text_changed(self, keyFprScanWidget, entryObject, *args):
        self.log.debug ("Text changed! %s" % (entryObject.get_text(),))

    def on_barcode(self, sender, barcode, message, image):
        self.log.debug ("Barcode signal %r %r", barcode, message)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)
    Gst.init(None)
    app = KeyScanApp()
    app.run()
