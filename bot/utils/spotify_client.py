import random
import re
from dataclasses import dataclass
from urllib.parse import urlencode

import requests


SPOTIFY_URL_PATTERN = re.compile(
    r"^(?:https?://open\.spotify\.com/(?:intl-[a-z-]+/)?|spotify:)(track|album|playlist)(?:/|:)([A-Za-z0-9]+)"
)


class SpotifyError(Exception):
    pass


class SpotifyCredentialsError(SpotifyError):
    pass


class SpotifyAccessError(SpotifyError):
    pass


class SpotifyPlaylistAuthorizationError(SpotifyError):
    pass


@dataclass(frozen=True)
class SpotifyResource:
    resource_type: str
    resource_id: str


def parse_resource(value):
    match = SPOTIFY_URL_PATTERN.match(value.strip())
    return SpotifyResource(*match.groups()) if match else None


def parse_playlist_options(value, max_tracks, default_tracks, default_shuffle):
    tokens = value.split()
    options = {"count": None, "range": None, "shuffle": default_shuffle}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--shuffle":
            options["shuffle"] = True
        elif token == "--ordered":
            options["shuffle"] = False
        elif token == "--count":
            index += 1
            if index >= len(tokens) or options[token[2:]] is not None:
                raise SpotifyError(f"Invalid {token} option")
            options[token[2:]] = tokens[index]
        elif token == "--range":
            index += 1
            if index >= len(tokens) or options["range"] is not None:
                raise SpotifyError("Invalid --range option")
            range_tokens = []
            while index < len(tokens) and not tokens[index].startswith("--"):
                range_tokens.append(tokens[index])
                index += 1
            if not range_tokens:
                raise SpotifyError("Invalid --range option")
            options["range"] = "".join(range_tokens)
            continue
        else:
            raise SpotifyError(f"Unknown playlist option: {token}")
        index += 1

    count = default_tracks if options["count"] is None else int(options["count"])
    if count < 1 or count > max_tracks:
        raise SpotifyError(f"Count must be between 1 and {max_tracks}")
    ranges = [(1, None)]
    if options["range"]:
        ranges = []
        for range_part in options["range"].split(","):
            range_match = re.fullmatch(r"(\d+)(?:-(\d*))?", range_part)
            if not range_match:
                raise SpotifyError("Range must use positions, START-END ranges, or START- ranges, such as 1-3,5,7,9-10,40-")
            start = int(range_match.group(1))
            end_value = range_match.group(2)
            end = None if end_value == "" and range_part.endswith("-") else int(end_value or start)
            if start < 1 or (end is not None and end < start):
                raise SpotifyError("Range positions must be positive and increasing")
            ranges.append((start, end))
    return {"count": count, "ranges": ranges, "shuffle": options["shuffle"]}


def select_tracks(tracks, options):
    ranges = options.get("ranges")
    if ranges is None:
        ranges = [(options["start"], options["end"])]
    selected_indices = []
    for start, end in ranges:
        end = len(tracks) if end is None else min(end, len(tracks))
        for track_index in range(start - 1, end):
            if track_index not in selected_indices:
                selected_indices.append(track_index)

    final_range_end = ranges[-1][1]
    if final_range_end is not None:
        for track_index in range(final_range_end, len(tracks)):
            if len(selected_indices) >= options["count"]:
                break
            if track_index not in selected_indices:
                selected_indices.append(track_index)

    if options["shuffle"]:
        if ranges == [(1, None)]:
            return random.sample(tracks, min(options["count"], len(tracks)))
        selected_for_shuffle = selected_indices if ranges[-1][1] is None else selected_indices[:options["count"]]
        eligible = [tracks[track_index] for track_index in selected_for_shuffle]
        return random.sample(eligible, min(options["count"], len(eligible)))
    return [tracks[track_index] for track_index in selected_indices[:options["count"]]]


class SpotifyClient:
    def __init__(self, client_id, client_secret, market="US"):
        if not re.fullmatch(r"[A-Za-z]{2}", market):
            raise SpotifyError("Spotify market must be a two-letter ISO country code")
        self.client_id = client_id
        self.client_secret = client_secret
        self.market = market.upper()
        self.access_token = None

    def validate_credentials(self):
        self.access_token = self._get_access_token()

    def playlist_authorization_url(self, redirect_uri, state):
        return "https://accounts.spotify.com/authorize?" + urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": "playlist-read-private playlist-read-collaborative",
                "state": state,
                "show_dialog": "true",
            }
        )

    def exchange_playlist_authorization_code(self, code, redirect_uri):
        return self._playlist_token_request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
        )

    def refresh_playlist_access_token(self, refresh_token):
        access_token, new_refresh_token, expires_in = self._playlist_token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        return access_token, new_refresh_token or refresh_token, expires_in

    def _playlist_token_request(self, data):
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data=data,
            auth=(self.client_id, self.client_secret),
            timeout=10,
        )
        if response.status_code in (400, 401, 403):
            raise SpotifyPlaylistAuthorizationError("Spotify rejected the playlist authorization")
        response.raise_for_status()
        payload = response.json()
        return payload["access_token"], payload.get("refresh_token"), payload["expires_in"]

    def _get_access_token(self):
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=10,
        )
        if response.status_code in (401, 403):
            raise SpotifyCredentialsError("Spotify rejected the supplied credentials")
        response.raise_for_status()
        return response.json()["access_token"]

    def _get(self, url, retry=True):
        if not self.access_token:
            self.access_token = self._get_access_token()
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.access_token}"},
            params={"market": self.market},
            timeout=10,
        )
        if response.status_code == 401 and retry:
            self.access_token = self._get_access_token()
            return self._get(url, retry=False)
        if response.status_code == 401:
            raise SpotifyCredentialsError("Spotify rejected the stored credentials")
        if response.status_code == 403:
            raise SpotifyAccessError(
                "Spotify denied access to this link. It may be private, unavailable in this region, "
                "or unavailable to Client Credentials authentication"
            )
        if response.status_code == 429:
            raise SpotifyError("Spotify is rate limiting requests; try again shortly")
        response.raise_for_status()
        return response.json()

    def get_tracks(self, resource, playlist_access_token=None):
        if resource.resource_type == "track":
            return [self._get(f"https://api.spotify.com/v1/tracks/{resource.resource_id}")]

        if resource.resource_type == "playlist":
            if not playlist_access_token:
                raise SpotifyPlaylistAuthorizationError("Spotify playlist authorization is required")
            return self._get_playlist_items(resource.resource_id, playlist_access_token)

        url = f"https://api.spotify.com/v1/albums/{resource.resource_id}/tracks?limit=50"
        tracks = []
        while url:
            page = self._get(url)
            tracks.extend(page.get("items", []))
            url = page.get("next")
        return [track for track in tracks if track.get("type") == "track" and track.get("is_playable", True)]

    def _get_playlist_items(self, playlist_id, access_token):
        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items?limit=50"
        tracks = []
        while url:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"market": self.market},
                timeout=10,
            )
            if response.status_code == 401:
                raise SpotifyPlaylistAuthorizationError("Spotify playlist authorization has expired")
            if response.status_code in {403, 404}:
                raise SpotifyPlaylistAuthorizationError(
                    "Spotify could not access that playlist. The authorizing user must own the playlist "
                    "or be a collaborator. Copy the playlist to that Spotify account or ask its owner to add them as a collaborator"
                )
            if response.status_code == 429:
                raise SpotifyError("Spotify is rate limiting requests; try again shortly")
            response.raise_for_status()
            page = response.json()
            tracks.extend(item.get("item") for item in page.get("items", []) if item.get("item"))
            url = page.get("next")
        return [track for track in tracks if track.get("type") == "track" and track.get("is_playable", True)]


def track_query(track):
    artists = ", ".join(artist["name"] for artist in track.get("artists", []))
    return f"{artists} - {track['name']}"