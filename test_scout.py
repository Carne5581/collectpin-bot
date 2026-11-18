"""Offline checks for queuing guide links without spending Groq quota."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import re
import unittest
from urllib.parse import urlparse


TREE = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
NS = dict(re=re, urlparse=urlparse, datetime=datetime, timezone=timezone,
          SEARCH_START=datetime(2026, 11, 18, 11, tzinfo=timezone.utc))
exec(compile(ast.Module(body=[node for node in TREE.body
     if isinstance(node, ast.FunctionDef) and node.name == "guide_candidate"],
     type_ignores=[]), "bot.py", "exec"), NS)


class GuideTests(unittest.TestCase):
    def candidate(self, **changes):
        post = dict(title="GTA 6 Los Santos Panic Collectible Card Set Locations",
                    text="", link="https://www.powerpyx.com/guide/", image_url="",
                    guide_host="www.powerpyx.com",
                    published=datetime(2026, 11, 18, 12, tzinfo=timezone.utc))
        post.update(changes)
        return NS["guide_candidate"](post)

    def test_trusted_guide_is_queued_without_coordinates(self):
        candidate = self.candidate()
        self.assertEqual(candidate["category"], "коллекционка")
        self.assertEqual(candidate["source_url"], "https://www.powerpyx.com/guide/")
        self.assertEqual(candidate["coord_x"], "")
        self.assertEqual(candidate["coord_y"], "")

    def test_ignores_prelaunch_wrong_host_and_general_news(self):
        for changes in [
            dict(published=datetime(2026, 11, 17, 23, tzinfo=timezone.utc)),
            dict(published=None),
            dict(link="https://www.powerpyx.com.example.org/guide/"),
            dict(title="GTA VI launch date news"),
            dict(title="GTA VI: Gone Fishing Walkthrough"),
            dict(title="GTA 5 Collectible Locations"),
            dict(title="GTA 6 Collectible Locations Rumor"),
        ]:
            with self.subTest(changes=changes):
                self.assertIsNone(self.candidate(**changes))

    def test_rare_thief_guide_uses_same_path(self):
        candidate = self.candidate(
            title="All GTA VI Magazines, Books & Comics Locations",
            link="https://rarethief.com/all-gta-vi-magazines-books-comics-locations/",
            guide_host="rarethief.com")
        self.assertEqual(candidate["category"], "коллекционка")

    def test_release_day_guide_published_just_before_unlock(self):
        candidate = self.candidate(published=datetime(2026, 11, 18, 10, 51,
                                                      tzinfo=timezone.utc))
        self.assertIsNotNone(candidate)
