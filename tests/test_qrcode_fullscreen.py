"""Tests for the fullscreen QR code window.

The fullscreen state has to be requested *before* the window is first
presented. On GTK4's X11 backend the fullscreen bit is part of the
GdkToplevelLayout handed to gdk_toplevel_present(), and a fullscreen()
issued after the first present(), but before the window has been mapped
and configured by the window manager, is simply lost: the layout that
reaches the server is the pre-fullscreen one. The window then shows up
at its natural size (the QR bitmap is only a few dozen pixels) in the
corner of the screen.

That matters in practice because the Flatpak runs under XWayland. On
the Wayland backend the ordering makes no difference, since fullscreen
is negotiated through xdg_toplevel configure round-trips.

None of the headless backends available in CI reproduce this (broadway
never acknowledges the fullscreen state at all, and a lightweight X11
WM happens to honour the late request), so the ordering itself is what
gets pinned here.
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

from keysign.QRCode import FullscreenQRImageWindow

DATA = "OPENPGP4FPR:A2E97B5573D25E5B4D5AD66EDF71C6E43409E985"


def test_fullscreen_is_requested_before_the_window_is_presented(monkeypatch):
    calls = []
    monkeypatch.setattr(Gtk.Window, 'fullscreen',
                        lambda self: calls.append('fullscreen'))
    monkeypatch.setattr(Gtk.Window, 'present',
                        lambda self: calls.append('present'))

    FullscreenQRImageWindow(data=DATA)

    assert calls == ['fullscreen', 'present'], (
        "fullscreen() must be requested before the first present(), "
        "otherwise the X11 backend drops it and the window opens at the "
        "QR code's natural size instead of fullscreen"
    )
