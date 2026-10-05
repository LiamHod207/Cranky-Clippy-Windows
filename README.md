# cranky-clippy

This is Cranky Clippy. He's happy for now, but he won't be for much longer if you get distracted. This repo only runs on Windows, If you want the Linux version, use this link: [Cranky-Clippy](https://github.com/neaflow/cranky-clippy)

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
| focused window | KWin over DBus, else AT-SPI | Win32 (`GetForegroundWindow` + the owning process' image name) |
| idle time | Mutter / freedesktop ScreenSaver / X11 / Wayland idle protocol | `GetLastInputInfo` |
| app metadata | `.desktop` entries + AppStream | the executable's version resource (FileDescription/ProductName) |
| restore/minimize a window | KWin scripting | `ShowWindow` + `SetForegroundWindow` |
| reopen a closed app | `gtk-launch` desktop entry | the executable path recorded for that window, else the app's own exe |
| browser tab | sessionstore (Gecko) / History DB (Chromium) / extension | extension first, then History DB |
| pet compositing | X11/XWayland tolerates a damage rect overhanging the window | the artwork is shifted *inside* the window, because `UpdateLayeredWindow` rejects an overhang |

## How to set it up

### 1. Install dependencies

**Linux**

```sh
sudo apt update
sudo apt install python3 xwayland python3-venv python3-tk python3-gi python3-dbus \
  gir1.2-atspi-2.0 at-spi2-core
```

**Windows** — nothing to install. `tkinter` ships with the Windows Python
installer, and the pet comes from `pip install PySide6` below. Qt's own
`UpdateLayeredWindow` composites the pet, so there is no extra desktop
integration to install.

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

### 3. Set up the API keys

Create or copy an API key from each provider:

- Jev from OpenRouter: <https://openrouter.ai/keys>
- Gemini: <https://aistudio.google.com/apikey>
- ElevenLabs: <https://elevenlabs.io/app/settings/api-keys>

Copy `.env.local.example` to `.env.local` and paste each key after its matching
name (`OPENROUTER_API_KEY`, `GEMINI_API_KEY`, and `ELEVENLABS_API_KEY`):

```sh
cp .env.local.example .env.local
chmod 600 .env.local
```

```powershell
# Windows (PowerShell)
Copy-Item .env.local.example .env.local
```

`.env.local` is ignored by Git. Environment variables with the same names take
precedence over values in that file.

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
