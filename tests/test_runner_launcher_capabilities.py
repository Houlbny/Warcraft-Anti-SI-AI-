"""Launcher facts describe existing argv without executing or widening the runner."""
import json
from pathlib import Path

import pytest

from backend import runner


def write(root, path, text):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding='utf-8')
    return path


@pytest.fixture
def tools(monkeypatch, tmp_path):
    directory = tmp_path / 'private-tools'
    directory.mkdir()
    monkeypatch.setattr(runner, '_tool', lambda name: str(directory / (name + '.exe')))
    monkeypatch.setattr(runner, '_build_tool', lambda name: [str(directory / (name + '.exe'))])
    monkeypatch.setattr(runner, '_shell', lambda source, default='sh': str(directory / (
        ('bash' if 'bash' in source.splitlines()[0] else default) + '.exe')))
    monkeypatch.setattr(runner.shutil, 'which', lambda name: str(directory / (name + '.exe')))
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Capability inspection must not launch a process'))
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k: pytest.fail('Capability inspection must not run commands'))
    monkeypatch.setattr(runner, '_environment', lambda *a, **k: pytest.fail('Capability inspection must not create a run environment'))
    return directory


def inspected(root, entry):
    result = runner.execution_capabilities(root, [entry])
    assert result['preflight']['status'] == 'passed'
    assert result['execution_verified'] is False
    item = result['entries'][0]
    assert item['tool_available'] is True
    launcher = item['launcher']
    assert launcher['cwd'] == 'practice_root'
    assert launcher['entry'] == entry
    assert launcher['shell'] is False
    assert 'command' in launcher
    return launcher


def test_python_launcher_reports_direct_file_and_import_path_semantics(tmp_path, tools, monkeypatch):
    root = tmp_path / 'practice'; root.mkdir()
    entry = write(root, 'tests/test_sample.py', 'import unittest\nclass Sample(unittest.TestCase):\n    pass\n')
    monkeypatch.setenv('PYTHONPATH', 'synthetic-parent-import-path')
    monkeypatch.setenv('SYNTHETIC_RUNNER_SECRET', 'do-not-serialize-this-value')
    launcher = inspected(root, entry)
    assert launcher['mode'] == 'python_file'
    assert launcher['command'] == ['python', '-u', entry]
    assert launcher['automatic_test_discovery'] is False
    assert launcher['python_import_path'] == {
        'first': 'entry_parent', 'adds_project_root': False, 'inherits_pythonpath': False}
    serialized = json.dumps(launcher)
    assert str(root) not in serialized and str(tools) not in serialized
    assert 'synthetic-parent-import-path' not in serialized
    assert 'do-not-serialize-this-value' not in serialized


@pytest.mark.parametrize(('entry', 'text', 'mode', 'command'), [
    ('main.js', 'console.log(1)', 'node_file', ['node', 'main.js']),
    ('main.mjs', 'console.log(1)', 'node_file', ['node', 'main.mjs']),
    ('main.cjs', 'console.log(1)', 'node_file', ['node', 'main.cjs']),
    ('main.py', 'print(1)', 'python_file', ['python', '-u', 'main.py']),
    ('main.go', 'package main\nfunc main() {}\n', 'go_run_files', ['go', 'run', 'main.go']),
    ('main_test.go', 'package main\n', 'go_test_files', ['go', 'test', 'main_test.go']),
    ('main.sh', '#!/bin/sh\necho 1\n', 'shell_file', ['sh', 'main.sh']),
    ('main.bash', '#!/bin/bash\necho 1\n', 'shell_file', ['bash', 'main.bash']),
    ('bash_named.sh', '#!/usr/bin/env bash\necho 1\n', 'shell_file', ['bash', 'bash_named.sh']),
])
def test_simple_language_launchers_follow_the_actual_argv(tmp_path, tools, entry, text, mode, command):
    root = tmp_path / 'practice'; root.mkdir()
    write(root, entry, text)
    launcher = inspected(root, entry)
    assert launcher['mode'] == mode
    assert launcher['command'] == command


@pytest.mark.parametrize(('script', 'mode', 'command'), [
    ('node checks.js', 'node_file', ['node', 'checks.js']),
    ('node --test checks.js', 'node_test', ['node', '--test', 'checks.js']),
    ('node', 'node_stdin', ['node']),
    ('vitest run', 'npm_script', None),
])
def test_package_launcher_uses_selected_script_not_an_invented_node_command(tmp_path, tools, script, mode, command):
    root = tmp_path / 'practice'; root.mkdir()
    write(root, 'checks.js', 'console.log(1)')
    write(root, 'package.json', json.dumps({'scripts': {'test': script}}))
    launcher = inspected(root, 'package.json')
    assert launcher['mode'] == mode
    assert launcher['command'] == command
    if mode == 'npm_script':
        assert launcher['script'] == 'test'


@pytest.mark.parametrize('task', ['build', 'start'])
def test_npm_capability_reports_the_actual_selected_task(tmp_path, tools, task):
    root = tmp_path / 'practice'; root.mkdir()
    write(root, 'package.json', json.dumps({'scripts': {task: 'custom-tool task'}}))
    launcher = inspected(root, 'package.json')
    assert launcher['mode'] == 'npm_script'
    assert launcher['script'] == task
    assert launcher['command'] is None


@pytest.mark.parametrize(('entry', 'text', 'config', 'mode'), [
    ('Main.java', 'class Main { public static void main(String[] args) {} }', None, 'java_compile_run'),
    ('pom.xml', '<project/>', None, 'maven_test'),
    ('build.gradle', '', None, 'gradle_test'),
    ('build.gradle.kts', '', None, 'gradle_test'),
    ('src/Main.java', 'class Main {}', 'pom.xml', 'maven_test'),
    ('src/Main.java', 'class Main {}', 'build.gradle', 'gradle_test'),
    ('main.ts', 'const value: number = 1;', None, 'typescript_compile_run'),
    ('src/main.ts', 'const value: number = 1;', 'tsconfig.json', 'typescript_project_compile_run'),
])
def test_build_launchers_are_accurate_and_do_not_expose_random_or_absolute_paths(tmp_path, tools, entry, text, config, mode):
    root = tmp_path / 'practice'; root.mkdir()
    write(root, entry, text)
    if config:
        write(root, config, '{}' if config.endswith('.json') else '')
    if entry.endswith('.ts'):
        write(root, 'node_modules/typescript/bin/tsc', '// existing synthetic compiler; never executed\n')
    before = {str(path.relative_to(root)) for path in root.rglob('*')}
    first = inspected(root, entry)
    second = inspected(root, entry)
    assert first == second
    assert first['mode'] == mode
    assert first['command'] is None  # Accurate mode, no pretend copyable build command.
    serialized = json.dumps(first)
    assert str(root) not in serialized and str(tools) not in serialized
    assert '.runner' not in serialized
    assert before == {str(path.relative_to(root)) for path in root.rglob('*')}


def test_launcher_is_computed_from_the_same_plan_once_without_repeating_preflight(tmp_path, tools, monkeypatch):
    root = tmp_path / 'practice'; root.mkdir()
    write(root, 'main.py', 'print(1)')
    original_plan, original_scan = runner._entry_plan, runner._preflight
    calls = []

    def plan(*args):
        calls.append('plan')
        return original_plan(*args)

    def scan(*args):
        calls.append('scan')
        return original_scan(*args)

    monkeypatch.setattr(runner, '_entry_plan', plan)
    monkeypatch.setattr(runner, '_preflight', scan)
    result = runner.execution_capabilities(root, ['main.py', 'main.py'])
    assert result['entries'][0]['launcher']['command'] == ['python', '-u', 'main.py']
    assert calls == ['scan', 'plan']


def test_missing_tool_does_not_claim_a_concrete_launcher(tmp_path, tools, monkeypatch):
    root = tmp_path / 'practice'; root.mkdir()
    write(root, 'main.js', 'console.log(1)')

    def missing(name):
        raise RuntimeError('Synthetic tool unavailable')

    monkeypatch.setattr(runner, '_tool', missing)
    result = runner.execution_capabilities(root, ['main.js'])
    assert result['entries'][0]['tool_available'] is False
    assert result['entries'][0].get('launcher') is None
    assert result['execution_verified'] is False
