"""Exercise segmented generation over real loopback HTTP, never a provider."""
import copy
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient
from backend.api import create_app
from test_campaigns import chapter


class LocalChapterModel:
    def __init__(self, *, fail_at=4, delay=0):
        self.calls = []
        self.fail_at = fail_at
        self.failed = False
        self.delay = delay
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                wire = json.loads(raw)
                data = json.loads(wire['messages'][-1]['content'])
                stage = data.get('stage')
                number = data.get('quest_number')
                owner.calls.append({'stage': stage, 'quest_number': number, 'request_bytes': len(raw)})
                if owner.delay:
                    time.sleep(owner.delay if stage == 'quests' else owner.delay * 2)
                if stage == 'quests' and number == owner.fail_at and not owner.failed:
                    owner.failed = True
                    self.respond(503, {'error': 'Synthetic stage failure; retry explicitly.'})
                    return
                result = owner.reply(data)
                self.respond(200, {'choices': [{'message': {'content': json.dumps(result, ensure_ascii=False)}}]})

            def respond(self, status, data):
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def reply(self, data):
        role = data['role']['id']
        result = chapter(role)
        result['quests'][-1]['objective'] = '将负库存限制为零。'
        result['quests'][-1]['expected_output'] = 'remaining=0'
        stage = data['stage']
        if stage == 'analyzing':
            return {'summary': '本批源码涉及库存计算与调用入口。', 'files': [{'path': s['path'], 'summary': '提供库存计算或支持逻辑。'} for s in data['sources']]}
        if stage == 'planning':
            return {'title': result['title'], 'summary': result['summary'], 'quests': [
                {**{k: q[k] for k in ('title', 'type', 'focus', 'source_file', 'line', 'verification_mode', 'run_entry', 'objective')},
                 'depends_on': [i] if i else [], 'expected_changes': '无' if i < 4 else ('改用减法扣减库存。' if i == 4 else '负库存返回零。')}
                for i, q in enumerate(result['quests'])]}
        if stage == 'quests':
            return {'quest': copy.deepcopy(result['quests'][data['quest_number'] - 1])}
        if stage == 'checking':
            return {'consistent': True, 'issues': []}
        raise AssertionError(f'Unexpected generation stage: {stage}')


class SegmentedHttpTests(unittest.TestCase):
    def test_real_http_failure_restart_and_resume_only_missing_quests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            original = 'def remaining(stock, ordered):\n    return stock + ordered\nprint(f"remaining={remaining(10, 2)}")\n'
            (source / 'main.py').write_text(original, encoding='utf-8')
            service = LocalChapterModel()
            try:
                app = create_app(root / 'state', use_keyring=False)
                client = TestClient(app)
                settings = client.put('/api/settings', json={'base_url': service.url, 'model': 'synthetic-segmented'}).json()
                self.assertFalse(settings['key_set'])
                project = client.post('/api/projects/import', json={'path': str(source)}).json()
                base = '/api/projects/' + project['id']
                failure = client.post(base + '/campaign', json={'role': 'mage'})
                self.assertEqual(failure.status_code, 400, failure.text)
                state = client.get(base + '/campaign').json()
                self.assertEqual(state['generation']['quests_completed'], 3)
                self.assertTrue(state['generation']['resumable'])
                self.assertEqual(state['quests'], [])
                self.assertEqual(client.get(base + '/quests').json(), [])
                self.assertNotIn('answer', json.dumps(state))
                before = list(service.calls)
                # Simulate process exit while the same saved draft was active.
                interrupted = app.state.store.get('campaigns', project['id'])
                interrupted['status'] = 'generating'
                interrupted['error'] = ''
                interrupted['generation']['quest_titles'][3]['status'] = 'generating'
                app.state.store.put('campaigns', project['id'], interrupted)
                client.close()

                restarted = TestClient(create_app(root / 'state', use_keyring=False))
                restored = restarted.get(base + '/campaign').json()
                self.assertEqual(restored['status'], 'failed')
                self.assertTrue(restored['generation']['resumable'])
                self.assertEqual(restored['generation']['quests_completed'], 3)
                self.assertTrue(all(q['status'] != 'generating' for q in restored['generation']['quest_titles']))
                self.assertEqual(service.calls, before)
                success = restarted.post(base + '/campaign', json={'role': 'mage'})
                self.assertEqual(success.status_code, 200, success.text)
                self.assertEqual(success.json()['status'], 'ready')
                self.assertEqual([q['status'] for q in success.json()['quests']], ['available'] + ['locked'] * 5)
                resumed = service.calls[len(before):]
                self.assertEqual([(r['stage'], r['quest_number']) for r in resumed], [('quests', 4), ('quests', 5), ('quests', 6), ('checking', None)])
                restarted.post(base + '/campaign', json={'role': 'mage'})
                self.assertEqual(service.calls[len(before):], resumed)
                restarted.close()
                self.assertEqual((source / 'main.py').read_text(encoding='utf-8'), original)
            finally:
                service.close()
