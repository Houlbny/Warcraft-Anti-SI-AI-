"""Immutable, bounded material imports and independent task practice copies."""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit
import uuid

from .languages import detect_language

MAX_FILES = 512
MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT_FILE = 2 * 1024 * 1024
MAX_PDF_FILE = 8 * 1024 * 1024
MAX_PAGES = 500
MAX_EXTRACTED_CHARS = 3_000_000
CONTEXT_CHARS = 24000
CONTEXT_BYTES = 16000
IGNORED_DIRS = {'.git', '.svn', '.hg', '.warcraft', '.venv', 'venv', 'node_modules', '__pycache__', 'target', 'dist', 'build', '.idea', '.ssh', '.aws', '.codex', '.agents', '.gradle', '.cache', '.runner'}
SECRET_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.jks', '.keystore', '.sqlite', '.sqlite3', '.db'}
BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.mp3', '.mp4', '.wav', '.zip', '.gz', '.7z', '.exe', '.dll', '.so', '.dylib', '.woff', '.woff2', '.ttf', '.pyc', '.class', '.jar'}
LANGUAGES = {'.md': 'markdown', '.txt': 'text', '.rs': 'rust', '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.hpp': 'cpp', '.cs': 'csharp', '.rb': 'ruby', '.php': 'php', '.kt': 'kotlin', '.swift': 'swift', '.lua': 'lua', '.r': 'r', '.ps1': 'powershell', '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.sql': 'sql', '.html': 'html', '.css': 'css'}


def _linked(path: Path) -> bool:
    return path.is_symlink() or (path.exists() and bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400))


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or '\x00' in value or ':' in value:
        raise ValueError('材料路径无效。')
    value = value.replace('\\', '/')
    parts = value.split('/')
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute() or any(p in {'', '.', '..'} for p in parts):
        raise ValueError('材料路径必须位于所选目录内。')
    if any(p.rstrip(' .') != p or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in parts):
        raise ValueError('材料路径含 Windows 保留文件名。')
    return '/'.join(parts)


def safe_child(root: Path, relative: str, *, exists=False) -> Path:
    relative = safe_relative(relative)
    root = root.resolve()
    candidate = root.joinpath(*relative.split('/'))
    cursor = candidate
    while cursor != root:
        if _linked(cursor):
            raise ValueError('不允许通过链接访问材料或练习目录。')
        cursor = cursor.parent
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or (exists and not resolved.is_file()):
        raise ValueError('材料路径越界或文件不存在。')
    return resolved


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('材料或任务标识无效。')
    return value


def _excluded(relative: str) -> bool:
    parts = relative.split('/')
    name = parts[-1].lower()
    return any(p.startswith('.') or p.lower() in IGNORED_DIRS for p in parts) or name.startswith(('id_rsa', 'id_ed25519')) or 'credential' in name or 'secret' in name or Path(name).suffix in SECRET_SUFFIXES


def _digest(content: str | bytes) -> str:
    return hashlib.sha256(content.encode('utf-8') if isinstance(content, str) else content).hexdigest()


def _chunks(text: str, path: str, *, page=None):
    """Keep real source line anchors even when a paragraph contains long lines."""
    buffer, start, line_number = '', 1, 1
    for line in text.splitlines(keepends=True):
        if buffer and len(buffer) + len(line) > 2400:
            yield {'path': path, 'line': start, **({'page': page} if page else {}), 'content': buffer}
            buffer, start = '', line_number
        if len(line) > 2400:
            for offset in range(0, len(line), 2400):
                yield {'path': path, 'line': line_number, **({'page': page} if page else {}), 'content': line[offset:offset + 2400]}
            start = line_number + 1
        else:
            if not buffer:
                start = line_number
            buffer += line
        line_number += 1
    if buffer.strip():
        yield {'path': path, 'line': start, **({'page': page} if page else {}), 'content': buffer}


def _json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def _split_fragment(part, max_chars, max_bytes):
    """Split only oversized fragments; every original character keeps an anchor."""
    content = part['content']
    if len(content) <= max_chars and _json_bytes([part]) <= max_bytes:
        yield copy.deepcopy(part)
        return
    offset, line = 0, part.get('line', 1)
    while offset < len(content):
        def piece(size):
            return {**part, 'id': f"{part['id']}_p{offset}_{offset + size}",
                    'line': line, 'content': content[offset:offset + size]}
        low, high = 0, min(max_chars, len(content) - offset)
        while low < high:
            middle = (low + high + 1) // 2
            if _json_bytes([piece(middle)]) <= max_bytes:
                low = middle
            else:
                high = middle - 1
        if not low:
            raise ValueError('来源位置元数据超过单批请求预算，无法构造有效片段。')
        result = piece(low)
        yield result
        line += result['content'].count('\n')
        offset += low


class SourceImports:
    def __init__(self, store):
        self.store = store
        self.root = store.root.resolve()

    def _snapshot(self, import_id):
        return safe_child(self.root, f'sources_v2/{_identifier(import_id)}')

    def get(self, import_id):
        record = self.store.get('imports_v2', _identifier(import_id))
        if record is None:
            raise KeyError('材料不存在。')
        return record

    def _selected(self, record):
        scope = record.get('scope', {})
        paths = set(scope.get('paths') or [f['path'] for f in record['files']])
        first, last = scope.get('page_start'), scope.get('page_end')
        return [part for part in record['fragments'] if part['path'] in paths and (not part.get('page') or ((first is None or part['page'] >= first) and (last is None or part['page'] <= last)))]

    def public(self, record_or_id):
        record = self.get(record_or_id) if isinstance(record_or_id, str) else record_or_id
        result = {key: copy.deepcopy(value) for key, value in record.items() if key not in {'fragments', 'root', 'analysis_work'}}
        total = sum(len(x['content']) for x in record['fragments'])
        selected = sum(len(x['content']) for x in self._selected(record))
        context_chars = sum(len(x['content']) for x in self._context(record, CONTEXT_CHARS))
        batches = self._batches(record, CONTEXT_CHARS, CONTEXT_BYTES)
        result['coverage'] = {'total_chars': total, 'selected_chars': selected, 'context_chars': context_chars,
                              'truncated': selected > context_chars, 'batch_count': len(batches),
                              'max_batch_chars': max((batch['chars'] for batch in batches), default=0)}
        result['analysis_complete'] = record.get('status') == 'analyzed' and bool(record.get('analysis_complete', len(batches) == 1))
        return result

    def selected_fragments(self, import_id):
        """All selected sources, without preview truncation; IDs are deterministic."""
        return [fragment for part in self._selected(self.get(import_id))
                for fragment in _split_fragment(part, CONTEXT_CHARS, CONTEXT_BYTES)]

    def batches(self, import_id, max_chars=CONTEXT_CHARS, max_bytes=CONTEXT_BYTES):
        if type(max_chars) is not int or max_chars < 1 or type(max_bytes) is not int or max_bytes < 1:
            raise ValueError('材料批次预算必须为正整数。')
        return self._batches(self.get(import_id), max_chars, max_bytes)

    def _batches(self, record, max_chars, max_bytes):
        result, current, chars = [], [], 0
        for part in self._selected(record):
            for fragment in _split_fragment(part, max_chars, max_bytes):
                size = len(fragment['content'])
                if current and (chars + size > max_chars or _json_bytes(current + [fragment]) > max_bytes):
                    result.append({'id': f'b{len(result) + 1}', 'sources': current, 'chars': chars})
                    current, chars = [], 0
                current.append(fragment)
                chars += size
        if current:
            result.append({'id': f'b{len(result) + 1}', 'sources': current, 'chars': chars})
        return result

    def context_for_ids(self, import_id, source_ids, max_bytes=None):
        """Resolve original evidence directly, including sources beyond a preview."""
        if not isinstance(source_ids, list) or any(not isinstance(sid, str) for sid in source_ids):
            raise ValueError('来源片段标识无效。')
        record = self.get(import_id)
        # Original IDs remain valid for previously published tasks.
        by_id = {part['id']: part for part in self._selected(record)}
        for part in list(by_id.values()):
            by_id.update({fragment['id']: fragment for fragment in _split_fragment(part, CONTEXT_CHARS, CONTEXT_BYTES)})
        result = []
        for sid in dict.fromkeys(source_ids):
            part = by_id.get(sid)
            if part is None:
                # A tighter per-request budget may split a selected fragment further.
                match = re.fullmatch(r'(s[0-9]+)_p([0-9]{1,9})_([0-9]{1,9})', sid)
                parent = by_id.get(match[1]) if match else None
                if parent is not None:
                    start, end = int(match[2]), int(match[3])
                    if 0 <= start < end <= len(parent['content']):
                        part = {**parent, 'id': sid, 'content': parent['content'][start:end],
                                'line': parent.get('line', 1) + parent['content'][:start].count('\n')}
            if part is None:
                raise ValueError('目标引用了所选范围以外或不存在的来源片段。')
            result.append(copy.deepcopy(part))
        if max_bytes is not None:
            if type(max_bytes) is not int or max_bytes < 1:
                raise ValueError('来源请求预算必须为正整数。')
            if _json_bytes(result) > max_bytes:
                raise ValueError('目标关联的原文超过单次请求预算，需要拆分学习目标；没有截断原文。')
        return result

    def context(self, import_id, max_chars=CONTEXT_CHARS):
        if type(max_chars) is not int or max_chars < 1:
            raise ValueError('材料上下文预算必须为正整数。')
        return self._context(self.get(import_id), max_chars)

    def _context(self, record, max_chars):
        selected = self._selected(record)
        # Round-robin files so a large README does not conceal all source code.
        buckets = {}
        for part in selected:
            buckets.setdefault(part['path'], []).append(part)
        ordered = []
        while any(buckets.values()):
            for parts in buckets.values():
                if parts:
                    ordered.append(parts.pop(0))
        result, remaining = [], max_chars
        for part in ordered:
            if remaining <= 0:
                break
            item = copy.deepcopy(part)
            item['content'] = item['content'][:remaining]
            # Count actual UTF-8 JSON, including escaped text and reference metadata.
            if len(json.dumps(result + [item], ensure_ascii=False).encode('utf-8')) > CONTEXT_BYTES:
                content = item['content']
                low, high = 0, len(content)
                while low < high:
                    middle = (low + high + 1) // 2
                    item['content'] = content[:middle]
                    if len(json.dumps(result + [item], ensure_ascii=False).encode('utf-8')) <= CONTEXT_BYTES:
                        low = middle
                    else:
                        high = middle - 1
                item['content'] = content[:low]
                if not low:
                    break
            if not item['content'].strip():
                continue
            result.append(item)
            remaining -= len(item['content'])
        return result

    def select_scope(self, import_id, paths, page_start=None, page_end=None):
        with self.store.lock:
            record = self.get(import_id)
            if record.get('status') == 'analyzing':
                raise ValueError('材料正在分析，请完成后再调整范围。')
            if any(a.get('import_id') == import_id for a in self.store.all('adventures_v2')):
                raise ValueError('材料已用于冒险，请重新导入以选择新的范围。')
            available = {f['path'] for f in record['files']}
            if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or p not in available for p in paths):
                raise ValueError('请选择至少一个已读取的文件。')
            for number in (page_start, page_end):
                if number is not None and (type(number) is not int or number < 1):
                    raise ValueError('PDF 页码必须为正整数。')
            if page_start is not None and page_end is not None and page_start > page_end:
                raise ValueError('起始页不能大于结束页。')
            record['scope'] = {'paths': list(dict.fromkeys(paths)), 'page_start': page_start, 'page_end': page_end}
            if not self._selected(record):
                raise ValueError('所选范围没有可读取的文字。')
            record['scope_revision'] = record.get('scope_revision', 0) + 1
            record.update(status='imported', error='')
            record.pop('analysis', None)
            for key in ('analysis_work', 'analysis_progress', 'analysis_complete'):
                record.pop(key, None)
            self.store.put('imports_v2', import_id, record)
            return record

    def read(self, import_id, path):
        record = self.get(import_id)
        path = safe_relative(path)
        file = next((f for f in record['files'] if f['path'] == path), None)
        if file is None:
            raise KeyError('材料中没有此文件。')
        folder = self._snapshot(import_id)
        target = safe_child(folder, f'extracted/{path}.txt' if file['language'] == 'pdf' else f'files/{path}', exists=True)
        content = target.read_text(encoding='utf-8')
        return {'path': path, 'content': content, 'language': file['language'], 'hash': _digest(content)}

    def workspace(self, import_id, task_id):
        _identifier(task_id)
        record = self.get(import_id)
        with self.store.lock:
            target = safe_child(self.root, f'practice_v2/{task_id}')
            if target.exists():
                if not target.is_dir():
                    raise ValueError('练习目录无效。')
                return target
            target.mkdir(parents=True)
            try:
                for file in record['files']:
                    if file['language'] == 'pdf':
                        continue
                    source = safe_child(self._snapshot(import_id), 'files/' + file['path'], exists=True)
                    dest = safe_child(target, file['path'])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, dest)
            except Exception:
                self._remove_managed(target)
                raise
            return target

    def _remove_managed(self, target):
        resolved = target.resolve()
        if _linked(target) or not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError('拒绝清理受管理目录以外的文件。')
        if target.exists():
            shutil.rmtree(target)

    def delete(self, import_id):
        with self.store.lock:
            self.get(import_id)
            if any(a.get('import_id') == import_id for a in self.store.all('adventures_v2')):
                raise ValueError('此材料仍属于一个冒险，请先删除该冒险。')
            self._remove_managed(self._snapshot(import_id))
            self.store.delete_record('imports_v2', import_id)

    def _local_files(self, raw):
        source = Path(raw).expanduser().absolute()
        if not source.exists():
            raise ValueError('材料路径不存在。')
        cursor = source
        while cursor != cursor.parent:
            if _linked(cursor):
                raise ValueError('请选择实际材料，不能导入链接目录或文件。')
            cursor = cursor.parent
        source = source.resolve()
        if source in {Path(source.anchor), Path.home().resolve()} or source.is_relative_to(self.root) or self.root.is_relative_to(source):
            raise ValueError('请选择具体材料目录，不要导入磁盘根目录、用户目录或应用存档。')
        if source.is_file():
            yield source, safe_relative(source.name)
            return
        for base, dirs, names in os.walk(source, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d.lower() not in IGNORED_DIRS and not _linked(Path(base) / d))
            for name in sorted(names):
                item = Path(base) / name
                if _linked(item) or not item.resolve().is_relative_to(source):
                    continue
                yield item, safe_relative(item.relative_to(source).as_posix())

    def _pdf(self, data, relative):
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError('PDF 已加密，请先提供可读取的版本。')
        pages = len(reader.pages)
        if pages > MAX_PAGES:
            raise ValueError(f'PDF 超过 {MAX_PAGES} 页，请拆分后导入。')
        parts, texts, diagnostics, count = [], [], [], 0
        for index, page in enumerate(reader.pages, 1):
            try:
                text = page.extract_text() or ''
            except Exception:
                diagnostics.append(f'{relative} 第 {index} 页无法提取文字，未纳入学习范围。')
                continue
            if not text.strip():
                diagnostics.append(f'{relative} 第 {index} 页没有可提取文字，可能是扫描页；此版本不执行 OCR。')
                continue
            count += len(text)
            if count > MAX_EXTRACTED_CHARS:
                raise ValueError('PDF 提取文字超过容量上限，请拆分章节。')
            parts.extend(_chunks(text, relative, page=index))
            texts.append(f'【第 {index} 页】\n{text}')
        return '\n\n'.join(texts), parts, pages, diagnostics

    def create(self, *, path=None, files=None, git_url=None, encoding='utf-8-sig'):
        if sum(value is not None for value in (path, files, git_url)) != 1:
            raise ValueError('请选择一种材料导入方式。')
        if encoding not in {'utf-8-sig', 'utf-8', 'utf-16', 'gb18030'}:
            raise ValueError('文字编码支持 UTF-8、UTF-16 或 GB18030。')
        if git_url is not None:
            return self._git(git_url, encoding)
        import_id = uuid.uuid4().hex
        snapshot = self._snapshot(import_id)
        snapshot.mkdir(parents=True)
        diagnostics, accepted, fragments, seen = [], [], [], set()
        total_bytes, extracted_chars = 0, 0
        try:
            if files is not None:
                if not isinstance(files, list) or not files or len(files) > MAX_FILES:
                    raise ValueError(f'请选择 1–{MAX_FILES} 个文件。')
                entries = []
                for entry in files:
                    if not isinstance(entry, dict) or not isinstance(entry.get('data_base64'), str):
                        raise ValueError('上传文件格式无效。')
                    relative = safe_relative(entry.get('path'))
                    if len(entry['data_base64']) > MAX_PDF_FILE * 4 // 3 + 16:
                        raise ValueError('上传文件过大。')
                    try:
                        content = base64.b64decode(entry['data_base64'], validate=True)
                    except (ValueError, binascii.Error):
                        raise ValueError('上传文件不是有效的 Base64 数据。') from None
                    total_bytes += len(content)
                    if total_bytes > MAX_BYTES:
                        raise ValueError('本次材料超过 20 MB，请分次导入。')
                    entries.append((relative, content))
                title = entries[0][0].split('/')[0] if len(entries) > 1 else Path(entries[0][0]).stem
            else:
                entries = []
                title = Path(path).stem if Path(path).is_file() else Path(path).name
                for item, relative in self._local_files(path):
                    if _excluded(relative):
                        continue
                    if len(entries) >= MAX_FILES:
                        raise ValueError(f'材料超过 {MAX_FILES} 个文件，请选择较小目录。')
                    size = item.stat().st_size
                    limit = MAX_PDF_FILE if item.suffix.lower() == '.pdf' else MAX_TEXT_FILE
                    if size > limit:
                        diagnostics.append(f'{relative} 超过单文件容量上限，未导入。')
                        continue
                    total_bytes += size
                    if total_bytes > MAX_BYTES:
                        raise ValueError('本次材料超过 20 MB，请选择较小目录。')
                    entries.append((relative, item.read_bytes()))
            for relative, data in entries:
                folded = relative.casefold()
                if folded in seen:
                    raise ValueError('材料含大小写冲突或重复文件名，请整理后导入。')
                seen.add(folded)
                if _excluded(relative):
                    diagnostics.append(f'{relative} 属于隐藏或凭据文件，未导入。')
                    continue
                suffix = Path(relative).suffix.lower()
                if suffix in BINARY_SUFFIXES:
                    diagnostics.append(f'{relative} 不是支持的文字材料，未导入。')
                    continue
                limit = MAX_PDF_FILE if suffix == '.pdf' else MAX_TEXT_FILE
                if len(data) > limit:
                    diagnostics.append(f'{relative} 超过单文件容量上限，未导入。')
                    continue
                pages = None
                try:
                    if suffix == '.pdf':
                        text, parts, pages, warnings = self._pdf(data, relative)
                        diagnostics.extend(warnings)
                        language = 'pdf'
                    else:
                        text = data.decode(encoding)
                        if '\x00' in text or sum(ord(c) < 9 or 13 < ord(c) < 32 for c in text[:4096]) > 8:
                            raise ValueError('文件包含二进制内容。')
                        parts = list(_chunks(text, relative))
                        language = detect_language(relative) or LANGUAGES.get(suffix, 'text')
                except (UnicodeError, ValueError, OSError) as exc:
                    reason = '编码无法读取，请选择正确编码' if isinstance(exc, UnicodeError) else str(exc)
                    diagnostics.append(f'{relative}：{reason}，未导入。')
                    continue
                except Exception:
                    diagnostics.append(f'{relative} 无法解析，未纳入学习范围。')
                    continue
                if not text.strip() or not parts:
                    diagnostics.append(f'{relative} 没有可读取文字，未导入。')
                    continue
                extracted_chars += len(text)
                if extracted_chars > MAX_EXTRACTED_CHARS:
                    raise ValueError('本次材料文字量过大，请选择较小章节或目录。')
                dest = safe_child(snapshot, f'files/{relative}')
                dest.parent.mkdir(parents=True, exist_ok=True)
                if suffix == '.pdf':
                    dest.write_bytes(data)
                    extracted = safe_child(snapshot, f'extracted/{relative}.txt')
                    extracted.parent.mkdir(parents=True, exist_ok=True)
                    extracted.write_text(text, encoding='utf-8', newline='')
                else:
                    dest.write_text(text, encoding='utf-8', newline='')
                for part in parts:
                    part['id'] = f's{len(fragments) + 1}'
                    fragments.append(part)
                accepted.append({'path': relative, 'language': language, 'chars': len(text), 'hash': _digest(text), **({'pages': pages} if pages is not None else {})})
            if not accepted:
                raise ValueError('未找到可读取的文字材料。' + ' '.join(diagnostics[:3]))
            record = {'id': import_id, 'title': title or '新材料', 'files': accepted, 'fragments': fragments, 'diagnostics': diagnostics, 'snapshot_hash': _digest('\n'.join(f['path'] + ':' + f['hash'] for f in accepted)), 'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'imported', 'error': '', 'scope': {'paths': [f['path'] for f in accepted]}, 'scope_revision': 0}
            self.store.put('imports_v2', import_id, record)
            return record
        except Exception:
            self._remove_managed(snapshot)
            raise

    def _git(self, url, encoding):
        if not isinstance(url, str):
            raise ValueError('Git 地址无效。')
        parsed = urlsplit(url)
        https = parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
        ssh = bool(re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+', url))
        if not (https or ssh):
            raise ValueError('请使用不含凭据的 HTTPS 或 git@host:owner/repo 地址。')
        executable = shutil.which('git')
        if not executable:
            raise ValueError('本机没有 Git，请自行安装后重试，或导入本地目录。')
        parent = safe_child(self.root, 'git_staging_v2')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent, prefix='clone-') as temporary:
            destination = Path(temporary) / 'repository'
            env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_LFS_SKIP_SMUDGE': '1'}
            try:
                result = subprocess.run([executable, '-c', 'core.hooksPath=' + str(Path(temporary) / 'no-hooks'), '-c', 'protocol.file.allow=never', 'clone', '--depth', '1', '--', url, str(destination)], capture_output=True, timeout=90, env=env)
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError('Git 下载失败或超时，请检查地址和访问权限。') from None
            if result.returncode:
                raise ValueError('Git 下载失败，请检查地址和本机访问权限。')
            # The validated clone is intentionally inside managed staging; import
            # with a temporary external view is unnecessary. Reuse browser bytes.
            entries = []
            total = 0
            for base, dirs, names in os.walk(destination, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d.lower() not in IGNORED_DIRS and not _linked(Path(base) / d)]
                for name in sorted(names):
                    file = Path(base) / name
                    relative = safe_relative(file.relative_to(destination).as_posix())
                    if _linked(file) or _excluded(relative) or file.suffix.lower() in BINARY_SUFFIXES:
                        continue
                    size = file.stat().st_size
                    if size > MAX_TEXT_FILE:
                        continue
                    total += size
                    if len(entries) >= MAX_FILES or total > MAX_BYTES:
                        raise ValueError('Git 项目超过材料容量上限，请导入相关本地子目录。')
                    entries.append({'path': relative, 'data_base64': base64.b64encode(file.read_bytes()).decode('ascii')})
            record = self.create(files=entries, encoding=encoding)
            record['title'] = url.rstrip('/').split('/')[-1].removesuffix('.git')
            self.store.put('imports_v2', record['id'], record)
            return record
