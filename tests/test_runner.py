import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from backend import runner


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'practice'
        self.root.mkdir()

    def source(self, text, name='main.py'):
        (self.root / name).write_text(text, encoding='utf-8')
        return name

    def test_path_rejects_all_escape_forms_and_accepts_new_descendant(self):
        for value in ['../original.py', '/etc/passwd', 'C:\\outside.py', '\\\\server\\file', 'a/../main.py', '.git/config', 'x:stream', '.runner/output']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.validate_patch_path(self.root, value)
        self.assertEqual(runner.validate_patch_path(self.root, 'new/code.py'), self.root / 'new' / 'code.py')

    def test_symlink_ancestor_escape(self):
        outside = Path(self.temp.name) / 'original'
        outside.mkdir()
        try:
            (self.root / 'escape').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink permission unavailable')
        with self.assertRaises(ValueError):
            runner.validate_patch_path(self.root, 'escape/not-created/file.py')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction')
    def test_junction_ancestor_escape(self):
        import _winapi
        outside = Path(self.temp.name) / 'original'
        outside.mkdir()
        _winapi.CreateJunction(str(outside), str(self.root / 'junction'))
        with self.assertRaises(ValueError):
            runner.validate_patch_path(self.root, 'junction/new/file.py')

    def test_real_python_success_and_secret_filter(self):
        entry = self.source("import os\nprint('hello')\nprint(os.environ.get('RUNNER_TEST_SECRET', 'absent'))\nprint(os.environ['HOME'])\n")
        with patch.dict(os.environ, {'RUNNER_TEST_SECRET': 'private'}):
            result = runner.run_project(self.root, entry, run_key='success')
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('hello\nabsent\n', result['stdout'])
        self.assertIn(str(self.root), result['stdout'])
        self.assertEqual(result['command'][0], sys.executable)

    def test_real_python_failure(self):
        result = runner.run_project(self.root, self.source("raise RuntimeError('oops')"), run_key='failure')
        self.assertEqual(result['status'], 'failed')
        self.assertNotEqual(result['exit_code'], 0)
        self.assertIn('oops', result['stderr'])

    def test_timeout_and_bounded_output(self):
        entry = self.source("import time\nprint('x' * 200000, flush=True)\ntime.sleep(60)")
        start = time.monotonic()
        result = runner.run_project(self.root, entry, run_key='timeout', timeout=.4)
        self.assertEqual(result['status'], 'timeout', result)
        self.assertLess(len(result['stdout']), 100000)
        self.assertLess(time.monotonic() - start, 8)
        self.assertFalse(runner.stop_run('timeout'))

    def test_cancel_actual_running_process(self):
        entry = self.source("import time\nprint('ready', flush=True)\ntime.sleep(60)")
        results = []
        worker = threading.Thread(target=lambda: results.append(runner.run_project(self.root, entry, run_key='cancel')))
        worker.start()
        stopped = False
        for _ in range(100):
            if runner.stop_run('cancel'):
                stopped = True
                break
            time.sleep(.02)
        worker.join(8)
        self.assertTrue(stopped)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]['status'], 'cancelled')
        self.assertFalse(runner.stop_run('cancel'))

    def test_preflight_blocks_install_and_absolute_write(self):
        for code in ['import os\nos.system("pip install --user thing")', 'open("/tmp/original.txt", "w").write("bad")',
                     'from pathlib import Path\nPath("../original.txt").write_text("bad")',
                     'from pathlib import Path\nPath("/tmp/original.txt").write_text("bad")',
                     'import shutil\nshutil.rmtree("folder")']:
            result = runner.run_project(self.root, self.source(code), run_key='blocked')
            self.assertEqual(result['status'], 'blocked', result)
            self.assertIsNone(result['exit_code'])

    def test_plan_itself_rejects_known_dangerous_source(self):
        entry = self.source('import os\nos.system("npm install --global package")')
        with self.assertRaises(ValueError):
            runner.plan_run(self.root, entry)

    def test_success_cleans_up_surviving_descendant(self):
        # Without process-tree cleanup the detached child writes this marker.
        entry = self.source('import os, sys\nos.spawnv(os.P_NOWAIT, sys.executable, [sys.executable, "-c", "import time; time.sleep(1); open(\'survived\', \'w\').write(\'bad\')"])\nprint("parent complete")')
        result = runner.run_project(self.root, entry, run_key='descendant')
        self.assertEqual(result['status'], 'passed', result)
        time.sleep(1.3)
        self.assertFalse((self.root / 'survived').exists())

    def test_shell_real_success_if_available(self):
        entry = self.source('#!/bin/sh\nprintf "shell-ok\\n"\n', 'main.sh')
        result = runner.run_project(self.root, entry, run_key='shell')
        if result['status'] == 'missing_tool':
            self.skipTest(result['stderr'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['stdout'], 'shell-ok\n')

    def test_build_plan_does_not_reuse_previous_output_tree(self):
        entry = self.source('public class Main {}', 'Main.java')
        with patch('backend.runner.shutil.which', side_effect=lambda x: '/tools/' + x):
            first = runner.plan_run(self.root, entry)
            second = runner.plan_run(self.root, entry)
        self.assertNotEqual(first[0][2], second[0][2])

    def test_missing_runtime_is_deterministic(self):
        entry = self.source('console.log("ok")', 'main.js')
        with patch('backend.runner.shutil.which', return_value=None):
            result = runner.run_project(self.root, entry, run_key='missing')
        self.assertEqual(result['status'], 'missing_tool')
        self.assertIn('node', result['stderr'])

    def test_unknown_entry_is_blocked(self):
        result = runner.run_project(self.root, self.source('whatever', 'package.json'), run_key='unknown')
        self.assertEqual(result['status'], 'blocked')

    def test_package_json_test_script_runs_direct_node(self):
        self.source('const test = require("node:test"); test("works", () => {});', 'check.cjs')
        entry = self.source('{"scripts":{"test":"node --test check.cjs"}}', 'package.json')
        result = runner.run_project(self.root, entry, run_key='package')
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['command'][1:], ['--test', str(self.root / 'check.cjs')])
        self.assertIn('works', result['stdout'])

    def test_package_json_does_not_run_arbitrary_or_install_hooks(self):
        for scripts in [{'test': 'node --test && npm install'}, {'test': 'node --test', 'pretest': 'npm install'}]:
            import json
            entry = self.source(json.dumps({'scripts': scripts}), 'package.json')
            result = runner.run_project(self.root, entry, run_key='unsafe-package')
            self.assertEqual(result['status'], 'blocked', result)

    def test_go_plan_includes_sibling_source_not_tests(self):
        entry = self.source('package main\nfunc main(){ helper() }', 'main.go')
        self.source('package main\nfunc helper(){}', 'helper.go')
        self.source('package main', 'main_test.go')
        with patch('backend.runner.shutil.which', return_value='/tools/go'):
            plan = runner.plan_run(self.root, entry)
        self.assertEqual(plan[0], ['/tools/go', 'run', str(self.root / 'helper.go'), str(self.root / 'main.go')])

    def test_maven_plan_resolves_dependencies_only_into_local_repository(self):
        entry = self.source('<project/>', 'pom.xml')
        with patch('backend.runner.shutil.which', return_value='/tools/mvn'):
            command = runner.plan_run(self.root, entry)[0]
        self.assertEqual(command[0], '/tools/mvn')
        self.assertNotIn('--offline', command)
        self.assertIn('test', command)
        self.assertTrue(any(arg.startswith('-Dmaven.repo.local=' + str(self.root)) for arg in command))

    def test_gradle_plan_uses_project_local_cache_without_daemon(self):
        entry = self.source('plugins { id "java" }', 'build.gradle')
        with patch('backend.runner.shutil.which', return_value='/tools/gradle'):
            command = runner.plan_run(self.root, entry)[0]
        self.assertEqual(command[0], '/tools/gradle')
        self.assertNotIn('--offline', command)
        self.assertIn('--no-daemon', command)
        self.assertIn('-Duser.home=' + str(self.root / '.runner/home'), command)
        self.assertIn('test', command)

    def typescript_compiler(self):
        compiler = Path(__file__).resolve().parents[1] / 'frontend/node_modules/typescript/bin/tsc'
        if not compiler.is_file():
            self.skipTest('Project TypeScript compiler not installed')
        return compiler

    def test_real_typescript_uses_bundled_compiler_without_project_dependencies(self):
        self.typescript_compiler()
        demo = Path(__file__).resolve().parents[1] / 'examples/typescript/main.ts'
        baseline = demo.read_text(encoding='utf-8')
        entry = self.source(baseline, 'main.ts')
        self.assertFalse((self.root / 'node_modules').exists())
        result = runner.run_project(self.root, entry, run_key='ts-bundled')
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['stdout'].strip(), 'remaining=12')
        self.source(baseline.replace('stock + ordered', 'stock - ordered'), 'main.ts')
        repaired = runner.run_project(self.root, entry, run_key='ts-bundled-fixed')
        self.assertEqual(repaired['status'], 'passed', repaired)
        self.assertEqual(repaired['stdout'].strip(), 'remaining=8')
        self.assertEqual(demo.read_text(encoding='utf-8'), baseline)

    def test_real_typescript_honors_repaired_nearest_config(self):
        compiler = self.typescript_compiler()
        (self.root / 'src').mkdir()
        self.source('{"compilerOptions":{"target":"invalid-root-config"}}', 'tsconfig.json')
        entry = self.source('console.log("typescript-config-ok");', 'src/main.ts')
        config = self.root / 'src/tsconfig.json'
        config.write_text('{"compilerOptions":{"target":"definitely-invalid"},"files":["main.ts"]}', encoding='utf-8')
        original_which = runner.shutil.which
        with patch('backend.runner.shutil.which', side_effect=lambda name: str(compiler) if name == 'tsc' else original_which(name)):
            failed = runner.run_project(self.root, entry, run_key='ts-bad')
            self.assertEqual(failed['status'], 'failed', failed)
            self.assertIn('target', failed['stdout'] + failed['stderr'])
            self.assertFalse(list((self.root / '.runner/build').rglob('*.js')))
            config.write_text('{"compilerOptions":{"target":"ES2020","module":"commonjs","outDir":"../../outside","outFile":"../../escape.js","declarationDir":"../../types","tsBuildInfoFile":"../../escape.tsbuildinfo","noEmit":true},"files":["main.ts"]}', encoding='utf-8')
            passed = runner.run_project(self.root, entry, run_key='ts-fixed')
        self.assertEqual(passed['status'], 'passed', passed)
        self.assertEqual(passed['stdout'].strip(), 'typescript-config-ok')
        self.assertTrue(Path(passed['command'][-1]).is_relative_to(self.root / '.runner/build'))
        self.assertFalse((Path(self.temp.name) / 'outside').exists())
        self.assertFalse((Path(self.temp.name) / 'escape.js').exists())
        self.assertFalse((Path(self.temp.name) / 'escape.tsbuildinfo').exists())
        self.assertFalse((Path(self.temp.name) / 'types').exists())

    def test_real_typescript_default_config_and_no_config_fallback(self):
        compiler = self.typescript_compiler()
        entry = self.source('console.log("typescript-default-ok");', 'main.ts')
        original_which = runner.shutil.which
        with patch('backend.runner.shutil.which', side_effect=lambda name: str(compiler) if name == 'tsc' else original_which(name)):
            plain = runner.run_project(self.root, entry, run_key='ts-plain')
            self.assertEqual(plain['status'], 'passed', plain)
            self.source('{"compilerOptions":{"target":"ES2020","module":"commonjs"}}', 'tsconfig.json')
            configured = runner.run_project(self.root, entry, run_key='ts-default-config')
        self.assertEqual(configured['status'], 'passed', configured)
        self.assertEqual(configured['stdout'].strip(), 'typescript-default-ok')

    def test_real_typescript_type_errors_never_emit(self):
        compiler = self.typescript_compiler()
        entry = self.source('const value: number = "wrong"; console.log(value);', 'main.ts')
        self.source('{"compilerOptions":{"target":"ES2020","noEmitOnError":false},"files":["main.ts"]}', 'tsconfig.json')
        original_which = runner.shutil.which
        with patch('backend.runner.shutil.which', side_effect=lambda name: str(compiler) if name == 'tsc' else original_which(name)):
            result = runner.run_project(self.root, entry, run_key='ts-errors')
        self.assertEqual(result['status'], 'failed', result)
        self.assertFalse(list((self.root / '.runner/build').rglob('*.js')))

    def test_bash_extension_uses_real_shell_and_source_preflight(self):
        entry = self.source('#!/bin/bash\nprintf "bash-extension-ok\\n"\n', 'main.bash')
        result = runner.run_project(self.root, entry, run_key='bash-ext')
        if result['status'] == 'missing_tool':
            self.skipTest(result['stderr'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['stdout'].strip(), 'bash-extension-ok')
        self.source('values=(first second)\nprintf "%s\\n" "${values[1]}"\n', 'main.bash')
        with patch('backend.runner.shutil.which', side_effect=lambda name: '/tools/' + name):
            self.assertEqual(runner.plan_run(self.root, entry)[0][0], '/tools/bash')
        self.source('#!/bin/bash\nsudo touch /tmp/example\n', 'main.bash')
        blocked = runner.run_project(self.root, entry, run_key='bash-ext-blocked')
        self.assertEqual(blocked['status'], 'blocked')

    def test_real_javascript(self):
        entry = self.source('console.log("javascript-ok")', 'main.js')
        result = runner.run_project(self.root, entry, run_key='js')
        if result['status'] == 'missing_tool':
            self.skipTest(result['stderr'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['stdout'].strip(), 'javascript-ok')

    def test_real_java(self):
        entry = self.source('public class Main { public static void main(String[] args) { System.out.println("java-ok"); } }', 'Main.java')
        result = runner.run_project(self.root, entry, run_key='java', timeout=60)
        if result['status'] == 'missing_tool':
            self.skipTest(result['stderr'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['stdout'].strip(), 'java-ok')
        self.assertFalse((self.root / 'Main.class').exists())

    def test_real_go_with_sibling_function(self):
        entry = self.source('package main\nfunc main() { helper() }\n', 'main.go')
        self.source('package main\nimport "fmt"\nfunc helper() { fmt.Println("go-ok") }\n', 'helper.go')
        result = runner.run_project(self.root, entry, run_key='go', timeout=90)
        if result['status'] == 'missing_tool':
            self.skipTest(result['stderr'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['stdout'].strip(), 'go-ok')

    def test_java_plan_uses_workspace_build_and_package(self):
        entry = self.source('package sample; public class Main { public static void main(String[] a) {} }', 'Main.java')
        with patch('backend.runner.shutil.which', side_effect=lambda x: '/tools/' + x):
            plan = runner.plan_run(self.root, entry)
        self.assertEqual(plan[0][0], '/tools/javac')
        self.assertEqual(plan[0][1], '-d')
        self.assertTrue(Path(plan[0][2]).is_relative_to(self.root))
        self.assertEqual(plan[1][-1], 'sample.Main')


if __name__ == '__main__':
    unittest.main()
