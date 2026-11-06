import importlib
import unittest


class LanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = importlib.import_module('backend.languages')

    def test_detection(self):
        for path, language in [('A.java','java'),('a.py','python'),('a.jsx','javascript'),('a.tsx','typescript'),('a.go','go'),('a.bash','shell')]:
            self.assertEqual(self.mod.detect_language(path), language)
        self.assertIsNone(self.mod.detect_language('package.json'))

    def test_python_ast_and_invalid_source(self):
        result = self.mod.analyze_file('a.py', '# def ghost():\nclass 库存:\n    async def count(self):\n        return 3\n')
        self.assertEqual([(s['name'],s['line'],s['end_line']) for s in result['symbols']], [('库存',2,4),('count',3,4)])
        self.assertEqual(self.mod.analyze_file('a.py','def broken(')['symbols'], [])
        self.assertEqual(result['size'],len('# def ghost():\nclass 库存:\n    async def count(self):\n        return 3\n'.encode()))

    def test_six_language_declarations_ignore_comments_and_strings(self):
        samples = {
            'Main.java': '/* class Fake {} */\nclass Main {\n public static int stock(int n) {\n return n;\n }\n}\n',
            'a.js': '// function fake() {}\nconst message = "function ghost() {}";\nfunction stock(n) {\n return n;\n}\n',
            'a.ts': '/* function fake() {} */\nexport function stock(n: number): number {\n return n;\n}\n',
            'a.go': '// func fake() {}\npackage main\nfunc stock(n int) int {\n return n\n}\n',
            'a.sh': '# fake() {\nstock() {\n echo "$1"\n}\n',
        }
        for path, source in samples.items():
            symbols = self.mod.analyze_file(path,source)['symbols']
            self.assertIn('stock',[s['name'] for s in symbols],path)
            self.assertNotIn('fake',[s['name'] for s in symbols])
            self.assertNotIn('ghost',[s['name'] for s in symbols])
            for symbol in symbols:
                self.assertGreaterEqual(symbol['end_line'],symbol['line'])
                self.assertLessEqual(symbol['end_line'],len(source.splitlines()))

    def test_entries_and_demo_sources(self):
        self.assertEqual(self.mod.suggest_entry([{'path':'util.py','symbols':[]},{'path':'main.py','symbols':[]}]), 'main.py')
        self.assertIsNone(self.mod.suggest_entry([{'path':'package.json','symbols':[]}]))
        for lang in ('java','python','javascript','typescript','go','shell'):
            folder = self.mod.demo_path(lang)
            self.assertTrue(folder.is_dir())
            sources = [self.mod.analyze_file(path.name, path.read_text(encoding='utf-8'))
                       for path in folder.iterdir() if path.is_file()]
            entry = self.mod.suggest_entry(sources, lang)
            self.assertIsNotNone(entry, lang)
            self.assertEqual(self.mod.detect_language(entry), lang)
            self.assertTrue((folder / entry).read_text(encoding='utf-8').strip())
        with self.assertRaises(ValueError):
            self.mod.demo_path('../outside')


    def test_masked_literals_and_shell_heredoc(self):
        shell = "cat <<'DOC'\nghost() {\n echo false\n}\nDOC\nreal() { echo yes; }\n"
        self.assertEqual([s['name'] for s in self.mod.analyze_file('a.sh', shell)['symbols']], ['real'])
        java = 'class Main {\n String text = """\n class Ghost {}\n """;\n int real() { return 1; }\n}\n'
        self.assertEqual([s['name'] for s in self.mod.analyze_file('Main.java', java)['symbols']], ['Main', 'real'])
