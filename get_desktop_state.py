"""Detect the focused window and collect the desktop state Jev will judge.

Run directly (`python3 get_desktop_state.py`) to print a test report of what
was detected. jev_decides.py imports get_desktop_state() and feeds the
returned dict to Jev as the `state`.

Static app and website definitions live in app_catalog.py; this module owns
live desktop detection and metadata collection.

Linux: KDE uses KWin; GNOME uses the Focused Window D-Bus Shell extension.
X11 desktops use EWMH via xprop when compositor DBus is unavailable. AT-SPI is
only a best-effort fallback (accessibility flags can be absent or stale). Window-focus duration is tracked
between polls; system idle time is read from the desktop API where available
and remains None where the compositor does not expose it (notably some KDE
Wayland versions).

Windows: the focused window comes from the Win32 API (GetForegroundWindow plus
the owning process' image name), which is exact on every Windows desktop and
needs no extra packages. Idle time comes from GetLastInputInfo, and restoring
or minimizing a window uses the same ShowWindow/SetForegroundWindow calls
Win32 provides. Browser tabs are read through the browser extension bridge
first (the only way to see a real Chromium tab on Windows), then from the
browser's own profile files.

On Linux only python3-gi and python3-dbus are needed from the repos (both
preinstalled on Ubuntu with KDE/GNOME). On Windows everything used here ships
with Python.
"""

from desktop_focus import gnome_focus, x11_focus

import configparser
import functools
import glob
import html
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import urllib.parse
from urllib.parse import urlparse

# Which platform's desktop APIs this run should use. Every backend below is
# chosen from this flag, so the Linux behaviour is untouched on Linux and the
# Windows behaviour is unreachable there.
IS_WINDOWS = sys.platform == "win32"

try:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    _ATSPI_OK = True
except Exception:  # gi/Atspi can be missing on headless boxes
    Atspi = None
    _ATSPI_OK = False

try:
    import dbus
    import dbus.mainloop.glib
    import dbus.service

    from gi.repository import GLib

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    _DBUS_OK = True
except Exception:  # dbus-python can be missing
    dbus = None
    _DBUS_OK = False

from app_catalog import (
    AI_CHAT_WEBAPP_DESCRIPTIONS,
    AI_CHAT_WEBAPP_PROVIDERS,
    APP_DESCRIPTIONS,
    APP_METADATA,
    EMAIL_WEBAPP_PROVIDERS,
    SHELL_APPS,
    WEBAPP_CATEGORIES,
    WEBAPP_METADATA,
    _DESKTOP_APP_DISPLAY_NAMES,
    _GAME_DISPLAY_NAMES,
    _GECKO_SESSIONSTORE_BROWSERS,
    _PROFILE_DIR_SKIPS,
    _canonical,
    _match_app,
    _match_webapp,
    _match_webapp_from_title,
    _tab_title_from_window,
    _expand_profile_pattern,
    chromium_profile_roots,
    gecko_profile_roots,
)


# Linux-specific fallback for apps without a curated APP_DESCRIPTIONS entry.
# The future macOS and Windows versions should use native metadata providers
# (LaunchServices/Info.plist and AppUserModelID/package metadata, respectively).
_DESKTOP_ENTRY_DIRS = (
    "~/.local/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/usr/local/share/applications",
    "/usr/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
    "/opt/share/applications",
    "~/.local/share/flatpak/app/*/active/files/share/applications",
    "/var/lib/flatpak/app/*/active/files/share/applications",
)
_APPSTREAM_DIRS = (
    "~/.local/share/metainfo",
    "/usr/local/share/metainfo",
    "/usr/share/metainfo",
    "/usr/share/appdata",
    "~/.local/share/flatpak/exports/share/metainfo",
    "/var/lib/flatpak/exports/share/metainfo",
    "~/.local/share/flatpak/app/*/active/files/share/metainfo",
    "/var/lib/flatpak/app/*/active/files/share/metainfo",
)
_MAX_APP_METADATA_FILE_BYTES = 2 * 1024 * 1024


def _clean_app_metadata_text(value, limit=600):
    """Normalize local launcher text before including it in Jev's state."""
    if not value:
        return None
    value = html.unescape(re.sub(r"<[^>]*>", " ", str(value)))
    value = " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())
    return value[:limit] or None


@functools.lru_cache(maxsize=1)
def _desktop_entry_roots():
    roots = []
    for pattern in _DESKTOP_ENTRY_DIRS:
        for root in glob.glob(os.path.expanduser(pattern)):
            if os.path.isdir(root) and root not in roots:
                roots.append(root)
    return tuple(roots)


@functools.lru_cache(maxsize=1024)
def _read_desktop_entry(path, modified_ns, size):
    """Read descriptive, non-executable fields from one .desktop entry."""
    del modified_ns, size  # included in the cache key so edits invalidate it
    try:
        if os.path.getsize(path) > _MAX_APP_METADATA_FILE_BYTES:
            return None
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            parser.read_file(stream)
        if not parser.has_section("Desktop Entry"):
            return None
        entry = parser["Desktop Entry"]
        if entry.get("Type", "Application").lower() != "application":
            return None

        locale = (
            os.environ.get("LC_ALL")
            or os.environ.get("LC_MESSAGES")
            or os.environ.get("LANG")
            or ""
        ).split(".")[0].split("@")[0]
        language = locale.split("_")[0] if locale else ""

        def localized(key):
            for candidate in ("%s[%s]" % (key, locale), "%s[%s]" % (key, language), key):
                value = _clean_app_metadata_text(entry.get(candidate))
                if value:
                    return value
            return None

        return {
            "name": localized("Name"),
            "generic_name": localized("GenericName"),
            "comment": localized("Comment"),
            "startup_wm_class": _clean_app_metadata_text(entry.get("StartupWMClass")),
            "desktop_file_id": os.path.basename(path)[:-len(".desktop")],
        }
    except (OSError, configparser.Error, UnicodeError):
        return None


def _desktop_entry_details(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return _read_desktop_entry(path, stat.st_mtime_ns, stat.st_size)


def _metadata_name_candidates(raw_app_name, desktop_file_id, app_key):
    values = []
    for value in (desktop_file_id, app_key, raw_app_name):
        value = (value or "").strip()
        if not value:
            continue
        if value.endswith(".desktop") or os.path.sep in value:
            value = os.path.basename(value)
            if value.endswith(".desktop"):
                value = value[:-len(".desktop")]
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _find_desktop_entry(raw_app_name, desktop_file_id, app_key):
    candidates = _metadata_name_candidates(raw_app_name, desktop_file_id, app_key)
    if not candidates:
        return None, None
    normalized = {
        re.sub(r"[^a-z0-9]", "", value.casefold()) for value in candidates
    }
    roots = _desktop_entry_roots()

    for candidate in candidates:
        filename = candidate if candidate.endswith(".desktop") else candidate + ".desktop"
        for root in roots:
            path = os.path.join(root, filename)
            if os.path.isfile(path):
                return path, _desktop_entry_details(path)

    # Some windows only expose a WM class. Match that against StartupWMClass
    # or the application name from installed launchers.
    for root in roots:
        try:
            entries = os.scandir(root)
        except OSError:
            continue
        with entries:
            for item in entries:
                if not item.is_file() or not item.name.endswith(".desktop"):
                    continue
                details = _desktop_entry_details(item.path)
                if not details:
                    continue
                for key in ("startup_wm_class", "name", "desktop_file_id"):
                    value = details.get(key)
                    if value and re.sub(r"[^a-z0-9]", "", value.casefold()) in normalized:
                        return item.path, details
    return None, None


@functools.lru_cache(maxsize=1)
def _appstream_roots():
    roots = []
    for pattern in _APPSTREAM_DIRS:
        for root in glob.glob(os.path.expanduser(pattern)):
            if os.path.isdir(root) and root not in roots:
                roots.append(root)
    return tuple(roots)


def _xml_local_name(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _read_appstream_description(path, modified_ns, size, candidate_ids):
    del modified_ns, size
    try:
        if os.path.getsize(path) > _MAX_APP_METADATA_FILE_BYTES:
            return None
        document = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None

    wanted = {
        re.sub(r"[^a-z0-9]", "", name.casefold().removesuffix(".desktop"))
        for name in candidate_ids
    }
    components = [
        node for node in document.iter() if _xml_local_name(node.tag) == "component"
    ] or [document]
    for component in components:
        component_id = None
        launchable_ids = []
        summary = None
        description = None
        for child in component:
            kind = _xml_local_name(child.tag)
            if kind == "id":
                component_id = _clean_app_metadata_text(child.text)
            elif kind == "launchable":
                launchable_ids.extend(
                    _clean_app_metadata_text(grandchild.text) for grandchild in child
                )
            elif kind == "summary":
                summary = _clean_app_metadata_text(" ".join(child.itertext()))
            elif kind == "description":
                description = _clean_app_metadata_text(" ".join(child.itertext()))
        ids = [component_id] + launchable_ids
        if not any(
            value
            and re.sub(r"[^a-z0-9]", "", value.casefold().removesuffix(".desktop")) in wanted
            for value in ids
        ):
            continue
        if summary and description and description.casefold() not in summary.casefold():
            return _clean_app_metadata_text("%s. %s" % (summary, description))
        return summary or description
    return None


def _appstream_description(candidate_ids):
    for root in _appstream_roots():
        try:
            files = list(glob.iglob(os.path.join(root, "*.metainfo.xml")))
            files.extend(glob.iglob(os.path.join(root, "*.appdata.xml")))
        except OSError:
            continue
        for path in files:
            try:
                stat = os.stat(path)
            except OSError:
                continue
            description = _read_appstream_description(
                path, stat.st_mtime_ns, stat.st_size, tuple(candidate_ids)
            )
            if description:
                return description
    return None


@functools.lru_cache(maxsize=256)
def _win_exe_version_strings(path):
    """(FileDescription, ProductName, CompanyName) from an exe's version info.

    Windows' equivalent of a .desktop Comment / AppStream summary: the metadata
    the vendor shipped with the binary. Read with version.dll, so no extra
    Python package is needed.
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buffer):
            return None

        def _query(key):
            pointer = ctypes.c_void_p()
            length = ctypes.c_uint()
            if not ctypes.windll.version.VerQueryValueW(
                buffer, key, ctypes.byref(pointer), ctypes.byref(length)
            ):
                return None
            return ctypes.wstring_at(pointer.value, length.value)

        translation = _query("\\VarFileInfo\\Translation") or ""
        strings = {}
        prefix = None
        if len(translation) >= 2:
            # Stored as a wchar[2] holding the language id and code page as
            # numbers, not as the hex text they are written with in the tree.
            prefix = "\\StringFileInfo\\%04x%04x\\" % (
                ord(translation[0]), ord(translation[1]),
            )
        for name in ("FileDescription", "ProductName", "CompanyName"):
            value = _query("%s%s" % (prefix, name)) if prefix else None
            # VerQueryValueW counts the terminating NUL, so trim it here rather
            # than letting it reach a comparison or Jev's state text.
            value = _clean_app_metadata_text(value, limit=200) if value else None
            if value:
                strings[name] = value
        return strings or None
    except Exception as exc:
        print("[DesktopState] Could not read version info for %s: %s" % (path, exc))
        return None


def _windows_app_description(process_path, app_key):
    """Describe a Windows app from its catalog entry or its own metadata."""
    if not IS_WINDOWS:
        return None
    if process_path and app_key:
        description = APP_DESCRIPTIONS.get(app_key)
        if description:
            return description
    strings = _win_exe_version_strings(process_path)
    if not strings:
        return None
    product = strings.get("ProductName")
    description = strings.get("FileDescription")
    # Windows' own binaries report the operating system as their product name,
    # and Electron apps report the framework as their description: in both
    # cases the other field is the one that names the app.
    if not description or description.strip().casefold() == "electron":
        description = product
    if not product or re.search(r"operating system", product, re.I):
        product = None
    if not product and not description:
        return None
    if product and description and product.casefold() != description.casefold():
        return _clean_app_metadata_text("%s - %s" % (product, description))
    return _clean_app_metadata_text(product or description)


@functools.lru_cache(maxsize=256)
def _installed_app_description(raw_app_name, desktop_file_id, app_key, process_path=None):
    """Return an installed app description and its source, if available.

    Windows has no .desktop files or AppStream data: the executable's own
    version resource is the equivalent source of truth, so its FileDescription
    and ProductName are read for apps the catalog does not already describe.
    """
    if IS_WINDOWS:
        description = _windows_app_description(process_path, app_key)
        if description:
            return description, "windows_version_info"
        if app_key and APP_DESCRIPTIONS.get(app_key):
            return APP_DESCRIPTIONS[app_key], "app_catalog"
        return None, None

    path, details = _find_desktop_entry(raw_app_name, desktop_file_id, app_key)
    if details:
        name = details.get("name")
        generic_name = details.get("generic_name")
        comment = details.get("comment")
        if comment or generic_name:
            parts = [name] if name else []
            if generic_name and generic_name.casefold() != (name or "").casefold():
                parts.append(generic_name)
            if comment and comment.casefold() not in {part.casefold() for part in parts}:
                parts.append(comment)
            return _clean_app_metadata_text(" — ".join(parts)), "desktop_entry"

    candidate_ids = list(_metadata_name_candidates(raw_app_name, desktop_file_id, app_key))
    if details and details.get("desktop_file_id"):
        candidate_ids.insert(0, details["desktop_file_id"])
    description = _appstream_description(tuple(candidate_ids))
    if description:
        return description, "appstream"
    if details and details.get("name"):
        return details["name"], "desktop_entry"
    return None, None


# --- focused-window backends -------------------------------------------------
#
# Two ways to find the focused window. On KDE, KWin itself is the only
# source of truth: the AT-SPI ACTIVE flag often stays on a dead window, so
# we ask the compositor over DBus (load a tiny script via
# org.kde.kwin.Scripting, which reports workspace.activeWindow). Everywhere
# else we use the AT-SPI accessibility tree.
#
# Reference for the KDE pattern: kdotool works the same way (loadScript +
# start + callDBus back to a private DBus service). The `start()` call is
# mandatory on current KWin versions: loadScript only parks the script.

def _desktop_kind():
    return (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()


# --- Windows backend ---------------------------------------------------------
#
# Win32 hands out the focused window directly, with no accessibility bridge
# and no per-desktop special cases: GetForegroundWindow() is the answer, and
# the owning process' executable name is the app identity (Windows has no
# StartupWMClass or .desktop files to match on, so app_catalog maps the
# executable names instead).
#
# Only the pieces this project needs are bound; every one is bound with
# explicit argtypes/restypes because a 64-bit HWND would otherwise be truncated
# to 32 bits by ctypes' default c_int restype.

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetShellWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.restype = wintypes.BOOL
    _user32.SetActiveWindow.argtypes = [wintypes.HWND]
    _user32.SetActiveWindow.restype = wintypes.HWND
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _user32.keybd_event.argtypes = [
        ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t,
    ]
    _user32.keybd_event.restype = None
    _user32.GetLastInputInfo.argtypes = [ctypes.c_void_p]
    _user32.GetLastInputInfo.restype = wintypes.BOOL
    _user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _user32.EnumWindows.restype = wintypes.BOOL
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.c_void_p,
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    # Window actions / styles used by the "put the task window back" path.
    SW_RESTORE = 9
    SW_MINIMIZE_WIN = 6
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002

    # Shell and framework windows that are not a user's app: the desktop
    # itself, the taskbar, the Start menu and its search box. Windows puts
    # the Start menu in the foreground routinely, and that is the exact
    # analogue of KWin reporting "nothing is focused".
    _WIN_SHELL_CLASSES = {
        "progman", "workerw", "shell_traywnd", "shell_sectraywnd",
        "windows.ui.core.corewindow", "notifyicoverflowwindow",
        "windows.ui.composition.desktopwindowmanager",
        "button", "applicationmanager_DesktopShellWindow",
    }

    class _WinLastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("dwTime", wintypes.DWORD)]

    _WIN_ENUM_PROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    # What the last detected window was, for the relaunch path. Kept out of the
    # state dict on purpose: it holds a local filesystem path, which is no use
    # to Jev but everything the overlay needs to reopen the app.
    _LAST_WINDOWS_WINDOW = {
        "window_id": None,
        "window_class": None,
        "process_id": None,
        "process_path": None,
        "caption": None,
        "app_class": None,
    }
else:
    _LAST_WINDOWS_WINDOW = {}


def _win_window_title(hwnd):
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value or ""


def _win_window_class(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value or ""


def _win_process_path(pid):
    """Full image path of a process, or None when it cannot be queried."""
    handle = _kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value or None
    finally:
        _kernel32.CloseHandle(handle)


def _win_app_identity(window_class, process_path):
    """The app name Win32 knows this window as.

    The executable is the identity, which app_catalog maps onto an app key;
    the window class is only a last-resort fallback, because most Chromium
    windows share the class "Chrome_WidgetWin_1". Store and other packaged
    apps are all hosted by one ApplicationFrameHost.exe, so they keep that
    name here and are identified from their window title instead (see
    _WINDOWS_TITLE_APPS in the catalog).
    """
    if process_path:
        stem = os.path.splitext(os.path.basename(process_path))[0]
        if stem:
            return stem.strip().lower()
    return {
        "mozillawindowclass": "firefox",
        "consolewindowclass": "conhost",
        "applicationframewindow": "applicationframehost",
    }.get(window_class.strip().lower(), "")


def _win_top_level_windows(visible_only=True):
    """(hwnd, title, class name, pid) for every top-level window."""
    found = []

    def _collect(hwnd, _lparam):
        if visible_only and not _user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(
            (int(hwnd), _win_window_title(hwnd), _win_window_class(hwnd), pid.value)
        )
        return True

    try:
        _user32.EnumWindows(_WIN_ENUM_PROC(_collect), 0)
    except Exception as exc:  # EnumWindows should not fail, but never crash a poll
        print("[DesktopState] EnumWindows failed: %s" % exc)
        return []
    return found


def _win_active_window():
    """Win32 focused window, shaped like the KWin backend's return value.

    Returns the same keys get_desktop_state() consumes: app_class, caption,
    window_id. window_id is the HWND, which is what the restore path needs to
    address this exact window again.
    """
    hwnd = _user32.GetForegroundWindow()
    if not hwnd or int(hwnd) == int(_user32.GetShellWindow() or 0):
        return {"no_active": True}

    window_class = _win_window_class(hwnd)
    if window_class.strip().lower() in _WIN_SHELL_CLASSES:
        # The desktop, taskbar or Start menu owns the foreground: report the
        # same "nothing is focused" the KDE backend reports.
        return {"no_active": True}

    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_path = _win_process_path(pid.value)
    caption = _win_window_title(hwnd)
    if caption.strip().casefold() in _IGNORED_WINDOW_TITLES:
        # One of our own windows holds the foreground. That is not a user app
        # and must never be judged, so report it the way the shell windows are
        # reported: nothing is focused.
        return {"no_active": True}
    app_class = _win_app_identity(window_class, process_path)
    if not caption and not app_class:
        return {"no_active": True}

    _LAST_WINDOWS_WINDOW.update({
        "window_id": int(hwnd),
        "window_class": window_class,
        "process_id": pid.value,
        "process_path": process_path,
        "caption": caption,
        "app_class": app_class,
    })
    return {
        "app_class": app_class,
        "caption": caption,
        "window_id": int(hwnd),
    }


def last_window_launch_info():
    """How to relaunch the window that was focused on the last poll.

    jev_overlay needs the executable path to reopen a closed on-task app, but
    that path is deliberately kept out of the state handed to Jev.
    """
    return dict(_LAST_WINDOWS_WINDOW)


def _win_idle_seconds():
    """Seconds since the last keyboard or mouse input, per Win32."""
    info = _WinLastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    elapsed = (_kernel32.GetTickCount64() - info.dwTime) / 1000.0
    return round(max(0.0, elapsed), 1)


def _win_force_foreground(hwnd):
    """Make a window the foreground window from a background process.

    Windows refuses foreground changes from a process that does not own the
    current foreground window. The documented way around it is to attach this
    thread's input queue to the foreground window's, do the normal calls
    inside that shared queue, and detach again.
    """
    foreground = _user32.GetForegroundWindow()
    our_thread = _kernel32.GetCurrentThreadId()
    attached = False
    if foreground:
        foreground_thread = _user32.GetWindowThreadProcessId(foreground, None)
        if foreground_thread and foreground_thread != our_thread:
            attached = bool(_user32.AttachThreadInput(our_thread, foreground_thread, True))
    try:
        _user32.BringWindowToTop(hwnd)
        if _user32.SetForegroundWindow(hwnd):
            _user32.SetActiveWindow(hwnd)
            return True
    finally:
        if attached:
            _user32.AttachThreadInput(our_thread, foreground_thread, False)

    # Windows also keeps a foreground *lock* (only the process that last had
    # input may change focus) and AttachThreadInput cannot help when the
    # current foreground window runs at a higher integrity level, e.g. an
    # elevated Task Manager. A harmless ALT tap is the documented way to
    # release that lock; it is only sent when the normal calls were refused.
    _user32.keybd_event(VK_MENU, 0, 0, 0)
    _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    if _user32.SetForegroundWindow(hwnd):
        _user32.SetActiveWindow(hwnd)
        return True
    return bool(_user32.GetForegroundWindow() == hwnd)


def _win_find_window(window_id=None, caption=None, app_name=None):
    """Locate a top-level window by HWND, exact title, then app identity."""
    if window_id:
        hwnd = int(window_id)
        if _user32.IsWindowVisible(hwnd):
            return hwnd
    if not caption:
        return None
    candidates = [
        hwnd for hwnd, title, _cls, _pid in _win_top_level_windows()
        if title.strip() == caption.strip()
    ]
    if app_name:
        wanted = app_name.strip().lower()
        for hwnd, _title, window_class, pid in _win_top_level_windows():
            if _win_app_identity(window_class, _win_process_path(pid)) == wanted:
                candidates.append(hwnd)
    return candidates[0] if candidates else None


def _win_activate_and_minimize(target_window_id, expected_active_window_id=None,
                               minimize_if_target_missing=False,
                               minimize_current=True,
                               return_detail=False,
                               target_desktop_file_id=None,
                               target_caption=None,
                               target_app=None):
    """Restore/activate a Windows window and optionally minimize the distraction.

    The same contract as _kwin_activate_and_minimize(), because this is the
    Windows implementation of that one: the active window is minimized only if
    it is still the exact window Jev observed, and only when it differs from
    the saved task window. Detail strings match too ("active window changed",
    "target window not found", "activated") so the caller does not care which
    platform it is running on.
    """
    def _report(ok, detail):
        return (bool(ok), detail) if return_detail else bool(ok)

    if not IS_WINDOWS:
        return _report(False, "Win32 window control unavailable")

    active_hwnd = _user32.GetForegroundWindow()
    active_id = int(active_hwnd) if active_hwnd else 0
    expected_id = int(expected_active_window_id) if expected_active_window_id else 0
    if expected_id and active_id != expected_id:
        return _report(False, "active window changed")

    target_id = int(target_window_id) if target_window_id else 0
    if not target_id or not _user32.IsWindowVisible(target_id):
        target_id = _win_find_window(
            caption=target_caption,
            app_name=target_app or _win_app_name_from_id(target_desktop_file_id),
        ) or 0

    if (
        minimize_current
        and expected_id
        and active_id == expected_id
        and active_id != target_id
        and active_id
        and (target_id or minimize_if_target_missing)
    ):
        _user32.ShowWindow(active_id, SW_MINIMIZE_WIN)

    if not target_id:
        return _report(False, "target window not found")
    if _user32.IsIconic(target_id):
        _user32.ShowWindow(target_id, SW_RESTORE)
    if not _win_force_foreground(target_id):
        return _report(False, "could not activate target window")
    return _report(True, "activated")


def _win_app_name_from_id(desktop_file_id):
    """Windows has no desktop-file ids; kept for call-parity with KWin."""
    return None


_FOCUS_TIMER_IDENTITY = None
_FOCUS_TIMER_STARTED = None


def _time_since_window_focused(identity, now=None):
    """Elapsed monotonic seconds since this window was first observed active.

    The overlay polls every few seconds, so the first value is 0 and the
    counter starts on the first observation (rather than guessing when focus
    actually changed).  A compositor window UUID distinguishes same-app
    windows; app/title is the fallback on AT-SPI-only desktops.
    """
    global _FOCUS_TIMER_IDENTITY, _FOCUS_TIMER_STARTED
    if identity is None:
        _FOCUS_TIMER_IDENTITY = None
        _FOCUS_TIMER_STARTED = None
        return None
    now = time.monotonic() if now is None else now
    if identity != _FOCUS_TIMER_IDENTITY or _FOCUS_TIMER_STARTED is None:
        _FOCUS_TIMER_IDENTITY = identity
        _FOCUS_TIMER_STARTED = now
    return round(max(0.0, now - _FOCUS_TIMER_STARTED), 1)


def _x11_idle_seconds():
    """Read X11 idle time when the X server supports MIT-SCREEN-SAVER."""
    import ctypes
    import ctypes.util

    x11_name = ctypes.util.find_library("X11")
    xss_name = ctypes.util.find_library("Xss")
    if not x11_name or not xss_name:
        return None
    try:
        x11 = ctypes.CDLL(x11_name)
        xss = ctypes.CDLL(xss_name)
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        display = x11.XOpenDisplay(None)
        if not display:
            return None

        try:
            opcode = ctypes.c_int()
            event_base = ctypes.c_int()
            error_base = ctypes.c_int()
            x11.XQueryExtension.argtypes = [
                ctypes.c_void_p, ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
            ]
            x11.XQueryExtension.restype = ctypes.c_int
            if not x11.XQueryExtension(
                display, b"MIT-SCREEN-SAVER", ctypes.byref(opcode),
                ctypes.byref(event_base), ctypes.byref(error_base),
            ):
                return None

            class XScreenSaverInfo(ctypes.Structure):
                _fields_ = [
                    ("window", ctypes.c_ulong),
                    ("state", ctypes.c_int),
                    ("kind", ctypes.c_int),
                    ("til_or_since", ctypes.c_ulong),
                    ("idle", ctypes.c_ulong),
                    ("event_mask", ctypes.c_ulong),
                ]

            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(XScreenSaverInfo)
            xss.XScreenSaverQueryInfo.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong,
                ctypes.POINTER(XScreenSaverInfo),
            ]
            xss.XScreenSaverQueryInfo.restype = ctypes.c_int
            xss.XScreenSaverFreeInfo.argtypes = [ctypes.POINTER(XScreenSaverInfo)]
            info = xss.XScreenSaverAllocInfo()
            if not info:
                return None
            try:
                root = x11.XDefaultRootWindow(display)
                if xss.XScreenSaverQueryInfo(display, root, info):
                    return round(info.contents.idle / 1000.0, 1)
            finally:
                xss.XScreenSaverFreeInfo(info)
        finally:
            x11.XCloseDisplay(display)
    except Exception:
        return None
    return None


_WAYLAND_IDLE_LOCK = threading.Lock()
_WAYLAND_IDLE_STATE = {
    "started": False,
    "status": "not_started",
    "last_active": None,
    "first_idle_event": True,
    "monitor_started": None,
}
_WAYLAND_IDLE_TIMEOUT_MS = 1


def _record_wayland_idle_event(is_idle, now=None):
    """Update the cached activity timestamp from one Wayland protocol event."""
    now = time.monotonic() if now is None else now
    with _WAYLAND_IDLE_LOCK:
        if is_idle:
            if _WAYLAND_IDLE_STATE["first_idle_event"]:
                started = _WAYLAND_IDLE_STATE["monitor_started"] or now
                if now - started < 0.5:
                    # The session was already idle when this monitor attached;
                    # the protocol cannot reveal how long it had been idle.
                    _WAYLAND_IDLE_STATE["last_active"] = None
                    _WAYLAND_IDLE_STATE["status"] = "initial_idle_unknown"
                else:
                    _WAYLAND_IDLE_STATE["last_active"] = (
                        now - _WAYLAND_IDLE_TIMEOUT_MS / 1000.0
                    )
                    _WAYLAND_IDLE_STATE["status"] = "ready"
                _WAYLAND_IDLE_STATE["first_idle_event"] = False
            else:
                _WAYLAND_IDLE_STATE["last_active"] = (
                    now - _WAYLAND_IDLE_TIMEOUT_MS / 1000.0
                )
                _WAYLAND_IDLE_STATE["status"] = "ready"
        else:
            _WAYLAND_IDLE_STATE["last_active"] = now
            _WAYLAND_IDLE_STATE["status"] = "ready"
            _WAYLAND_IDLE_STATE["first_idle_event"] = False


def _run_wayland_idle_monitor():
    """Listen for Wayland idle/resume notifications in a background thread.

    This is event-based because Wayland intentionally does not let ordinary
    clients poll raw global input state.  A 1 ms input-idle notification
    gives the focuser's monotonic timer close to per-input resolution.
    """
    import ctypes
    import ctypes.util

    class WlInterface(ctypes.Structure):
        pass

    class WlMessage(ctypes.Structure):
        _fields_ = [
            ("name", ctypes.c_char_p),
            ("signature", ctypes.c_char_p),
            ("types", ctypes.POINTER(ctypes.POINTER(WlInterface))),
        ]

    WlInterface._fields_ = [
        ("name", ctypes.c_char_p),
        ("version", ctypes.c_int),
        ("method_count", ctypes.c_int),
        ("methods", ctypes.POINTER(WlMessage)),
        ("event_count", ctypes.c_int),
        ("events", ctypes.POINTER(WlMessage)),
    ]

    lib_name = ctypes.util.find_library("wayland-client")
    if not lib_name:
        with _WAYLAND_IDLE_LOCK:
            _WAYLAND_IDLE_STATE["status"] = "wayland_client_library_missing"
        return

    try:
        lib = ctypes.CDLL(lib_name)
        lib.wl_display_connect.argtypes = [ctypes.c_char_p]
        lib.wl_display_connect.restype = ctypes.c_void_p
        lib.wl_display_roundtrip.argtypes = [ctypes.c_void_p]
        lib.wl_display_roundtrip.restype = ctypes.c_int
        lib.wl_display_dispatch.argtypes = [ctypes.c_void_p]
        lib.wl_display_dispatch.restype = ctypes.c_int
        lib.wl_proxy_add_listener.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ]
        lib.wl_proxy_add_listener.restype = ctypes.c_int
        lib.wl_proxy_get_version.argtypes = [ctypes.c_void_p]
        lib.wl_proxy_get_version.restype = ctypes.c_uint
        lib.wl_proxy_marshal_flags.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(WlInterface),
            ctypes.c_uint, ctypes.c_uint,
        ]
        lib.wl_proxy_marshal_flags.restype = ctypes.c_void_p

        registry_interface = WlInterface.in_dll(lib, "wl_registry_interface")
        seat_interface = WlInterface.in_dll(lib, "wl_seat_interface")
        registry_interface_ptr = ctypes.pointer(registry_interface)
        seat_interface_ptr = ctypes.pointer(seat_interface)

        # The protocol descriptors below are the small client-side interface
        # needed for ext-idle-notify-v1; no generated bindings are required.
        notification_interface = WlInterface()
        notifier_interface = WlInterface()
        notifier_get_idle_types = (ctypes.POINTER(WlInterface) * 3)(
            ctypes.pointer(notification_interface),
            ctypes.POINTER(WlInterface)(),
            seat_interface_ptr,
        )
        notifier_requests = (WlMessage * 3)(
            WlMessage(b"destroy", b"", None),
            WlMessage(b"get_idle_notification", b"nuo", notifier_get_idle_types),
            WlMessage(b"get_input_idle_notification", b"2nuo", notifier_get_idle_types),
        )
        notifier_interface.name = b"ext_idle_notifier_v1"
        notifier_interface.version = 2
        notifier_interface.method_count = len(notifier_requests)
        notifier_interface.methods = notifier_requests
        notifier_interface.event_count = 0
        notifier_interface.events = None

        notification_requests = (WlMessage * 1)(WlMessage(b"destroy", b"", None))
        notification_events = (WlMessage * 2)(
            WlMessage(b"idled", b"", None),
            WlMessage(b"resumed", b"", None),
        )
        notification_interface.name = b"ext_idle_notification_v1"
        notification_interface.version = 2
        notification_interface.method_count = 1
        notification_interface.methods = notification_requests
        notification_interface.event_count = len(notification_events)
        notification_interface.events = notification_events

        display = lib.wl_display_connect(None)
        if not display:
            raise RuntimeError("could not connect to Wayland display")
        registry = lib.wl_proxy_marshal_flags(
            display, 1, registry_interface_ptr,
            lib.wl_proxy_get_version(display), 0, ctypes.c_void_p(),
        )
        if not registry:
            raise RuntimeError("could not get Wayland registry")

        bindings = {"seat": None, "notifier": None, "notifier_version": 0}

        @ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_char_p, ctypes.c_uint,
        )
        def on_global(_data, registry_proxy, name, interface_name, version):
            if not interface_name:
                return
            if interface_name == b"wl_seat" and bindings["seat"] is None:
                seat_version = min(int(version), 9)
                bindings["seat"] = lib.wl_proxy_marshal_flags(
                    registry_proxy, 0, seat_interface_ptr, seat_version, 0,
                    ctypes.c_uint(name), ctypes.c_char_p(b"wl_seat"),
                    ctypes.c_uint(seat_version), ctypes.c_void_p(),
                )
            elif (
                interface_name == b"ext_idle_notifier_v1"
                and bindings["notifier"] is None
            ):
                notifier_version = min(int(version), 2)
                bindings["notifier_version"] = notifier_version
                bindings["notifier"] = lib.wl_proxy_marshal_flags(
                    registry_proxy, 0, ctypes.pointer(notifier_interface),
                    notifier_version, 0, ctypes.c_uint(name),
                    ctypes.c_char_p(b"ext_idle_notifier_v1"),
                    ctypes.c_uint(notifier_version), ctypes.c_void_p(),
                )

        @ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint)
        def on_global_remove(_data, _registry_proxy, _name):
            return

        registry_listener = (ctypes.c_void_p * 2)(
            ctypes.cast(on_global, ctypes.c_void_p).value,
            ctypes.cast(on_global_remove, ctypes.c_void_p).value,
        )
        if lib.wl_proxy_add_listener(
            registry, ctypes.cast(registry_listener, ctypes.POINTER(ctypes.c_void_p)), None
        ) != 0:
            raise RuntimeError("could not listen to Wayland registry")
        if lib.wl_display_roundtrip(display) < 0:
            raise RuntimeError("Wayland registry roundtrip failed")
        if not bindings["seat"] or not bindings["notifier"]:
            raise RuntimeError("compositor does not expose idle notifications")

        idle_opcode = 2 if bindings["notifier_version"] >= 2 else 1
        idle_notification = lib.wl_proxy_marshal_flags(
            bindings["notifier"], idle_opcode,
            ctypes.pointer(notification_interface),
            bindings["notifier_version"], 0, ctypes.c_void_p(),
            ctypes.c_uint(_WAYLAND_IDLE_TIMEOUT_MS),
            ctypes.c_void_p(bindings["seat"]),
        )
        if not idle_notification:
            raise RuntimeError("could not create idle notification")

        @ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
        def on_idled(_data, _notification):
            _record_wayland_idle_event(True)

        @ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
        def on_resumed(_data, _notification):
            _record_wayland_idle_event(False)

        idle_listener = (ctypes.c_void_p * 2)(
            ctypes.cast(on_idled, ctypes.c_void_p).value,
            ctypes.cast(on_resumed, ctypes.c_void_p).value,
        )
        if lib.wl_proxy_add_listener(
            idle_notification,
            ctypes.cast(idle_listener, ctypes.POINTER(ctypes.c_void_p)), None,
        ) != 0:
            raise RuntimeError("could not listen for Wayland idle events")
        if lib.wl_display_roundtrip(display) < 0:
            raise RuntimeError("idle notification roundtrip failed")
        with _WAYLAND_IDLE_LOCK:
            if _WAYLAND_IDLE_STATE["status"] == "starting":
                _WAYLAND_IDLE_STATE["status"] = "ready"

        while lib.wl_display_dispatch(display) >= 0:
            pass
        raise RuntimeError("Wayland idle-event connection closed")
    except Exception as exc:
        with _WAYLAND_IDLE_LOCK:
            _WAYLAND_IDLE_STATE["status"] = "error:%s" % type(exc).__name__


def _wayland_idle_seconds():
    """Return idle duration recorded from compositor idle/resume events."""
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
        return None, None
    with _WAYLAND_IDLE_LOCK:
        if not _WAYLAND_IDLE_STATE["started"]:
            _WAYLAND_IDLE_STATE["started"] = True
            _WAYLAND_IDLE_STATE["monitor_started"] = time.monotonic()
            _WAYLAND_IDLE_STATE["status"] = "starting"
            threading.Thread(target=_run_wayland_idle_monitor, daemon=True).start()
        status = _WAYLAND_IDLE_STATE["status"]
        last_active = _WAYLAND_IDLE_STATE["last_active"]
    if last_active is not None:
        return round(max(0.0, time.monotonic() - last_active), 1), "wayland_ext_idle_notify_v1"
    if status.startswith("error:"):
        return None, "wayland_idle_monitor_unavailable"
    if status == "initial_idle_unknown":
        return None, "wayland_initial_idle_unknown"
    if status == "starting":
        return None, "wayland_ext_idle_notify_v1_starting"
    return None, "wayland_ext_idle_notify_v1_waiting_for_event"


def _time_since_last_active():
    """Best-effort idle duration from the desktop's supported system API.

    KDE Wayland rejects GetSessionIdleTime, so use its input-idle protocol to
    time idle/resume events. If attached while already idle, the protocol
    cannot reveal the prior duration until the next user input resumes it.
    """
    if IS_WINDOWS:
        # GetLastInputInfo reports exactly what the Linux branches below go
        # out of their way to recover, and Windows has no compositor to
        # restrict it.
        idle = _win_idle_seconds()
        if idle is not None:
            return idle, "win32_get_last_input_info"
        return None, "unavailable"

    if _DBUS_OK:
        try:
            bus = dbus.SessionBus()
            if "gnome" in _desktop_kind() or "ubuntu" in _desktop_kind():
                value = bus.call_blocking(
                    "org.gnome.Mutter.IdleMonitor",
                    "/org/gnome/Mutter/IdleMonitor/Core",
                    "org.gnome.Mutter.IdleMonitor", "GetIdletime", "", (),
                )
                return round(float(value) / 1000.0, 1), "mutter_idle_monitor"
            value = bus.call_blocking(
                "org.freedesktop.ScreenSaver", "/ScreenSaver",
                "org.freedesktop.ScreenSaver", "GetSessionIdleTime", "", (),
            )
            return float(value), "freedesktop_screensaver"
        except Exception:
            pass

    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        idle, source = _wayland_idle_seconds()
        if idle is not None or source != "wayland_idle_monitor_unavailable":
            return idle, source

    idle = _x11_idle_seconds()
    if idle is not None:
        return idle, "x11_screensaver"
    is_kde_wayland = (
        "kde" in _desktop_kind()
        and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )
    if is_kde_wayland:
        return None, "unsupported_kde_wayland"
    return None, "unavailable"


def _active_window_kwin():
    """Ask KWin for focused-window identity and desktop-file ID."""
    if not _DBUS_OK or "kde" not in _desktop_kind():
        return None

    result = {}
    loop = GLib.MainLoop()
    detector_path = "/com/crankyclippy/detector/%s" % uuid.uuid4().hex

    class _Detector(dbus.service.Object):
        def __init__(self, conn):
            dbus.service.Object.__init__(self, conn, detector_path)

        @dbus.service.method("com.crankyclippy.detector", in_signature="ssss")
        def Report(self, app_class, caption, window_id, desktop_file_id):
            result["app_class"] = str(app_class)
            result["caption"] = str(caption)
            result["window_id"] = str(window_id)
            result["desktop_file_id"] = str(desktop_file_id)
            loop.quit()

    try:
        session_bus = dbus.SessionBus()
        before = session_bus.get_unique_name()
    except Exception:
        return None

    try:
        # Keep the exported object alive until the callback arrives.  The
        # detector is called from the overlay's polling thread; if this
        # temporary gets collected, DBus unregisters the object and the
        # KWin request times out.  Falling back to AT-SPI in that case can
        # report a different app whose ACTIVE state is stale (or "unknown"
        # for sandboxed apps such as Firefox).
        detector = _Detector(session_bus)
    except Exception:
        return None

    js = (
        "try {\n"
        "    var win = workspace.activeWindow;\n"
        '    var wid = win && win.internalId ? String(win.internalId) : "";\n'
        '    var desktopId = win && win.desktopFileName ? String(win.desktopFileName) : "";\n'
        '    if (win) { callDBus("%s", "%s",'
        ' "com.crankyclippy.detector", "Report",'
        " win.resourceClass, win.caption, wid || String(win.caption || win.resourceClass), desktopId); }\n"
        "} catch (e) {\n"
        '    callDBus("%s", "%s",'
        ' "com.crankyclippy.detector", "Report", "SCRIPT-ERROR", String(e), "", "");\n'
        "}\n" % (before, detector_path, before, detector_path)
    )
    plugin_name = "cranky-detect-%s" % uuid.uuid4().hex[:8]

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        js_path = fh.name

    try:
        session_bus.call_blocking("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                                  "loadScript", "ss", (js_path, plugin_name))
        # start is what actually runs the script on current KWin
        session_bus.call_blocking("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                                  "start", "", ())
        def _timeout():
            loop.quit()
            return False
        GLib.timeout_add_seconds(3, _timeout)
        loop.run()
    except Exception:
        result = {}
    finally:
        # Keep ``detector`` alive through the GLib loop, then unregister its
        # per-call object path so the reused session-bus connection stays clean.
        try:
            detector.remove_from_connection()
        except Exception:
            pass
        try:
            del detector
        except UnboundLocalError:
            pass
        try:
            session_bus.call_blocking("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                                      "unloadScript", "s", (plugin_name,))
        except Exception:
            pass
        finally:
            os.unlink(js_path)

    out = result.get("app_class"), result.get("caption")
    if not out[0]:
        # no focused window at all (or the report timed out) — KWin is
        # authoritative on KDE, so signal "nothing is focused" rather than
        # letting callers fall back to the stale AT-SPI tree
        return {"no_active": True}
    if out[0] == "SCRIPT-ERROR":
        return None
    return {
        "app_class": out[0],
        "caption": out[1],
        "window_id": result.get("window_id"),
        "desktop_file_id": result.get("desktop_file_id"),
    }


def _kwin_activate_and_minimize(target_window_id, expected_active_window_id=None,
                                minimize_if_target_missing=False,
                                minimize_current=True,
                                return_detail=False,
                                target_desktop_file_id=None,
                                target_caption=None):
    """Restore/activate a KWin window and optionally minimize the distraction.

    The active window is minimized only if it is still the exact window Jev
    observed, and only when it differs from the saved task window. This avoids
    minimizing a window the user switched to after Jev's last poll.
    """
    if not _DBUS_OK or "kde" not in _desktop_kind():
        return (False, "KWin control unavailable") if return_detail else False

    result = {"ok": False}
    loop = GLib.MainLoop()
    control_path = "/com/crankyclippy/windowcontrol/%s" % uuid.uuid4().hex

    class _WindowControl(dbus.service.Object):
        def __init__(self, conn):
            dbus.service.Object.__init__(self, conn, control_path)

        @dbus.service.method("com.crankyclippy.windowcontrol", in_signature="bs")
        def Report(self, ok, detail):
            result["ok"] = bool(ok)
            result["detail"] = str(detail)
            loop.quit()

    try:
        session_bus = dbus.SessionBus()
        sender = session_bus.get_unique_name()
        control = _WindowControl(session_bus)
    except Exception:
        return (False, "could not create KWin DBus control") if return_detail else False

    target_id = str(target_window_id or "")
    expected_id = str(expected_active_window_id or "")
    desktop_file_id = str(target_desktop_file_id or "")
    caption = str(target_caption or "")
    js = (
        "try {\n"
        "  var targetId = %s;\n"
        "  var expectedId = %s;\n"
        "  var desktopId = %s;\n"
        "  var targetCaption = %s;\n"
        "  var target = null;\n"
        "  var windows = workspace.windowList();\n"
        "  for (var i = 0; i < windows.length; i++) {\n"
        "    if (String(windows[i].internalId) === targetId) { target = windows[i]; break; }\n"
        "  }\n"
        "  if (!target && desktopId) {\n"
        "    var candidates = windows.filter(function(w) { return String(w.desktopFileName || '') === desktopId; });\n"
        "    target = candidates.find(function(w) { return targetCaption && String(w.caption || '') === targetCaption; }) || (!targetCaption ? candidates[0] : null);\n"
        "  }\n"
        "  if (!target && targetCaption) {\n"
        "    target = windows.find(function(w) { return String(w.caption || '') === targetCaption; }) || null;\n"
        "  }\n"
        "  var active = workspace.activeWindow;\n"
        "  var activeId = active ? String(active.internalId) : \"\";\n"
        "  if (expectedId && activeId !== expectedId) {\n"
        '    callDBus(%s, %s, "com.crankyclippy.windowcontrol", "Report", false, "active window changed");\n'
        "  } else {\n"
        "    if (%s && expectedId && active && activeId === expectedId && activeId !== targetId && active.minimizable && (target || %s)) {\n"
        "      active.minimized = true;\n"
        "    }\n"
        "    if (target) { target.minimized = false; workspace.activeWindow = target; }\n"
        '    callDBus(%s, %s, "com.crankyclippy.windowcontrol", "Report", !!target, target ? "activated" : "target window not found");\n'
        "  }\n"
        "} catch (e) {\n"
        '  callDBus(%s, %s, "com.crankyclippy.windowcontrol", "Report", false, String(e));\n'
        "}\n" % (
        json.dumps(target_id), json.dumps(expected_id),
        json.dumps(desktop_file_id), json.dumps(caption),
        json.dumps(sender), json.dumps(control_path),
            "true" if minimize_current else "false",
            "true" if minimize_if_target_missing else "false",
            json.dumps(sender), json.dumps(control_path),
            json.dumps(sender), json.dumps(control_path),
        )
    )

    plugin_name = "cranky-control-%s" % uuid.uuid4().hex[:8]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        js_path = fh.name
    try:
        session_bus.call_blocking("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                                  "loadScript", "ss", (js_path, plugin_name))
        session_bus.call_blocking("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                                  "start", "", ())
        GLib.timeout_add_seconds(3, lambda: (loop.quit(), False)[1])
        loop.run()
    except Exception as exc:
        print("[DesktopState] KWin window control failed: %s" % exc)
    finally:
        try:
            control.remove_from_connection()
        except Exception:
            pass
        try:
            session_bus.call_blocking("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                                      "unloadScript", "s", (plugin_name,))
        except Exception:
            pass
        try:
            os.unlink(js_path)
        except OSError:
            pass
    if return_detail:
        return result["ok"], result.get("detail", "KWin control timed out")
    return result["ok"]


def _active_window_atspi():
    """AT-SPI fallback: return (app_name, window_title) of the ACTIVE frame,
    or (None, None). Note: on KDE this can be stale — the KWin backend wins."""
    if not _ATSPI_OK:
        return None, None
    try:
        desktop = Atspi.get_desktop(0)
    except Exception:
        return None, None
    for i in range(desktop.get_child_count()):
        try:
            app = desktop.get_child_at_index(i)
        except Exception:
            continue
        try:
            app_name = (app.get_name() or "").lower()
        except Exception:
            continue
        if app_name in (s.strip() for s in SHELL_APPS):
            continue
        try:
            frame_count = app.get_child_count()
        except Exception:
            continue  # app died between listing and walking
        for j in range(frame_count):
            try:
                frame = app.get_child_at_index(j)
                if frame is None:
                    continue
                if frame.get_role() not in (Atspi.Role.FRAME, Atspi.Role.DIALOG):
                    continue
                if frame.get_state_set().contains(Atspi.StateType.ACTIVE):
                    return app_name, (frame.get_name() or "")
            except Exception:
                continue
    return None, None


def _find_app_frame(app_key, caption):
    """Locate the focused app's AT-SPI frame (for deep reads like the URL
    bar). Preference: ACTIVE frame -> exact caption match -> first window.

    App ids never line up perfectly between KWin ("chrome") and AT-SPI
    ("google-chrome"), so the name check tries exact, then suffix forms.
    """
    if not _ATSPI_OK:
        return None

    def _norm(s):
        return re.sub(r"[-_.\s]", "", (s or "").strip().lower())

    target = _norm(app_key)
    if not target:
        return None
    fallback = None
    caption_match = None
    try:
        desktop = Atspi.get_desktop(0)
        desktop_count = desktop.get_child_count()
    except Exception:
        return None
    for i in range(desktop_count):
        try:
            app = desktop.get_child_at_index(i)
            aname = _norm(app.get_name())
            if aname != target and not aname.endswith(target) and not target.endswith(aname):
                continue
        except Exception:
            continue
        try:
            frame_count = app.get_child_count()
        except Exception:
            continue  # app died between listing and walking
        for j in range(frame_count):
            try:
                frame = app.get_child_at_index(j)
                title = frame.get_name() or ""
            except Exception:
                continue
            fallback = fallback or frame
            if caption and _norm(title) == _norm(caption) and caption_match is None:
                caption_match = frame
    if caption_match is not None:
        return caption_match
    # Never read an address bar from a different browser window.
    return None if caption else fallback


def _walk(node, max_depth=12, max_nodes=1500):
    """Iterate (node, depth) over an AT-SPI subtree, best effort."""
    budget = max_nodes
    stack = [(node, 0)]
    while stack and budget > 0:
        n, d = stack.pop()
        budget -= 1
        yield n, d
        if d >= max_depth:
            continue
        try:
            for i in range(n.get_child_count()):
                try:
                    child = n.get_child_at_index(i)
                    if child is not None:
                        stack.append((child, d + 1))
                except Exception:
                    pass
        except Exception:
            pass


def _node_text(node):
    """Best-effort text of a text/entry AT-SPI node."""
    for getter in (lambda: node.get_text(0, -1), lambda: node.query_text().get_text(0, -1)):
        try:
            t = getter()
            if t and "." not in t.expandtabs(0)[:0]:
                return t
        except Exception:
            pass
    return None


def _decompress_mozlz4(raw):
    """Decompress Firefox's mozlz4 file (mozLz40 header + raw LZ4 block)."""
    if not raw.startswith(b"mozLz40\0"):
        raise ValueError("not a mozlz4 file")
    out = bytearray()
    pos = 12  # magic (8) + original size (4)
    n = len(raw)
    while pos < n:
        token = raw[pos]
        pos += 1
        lit_len = token >> 4
        if lit_len == 15:
            while raw[pos] == 255:
                lit_len += 255
                pos += 1
            lit_len += raw[pos]
            pos += 1
        out += raw[pos:pos + lit_len]
        pos += lit_len
        if pos >= n:
            break
        offset = int.from_bytes(raw[pos:pos + 2], "little")
        pos += 2
        match_len = token & 0x0F
        if match_len == 15:
            while True:
                b = raw[pos]
                pos += 1
                match_len += b
                if b != 255:
                    break
        match_len += 4
        start = len(out) - offset
        for i in range(match_len):
            out.append(out[start + i])
    return bytes(out)


def _firefox_session_file(browser_key="firefox"):
    """Newest live sessionstore file across debs, snaps, flatpaks and the
    Gecko soft-forks (LibreWolf, Waterfox, Zen, Floorp)."""
    import glob as _glob

    newest = None
    for pattern in gecko_profile_roots(browser_key):
        for root in _glob.glob(_expand_profile_pattern(pattern)):
            try:
                profiles = os.scandir(root)
            except OSError:
                continue
            for profile in profiles:
                if not profile.is_dir():
                    continue
                for name in ("recovery.jsonlz4", "previous.jsonlz4"):
                    path = os.path.join(root, profile.name, "sessionstore-backups", name)
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    if newest is None or mtime > newest[0]:
                        newest = (mtime, path)
    if newest is None:
        return None
    try:
        with open(newest[1], "rb") as fh:
            raw = fh.read()
        return json.loads(_decompress_mozlz4(raw))
    except Exception:
        return None


def _tab_entry(tab):
    """url + title of the tab's current history entry."""
    entries = tab.get("entries") or []
    i = (tab.get("index") or 1) - 1
    entry = entries[i] if 0 <= i < len(entries) else {}
    return (entry.get("url") or tab.get("url") or ""), (entry.get("title") or tab.get("title") or "")


def _firefox_focus(window_title, browser_key="firefox"):
    """Return the tab dict most likely to be the focused Gecko-family tab."""
    session = _firefox_session_file(browser_key)
    if not session:
        return None
    needle = _tab_title_from_window(window_title)  # caption minus "- Firefox"
    fallback = None
    for window in session.get("windows", []):
        for idx, tab in enumerate(window.get("tabs", [])):
            url, title = _tab_entry(tab)
            cand = {
                "url": url,
                "title": title,
                "tab": tab,
                "window": window,
                "idx": idx,
                "lastAccessed": tab.get("lastAccessed") or 0,
            }
            if fallback is None or cand["lastAccessed"] > fallback["lastAccessed"]:
                fallback = cand
            if _title_matches(needle, title):
                return cand
    return None  # do not label an unmatched background tab as active


def _firefox_other_domains(window, exclude_idx, limit=8):
    """Domains of the other open tabs in the window, most recent first."""
    per_domain = {}
    for idx, tab in enumerate(window.get("tabs", [])):
        if idx == exclude_idx:
            continue
        url, _ = _tab_entry(tab)
        if not url.startswith("http"):
            continue
        domain = _domain_from_url(url)
        if domain:
            per_domain[domain] = max(per_domain.get(domain, 0), tab.get("lastAccessed") or 0)
    return [d for d, _ in sorted(per_domain.items(), key=lambda kv: kv[1], reverse=True)[:limit]]


def _read_browser_url(frame):
    """Read the URL from the browser's address bar, if accessibility allows.

    Firefox exposes its URL bar as "Search or enter web address", Chrome
    family as "Address and search bar"/"Omnibox". Anything that looks like
    a URL is taken; None when the browser does not cooperate.
    """
    url_re = re.compile(r"^[a-z][a-z0-9+.-]*://|^(about|chrome|moz):/", re.I)
    for node, _ in _walk(frame):
        if node.get_role() not in (Atspi.Role.ENTRY, Atspi.Role.TEXT):
            continue
        name = (node.get_name() or "").lower()
        text = _node_text(node)
        # Page text may contain URLs too; only address-bar controls count.
        is_address = any(hint in name for hint in ("address", "url", "location", "search or enter"))
        if is_address and text and url_re.match(text.strip()):
            return text.strip()
        if not text and ("address" in name or "url" in name or "enter" in name):
            # the url-bar entry exists but its text came back empty
            continue
    return None


def _chromium_history_db(app_key):
    """Path of the History DB with the most recent mtime, or None.

    Each root is either a profile directory itself (History directly
    inside — Opera's layout) or a parent of profile directories
    (Chrome's Default/, Profile 1/, ...).
    """
    import glob as _glob

    newest = None
    for pattern in chromium_profile_roots(app_key):
        root = _expand_profile_pattern(pattern)
        if os.path.isdir(root):
            search = [root]
        else:
            search = [r for r in _glob.glob(root) if os.path.isdir(r)]
        for base in search:
            for profile_dir in [base] + [
                p.path for p in os.scandir(base) if p.is_dir()
            ]:
                if os.path.basename(profile_dir).lower() in _PROFILE_DIR_SKIPS:
                    continue
                hist = os.path.join(profile_dir, "History")
                try:
                    mtime = os.path.getmtime(hist)
                except OSError:
                    continue
                if newest is None or mtime > newest[0]:
                    newest = (mtime, hist)
    return newest[1] if newest else None


def _chromium_history_rows(app_key, limit=60):
    """Most recent visits as (url, title, last_visit_time) tuples, or []."""
    import shutil
    import sqlite3

    db = _chromium_history_db(app_key)
    if not db:
        return []
    tmp = os.path.join(tempfile.gettempdir(), "cranky-%s-%d-History" % (app_key, os.getpid()))
    try:
        shutil.copy2(db, tmp)
        # include the WAL companion or recent writes may be invisible
        for ext in ("-wal", "-shm"):
            try:
                shutil.copy2(db + ext, tmp + ext)
            except OSError:
                pass
        conn = sqlite3.connect(tmp)
        try:
            rows = conn.execute(
                "SELECT url, title, last_visit_time FROM urls "
                "ORDER BY last_visit_time DESC LIMIT %d" % limit
            ).fetchall()
        finally:
            conn.close()
        return rows
    except Exception:
        return []
    finally:
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(tmp + ext)
            except OSError:
                pass


def _clean_chromium_title(title):
    return re.sub(r"^\(\d+\)\s*", "", (title or "").strip())


def _title_matches(needle, title):
    """Needle-vs-title match that tolerates prefixes/suffixes but refuses
    trivial hits (e.g. one-word title 'X' matching '(3) Home / X')."""
    if not needle or not title:
        return False
    t = _clean_chromium_title(title).lower()
    n = _clean_chromium_title(needle).lower()
    if t == n:
        return True
    if len(t) >= 15 and t in n:
        return True
    if len(n) >= 15 and n in t:
        return True
    return False


def _chromium_history_url(app_key, window_title):
    """Best-guess URL + title of the page in the focused Chromium window."""
    rows = _chromium_history_rows(app_key)
    if not rows:
        return None
    needle = _tab_title_from_window(window_title)
    if needle:
        for url, title, _t in rows:
            if _title_matches(needle, title):
                return url, title
    return None  # latest visit is not necessarily the active tab


def _chromium_recent_domains(app_key, needle, limit=8):
    """Domains from the most recent browsing (current tab first)."""
    rows = _chromium_history_rows(app_key)
    out, self_domain = {}, None
    for url, title, t in rows:
        domain = _domain_from_url(url)
        if not domain or not url.startswith("http"):
            continue
        if needle and self_domain is None and _title_matches(needle, title):
            self_domain = domain   # skip the focused tab's domain entirely
            continue
        if self_domain and domain == self_domain:
            continue
        out.setdefault(domain, t)
    return [d for d in sorted(out, key=out.get, reverse=True)][:limit]


def _domain_from_url(url):
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
        # drop the cosmetic subdomains we add to match keys: open./www.;
        # music. is kept so YouTube Music can be told apart from YouTube
        return re.sub(r"^(open|www)\.", "", host)
    except Exception:
        return None


# Optional hook: jev_overlay registers the browser-extension bridge here so the
# focused tab can be read on Windows, where no profile file exposes it. Linux
# uses the same live bridge before the sessionstore/History readers.
_ACTIVE_TAB_QUERY = None
_IGNORED_WINDOW_TITLES = set()


def set_active_tab_query(callback):
    """Register callable(browser_key) -> {url, title, window_title, ...}."""
    global _ACTIVE_TAB_QUERY
    _ACTIVE_TAB_QUERY = callback


def set_ignored_window_titles(titles):
    """Register window titles that belong to this project, not to the user.

    The status widget and Clippy itself are windows like any other, so Windows
    reports them as the focused app - and Jev would then judge our own window
    as a distraction. Registering their title makes the detector report
    "nothing is focused" instead, which is what pausing the poll already
    expects.
    """
    global _IGNORED_WINDOW_TITLES
    _IGNORED_WINDOW_TITLES = {str(title).strip().casefold() for title in titles if title}


def _live_browser_tab(app_key, window_title):
    """The focused browser tab, as reported by the installed extension.

    On Windows this is the only way to see the real current tab: Chromium keeps
    no readable "current tab" on disk, and Windows has no accessibility tree
    this project can read without extra packages. The extension does have the
    tabs permission, so jev_overlay wires the bridge in through
    set_active_tab_query(). Returns None when no extension answered.

    The answer is only trusted when it belongs to the window that is actually
    focused. A browser captions its window "<tab title> - <browser name>", and
    Chromium's extension API does not even expose window titles, so the tab
    title the extension reports is matched against the detected window title
    instead - exactly, or as the caption that produced it.
    """
    query = _ACTIVE_TAB_QUERY
    if query is None:
        return None
    try:
        live = query(app_key)
    except Exception as exc:
        print("[DesktopState] Browser tab query failed: %s" % exc)
        return None
    if not isinstance(live, dict) or not live.get("url"):
        return None
    live_title = (live.get("window_title") or live.get("title") or "").strip()
    if not window_title or not live_title:
        return None
    caption = window_title.strip().casefold()
    tab_caption = (_tab_title_from_window(window_title) or "").casefold()
    if live_title.casefold() not in (caption, tab_caption):
        return None
    return live


def _collect_browser_metadata(app_key, window_title, frame):
    """URL/tab title for the focused browser window.

    Firefox: sessionstore file (its URL bar is AppArmor-fenced in the snap).
    Chromium family: the extension's active tab when one is installed, else an
    AT-SPI address bar read when the browser exposes one, otherwise its History
    DB, matching the window title against the most recent visits.
    """
    url = None
    tab_title = None
    history = None

    # Prefer the live browser extension on Linux as well as Windows.
    live = _live_browser_tab(app_key, window_title)
    if live:
        url = live.get("url") or None
        tab_title = live.get("title") or None
        history = live.get("other_domains") or None

    if url is None and app_key in _GECKO_SESSIONSTORE_BROWSERS:
        info = _firefox_focus(window_title, app_key)
        if info:
            url = info["url"] or None
            tab_title = info["title"] or None
            history = _firefox_other_domains(info["window"], info["idx"])
    elif url is None:
        url = _read_browser_url(frame) if frame is not None else None
        if not url:
            info = _chromium_history_url(app_key, window_title)
            if info:
                url = info[0]
                tab_title = info[1]
        if not tab_title:
            tab_title = _tab_title_from_window(window_title)
        if not history and url:
            history = _chromium_recent_domains(
                app_key, _tab_title_from_window(window_title))

    if not tab_title:
        tab_title = _tab_title_from_window(window_title)
    domain = _domain_from_url(url) if url else None
    fields = {
        "site_url": url,
        "site_domain": domain,
        "tab_title": tab_title,
        # focus timing needs a running tracker; single-shot runs report None
        "tab_focus_seconds": None,
        "history_domains": history,
    }
    return {k: fields.get(k) for k in APP_METADATA["browser"][app_key]}


# Windows chat clients whose single context field is the chat/room name, and
# meeting apps whose title is just the meeting name.
_WINDOWS_CHAT_NAME_APPS = {
    "wechat", "line", "threema", "trillian", "session",
}
_WINDOWS_MEETING_APPS = {
    "zoom", "webex", "gotomeeting", "bluejeans",
}


def _title_parts(window_title):
    parts = [p.strip() for p in (window_title or "").split(" - ") if p.strip()]
    # KDE apps (Dolphin etc.) split with an em/en dash: "folder — Dolphin"
    out = []
    for p in parts:
        out.extend(q.strip() for q in re.split(r"\s+[—–]\s+", p) if q.strip())
    return out


def _collect_generic_metadata(cls, app_key, window_title):
    """Best-effort metadata for non-browser apps.

    Everything truly derivable from the window title goes in; fields that
    need deeper per-app integrations stay None for now (Jev tolerates it).
    """
    parts = _title_parts(window_title)
    meta = {}
    for field in APP_METADATA[cls][app_key]:
        meta[field] = None

    if cls == "editor":
        # "file.py - project - Visual Studio Code" / "file.py — Kate" /
        # "file.py - Vim": first part is the file; workspace (rarely) in
        # parts[1] only when it is not the app's own identify.
        identity = {
            "visual studio code", "kate", "vim", "neovim", "emacs",
            "sublime text", "zed", "code", "cursor", "windsurf",
        }
        if parts:
            meta["file_path"] = parts[0]
            base = os.path.basename(parts[0])
            if IS_WINDOWS:
                # Notepad marks unsaved changes with a leading "*" and shows
                # "Untitled" for a new document; neither is part of the name.
                base = base.lstrip("*")
                if base.casefold() == "untitled":
                    base = ""
            if "document_name" in meta:
                meta["document_name"] = base or None
            for p in parts[1:]:
                if p.lower() not in identity:
                    if "workspace_name" in meta:
                        meta["workspace_name"] = p
                    break
            ext = base.rsplit(".", 1)[-1]
            if "language" in meta and "." in base and 1 <= len(ext) <= 5:
                meta["language"] = ext
    elif cls == "video" or cls == "media":
        # "video_title - VLC media player" / "<title> - mpv" /
        # "<track> - Spotify"
        meta["media_title"] = _tab_title_from_window(window_title)
    elif cls in ("chat", "collaboration"):
        # Discord: "server - channel - Discord"
        context = _tab_title_from_window(window_title)
        if app_key == "teams":
            meta["channel_or_chat_name"] = context
        elif app_key == "slack":
            meta["channel_name"] = context
        elif app_key == "google_chat":
            meta["space_or_chat_name"] = context
        elif app_key in ("mattermost", "rocketchat"):
            meta["channel_name"] = context
        elif app_key == "zulip":
            meta["stream_name"] = context
        elif IS_WINDOWS and app_key in _WINDOWS_CHAT_NAME_APPS:
            # Windows chat clients ("#general - Element", a chat name in the
            # title) have a single context field, exactly like Telegram's.
            meta["chat_name"] = context
        elif IS_WINDOWS and app_key in _WINDOWS_MEETING_APPS:
            # Zoom/Webex titles are just the meeting name.
            meta["meeting_name"] = context
        elif parts and "?" not in parts[-1]:
            meta["server_or_dm_name"] = parts[0]
        if window_title and ("voice" in window_title.lower() or "call" in window_title.lower()):
            if "voice_call_active" in meta:
                meta["voice_call_active"] = True
            if "call_active" in meta:
                meta["call_active"] = True
        if window_title and "huddle" in window_title.lower() and "huddle_active" in meta:
            meta["huddle_active"] = True
        if window_title and "stream" in window_title.lower():
            meta["streaming"] = True
    elif cls == "project":
        # "<project or board> - Jira", "<board> - Trello", "PAY-123 fix - Linear"
        context = _tab_title_from_window(window_title)
        if "workspace_name" in meta:
            meta["workspace_name"] = context
        if "board_name" in meta and parts:
            meta["board_name"] = parts[0]
        if "project_name" in meta and parts:
            meta["project_name"] = parts[0]
        if "issue_key" in meta and parts:
            match = re.match(r"^([A-Z][A-Z0-9]{1,9}-\d+)\b", window_title or "")
            meta["issue_key"] = match.group(1) if match else None
        for field in ("space_name", "story_name", "task_list_name"):
            if field in meta and parts:
                meta[field] = parts[0]
    elif cls == "email":
        context = _tab_title_from_window(window_title)
        if context:
            common_folders = {
                "inbox", "sent", "sent mail", "drafts", "archive", "trash",
                "junk", "spam", "all mail", "starred",
            }
            if context.strip().lower() in common_folders:
                meta["mail_folder"] = context
            else:
                meta["mail_subject"] = context
    elif cls == "document":
        if parts:
            meta["document_name"] = parts[0]
    elif cls == "desktop":
        # Window titles are "<what is open><dash><App name>". The first
        # part is usually the thing being worked on (folder, settings
        # panel, archive, ...); polish per app.
        thing = None
        for p in parts:
            if p.lower() not in _DESKTOP_APP_DISPLAY_NAMES.get(app_key, {app_key}):
                thing = p
                break
        if IS_WINDOWS:
            # Windows titles are usually just the app's own name, so `thing`
            # is empty for most of these; the panel/process name is the title.
            if app_key in ("explorer",):
                # File Explorer's title is the folder it is showing.
                meta["location_name"] = _tab_title_from_window(window_title)
            elif app_key in (
                "settings", "control_panel", "windows_security",
                "ease_of_access", "xbox_app_settings",
            ):
                meta["panel_name"] = _tab_title_from_window(window_title)
            elif app_key == "task_manager":
                meta["process_name"] = thing
            elif app_key == "registry_editor":
                meta["key_name"] = thing
            elif app_key == "services":
                meta["service_name"] = thing
            elif app_key == "device_manager":
                meta["device_name"] = thing
            elif app_key == "disk_management":
                meta["disk_name"] = thing
            elif app_key in ("photo_viewer", "paint", "photos"):
                meta["image_name"] = _tab_title_from_window(window_title)
            elif app_key == "snipping_tool":
                meta["capture_mode"] = _tab_title_from_window(window_title)
            elif app_key == "store":
                meta["page_name"] = _tab_title_from_window(window_title)
            else:
                # Most of these windows carry only their own name, so the tool
                # name is the whole title.
                meta["tool_name"] = thing or _tab_title_from_window(window_title)
            return meta
        if app_key == "dolphin":
            # title is "<folder — Dolphin"; real full path needs a deep
            # read, so the folder name goes to location_name for now
            meta["location_name"] = thing
        elif app_key in ("systemsettings", "kinfocenter"):
            meta["panel_name"] = thing
        elif app_key == "ark":
            meta["archive_name"] = thing
        elif app_key == "spectacle":
            meta["capture_mode"] = thing
        elif app_key == "discover":
            meta["page_name"] = thing
        elif app_key == "kdeconnect":
            meta["device_name"] = thing
        elif app_key == "partitionmanager":
            meta["disk_name"] = thing
        elif app_key in ("kolourpaint", "gwenview"):
            meta["image_name"] = thing
        elif app_key == "github-desktop":
            meta["repo_name"] = thing
        elif app_key == "nordvpn":
            meta["panel_name"] = thing
        else:
            meta["tool_name"] = thing
    elif cls == "terminal":
        meta["session_context"] = window_title or None
        tab_name = _tab_title_from_window(window_title)
        if tab_name:
            meta["tab_name"] = tab_name
    elif cls == "game":
        # launchers show the current page in the title; exact page name only
        # when it is not just the launcher's own name.
        display = _GAME_DISPLAY_NAMES.get(app_key, {app_key})
        for p in parts:
            if p.lower() not in display:
                meta["game_name"] = p
                break
    elif cls in ("assistant", "ai_coding"):
        if window_title:
            meta["window_context"] = window_title
            if cls == "ai_coding" and app_key in {"cursor", "windsurf", "zed"} and parts:
                meta["file_path"] = parts[0]
            if parts and parts[0].lower() not in {
                app_key, "opencode", "openwork", "antigravity", "zcode",
                "cursor", "windsurf", "zed", "codex", "claude code",
            }:
                meta["workspace_name"] = parts[0]
    elif cls == "ai_chat":
        if window_title:
            meta["conversation_title"] = window_title
    elif cls == "creative":
        if parts:
            if app_key == "krita":
                meta["document_name"] = parts[0]
                meta["canvas_name"] = parts[0]
            elif app_key == "cura_slicer":
                meta["model_name"] = parts[0]
    return meta


def _youtube_video_id(url):
    """v= parameter or /shorts/<id> path segment; None when not a watch URL."""
    try:
        query = urllib.parse.parse_qs(urlparse(url).query) if url else {}
        if "v" in query and query["v"]:
            return query["v"][0]
        m = re.search(r"/shorts/([A-Za-z0-9_-]{5,20})", url or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _youtube_channel_from_url(url):
    """Channel name via YouTube's public oEmbed endpoint (unauthenticated).

    network-bound, so strictly best-effort: any failure returns None.
    """
    video_id = _youtube_video_id(url)
    if not video_id:
        return None
    try:
        import urllib.parse
        import urllib.request

        embed_url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(
            "https://www.youtube.com/watch?v=" + video_id, safe=""
        )
        with urllib.request.urlopen(embed_url, timeout=2) as resp:
            data = json.loads(resp.read().decode())
        author = data.get("author_name")
        return author if author else None
    except Exception:
        return None


def _collect_webapp_metadata(webapp_key, site_url, tab_title, window_title):
    """Best-effort site metadata from URL/tab title; None where unavailable."""
    meta = {k: None for k in WEBAPP_METADATA.get(webapp_key, [])}
    if webapp_key == "_default":
        return meta

    if webapp_key in EMAIL_WEBAPP_PROVIDERS:
        meta["mail_provider"] = EMAIL_WEBAPP_PROVIDERS[webapp_key]
        title = (tab_title or window_title or "").strip()
        provider_names = "|".join(
            re.escape(name)
            for name in (
                "Gmail", "Outlook Web", "Outlook", "Yahoo Mail", "Proton Mail",
                "Fastmail", "Zoho Mail", "AOL Mail",
            )
        )
        title = re.sub(
            r"\s+[-\u2013\u2014|]\s+(?:%s)$" % provider_names,
            "",
            title,
            flags=re.I,
        ).strip()
        meta["mail_subject"] = title or None
        return meta

    if webapp_key in AI_CHAT_WEBAPP_PROVIDERS:
        meta["ai_provider"] = AI_CHAT_WEBAPP_PROVIDERS[webapp_key]
        meta["conversation_title"] = tab_title or window_title or None
        return meta

    if webapp_key == "youtube.com":
        # page title is usually "<video title> - YouTube"; live/premiere
        # titles may carry a "(N) watching" prefix — drop it
        m = re.search(r"^(.*)\s+-\s+YouTube$", tab_title or window_title or "")
        if m:
            meta["video_title"] = re.sub(r"^\(\d+\)\s*", "", m.group(1).strip())
        if site_url:
            meta["channel_name"] = _youtube_channel_from_url(site_url)
    elif webapp_key == "youtube_music":
        m = re.search(r"^(.*)\s+-\s+YouTube Music$", tab_title or window_title or "")
        if m:
            meta["track_title"] = m.group(1).strip()
        if site_url:
            meta["channel_name"] = _youtube_channel_from_url(site_url)
    elif webapp_key == "netflix.com":
        m = re.search(r"^(.*)\s+\|\s+Netflix.*$", tab_title or window_title or "")
        if m:
            meta["show_title"] = m.group(1).strip()
    elif webapp_key == "twitch.tv":
        m = re.search(r"^(.*)\s+[-\u2013\u2014]\s+Twitch$", tab_title or window_title or "")
        url_streamer = None
        if site_url:
            path = urlparse(site_url).path.strip("/")
            first = path.split("/")[0] if path else ""
            if first and first.lower() not in (
                "directory", "videos", "downloads", "settings", "jobs", "store", "turbo",
            ):
                url_streamer = first
        if url_streamer:
            meta["streamer_name"] = url_streamer
        if m:
            # "streamer - stream title - Twitch" or just "streamer - Twitch"
            segs = [s.strip() for s in m.group(1).split(" - ") if s.strip()]
            if len(segs) >= 2:
                if not url_streamer:
                    meta["streamer_name"] = segs[0]
                meta["stream_title"] = " - ".join(segs[1:])
            elif len(segs) == 1 and not url_streamer:
                meta["stream_title"] = segs[0]
    elif webapp_key == "spotify.com":
        combined = tab_title or window_title or ""
        m = re.search(r"^(.*)\s+\|\s+Spotify(.*)$", combined)
        if m:
            name = m.group(1).strip()
            if m.group(2).lower().startswith(" playlist"):
                meta["playlist_name"] = name
            else:
                meta["track_title"] = name
    elif webapp_key == "reddit.com":
        meta["site_url"] = site_url
        combined = tab_title or ""
        m = re.search(
            r"^(.*\S)\s+(?:[-\u2013\u2014]|\u2022)\s+(r/[\w-]+)\s*(?:[-\u2013\u2014]\s+Reddit.?)?$",
            combined,
        )
        if m:
            meta["post_title"] = m.group(1).strip()
            meta["subreddit"] = m.group(2).strip()
        else:
            m2 = re.search(r"^(.*\S)\s+[-\u2013\u2014]\s+Reddit.?$", combined)
            if m2:
                meta["post_title"] = m2.group(1).strip()
    elif webapp_key == "docs.google.com":
        m = re.search(r"^(.*)\s+[-\u2013\u2014]\s+Google Docs$", tab_title or window_title or "")
        name = m.group(1).strip() if m else (tab_title or "").strip()
        if name:
            meta["document_name"] = name
    elif webapp_key == "notion.so":
        m = re.search(r"^(.*\S)\s+[-\u2013\u2014|]\s+Notion.?$", tab_title or window_title or "")
        name = m.group(1).strip() if m else (tab_title or "").strip()
        if name:
            meta["page_name"] = name
    elif webapp_key == "overleaf.com":
        m = re.search(r"^(.*)\s+-\s+Overleaf", tab_title or window_title or "")
        if m:
            meta["project_name"] = m.group(1).strip()
    return meta


def get_desktop_state(user_goal=None):
    """Collect what the user is doing right now; returns the state dict."""
    if IS_WINDOWS:
        # Win32 answers this directly on every Windows desktop.
        active = _win_active_window()
        detection_backend = "win32"
    else:
        active = _active_window_kwin()
        detection_backend = "kwin"
        if active is None and "gnome" in _desktop_kind():
            active = gnome_focus()
            detection_backend = "gnome_dbus"
        if active is None:
            active = x11_focus()
            detection_backend = "x11"

    if active is not None and (active.get("caption") or "").strip().casefold() in _IGNORED_WINDOW_TITLES:
        active = {"no_active": True}
    if active is not None and active.get("no_active"):
        # On KDE, KWin is the only source of truth: if it says nothing is
        # focused (desktop peek, no focus, or between windows) we report
        # exactly that instead of the stale accessibility tree. Win32's answer
        # is just as definitive, and says the same thing when the desktop,
        # taskbar or Start menu owns the foreground.
        idle_seconds, idle_source = _time_since_last_active()
        universal = {
            "app_focused_name": "none",
            "app_class": "none",
            "window_title": None,
            "window_id": None,
            "desktop_file_id": None,
            "detection_backend": detection_backend,
            "focus_lost": True,
            "time_since_window_focused": _time_since_window_focused(None),
            "time_since_last_active": idle_seconds,
            "activity_time_source": idle_source,
            "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if user_goal:
            universal["user_goal"] = user_goal
        return {"universal": universal}

    if active is not None:
        state_source = detection_backend
        raw_app_name = active["app_class"]
        window_title = active["caption"]
        raw_window_id = active.get("window_id")
        raw_desktop_file_id = active.get("desktop_file_id")
    else:
        state_source = "atspi"
        raw_app_name, window_title = _active_window_atspi()
        raw_window_id = None
        raw_desktop_file_id = None

    # Only Windows has an executable path to describe the app with; it is kept
    # here (out of the state) rather than added to the universal block.
    process_path = _LAST_WINDOWS_WINDOW.get("process_path") if IS_WINDOWS else None

    if raw_app_name:
        window_identity = (
            (state_source, str(raw_window_id))
            if raw_window_id
            else (state_source, raw_app_name.strip().lower(), window_title or "")
        )
    else:
        window_identity = None
    time_since_window_focused = _time_since_window_focused(window_identity)
    time_since_last_active, activity_time_source = _time_since_last_active()

    cls, app_key = _match_app(raw_app_name, window_title)
    # Deep reads (browser URL bar) go through the accessibility tree; look
    # up the frame by the canonical app key when we have one.
    want_frame = app_key or _canonical(raw_app_name)
    frame = _find_app_frame(want_frame, window_title) if want_frame else None

    universal = {
        "app_focused_name": app_key or (raw_app_name or "").strip().lower() or "unknown",
        "app_class": cls or "unknown",
        "window_title": window_title,
        "window_id": raw_window_id,
        "desktop_file_id": raw_desktop_file_id,
        "detection_backend": state_source,
        "focus_lost": False,
        "detection_status": "ok" if raw_app_name else "unavailable",
        "detection_hint": None if raw_app_name else (
            "GNOME Wayland needs the Focused Window D-Bus extension enabled; "
            "X11 needs xprop; AT-SPI needs gi/Atspi and app accessibility enabled."
            if not IS_WINDOWS else "Win32 could not read the foreground identity."
        ),
        "time_since_window_focused": time_since_window_focused,
        "time_since_last_active": time_since_last_active,
        "activity_time_source": activity_time_source,
        "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if user_goal:
        universal["user_goal"] = user_goal

    state = {"universal": universal}

    if cls:
        if cls == "browser":
            state["app_metadata"] = _collect_browser_metadata(app_key, window_title, frame)
        else:
            state["app_metadata"] = _collect_generic_metadata(cls, app_key, window_title)
        app_description = APP_DESCRIPTIONS.get(app_key)
        description_source = "app_catalog" if app_description else None
        if not app_description:
            app_description, description_source = _installed_app_description(
                raw_app_name, raw_desktop_file_id, app_key, process_path
            )
        if not app_description:
            app_description = (
                "A %s application; use its window title and content to judge its relevance."
                % cls
            )
            description_source = "category_fallback"
        state["app_metadata"]["app_description"] = app_description
        state["app_metadata"]["app_description_source"] = description_source
    else:
        # Preserve the actual identity even when this app has no specialized
        # extractor yet; this is safer than giving Jev a blank state.
        app_description, description_source = _installed_app_description(
            raw_app_name, raw_desktop_file_id, None, process_path
        )
        state["app_metadata"] = {
            "app_description": app_description or (
                "Purpose not yet catalogued; do not assume this application is related to the user's goal."
            ),
            "app_description_source": description_source or "unknown_fallback",
        }

    if cls == "browser":
        md = state["app_metadata"]
        # A verified URL outranks title hints, which can name another site.
        hinted = _match_webapp_from_title(md.get("tab_title"))
        webapp_key = _match_webapp(md.get("site_domain")) if md.get("site_domain") else hinted
        if webapp_key:
            matched_via = "url" if md.get("site_domain") else "tab_title"
        else:
            webapp_key = _match_webapp(md.get("site_domain"))
            matched_via = "url"
        if webapp_key and webapp_key != "_default":
            webapp = {
                "site_domain": md.get("site_domain"),
                "matched_via": matched_via,
                "category": WEBAPP_CATEGORIES.get(webapp_key, "webapp"),
                "metadata": _collect_webapp_metadata(
                    webapp_key,
                    md.get("site_url"),
                    md.get("tab_title"),
                    window_title,
                ),
            }
            if webapp_key in EMAIL_WEBAPP_PROVIDERS:
                provider = EMAIL_WEBAPP_PROVIDERS[webapp_key]
                webapp["app_description"] = (
                    "%s is a web email client; it is not a chat app or brief chat check-in."
                    % provider
                )
            elif webapp_key in AI_CHAT_WEBAPP_PROVIDERS:
                webapp["app_description"] = AI_CHAT_WEBAPP_DESCRIPTIONS[webapp_key]
            state["webapp"] = webapp
    return state


def _print_state(state):
    """Human-readable test report: universal -> app metadata -> web metadata."""
    u = state.get("universal", {})
    print("== UNIVERSAL ==")
    for k, v in u.items():
        print(f"{k}: {v}")
    if "app_metadata" in state:
        print("\n== APP METADATA ==")
        for k, v in state["app_metadata"].items():
            print(f"{k}: {v}")
    if "webapp" in state:
        print(f"\n== WEBAPP: {state['webapp']['site_domain']} ==")
        for k, v in state["webapp"]["metadata"].items():
            print(f"{k}: {v}")


def _main():
    state = get_desktop_state()
    _print_state(state)


if __name__ == "__main__":
    _main()
