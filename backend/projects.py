import hashlib
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from urllib.parse import urlparse

from .storage import Store

IGNORED = {'.git', '.svn', '.hg', '.warcraft', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', 'target', 'dist', 'build', '.idea', '.ssh', '.aws', '.codex', '.agents', '.gradle', '.cache', '.runner'}
SECRET_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.jks', '.keystore', '.sqlite', '.sqlite3', '.db'}
TEXT_SUFFIXES = {'.py', '.java', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.go', '.sh', '.bash', '.json', '.xml', '.toml', '.yaml', '.yml', '.txt', '.md', '.csv', '.html', '.css', '.sql', '.mod', '.sum', '.lock', '.properties', '.gradle', '.kts'}


def digest(content: str):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def safe_path(root: Path, relative: str, *, exists=True):
    if not relative or '\x00' in relative or Path(relative).is_absolute() or PureWindowsPath(relative).is_absolute() or ':' in relative or '..' in relative.replace('\\', '/').split('/'):
        raise ValueError('文件路径必须位于当前练习目录内。')
    root = root.resolve()
    path = root / relative.replace('\\', '/')
    current = path
    while current != root:
        reparse = current.exists() and bool(getattr(current.lstat(), 'st_file_attributes', 0) & 0x400)
        if current.is_symlink() or reparse:
            raise ValueError('不允许通过链接访问练习目录。')
        if current == current.parent:
            raise ValueError('文件路径越界。')
        current = current.parent
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('文件路径越界。')
    if exists and not resolved.is_file():
        raise ValueError('文件不存在。')
    return resolved


def eligible(path: Path):
    low = path.name.lower()
    return not (low.startswith('.') or low.startswith('id_rsa') or 'credential' in low or 'secret' in low or path.suffix.lower() in SECRET_SUFFIXES) and (path.suffix.lower() in TEXT_SUFFIXES or low in {'makefile', 'dockerfile', 'gradlew', 'mvnw'})


def source_files(source: Path):
    if source.is_file():
        if not eligible(source):
            raise ValueError('请选择支持的源码或脚本文件。')
        yield source, source.name
        return
    count = total = 0
    for base, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and not d.startswith('.') and not (Path(base) / d).is_symlink() and (Path(base) / d).resolve().is_relative_to(source))
        for name in sorted(names):
            file = Path(base) / name
            if file.is_symlink() or not eligible(file) or not file.resolve().is_relative_to(source):
                continue
            size = file.stat().st_size
            if size > 1024 * 1024:
                continue
            count += 1
            total += size
            if count > 2000 or total > 40 * 1024 * 1024:
                raise ValueError('项目超过首版索引上限（2000 个文本文件 / 40 MB），请导入相关子目录。')
            yield file, file.relative_to(source).as_posix()


class Projects:
    def __init__(self, store: Store):
        self.store = store
        # Lessons.complete already owns Store.lock when activating the next
        # quest. One reentrant lock avoids store/project lock-order inversion.
        self.lock = store.lock

    def get(self, project_id):
        with self.lock:
            return self._get(project_id)

    def _get(self, project_id):
        project = self.store.get('projects', project_id)
        if not project:
            raise KeyError('项目不存在。')
        if self.store.get('deletion_journal', project_id):
            raise ValueError('存档恢复冲突：删除尚未完成，请重新启动以恢复。')
        quests = self.store.get('quests', project_id, [])
        return {**project, 'completed': sum(q.get('status') == 'completed' for q in quests), 'total': len(quests)}

    def list(self):
        with self.lock:
            return [self.get(p['id']) for p in self.store.all('projects') if not self.store.get('deletion_journal', p['id'])]

    def recover_deletions(self):
        from .project_deletion import recover
        with self.lock, self.store.lock:
            recover(self.store)

    def delete(self, project_id):
        from .project_deletion import delete, validate_id
        validate_id(project_id)
        with self.lock, self.store.lock:
            self.get(project_id)
            return delete(self.store, project_id)

    def import_path(self, raw: str, *, demo=False, language=None):
        with self.lock:
            return self._import_path(raw, demo=demo, language=language)

    def _import_path(self, raw: str, *, demo=False, language=None):
        from .languages import detect_language
        source = Path(raw).expanduser().resolve()
        if not source.exists():
            raise ValueError('项目路径不存在。请填写本机的完整路径。')
        if source == Path(source.anchor) or source == Path.home() or source.is_relative_to(self.store.root / 'workspaces') or self.store.root.is_relative_to(source):
            raise ValueError('请选择具体项目目录，不要导入磁盘根目录、用户主目录或本应用数据目录。')
        project_id = uuid.uuid4().hex
        workspace = self.store.root / 'workspaces' / project_id
        baseline = self.store.root / 'baselines' / project_id
        workspace.mkdir(parents=True)
        baseline.mkdir(parents=True)
        snapshot, languages = {}, set()
        for file, relative in source_files(source):
            try:
                content = file.read_text(encoding='utf-8-sig')
            except (UnicodeError, OSError):
                continue
            if '\x00' in content:
                continue
            for dest in (workspace / relative, baseline / relative):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding='utf-8', newline='')
            snapshot[relative] = digest(content)
            lang = detect_language(relative)
            if lang:
                languages.add(lang)
        if not languages:
            raise ValueError('未找到 Java、Python、JS/TS、Go 或 Bash/sh 源码。')
        project = {'id': project_id, 'name': source.stem if source.is_file() else source.name, 'source_path': str(source), 'workspace': str(workspace), 'languages': sorted(languages), 'file_count': len(snapshot), 'created_at': datetime.now(timezone.utc).isoformat(), 'stale': False, 'demo': demo, 'demo_language': language, 'snapshot': snapshot}
        self.store.put('projects', project_id, project)
        return self.get(project_id)

    def import_git(self, url: str):
        parsed = urlparse(url)
        valid_https = parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
        valid_ssh = bool(re.fullmatch(r'git@[\w.-]+:[\w./-]+', url))
        if not (valid_https or valid_ssh):
            raise ValueError('请输入 HTTPS Git 地址或 git@host:owner/repo SSH 地址，不要在地址中包含密码或令牌。')
        git = shutil.which('git')
        if not git:
            raise ValueError('本机未检测到 Git。安装系统工具需要你确认；也可以先导入本地目录。')
        dest = self.store.root / 'imports' / uuid.uuid4().hex
        dest.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_LFS_SKIP_SMUDGE': '1'}
        try:
            result = subprocess.run([git, '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.file.allow=never', 'clone', '--depth', '1', '--', url, str(dest)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120, env=env)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('仓库导入超时。请检查网络或先克隆到本地。') from exc
        if result.returncode:
            raise ValueError('Git 导入失败。请检查地址、本机 Git 凭据和网络，然后重试。')
        # Clone outside the shared lock; publish the local copy and final name
        # together so deletion cannot interleave with a stale record update.
        with self.lock:
            project = self.import_path(str(dest))
            data = self.store.get('projects', project['id'])
            data['name'] = url.rstrip('/').split('/')[-1].removesuffix('.git')
            self.store.put('projects', project['id'], data)
            return self.get(project['id'])

    def files(self, project_id):
        from .languages import analyze_file
        project = self.get(project_id)
        files = []
        for relative in project['snapshot']:
            try:
                content = safe_path(Path(project['workspace']), relative).read_text(encoding='utf-8')
                info = analyze_file(relative, content)
                files.append({**info, 'path': relative, 'language': info.get('language') or 'text'})
            except (OSError, ValueError, SyntaxError):
                files.append({'path': relative, 'language': 'text', 'size': 0, 'symbols': []})
        return files

    def read_file(self, project_id, relative):
        from .languages import detect_language
        project = self.get(project_id)
        if relative not in project['snapshot']:
            raise ValueError('此文件不在项目索引中。')
        content = safe_path(Path(project['workspace']), relative).read_text(encoding='utf-8')
        baseline = safe_path(self.store.root / 'baselines' / project_id, relative).read_text(encoding='utf-8')
        return {'path': relative, 'content': content, 'original': baseline, 'hash': digest(content), 'language': detect_language(relative) or 'text'}

    def write_file(self, project_id, relative, content, expected_hash=None):
        if len(content.encode('utf-8')) > 1024 * 1024:
            raise ValueError('单个文件不能超过 1 MB。')
        with self.lock:
            old = self.read_file(project_id, relative)
            if expected_hash is not None and expected_hash != old['hash']:
                raise ValueError('文件冲突：内容已变化，请重新打开后合并修改。')
            path = safe_path(Path(self.get(project_id)['workspace']), relative)
            path.write_text(content, encoding='utf-8', newline='')
            return {'path': relative, 'content': content, 'hash': digest(content)}

    def refresh(self, project_id):
        with self.lock:
            return self._refresh(project_id)

    def _refresh(self, project_id):
        project = self.get(project_id)
        source = Path(project['source_path'])
        current = {}
        if source.exists():
            for file, relative in source_files(source):
                try:
                    current[relative] = digest(file.read_text(encoding='utf-8-sig'))
                except (OSError, UnicodeError):
                    pass
        project['stale'] = current != project['snapshot']
        self.store.put('projects', project_id, project)
        return self.get(project_id)
