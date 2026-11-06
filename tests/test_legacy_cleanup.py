from backend.storage import Store
from backend.legacy_cleanup import cleanup_legacy


def test_cleanup_removes_obsolete_history_and_keeps_settings_sources_and_new_state(tmp_path):
    store = Store(tmp_path / 'state')
    store.put('settings', 'model', {'model': 'keep-setting'})
    store.put('projects', 'old', {'source_path': str(tmp_path / 'original')})
    store.put('campaign_history', 'old:1', {'materials': ['obsolete']})
    store.put('learning_materials', 'old-card', {'body': 'obsolete'})
    store.put('preferences', 'favorites', ['old-card'])
    store.put('characters_v2', 'new', {'id': 'new'})
    store.put('imports_v2', 'new-source', {'id': 'new-source'})
    original = tmp_path / 'original'
    original.mkdir()
    (original / 'keep.md').write_text('original', encoding='utf-8')
    for name in ('workspaces', 'baselines', 'teaching_examples', 'diagnostics'):
        folder = store.root / name / 'old'
        folder.mkdir(parents=True)
        (folder / 'old.txt').write_text('obsolete', encoding='utf-8')
    credential = store.root / 'credentials'
    credential.mkdir()
    (credential / 'keep.bin').write_bytes(b'not-an-actual-credential')
    cleanup_legacy(store)
    assert store.get('settings', 'model')['model'] == 'keep-setting'
    assert store.get('characters_v2', 'new')
    assert store.get('imports_v2', 'new-source')
    assert not store.get('projects', 'old')
    assert not store.get('campaign_history', 'old:1')
    assert not store.get('learning_materials', 'old-card')
    assert not store.get('preferences', 'favorites')
    assert (credential / 'keep.bin').read_bytes() == b'not-an-actual-credential'
    assert (original / 'keep.md').read_text() == 'original'
    assert not (store.root / 'workspaces').exists()
    cleanup_legacy(store)
    assert store.get('characters_v2', 'new')
