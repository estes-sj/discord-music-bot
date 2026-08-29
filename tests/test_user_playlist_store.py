import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "bot" / "utils" / "user_playlist_store.py"
SPEC = importlib.util.spec_from_file_location("user_playlist_store_for_tests", MODULE_PATH)
USER_PLAYLISTS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = USER_PLAYLISTS
SPEC.loader.exec_module(USER_PLAYLISTS)


class UserPlaylistStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = USER_PLAYLISTS.UserPlaylistStore(Path(self.directory.name) / "playlists.db")

    def tearDown(self):
        self.directory.cleanup()

    def test_creates_lists_and_deletes_playlists_per_user(self):
        self.store.create_playlist(1, "Road trip", 3)
        self.store.create_playlist(2, "Road trip", 3)
        self.store.add_tracks(1, "Road trip", [("Song", "stream", "thumb", "source", 60)], 50)

        self.assertEqual(self.store.list_playlists(1), [("Road trip", 1)])
        self.assertTrue(self.store.delete_playlist(1, "Road trip"))
        self.assertEqual(self.store.list_playlists(1), [])
        self.store.create_playlist(1, "Road trip", 3)
        self.assertEqual(self.store.list_playlists(1), [("Road trip", 0)])
        self.assertEqual(self.store.list_playlists(2), [("Road trip", 0)])

    def test_enforces_playlist_and_song_limits(self):
        self.store.create_playlist(1, "One", 2)
        with self.assertRaisesRegex(ValueError, "up to 1 playlists"):
            self.store.create_playlist(1, "Two", 1)

        self.store.add_tracks(1, "One", [("Song", "stream", "thumb", "source", 60)], 1)
        with self.assertRaisesRegex(ValueError, "up to 1 songs"):
            self.store.add_tracks(1, "One", [("Other", "stream", "thumb", "source", 60)], 1)

        self.store.create_playlist(1, "Two", 2)
        with self.assertRaisesRegex(ValueError, "up to 1 songs"):
            self.store.add_tracks(1, "Two", [("Other", "stream", "thumb", "source", 60)], 1)

    def test_removes_and_moves_tracks_without_losing_order(self):
        self.store.create_playlist(1, "One", 3)
        self.store.add_tracks(
            1,
            "One",
            [("First", "s1", "t1", "u1", 1), ("Second", "s2", "t2", "u2", 2), ("Third", "s3", "t3", "u3", 3)],
            50,
        )
        self.store.move_track(1, "One", 3, 1)
        removed_track = self.store.remove_track(1, "One", 2)

        self.assertEqual(removed_track[0], "First")
        self.assertEqual([track[0] for track in self.store.get_playlist_tracks(1, "One")], ["Third", "Second"])


if __name__ == "__main__":
    unittest.main()