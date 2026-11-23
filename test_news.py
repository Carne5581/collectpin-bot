"""Offline regression tests; do not load credentials or call external services."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import re
import unittest


SOURCE = Path(__file__).with_name('bot.py').read_text(encoding='utf-8')
TREE = ast.parse(SOURCE)
NAMES = {'chisla', 'analyze_news'}
NS = dict(datetime=datetime, timezone=timezone, re=re, NEWS_POROG=6)
exec(compile(ast.Module(body=[n for n in TREE.body
     if isinstance(n, ast.FunctionDef) and n.name in NAMES], type_ignores=[]),
     'bot.py', 'exec'), NS)


class NewsTests(unittest.TestCase):
    def analyze(self, **changes):
        data = dict(is_news=True, hype=9, kind='официально',
                    title_ru='Новости GTA 6', title_en='GTA 6 news',
                    text_ru='Новый ролик.', text_en='A new video.')
        data.update(changes)
        NS['sprosit_ii'] = lambda client, prompt: data
        return NS['analyze_news'](None, dict(title='GTA 6 news', text='',
                                           link='https://example.com', izdanie='Rockstar'))

    def test_news_remains_a_draft_without_a_generic_review_note(self):
        result = self.analyze(hype=10)
        self.assertEqual(result['status'], '')
        self.assertEqual(result['note'], 'Rockstar')

    def test_long_text_is_preserved_and_flagged(self):
        title = 'Разработка игры ' * 10
        body = 'Подробности разработки. ' * 20
        result = self.analyze(title_ru=title, text_en=body)
        self.assertEqual(result['title_ru'], title.strip())
        self.assertEqual(result['text_en'], body.strip())
        self.assertIn('⚠️', result['note'])

    def test_invalid_text_fields_are_rejected(self):
        for value in [None, '', '   ', 123, ['text']]:
            with self.subTest(value=value):
                self.assertIsNone(self.analyze(title_ru=value))

    def test_added_numbers_are_flagged(self):
        self.assertIn('⚠️', self.analyze(text_ru='Размер 230 ГБ.')['note'])

    def test_valid_news_and_existing_filters(self):
        self.assertEqual(self.analyze()['title_en'], 'GTA 6 news')
        self.assertIs(self.analyze(hype=3), False)
        self.assertIs(self.analyze(povtor=True), False)
        self.assertIs(self.analyze(is_news=False), False)


if __name__ == '__main__':
    unittest.main()
