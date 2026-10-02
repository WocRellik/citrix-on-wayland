#!/usr/bin/env python3
"""Bridge text copied in native Wayland apps to the X11 clipboard for Citrix.

Problem: KWin hands the Wayland clipboard to Xwayland clients using the mime
types the source app offered. GTK4 and Qt apps only offer ``text/plain``
variants. Citrix (wfica, an X11 client) pastes the classic ``STRING`` target
and only works when the owner also answers the other text targets it probes
(``UTF8_STRING``, ``COMPOUND_TEXT``, ...). Pasting from those apps into Citrix
therefore does nothing; Chromium/Electron apps work because they offer the X11
targets themselves.

Approach: whenever Klipper reports a clipboard change, compare Klipper's text
with the text on the X11 CLIPBOARD selection. If they differ, this process
takes ownership of the X11 CLIPBOARD and serves the text in every common text
target. KWin mirrors it back to Wayland, so every app keeps seeing the same
text. Comparing content (rather than checking whether the selection has an
owner) is needed because KWin does not always release the X11 selection when
a Wayland app copies. When the texts already match (e.g. after copying inside
Citrix, or after our own selection was mirrored back), nothing is done; this
also prevents feedback loops.

Only text is bridged. Citrix only reads STRING, so text arriving in Citrix is
limited to Windows-1252 (Latin-1 plus euro sign, smart quotes, dashes);
other characters such as emoji become '?'. The clipboard content is never
logged.

Requirements: Plasma (Klipper D-Bus service), xclip, python3-gi, python3-xlib.
"""

import logging
import subprocess
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402
from Xlib import X, Xatom, display, error  # noqa: E402
from Xlib.protocol import event  # noqa: E402

KLIPPER_BUS_NAME = "org.kde.klipper"
KLIPPER_OBJECT_PATH = "/klipper"
KLIPPER_INTERFACE = "org.kde.klipper.klipper"
KLIPPER_CHANGED_SIGNAL = "clipboardHistoryUpdated"
KLIPPER_GET_METHOD = "getClipboardContents"

# Klipper can emit several signals for one copy action; handle them once.
DEBOUNCE_MILLISECONDS = 150
DBUS_TIMEOUT_MILLISECONDS = 2000
XCLIP_READ_COMMAND = ["xclip", "-selection", "clipboard", "-out",
                      "-target", "UTF8_STRING"]
# An unresponsive selection owner must not block the bridge.
XCLIP_READ_TIMEOUT_SECONDS = 1
# python-xlib may read events while handling other requests; those never make
# the socket readable again, so drain the queue periodically as well.
X11_DRAIN_INTERVAL_MILLISECONDS = 500

# Text targets served, with the encoding of each. COMPOUND_TEXT is Latin-1 per
# ICCCM. STRING is the only target Citrix reads, and it passes the bytes on to
# Windows unchanged, so it is encoded as Windows-1252: identical to Latin-1 for
# Latin-1 text, but it also carries the euro sign, smart quotes and dashes.
# Characters outside the encoding become '?'.
UTF8_ENCODING = "utf-8"
LATIN1_ENCODING = "latin-1"
WINDOWS_1252_ENCODING = "cp1252"
TEXT_TARGET_ENCODINGS = {
    "UTF8_STRING": UTF8_ENCODING,
    "STRING": WINDOWS_1252_ENCODING,
    "TEXT": UTF8_ENCODING,
    "COMPOUND_TEXT": LATIN1_ENCODING,
    "text/plain;charset=utf-8": UTF8_ENCODING,
    "text/plain": UTF8_ENCODING,
}
# TEXT is a request for "any text type"; answer it as UTF8_STRING.
TEXT_TARGET_REPLY_TYPES = {"TEXT": "UTF8_STRING"}
PROPERTY_FORMAT_BYTES = 8
PROPERTY_FORMAT_ATOMS = 32
# Leave room for the ChangeProperty request header in the size check.
REQUEST_HEADER_BYTES = 24

log = logging.getLogger("citrix-clipboard-bridge")


class X11ClipboardOwner:
    """Own the X11 CLIPBOARD selection and serve text in all text targets."""

    def __init__(self) -> None:
        """Connect to $DISPLAY and create the (unmapped) owner window.

        Raises:
            Xlib.error.DisplayError: if the X11 display cannot be opened.
        """
        self._display = display.Display()
        self._window = self._display.screen().root.create_window(
            0, 0, 1, 1, 0, X.CopyFromParent)
        self._clipboard = self._display.intern_atom("CLIPBOARD")
        self._targets = self._display.intern_atom("TARGETS")
        self._timestamp = self._display.intern_atom("TIMESTAMP")
        self._text_targets = {
            self._display.intern_atom(name): (
                encoding,
                self._display.intern_atom(
                    TEXT_TARGET_REPLY_TYPES.get(name, name)),
            )
            for name, encoding in TEXT_TARGET_ENCODINGS.items()
        }
        # 4-byte units, as reported by the server.
        self._max_property_bytes = (
            self._display.display.info.max_request_length * 4
            - REQUEST_HEADER_BYTES)
        self._text = ""
        self._owns_selection = False

    @property
    def owned_text(self) -> str | None:
        """Text currently served, or None if another client owns CLIPBOARD."""
        return self._text if self._owns_selection else None

    def fileno(self) -> int:
        """Return the X11 connection socket, for the main loop to watch."""
        return self._display.fileno()

    def set_text(self, text: str) -> bool:
        """Take CLIPBOARD ownership and serve `text`.

        Args:
            text: the text to offer to X11 clients.

        Returns:
            True if ownership was obtained, False otherwise (e.g. the text is
            too large to send without the INCR protocol).
        """
        if len(text.encode(UTF8_ENCODING)) > self._max_property_bytes:
            log.warning("text too large to bridge (%d characters)", len(text))
            return False

        self._text = text
        self._window.set_selection_owner(self._clipboard, X.CurrentTime)
        self._display.sync()
        self._owns_selection = (
            self._display.get_selection_owner(self._clipboard)
            == self._window)
        self.process_events()
        return self._owns_selection

    def process_events(self, *_watch_arguments: object) -> bool:
        """Answer queued selection requests. Returns True to keep watching."""
        while self._display.pending_events():
            x_event = self._display.next_event()
            if x_event.type == X.SelectionRequest:
                self._answer_request(x_event)
            elif x_event.type == X.SelectionClear:
                self._owns_selection = False
                self._text = ""
        return True

    def _answer_request(self, request: event.SelectionRequest) -> None:
        """Write the requested target to the requestor and notify it."""
        reply_property = request.property or request.target
        try:
            if not self._owns_selection:
                reply_property = X.NONE
            elif request.target == self._targets:
                request.requestor.change_property(
                    reply_property, Xatom.ATOM, PROPERTY_FORMAT_ATOMS,
                    [self._targets, self._timestamp, *self._text_targets])
            elif request.target == self._timestamp:
                request.requestor.change_property(
                    reply_property, Xatom.INTEGER, PROPERTY_FORMAT_ATOMS,
                    [X.CurrentTime])
            elif request.target in self._text_targets:
                encoding, reply_type = self._text_targets[request.target]
                data = self._text.encode(encoding, errors="replace")
                request.requestor.change_property(
                    reply_property, reply_type, PROPERTY_FORMAT_BYTES, data)
            else:
                reply_property = X.NONE
        except error.XError as x_error:
            log.warning("cannot answer selection request: %s", x_error)
            reply_property = X.NONE

        notify = event.SelectionNotify(
            time=request.time,
            requestor=request.requestor,
            selection=request.selection,
            target=request.target,
            property=reply_property,
        )
        request.requestor.send_event(notify)
        self._display.flush()


class ClipboardBridge:
    """Mirror Klipper's current text to X11 when the X11 text differs."""

    def __init__(self, bus: Gio.DBusConnection,
                 x11_owner: X11ClipboardOwner) -> None:
        """Store the session bus and the X11 selection owner.

        Args:
            bus: connection to the D-Bus session bus.
            x11_owner: the object serving the X11 CLIPBOARD selection.
        """
        self._bus = bus
        self._x11_owner = x11_owner
        self._pending_timeout_id = 0

    def subscribe(self) -> None:
        """Listen for Klipper clipboard change signals."""
        self._bus.signal_subscribe(
            KLIPPER_BUS_NAME,
            KLIPPER_INTERFACE,
            KLIPPER_CHANGED_SIGNAL,
            KLIPPER_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_clipboard_changed,
        )

    def _on_clipboard_changed(self, *_signal_arguments: object) -> None:
        """Schedule a sync, collapsing bursts of signals into one."""
        if self._pending_timeout_id:
            GLib.source_remove(self._pending_timeout_id)
        self._pending_timeout_id = GLib.timeout_add(
            DEBOUNCE_MILLISECONDS, self._sync)

    def _sync(self) -> bool:
        """Bridge the clipboard if needed. Returns False to stop the timer."""
        self._pending_timeout_id = 0
        text = self._read_klipper_text()
        if not text or text == self._current_x11_text():
            return False

        if self._x11_owner.set_text(text):
            log.info("bridged %d characters to X11", len(text))
        return False

    def _current_x11_text(self) -> str:
        """Return the text X11 clients currently get from CLIPBOARD.

        When this process owns the selection the stored text is used: reading
        it through xclip would block on our own (paused) event loop.
        """
        owned_text = self._x11_owner.owned_text
        if owned_text is not None:
            return owned_text
        return self._read_x11_clipboard()

    def _read_klipper_text(self) -> str:
        """Return the current clipboard text from Klipper ('' on failure)."""
        try:
            result = self._bus.call_sync(
                KLIPPER_BUS_NAME,
                KLIPPER_OBJECT_PATH,
                KLIPPER_INTERFACE,
                KLIPPER_GET_METHOD,
                None,
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                DBUS_TIMEOUT_MILLISECONDS,
                None,
            )
        except GLib.Error as glib_error:
            log.warning("cannot read clipboard from Klipper: %s",
                        glib_error.message)
            return ""
        return result.unpack()[0]

    @staticmethod
    def _read_x11_clipboard() -> str:
        """Return the text on the X11 CLIPBOARD selection ('' if none)."""
        try:
            result = subprocess.run(
                XCLIP_READ_COMMAND,
                capture_output=True,
                timeout=XCLIP_READ_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as read_error:
            log.warning("cannot read X11 clipboard: %s", read_error)
            return ""
        return result.stdout.decode(UTF8_ENCODING, errors="replace")


def main() -> int:
    """Run the bridge until terminated. Returns the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        x11_owner = X11ClipboardOwner()
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except error.DisplayError as display_error:
        log.error("cannot open X11 display: %s", display_error)
        return 1
    except GLib.Error as glib_error:
        log.error("cannot connect to the session bus: %s", glib_error.message)
        return 1

    GLib.io_add_watch(x11_owner.fileno(), GLib.PRIORITY_DEFAULT,
                      GLib.IOCondition.IN, x11_owner.process_events)
    GLib.timeout_add(X11_DRAIN_INTERVAL_MILLISECONDS,
                     x11_owner.process_events)
    ClipboardBridge(bus, x11_owner).subscribe()
    log.info("listening for Klipper clipboard changes")
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
