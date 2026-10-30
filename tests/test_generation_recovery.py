import json
import unittest

import test_segmented_campaigns as fixtures

staged_response = fixtures.staged_response


class GenerationRecoveryTests(unittest.TestCase):
    setUp = fixtures.SegmentedCampaignTests.setUp
    request = fixtures.SegmentedCampaignTests.request

    def test_one_bad_summary_is_corrected_once_without_leaking_its_value(self):
        self.fail_at = None
        marker = 'synthetic-private-summary-value'
        def model(system, user, **kwargs):
            payload = json.loads(user); self.calls.append(payload)
            result = staged_response(payload, self.response)
            if len(self.calls) == 1:
                result['files'][0]['summary'] = marker * 15
            elif len(self.calls) == 2:
                progress = self.app.state.store.get('campaigns', self.pid)
                self.assertEqual(progress['generation']['attempt'], 2)
                self.assertIn('自动纠正重试 1/1', progress['generation']['message'])
            return result
        self.app.state.model.ask_json = model
        result = self.request()
        self.assertEqual(result.status_code, 200, result.text)
        analyses = [p for p in self.calls if p['stage'] == 'analyzing']
        self.assertEqual(len(analyses), 2)
        feedback = analyses[1]['validation_feedback']
        self.assertEqual((feedback['field'], feedback['reason'], feedback['maximum']), ('files[0].summary', 'too_long', 240))
        self.assertEqual(feedback['actual_length'], len(marker * 15))
        diagnostics = self.app.state.store.get('generation_diagnostics', self.pid)
        self.assertEqual(len(diagnostics), 1)
        self.assertNotIn(marker, json.dumps(diagnostics))
        self.assertNotIn(marker, json.dumps(analyses[1]))
        self.assertNotIn(marker, result.text)
        self.assertEqual(self.app.state.store.get('campaign_drafts', self.pid).get('format_failures'), {})

    def test_two_invalid_responses_stop_and_resume_only_failed_batch_with_feedback(self):
        self.fail_at = None
        source = self.root / 'source'
        (source / 'extra.py').write_text('extra = 1\n', encoding='utf-8')
        self.project = self.client.post('/api/projects/import', json={'path': str(source)}).json()
        self.pid = self.project['id']; self.base = '/api/projects/' + self.pid
        def model(system, user, **kwargs):
            payload = json.loads(user); self.calls.append(payload)
            result = staged_response(payload, self.response)
            if payload['stage'] == 'analyzing' and payload['batch'] == 2:
                result['summary'] = {'wrong': 'synthetic-private-value'}
            return result
        self.app.state.model.ask_json = model
        failed = self.request()
        self.assertEqual(failed.status_code, 400)
        self.assertEqual([p['batch'] for p in self.calls], [1, 2, 2])
        state = self.client.get(self.base + '/campaign').json()
        self.assertEqual(state['generation']['analysis_completed'], 1)
        self.assertIn('第2/2批', state['error'])
        self.assertIn('summary', state['error'])
        self.assertEqual(state['generation']['attempt'], 2)
        self.assertEqual(state['generation']['validation_error']['reason'], 'type')
        self.assertEqual(state['quests'], [])
        self.calls.clear()
        self.app.state.model.ask_json = self.model
        ready = self.request()
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertEqual(self.calls[0]['batch'], 2)
        self.assertEqual(self.calls[0]['validation_feedback']['reason'], 'type')
        self.assertEqual(len([p for p in self.calls if p['stage'] == 'analyzing']), 1)

    def test_invalid_json_is_corrected_but_transport_failures_are_not_retried(self):
        from backend.model_client import parse_json
        self.fail_at = None
        def model(system, user, **kwargs):
            payload = json.loads(user); self.calls.append(payload)
            if len(self.calls) == 1:
                return parse_json('synthetic not json')
            return staged_response(payload, self.response)
        self.app.state.model.ask_json = model
        ready = self.request()
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertEqual(self.calls[1]['validation_feedback']['reason'], 'invalid_json')

    def test_transport_error_never_consumes_a_correction_retry(self):
        def model(system, user, **kwargs):
            self.calls.append(json.loads(user))
            raise ValueError('模型接口返回 HTTP 503：synthetic failure')
        self.app.state.model.ask_json = model
        self.assertEqual(self.request().status_code, 400)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.app.state.store.get('generation_diagnostics', self.pid))

    def test_source_change_during_invalid_response_never_retries_or_saves_feedback(self):
        def model(system, user, **kwargs):
            payload = json.loads(user); self.calls.append(payload)
            self.app.state.projects.write_file(self.pid, 'main.py', 'print("changed")\n')
            return {'summary': None, 'files': []}
        self.app.state.model.ask_json = model
        failed = self.request()
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('变化', failed.text)
        self.assertIsNone(self.app.state.store.get('generation_diagnostics', self.pid))

    def test_planning_and_checker_shapes_are_corrected_independently(self):
        self.fail_at = None
        counts = {}
        def model(system, user, **kwargs):
            payload = json.loads(user); self.calls.append(payload)
            stage = payload['stage']; counts[stage] = counts.get(stage, 0) + 1
            response = staged_response(payload, self.response)
            if stage == 'planning' and counts[stage] == 1:
                response['summary'] = []
            if stage == 'checking' and counts[stage] == 1:
                response['consistent'] = 'true'
            return response
        self.app.state.model.ask_json = model
        ready = self.request()
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertEqual(counts, {'analyzing': 1, 'planning': 2, 'quests': 6, 'checking': 2})

    def test_valid_semantic_conflict_is_not_a_format_retry(self):
        self.fail_at = None
        def model(system, user, **kwargs):
            payload = json.loads(user); self.calls.append(payload)
            if payload['stage'] == 'checking':
                return {'consistent': False, 'issues': [{'quest_number': 5, 'message': 'synthetic semantic conflict', 'scope': 'detail'}]}
            return staged_response(payload, self.response)
        self.app.state.model.ask_json = model
        self.assertEqual(self.request().status_code, 400)
        self.assertEqual(len([p for p in self.calls if p['stage'] == 'checking']), 1)
        self.assertIsNone(self.app.state.store.get('generation_diagnostics', self.pid))

    def test_checker_reserves_feedback_space_and_reuses_completed_groups(self):
        from backend.campaign_generation import SegmentedGeneration
        from backend.campaigns import Campaigns
        from backend.lessons import Lessons
        lessons = Lessons(self.app.state.store, self.app.state.projects, self.app.state.model)
        service = Campaigns(self.app.state.store, self.app.state.projects, self.app.state.model, lessons)
        generator = SegmentedGeneration(service, self.pid, 'mage', {'status': 'generating', 'role': 'mage'})
        outline = {'title': '短标题', 'summary': '短摘要', 'quests': [
            {'title': f'任务{i}', 'depends_on': [i] if i else []} for i in range(10)]}
        generator.draft['outline'] = outline
        checks = [{'quest_number': i + 1, 'title': f'任务{i}', 'type': 'explore', 'focus': 'language',
                   'source_file': 'main.py', 'line': 1, 'run_entry': None, 'verification_mode': 'explain',
                   'objective': '文' * 300, 'verification': '文' * 305, 'planned_changes': '文' * 100,
                   'depends_on': [i] if i else []} for i in range(10)]
        def model(system, user, **kwargs):
            payload = json.loads(user); self.calls.append(payload)
            return {'consistent': 'true' if len(self.calls) == 1 else True, 'issues': []}
        self.app.state.model.ask_json = model
        self.assertTrue(generator.check_groups(outline, checks)['consistent'])
        self.assertTrue(all(len(p['quests']) == 2 for p in self.calls))
        self.assertTrue(all(len(json.dumps(p, ensure_ascii=False, separators=(',', ':')).encode()) <= 24000 for p in self.calls))
        # A ten-quest chain has 45 ancestor pairs, plus one corrective request.
        self.assertEqual(len(self.calls), 46)
        self.assertIn('validation_feedback', self.calls[1])
        self.calls.clear()
        self.assertTrue(generator.check_groups(outline, checks)['consistent'])
        self.assertEqual(self.calls, [])

    def test_invalid_quest_is_corrected_without_repeating_prior_segments(self):
        self.fail_at = None
        seen = 0
        def model(system, user, **kwargs):
            nonlocal seen
            payload = json.loads(user); self.calls.append(payload)
            response = staged_response(payload, self.response)
            if payload['stage'] == 'quests' and payload['quest_number'] == 2:
                seen += 1
                if seen == 1:
                    response['quest']['steps'] = []
            return response
        self.app.state.model.ask_json = model
        ready = self.request()
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertEqual([p['quest_number'] for p in self.calls if p['stage'] == 'quests'], [1, 2, 2, 3, 4, 5, 6])
        self.assertEqual(len([p for p in self.calls if p['stage'] == 'analyzing']), 1)

    def test_changed_source_does_not_reuse_obsolete_format_feedback(self):
        self.fail_at = None
        def invalid(system, user, **kwargs):
            payload = json.loads(user); self.calls.append(payload)
            response = staged_response(payload, self.response)
            response['summary'] = None
            return response
        self.app.state.model.ask_json = invalid
        self.assertEqual(self.request().status_code, 400)
        self.assertEqual(len(self.calls), 2)
        self.app.state.projects.write_file(self.pid, 'main.py', 'def remaining(stock, ordered):\n    return stock - ordered\nprint(remaining(10, 2))\n')
        self.calls.clear()
        self.app.state.model.ask_json = self.model
        ready = self.request()
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertNotIn('validation_feedback', self.calls[0])
