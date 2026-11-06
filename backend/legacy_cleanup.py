"""Retire the explicitly abandoned v1 campaign data, preserving app credentials."""
from pathlib import Path
import shutil


LEGACY_NAMESPACES = (
    'projects', 'quests', 'campaigns', 'campaign_drafts', 'generation_diagnostics',
    'campaign_updates', 'campaign_update_drafts', 'runs', 'repair_journal',
    'repair_reports', 'learning_journal', 'evaluations', 'quest_archives',
    'campaign_history', 'learning_materials', 'teaching_example_results',
    'deletion_journal',
)
LEGACY_DIRECTORIES = ('workspaces', 'baselines', 'imports', 'teaching_examples', 'diagnostics', '.deletions')


def cleanup_legacy(store):
    """Only known managed v1 state is removed; never follow stored source paths."""
    root = Path(store.root).resolve()
    removed_files = []
    with store.lock:
        # Validate every destination before any recursive filesystem operation.
        targets = []
        for name in LEGACY_DIRECTORIES:
            target = root / name
            if not target.exists() and not target.is_symlink():
                continue
            if target.is_symlink() or bool(getattr(target.lstat(), 'st_file_attributes', 0) & 0x400):
                raise ValueError('旧数据目录存在链接，请检查后再清理：' + name)
            resolved = target.resolve()
            if resolved.parent != root or not resolved.is_relative_to(root):
                raise ValueError('旧数据清理目录越界。')
            targets.append(resolved)
        for target in targets:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed_files.append(target.name)
        with store.connect() as db:
            placeholders = ','.join('?' for _ in LEGACY_NAMESPACES)
            count = db.execute(f'SELECT COUNT(*) FROM records WHERE namespace IN ({placeholders})', LEGACY_NAMESPACES).fetchone()[0]
            db.execute(f'DELETE FROM records WHERE namespace IN ({placeholders})', LEGACY_NAMESPACES)
            db.execute("DELETE FROM records WHERE namespace='preferences' AND key='favorites'")
        return {'removed_records': count, 'removed_directories': removed_files}
