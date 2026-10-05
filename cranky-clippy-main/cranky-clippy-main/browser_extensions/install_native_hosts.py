"""Register Jev's native-messaging bridge for this user's Firefox and Chrome.

Linux/macOS keep the original behaviour (a manifest inside each browser's own
NativeMessagingHosts directory, with the Python script itself as the host).
Windows has its own rules and gets its own branch: manifests go to the
per-user app-data locations *and* to the HKCU registry keys Chromium reads,
and the host path has to be an executable, so a small .cmd wrapper is written
that launches this Python interpreter.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"


ROOT = Path(__file__).resolve().parent
HOST_NAME = "com.crankyclippy.bridge"
CHROME_EXTENSION_ID = "kfcdbpbcgbnchboagjfonaegocnpglbh"
SOURCE_HOST = ROOT / "native_messaging_host.py"


def write_manifest(path, executable, browser):
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": HOST_NAME,
        "description": "Cranky Clippy browser tab bridge",
        "path": str(executable),
        "type": "stdio",
    }
    if browser == "chrome":
        manifest["allowed_origins"] = [
            "chrome-extension://%s/" % CHROME_EXTENSION_ID
        ]
    else:
        manifest["allowed_extensions"] = ["cranky-clippy@example.com"]
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


# --- Windows -----------------------------------------------------------------


def _launcher_name(interpreter, host_script):
    """Versioned launcher file name derived from what it actually launches.

    The launcher stays running for as long as a browser keeps the host alive,
    and Windows locks a running executable: rebuilding the *same* file name
    then fails with CS0016, which used to drop the installer back to the .cmd
    wrapper that Firefox silently refuses. Hashing the interpreter and the host
    script into the name means a rebuild never collides with a live instance,
    and an unchanged install simply reuses the launcher already in use.
    """
    digest = hashlib.sha256()
    digest.update(str(interpreter).encode("utf-8", "replace"))
    digest.update(b"\0")
    try:
        digest.update(host_script.read_bytes())
    except OSError:
        digest.update(str(host_script).encode("utf-8", "replace"))
    return "native_messaging_host_launcher_%s.exe" % digest.hexdigest()[:12]


def _windows_host_launcher():
    """The executable Windows manifests should point at.

    Chrome is happy to run a .cmd wrapper (it goes through cmd.exe), but
    Firefox launches the host with CreateProcess and a .cmd is not an
    executable image, so Firefox refuses it silently. A native launcher is
    therefore compiled once and used by both browsers; the .cmd wrapper stays
    as the fallback when no C# compiler is available. Either way it runs this
    Python interpreter on the host script and forwards the arguments the
    browser passes - the extension origin or the add-on id - while inheriting
    the browser's stdin/stdout, which is the native-messaging pipe.
    """
    interpreter = Path(sys.executable)
    quiet = interpreter.with_name("pythonw.exe")
    if quiet.exists():
        interpreter = quiet
    install_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    install_dir = install_dir / "cranky-clippy"
    install_dir.mkdir(parents=True, exist_ok=True)
    host_script = install_dir / "native_messaging_host.py"
    shutil.copy2(SOURCE_HOST, host_script)
    launcher = install_dir / "native_messaging_host.cmd"
    launcher.write_text(
        # %* is essential: the browser identifies itself by passing its
        # extension origin as argv[1], and without it the host cannot tell
        # Chrome and Firefox apart.
        '@echo off\r\n"%s" "%s" %%*\r\n' % (interpreter, host_script),
        encoding="utf-8",
        newline="",
    )
    native = _compile_native_launcher(
        install_dir, interpreter, host_script, _launcher_name(interpreter, host_script)
    )
    if native is not None:
        _prune_stale_launchers(install_dir, native)
    return native or launcher


def _prune_stale_launchers(install_dir, keep):
    """Remove launchers from earlier installs, best effort.

    A stale launcher may still be running, in which case Windows refuses the
    delete; that is fine, because nothing points at it any more.
    """
    for path in install_dir.glob("native_messaging_host_launcher*.exe"):
        if path.name == keep.name:
            continue
        try:
            path.unlink()
        except OSError:
            pass


_LAUNCHER_SOURCE = """using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;

static class CrankyClippyNativeHost
{{
    static int Main(string[] args)
    {{
        string arguments = "{1}";
        foreach (string argument in args)
        {{
            arguments += " \\"" + argument + "\\"";
        }}
        try
        {{
            ProcessStartInfo info = new ProcessStartInfo(@"{0}", arguments);
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardInput = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = false;
            using (Process child = Process.Start(info))
            {{
                // The browser's native-messaging handles cannot simply be
                // inherited (CreateProcess without STARTF_USESTDHANDLES gives
                // the child invalid ones), so the launcher pipes them through:
                // browser -> child on stdin, child -> browser on stdout.
                Stream input = Console.OpenStandardInput();
                Stream output = Console.OpenStandardOutput();
                Task pump = Task.Run(() =>
                {{
                    byte[] buffer = new byte[8192];
                    int read;
                    while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                    {{
                        child.StandardInput.BaseStream.Write(buffer, 0, read);
                        child.StandardInput.BaseStream.Flush();
                    }}
                    try {{ child.StandardInput.Close(); }} catch (Exception) {{ }}
                }});
                child.StandardOutput.BaseStream.CopyTo(output);
                output.Flush();
                pump.Wait(5000);
                child.WaitForExit();
                return child.ExitCode;
            }}
        }}
        catch (Exception)
        {{
            return 1;
        }}
    }}
}}
"""


def _compile_native_launcher(install_dir, interpreter, host_script, output_name):
    """Compile the native host launcher, or return None when it cannot.

    An existing launcher for the same interpreter and host script is reused as
    is: it is the identical binary, and rebuilding it would only fail while a
    browser still has it running.
    """
    output = install_dir / output_name
    if output.is_file():
        return output

    compiler = None
    for framework in ("Framework64", "Framework"):
        candidate = (Path(os.environ.get("WINDIR", r"C:\Windows")) /
                     ("Microsoft.NET/%s/v4.0.30319/csc.exe" % framework))
        if candidate.is_file():
            compiler = candidate
            break
    if compiler is None:
        print("No C# compiler found; manifests will use the .cmd wrapper, which "
              "Chrome accepts but Firefox does not.")
        return None

    source = install_dir / "native_messaging_host_launcher.cs"
    # The host path goes into a regular C# string, so its backslashes have to
    # be doubled; the interpreter path is a verbatim string and needs no help.
    source.write_text(
        _LAUNCHER_SOURCE.format(
            interpreter, str(host_script).replace("\\", "\\\\")
        ),
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [str(compiler), "/nologo", "/target:winexe", "/optimize",
             "/out:%s" % output, str(source)],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print("Could not run %s: %s" % (compiler, exc))
        return None
    if completed.returncode != 0 or not output.is_file():
        print("Could not build the native launcher: %s"
              % (completed.stderr or completed.stdout).strip())
        return None
    return output


def _windows_registry_manifest(manifest_path, registry_key):
    """Point a Chromium browser at a manifest through HKCU.

    On Windows the registry is the documented way to register a native
    messaging host; the per-user directory is written as well because some
    Chromium builds read that too.
    """
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows always has winreg
        return False
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_key)
        try:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
        finally:
            winreg.CloseKey(key)
        return True
    except OSError as exc:
        print("Could not write registry key %s: %s" % (registry_key, exc))
        return False


def _install_windows():
    launcher = _windows_host_launcher()
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    written = []

    chromium_browsers = (
        ("Chrome", local / "Google/Chrome/User Data/NativeMessagingHosts",
         r"Software\Google\Chrome\NativeMessagingHosts"),
        ("Chromium", local / "Chromium/User Data/NativeMessagingHosts",
         r"Software\Chromium\NativeMessagingHosts"),
        ("Edge", local / "Microsoft/Edge/User Data/NativeMessagingHosts",
         r"Software\Microsoft\Edge\NativeMessagingHosts"),
        ("Brave", local / "BraveSoftware/Brave-Browser/User Data/NativeMessagingHosts",
         r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"),
        ("Vivaldi", local / "Vivaldi/User Data/NativeMessagingHosts",
         r"Software\Vivaldi\NativeMessagingHosts"),
        ("Opera", roaming / "Opera Software/Opera Stable/NativeMessagingHosts",
         None),
        ("Opera GX", roaming / "Opera Software/Opera GX Stable/NativeMessagingHosts",
         None),
    )

    for label, directory, registry_key in chromium_browsers:
        manifest = Path(directory) / (HOST_NAME + ".json")
        write_manifest(manifest, launcher, "chrome")
        written.append("%s: %s" % (label, manifest))
        if registry_key:
            _windows_registry_manifest(manifest, registry_key + "\\" + HOST_NAME)

    firefox_manifest = roaming / "Mozilla/NativeMessagingHosts" / (HOST_NAME + ".json")
    write_manifest(firefox_manifest, launcher, "firefox")
    written.append("Firefox: %s" % firefox_manifest)

    print("Native host launcher: %s" % launcher)
    for line in written:
        print("Registered", line)
    return written


def _install_posix():
    SOURCE_HOST.chmod(SOURCE_HOST.stat().st_mode | 0o111)
    home = Path.home()

    chrome_manifest = (
        home / ".config/google-chrome/NativeMessagingHosts"
        / (HOST_NAME + ".json")
    )
    write_manifest(chrome_manifest, SOURCE_HOST, "chrome")

    # Standard Firefox path (for non-Snap installs).
    firefox_manifest = (
        home / ".mozilla/native-messaging-hosts" / (HOST_NAME + ".json")
    )
    write_manifest(firefox_manifest, SOURCE_HOST, "firefox")

    # Firefox Snap cannot execute/read arbitrary files in ~/Documents or
    # ~/.cache. Keep its host and bridge config within Snap's shared area.
    snap_common = home / "snap/firefox/common/cranky-clippy"
    snap_common.mkdir(parents=True, exist_ok=True)
    snap_host = snap_common / "native_messaging_host.py"
    shutil.copy2(SOURCE_HOST, snap_host)
    snap_host.chmod(snap_host.stat().st_mode | 0o111)
    firefox_snap_manifest = (
        home / "snap/firefox/common/.mozilla/native-messaging-hosts"
        / (HOST_NAME + ".json")
    )
    write_manifest(firefox_snap_manifest, snap_host, "firefox")

    print("Registered Chrome native host:", chrome_manifest)
    print("Registered Firefox native host:", firefox_manifest)
    print("Registered Firefox Snap native host:", firefox_snap_manifest)


def main():
    if IS_WINDOWS:
        _install_windows()
    else:
        _install_posix()


if __name__ == "__main__":
    main()