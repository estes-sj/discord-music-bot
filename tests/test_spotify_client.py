import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "bot" / "utils" / "spotify_client.py"
SPEC = importlib.util.spec_from_file_location("spotify_client_for_tests", MODULE_PATH)
SPOTIFY_CLIENT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SPOTIFY_CLIENT
SPEC.loader.exec_module(SPOTIFY_CLIENT)


class SpotifyClientTests(unittest.TestCase):
    def test_parses_spotify_urls_and_uris(self):
        self.assertEqual(SPOTIFY_CLIENT.parse_resource("https://open.spotify.com/track/abc123").resource_type, "track")
        self.assertEqual(SPOTIFY_CLIENT.parse_resource("spotify:playlist:abc123").resource_id, "abc123")
        self.assertIsNone(SPOTIFY_CLIENT.parse_resource("https://youtube.com/watch?v=abc123"))

    def test_playlist_options_use_inclusive_ranges(self):
        options = SPOTIFY_CLIENT.parse_playlist_options("--count 2 --range 2-4 --ordered", 20, 20, False)
        self.assertEqual(SPOTIFY_CLIENT.select_tracks(["one", "two", "three", "four"], options), ["two", "three"])

    def test_playlist_options_support_multiple_ranges_and_positions(self):
        options = SPOTIFY_CLIENT.parse_playlist_options("--count 7 --range 1-3,5,7,9-10 --ordered", 20, 20, False)
        tracks = list(range(1, 15))
        self.assertEqual(SPOTIFY_CLIENT.select_tracks(tracks, options), [1, 2, 3, 5, 7, 9, 10])

    def test_playlist_options_expand_short_selection_to_requested_count(self):
        options = SPOTIFY_CLIENT.parse_playlist_options("--count 10 --range 1-3,5,7,9-10 --ordered", 20, 20, False)
        tracks = list(range(1, 15))
        self.assertEqual(SPOTIFY_CLIENT.select_tracks(tracks, options), [1, 2, 3, 5, 7, 9, 10, 11, 12, 13])

    def test_playlist_options_enforce_maximum(self):
        with self.assertRaises(SPOTIFY_CLIENT.SpotifyError):
            SPOTIFY_CLIENT.parse_playlist_options("--count 21", 20, 20, False)

    def test_album_items_are_returned_as_tracks(self):
        client = SPOTIFY_CLIENT.SpotifyClient("client", "secret")
        client._get = lambda url: {
            "items": [{"type": "track", "name": "Album song", "is_playable": True}],
            "next": None,
        }
        resource = SPOTIFY_CLIENT.SpotifyResource("album", "abc123")
        self.assertEqual(client.get_tracks(resource)[0]["name"], "Album song")

    def test_refreshes_token_once_after_unauthorized_response(self):
        client = SPOTIFY_CLIENT.SpotifyClient("client", "secret")
        client.access_token = "expired"
        tokens = iter(["refreshed"])
        client._get_access_token = lambda: next(tokens)

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"name": "Track"}

            @staticmethod
            def raise_for_status():
                return None

        calls = []
        original_get = SPOTIFY_CLIENT.requests.get
        try:
            SPOTIFY_CLIENT.requests.get = lambda *args, **kwargs: calls.append(kwargs["headers"]["Authorization"]) or (
                type("Unauthorized", (), {"status_code": 401})() if len(calls) == 1 else Response()
            )
            self.assertEqual(client._get("https://example.invalid")["name"], "Track")
        finally:
            SPOTIFY_CLIENT.requests.get = original_get
        self.assertEqual(calls, ["Bearer expired", "Bearer refreshed"])

    def test_catalog_requests_include_the_configured_market(self):
        client = SPOTIFY_CLIENT.SpotifyClient("client", "secret", market="ca")
        client.access_token = "token"

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"name": "Track"}

            @staticmethod
            def raise_for_status():
                return None

        calls = []
        original_get = SPOTIFY_CLIENT.requests.get
        try:
            SPOTIFY_CLIENT.requests.get = lambda *args, **kwargs: calls.append(kwargs) or Response()
            client._get("https://example.invalid")
        finally:
            SPOTIFY_CLIENT.requests.get = original_get
        self.assertEqual(calls[0]["params"], {"market": "CA"})

    def test_playlist_uses_authenticated_items_endpoint(self):
        client = SPOTIFY_CLIENT.SpotifyClient("client", "secret")

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"items": [{"item": {"type": "track", "name": "Playlist song", "is_playable": True}}], "next": None}

            @staticmethod
            def raise_for_status():
                return None

        calls = []
        original_get = SPOTIFY_CLIENT.requests.get
        try:
            SPOTIFY_CLIENT.requests.get = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
            tracks = client.get_tracks(SPOTIFY_CLIENT.SpotifyResource("playlist", "abc123"), "user-token")
        finally:
            SPOTIFY_CLIENT.requests.get = original_get
        self.assertEqual(tracks[0]["name"], "Playlist song")
        self.assertTrue(calls[0][0][0].endswith("/items?limit=50"))
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer user-token")

    def test_playlist_forbidden_response_explains_ownership_requirement(self):
        client = SPOTIFY_CLIENT.SpotifyClient("client", "secret")

        class Response:
            status_code = 403

        original_get = SPOTIFY_CLIENT.requests.get
        try:
            SPOTIFY_CLIENT.requests.get = lambda *args, **kwargs: Response()
            with self.assertRaisesRegex(SPOTIFY_CLIENT.SpotifyPlaylistAuthorizationError, "must own"):
                client.get_tracks(SPOTIFY_CLIENT.SpotifyResource("playlist", "abc123"), "user-token")
        finally:
            SPOTIFY_CLIENT.requests.get = original_get

    def test_playlist_not_found_response_explains_ownership_requirement(self):
        client = SPOTIFY_CLIENT.SpotifyClient("client", "secret")

        class Response:
            status_code = 404

        original_get = SPOTIFY_CLIENT.requests.get
        try:
            SPOTIFY_CLIENT.requests.get = lambda *args, **kwargs: Response()
            with self.assertRaisesRegex(SPOTIFY_CLIENT.SpotifyPlaylistAuthorizationError, "must own"):
                client.get_tracks(SPOTIFY_CLIENT.SpotifyResource("playlist", "abc123"), "user-token")
        finally:
            SPOTIFY_CLIENT.requests.get = original_get


if __name__ == "__main__":
    unittest.main()