import os
import sqlite3
from pathlib import Path


class SongStatsStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        return sqlite3.connect(self.database_path)

    def initialize(self):
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS song_stats (
                    guild_id INTEGER NOT NULL,
                    track_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    thumbnail_url TEXT NOT NULL,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    last_played_at TEXT,
                    PRIMARY KEY (guild_id, track_url)
                );

                CREATE TABLE IF NOT EXISTS song_ratings (
                    guild_id INTEGER NOT NULL,
                    track_url TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating IN (-1, 1)),
                    rated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, track_url, user_id)
                );
                """
            )

    def record_play(self, guild_id, track_url, title, thumbnail_url):
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO song_stats (guild_id, track_url, title, thumbnail_url, play_count, last_played_at)
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id, track_url) DO UPDATE SET
                    title = excluded.title,
                    thumbnail_url = excluded.thumbnail_url,
                    play_count = song_stats.play_count + 1,
                    last_played_at = CURRENT_TIMESTAMP
                """,
                (guild_id, track_url, title, thumbnail_url),
            )

    def set_rating(self, guild_id, track_url, user_id, rating):
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT rating FROM song_ratings WHERE guild_id = ? AND track_url = ? AND user_id = ?",
                (guild_id, track_url, user_id),
            ).fetchone()
            if existing and existing[0] == rating:
                connection.execute(
                    "DELETE FROM song_ratings WHERE guild_id = ? AND track_url = ? AND user_id = ?",
                    (guild_id, track_url, user_id),
                )
                return 0
            connection.execute(
                """
                INSERT INTO song_ratings (guild_id, track_url, user_id, rating)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, track_url, user_id) DO UPDATE SET
                    rating = excluded.rating,
                    rated_at = CURRENT_TIMESTAMP
                """,
                (guild_id, track_url, user_id, rating),
            )
            return rating

    def rating_summary(self, guild_id, track_url):
        with self.connect() as connection:
            likes, dislikes = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END), 0)
                FROM song_ratings
                WHERE guild_id = ? AND track_url = ?
                """,
                (guild_id, track_url),
            ).fetchone()
        return likes, dislikes

    def rating_users(self, guild_id, track_url, rating):
        with self.connect() as connection:
            return [
                row[0] for row in connection.execute(
                    """
                    SELECT user_id
                    FROM song_ratings
                    WHERE guild_id = ? AND track_url = ? AND rating = ?
                    ORDER BY rated_at, user_id
                    """,
                    (guild_id, track_url, rating),
                ).fetchall()
            ]

    def top_played(self, guild_id, limit=20):
        return self._query(guild_id, "song_stats.play_count DESC, song_stats.last_played_at DESC", limit)

    def top_liked(self, guild_id, limit=20):
        return self._query(guild_id, "likes DESC, song_stats.play_count DESC", limit, "HAVING likes > 0")

    def top_disliked(self, guild_id, limit=20):
        return self._query(guild_id, "dislikes DESC, song_stats.play_count DESC", limit, "HAVING dislikes > 0")

    def liked_by_user(self, guild_id, user_id, limit=20):
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT
                    song_stats.title,
                    song_stats.track_url,
                    song_stats.thumbnail_url,
                    song_stats.play_count,
                    1 AS likes,
                    0 AS dislikes
                FROM song_ratings
                JOIN song_stats ON song_stats.guild_id = song_ratings.guild_id
                    AND song_stats.track_url = song_ratings.track_url
                WHERE song_ratings.guild_id = ? AND song_ratings.user_id = ? AND song_ratings.rating = 1
                ORDER BY song_ratings.rated_at DESC
                LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()

    def _query(self, guild_id, order_by, limit, having=""):
        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT
                    song_stats.title,
                    song_stats.track_url,
                    song_stats.thumbnail_url,
                    song_stats.play_count,
                    COALESCE(SUM(CASE WHEN song_ratings.rating = 1 THEN 1 ELSE 0 END), 0) AS likes,
                    COALESCE(SUM(CASE WHEN song_ratings.rating = -1 THEN 1 ELSE 0 END), 0) AS dislikes
                FROM song_stats
                LEFT JOIN song_ratings ON song_ratings.guild_id = song_stats.guild_id
                    AND song_ratings.track_url = song_stats.track_url
                WHERE song_stats.guild_id = ?
                GROUP BY song_stats.guild_id, song_stats.track_url
                {having}
                ORDER BY {order_by}
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()


def create_store_from_environment():
    return SongStatsStore(os.getenv("SONG_STATS_DATABASE_PATH", "/app/data/song_stats.db"))