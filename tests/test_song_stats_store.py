import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "bot" / "utils" / "song_stats_store.py"
SPEC = importlib.util.spec_from_file_location("song_stats_store_for_tests", MODULE_PATH)
SONG_STATS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SONG_STATS
SPEC.loader.exec_module(SONG_STATS)


class SongStatsStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SONG_STATS.SongStatsStore(Path(self.directory.name) / "song_stats.db")

    def tearDown(self):
        self.directory.cleanup()

    def record_play(self, guild_id, url, title="Song"):
        self.store.record_play(guild_id, url, title, "thumb")

    def test_records_plays_per_guild(self):
        self.record_play(1, "url-1", "First")
        self.record_play(1, "url-1", "First updated")
        self.record_play(2, "url-1", "Other guild")

        self.assertEqual(self.store.top_played(1), [("First updated", "url-1", "thumb", 2, 0, 0)])
        self.assertEqual(self.store.top_played(2), [("Other guild", "url-1", "thumb", 1, 0, 0)])

    def test_replaces_and_removes_user_rating(self):
        self.record_play(1, "url-1")
        self.assertEqual(self.store.set_rating(1, "url-1", 10, 1), 1)
        self.assertEqual(self.store.rating_summary(1, "url-1"), (1, 0))
        self.assertEqual(self.store.set_rating(1, "url-1", 10, -1), -1)
        self.assertEqual(self.store.rating_summary(1, "url-1"), (0, 1))
        self.assertEqual(self.store.set_rating(1, "url-1", 10, -1), 0)
        self.assertEqual(self.store.rating_summary(1, "url-1"), (0, 0))

    def test_rankings_and_liked_tracks_are_limited_and_ordered(self):
        for index in range(25):
            url = f"url-{index}"
            for _ in range(index + 1):
                self.record_play(1, url, f"Song {index}")
            self.store.set_rating(1, url, 10, 1)
            if index % 2 == 0:
                self.store.set_rating(1, url, 20, -1)

        self.assertEqual(len(self.store.top_played(1)), 20)
        self.assertEqual(self.store.top_played(1)[0][0], "Song 24")
        self.assertEqual(self.store.top_liked(1)[0][4], 1)
        self.assertEqual(self.store.top_disliked(1)[0][5], 1)
        self.assertEqual(len(self.store.liked_by_user(1, 10)), 20)


if __name__ == "__main__":
    unittest.main()