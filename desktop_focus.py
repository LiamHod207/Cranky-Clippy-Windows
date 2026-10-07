"""Non-KWin Linux focus providers. Never use XWayland as global Wayland focus."""
import ast
import json
import os
import re
import subprocess


def _run(args):
    try:
        return subprocess.check_output(args, timeout=1.0, text=True,
                                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def gnome_focus():
    """Read our bundled Shell extension; accept the optional existing provider."""
    output = _run([
        'gdbus', 'call', '--session', '--dest', 'org.gnome.Shell',
        '--object-path', '/org/crankyclippy/Focus',
        '--method', 'org.crankyclippy.Focus.Get',
    ])
    if output is not None:
        try:
            payload = json.loads(ast.literal_eval(output.strip())[0])
            if isinstance(payload, dict):
                if payload.get('no_active') is True:
                    return {'no_active': True}
                if payload.get('app_class'):
                    return {key: payload.get(key) for key in
                            ('app_class', 'caption', 'window_id', 'desktop_file_id')}
        except (ValueError, TypeError, KeyError, IndexError, SyntaxError):
            pass
    output = _run([
        'gdbus', 'call', '--session', '--dest', 'org.gnome.Shell',
        '--object-path', '/org/gnome/shell/extensions/FocusedWindow',
        '--method', 'org.gnome.shell.extensions.FocusedWindow.Get',
    ])
    if output is None:
        return None
    try:
        payload = json.loads(ast.literal_eval(output.strip())[0])
        if not isinstance(payload, dict):
            return None
        if not payload:
            return {'no_active': True}
        if payload.get('focus') is not True:
            return None
        identity = payload.get('wm_class') or payload.get('wm_class_instance')
        if not identity:
            return None
        return {'app_class': str(identity), 'caption': payload.get('title') or '',
                'window_id': payload.get('id'), 'desktop_file_id': None}
    except (ValueError, TypeError, KeyError, IndexError, SyntaxError):
        return None


def _property(output, key):
    if not output:
        return None
    for line in output.splitlines():
        if line.startswith(key + '(') or line.startswith(key + ' '):
            return line.split(' = ', 1)[-1]
    return None


def _strings(value):
    # xprop quotes and escapes strings. literal_eval does not execute content.
    result = []
    for quoted in re.findall(r'"(?:\\.|[^"\\])*"', value or ''):
        try:
            result.append(ast.literal_eval(quoted))
        except (ValueError, SyntaxError):
            pass
    return result


def x11_focus():
    """EWMH active window, available on GNOME/KDE/other X11 desktops."""
    session = os.environ.get('XDG_SESSION_TYPE', '').lower()
    if session == 'wayland' or os.environ.get('WAYLAND_DISPLAY'):
        return None  # XWayland's last X window is not the active native app.
    if not os.environ.get('DISPLAY'):
        return None
    root = _run(['xprop', '-root', '_NET_ACTIVE_WINDOW'])
    value = _property(root, '_NET_ACTIVE_WINDOW')
    match = re.search(r'0x[0-9a-fA-F]+', value or '')
    if not match:
        return None
    window_id = match.group()
    if int(window_id, 16) == 0:
        return {'no_active': True}
    data = _run(['xprop', '-id', window_id, 'WM_CLASS', '_NET_WM_NAME',
                 'WM_NAME', '_GTK_APPLICATION_ID', '_NET_WM_DESKTOP_FILE'])
    names = _strings(_property(data, 'WM_CLASS'))
    desktop_ids = _strings(_property(data, '_NET_WM_DESKTOP_FILE'))
    gtk_ids = _strings(_property(data, '_GTK_APPLICATION_ID'))
    titles = (_strings(_property(data, '_NET_WM_NAME')) or
              _strings(_property(data, 'WM_NAME')))
    identity = (names[-1] if names else None) or (gtk_ids[0] if gtk_ids else None)
    if not identity:
        return None
    return {'app_class': identity, 'caption': titles[0] if titles else '',
            'window_id': window_id,
            'desktop_file_id': desktop_ids[0] if desktop_ids else
                               (gtk_ids[0] if gtk_ids else None)}
