import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "bot" / "utils" / "lastfm_client.py"
SPEC = importlib.util.spec_from_file_location("lastfm_client_for_tests", MODULE_PATH)
LASTFM_CLIENT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LASTFM_CLIENT
SPEC.loader.exec_module(LASTFM_CLIENT)


class NamedValue:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class Track(NamedValue):
    def __init__(self, artist, name, similar=None):
        super().__init__(name)
        self.artist = NamedValue(artist)
        self.similar = similar or []

    def get_artist(self):
        return self.artist

    def get_similar(self, limit):
        return self.similar[:limit]


class Artist(NamedValue):
    def __init__(self, name, tracks):
        super().__init__(name)
        self.tracks = tracks

    def get_top_tracks(self, limit):
        return self.tracks[:limit]


class Search:
    def __init__(self, values):
        self.values = values

    def get_next_page(self):
        return self.values


class Tag:
    def __init__(self, tracks):
        self.tracks = tracks

    def get_top_tracks(self, limit):
        return self.tracks[:limit]


class Network:
    def __init__(self):
        self.shared = Track("Artist", "Shared")
        self.seed = Track("Seed Artist", "Seed", [self.shared, Track("Similar", "Song")])

    def search_for_track(self, artist, track):
        return Search([self.seed])

    def search_for_artist(self, artist):
        return Search([Artist(artist, [self.shared, Track("Artist", "Top Song")])])

    def get_tag(self, tag):
        return Tag([Track("Genre Artist", "Genre Song")])


class LastFMClientTests(unittest.TestCase):
    def test_radio_tracks_combine_and_deduplicate_sources(self):
        client = LASTFM_CLIENT.LastFMClient("key", "secret", network=Network())

        tracks = client.get_radio_tracks("rock", 4)

        self.assertEqual(
            tracks,
            [
                {"name": "Shared", "artists": [{"name": "Artist"}]},
                {"name": "Song", "artists": [{"name": "Similar"}]},
                {"name": "Top Song", "artists": [{"name": "Artist"}]},
                {"name": "Genre Song", "artists": [{"name": "Genre Artist"}]},
            ],
        )

    def test_credentials_require_key_secret_and_complete_user_login(self):
        with self.assertRaises(LASTFM_CLIENT.LastFMError):
            LASTFM_CLIENT.LastFMClient("", "secret")
        with self.assertRaises(LASTFM_CLIENT.LastFMError):
            LASTFM_CLIENT.LastFMClient("key", "secret", username="user")


if __name__ == "__main__":
    unittest.main()