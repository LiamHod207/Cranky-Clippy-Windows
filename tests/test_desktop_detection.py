import json
import os
import unittest
from unittest.mock import patch
import desktop_focus as focus
import get_desktop_state as state


class LinuxFocusTests(unittest.TestCase):
    def test_gnome_native_identity(self):
        payload = {'focus': True, 'wm_class': 'org.mozilla.firefox',
                   'title': "It's coding - Mozilla Firefox", 'id': 42}
        with patch.object(focus, '_run', return_value=repr((json.dumps(payload),))):
            self.assertEqual(focus.gnome_focus()['window_id'], 42)
            self.assertEqual(focus.gnome_focus()['app_class'], 'org.mozilla.firefox')

    def test_gnome_no_focus(self):
        with patch.object(focus, '_run', return_value="('{}',)"):
            self.assertEqual(focus.gnome_focus(), {'no_active': True})

    def test_gnome_unavailable_and_bad_reply(self):
        for output in (None, 'bad', "('[]',)", "('{\"focus\": false}',)"):
            with patch.object(focus, '_run', return_value=output):
                self.assertIsNone(focus.gnome_focus())

    def test_x11_identity(self):
        root = '_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1234\n'
        props = ('WM_CLASS(STRING) = "firefox", "Firefox"\n'
                 '_NET_WM_NAME(UTF8_STRING) = "Coding - Mozilla Firefox"\n'
                 '_GTK_APPLICATION_ID(UTF8_STRING) = "org.mozilla.firefox"\n')
        with patch.dict(os.environ, {'DISPLAY': ':99', 'XDG_SESSION_TYPE': 'x11',
                                     'WAYLAND_DISPLAY': ''}), \
             patch.object(focus, '_run', side_effect=[root, props]):
            result = focus.x11_focus()
        self.assertEqual(result['app_class'], 'Firefox')
        self.assertEqual(result['desktop_file_id'], 'org.mozilla.firefox')

    def test_xwayland_is_not_global_focus(self):
        with patch.dict(os.environ, {'DISPLAY': ':99', 'XDG_SESSION_TYPE': 'wayland'}), \
             patch.object(focus, '_run') as run:
            self.assertIsNone(focus.x11_focus())
            run.assert_not_called()

    def test_x11_no_focus(self):
        with patch.dict(os.environ, {'DISPLAY': ':99', 'XDG_SESSION_TYPE': 'x11', 'WAYLAND_DISPLAY': ''}), \
             patch.object(focus, '_run', return_value='_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0'):
            self.assertEqual(focus.x11_focus(), {'no_active': True})


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(state, '_time_since_last_active', return_value=(0, 'test')),
                        patch.object(state, '_find_app_frame', return_value=None),
                        patch.object(state, '_installed_app_description', return_value=(None, None))]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(state.set_active_tab_query, None)
        self.addCleanup(state.set_ignored_window_titles, [])

    def _active(self, backend, desktop='gnome'):
        active = {'app_class': 'org.gnome.TextEditor', 'caption': 'test.py - Text Editor', 'window_id': 9}
        with patch.object(state, 'IS_WINDOWS', backend == 'win32'), \
             patch.object(state, '_desktop_kind', return_value=desktop), \
             patch.object(state, '_win_active_window', return_value=active), \
             patch.object(state, '_active_window_kwin', return_value=active if backend == 'kwin' else None), \
             patch.object(state, 'gnome_focus', return_value=active if backend == 'gnome_dbus' else None), \
             patch.object(state, 'x11_focus', return_value=active):
            return state.get_desktop_state('coding')

    def test_backend_routing(self):
        for backend, desktop in [('gnome_dbus', 'gnome'), ('x11', 'xfce'), ('kwin', 'kde'), ('win32', '')]:
            with self.subTest(backend=backend):
                self.assertEqual(self._active(backend, desktop)['universal']['detection_backend'], backend)

    def test_authoritative_no_focus_never_falls_back(self):
        with patch.object(state, 'IS_WINDOWS', False), patch.object(state, '_desktop_kind', return_value='gnome'), \
             patch.object(state, '_active_window_kwin', return_value=None), \
             patch.object(state, 'gnome_focus', return_value={'no_active': True}), \
             patch.object(state, '_active_window_atspi') as atspi:
            self.assertTrue(state.get_desktop_state()['universal']['focus_lost'])
            atspi.assert_not_called()

    def test_unknown_has_setup_diagnostic(self):
        with patch.object(state, 'IS_WINDOWS', False), patch.object(state, '_desktop_kind', return_value='gnome'), \
             patch.object(state, '_active_window_kwin', return_value=None), \
             patch.object(state, 'gnome_focus', return_value=None), patch.object(state, 'x11_focus', return_value=None), \
             patch.object(state, '_active_window_atspi', return_value=(None, None)):
            u = state.get_desktop_state()['universal']
            self.assertEqual(u['detection_status'], 'unavailable')
            self.assertIn('GNOME', u['detection_hint'])

    def test_linux_live_extension_is_preferred(self):
        state.set_active_tab_query(lambda key: {'url': 'https://github.com/neaflow', 'title': 'Coding'})
        with patch.object(state, 'IS_WINDOWS', False), patch.object(state, '_firefox_focus') as disk:
            metadata = state._collect_browser_metadata('firefox', 'Coding - Mozilla Firefox', None)
        self.assertEqual(metadata['site_domain'], 'github.com')
        disk.assert_not_called()

    def test_live_tab_requires_exact_title(self):
        state.set_active_tab_query(lambda key: {'url': 'https://x.com', 'title': 'X'})
        self.assertIsNone(state._live_browser_tab('firefox', 'Coding on X - Mozilla Firefox'))
        self.assertIsNone(state._live_browser_tab('firefox', None))

    def test_sessionstore_unmatched_tab_is_not_active(self):
        with patch.object(state, '_firefox_session_file', return_value={'windows': [{'tabs': [
                {'entries': [{'url': 'https://x.com', 'title': 'Old tab'}]}]}]}):
            self.assertIsNone(state._firefox_focus('New tab - Mozilla Firefox'))

    def test_history_latest_visit_is_not_active(self):
        with patch.object(state, '_chromium_history_rows', return_value=[('https://x.com', 'Old tab', 42)]):
            self.assertIsNone(state._chromium_history_url('chrome', 'New tab - Google Chrome'))

    def test_url_beats_misleading_site_title(self):
        md = {'site_url': 'https://docs.google.com', 'site_domain': 'docs.google.com', 'tab_title': 'Test - YouTube'}
        with patch.object(state, 'IS_WINDOWS', True), patch.object(state, '_win_active_window', return_value={
                'app_class': 'firefox', 'caption': 'Test - YouTube - Mozilla Firefox', 'window_id': 7}), \
             patch.object(state, '_collect_browser_metadata', return_value=md):
            result = state.get_desktop_state()
        self.assertEqual(result.get('webapp', {}).get('site_domain'), 'docs.google.com')
        self.assertEqual(result.get('webapp', {}).get('matched_via'), 'url')



class BridgeTests(unittest.TestCase):
    def test_correlated_reply_ignores_old_poll(self):
        import collections
        import threading
        from browser_extensions.bridge_server import BrowserExtensionBridge
        bridge = BrowserExtensionBridge.__new__(BrowserExtensionBridge)
        bridge.lock = threading.RLock()
        bridge.connections = {'chrome': object()}
        bridge.tab_replies = collections.defaultdict(collections.deque)
        def send(browser, message):
            bridge.tab_replies[browser].append({'request_id': 'old', 'url': 'https://wrong.test'})
            bridge.tab_replies[browser].append({'request_id': message['request_id'], 'url': 'https://right.test'})
            return True
        bridge.send = send
        self.assertEqual(bridge.request_active_tab('chrome')['url'], 'https://right.test')

    def test_disconnected_returns_without_wait(self):
        import threading
        from browser_extensions.bridge_server import BrowserExtensionBridge
        bridge = BrowserExtensionBridge.__new__(BrowserExtensionBridge)
        bridge.lock = threading.RLock()
        bridge.connections = {}
        self.assertIsNone(bridge.request_active_tab('firefox'))


if __name__ == '__main__':
    unittest.main()

class BundledGnomeTests(unittest.TestCase):
    def test_bundled_focus_has_desktop_metadata(self):
        data = {'app_class': 'firefox', 'caption': 'Code - Firefox',
                'window_id': '99', 'desktop_file_id': 'org.mozilla.firefox.desktop'}
        with patch.object(focus, '_run', return_value=repr((json.dumps(data),))) as run:
            self.assertEqual(focus.gnome_focus(), data)
            self.assertEqual(run.call_count, 1)

    def test_bundled_no_focus(self):
        with patch.object(focus, '_run', return_value=repr((json.dumps({'no_active': True}),))):
            self.assertEqual(focus.gnome_focus(), {'no_active': True})

class InstallerTests(unittest.TestCase):
    def test_non_gnome_is_noop_even_if_shell_installed(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('clippy_gnome_install', 'gnome_extension/install.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        for desktop in ('KDE', 'XFCE', ''):
            with patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': desktop, 'XDG_SESSION_DESKTOP': desktop}), \
                 patch.object(installer.subprocess, 'check_output') as run:
                installer.main()
                run.assert_not_called()
