# cranky-clippy

This is Cranky Clippy. He's happy for now, but he won't be for much longer if you get distracted. This continuation repo targets Linux and Windows. We originally made this in 24 hours for Stormhacks 2026, and this is the more full version with everything we didn't have time to add.

If you want the Linux-only version made during the hackathon, use this link: [Cranky-Clippy](https://github.com/neaflow/cranky-clippy)

Currently doesn't work on MacOS, we're working on it...

![Happy Clippy](assets/2.%20happy/h-frame1.png)

If you find yourself getting easily distracted from the study/work goals that you set for yourself, you probably are in need of a motivator. **Cranky Clippy** is like the OG Clippert from the 90s versions of Word, but he lives on your desktop, and feeds on your productivity. He gets really, really mad if you dare distract yourself. So much so that he *will* take action to make sure you stay focused.



## How it works

1. Launch the program after setup with `python RUNTHISONE.py`; you'll see this window appear.

   ![Goal entry window](sss/1.png)

2. Get to work! he'll get angry lest you get distracted.

   ![Cranky Clippy in action](sss/2.png)

Note: you can hide the debug menu with the **collapse** button. You can kill Cranky Clippy by simply clicking the debug window's close button.

## Platforms

Linux and Windows are both supported. The platform is detected at run time and
each piece has its own implementation of the same contract, so neither platform
can change what the other does.

| | Linux | Windows |
|---|---|---|
| focused window | KWin / GNOME Shell extension / X11 EWMH, then best-effort AT-SPI | Win32 (`GetForegroundWindow` + the owning process' image name) |
| idle time | Mutter / freedesktop ScreenSaver / X11 / Wayland idle protocol | `GetLastInputInfo` |
| app metadata | `.desktop` entries + AppStream | the executable's version resource (FileDescription/ProductName) |
| restore/minimize a window | KWin scripting | `ShowWindow` + `SetForegroundWindow` |
| reopen a closed app | `gtk-launch` desktop entry | the executable path recorded for that window, else the app's own exe |
| browser tab | live extension first, then caption-matched sessionstore/History | extension first, then History DB |
| pet compositing | X11/XWayland tolerates a damage rect overhanging the window | the artwork is shifted *inside* the window, because `UpdateLayeredWindow` rejects an overhang |

## How to set it up

### 1. Install dependencies

**Linux**

```sh
sudo apt update
sudo apt install python3 xwayland python3-venv python3-tk python3-gi python3-dbus \
  gir1.2-atspi-2.0 at-spi2-core x11-utils libglib2.0-bin
python3 gnome_extension/install.py
```

**Windows** — nothing to install. `tkinter` ships with the Windows Python
installer, and the pet comes from `pip install PySide6` below. Qt's own
`UpdateLayeredWindow` composites the pet, so there is no extra desktop
integration to install.

### GNOME focus detection (especially Wayland)

GNOME does not expose global native Wayland focus through X11. This repo bundles
its own small read-only Shell extension for GNOME 45-50, with no third-party
download, pip dependency, root privileges or unsafe Shell mode. The universal
Linux setup above runs this installer on every desktop. It detects the active
GNOME session and does nothing on KDE/other desktops, even if GNOME is installed.
You can also rerun it:

```sh
python3 gnome_extension/install.py
```

GNOME may require logging out and back in to discover a newly installed extension.
If the installer says it could not enable it, after logging in run:

```sh
gnome-extensions enable focus@cranky-clippy.local
```

Updates to the running extension also need logout/login. The installer rejects
unsupported GNOME versions rather than disabling GNOME's version check. This
extension makes focused app/title identity readable to other apps on the same
local session bus. It has no network or window-control methods. Disable/remove:

```sh
gnome-extensions disable focus@cranky-clippy.local
gnome-extensions uninstall focus@cranky-clippy.local
```

Check it before launching:

```sh
gdbus call --session --dest org.gnome.Shell \
  --object-path /org/crankyclippy/Focus --method org.crankyclippy.Focus.Get
python3 get_desktop_state.py
```

The detector also accepts an already-installed
[Focused Window D-Bus](https://extensions.gnome.org/extension/5592/focused-window-d-bus/)
provider, but that extension is not required. Its interface is documented at
<https://github.com/flexagoon/focused-window-dbus>.

Do not turn on Shell unsafe mode. Without the extension, AT-SPI is best effort,
not guaranteed: install the distro accessibility packages, keep system packages
visible in the venv, and enable accessibility in the application. On X11, xprop
provides focus without the extension. XWayland is deliberately not used as a
Wayland focus source, because it can report the last X11 app while a native
Wayland app is actually focused. A missing focus source now includes
`detection_status: unavailable` and a setup hint.

For reliable browser URLs on either OS, install the browser extension/native
host described below. The live extension is used on Linux too. Reload it after updating this branch
(the request/reply protocol now includes a poll ID). Without it,
caption-matched profile files and address-bar accessibility are best effort;
unmatched history/session tabs are no longer reported as the active URL.
Two tabs with identical titles cannot be distinguished by disk/title matching.
Unsupported or sandboxed apps can still omit metadata. Window actions on GNOME
are not added by this detection change.

### Compatibility checks

```sh
python3 -m unittest discover -s tests -v
node tests/test_gnome_extension.js  # optional offline JS mock checks
```

The regression tests use simulated GNOME, X11, KWin and Win32 replies. They are
not substitutes for running on those desktops. Before merging, test at least:
GNOME Wayland with the extension, GNOME X11, KDE and Windows. Switch between a
native editor and browser, switch tabs/windows (including matching titles),
focus Clippy, and check `python3 get_desktop_state.py` plus the running UI.
The standalone detector has no running browser bridge; URL detection there
uses only accessibility/profile fallbacks. Check live extension URLs in the UI.

### 2. Create a virtual environment and install PySide6

```sh
# Linux
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install PySide6
```

```powershell
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install PySide6
```

On Linux the venv needs `--system-site-packages` so the `gi`/AT-SPI modules
installed by apt stay importable. Windows has no equivalent requirement.

### 3. Set up one OpenRouter API key

Get one key at <https://openrouter.ai/keys>. Jev decisions, DeepSeek dialogue
and excuse assessment, and Kokoro speech all use `OPENROUTER_API_KEY`.
Gemini and ElevenLabs accounts or keys are no longer needed.

Copy `.env.local.example` to `.env.local` and add your key only in the local file:

```sh
cp .env.local.example .env.local
chmod 600 .env.local
```

```powershell
# Windows (PowerShell)
Copy-Item .env.local.example .env.local
```

`.env.local` is ignored by Git. Environment variables with the same names take
precedence over values in that file. Never commit API keys or local config.
Restart the app after changing config.

Defaults (optional config overrides are shown in the example):

- Jev focus decisions: `typesafe/jev-1.13` (existing OpenRouter integration).
- Dialogue and excuses: `deepseek/deepseek-chat`, using structured JSON responses.
- Speech: `hexgrad/kokoro-82m`, voice `am_liam` (underscore, not `am-liam`).

Chat uses OpenRouter's `/api/v1/chat/completions`; TTS uses
`/api/v1/audio/speech` with explicit MP3 output for Qt playback. No extra SDK is
needed. Speech remains optional: audio failures keep the text overlay working.
Account credit, model/provider access, network errors, and Qt multimedia support
can affect availability. Changing models requires matching schema/voice support.

References: [DeepSeek](https://openrouter.ai/deepseek/deepseek-chat),
[Kokoro](https://openrouter.ai/hexgrad/kokoro-82m),
[TTS API](https://openrouter.ai/skills/openrouter-tts).

Offline regression tests (no API key or PySide6 needed):

```sh
python -m unittest discover -s tests -v
```

### 4. Install the browser extension (optional, for returning to the exact tab)

First register the native messaging host:

```sh
python browser_extensions/install_native_hosts.py
```

On Windows this writes a manifest into each browser's per-user
`NativeMessagingHosts` folder *and* the matching `HKCU` registry key, and
compiles a small native launcher that the manifests point at. Firefox starts a
native host with `CreateProcess` and silently refuses a `.cmd`, so the launcher
is a real executable; it is named after the interpreter and host script it wraps,
so re-running the installer never collides with a copy a browser still has open.
If no C# compiler is available it falls back to a `.cmd`, which Chrome accepts
but Firefox does not.

On Linux the original behaviour is used: a manifest in each browser's own
`NativeMessagingHosts` directory, with the Python script itself as the host.

- **Chrome:** Open `chrome://extensions`, enable **Developer mode**, select **Load unpacked**, and choose the repo's `browser_extensions/chrome` folder.
- **Firefox:** Open `about:debugging#/runtime/this-firefox`, select **Load Temporary Add-on**, and choose `browser_extensions/firefox/manifest.json`. Firefox temporary add-ons must be loaded again after Firefox restarts.

This lets Cranky Clippy return you to the last on-task browser tab. Without the extension, it can fall back to opening the saved URL in a new tab.

On Windows the extension is the only way to read the *live* Chromium tab:
there is no readable session file and no accessibility tree this project can use.
Without it, the detector falls back to matching the window caption against the
browser's History DB, which is a guess rather than the real tab.

### 5. Run the program

```sh
python RUNTHISONE.py                 # then type your goal
python RUNTHISONE.py "write the report"   # or pass it straight in
```



By the way; there are API keys in the git history. these have already been rotated. NO FREE API FOR YOU!!!
Also, for anyone not viewing this from StormHacks 2026, yeah that's what this was for. this entire thing was made in 24 hours (less, even)
