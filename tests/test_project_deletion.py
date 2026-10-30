"""Archive deletion uses only disposable sources/state; no real model or runner."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.campaigns import Campaigns
from backend.projects import Projects
from backend.storage import Store


@pytest.fixture
def archive(tmp_path):
    source = tmp_path / 'original'
    source.mkdir()
    (source / 'main.py').write_text('print(1)\n', encoding='utf-8')
    app = create_app(tmp_path / 'state', use_keyring=False)
    with TestClient(app) as client:
        project = client.post('/api/projects/import', json={'path': str(source)}).json()
        yield app, client, project, source


def test_delete_cascades_exact_archive_records_preserves_sources_others_and_shared_state(archive):
    app, client, project, source = archive
    store = app.state.store
    project_id = project['id']
    other = client.post('/api/projects/import', json={'path': str(source)}).json()
    exact = ['quests', 'campaigns', 'campaign_drafts', 'generation_diagnostics', 'runs', 'repair_journal', 'repair_reports']
    for namespace in exact:
        store.put(namespace, project_id, [{'marker': 'remove'}] if namespace == 'quests' else {'marker': 'remove'})
        store.put(namespace, other['id'], [{'marker': 'keep'}] if namespace == 'quests' else {'marker': 'keep'})
    for namespace in ['evaluations', 'quest_archives']:
        store.put(namespace, project_id + ':q1', {'marker': 'remove'})
        store.put(namespace, project_id + 'extra:q1', {'marker': 'keep'})
        store.put(namespace, other['id'] + ':q1', {'marker': 'keep'})
    for namespace in ['settings', 'preferences', 'credentials']:
        store.put(namespace, project_id, {'marker': 'shared'})
    cache = store.root / 'imports' / 'cached-source'
    cache.mkdir(parents=True)
    (cache / 'main.py').write_text('source cache', encoding='utf-8')

    response = client.delete('/api/projects/' + project_id)
    assert response.status_code == 200
    assert response.json()['deleted'] is True
    assert response.json()['project_id'] == project_id
    assert response.json()['message']
    assert not response.json().get('cleanup_pending')
    assert client.get('/api/projects/' + project_id).status_code == 404
    assert [p['id'] for p in client.get('/api/projects').json()] == [other['id']]
    reopened = Store(store.root)
    for namespace in ['projects'] + exact:
        assert reopened.get(namespace, project_id) is None
        assert reopened.get(namespace, other['id']) is not None
    for namespace in ['evaluations', 'quest_archives']:
        assert reopened.get(namespace, project_id + ':q1') is None
        assert reopened.get(namespace, project_id + 'extra:q1') == {'marker': 'keep'}
        assert reopened.get(namespace, other['id'] + ':q1') == {'marker': 'keep'}
    for namespace in ['settings', 'preferences', 'credentials']:
        assert reopened.get(namespace, project_id) == {'marker': 'shared'}
    assert (source / 'main.py').read_text(encoding='utf-8') == 'print(1)\n'
    assert (cache / 'main.py').read_text(encoding='utf-8') == 'source cache'
    for parent in ['workspaces', 'baselines']:
        assert not (store.root / parent / project_id).exists()
        assert (store.root / parent / other['id'] / 'main.py').is_file()


def test_delete_missing_invalid_and_cross_site_requests_are_non_destructive(archive):
    app, client, project, source = archive
    assert client.delete('/api/projects/' + 'f' * 32).status_code == 404
    raw = app.state.store.get('projects', project['id'])
    app.state.store.put('projects', 'bad-id', {**raw, 'id': 'bad-id'})
    assert client.delete('/api/projects/bad-id').status_code == 400
    assert client.delete('/api/projects/' + project['id'], headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.delete('/api/projects/' + project['id'], headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    preflight = client.options('/api/projects/' + project['id'], headers={'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'DELETE'})
    assert preflight.status_code == 200
    assert (Path(project['workspace']) / 'main.py').is_file()


def test_arbitrary_saved_workspace_path_is_never_a_deletion_target(archive):
    app, client, project, source = archive
    raw = app.state.store.get('projects', project['id'])
    app.state.store.put('projects', project['id'], {**raw, 'workspace': str(source)})
    assert client.delete('/api/projects/' + project['id']).status_code == 200
    assert (source / 'main.py').is_file()
    assert not (app.state.store.root / 'workspaces' / project['id']).exists()


@pytest.mark.parametrize('owner', ['self', 'other'])
def test_source_inside_owned_directory_blocks_deletion_before_mutation(archive, owner):
    app, client, project, source = archive
    target = project if owner == 'self' else client.post('/api/projects/import', json={'path': str(source)}).json()
    raw = app.state.store.get('projects', target['id'])
    app.state.store.put('projects', target['id'], {**raw, 'source_path': str(Path(project['workspace']) / 'main.py')})
    assert client.delete('/api/projects/' + project['id']).status_code == 400
    assert (Path(project['workspace']) / 'main.py').is_file()
    assert app.state.store.get('projects', project['id']) is not None


@pytest.mark.parametrize('boundary', ['parent', 'target', 'nested', 'trash'])
def test_windows_reparse_boundaries_are_rejected_before_staging(archive, boundary):
    app, client, project, source = archive
    root = app.state.store.root
    target = {'parent': root / 'workspaces', 'target': Path(project['workspace']), 'nested': Path(project['workspace']) / 'main.py', 'trash': root / '.deletions'}[boundary]
    if boundary == 'trash':
        target.mkdir()
    original_lstat = Path.lstat
    def lstat(path, *args, **kwargs):
        result = original_lstat(path, *args, **kwargs)
        return SimpleNamespace(st_file_attributes=0x400, st_mode=result.st_mode) if path == target else result
    with patch.object(Path, 'lstat', lstat):
        assert client.delete('/api/projects/' + project['id']).status_code == 400
    assert (Path(project['workspace']) / 'main.py').is_file()
    assert (root / 'baselines' / project['id'] / 'main.py').is_file()


def test_symlink_to_source_blocks_deletion_and_preserves_source(archive):
    app, client, project, source = archive
    link = Path(project['workspace']) / 'linked'
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip('Windows user cannot create symlinks; reparse cases tested separately')
    assert client.delete('/api/projects/' + project['id']).status_code == 400
    assert (source / 'main.py').is_file()


def test_unreadable_subtree_aborts_validation_before_any_directory_is_moved(archive):
    import os
    app, client, project, source = archive
    nested = Path(project['workspace']) / 'unreadable'
    nested.mkdir()
    (nested / 'main.py').write_text('print(2)', encoding='utf-8')
    scan = os.scandir
    def denied(path):
        if Path(path) == nested:
            raise PermissionError('synthetic unreadable subtree')
        return scan(path)
    with patch('backend.project_deletion.os.scandir', denied):
        response = client.delete('/api/projects/' + project['id'])
    assert response.status_code == 400
    assert (nested / 'main.py').is_file()
    assert (app.state.store.root / 'baselines' / project['id'] / 'main.py').is_file()
    assert app.state.store.all('deletion_journal') == []


def test_second_stage_failure_rolls_back_first_directory_and_records(archive):
    app, client, project, source = archive
    baseline = app.state.store.root / 'baselines' / project['id']
    rename = Path.rename
    def fail_second(path, target):
        if path == baseline:
            raise OSError('synthetic stage failure')
        return rename(path, target)
    with patch.object(Path, 'rename', fail_second):
        response = client.delete('/api/projects/' + project['id'])
    assert response.status_code == 400
    assert client.get('/api/projects/' + project['id']).status_code == 200
    assert (Path(project['workspace']) / 'main.py').is_file()
    assert (baseline / 'main.py').is_file()


def test_database_failure_rolls_back_metadata_and_staged_directories(archive):
    app, client, project, source = archive
    store = app.state.store
    store.put('runs', project['id'], [{'id': 'run'}])
    with store.connect() as db:
        db.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON records WHEN OLD.namespace='runs' BEGIN SELECT RAISE(ABORT, 'synthetic database failure'); END")
    response = client.delete('/api/projects/' + project['id'])
    assert response.status_code == 400
    assert client.get('/api/projects/' + project['id']).status_code == 200
    assert store.get('runs', project['id']) == [{'id': 'run'}]
    assert (Path(project['workspace']) / 'main.py').is_file()


def test_postcommit_cleanup_failure_is_logically_deleted_and_retried_on_reopen(archive):
    app, client, project, source = archive
    with patch('backend.project_deletion.shutil.rmtree', side_effect=OSError('synthetic cleanup failure')):
        response = client.delete('/api/projects/' + project['id'])
    assert response.status_code == 200
    assert response.json()['cleanup_pending'] is True
    assert client.get('/api/projects/' + project['id']).status_code == 404
    assert client.get('/api/projects').json() == []
    assert app.state.store.all('deletion_journal')
    restored = create_app(app.state.store.root, use_keyring=False)
    assert restored.state.projects.list() == []
    assert restored.state.store.all('deletion_journal') == []
    assert (source / 'main.py').is_file()


def test_interruption_before_commit_recovers_staged_directories_on_reopen(archive):
    app, client, project, source = archive
    with patch.object(Store, 'delete_project_records', side_effect=KeyboardInterrupt('synthetic crash')):
        with pytest.raises(KeyboardInterrupt):
            app.state.projects.delete(project['id'])
    assert app.state.store.get('projects', project['id']) is not None
    assert not Path(project['workspace']).exists()
    restored = create_app(app.state.store.root, use_keyring=False)
    assert restored.state.projects.get(project['id'])['id'] == project['id']
    assert (Path(project['workspace']) / 'main.py').is_file()
    assert restored.state.store.all('deletion_journal') == []


def test_failed_restore_hides_archive_and_keeps_recovery_journal_until_restart(archive):
    app, client, project, source = archive
    rename = Path.rename
    def fail_restore(path, target):
        if path.parent.parent.name == '.deletions':
            raise OSError('synthetic restore failure')
        return rename(path, target)
    with patch.object(Store, 'delete_project_records', side_effect=OSError('database unavailable')), patch.object(Path, 'rename', fail_restore):
        assert client.delete('/api/projects/' + project['id']).status_code == 409
    assert app.state.store.get('projects', project['id']) is not None
    assert app.state.store.all('deletion_journal')
    assert client.get('/api/projects/' + project['id']).status_code == 409
    assert client.get('/api/projects').json() == []
    with patch.object(Path, 'rename', fail_restore), pytest.raises(OSError):
        create_app(app.state.store.root, use_keyring=False)
    restored = create_app(app.state.store.root, use_keyring=False)
    assert restored.state.projects.get(project['id'])['id'] == project['id']
    assert (Path(project['workspace']) / 'main.py').is_file()


def test_other_archive_workspace_overlap_is_protected(archive):
    app, client, project, source = archive
    other = client.post('/api/projects/import', json={'path': str(source)}).json()
    raw = app.state.store.get('projects', other['id'])
    app.state.store.put('projects', other['id'], {**raw, 'workspace': project['workspace']})
    assert client.delete('/api/projects/' + project['id']).status_code == 400
    assert (Path(project['workspace']) / 'main.py').is_file()


def test_delete_inflight_blocks_refresh_and_list_cannot_publish_a_removed_archive(archive):
    app, client, project, source = archive
    started, finish, list_started, list_done = Event(), Event(), Event(), Event()
    original = Store.delete_project_records
    def delayed(store, project_id):
        started.set()
        assert finish.wait(10)
        return original(store, project_id)
    def listing():
        list_started.set()
        result = client.get('/api/projects')
        list_done.set()
        return result
    with patch.object(Store, 'delete_project_records', delayed), ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.delete, '/api/projects/' + project['id'])
        try:
            assert started.wait(5)
            assert client.post('/api/projects/' + project['id'] + '/refresh').status_code == 409
            listed = pool.submit(listing)
            assert list_started.wait(5)
            assert not list_done.wait(.1)
        finally:
            finish.set()
        assert pending.result().status_code == 200
        assert listed.result().json() == []


def test_other_project_completion_and_archive_deletion_do_not_invert_locks(archive):
    from backend.lessons import Lessons
    app, client, project, source = archive
    other = client.post('/api/projects/import', json={'path': str(source)}).json()
    store, projects = app.state.store, app.state.projects
    store.put('quests', other['id'], [
        {'id': 'first', 'order': 1, 'status': 'available', 'source_file': 'main.py'},
        {'id': 'second', 'order': 2, 'status': 'locked', 'source_file': 'main.py'},
    ])
    lessons = Lessons(store, projects, None)
    activating, deletion_locked = Event(), Event()
    completed, deleted, errors = [], [], []
    activate = lessons.activate
    def gated_activate(*args):
        activating.set()
        # With a shared RLock deletion waits here until completion releases it.
        # Separate locks permit deletion to hold the project lock, exposing inversion.
        deletion_locked.wait(.2)
        return activate(*args)
    def complete():
        try:
            completed.append(lessons.complete(other['id'], 'first'))
        except BaseException as error:
            errors.append(error)
    def delete():
        try:
            with projects.lock:
                deletion_locked.set()
                deleted.append(projects.delete(project['id']))
        except BaseException as error:
            errors.append(error)
    with patch.object(lessons, 'activate', gated_activate):
        completing = Thread(target=complete, daemon=True)
        deleting = Thread(target=delete, daemon=True)
        completing.start()
        assert activating.wait(5)
        deleting.start()
        completing.join(2)
        deleting.join(2)
        assert not completing.is_alive() and not deleting.is_alive(), 'completion and deletion deadlocked'
    assert errors == []
    assert completed[0]['status'] == 'completed'
    assert deleted[0]['deleted'] is True
    progress = store.get('quests', other['id'])
    assert [q['status'] for q in progress] == ['completed', 'available']
    assert progress[1]['baseline_content'] == 'print(1)\n'


@pytest.mark.parametrize('kind', ['demo', 'git'])
def test_import_final_publication_cannot_race_listing_or_deletion(archive, kind):
    app, client, project, source = archive
    publishing, finish, listed, deleting = Event(), Event(), Event(), Event()
    ids = []
    put = Store.put
    def gated_put(store, namespace, key, value):
        if namespace == 'projects' and key != project['id'] and key not in ids:
            ids.append(key)
        final_publication = (kind == 'demo' and namespace == 'quests') or (kind == 'git' and namespace == 'projects' and key in ids and value.get('name') == 'repository')
        if final_publication:
            publishing.set()
            assert finish.wait(10)
        return put(store, namespace, key, value)
    def fake_clone(args, **kwargs):
        destination = Path(args[-1])
        destination.mkdir(parents=True)
        (destination / 'main.py').write_text('print(3)\n', encoding='utf-8')
        return SimpleNamespace(returncode=0)
    def listing():
        result = client.get('/api/projects')
        listed.set()
        return result
    def deletion():
        result = client.delete('/api/projects/' + ids[0])
        deleting.set()
        return result
    suffix, body = ('demo', {'language': 'python'}) if kind == 'demo' else ('import', {'git_url': 'https://example.invalid/repository.git'})
    with patch.object(Store, 'put', gated_put), patch('backend.projects.shutil.which', return_value='synthetic-git'), patch('backend.projects.subprocess.run', fake_clone), ThreadPoolExecutor(max_workers=3) as pool:
        importing = pool.submit(client.post, '/api/projects/' + suffix, json=body)
        try:
            assert publishing.wait(5)
            listing_result = pool.submit(listing)
            deleting_result = pool.submit(deletion)
            assert not listed.wait(.1), 'list exposed an archive before final publication'
            assert not deleting.wait(.1), 'delete entered an unfinished import'
        finally:
            finish.set()
        assert importing.result().status_code == 200
        assert listing_result.result().status_code == 200
        assert deleting_result.result().status_code == 200
    assert client.get('/api/projects/' + ids[0]).status_code == 404
    assert [p['id'] for p in client.get('/api/projects').json()] == [project['id']]
    assert (source / 'main.py').is_file()


@pytest.mark.parametrize('kind', ['generation', 'run', 'refresh'])
def test_delete_conflicts_with_inflight_operation_then_succeeds(archive, kind):
    app, client, project, source = archive
    started, finish = Event(), Event()
    base = '/api/projects/' + project['id']
    refresh = Projects.refresh
    def delayed(*args, **kwargs):
        started.set()
        assert finish.wait(10)
        if kind == 'refresh':
            return refresh(*args, **kwargs)
        return {'status': 'failed'} if kind == 'generation' else {'exit_code': 0, 'stdout': '', 'stderr': ''}
    target = {'generation': patch.object(Campaigns, 'generate', delayed), 'run': patch('backend.api.run_project', delayed), 'refresh': patch.object(Projects, 'refresh', delayed)}[kind]
    suffix = {'generation': '/campaign', 'run': '/run', 'refresh': '/refresh'}[kind]
    body = {'role': 'mage'} if kind == 'generation' else {}
    with target, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, base + suffix, json=body)
        try:
            assert started.wait(5)
            assert client.delete(base).status_code == 409
            assert (Path(project['workspace']) / 'main.py').is_file()
        finally:
            finish.set()
        assert pending.result().status_code == 200
    assert client.delete(base).status_code == 200
    assert client.post(base + '/refresh').status_code == 404
    assert client.get('/api/projects').json() == []
