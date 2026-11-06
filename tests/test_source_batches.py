import base64
import json

import pytest

from backend.sources import SourceImports
from backend.storage import Store


def uploaded(path, text):
    return {'path': path, 'data_base64': base64.b64encode(text.encode('utf-8')).decode()}


@pytest.fixture
def sources(tmp_path):
    return SourceImports(Store(tmp_path / 'state'))


def test_batches_cover_every_selected_character_once_with_real_wire_limits(sources):
    texts = {'book.md': ('章节知识与关系。\\n"条件"\n' * 2200) + '唯一结尾概念', 'src/main.py': ('print("state")\n' * 1300) + '# final-module'}
    record = sources.create(files=[uploaded(path, content) for path, content in texts.items()])
    batches = sources.batches(record['id'])
    assert len(batches) > 2
    assert [b['id'] for b in batches] == [f'b{i + 1}' for i in range(len(batches))]
    fragments = [part for batch in batches for part in batch['sources']]
    assert len({p['id'] for p in fragments}) == len(fragments)
    for path, content in texts.items():
        assert ''.join(p['content'] for p in fragments if p['path'] == path) == content
    for batch in batches:
        assert batch['chars'] == sum(len(p['content']) for p in batch['sources'])
        assert 0 < batch['chars'] <= 24000
        assert len(json.dumps(batch['sources'], ensure_ascii=False).encode('utf-8')) <= 16000
    assert fragments == sources.selected_fragments(record['id'])


def test_task_can_read_exact_tail_sources_without_prefix_truncation(sources):
    record = sources.create(files=[uploaded('book.txt', '早期概念\n' * 5000 + '尾部独有知识：河床逆流。')])
    all_parts = sources.selected_fragments(record['id'])
    tail = all_parts[-1]
    assert tail['id'] not in {p['id'] for p in sources.context(record['id'])}
    assert sources.context_for_ids(record['id'], [tail['id']]) == [tail]
    assert '河床逆流' in tail['content']
    with pytest.raises(ValueError, match='范围|来源|片段'):
        sources.context_for_ids(record['id'], ['missing'])
    with pytest.raises(ValueError, match='预算|上限|过大'):
        sources.context_for_ids(record['id'], [p['id'] for p in all_parts], max_bytes=16000)


def test_oversized_legacy_fragment_is_split_losslessly_with_stable_lookup(sources):
    record = sources.create(files=[uploaded('legacy.txt', 'placeholder')])
    content = ('中文材料\n' * 6000) + 'END'
    record['fragments'][0]['content'] = content
    sources.store.put('imports_v2', record['id'], record)
    parts = sources.selected_fragments(record['id'])
    assert len(parts) > 1
    assert ''.join(p['content'] for p in parts) == content
    assert sources.selected_fragments(record['id']) == parts
    assert sources.context_for_ids(record['id'], [parts[-1]['id']]) == [parts[-1]]
    assert parts[-1]['line'] > parts[0]['line']
    assert all(len(json.dumps(b['sources'], ensure_ascii=False).encode()) <= 16000 for b in sources.batches(record['id']))


def test_scope_excludes_sources_and_invalidates_analysis_checkpoints(sources):
    record = sources.create(files=[uploaded('a.md', 'alpha'), uploaded('b.md', 'beta')])
    ids = {p['path']: p['id'] for p in record['fragments']}
    record.update(status='analyzed', analysis={'summary': 'old'}, analysis_complete=True,
                  analysis_work={'fingerprint': 'old', 'batches': {'b1': {'private': 'result'}}},
                  analysis_progress={'phase': 'complete'}, analysis_focus='复习')
    sources.store.put('imports_v2', record['id'], record)
    scoped = sources.select_scope(record['id'], ['b.md'])
    assert scoped['status'] == 'imported'
    assert not scoped.get('analysis_complete')
    assert not scoped.get('analysis_work') and not scoped.get('analysis_progress') and not scoped.get('analysis')
    assert {p['path'] for b in sources.batches(record['id']) for p in b['sources']} == {'b.md'}
    with pytest.raises(ValueError):
        sources.context_for_ids(record['id'], [ids['a.md']])


def test_smaller_batch_budget_keeps_derived_ids_resolvable(sources):
    record = sources.create(files=[uploaded('notes.md', '知识与原理\n' * 1000)])
    parts = [part for batch in sources.batches(record['id'], max_chars=900, max_bytes=2500) for part in batch['sources']]
    assert ''.join(part['content'] for part in parts) == '知识与原理\n' * 1000
    for part in parts:
        assert sources.context_for_ids(record['id'], [part['id']]) == [part]


def test_public_coverage_exposes_batch_count_without_internal_checkpoint_data(sources):
    record = sources.create(files=[uploaded('book.md', '需要阅读的知识。\n' * 5000)])
    record.update(status='analyzed', analysis_work={'private': 'hidden'}, analysis={'summary': 'old prefix'})
    public = sources.public(record)
    assert 'analysis_work' not in public
    assert public['analysis_complete'] is False
    assert public['coverage']['batch_count'] == len(sources.batches(record['id']))
    assert public['coverage']['batch_count'] > 1
    assert public['coverage']['max_batch_chars'] == max(b['chars'] for b in sources.batches(record['id']))
    short = sources.create(files=[uploaded('short.md', '完整短文')])
    short['status'] = 'analyzed'
    assert sources.public(short)['analysis_complete'] is True
