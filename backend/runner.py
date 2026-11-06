"""Local, bounded subprocess execution. This is NOT an OS security sandbox.

Preflight rejects recognizable risky operations, not arbitrary program behavior.
Programs retain the user's filesystem/network privileges; only trusted source
should be run. No installs, downloads, AI commands, or shell command strings.
"""
from __future__ import annotations

import ast
import ctypes
from ctypes import wintypes
import math
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import signal
import shlex
import subprocess
import sys
import threading
import time
import uuid

_LIMIT = 65536
_LOCK = threading.RLock()
_RUNS: dict[str, dict] = {}
# Fixed adapter, never populated with model/user command text. TypeScript itself
# resolves JSONC, extends, includes and references. Its diagnostics remain real
# failures. The derived project preserves settings and resolves input paths but
# overrides every output destination before the ordinary tsc --project stage.
_TS_PROJECT = r'''
const fs = require('fs'), path = require('path');
const [compiler, original, build, root] = process.argv.slice(1);
const ts = require(path.join(path.dirname(path.dirname(compiler)), 'lib', 'typescript.js'));
const loaded = ts.readConfigFile(original, ts.sys.readFile);
const config = ts.parseJsonConfigFileContent(loaded.config || {}, ts.sys, path.dirname(original), undefined, original);
const errors = [loaded.error, ...config.errors].filter(Boolean);
if (errors.length) {
  for (const error of errors) console.error(ts.flattenDiagnosticMessageText(error.messageText, '\n'));
  process.exit(1);
}
fs.mkdirSync(build, {recursive:true});
const derived = {
  extends: original,
  files: config.fileNames,
  include: [], exclude: [],
  compilerOptions: {
    outDir: build, rootDir: root, outFile: null,
    noEmit: false, noEmitOnError: true, emitDeclarationOnly: false,
    declaration: true, declarationDir: build, declarationMap: false,
    incremental: true, tsBuildInfoFile: path.join(build, 'project.tsbuildinfo')
  }
};
if (config.projectReferences) derived.references = config.projectReferences.map(ref => ({path:ref.path}));
fs.writeFileSync(path.join(build, 'tsconfig.run.json'), JSON.stringify(derived));
'''
_SOURCE = {'.py', '.java', '.js', '.mjs', '.cjs', '.ts', '.go', '.sh', '.bash', '.gradle', '.kts', '.xml', '.json'}
_SKIP = {'.git', '.runner', '.venv', 'venv', 'node_modules', '__pycache__'}


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def validate_patch_path(workspace: Path, relative: str) -> Path:
    """Validate existing ancestors too, so a missing leaf cannot hide an escape."""
    if not isinstance(relative, str) or not relative or '\x00' in relative:
        raise ValueError('文件路径不能为空')
    windows = PureWindowsPath(relative)
    parts = relative.replace('\\', '/').split('/')
    if Path(relative).is_absolute() or windows.drive or windows.root or ':' in relative:
        raise ValueError('仅允许练习目录内的相对路径')
    if any(p in {'', '.', '..'} or p.rstrip(' .') != p for p in parts):
        raise ValueError('路径不得包含空段、点或父目录')
    if any(p.lower() in {'.git', '.runner', '.aws', '.codex', '.agents'} for p in parts):
        raise ValueError('不可修改管理目录或运行器内部目录')
    if any(re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', p, re.I) for p in parts):
        raise ValueError('不支持设备文件名')
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('练习目录不存在')
    candidate = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            if _is_link(cursor):
                raise ValueError('练习路径不可经过符号链接或目录联接')
    try:
        candidate.resolve().relative_to(root)
    except (ValueError, OSError, RuntimeError) as exc:
        raise ValueError('文件路径超出练习目录') from exc
    return candidate


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f'缺少运行工具 {name}，请自行安装并配置 PATH 后重试；不会自动安装。')
    if Path(found).suffix.lower() in {'.cmd', '.bat', '.ps1'}:
        raise RuntimeError(f'{name} 仅找到脚本包装器，无法安全直接执行，请配置真实可执行文件。')
    return found


def _shell(source: str, default='sh') -> str:
    first = source.splitlines()[0] if source else ''
    name = 'bash' if re.search(r'\bbash\b', first) else ('sh' if first.startswith('#!') and re.search(r'\bsh\b', first) else default)
    found = shutil.which(name)
    if found and not (os.name == 'nt' and any(p in found.lower() for p in ['windowsapps', 'system32'])):
        return found
    if os.name == 'nt':
        for folder in [os.environ.get('ProgramFiles', ''), os.environ.get('LOCALAPPDATA', '')]:
            for sub in ['Git/bin', 'Git/usr/bin', 'Programs/Git/bin', 'Programs/Git/usr/bin']:
                path = Path(folder) / sub / (name + '.exe')
                if path.is_file():
                    return str(path)
    raise RuntimeError(f'缺少 {name}；Windows 可配置 Git Bash，macOS 请配置 bash/sh。不会自动安装。')


def _internal(root: Path, *parts: str) -> Path:
    cursor = root
    for part in ('.runner', *parts):
        cursor = cursor / part
        if (cursor.exists() or cursor.is_symlink()) and _is_link(cursor):
            raise ValueError('运行器目录不可为符号链接或目录联接')
    return cursor


def _build_tool(name: str) -> list[str]:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f'缺少构建工具 {name}，不会自动安装或下载 wrapper。')
    if Path(found).suffix.lower() not in {'.cmd', '.bat'}:
        return [found]
    home = Path(found).resolve().parent.parent
    if name == 'mvn':
        boot = sorted((home / 'boot').glob('plexus-classworlds-*.jar'))
        if boot:
            return [_tool('java'), f'-Dmaven.home={home}', f'-Dclassworlds.conf={home / "bin/m2.conf"}',
                    '-cp', str(boot[0]), 'org.codehaus.plexus.classworlds.launcher.Launcher']
    elif name == 'gradle' and (home / 'lib').is_dir():
        return [_tool('java'), '-cp', str(home / 'lib/*'), 'org.gradle.launcher.GradleMain']
    raise RuntimeError(f'{name} 仅找到批处理包装器且无法定位其 Java 启动文件。')


def _package_plan(root: Path, package: Path, source: str) -> list[list[str]]:
    try:
        scripts = json.loads(source).get('scripts', {})
    except (ValueError, AttributeError) as exc:
        raise ValueError('package.json 格式无效') from exc
    task = next((name for name in ['test', 'build', 'start'] if scripts.get(name)), None)
    if not task or not isinstance(scripts[task], str):
        raise ValueError('package.json 未声明 test/build/start 脚本')
    # Do not run lifecycle hooks or installs. Simple node scripts avoid npm's
    # shell entirely; other declared scripts use npm's fixed run entrypoint.
    if scripts.get('pre' + task) or scripts.get('post' + task):
        raise ValueError('构建脚本含前置/后置生命周期操作，请先人工检查并移除这些自动操作')
    script = scripts[task]
    if re.search(r'[;&|`<>\n\r]|\$\(', script):
        raise ValueError('自动运行不支持组合 shell 命令')
    words = shlex.split(script)
    if words and words[0] == 'node' and all(w == '--test' or not w.startswith('-') for w in words[1:]):
        args = []
        for word in words[1:]:
            args.append(word if word == '--test' else str(validate_patch_path(root, str(package.parent.relative_to(root) / word))))
        return [[_tool('node'), *args]]
    npm = shutil.which('npm')
    if not npm:
        raise RuntimeError('缺少 npm，不会自动安装依赖。')
    if Path(npm).suffix.lower() in {'.cmd', '.bat', '.ps1'}:
        cli = Path(npm).parent / 'node_modules/npm/bin/npm-cli.js'
        if not cli.is_file():
            raise RuntimeError('无法定位 npm-cli.js，请配置现有 npm。')
        command = [_tool('node'), str(cli)]
    else:
        command = [npm]
    return [[*command, '--prefix', str(package.parent), '--ignore-scripts',
             '--cache', str(_internal(root, 'npm-cache')), 'run', task]]


def plan_run(workspace: Path, entry: str) -> list[list[str]]:
    root = Path(workspace).resolve(strict=True)
    validate_patch_path(root, entry)
    _preflight(root)
    return _entry_plan(root, entry)


def _entry_plan(root: Path, entry: str) -> list[list[str]]:
    """Build an entry's argv without execution, filesystem writes or rescanning."""
    source_path = validate_patch_path(root, entry)
    if not source_path.is_file():
        raise ValueError('入口源文件不存在')
    source = source_path.read_text(encoding='utf-8-sig')
    ext = source_path.suffix.lower()
    build = _internal(root, 'build', uuid.uuid4().hex)
    if source_path.name == 'package.json':
        return _package_plan(root, source_path, source)
    if source_path.name == 'pom.xml':
        return [[*_build_tool('mvn'), '--batch-mode', '-f', str(source_path),
                 '-Dmaven.repo.local=' + str(_internal(root, 'maven-repository')),
                 '-Duser.home=' + str(_internal(root, 'maven-home')), 'test']]
    if source_path.name in {'build.gradle', 'build.gradle.kts'}:
        return [[*_build_tool('gradle'), '-Duser.home=' + str(_internal(root, 'home')),
                 '--no-daemon', '--project-dir', str(source_path.parent),
                 '--gradle-user-home', str(_internal(root, 'gradle-home')), 'test']]
    if ext == '.py':
        return [[sys.executable, '-u', str(source_path)]]
    if ext in {'.js', '.mjs', '.cjs'}:
        return [[_tool('node'), str(source_path)]]
    if ext == '.java':
        for directory in [source_path.parent, *source_path.parents]:
            if not directory.is_relative_to(root):
                break
            for config in ['pom.xml', 'build.gradle', 'build.gradle.kts']:
                if (directory / config).is_file():
                    return _entry_plan(root, str((directory / config).relative_to(root)))
        package = re.search(r'^\s*package\s+([\w.]+)\s*;', source, re.M)
        classname = (package.group(1) + '.' if package else '') + source_path.stem
        source_root = source_path.parent
        if package:
            for segment in reversed(package.group(1).split('.')):
                if source_root.name != segment:
                    source_root = root
                    break
                source_root = source_root.parent
        return [[_tool('javac'), '-d', str(build), '-sourcepath', str(source_root), str(source_path)],
                [_tool('java'), '-cp', str(build), classname]]
    if ext == '.ts':
        node = _tool('node')
        candidates = [root / 'node_modules/typescript/bin/tsc']
        global_tsc = shutil.which('tsc')
        if global_tsc:
            resolved = Path(global_tsc).resolve()
            candidates.extend([resolved, resolved.parent / 'node_modules/typescript/bin/tsc',
                               resolved.parent.parent / 'lib/node_modules/typescript/bin/tsc'])
        # The application's local frontend install provides an offline compiler
        # for copied projects, whose node_modules are deliberately not imported.
        # Existing project/global tools keep priority over this bundled fallback.
        candidates.append(Path(__file__).resolve().parents[1] / 'frontend/node_modules/typescript/bin/tsc')
        compiler = next((p for p in candidates if p.is_file() and p.suffix.lower() not in {'.cmd', '.bat', '.ps1'}), None)
        if not compiler:
            raise RuntimeError('缺少 TypeScript 编译器 tsc；请配置项目本地或全局 TypeScript。不会下载依赖。')
        output = build / source_path.relative_to(root).with_suffix('.js')
        config = None
        for directory in [source_path.parent, *source_path.parents]:
            if not directory.is_relative_to(root):
                break
            candidate = directory / 'tsconfig.json'
            if candidate.exists():
                config = validate_patch_path(root, str(candidate.relative_to(root)))
                break
        if config:
            # Preserve the project's actual compiler settings (including errors
            # repaired by the learner) while keeping every emitted artifact local.
            # JSON null clears inherited outFile (tsc has no CLI equivalent).
            # TypeScript's API computes the exact project's effective file list.
            return [[node, '-e', _TS_PROJECT, str(compiler), str(config), str(build), str(root)],
                    [node, str(compiler), '--project', str(build / 'tsconfig.run.json'),
                     '--outDir', str(build), '--rootDir', str(root), '--noEmitOnError', 'true'],
                    [node, str(output)]]
        return [[node, str(compiler), str(source_path), '--outDir', str(build), '--rootDir', str(root),
                 '--target', 'ES2020', '--module', 'commonjs', '--skipLibCheck',
                 '--noEmitOnError', 'true'], [node, str(output)]]
    if ext == '.go':
        siblings = sorted(source_path.parent.glob('*.go'))
        paths = [str(validate_patch_path(root, str(p.relative_to(root)))) for p in siblings
                 if not p.name.endswith('_test.go') or source_path.name.endswith('_test.go')]
        return [[_tool('go'), 'test' if source_path.name.endswith('_test.go') else 'run', *paths]]
    if ext in {'.sh', '.bash'}:
        return [[_shell(source, 'bash' if ext == '.bash' else 'sh'), str(source_path)]]
    raise ValueError('不支持该入口；请选择 Java/Python/JavaScript/TypeScript/Go/sh 源文件')


def _entry_tool(root: Path, path: Path) -> str | None:
    names = {'package.json': 'node/npm', 'pom.xml': 'mvn',
             'build.gradle': 'gradle', 'build.gradle.kts': 'gradle'}
    if path.name in names:
        return names[path.name]
    if path.suffix.lower() == '.java':
        for directory in [path.parent, *path.parents]:
            if not directory.is_relative_to(root):
                break
            for config in ('pom.xml', 'build.gradle', 'build.gradle.kts'):
                if (directory / config).is_file():
                    return names[config]
        return 'javac+java'
    if path.suffix.lower() in {'.sh', '.bash'}:
        first = path.read_text(encoding='utf-8-sig').splitlines()
        line = first[0] if first else ''
        return 'bash' if re.search(r'\bbash\b', line) else (
            'sh' if line.startswith('#!') and re.search(r'\bsh\b', line)
            else 'bash' if path.suffix.lower() == '.bash' else 'sh')
    return {'.py': 'python', '.js': 'node', '.mjs': 'node', '.cjs': 'node',
            '.ts': 'tsc+node', '.go': 'go'}.get(path.suffix.lower())


def _launcher(root: Path, entry: str, commands: list[list[str]], tool: str) -> dict:
    """Describe the existing plan; display templates never become execution input."""
    path = validate_patch_path(root, entry)
    result = {'mode': '', 'cwd': 'practice_root', 'shell': False,
              'entry': path.relative_to(root).as_posix(), 'command': None}
    # Build commands contain local tool installations, inline runner code and
    # random internal paths. Their stable mode is truthful; an invented shell
    # command would not be. None of these descriptions changes the actual argv.
    if tool in {'mvn', 'gradle'}:
        result['mode'] = 'maven_test' if tool == 'mvn' else 'gradle_test'
        return result
    if tool == 'javac+java':
        result['mode'] = 'java_compile_run'
        return result
    if tool == 'tsc+node':
        result['mode'] = ('typescript_project_compile_run' if len(commands) == 3
                          and '--project' in commands[1] else 'typescript_compile_run')
        return result
    command = commands[0]

    def relative(argument):
        return Path(argument).resolve().relative_to(root).as_posix()

    if tool == 'python':
        result.update(mode='python_file', command=['python', *command[1:-1], relative(command[-1])],
            automatic_test_discovery=False,
            python_import_path={'first': 'entry_parent', 'adds_project_root': False,
                                'inherits_pythonpath': False})
    elif tool == 'node/npm' and '--prefix' in command and '--ignore-scripts' in command:
        result.update(mode='npm_script', script=command[-1])
    elif tool in {'node', 'node/npm'}:
        result['mode'] = 'node_test' if '--test' in command[1:] else 'node_file' if len(command) > 1 else 'node_stdin'
        result['command'] = ['node', *[argument if argument == '--test' else relative(argument)
                                     for argument in command[1:]]]
    elif tool == 'go':
        result['mode'] = 'go_test_files' if command[1] == 'test' else 'go_run_files'
        result['command'] = ['go', command[1], *[relative(argument) for argument in command[2:]]]
    else:
        result.update(mode='shell_file', command=[tool, relative(command[-1])])
    return result


def execution_capabilities(root: Path, entries: list[str]) -> dict:
    """Read-only preflight/tool discovery; availability is never runtime proof."""
    if not isinstance(entries, list) or len(entries) > 24 or any(not isinstance(entry, str) for entry in entries):
        raise ValueError('运行能力查询需要至多24个入口路径。')
    result = {'preflight': {'status': 'passed', 'reason': ''},
              'entries': [], 'execution_verified': False}
    try:
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('练习目录不存在')
        _preflight(root)
    except (OSError, ValueError) as error:
        result['preflight'] = {'status': 'blocked', 'reason': str(error)}
    for entry in dict.fromkeys(entries):
        item = {'path': entry, 'supported': False, 'tool': None,
                'tool_available': False, 'reason': '', 'launcher': None}
        try:
            path = validate_patch_path(root, entry)
            if not path.is_file():
                raise ValueError('入口源文件不存在')
            item['tool'] = _entry_tool(root, path)
            if item['tool'] is None:
                raise ValueError('不支持该入口类型')
            item['supported'] = True
            commands = _entry_plan(root, entry)
            item['launcher'] = _launcher(root, entry, commands, item['tool'])
            item['tool_available'] = True
        except (OSError, ValueError, RuntimeError) as error:
            item['reason'] = str(error)
        result['entries'].append(item)
    return result


def _python_write_risk(source: str, write_operation: re.Pattern) -> bool | None:
    """Associate recognizable writes with their own receiver/arguments.

    Bindings are scoped lexically and conservatively include every rebinding.
    Unknown receivers are never assumed to be containers; only unambiguous
    literal/container bindings may make append/remove/copy/replace benign.
    Unparseable source retains the existing conservative textual fallback.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    nodes = list(ast.walk(tree))
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    scope_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
                   ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    comprehension_types = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    outer = {}
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for value in [*node.args.defaults, *node.args.kw_defaults,
                          *getattr(node, 'decorator_list', []), getattr(node, 'returns', None)]:
                if value is not None:
                    outer[value] = node
        elif isinstance(node, ast.ClassDef):
            for value in [*node.bases, *node.keywords, *node.decorator_list]:
                outer[value] = node
        elif isinstance(node, comprehension_types) and node.generators:
            outer[node.generators[0].iter] = node
    scope_cache = {}

    def scope_of(node):
        if node not in scope_cache:
            parent = parents.get(node)
            scope_cache[node] = (scope_of(outer[node]) if node in outer else parent
                if isinstance(parent, scope_types) else scope_of(parent) if parent is not None else tree)
        return scope_cache[node]

    scoped_bindings, scoped_imports = {}, {}

    def bind(scope, target, value):
        if isinstance(target, ast.Name):
            scoped_bindings.setdefault(scope, {}).setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                bind(scope, child, None)

    for node in nodes:
        scope = scope_of(node)
        bindings = scoped_bindings.setdefault(scope, {})
        imports = scoped_imports.setdefault(scope, {})
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            if isinstance(node, ast.NamedExpr):
                while isinstance(scope, comprehension_types):
                    scope = scope_of(scope)
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                bind(scope, target, node.value)
        elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.comprehension)):
            bind(scope, node.target, None)
        elif isinstance(node, ast.withitem) and node.optional_vars:
            bind(scope, node.optional_vars, node.context_expr)
        elif isinstance(node, ast.arg):
            bindings.setdefault(node.arg, []).append(None)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.setdefault(node.name, []).append(None)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bindings.setdefault(node.name, []).append(None)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split('.')[0]
                imports[name] = alias.name
                bindings.setdefault(name, []).append(None)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                name = alias.asname or alias.name
                imports[name] = node.module + '.' + alias.name
                bindings.setdefault(name, []).append(None)
    # Global/nonlocal mutation prevents a binding from proving a safe type.
    for node in nodes:
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                for bindings in scoped_bindings.values():
                    if name in bindings:
                        bindings[name].append(None)

    def lookup(node, name):
        scope = scope_of(node)
        while True:
            if name in scoped_bindings.get(scope, {}) or scope is tree:
                return scope, scoped_bindings.get(scope, {}).get(name, [])
            scope = scope_of(scope)
            # A method does not close over its class namespace.
            while isinstance(scope, ast.ClassDef):
                scope = scope_of(scope)

    def qualified(node):
        if isinstance(node, ast.Name):
            scope, _ = lookup(node, node.id)
            return scoped_imports.get(scope, {}).get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return qualified(node.value) + '.' + node.attr
        return ''

    def kind(node, seen=frozenset()):
        if isinstance(node, (ast.List, ast.ListComp)):
            return 'list'
        if isinstance(node, (ast.Dict, ast.DictComp)):
            return 'dict'
        if isinstance(node, (ast.Set, ast.SetComp)):
            return 'set'
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return 'str'
        if isinstance(node, ast.Name) and len(seen) < 8:
            scope, values = lookup(node, node.id)
            key = (scope, node.id)
            if key not in seen:
                types = {kind(value, seen | {key}) for value in values}
                return types.pop() if len(types) == 1 else None
        if isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Name) and node.func.id in {'list', 'dict', 'set', 'str'}
                    and not lookup(node.func, node.func.id)[1]):
                return node.func.id
            if isinstance(node.func, ast.Attribute):
                receiver = kind(node.func.value, seen)
                if receiver in {'list', 'dict', 'set'} and node.func.attr == 'copy':
                    return receiver
        return None

    def external(node, seen=frozenset()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return bool(re.match(r'^(?:/|[a-z]:[\\/]|\\|\.\.[\\/])', node.value, re.I))
        if isinstance(node, ast.Name):
            scope, values = lookup(node, node.id)
            key = (scope, node.id)
            if key in seen:
                # Other bindings of this name are already traversed. A cycle
                # alone does not connect an unrelated route to this operation.
                return False
            if len(seen) >= 8:
                # Do not let a long unresolved path alias bypass the check.
                return True
            return any(external(value, seen | {key}) for value in values if value is not None)
        return isinstance(node, ast.AST) and any(external(child, seen) for child in ast.iter_child_nodes(node))

    safe = {'list': {'append', 'remove', 'copy'}, 'dict': {'copy'},
            'set': {'remove', 'copy'}, 'str': {'replace'}}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = qualified(node.func)
        if name.startswith('subprocess.') or name == 'os.system':
            return True
        operation = node.func.attr if isinstance(node.func, ast.Attribute) else name.rsplit('.', 1)[-1]
        if not write_operation.search(operation + '('):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in safe.get(kind(node.func.value), set()):
            continue
        if external(node):
            return True
    return False


def _javascript_dom_operations(source: str, external_literal: re.Pattern) -> str:
    """Exclude narrow DOM operations from the legacy file-write name scan.

    Only fixed class tokens or appending a visibly constructed DOM node qualify.
    Unknown arguments, external-path bindings and dynamic method replacement
    retain the conservative scan. This is not a JavaScript semantic resolver.
    """
    from .languages import _mask
    masked = _mask(source)
    if re.search(r'\b(?:Proxy|Reflect)\b|\bObject\s*\.\s*(?:assign|definePropert\w*)\s*\(|'
                 r'\.\s*(?:classList|remove|appendChild)\s*=(?!=)', masked):
        return source

    def end_expression(start):
        depth = 0
        for offset in range(start, len(masked)):
            char = masked[offset]
            if char in '([{':
                depth += 1
            elif char in ')]}':
                if depth == 0:
                    return offset
                depth -= 1
            elif depth == 0 and char in ';\r\n':
                return offset
        return len(masked)

    bindings = {}
    for match in re.finditer(r'(?<![\w.$])([A-Za-z_$][\w$]*)\s*=(?!=|>)', masked):
        finish = end_expression(match.end())
        bindings.setdefault(match[1], []).append(source[match.end():finish].strip())

    def external(expression, seen=frozenset()):
        if external_literal.search(expression):
            return True
        # Property names and object keys are not references to same-named
        # variables elsewhere in the file (e.g. els.root versus const root).
        for name in re.findall(r'(?<![\w.$])([A-Za-z_$][\w$]*)(?![\w$]|\s*:)', _mask(expression)):
            if name in seen:
                continue
            if len(seen) >= 8:
                return True
            if any(external(value, seen | {name}) for value in bindings.get(name, [])):
                return True
        return False

    def created_node(expression):
        if re.fullmatch(r'document\s*\.\s*createElement\s*\(\s*([\"\'])[\w-]+\1\s*\)', expression):
            return True
        if re.fullmatch(r'[A-Za-z_$][\w$]*', expression):
            values = bindings.get(expression, [])
            return bool(values) and all(re.fullmatch(
                r'document\s*\.\s*createElement\s*\(\s*([\"\'])[\w-]+\1\s*\)', value) for value in values)
        return False

    result = list(source)
    calls = re.finditer(r'(?P<receiver>\b[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)'
                        r'\s*\.\s*(?P<method>remove|appendChild)\s*\(', masked)
    for match in calls:
        receiver = re.sub(r'\s+', '', match['receiver'])
        if match['method'] == 'remove' and not receiver.endswith('.classList'):
            continue
        root_name = receiver.split('.')[0]
        if len(bindings.get(root_name, [])) > 1:
            continue
        finish = end_expression(match.end())
        if finish >= len(masked) or masked[finish] != ')':
            continue
        arguments = source[match.end():finish].strip()
        if external(receiver) or external(arguments):
            continue
        if match['method'] == 'remove':
            # Dynamic class names may hide a path argument; retain the old guard.
            token = r'(?:"[\w-]+"|\'[\w-]+\')'
            if not re.fullmatch(token + r'(?:\s*,\s*' + token + r')*', arguments):
                continue
        elif not created_node(arguments):
            continue
        begin, end = match.span('method')
        result[begin:end] = ' ' * (end - begin)
    return ''.join(result)


def _preflight(root: Path) -> None:
    # Conservative recognizable patterns only. Dynamic/computed behavior cannot
    # be secured by a static scan; the UI must expose this local execution limit.
    forbidden = re.compile(
        r'\b(?:sudo|su\s+-|doas|chmod|chown|mkfs|diskpart|reg\s+(?:add|delete)|'
        r'format\s+[a-z]:|shutdown|reboot|Remove-Item|Invoke-Expression|'
        r'curl|wget|Invoke-WebRequest|Start-Process)\b|'
        r'\b(?:pip\d*|npm|pnpm|yarn|brew|apt(?:-get)?|yum|winget|choco)\s+(?:install|add|update|upgrade)|'
        r'\bgo\s+(?:install|get)\b|\b(?:rm|rmdir|del)\s+|'
        r'\b(?:shutil\.rmtree|os\.(?:remove|unlink|rename|replace))\s*\(|'
        r'\b(?:os\.system|subprocess\.|child_process|Runtime\.getRuntime|ProcessBuilder|exec\.Command)|'
        r'\b(?:eval|exec)\s*\(|(?:--global|--user|--prefix)\b', re.I)
    absolute_write = re.compile(
        r'(?:open|writeFile(?:Sync)?|createWriteStream|WriteAllText|FileOutputStream|WriteFile)\s*\(\s*[rbuf]*[\"\'](?:/|[a-z]:[\\/]|\\\\)|'
        r'(?:>|>>|Set-Content|Out-File)\s*[\"\']?(?:/|[a-z]:[\\/])', re.I)
    write_operation = re.compile(r'\b(?:write\w*|append\w*|mkdir\w*|touch|unlink|rename|replace|remove|rmtree|copy\w*|move|open)\s*\(', re.I)
    external_literal = re.compile(r'[\"\'](?:/|[a-z]:[\\/]|\\\\|\.\.[\\/])', re.I)
    count = 0
    for base, directories, files in os.walk(root, followlinks=False):
        for name in list(directories):
            path = Path(base) / name
            if name in _SKIP:
                directories.remove(name)
            elif _is_link(path):
                raise ValueError('源目录包含符号链接或目录联接，拒绝自动运行')
        for name in files:
            path = Path(base) / name
            if path.suffix.lower() not in _SOURCE:
                continue
            count += 1
            if count > 3000 or path.stat().st_size > 1024 * 1024:
                raise ValueError('项目源文件过多或过大，无法完成本地运行预检')
            if _is_link(path):
                raise ValueError('源文件包含符号链接，拒绝自动运行')
            source = path.read_text(encoding='utf-8-sig')
            write_risk = _python_write_risk(source, write_operation) if path.suffix.lower() == '.py' else None
            if write_risk is None:
                javascript = path.suffix.lower() in {'.js', '.mjs', '.cjs', '.ts'}
                write_source = _javascript_dom_operations(source, external_literal) if javascript else source
                absolute_risk = bool(absolute_write.search(source))
                if javascript:
                    from .languages import _mask
                    masked = _mask(source)
                    # HTML text such as <span>/path is not shell redirection.
                    # A real fs call starts in code, even when its path is quoted.
                    absolute_risk = any(not masked[match.start()].isspace()
                                        for match in absolute_write.finditer(source))
                write_risk = bool(absolute_risk
                    or (write_operation.search(write_source) and external_literal.search(source)))
            if forbidden.search(source) or write_risk:
                raise ValueError(f'检测到潜在危险本地操作：{path.relative_to(root)}。自动运行已阻止；静态预检并非完整安全沙箱。')


def _environment(root: Path, token: str) -> dict[str, str]:
    runroot = _internal(root, 'runs', token)
    for name in ['home', 'cache', 'tmp', 'gocache', 'gomodcache']:
        (runroot / name).mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
    env.update(HOME=str(runroot / 'home'), USERPROFILE=str(runroot / 'home'),
               APPDATA=str(runroot / 'home'), LOCALAPPDATA=str(runroot / 'cache'),
               XDG_CACHE_HOME=str(runroot / 'cache'), XDG_CONFIG_HOME=str(runroot / 'home'),
               TMPDIR=str(runroot / 'tmp'), TMP=str(runroot / 'tmp'), TEMP=str(runroot / 'tmp'),
               GOCACHE=str(runroot / 'gocache'), GOMODCACHE=str(runroot / 'gomodcache'),
               GOPATH=str(runroot / 'go'), GOENV='off', GOPROXY='https://proxy.golang.org,direct', GOSUMDB='sum.golang.org',
               GOTOOLCHAIN='local', GOTELEMETRY='off', PYTHONNOUSERSITE='1',
               PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
               npm_config_cache=str(runroot / 'cache'), npm_config_update_notifier='false',
               LANG='en_US.UTF-8', LC_ALL='en_US.UTF-8')
    return env


class _WindowsJob:
    """Kill-on-close Job Object; suspended spawn avoids untracked children."""
    def __init__(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION is 144 bytes on 64-bit Windows,
        # 112 on 32-bit; LimitFlags sits at offset 16 on both architectures.
        info = ctypes.create_string_buffer(144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112)
        ctypes.c_uint32.from_buffer(info, 16).value = 0x2000
        if not self.handle or not kernel.SetInformationJobObject(self.handle, 9, info, len(info)):
            self.close()
            raise ValueError('系统无法创建受控进程组，已阻止本地运行')

    def attach(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            process.kill()
            process.wait()
            raise ValueError('系统无法跟踪子进程树，已阻止本地运行')
        resume = ctypes.WinDLL('ntdll').NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        if resume(wintypes.HANDLE(int(process._handle))) != 0:
            self.kill()
            raise ValueError('无法恢复受控进程')

    def kill(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _kill(state):
    if state.get('job'):
        state['job'].kill()
    process = state.get('process')
    if process is not None:
        if os.name == 'nt':
            if not state.get('job') and process.poll() is None:
                taskkill = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/taskkill.exe')
                subprocess.run([taskkill, '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, shell=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def stop_run(run_key: str) -> bool:
    with _LOCK:
        state = _RUNS.get(run_key)
        if state is None:
            return False
        state['cancel'].set()
        _kill(state)
        return True


def run_project(workspace: Path, entry: str, *, run_key: str, timeout: float = 30) -> dict:
    start = time.monotonic()
    result = dict(status='blocked', command=[], exit_code=None, stdout='', stderr='', duration_ms=0)
    state = {'cancel': threading.Event(), 'process': None, 'job': None}
    chunks = {'stdout': bytearray(), 'stderr': bytearray()}
    truncated = set()
    registered = False

    def drain(pipe, key):
        try:
            while True:
                data = pipe.read(8192)
                if not data:
                    break
                remaining = _LIMIT - len(chunks[key])
                chunks[key].extend(data[:max(0, remaining)])
                if len(data) > remaining:
                    truncated.add(key)
        finally:
            pipe.close()

    try:
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
            raise ValueError('运行超时必须在 0 到 300 秒之间')
        root = Path(workspace).resolve(strict=True)
        commands = plan_run(root, entry)
        with _LOCK:
            if run_key in _RUNS:
                raise ValueError('该项目已有运行任务')
            _RUNS[run_key] = state
            registered = True
        env = _environment(root, uuid.uuid4().hex)
        _internal(root, 'build').mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            state['job'] = _WindowsJob()
        for command in commands:
            result['command'] = command
            with _LOCK:
                if state['cancel'].is_set():
                    result['status'] = 'cancelled'
                    break
                if time.monotonic() - start >= timeout:
                    result['status'] = 'timeout'
                    break
                process = subprocess.Popen(command, cwd=root, env=env, shell=False,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=(os.name != 'nt'),
                    creationflags=(0x4 | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0)
                state['process'] = process
                if state['job']:
                    state['job'].attach(process)
            readers = [threading.Thread(target=drain, args=(getattr(process, key), key), daemon=True)
                       for key in ['stdout', 'stderr']]
            for reader in readers:
                reader.start()
            while process.poll() is None:
                if state['cancel'].is_set():
                    result['status'] = 'cancelled'
                    break
                if time.monotonic() - start >= timeout:
                    result['status'] = 'timeout'
                    break
                state['cancel'].wait(.02)
            _kill(state)  # Also removes children surviving an exited main process.
            process.wait(timeout=5)
            for reader in readers:
                reader.join(5)
            result['exit_code'] = process.returncode
            if state['cancel'].is_set():
                result['status'] = 'cancelled'
            elif result['status'] != 'timeout':
                result['status'] = 'passed' if process.returncode == 0 else 'failed'
            if result['status'] != 'passed':
                break
    except RuntimeError as exc:
        result.update(status='missing_tool', stderr=str(exc))
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        result.update(status='blocked' if result['exit_code'] is None else 'failed', stderr=str(exc))
    finally:
        with _LOCK:
            _kill(state)
            if state['job']:
                state['job'].close()
            if registered:
                _RUNS.pop(run_key, None)
        for key in ['stdout', 'stderr']:
            decoded = bytes(chunks[key]).decode('utf-8', errors='replace').replace('\r\n', '\n')
            result[key] = decoded + result[key]
            if key in truncated:
                result[key] += '\n[输出已截断]\n'
        result['duration_ms'] = round((time.monotonic() - start) * 1000)
    return result
