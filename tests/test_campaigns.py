import copy
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from backend.api import create_app


def chapter(role='mage'):
    quests = []
    focuses = ['language'] * 4 + ['feature', 'regression'] if role == 'mage' else ['structure', 'flow', 'structure', 'flow', 'feature', 'feature']
    for i in range(6):
        code = i >= 4
        explain = role != 'mage' and i < 4
        quests.append({
            'type': 'feature' if code else 'explore', 'focus': focuses[i],
            'title': f'学习步骤 {i + 1}', 'brief': '检查库存处理。', 'objective': '扣减库存并核对输出。' if code else '找到库存函数。',
            'source_file': 'main.py', 'line': 1, 'run_entry': 'main.py',
            'steps': [{'title': '查看源码', 'instruction': '打开 main.py，找到 remaining。'}, {'title': '提交验证', 'instruction': '按目标完成本关。'}],
            'concepts': ['函数'], 'success_message': '本关已完成。',
            'verification_mode': 'run' if code else ('explain' if explain else 'answer'),
            'verification': '退出码为 0，输出为 remaining=8。' if code else '根据源码说明。',
            'answer': 'remaining' if not code and not explain else None,
            'expected_output': 'remaining=8' if code else None,
            'rubric': ['指出函数输入', '说明返回值的含义'] if explain else [],
            'hints': ['从调用处向上追踪。', '看参数和返回值。', '函数名是 remaining。'][:2 if role == 'druid' else 3],
        })
    return {'title': '库存项目入门', 'summary': '按步骤检查库存函数。', 'quests': quests}


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        source = root / 'source'
        source.mkdir()
        (source / 'main.py').write_text('def remaining(stock, ordered):\n    return stock + ordered\nprint(f"remaining={remaining(10, 2)}")\n', encoding='utf-8')
        (source / 'config.json').write_text('{"version":1}', encoding='utf-8')
        self.data = root / 'state'
        self.app = create_app(self.data, use_keyring=False)
        self.client = TestClient(self.app)
        self.project = self.client.post('/api/projects/import', json={'path': str(source)}).json()
        self.pid = self.project['id']
        self.base = '/api/projects/' + self.pid
        self.requests = []
        self.response = chapter()
        def model(system, user, **kwargs):
            from test_segmented_campaigns import staged_response
            payload = json.loads(user)
            self.requests.append((system, payload))
            return staged_response(payload, self.response)
        self.app.state.model.ask_json = model

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def generate(self, role='mage'):
        response = self.client.post(self.base + '/campaign', json={'role': role})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_import_requires_role_and_complete_ai_chapter_then_reuses_it(self):
        self.assertEqual(self.client.get(self.base + '/campaign').json()['status'], 'unprepared')
        result = self.generate()
        self.assertEqual(result['role'], 'mage')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual([q['status'] for q in result['quests']], ['available'] + ['locked'] * 5)
        self.assertTrue(all(q['steps'] and q['verification_mode'] for q in result['quests']))
        self.assertNotIn('answer', result['quests'][0])
        self.assertNotIn('rubric', result['quests'][0])
        self.generate()
        self.assertEqual(len(self.requests), 10)
        restored = TestClient(create_app(self.data, use_keyring=False))
        self.assertEqual(restored.get(self.base + '/campaign').json()['current_quest_id'], result['quests'][0]['id'])
        restored.close()

    def test_each_role_supplies_distinct_learning_contract_to_model(self):
        for role, focus in [('mage', 'language'), ('paladin', 'structure'), ('druid', 'feature')]:
            self.response = chapter(role)
            result = self.generate(role)
            request = self.requests[-1][1]
            self.assertEqual(request['role']['id'], role)
            self.assertIn(focus, request['role']['focus'])
            self.assertEqual(result['role'], role)
            if role != 'druid':
                self.project = self.client.post('/api/projects/import', json={'path': self.project['source_path']}).json()
                self.pid = self.project['id']
                self.base = '/api/projects/' + self.pid

    def test_server_rejects_locked_task_and_unlocks_only_after_success(self):
        tasks = self.generate()['quests']
        for action, body in [('answer', {'answer': 'remaining'}), ('hint', {}), ('review', {'answer': 'yes'})]:
            self.assertEqual(self.client.post(self.base + f"/quests/{tasks[1]['id']}/{action}", json=body).status_code, 400)
        self.assertEqual(self.client.post(self.base + '/run', json={'quest_id': tasks[4]['id']}).status_code, 400)
        bad = self.client.post(self.base + f"/quests/{tasks[0]['id']}/answer", json={'answer': 'wrong'})
        self.assertFalse(bad.json()['correct'])
        self.assertEqual(self.client.get(self.base + '/campaign').json()['completed'], 0)
        good = self.client.post(self.base + f"/quests/{tasks[0]['id']}/answer", json={'answer': 'remaining'})
        self.assertTrue(good.json()['correct'])
        campaign = self.client.get(self.base + '/campaign').json()
        self.assertEqual(campaign['current_quest_id'], tasks[1]['id'])
        self.assertEqual([q['status'] for q in campaign['quests']][:3], ['completed', 'available', 'locked'])

    def test_invalid_generation_preserves_old_work_and_can_retry(self):
        previous = [{'id': 'old', 'type': 'explore', 'status': 'completed'}]
        self.app.state.store.put('quests', self.pid, previous)
        self.response['quests'][-1]['source_file'] = '../outside.py'
        failed = self.client.post(self.base + '/campaign', json={'role': 'mage'})
        self.assertEqual(failed.status_code, 400)
        state = self.client.get(self.base + '/campaign').json()
        self.assertEqual((state['status'], state['role']), ('failed', 'mage'))
        self.assertEqual(self.app.state.store.get('quests', self.pid), previous)
        self.response = chapter()
        self.assertEqual(self.generate()['total'], 6)

    def test_unverifiable_or_partial_chapter_never_publishes(self):
        for defect in ('short', 'empty_answer', 'no_output', 'bad_entry', 'no_steps'):
            self.app.state.store.put('campaign_drafts', self.pid, {})
            self.response = chapter()
            if defect == 'short': self.response['quests'] = self.response['quests'][:2]
            if defect == 'empty_answer': self.response['quests'][0]['answer'] = ''
            if defect == 'no_output': self.response['quests'][-1]['expected_output'] = None
            if defect == 'bad_entry': self.response['quests'][-1]['run_entry'] = 'missing.py'
            if defect == 'no_steps': self.response['quests'][0]['steps'] = []
            result = self.client.post(self.base + '/campaign', json={'role': 'mage'})
            self.assertEqual(result.status_code, 400, defect)
            self.assertEqual(self.app.state.store.get('quests', self.pid, []), [])

    def test_explanation_uses_strict_rubric_not_fixed_answer(self):
        self.response = chapter('paladin')
        task = self.generate('paladin')['quests'][0]
        target = self.base + f"/quests/{task['id']}/review"
        self.response = {'feedback': '通过', 'checks': [{'criterion': '指出函数输入', 'passed': 'true', 'evidence': '输入库存和订单'}, {'criterion': '说明返回值的含义', 'passed': True, 'evidence': '返回剩余库存'}]}
        self.assertEqual(self.client.post(target, json={'answer': '库存和订单作为参数，返回库存运算结果。'}).status_code, 400)
        self.response['checks'][0]['passed'] = True
        result = self.client.post(target, json={'answer': '库存和订单作为参数，返回库存运算结果。'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['correct'])
        self.assertEqual(result.json()['evaluation'], 'ai_review')

    def test_run_checks_current_task_change_not_previous_task_change(self):
        tasks = self.generate()['quests']
        for task in tasks[:4]:
            self.client.post(self.base + f"/quests/{task['id']}/answer", json={'answer': 'remaining'})
        file = self.client.get(self.base + '/file?path=main.py').json()
        self.client.put(self.base + '/file', json={'path': 'main.py', 'content': file['content'].replace('stock + ordered', 'stock - ordered')})
        passed = self.client.post(self.base + '/run', json={'quest_id': tasks[4]['id']})
        self.assertEqual(passed.status_code, 200, passed.text)
        self.assertTrue(passed.json()['quest_completed'])
        reused = self.client.post(self.base + '/run', json={'quest_id': tasks[5]['id']})
        self.assertFalse(reused.json()['quest_completed'])

    def test_missing_model_fails_with_recoverable_role(self):
        from backend.model_client import ModelClient
        self.app.state.model.ask_json = ModelClient(self.app.state.store, use_keyring=False).ask_json
        result = self.client.post(self.base + '/campaign', json={'role': 'druid'})
        self.assertEqual(result.status_code, 400)
        campaign = self.client.get(self.base + '/campaign').json()
        self.assertEqual((campaign['status'], campaign['role']), ('failed', 'druid'))
        self.assertIn('模型', result.json()['detail'])

    def test_code_review_requires_current_run_and_checks_all_workspace_files(self):
        self.response['quests'][4]['verification_mode'] = 'review'
        self.response['quests'][4]['rubric'] = ['执行库存扣减', '保留参数通用性']
        tasks = self.generate()['quests']
        for task in tasks[:4]:
            self.client.post(self.base + f"/quests/{task['id']}/answer", json={'answer': 'remaining'})
        target = self.base + f"/quests/{tasks[4]['id']}/review"
        self.response = {'feedback': '扣减逻辑与运行结果一致。', 'checks': [{'criterion': '执行库存扣减', 'passed': True, 'evidence': '使用 stock - ordered'}, {'criterion': '保留参数通用性', 'passed': True, 'evidence': '两个参数仍参与运算'}]}
        file = self.client.get(self.base + '/file?path=main.py').json()
        self.client.put(self.base + '/file', json={'path': 'main.py', 'content': file['content'].replace('stock + ordered', 'stock - ordered')})
        self.assertEqual(self.client.post(target, json={}).status_code, 400)
        run = self.client.post(self.base + '/run', json={'quest_id': tasks[4]['id']}).json()
        self.assertEqual(run['status'], 'passed')
        self.assertFalse(run['quest_completed'])
        self.client.put(self.base + '/file', json={'path': 'config.json', 'content': '{"version":2}'})
        self.assertEqual(self.client.post(target, json={}).status_code, 400)
        self.client.post(self.base + '/run', json={'quest_id': tasks[4]['id']})
        self.assertTrue(self.client.post(target, json={}).json()['correct'])

    def test_interrupted_generation_restores_retry_and_legacy_generate_cannot_bypass(self):
        self.app.state.store.put('campaigns', self.pid, {'status': 'generating', 'role': 'mage'})
        restored = TestClient(create_app(self.data, use_keyring=False))
        state = restored.get(self.base + '/campaign').json()
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['role'], 'mage')
        self.assertEqual(restored.post(self.base + '/generate').status_code, 400)
        restored.close()
        chapter = self.generate()
        legacy = self.client.post(self.base + '/generate').json()
        self.assertEqual([q['id'] for q in legacy], [q['id'] for q in chapter['quests']])
        self.assertEqual(len(self.requests), 10)

    def test_concurrent_generation_is_rejected_while_status_remains_visible(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        started, finish = Event(), Event()
        def delayed(system, user, **kwargs):
            from test_segmented_campaigns import staged_response
            started.set()
            if not finish.wait(10):
                raise RuntimeError('test request did not finish')
            return staged_response(json.loads(user), chapter())
        self.app.state.model.ask_json = delayed
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(self.client.post, self.base + '/campaign', json={'role': 'mage'})
            try:
                self.assertTrue(started.wait(5))
                state = self.client.get(self.base + '/campaign').json()
                self.assertEqual(state['status'], 'generating')
                self.assertEqual(state['phase'], 'requesting')
                self.assertTrue(state['started_at'])
                self.assertGreater(state['input_bytes'], 0)
                self.assertGreater(state['source_count'], 0)
                self.assertEqual(state['wait_timeout_seconds'], 300)
                self.assertEqual(self.client.post(self.base + '/campaign', json={'role': 'mage'}).status_code, 409)
            finally:
                finish.set()
            self.assertEqual(first.result().status_code, 200)

    def test_chapter_timeout_keeps_diagnostics_and_existing_progress(self):
        from backend.model_client import ModelClient
        import httpx
        calls = []
        def timeout(request):
            calls.append(request)
            self.assertEqual(request.extensions['timeout']['read'], 420)
            raise httpx.ReadTimeout('synthetic timeout', request=request)
        real = ModelClient(self.app.state.store, use_keyring=False, transport=httpx.MockTransport(timeout))
        real.configure('http://127.0.0.1:9999/v1', 'test', generation_timeout=420)
        self.app.state.model.ask_json = real.ask_json
        previous = [{'id': 'old', 'status': 'completed'}]
        self.app.state.store.put('quests', self.pid, previous)
        response = self.client.post(self.base + '/campaign', json={'role': 'mage'})
        self.assertEqual(response.status_code, 400)
        state = self.client.get(self.base + '/campaign').json()
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['phase'], 'requesting')
        self.assertEqual(state['wait_timeout_seconds'], 420)
        self.assertTrue(state['started_at'])
        self.assertIn('420', state['error'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.app.state.store.get('quests', self.pid), previous)

    def test_large_multibyte_context_is_bounded_and_symbols_stay_inside_excerpts(self):
        from backend.campaigns import Campaigns
        source = Path(self.project['source_path'])
        for i in range(30):
            (source / f'module{i:02d}.py').write_text(''.join(f'def fn{n}(): return "中文内容测试"\n' for n in range(500)), encoding='utf-8')
        project = self.client.post('/api/projects/import', json={'path': str(source)}).json()
        pid = project['id']
        response = self.client.post('/api/projects/' + pid + '/campaign', json={'role': 'mage'})
        self.assertEqual(response.status_code, 200, response.text)
        payloads = [payload for _, payload in self.requests if payload.get('stage') == 'analyzing']
        self.assertGreater(len(payloads), 1)
        for payload in payloads:
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')), 24000)
            for item in payload['sources']:
                self.assertNotIn('hash', item)
                self.assertLessEqual(len(item['symbols']), 24)
                self.assertTrue(all(1 <= s['line'] <= item['line_count'] for s in item['symbols']))
