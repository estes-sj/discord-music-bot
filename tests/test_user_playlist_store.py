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
        self.store.add_tracks(1, "Road trip", [("Song", "thumb", "source", 60)], 50)

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

        self.store.add_tracks(1, "One", [("Song", "thumb", "source", 60)], 1)
        with self.assertRaisesRegex(ValueError, "up to 1 songs"):
            self.store.add_tracks(1, "One", [("Other", "thumb", "source", 60)], 1)

        self.store.create_playlist(1, "Two", 2)
        with self.assertRaisesRegex(ValueError, "up to 1 songs"):
            self.store.add_tracks(1, "Two", [("Other", "thumb", "source", 60)], 1)

    def test_removes_and_moves_tracks_without_losing_order(self):
        self.store.create_playlist(1, "One", 3)
        self.store.add_tracks(
            1,
            "One",
            [("First", "t1", "u1", 1), ("Second", "t2", "u2", 2), ("Third", "t3", "u3", 3)],
            50,
        )
        self.store.move_track(1, "One", 3, 1)
        removed_track = self.store.remove_track(1, "One", 2)

        self.assertEqual(removed_track[0], "First")
        self.assertEqual([track[0] for track in self.store.get_playlist_tracks(1, "One")], ["Third", "Second"])

    def test_migrates_legacy_signed_stream_urls_without_losing_tracks(self):
        database_path = Path(self.directory.name) / "legacy.db"
        with USER_PLAYLISTS.sqlite3.connect(database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE user_playlists (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL COLLATE NOCASE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (user_id, name)
                );
                CREATE TABLE user_playlist_tracks (
                    id INTEGER PRIMARY KEY,
                    playlist_id INTEGER NOT NULL REFERENCES user_playlists(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    stream_url TEXT NOT NULL,
                    thumbnail_url TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    duration INTEGER NOT NULL,
                    UNIQUE (playlist_id, position)
                );
                INSERT INTO user_playlists (id, user_id, name) VALUES (1, 1, 'Legacy');
                INSERT INTO user_playlist_tracks
                    (playlist_id, position, title, stream_url, thumbnail_url, source_url, duration)
                VALUES (1, 1, 'Song', 'https://expired.example', 'thumb', 'https://youtube.example', 60);
                """
            )

        store = USER_PLAYLISTS.UserPlaylistStore(database_path)

        self.assertEqual(store.get_playlist_tracks(1, "Legacy"), [("Song", "thumb", "https://youtube.example", 60)])
        with store.connect() as connection:
            columns = [column[1] for column in connection.execute("PRAGMA table_info(user_playlist_tracks)")]
        self.assertNotIn("stream_url", columns)


if __name__ == "__main__":
    unittest.main()