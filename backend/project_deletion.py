"""Recoverable deletion of canonical, app-owned archive copies only.

The durable journal precedes filesystem moves. After interruption, an existing
archive is restored before routes become available; a committed deletion is
cleaned up again. A failed restore keeps the journal and blocks archive access.
No path stored in a project or journal is used as a recursive deletion target.
"""
import os
import re
import shutil
import stat
import uuid
from pathlib import Path


def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('存档标识无效，未删除任何文件。')


def checked_path(root, path, *, tree=False):
    """Check lexical/resolved ancestry and every reparse boundary, without following links."""
    root, path = Path(root), Path(path)
    if not path.is_relative_to(root):
        raise ValueError('存档目录越界，已停止删除。')
    current = root
    for part in ('', *path.relative_to(root).parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('存档目录含链接或 Windows 重解析点，已停止删除。')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('存档目录越界，已停止删除。')
    if tree and path.exists():
        if not path.is_dir():
            raise ValueError('存档目录类型异常，已停止删除。')
        def unreadable(error):
            raise ValueError('无法完整检查存档目录，已停止删除；请确认目录可读取后重试。') from error
        for base, directories, files in os.walk(path, followlinks=False, onerror=unreadable):
            for name in directories + files:
                checked_path(root, Path(base) / name)
    return path


def paths(store, project_id, token):
    validate_id(project_id)
    validate_id(token)
    root = store.root
    stage = root / '.deletions' / (project_id + '-' + token)
    pairs = [(root / name / project_id, stage / name) for name in ('workspaces', 'baselines')]
    # Validate the complete plan before the first filesystem mutation.
    checked_path(root, stage, tree=True)
    for original, staged in pairs:
        checked_path(root, original, tree=True)
        checked_path(root, staged, tree=True)
    return stage, pairs


def protect_sources(store, project_id, targets):
    for project in store.all('projects'):
        protected = [project.get('source_path')]
        if project.get('id') != project_id:
            protected.append(project.get('workspace'))
        for raw in protected:
            if not raw:
                continue
            source = Path(raw).resolve()
            if any(source.is_relative_to(target.resolve()) or target.resolve().is_relative_to(source) for target in targets):
                raise ValueError('待删除目录与原始源码或其他存档重叠，已停止删除。')


def cleanup(store, journal):
    stage, pairs = paths(store, journal['project_id'], journal['token'])
    protect_sources(store, journal['project_id'], [stage])
    if stage.exists():
        checked_path(store.root, stage, tree=True)
        shutil.rmtree(stage)
    store.delete_record('deletion_journal', journal['project_id'])


def restore(store, journal):
    stage, pairs = paths(store, journal['project_id'], journal['token'])
    protect_sources(store, journal['project_id'], [stage])
    for original, staged in reversed(pairs):
        if staged.exists():
            if original.exists():
                raise ValueError('删除恢复冲突：原位置已有目录，请保留恢复日志并检查。')
            checked_path(store.root, original)
            checked_path(store.root, staged, tree=True)
            staged.rename(original)
    cleanup(store, journal)


def recover(store):
    """Called before an app serves archives. Incomplete restores fail closed."""
    for journal in store.all('deletion_journal'):
        if store.get('projects', journal['project_id']) is not None:
            restore(store, journal)
        else:
            try:
                cleanup(store, journal)
            except (OSError, ValueError):
                # Already logically deleted. Retain the journal for another retry.
                pass


def delete(store, project_id):
    validate_id(project_id)
    journal = {'project_id': project_id, 'token': uuid.uuid4().hex}
    stage, pairs = paths(store, project_id, journal['token'])
    if stage.exists():
        raise ValueError('删除暂存目录冲突，请重试。')
    protect_sources(store, project_id, [p for p, _ in pairs] + [stage])
    store.put('deletion_journal', project_id, journal)
    try:
        stage.mkdir(parents=True)
        for original, staged in pairs:
            checked_path(store.root, original, tree=True)
            checked_path(store.root, staged)
            if original.exists():
                original.rename(staged)
        store.delete_project_records(project_id)
    except Exception as exc:
        try:
            restore(store, journal)
        except Exception as restore_error:
            raise ValueError('删除未完成且恢复冲突；存档已暂时锁定，请保留数据并重新启动以恢复。') from restore_error
        raise ValueError('删除未完成，存档和练习目录已保留，请重试。') from exc
    result = {'deleted': True, 'project_id': project_id, 'message': '存档已删除，原始源码和共享设置已保留。'}
    try:
        cleanup(store, journal)
    except Exception:
        result.update(cleanup_pending=True, message='存档已删除；部分练习副本暂未清理，将在下次启动时重试。原始源码和共享设置已保留。')
    return result
