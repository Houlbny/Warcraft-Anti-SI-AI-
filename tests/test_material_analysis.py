import copy
import json
from collections import Counter

import pytest

from backend.adventures import Adventures
from backend.storage import Store


class BatchSources:
    def __init__(self, store, count=3):
        self.store = store
        self.parts = [{'id': f's{i}', 'path': f'part-{i}.md', 'line': 1,
                       'content': f'CONCEPT_{i}: original teaching for section {i}.'} for i in range(1, count + 1)]
        self.store.put('imports_v2', 'i', {'id': 'i', 'title': 'large book', 'snapshot_hash': 'snapshot',
            'scope_revision': 0, 'scope': {'paths': [p['path'] for p in self.parts]}, 'status': 'imported'})

    def get(self, iid):
        return self.store.get('imports_v2', iid)

    def selected_fragments(self, iid):
        return copy.deepcopy(self.parts)

    def batches(self, iid, **kwargs):
        return [{'id': f'b{i}', 'sources': [copy.deepcopy(p)], 'chars': len(p['content'])}
                for i, p in enumerate(self.parts, 1)]

    def context(self, iid, max_chars=24000):
        return copy.deepcopy(self.parts[:1])  # The retired, deliberately incomplete preview.

    def context_for_ids(self, iid, source_ids, max_bytes=None):
        by_id = {p['id']: p for p in self.parts}
        result = [copy.deepcopy(by_id[sid]) for sid in source_ids]
        if max_bytes and len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes:
            raise ValueError('over budget')
        return result


def analysis(sources, *, summary=None):
    return {'summary': summary or ' / '.join(p['content'] for p in sources), 'content_type': '文档',
        'recommendations': [{'role': 'mage', 'reason': '概念文档', 'focus': '理解关系'}],
        'outline': [{'title': p['id'], 'summary': p['content'], 'source_ids': [p['id']]} for p in sources]}


class BatchModel:
    def __init__(self):
        self.calls = []
        self.fail_once = None
        self.failed = False
        self.model = 'fake-model'
        self.hook = None

    def public(self):
        return {'base_url': 'http://localhost/v1', 'model': self.model}

    def ask_json(self, system, user, **kwargs):
        payload = json.loads(user)
        self.calls.append((system, payload, len(user.encode())))
        if self.hook:
            self.hook(system, payload)
        if 'ANALYSIS_BATCH' in system or ('ANALYSIS:' in system and 'sources' in payload):
            ids = [p['id'] for p in payload['sources']]
            if self.fail_once in ids and not self.failed:
                self.failed = True
                raise ValueError('batch failed once')
            return analysis(payload['sources'])
        if 'ANALYSIS_MERGE' in system:
            children = payload['analyses']
            entries = [entry for child in children for entry in child['outline']]
            return {'summary': 'All sections, including ' + entries[-1]['summary'], 'content_type': '文档',
                'recommendations': [{'role': 'mage', 'reason': '概念文档', 'focus': '理解关系'}],
                'outline': [entries[0], entries[-1]]}
        if 'PLAN' in system:
            sources = payload['sources']
            last = sources[-1]['id']
            return {'title': 'full book', 'summary': 'tail matters',
                'topics': [{'id': 'topic', 'title': 'Tail', 'description': 'late concept'}],
                'objectives': [{'id': 'tail', 'topic_id': 'topic', 'title': 'Late concept', 'description': 'Explain tail',
                    'source_ids': [last], 'prerequisite_ids': [], 'required': True}]}
        if 'TASK' in system:
            return {'title': 'tail task', 'brief': 'read original', 'objective': 'understand',
                'teaching': [{'title': 'source', 'body': payload['sources'][-1]['content']}],
                'question': 'Explain', 'interaction': 'explain', 'source_refs': [payload['sources'][-1]['id']],
                'criteria': ['explain'], 'hints': [], 'answer_baseline': 'meaning'}
        if 'REVIEW' in system:
            return {'status': 'passed', 'feedback': 'ok', 'checks': [{'criterion': 'explain', 'passed': True, 'evidence': 'source'}]}
        raise AssertionError(system)


def domain(tmp_path, count=3):
    store = Store(tmp_path)
    sources = BatchSources(store, count)
    model = BatchModel()
    return Adventures(store, sources, model), sources, model


def reads(model):
    return [p['sources'][0]['id'] for system, p, _ in model.calls if 'ANALYSIS_BATCH' in system]


def test_every_batch_is_read_and_tail_original_is_used_for_task_and_review(tmp_path):
    d, src, model = domain(tmp_path)
    d.analyze('i')
    record = src.get('i')
    assert record['status'] == 'analyzed', record.get('error')
    assert record['analysis_complete'] is True
    assert Counter(reads(model)) == Counter({'s1': 1, 's2': 1, 's3': 1})
    assert record['analysis_progress']['processed_chars'] == sum(len(p['content']) for p in src.parts)
    hero = d.create_character('mage')
    adventure = d.create_adventure(hero['id'], 'i')
    d.prepare(adventure['id'])
    task = d.get(adventure['id'])['tasks'][0]
    assert task['status'] == 'ready', task.get('error')
    task_payload = next(p for s, p, _ in model.calls if 'TASK' in s)
    assert task_payload['sources'] == [src.parts[-1]]
    d.answer(adventure['id'], task['id'], 'My explanation')
    review_payload = next(p for s, p, _ in model.calls if 'REVIEW' in s)
    assert review_payload['sources'] == [src.parts[-1]]


def test_failed_batch_preserves_completed_work_and_retry_only_reads_missing_batch(tmp_path):
    d, src, model = domain(tmp_path)
    model.fail_once = 's2'
    d.analyze('i', 'recall')
    failed = src.get('i')
    assert failed['status'] == 'failed'
    assert failed['analysis_complete'] is False
    assert failed['analysis_progress']['completed_batches'] == 2
    assert failed['analysis_progress']['failed_batches'] == 1
    assert 'analysis' not in failed
    before = Counter(reads(model))
    d = Adventures(d.store, src, model)
    d.analyze('i', 'recall')
    after = Counter(reads(model))
    assert after - before == Counter({'s2': 1})
    assert src.get('i')['status'] == 'analyzed'
    assert src.get('i')['analysis_focus'] == 'recall'


@pytest.mark.parametrize('change', ['focus', 'model', 'scope'])
def test_changed_input_invalidates_all_cached_batches(tmp_path, change):
    d, src, model = domain(tmp_path)
    d.analyze('i', 'first')
    before = len(reads(model))
    if change == 'model': model.model = 'new-model'
    if change == 'scope':
        record = src.get('i'); record['scope_revision'] += 1; d.store.put('imports_v2', 'i', record)
    d.analyze('i', 'second' if change == 'focus' else 'first')
    assert len(reads(model)) == before + 3
    assert src.get('i')['analysis_complete'] is True


def test_stale_inflight_batch_cannot_publish_or_replace_new_scope(tmp_path):
    d, src, model = domain(tmp_path)
    def change_scope(system, payload):
        if 'ANALYSIS_BATCH' in system:
            record = src.get('i'); record['scope_revision'] = 1; d.store.put('imports_v2', 'i', record)
            model.hook = None
    model.hook = change_scope
    d.analyze('i')
    record = src.get('i')
    assert record['scope_revision'] == 1
    assert record['status'] == 'failed'
    assert not record.get('analysis_complete')
    assert 'analysis' not in record


def test_many_summaries_are_reduced_in_bounded_groups(tmp_path):
    d, src, model = domain(tmp_path, 24)
    for p in src.parts: p['content'] += '知识背景和关系。' * 80
    d.analyze('i')
    assert src.get('i')['status'] == 'analyzed', src.get('i').get('error')
    merge_calls = [p for s, p, _ in model.calls if 'ANALYSIS_MERGE' in s]
    assert len(merge_calls) > 1
    assert all(len(json.dumps(p, ensure_ascii=False).encode()) <= 18000 for p in merge_calls)
    assert len(reads(model)) == 24


def test_old_prefix_analysis_cannot_create_new_large_adventure(tmp_path):
    d, src, model = domain(tmp_path)
    record = src.get('i'); record.update(status='analyzed', analysis=analysis(src.parts[:1]))
    d.store.put('imports_v2', 'i', record)
    hero = d.create_character('mage')
    with pytest.raises(ValueError, match='完整|分批|分析'):
        d.create_adventure(hero['id'], 'i')


def test_restart_never_automatically_requests_model_and_preserves_checkpoints(tmp_path):
    d, src, model = domain(tmp_path)
    model.fail_once = 's2'; d.analyze('i')
    record = src.get('i'); record['status'] = 'analyzing'; d.store.put('imports_v2', 'i', record)
    checkpoint = copy.deepcopy(record['analysis_work'])
    count = len(model.calls)
    Adventures(d.store, src, model)
    assert len(model.calls) == count
    assert src.get('i')['analysis_work'] == checkpoint
    assert src.get('i')['status'] == 'failed'


def compact_analysis(parts):
    return {'summary': 'All selected concepts', 'content_type': '文档',
        'recommendations': [{'role': 'mage', 'reason': '概念文档', 'focus': '理解关系'}],
        'outline': [{'title': 'Knowledge', 'summary': 'Selected content',
                     'source_ids': [parts[0]['id'], parts[-1]['id']]}]}


def test_single_batch_keeps_one_model_read(tmp_path):
    d, src, model = domain(tmp_path, 1)
    d.analyze('i')
    assert src.get('i')['analysis_complete'] is True
    assert len(model.calls) == 1


def test_merge_failure_resumes_without_reading_raw_batches_again(tmp_path):
    d, src, model = domain(tmp_path)
    original = model.ask_json
    failed = False
    def ask(system, user, **kwargs):
        nonlocal failed
        if 'ANALYSIS_MERGE' in system and not failed:
            failed = True
            raise ValueError('merge unavailable')
        return original(system, user, **kwargs)
    model.ask_json = ask
    d.analyze('i', 'preserved focus')
    record = src.get('i')
    assert not record['analysis_complete']
    assert record['analysis_progress']['completed_batches'] == 3
    assert record['status'] == 'failed'
    before = reads(model)
    d.analyze('i')
    assert reads(model) == before
    assert src.get('i')['analysis_focus'] == 'preserved focus'
    assert src.get('i')['analysis_complete'] is True


def test_escaped_originals_are_rebatched_by_actual_wire_size_and_ids_resolve(tmp_path):
    from backend.sources import SourceImports
    from backend.model_client import encode_chat_request, MAX_CHAPTER_REQUEST_BYTES
    store = Store(tmp_path / 'state')
    src = SourceImports(store)
    text = ('\\"' * 20000) + '\nTAIL_UNIQUE_KNOWLEDGE'
    input_path = tmp_path / 'quoted.md'
    input_path.write_text(text, encoding='utf-8', newline='')
    record = src.create(path=str(input_path))
    model = BatchModel()
    original = model.ask_json
    def ask(system, user, **kwargs):
        payload = json.loads(user)
        if 'ANALYSIS_BATCH' in system or 'ANALYSIS:' in system:
            model.calls.append((system, payload, len(user.encode())))
            return compact_analysis(payload['sources'])
        return original(system, user, **kwargs)
    model.ask_json = ask
    d = Adventures(store, src, model)
    d.analyze(record['id'], '学' * 2000)
    saved = src.get(record['id'])
    assert saved['status'] == 'analyzed', saved.get('error')
    raw_calls = [p for s, p, _ in model.calls if 'sources' in p]
    assert ''.join(s['content'] for p in raw_calls for s in p['sources']) == text
    assert saved['analysis_progress']['total_batches'] > len(src.batches(record['id']))
    for system, payload, _ in model.calls:
        assert len(encode_chat_request(model.model, system, json.dumps(payload, ensure_ascii=False),
                    response_format={'type': 'json_object'})) <= MAX_CHAPTER_REQUEST_BYTES
    for payload in raw_calls:
        assert src.context_for_ids(record['id'], [p['id'] for p in payload['sources']]) == payload['sources']
    hero = d.create_character('mage')
    adventure = d.create_adventure(hero['id'], record['id'])
    d.prepare(adventure['id'])
    state = d.get(adventure['id'])
    assert state['status'] == 'ready', state.get('error')
    assert state['tasks'][0]['status'] == 'ready', state['tasks'][0].get('error')


def test_large_legal_summaries_are_compressed_before_hierarchical_merge(tmp_path):
    from backend.model_client import encode_chat_request, MAX_CHAPTER_REQUEST_BYTES
    d, src, model = domain(tmp_path)
    original = model.ask_json
    def ask(system, user, **kwargs):
        payload = json.loads(user)
        if 'ANALYSIS_BATCH' in system:
            model.calls.append((system, payload, len(user.encode())))
            result = compact_analysis(payload['sources'])
            result['summary'] = '\\' * 1200
            result['outline'] = [{'title': str(i), 'summary': '\\' * 650,
                'source_ids': [payload['sources'][0]['id']]} for i in range(3)]
            return result
        if payload.get('summary_budget_bytes'):
            model.calls.append((system, payload, len(user.encode())))
            sid = payload['analyses'][0]['outline'][0]['source_ids'][0]
            return compact_analysis([{'id': sid}])
        return original(system, user, **kwargs)
    model.ask_json = ask
    d.analyze('i', '学' * 2000)
    saved = src.get('i')
    assert saved['status'] == 'analyzed', saved.get('error')
    assert any(p.get('summary_budget_bytes') for _, p, _ in model.calls)
    assert len(reads(model)) == 3
    for system, payload, _ in model.calls:
        assert len(encode_chat_request(model.model, system, json.dumps(payload, ensure_ascii=False),
            response_format={'type': 'json_object'})) <= MAX_CHAPTER_REQUEST_BYTES


def test_planning_overview_does_not_repeat_summary_for_each_source_reference(tmp_path):
    d, src, model = domain(tmp_path, 32)
    record = src.get('i')
    result = compact_analysis(src.parts)
    result['outline'][0].update(summary='学' * 600, source_ids=[s['id'] for s in src.parts])
    record.update(status='analyzed', analysis_complete=True, analysis=result)
    d.store.put('imports_v2', 'i', record)
    context = d._analyzer().planning_context('i')
    assert len(context) == 1
    assert len(json.dumps(context, ensure_ascii=False).encode()) < 4000
    assert context[0]['related_source_ids'] == [s['id'] for s in src.parts]


def test_oversized_goal_is_automatically_replanned_into_small_source_goals(tmp_path):
    d, src, model = domain(tmp_path)
    for part in src.parts: part['content'] = 'x' * 7000
    record = src.get('i'); record.update(status='analyzed', analysis_complete=True, analysis=compact_analysis(src.parts))
    record['analysis']['outline'][0]['source_ids'] = [p['id'] for p in src.parts]
    d.store.put('imports_v2', 'i', record)
    original = model.ask_json
    def ask(system, user, **kwargs):
        result = original(system, user, **kwargs)
        if 'PLAN' in system and not json.loads(user).get('planning_feedback'):
            result['objectives'][0]['source_ids'] = [p['id'] for p in src.parts]
        return result
    model.ask_json = ask
    hero = d.create_character('mage'); adventure = d.create_adventure(hero['id'], 'i')
    d.prepare(adventure['id'])
    state = d.get(adventure['id'])
    assert state['status'] == 'ready', state.get('error')
    plans = [p for s, p, _ in model.calls if 'PLAN' in s]
    assert len(plans) == 2 and plans[-1]['planning_feedback']
    assert len(state['objectives'][0]['source_ids']) == 1


def test_legacy_unplanned_large_adventure_finishes_analysis_on_explicit_prepare(tmp_path):
    d, src, model = domain(tmp_path)
    hero = d.create_character('mage')
    from backend.adventures import source_identity
    d.store.put('adventures_v2', 'old', {'id': 'old', 'character_id': hero['id'], 'import_id': 'i',
        'role': 'mage', 'title': 'old', 'summary': 'prefix', 'status': 'unprepared',
        'source_hash': source_identity(src.get('i')), 'topics': [], 'objectives': [], 'tasks': []})
    d.prepare('old')
    state = d.get('old')
    assert state['status'] == 'ready', state.get('error')
    assert state['source_analysis_complete'] is True
    assert len(reads(model)) == 3
