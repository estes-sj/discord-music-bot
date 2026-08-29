import os
import sqlite3
from pathlib import Path


class UserPlaylistStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self):
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_playlists (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL COLLATE NOCASE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (user_id, name)
                );
                """
            )
            track_columns = connection.execute("PRAGMA table_info(user_playlist_tracks)").fetchall()
            if track_columns and any(column[1] == "stream_url" for column in track_columns):
                connection.executescript(
                    """
                    CREATE TABLE user_playlist_tracks_replacement (
                        id INTEGER PRIMARY KEY,
                        playlist_id INTEGER NOT NULL REFERENCES user_playlists(id) ON DELETE CASCADE,
                        position INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        thumbnail_url TEXT NOT NULL,
                        source_url TEXT NOT NULL,
                        duration INTEGER NOT NULL,
                        UNIQUE (playlist_id, position)
                    );

                    INSERT INTO user_playlist_tracks_replacement
                        (id, playlist_id, position, title, thumbnail_url, source_url, duration)
                    SELECT id, playlist_id, position, title, thumbnail_url, source_url, duration
                    FROM user_playlist_tracks;

                    DROP TABLE user_playlist_tracks;
                    ALTER TABLE user_playlist_tracks_replacement RENAME TO user_playlist_tracks;
                    """
                )
            elif not track_columns:
                connection.executescript(
                    """
                    CREATE TABLE user_playlist_tracks (
                        id INTEGER PRIMARY KEY,
                        playlist_id INTEGER NOT NULL REFERENCES user_playlists(id) ON DELETE CASCADE,
                        position INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        thumbnail_url TEXT NOT NULL,
                        source_url TEXT NOT NULL,
                        duration INTEGER NOT NULL,
                        UNIQUE (playlist_id, position)
                    );

                    """
                )

    def create_playlist(self, user_id, name, max_playlists):
        name = name.strip()
        if not name:
            raise ValueError("Playlist name cannot be empty")
        with self.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM user_playlists WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            if count >= max_playlists:
                raise ValueError(f"You can create up to {max_playlists} playlists")
            try:
                cursor = connection.execute(
                    "INSERT INTO user_playlists (user_id, name) VALUES (?, ?)", (user_id, name)
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("You already have a playlist with that name") from error
            return cursor.lastrowid

    def list_playlists(self, user_id):
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT user_playlists.name, COUNT(user_playlist_tracks.id)
                FROM user_playlists
                LEFT JOIN user_playlist_tracks ON user_playlist_tracks.playlist_id = user_playlists.id
                WHERE user_playlists.user_id = ?
                GROUP BY user_playlists.id
                ORDER BY user_playlists.name COLLATE NOCASE
                """,
                (user_id,),
            ).fetchall()

    def track_count(self, user_id):
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT COUNT(*)
                FROM user_playlist_tracks
                JOIN user_playlists ON user_playlists.id = user_playlist_tracks.playlist_id
                WHERE user_playlists.user_id = ?
                """,
                (user_id,),
            ).fetchone()[0]

    def get_playlist_tracks(self, user_id, name):
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT title, thumbnail_url, source_url, duration
                FROM user_playlist_tracks
                JOIN user_playlists ON user_playlists.id = user_playlist_tracks.playlist_id
                WHERE user_playlists.user_id = ? AND user_playlists.name = ?
                ORDER BY user_playlist_tracks.position
                """,
                (user_id, name),
            ).fetchall()

    def add_tracks(self, user_id, name, tracks, max_songs):
        if not tracks:
            return 0
        with self.connect() as connection:
            playlist = connection.execute(
                "SELECT id FROM user_playlists WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
            if not playlist:
                raise ValueError("Playlist not found")
            playlist_id = playlist[0]
            playlist_count = connection.execute(
                "SELECT COUNT(*) FROM user_playlist_tracks WHERE playlist_id = ?", (playlist_id,)
            ).fetchone()[0]
            user_track_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM user_playlist_tracks
                JOIN user_playlists ON user_playlists.id = user_playlist_tracks.playlist_id
                WHERE user_playlists.user_id = ?
                """,
                (user_id,),
            ).fetchone()[0]
            if user_track_count + len(tracks) > max_songs:
                raise ValueError(f"You can save up to {max_songs} songs across your playlists")
            for index, track in enumerate(tracks, start=playlist_count + 1):
                connection.execute(
                    """
                    INSERT INTO user_playlist_tracks
                        (playlist_id, position, title, thumbnail_url, source_url, duration)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (playlist_id, index, *track),
                )
        return len(tracks)

    def remove_track(self, user_id, name, position):
        with self.connect() as connection:
            playlist = connection.execute(
                "SELECT id FROM user_playlists WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
            if not playlist:
                raise ValueError("Playlist not found")
            playlist_id = playlist[0]
            track = connection.execute(
                """
                SELECT title, thumbnail_url, source_url, duration
                FROM user_playlist_tracks
                WHERE playlist_id = ? AND position = ?
                """,
                (playlist_id, position),
            ).fetchone()
            if not track:
                raise ValueError("Song position not found")
            connection.execute(
                "DELETE FROM user_playlist_tracks WHERE playlist_id = ? AND position = ?",
                (playlist_id, position),
            )
            track_ids = [
                row[0] for row in connection.execute(
                    "SELECT id FROM user_playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist_id,)
                ).fetchall()
            ]
            for new_position, track_id in enumerate(track_ids, start=1):
                connection.execute("UPDATE user_playlist_tracks SET position = ? WHERE id = ?", (-new_position, track_id))
            for new_position, track_id in enumerate(track_ids, start=1):
                connection.execute("UPDATE user_playlist_tracks SET position = ? WHERE id = ?", (new_position, track_id))
            return track

    def move_track(self, user_id, name, source_position, destination_position):
        with self.connect() as connection:
            playlist = connection.execute(
                "SELECT id FROM user_playlists WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
            if not playlist:
                raise ValueError("Playlist not found")
            playlist_id = playlist[0]
            track_ids = [
                row[0] for row in connection.execute(
                    "SELECT id FROM user_playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist_id,)
                ).fetchall()
            ]
            count = len(track_ids)
            if not 1 <= source_position <= count or not 1 <= destination_position <= count:
                raise ValueError("Song position not found")
            if source_position == destination_position:
                return
            moved_track_id = track_ids.pop(source_position - 1)
            track_ids.insert(destination_position - 1, moved_track_id)
            for position, track_id in enumerate(track_ids, start=1):
                connection.execute("UPDATE user_playlist_tracks SET position = ? WHERE id = ?", (-position, track_id))
            for position, track_id in enumerate(track_ids, start=1):
                connection.execute("UPDATE user_playlist_tracks SET position = ? WHERE id = ?", (position, track_id))

    def delete_playlist(self, user_id, name):
        with self.connect() as connection:
            playlist = connection.execute(
                "SELECT id FROM user_playlists WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
            if not playlist:
                return False
            connection.execute("DELETE FROM user_playlist_tracks WHERE playlist_id = ?", (playlist[0],))
            connection.execute("DELETE FROM user_playlists WHERE id = ?", (playlist[0],))
            return True


def create_store_from_environment():
    return UserPlaylistStore(os.getenv("USER_PLAYLIST_DATABASE_PATH", "/app/data/user_playlists.db"))