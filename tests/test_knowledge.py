import importlib
import unittest


class KnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = importlib.import_module('backend.knowledge')

    def test_complete_original_offline_cards(self):
        cards = self.mod.list_cards()
        self.assertEqual(len({c['id'] for c in cards}),len(cards))
        for lang in ('java','python','javascript','typescript','go','shell'):
            subset = self.mod.list_cards(language=lang)
            self.assertGreaterEqual(len(subset),6)
            for card in subset:
                for key in ('title','summary','body','code','output','pitfalls','tags','version','source_url','reviewed_at'):
                    self.assertTrue(card[key], (card['id'],key))
                self.assertEqual(card['reviewed_at'],'2026-10-29')
                self.assertTrue(card['source_url'].startswith('https://'))

    def test_search_filters_and_returns_copies(self):
        self.assertTrue(self.mod.list_cards(query='库存',language='python'))
        self.assertEqual(self.mod.list_cards(language='unknown'), [])
        self.assertEqual(self.mod.list_cards(query='no-such-phrase-19999'), [])
        cards = self.mod.list_cards(language='python')
        cards[0]['tags'].append('mutated')
        self.assertNotIn('mutated', self.mod.list_cards(language='python')[0]['tags'])
