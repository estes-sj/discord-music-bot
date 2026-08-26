import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet


MODULE_PATH = Path(__file__).parents[1] / "bot" / "utils" / "spotify_store.py"
SPEC = importlib.util.spec_from_file_location("spotify_store_for_tests", MODULE_PATH)
SPOTIFY_STORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SPOTIFY_STORE
SPEC.loader.exec_module(SPOTIFY_STORE)


class SpotifyStoreTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_persisted_per_guild(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "spotify.db"
            store = SPOTIFY_STORE.SpotifyStore(database_path, Fernet.generate_key().decode())
            store.save_credentials(42, "client-id", "client-secret", 100)

            self.assertEqual(store.get_credentials(42), ("client-id", "client-secret"))
            self.assertIsNotNone(store.status(42))
            self.assertNotIn(b"client-secret", database_path.read_bytes())
            self.assertTrue(store.clear_credentials(42))
            self.assertIsNone(store.get_credentials(42))

    def test_playlist_tokens_are_encrypted_and_removed_with_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "spotify.db"
            store = SPOTIFY_STORE.SpotifyStore(database_path, Fernet.generate_key().decode())
            store.save_credentials(42, "client-id", "client-secret", 100)
            store.save_playlist_token(42, "access-token", "refresh-token", 123456, 100)

            self.assertEqual(store.get_playlist_token(42), ("access-token", "refresh-token", 123456))
            self.assertEqual(store.get_playlist_authorizer(42), 100)
            self.assertNotIn(b"refresh-token", database_path.read_bytes())
            store.clear_credentials(42)
            self.assertIsNone(store.get_playlist_token(42))
            self.assertIsNone(store.get_playlist_authorizer(42))


if __name__ == "__main__":
    unittest.main()