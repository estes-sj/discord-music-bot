import pylast


class LastFMError(Exception):
    pass


class LastFMClient:
    def __init__(self, api_key, api_secret, username=None, password=None, network=None):
        if not api_key or not api_secret:
            raise LastFMError("Last.fm API key and secret are required")
        if bool(username) != bool(password):
            raise LastFMError("Last.fm username and password must be configured together")
        if network is not None:
            self.network = network
            return
        arguments = {"api_key": api_key, "api_secret": api_secret}
        if username and password:
            arguments.update(username=username, password_hash=pylast.md5(password))
        self.network = pylast.LastFMNetwork(**arguments)

    @staticmethod
    def normalize_track(value):
        track = getattr(value, "item", value)
        artist = track.get_artist()
        return {
            "name": str(track.get_name()),
            "artists": [{"name": str(artist.get_name())}],
        }

    def get_radio_tracks(self, query, limit):
        query = query.strip()
        if not query:
            raise LastFMError("A song, artist, genre, or other search term is required")

        candidates = []
        try:
            track_results = self.network.search_for_track("", query).get_next_page() or []
            if track_results:
                candidates.extend(track_results[0].get_similar(limit=limit) or [])
            artist_results = self.network.search_for_artist(query).get_next_page() or []
            if artist_results:
                candidates.extend(artist_results[0].get_top_tracks(limit=limit) or [])
            candidates.extend(self.network.get_tag(query).get_top_tracks(limit=limit) or [])
        except pylast.WSError as error:
            raise LastFMError(f"Radio service could not create radio results: {error}") from error

        tracks = []
        seen = set()
        for candidate in candidates:
            try:
                track = self.normalize_track(candidate)
            except (AttributeError, TypeError):
                continue
            key = (track["artists"][0]["name"].casefold(), track["name"].casefold())
            if key in seen:
                continue
            seen.add(key)
            tracks.append(track)
            if len(tracks) >= limit:
                break
        return tracks