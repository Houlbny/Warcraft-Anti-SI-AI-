import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient
from backend.api import create_app
from test_campaigns import chapter


def staged_response(payload, result):
    stage = payload.get('stage')
    if stage == 'analyzing':
        return {'summary': '检查库存源码。', 'files': [{'path': s['path'], 'summary': '库存计算与调用。'} for s in payload['sources']]}
    if stage == 'planning':
        return {'title': result['title'], 'summary': result['summary'], 'quests': [
            {**{key: q[key] for key in ('title', 'type', 'focus', 'source_file', 'line', 'verification_mode', 'objective')},
             'run_entry': q.get('run_entry'), 'depends_on': [i] if i else [],
             'expected_changes': '执行库存扣减' if q['type'] in {'repair', 'feature'} else '无'}
            for i, q in enumerate(result['quests'])]}
    if stage == 'quests':
        return {'quest': copy.deepcopy(result['quests'][payload['quest_number'] - 1])}
    if stage == 'checking':
        return {'consistent': True, 'issues': []}
    return copy.deepcopy(result)


class SegmentedCampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'; source.mkdir()
        (source / 'main.py').write_text('def remaining(stock, ordered):\n    return stock + ordered\nprint(remaining(10, 2))\n', encoding='utf-8')
        self.data = self.root / 'state'
        self.app = create_app(self.data, use_keyring=False)
        self.client = TestClient(self.app); self.addCleanup(self.client.close)
        self.project = self.client.post('/api/projects/import', json={'path': str(source)}).json()
        self.pid = self.project['id']; self.base = '/api/projects/' + self.pid
        self.calls = []
        self.fail_at = 4
        self.response = chapter()
        def model(system, user, **kwargs):
            payload = json.loads(user); self.calls.append(payload)
            if payload.get('stage') == 'quests' and payload['quest_number'] == self.fail_at:
                raise ValueError('synthetic timeout')
            return staged_response(payload, self.response)
        self.model = model
        self.app.state.model.ask_json = model

    def request(self):
        return self.client.post(self.base + '/campaign', json={'role': 'mage'})

    def test_failure_after_three_quests_resumes_only_missing_segments(self):
        self.assertEqual(self.request().status_code, 400)
        state = self.client.get(self.base + '/campaign').json()
        self.assertEqual(state['generation']['quests_completed'], 3)
        self.assertTrue(state['generation']['resumable'])
        self.assertEqual(state['quests'], [])
        public = json.dumps(state)
        for private in ['"answer"', '"hints"', '"rubric"', '"sources"']:
            self.assertNotIn(private, public)
        self.calls.clear(); self.fail_at = None
        ready = self.request()
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertEqual([(p['stage'], p.get('quest_number')) for p in self.calls], [('quests', 4), ('quests', 5), ('quests', 6), ('checking', None)])
        self.assertEqual(ready.json()['total'], 6)

    def test_restart_preserves_saved_segments_without_automatic_calls(self):
        self.assertEqual(self.request().status_code, 400)
        restored = create_app(self.data, use_keyring=False)
        client = TestClient(restored); self.addCleanup(client.close)
        self.calls.clear(); self.fail_at = None
        restored.state.model.ask_json = self.model
        state = client.get(self.base + '/campaign').json()
        self.assertEqual(state['generation']['quests_completed'], 3)
        self.assertEqual(self.calls, [])
        response = client.post(self.base + '/campaign', json={'role': 'mage'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.calls[0]['quest_number'], 4)

    def test_changed_source_discards_outline_and_saved_details(self):
        self.request(); self.calls.clear(); self.fail_at = None
        file = self.client.get(self.base + '/file?path=main.py').json()
        self.client.put(self.base + '/file', json={'path': 'main.py', 'content': file['content'] + '\n# changed\n'})
        result = self.request()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn('planning', [p['stage'] for p in self.calls])
        self.assertEqual([p['quest_number'] for p in self.calls if p['stage'] == 'quests'], [1, 2, 3, 4, 5, 6])

    def test_requests_are_bounded_and_details_include_accepted_dependencies(self):
        self.fail_at = None
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual([p['stage'] for p in self.calls], ['analyzing', 'planning'] + ['quests'] * 6 + ['checking'])
        for payload in self.calls:
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()), 24000)
        fourth = next(p for p in self.calls if p.get('quest_number') == 4)
        self.assertEqual(fourth['prerequisites'][0]['objective'], self.response['quests'][2]['objective'])
        self.assertIn('verification', fourth['prerequisites'][0])
        self.assertIn('planned_changes', fourth['prerequisites'][0])
        checking = self.calls[-1]
        self.assertTrue(all('answer' not in item and 'hints' not in item and 'rubric' not in item for item in checking['quests']))
        self.assertEqual(checking['quests'][4]['expected_output'], 'remaining=8')

    def test_semantic_check_failure_revises_affected_chain_with_feedback(self):
        self.fail_at = None
        failed_check = True
        model = self.model
        def checking(system, user, **kwargs):
            payload = json.loads(user)
            response = model(system, user, **kwargs)
            if payload['stage'] == 'checking' and failed_check:
                return {'consistent': False, 'issues': [{'quest_number': 4, 'message': '后续目标必须保留前关的库存修改。'}]}
            return response
        self.app.state.model.ask_json = checking
        previous = [{'id': 'legacy', 'status': 'completed'}]
        self.app.state.store.put('quests', self.pid, previous)
        self.assertEqual(self.request().status_code, 400)
        self.assertEqual(self.app.state.store.get('quests', self.pid), previous)
        state = self.client.get(self.base + '/campaign').json()
        self.assertEqual(state['generation']['quests_completed'], 3)
        self.calls.clear(); failed_check = False
        response = self.request()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([p.get('quest_number') for p in self.calls if p['stage'] == 'quests'], [4, 5, 6])
        for payload in self.calls:
            if payload['stage'] == 'quests':
                self.assertIn('库存修改', json.dumps(payload['revision_notes'], ensure_ascii=False))

    def test_checker_timeout_reuses_all_details(self):
        self.fail_at = None
        original = self.model
        fail_check = True
        def checking(system, user, **kwargs):
            payload = json.loads(user)
            if payload['stage'] == 'checking' and fail_check:
                self.calls.append(payload)
                raise ValueError('synthetic checking timeout')
            return original(system, user, **kwargs)
        self.app.state.model.ask_json = checking
        self.assertEqual(self.request().status_code, 400)
        self.assertEqual(self.client.get(self.base + '/campaign').json()['generation']['quests_completed'], 6)
        self.calls.clear(); fail_check = False
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual([p['stage'] for p in self.calls], ['checking'])

    def test_model_change_discards_segments_but_timeout_change_does_not(self):
        self.app.state.model.configure('http://127.0.0.1:9999/v1', 'fixture', generation_timeout=300)
        self.request(); self.calls.clear(); self.fail_at = None
        self.app.state.model.configure('http://127.0.0.1:9999/v1', 'fixture', api_key='synthetic-key', generation_timeout=420)
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual(self.calls[0].get('quest_number'), 4)
        # New project: a model-name change invalidates all private segments.
        self.project = self.client.post('/api/projects/import', json={'path': self.project['source_path']}).json()
        self.pid = self.project['id']; self.base = '/api/projects/' + self.pid
        self.fail_at = 4; self.request(); self.calls.clear(); self.fail_at = None
        self.app.state.model.configure('http://127.0.0.1:9999/v1', 'another-model')
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual(self.calls[0]['stage'], 'analyzing')

    def test_model_change_during_response_does_not_save_current_segment(self):
        self.app.state.model.configure('http://127.0.0.1:9999/v1', 'fixture')
        original = self.model
        def changing(system, user, **kwargs):
            response = original(system, user, **kwargs)
            if json.loads(user)['stage'] == 'analyzing':
                self.app.state.model.configure('http://127.0.0.1:9999/v1', 'changed')
            return response
        self.app.state.model.ask_json = changing
        self.assertEqual(self.request().status_code, 400)
        self.assertEqual(self.client.get(self.base + '/campaign').json()['generation']['analysis_completed'], 0)

    def test_transitive_code_constraints_reach_later_reading_dependencies(self):
        self.fail_at = None
        # Existing quest5 modifies code; quest6 depends on a reading quest4,
        # which is in turn made dependent on a prior code quest3.
        code = copy.deepcopy(self.response['quests'][4])
        code.update(title=self.response['quests'][2]['title'], focus='language')
        self.response['quests'][2] = code
        response = self.request()
        self.assertEqual(response.status_code, 200, response.text)
        fifth = next(p for p in self.calls if p.get('quest_number') == 5)
        inherited = next(p for p in fifth['prerequisites'] if p['quest_number'] == 3)
        self.assertTrue(inherited['transitive'])
        self.assertIn('扣减', inherited['planned_changes'])

    def test_source_change_while_context_is_read_never_saves_stale_evidence(self):
        from backend.campaigns import Campaigns
        original = Campaigns.context
        def changing(service, project_id):
            context = original(service, project_id)
            file = service.projects.read_file(project_id, 'main.py')
            service.projects.write_file(project_id, 'main.py', file['content'] + '\n# concurrent edit\n')
            return context
        with patch.object(Campaigns, 'context', changing):
            self.assertEqual(self.request().status_code, 400)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.app.state.store.get('campaign_drafts', self.pid))

    def test_unchanged_analysis_is_reused_after_another_file_changes(self):
        source = self.root / 'source'
        (source / 'helper.py').write_text('def helper():\n    return 1\n', encoding='utf-8')
        self.project = self.client.post('/api/projects/import', json={'path': str(source)}).json()
        self.pid = self.project['id']; self.base = '/api/projects/' + self.pid
        self.assertEqual(self.request().status_code, 400)
        self.calls.clear(); self.fail_at = None
        self.app.state.projects.write_file(self.pid, 'helper.py', 'def helper():\n    return 2\n')
        observed = []
        original = self.model
        def observing(system, user, **kwargs):
            if json.loads(user)['stage'] == 'planning':
                observed.append(self.app.state.store.get('campaigns', self.pid)['generation']['message'])
            return original(system, user, **kwargs)
        self.app.state.model.ask_json = observing
        result = self.request()
        self.assertEqual(result.status_code, 200, result.text)
        analyses = [p for p in self.calls if p['stage'] == 'analyzing']
        self.assertEqual([s['path'] for p in analyses for s in p['sources']], ['helper.py'])
        self.assertIn('复用1批', observed[0])

    def test_outline_conflict_resumes_planning_with_feedback(self):
        self.fail_at = None
        original = self.model
        conflict = True
        def checking(system, user, **kwargs):
            response = original(system, user, **kwargs)
            if json.loads(user)['stage'] == 'checking' and conflict:
                return {'consistent': False, 'issues': [{'quest_number': 5, 'message': '大纲的预计改动与后关冲突。', 'scope': 'outline'}]}
            return response
        self.app.state.model.ask_json = checking
        self.assertEqual(self.request().status_code, 400)
        self.calls.clear(); conflict = False
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual(self.calls[0]['stage'], 'planning')
        self.assertIn('大纲', json.dumps(self.calls[0]['revision_notes'], ensure_ascii=False))

    def test_budgets_force_compaction_without_truncating_checker_objectives(self):
        import backend.campaign_generation as generation
        self.fail_at = None
        # Force the boundary on small synthetic source instead of requiring a
        # large real project or sending any external request.
        objective = '必须保留前一关参数约束。' * 22
        for quest in self.response['quests']:
            quest['objective'] = objective
        with patch.object(generation, 'MAX_USER_BYTES', 4500):
            result = self.request()
        self.assertEqual(result.status_code, 200, result.text)
        for payload in self.calls:
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()), 4500)
        checking = [p for p in self.calls if p['stage'] == 'checking']
        self.assertGreater(len(checking), 1)
        self.assertTrue(any(item['objective'] == objective for payload in checking for item in payload['quests']))


if __name__ == '__main__':
    unittest.main()
