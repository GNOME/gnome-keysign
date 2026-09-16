"""Tests for the QR code widget and its little demo program."""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

from keysign.QRCode import QRImage, build_window

DATA = "OPENPGP4FPR:A2E97B5573D25E5B4D5AD66EDF71C6E43409E985"


def test_the_demo_window_shows_the_data_as_a_qr_code():
    """"python3 keysign/QRCode.py <data>" shows the data as a QR code.

    The GTK4 port left this demo calling Gtk.main_quit(), which does not
    exist in GTK4, so running it raised AttributeError before it ever
    got a window up.
    """
    window = build_window(DATA)

    qrcode = window.get_child()
    assert isinstance(qrcode, QRImage)
    assert qrcode.data == DATA
