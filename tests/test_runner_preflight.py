"""Syntax-aware Python preflight and non-executing runtime capability reports."""
import hashlib
from pathlib import Path
import socket

import pytest

from backend import runner


def source(root, content, name='main.py'):
    path = root / name
    path.write_text(content, encoding='utf-8')
    return path


@pytest.mark.parametrize('code', [
    "route = '/health'\nitems = []\nitems.append({'status': 'ok'})\n",
    "routes = []\nroutes.append('/health')\n",
    "def route():\n    values: list[str] = []\n    values.append('/health')\n    return values\n",
    "routes = {}\nroute = '/health'\nroutes.setdefault('clients', []).append('client')\n",
    "from pathlib import Path\nroute = '/health'\nPath('local.txt').write_text('local')\n",
    "route = '/health'\nwith open('local.txt', 'w') as output:\n    output.write('local')\n",
    "route = '/health'\ndef caller():\n    targets, skipped = resolve()\ndef resolve():\n    targets = []\n    targets.append('/health')\n    return targets, {}\n",
    "route = '/health'\ndef adjust(now):\n    now = now.replace(tzinfo=None)\n    return now\n",
])
def test_python_routes_do_not_turn_unrelated_calls_into_external_writes(tmp_path, code):
    source(tmp_path, code)
    runner._preflight(tmp_path)


@pytest.mark.parametrize('code', [
    "open('/tmp/outside', 'w').write('bad')",
    "from pathlib import Path\nPath('../outside').write_text('bad')",
    "from pathlib import Path\ntarget = '/tmp/outside'\nPath(target).write_text('bad')",
    "from pathlib import Path\noutput = Path('/tmp/outside')\noutput.write_text('bad')",
    "sink.append('/tmp/outside')",
    "sink = UnknownWriter('/tmp/outside')\nsink.append('bad')",
    "items = {'sink': UnknownWriter('/tmp/outside')}\nitems.setdefault('sink', []).append('bad')",
    "items = []\nitems = UnknownWriter()\nitems.append('/tmp/outside')",
    "def save(items):\n    items.append('/tmp/outside')\n",
    "items = UnknownWriter()\ndef save():\n    items = []\nitems.append('/tmp/outside')\n",
    "items = UnknownWriter()\ndef save(value=items.append('/tmp/outside')):\n    items = []\n",
    "import os\nos.system('echo bad')",
    "import subprocess\nsubprocess.run(['echo', 'bad'])",
    "from subprocess import run as launch\nlaunch(['echo', 'bad'])",
])
def test_python_external_writes_unknown_receivers_and_process_calls_stay_blocked(tmp_path, code):
    source(tmp_path, code)
    with pytest.raises(ValueError, match='潜在危险'):
        runner._preflight(tmp_path)


@pytest.mark.parametrize(('name', 'code'), [
    ('main.js', "require('fs').writeFileSync('/tmp/outside', 'bad');"),
    ('main.ts', "import fs from 'fs'; fs.writeFileSync('/tmp/outside', 'bad');"),
    ('Main.java', 'class Main { void run() { new FileOutputStream("/tmp/outside"); } }'),
    ('main.go', 'package main\nfunc main() { os.WriteFile("/tmp/outside", data, 0600) }'),
    ('main.sh', '#!/bin/sh\nprintf bad > /tmp/outside\n'),
])
def test_other_language_external_write_guards_stay_active(tmp_path, name, code):
    source(tmp_path, code, name)
    with pytest.raises(ValueError, match='潜在危险'):
        runner._preflight(tmp_path)


@pytest.mark.parametrize('extension', ['.js', '.ts'])
def test_dom_class_updates_and_appending_created_nodes_do_not_write_files(tmp_path, extension):
    source(tmp_path, '''const route = "/api/health";
const root = "/tmp/unrelated-value";
const label = "<span>/path</span>";
const els = {root: document.querySelector("#root")};
function loading(button) { button.classList.remove("loading", "active"); }
const row = document.createElement("div");
els.root.appendChild(row);
els.root.classList.remove("hidden");
''', 'main' + extension)
    runner._preflight(tmp_path)


@pytest.mark.parametrize('code', [
    "const path = '/tmp/outside'; unknown.remove(path);",
    "const path = '/tmp/outside'; unknown.appendChild(path);",
    "unknown.classList.remove('/tmp/outside');",
    "const outside = '/tmp/outside'; unknown.classList.remove(outside);",
    "const sink = new UnknownWriter('/tmp/outside'); sink.classList.remove('entry');",
    "const route='/api'; let element=document.createElement('div'); element=unknown; element.classList.remove('entry');",
    "const route='/api'; const element=document.createElement('div'); element.classList.remove=unknown; element.classList.remove('entry');",
    "const route='/api'; function append(parent, child) { parent.appendChild(child); }",
    "const route='/api'; el.classList.remove('hidden'); fs.writeFileSync('/tmp/outside', 'bad');",
    "const route='/api'; el.classList.remove('hidden'); require('child_process').exec('bad');",
])
def test_dom_exceptions_keep_unknown_paths_rebindings_and_real_io_blocked(tmp_path, code):
    source(tmp_path, code, 'main.js')
    with pytest.raises(ValueError, match='潜在危险'):
        runner._preflight(tmp_path)


def test_invalid_python_keeps_conservative_external_write_detection(tmp_path):
    source(tmp_path, "route = '/health'\nwriter.append(value)\ndef broken(\n")
    with pytest.raises(ValueError, match='潜在危险'):
        runner._preflight(tmp_path)


def test_long_external_path_alias_chain_does_not_bypass_preflight(tmp_path):
    aliases = '\n'.join(f'p{index} = p{index - 1}' for index in range(1, 14))
    source(tmp_path, "from pathlib import Path\np0 = '/tmp/outside'\n" + aliases
           + "\nPath(p13).write_text('bad')\n")
    with pytest.raises(ValueError, match='潜在危险'):
        runner._preflight(tmp_path)


def snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def test_capabilities_report_missing_tools_without_running_or_creating_anything(tmp_path, monkeypatch):
    source(tmp_path, "print('ok')\n")
    source(tmp_path, "console.log('ok');\n", 'main.js')
    source(tmp_path, 'text\n', 'notes.txt')
    before = snapshot(tmp_path)
    monkeypatch.setattr(runner.shutil, 'which', lambda name: None)

    def forbidden(*args, **kwargs):
        raise AssertionError('Capability reporting must not execute, write, install, or use the network.')

    monkeypatch.setattr(runner.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(runner.subprocess, 'run', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    result = runner.execution_capabilities(tmp_path, ['main.py', 'main.js', 'notes.txt'])
    assert result['preflight'] == {'status': 'passed', 'reason': ''}
    assert result['execution_verified'] is False
    entries = {entry['path']: entry for entry in result['entries']}
    assert {key: value for key, value in entries['main.py'].items() if key != 'launcher'} == {
        'path': 'main.py', 'supported': True, 'tool': 'python', 'tool_available': True, 'reason': ''}
    assert {key: entries['main.py']['launcher'][key] for key in ('mode', 'entry', 'cwd')} == {
        'mode': 'python_file', 'entry': 'main.py', 'cwd': 'practice_root'}
    assert entries['main.js']['supported'] is True
    assert entries['main.js']['tool'] == 'node'
    assert entries['main.js']['tool_available'] is False
    assert 'node' in entries['main.js']['reason']
    assert entries['notes.txt']['supported'] is False
    assert entries['notes.txt']['tool_available'] is False
    assert snapshot(tmp_path) == before
    assert not (tmp_path / '.runner').exists()


def test_capabilities_distinguish_project_block_from_installed_python(tmp_path):
    source(tmp_path, "print('ok')\n")
    source(tmp_path, "import os\nos.system('echo blocked')\n", 'other.py')
    result = runner.execution_capabilities(tmp_path, ['main.py'])
    assert result['preflight']['status'] == 'blocked'
    assert 'other.py' in result['preflight']['reason']
    assert result['entries'][0]['supported'] is True
    assert result['entries'][0]['tool_available'] is True
    assert result['execution_verified'] is False


@pytest.mark.parametrize(('entry', 'code', 'tool'), [
    ('Main.java', 'class Main {}', 'javac+java'),
    ('main.js', 'console.log(1)', 'node'),
    ('main.ts', 'console.log(1)', 'tsc+node'),
    ('main.go', 'package main\nfunc main() {}', 'go'),
    ('main.sh', '#!/bin/sh\ntrue\n', 'sh'),
    ('main.bash', '#!/bin/bash\ntrue\n', 'bash'),
    ('pom.xml', '<project/>', 'mvn'),
    ('build.gradle', 'plugins { id "java" }', 'gradle'),
])
def test_capabilities_cover_supported_local_language_tools(tmp_path, monkeypatch, entry, code, tool):
    source(tmp_path, code, entry)
    monkeypatch.setattr(runner.shutil, 'which', lambda name: '/tools/' + name)
    if entry.endswith('.ts'):
        compiler = tmp_path / 'node_modules/typescript/bin/tsc'
        compiler.parent.mkdir(parents=True)
        compiler.write_text('// compiler placeholder; must never execute', encoding='utf-8')
    result = runner.execution_capabilities(tmp_path, [entry])
    assert result['preflight']['status'] == 'passed'
    assert [{key: value for key, value in item.items() if key != 'launcher'} for item in result['entries']] == [
        {'path': entry, 'supported': True, 'tool': tool, 'tool_available': True, 'reason': ''}]
    mode = {'javac+java': 'java_compile_run', 'node': 'node_file', 'tsc+node': 'typescript_compile_run',
            'go': 'go_run_files', 'sh': 'shell_file', 'bash': 'shell_file',
            'mvn': 'maven_test', 'gradle': 'gradle_test'}[tool]
    assert {key: result['entries'][0]['launcher'][key] for key in ('mode', 'entry', 'cwd')} == {
        'mode': mode, 'entry': entry, 'cwd': 'practice_root'}
    assert result['execution_verified'] is False
    assert not (tmp_path / '.runner').exists()


def test_capability_entries_reject_escape_and_missing_files(tmp_path):
    source(tmp_path, "print('ok')")
    result = runner.execution_capabilities(tmp_path, ['../outside.py', 'missing.py'])
    assert result['preflight']['status'] == 'passed'
    assert all(not entry['supported'] and not entry['tool_available'] and entry['reason']
               for entry in result['entries'])
    assert result['execution_verified'] is False


def test_capabilities_scan_once_but_execution_plan_rechecks_new_project_risks(tmp_path, monkeypatch):
    source(tmp_path, "print('ok')")
    source(tmp_path, "print('also ok')", 'other.py')
    original = runner._preflight
    scans = []

    def tracked(root):
        scans.append(root)
        return original(root)

    monkeypatch.setattr(runner, '_preflight', tracked)
    result = runner.execution_capabilities(tmp_path, ['main.py', 'other.py'])
    assert result['preflight']['status'] == 'passed'
    assert len(scans) == 1
    source(tmp_path, "import os\nos.system('echo blocked')", 'new.py')
    with pytest.raises(ValueError, match='潜在危险'):
        runner.plan_run(tmp_path, 'main.py')
    assert len(scans) == 2
