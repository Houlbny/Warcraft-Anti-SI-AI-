import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.runtime import (RuntimeError as ManagedRuntimeError, RuntimeLease,
    load_runtime, launch_background, probe_runtime, restart_managed, wait_ready)


REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE = r'''
import argparse, hmac, sys
from pathlib import Path
sys.path.insert(0, REPOSITORY)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from backend.runtime import serve_managed
parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent; data = root / 'state'
app = FastAPI()
@app.get('/api/health')
def health(): return {'status':'ok'}
@app.get('/api/runtime')
def info(request:Request):
    if not hmac.compare_digest(request.headers.get('X-Codewarden-Runtime-Token', ''), app.state.runtime_token):
        return JSONResponse({'detail':'unauthorized'}, status_code=403)
    return app.state.runtime_info
@app.post('/api/runtime/shutdown')
def shutdown(request:Request):
    if not hmac.compare_digest(request.headers.get('X-Codewarden-Runtime-Token', ''), app.state.runtime_token):
        return JSONResponse({'detail':'unauthorized'}, status_code=403)
    if (data / 'busy').exists(): return JSONResponse({'detail':'busy'}, status_code=409)
    return JSONResponse({'stopping':True,'instance_id':app.state.runtime_info['instance_id']}, status_code=202,
        background=BackgroundTask(app.state.shutdown_callback))
serve_managed(app, project_root=root, data_root=data, port=args.port)
'''


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'state'; self.data.mkdir()
        (self.root / 'start.py').write_text(FIXTURE.replace('REPOSITORY', repr(str(REPOSITORY))), encoding='utf-8')
        self.children = []
        # Track real restart-spawned children so Windows log handles are closed
        # before removing the isolated fixture directory.
        original = launch_background
        def tracked(*args, **kwargs):
            child = original(*args, **kwargs)
            self.children.append(child)
            return child
        self.tracking = patch('backend.runtime.launch_background', side_effect=tracked)
        self.tracking.start()

    def tearDown(self):
        self.tracking.stop()
        (self.data / 'busy').unlink(missing_ok=True)
        record = load_runtime(self.root, self.data)
        if record and probe_runtime(record):
            request = Request(f"http://127.0.0.1:{record['port']}/api/runtime/shutdown", data=b'{}',
                headers={'Content-Type':'application/json','X-Codewarden-Runtime-Token':record['token']}, method='POST')
            with urlopen(request, timeout=2): pass
        for child in self.children:
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                # Only handles created by this test are a permissible cleanup target.
                child.terminate(); child.wait(timeout=5)
        deadline = time.monotonic() + 5
        while (self.data / 'runtime.json').exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.temp.cleanup()

    def free_port(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.assertGreater(port, 10000)
        self.assertNotIn(port, (8765, 8766))
        return port

    def start(self):
        port = self.free_port()
        child = launch_background([sys.executable, str(self.root / 'start.py'), '--port', str(port)], self.root, self.data)
        self.children.append(child)
        return wait_ready(self.root, self.data, port, timeout=12)

    def test_managed_start_registers_identity_and_authenticated_loopback(self):
        record = self.start()
        self.assertEqual(record['project_root'], str(self.root.resolve()))
        self.assertEqual(record['data_root'], str(self.data.resolve()))
        self.assertTrue(probe_runtime(record))
        with self.assertRaises(HTTPError) as denied:
            urlopen(f"http://127.0.0.1:{record['port']}/api/runtime", timeout=2)
        self.assertEqual(denied.exception.code, 403)
        self.assertNotIn(record['token'], (self.data / 'server.log').read_text(encoding='utf-8'))

    def test_restart_keeps_port_and_changes_nonce(self):
        old = self.start()
        current = restart_managed(self.root, self.data, timeout=15)
        self.assertEqual(current['port'], old['port'])
        self.assertNotEqual(current['instance_id'], old['instance_id'])
        self.assertNotEqual(current['pid'], old['pid'])
        self.assertTrue(probe_runtime(current))

    def test_busy_restart_preserves_running_instance(self):
        old = self.start(); (self.data / 'busy').touch()
        with self.assertRaisesRegex(ManagedRuntimeError, '任务|busy|忙'):
            restart_managed(self.root, self.data, timeout=10)
        self.assertEqual(load_runtime(self.root, self.data)['instance_id'], old['instance_id'])
        self.assertTrue(probe_runtime(old))

    def test_without_instance_restart_starts_on_explicit_high_port(self):
        record = restart_managed(self.root, self.data, port=self.free_port(), timeout=15)
        self.assertTrue(probe_runtime(record))

    def test_restart_after_graceful_stop_remembers_previous_port(self):
        previous = self.start()
        request = Request(f"http://127.0.0.1:{previous['port']}/api/runtime/shutdown", data=b'{}',
            headers={'X-Codewarden-Runtime-Token':previous['token']}, method='POST')
        with urlopen(request, timeout=2): pass
        self.children[-1].wait(timeout=8)
        self.assertIsNone(load_runtime(self.root, self.data))
        current = restart_managed(self.root, self.data, timeout=15)
        self.assertEqual(current['port'], previous['port'])

    def test_wrong_project_metadata_is_rejected(self):
        old = self.start()
        record = {**old, 'project_root': str(self.root / 'other')}
        (self.data / 'runtime.json').write_text(json.dumps(record), encoding='utf-8')
        try:
            with self.assertRaises(ManagedRuntimeError):
                restart_managed(self.root, self.data, timeout=3)
            self.assertTrue(probe_runtime(old))
        finally:
            (self.data / 'runtime.json').write_text(json.dumps(old), encoding='utf-8')

    def test_occupied_unknown_port_is_never_stopped(self):
        port = self.free_port()
        with socket.socket() as unknown:
            unknown.bind(('127.0.0.1', port)); unknown.listen()
            with self.assertRaises(ManagedRuntimeError):
                restart_managed(self.root, self.data, port=port, timeout=.3)
            self.assertEqual(unknown.getsockname()[1], port)

    def test_lifetime_lease_prevents_duplicate_start(self):
        with RuntimeLease(self.data):
            with self.assertRaises(ManagedRuntimeError):
                with RuntimeLease(self.data): pass

    def test_parallel_restart_is_rejected_before_touching_service(self):
        old = self.start()
        with RuntimeLease(self.data, lock_name='restart.lock'):
            with self.assertRaisesRegex(ManagedRuntimeError, '重启'):
                restart_managed(self.root, self.data, timeout=3)
        self.assertTrue(probe_runtime(old))

    def test_stop_exits_without_respawn_and_preserves_saved_data(self):
        from backend.runtime import stop_managed
        old = self.start()
        saved = self.data / 'saved-progress.txt'
        saved.write_text('keep-this-progress', encoding='utf-8')
        history = (self.data / 'runtime.last.json').read_bytes()
        self.assertTrue(stop_managed(self.root, self.data, timeout=10))
        self.assertEqual(self.children[-1].wait(timeout=5), 0)
        self.assertFalse(probe_runtime(old))
        self.assertIsNone(load_runtime(self.root, self.data))
        self.assertEqual(saved.read_text(encoding='utf-8'), 'keep-this-progress')
        self.assertEqual((self.data / 'runtime.last.json').read_bytes(), history)
        self.assertFalse(stop_managed(self.root, self.data, timeout=1))

    def test_stop_without_instance_does_not_start_one(self):
        from backend.runtime import stop_managed
        self.assertFalse(stop_managed(self.root, self.data, timeout=1))
        self.assertIsNone(load_runtime(self.root, self.data))
        self.assertFalse((self.data / 'server.log').exists())

    def test_busy_stop_preserves_running_instance(self):
        from backend.runtime import stop_managed
        old = self.start(); (self.data / 'busy').touch()
        with self.assertRaisesRegex(ManagedRuntimeError, '任务|忙'):
            stop_managed(self.root, self.data, timeout=2)
        self.assertTrue(probe_runtime(old))
        self.assertEqual(load_runtime(self.root, self.data)['instance_id'], old['instance_id'])

    def test_stop_refuses_mismatched_instance_identity(self):
        from backend.runtime import stop_managed
        old = self.start()
        (self.data / 'runtime.json').write_text(json.dumps({**old, 'instance_id':'wrong-instance-' * 3}), encoding='utf-8')
        try:
            with self.assertRaises(ManagedRuntimeError):
                stop_managed(self.root, self.data, timeout=2)
            self.assertTrue(probe_runtime(old))
        finally:
            (self.data / 'runtime.json').write_text(json.dumps(old), encoding='utf-8')

    def test_stop_serializes_with_restart_before_touching_service(self):
        from backend.runtime import stop_managed
        old = self.start()
        with RuntimeLease(self.data, lock_name='restart.lock'):
            with self.assertRaises(ManagedRuntimeError):
                stop_managed(self.root, self.data, timeout=2)
        self.assertTrue(probe_runtime(old))

    def test_stop_cli_needs_no_frontend_build_and_is_repeatable(self):
        old = self.start()
        script = self.root / 'stop.py'
        script.write_text('import sys\nsys.path.insert(0, ' + repr(str(REPOSITORY)) + ')\n'
                          + (REPOSITORY / 'stop.py').read_text(encoding='utf-8'), encoding='utf-8')
        environment = {**os.environ, 'CODEWARDEN_DATA': str(self.data), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        for _ in range(2):
            result = subprocess.run([sys.executable, str(script), '--timeout', '10'],
                cwd=self.root, env=environment, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
            self.assertNotIn(old['token'], result.stdout.decode('utf-8', 'replace'))
        self.assertFalse(probe_runtime(old))
        self.assertEqual(self.children[-1].wait(timeout=5), 0)


if __name__ == '__main__':
    unittest.main()
