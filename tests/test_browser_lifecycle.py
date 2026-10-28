import threading
import unittest
import contextlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from browser_lifecycle import BrowserSessions
import remote


class BrowserSessionsTests(unittest.TestCase):
    def setUp(self):
        self.stopped = threading.Event()
        self.sessions = BrowserSessions(self.stopped.set, grace_seconds=.08)
        self.addCleanup(self.sessions.dispose)

    def test_last_of_two_tabs_triggers_shutdown(self):
        self.sessions.open('first-session-00000001')
        self.sessions.open('second-session-0000002')
        self.sessions.close('first-session-00000001')
        self.assertFalse(self.stopped.wait(.15))
        self.sessions.close('second-session-0000002')
        self.assertTrue(self.stopped.wait(1))
    def test_reload_cancels_pending_shutdown(self):
        self.sessions.open('first-session-00000001')
        self.sessions.close('first-session-00000001')
        self.sessions.open('replacement-session-01')
        self.assertFalse(self.stopped.wait(.15))
        self.sessions.close('replacement-session-01')
        self.assertTrue(self.stopped.wait(1))

    def test_close_before_delayed_open_does_not_revive_session(self):
        self.sessions.close('first-session-00000001')
        self.assertFalse(self.sessions.open('first-session-00000001'))
        self.assertTrue(self.stopped.wait(1))

    def test_invalid_id_and_remote_access_are_rejected(self):
        with self.assertRaises(ValueError):
            self.sessions.open('short')
        self.assertFalse(remote.allowed('POST', '/api/ui/open'))
        self.assertFalse(remote.allowed('POST', '/api/ui/close'))
        self.assertFalse(remote.allowed('GET', '/api/ui/watch'))

    def test_disconnected_stream_reconnects_and_old_disconnect_is_ignored(self):
        key = 'first-session-00000001'
        self.sessions.open(key)
        first = self.sessions.watch_open(key)
        self.sessions.watch_closed(key, first)
        second = self.sessions.watch_open(key)
        self.sessions.watch_closed(key, first)
        self.assertFalse(self.stopped.wait(.15))
        self.sessions.watch_closed(key, second)
        self.assertTrue(self.stopped.wait(1))


class ManagedServerShutdownTests(unittest.TestCase):
    def test_app_shutdown_sends_stop_to_a_managed_process(self):
        import app
        child = subprocess.Popen(
            [sys.executable, '-u', '-c',
             'import sys; sys.exit(0 if sys.stdin.readline().strip() == "stop" else 9)'],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        self.addCleanup(child.stdin.close)
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch.object(app, 'DATA_DIR', Path(temp)))
            stack.enter_context(patch.object(app, 'SERVER', None))
            stack.enter_context(patch.object(app, 'REMOTE', None))
            stack.enter_context(patch.object(app, 'LocalHTTPServer', return_value=MagicMock()))
            stack.enter_context(patch.object(app.remote, 'RemoteServer', return_value=MagicMock()))
            stack.enter_context(patch.object(app.updater, 'start_background_checks'))
            stack.enter_context(patch.object(app, 'UI_SESSIONS', MagicMock()))
            stack.enter_context(patch.object(app, 'PROCESSES', {'fake-server': child}))
            stack.enter_context(patch.object(app, 'READ_THREADS', {}))
            stack.enter_context(patch.object(app, 'STOP_REQUESTED', set()))
            stack.enter_context(patch.object(app, 'SHARED', MagicMock()))
            stack.enter_context(patch.object(app, 'TUNNEL', None))
            stack.enter_context(patch.object(app, 'stop_gate'))
            stack.enter_context(patch.object(sys, 'argv', ['app.py', '--no-browser']))
            app.main()
        self.assertEqual(child.returncode, 0)


if __name__ == '__main__':
    unittest.main()
