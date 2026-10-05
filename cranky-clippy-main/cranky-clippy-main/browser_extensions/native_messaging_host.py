#!/usr/bin/env python3
"""Native-messaging adapter from Firefox/Chrome to Jev's loopback bridge.

Reads and writes the browser's message channel with os.read/os.write on the
raw file descriptors rather than sys.stdin/sys.stdout. The reader runs on a
daemon thread that is almost always blocked in a read, and CPython's buffered
stdin holds a lock across that blocking call: if the main thread returns while
the reader is inside it, interpreter shutdown deadlocks and then faults with
an access violation (0xC0000005 on Windows). Raw descriptors have no such
lock, so the reader can be abandoned safely the moment the bridge goes away.
"""

import json
import os
import queue
import socket
import struct
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"

_STDIN_FD = 0
_STDOUT_FD = 1
_MAX_MESSAGE_BYTES = 1024 * 1024


def _read_exactly(fd, count):
    """Read exactly count bytes, or return what arrived before EOF."""
    chunks = []
    remaining = count
    while remaining > 0:
        try:
            chunk = os.read(fd, remaining)
        except OSError:
            return None
        if not chunk:
            return None if not chunks else b"".join(chunks)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_native_message():
    header = _read_exactly(_STDIN_FD, 4)
    if header is None or len(header) != 4:
        return None
    length = struct.unpack("<I", header)[0]
    if length > _MAX_MESSAGE_BYTES:
        return None
    payload = _read_exactly(_STDIN_FD, length)
    if payload is None or len(payload) != length:
        return None
    return json.loads(payload.decode("utf-8"))


def _write_native_message(message):
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    _write_all(_STDOUT_FD, struct.pack("<I", len(payload)))
    _write_all(_STDOUT_FD, payload)


def _write_all(fd, data):
    while data:
        try:
            written = os.write(fd, data)
        except OSError:
            return
        if written <= 0:
            return
        data = data[written:]


def _config_candidates(browser):
    home = os.path.expanduser("~")
    paths = [
        os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.join(home, ".cache")),
                     "cranky-clippy", "browser_bridge.json")
    ]
    if IS_WINDOWS:
        # Windows keeps the same per-user app-data folder the bridge writes to.
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.join(
            home, "AppData", "Local"
        )
        paths.insert(0, os.path.join(
            local_app_data, "cranky-clippy", "browser_bridge.json"
        ))
    if browser == "firefox":
        snap_common = os.environ.get(
            "SNAP_USER_COMMON", os.path.join(home, "snap", "firefox", "common")
        )
        paths.insert(0, os.path.join(
            snap_common, ".cache", "cranky-clippy", "browser_bridge.json"
        ))
    return paths


def _read_config(browser):
    for path in _config_candidates(browser):
        try:
            with open(path, encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, ValueError):
            continue
    return None


def _stdin_reader(messages):
    try:
        while True:
            message = _read_native_message()
            if message is None:
                break
            messages.put(("extension", message))
    finally:
        messages.put(("stdin_closed", None))


def _socket_reader(sock, messages):
    try:
        stream = sock.makefile("r", encoding="utf-8")
        for line in stream:
            try:
                messages.put(("jev", json.loads(line)))
            except ValueError:
                continue
    except OSError:
        pass
    finally:
        messages.put(("server_closed", None))


def main():
    # Chrome passes the chrome-extension:// origin. Firefox passes the
    # extension ID. Keep explicit browser arguments as a test-friendly
    # override, but do not rely on a nonstandard host-manifest args field.
    browser = next(
        (argument for argument in reversed(sys.argv[1:])
         if argument in {"chrome", "firefox"}),
        "",
    )
    if not browser:
        browser = next(
            ("chrome" if argument.startswith("chrome-extension://") else "firefox"
             for argument in sys.argv[1:]
             if argument.startswith("chrome-extension://")
             or argument == "cranky-clippy@example.com"
             or argument.startswith("moz-extension://")),
            "",
        )
    if browser not in {"chrome", "firefox"}:
        return 2

    messages = queue.Queue()
    threading.Thread(target=_stdin_reader, args=(messages,), daemon=True).start()
    sock = None
    config = None
    buffered_extension_messages = []
    while sock is None:
        config = _read_config(browser)
        if config:
            try:
                sock = socket.create_connection(
                    (config["host"], int(config["port"])), timeout=2
                )
                break
            except (OSError, KeyError, ValueError):
                sock = None
        try:
            kind, message = messages.get(timeout=0.25)
            if kind == "stdin_closed":
                return 0
            if kind == "extension":
                buffered_extension_messages.append(message)
        except queue.Empty:
            pass

    sock.sendall((json.dumps({
        "type": "hello",
        "browser": browser,
        "token": config["token"],
    }) + "\n").encode("utf-8"))
    for message in buffered_extension_messages:
        sock.sendall((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
    threading.Thread(target=_socket_reader, args=(sock, messages), daemon=True).start()

    try:
        while True:
            kind, message = messages.get()
            if kind in {"stdin_closed", "server_closed"}:
                break
            if kind == "extension":
                sock.sendall((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            elif kind == "jev":
                _write_native_message(message)
                if message.get("action") == "shutdown":
                    break
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
