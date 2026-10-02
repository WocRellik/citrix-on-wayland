# citrix-on-wayland

Copy+paste bridge for using Citrix Workspace app on a KDE Plasma Wayland
session.

On Plasma (Wayland), copying text in a native Wayland app such as GNOME Text
Editor, Kate or KWrite and pasting it into a Citrix session does nothing.
Copying *from* Citrix to local apps works, and copying from Chromium/Electron
apps (Firefox, Obsidian, ...) into Citrix also works. This bridge makes every
app work.

## Why it happens

- Citrix Workspace for Linux (`wfica`) is an X11 application and runs through
  Xwayland.
- When pasting, Citrix only asks the X11 clipboard for the classic `STRING`
  target. It never requests `UTF8_STRING`, `TEXT`, `COMPOUND_TEXT`,
  `text/plain`, `UTF16_STRING` or its own `_ISL_UNICODE`, even when offered.
- GTK4 and Qt apps only put `text/plain` and `text/plain;charset=utf-8` on the
  Wayland clipboard. KWin passes those through to Xwayland without adding
  `STRING`, so Citrix finds nothing it can use.
- Chromium/Electron apps offer `STRING` themselves, which is why they work.

## How the bridge works

1. It listens to Klipper's `clipboardHistoryUpdated` D-Bus signal.
2. On every change it compares Klipper's current text with the text on the X11
   `CLIPBOARD` selection.
3. If they differ, the bridge takes ownership of the X11 `CLIPBOARD` itself and
   serves the text as `STRING`, `UTF8_STRING`, `TEXT`, `COMPOUND_TEXT`,
   `text/plain;charset=utf-8` and `text/plain`.
4. KWin mirrors that selection back to Wayland, so all apps keep seeing the
   same text. If the texts already match (for example after copying inside
   Citrix), the bridge does nothing, which also prevents feedback loops.

`STRING` is encoded as Windows-1252. Citrix hands the bytes to Windows as-is,
so the euro sign, smart quotes, en/em dashes and the ellipsis arrive
correctly. For plain Latin-1 text this is identical to the ICCCM Latin-1
encoding.

The clipboard content is never logged; only the number of characters bridged.

## Requirements

- KDE Plasma 6 on Wayland, with Klipper (the Plasma clipboard) enabled
- `xclip`, `python3-gi`, `python3-xlib`

```bash
sudo apt install xclip python3-gi python3-xlib
```

## Installation

```bash
git clone https://github.com/WocRellik/citrix-on-wayland.git
cd citrix-on-wayland
./install.sh
```

This installs `~/.local/bin/citrix-clipboard-bridge` and a systemd user
service that starts with every Wayland session. No root is needed.

Check that it works:

```bash
journalctl --user -u citrix-clipboard-bridge -f
```

Every local copy action should log one `bridged N characters to X11` line.

## Uninstall

```bash
./uninstall.sh
```

## Limitations

- Text only; images and files are not bridged.
- Characters outside Windows-1252 (emoji, CJK, symbols such as ✓) arrive in
  Citrix as `?`. Citrix only reads `STRING`, so this cannot be fixed on the
  Linux side.
- Clipboard entries that Klipper ignores (for example passwords copied from a
  password manager that marks them as secret) are not bridged.
- Very large texts (beyond the X server's maximum request size) are skipped;
  the bridge does not implement the X11 INCR protocol.

## Troubleshooting

- **Nothing is bridged:** check that Klipper is running
  (`qdbus6 org.kde.klipper /klipper`) and that the service is active
  (`systemctl --user status citrix-clipboard-bridge`).
- **Diagnosing the clipboard:** do not poll `wl-paste` in a loop. KWin does not
  expose the data-control protocol to normal clients, so every `wl-paste` call
  briefly creates a window and steals keyboard focus. Read the X11 side with
  `xclip -o -selection clipboard` and the Wayland side through Klipper
  (`qdbus6 org.kde.klipper /klipper getClipboardContents`) instead.
- **Fallback:** an X11 Plasma session avoids the problem entirely
  (`sudo apt install plasma-session-x11`, then pick "Plasma (X11)" at login).

## Tested with

- Kubuntu 26.04 LTS, KDE Plasma / KWin 6.6.6, Wayland session
- Citrix Workspace app for Linux 26.04.10.1 (`icaclient`)
- Sources: GNOME Text Editor (GTK4), Obsidian (Electron)

## License

MIT, see [LICENSE](LICENSE).
