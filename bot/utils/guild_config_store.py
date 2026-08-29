import os
import sqlite3
from pathlib import Path


class GuildConfigStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        return sqlite3.connect(self.database_path)

    def initialize(self):
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    command_prefix TEXT NOT NULL,
                    slash_commands_enabled INTEGER NOT NULL DEFAULT 1 CHECK (slash_commands_enabled IN (0, 1)),
                    prefix_commands_enabled INTEGER NOT NULL DEFAULT 1 CHECK (prefix_commands_enabled IN (0, 1)),
                    empty_channel_enabled INTEGER NOT NULL CHECK (empty_channel_enabled IN (0, 1)),
                    empty_channel_minutes INTEGER NOT NULL CHECK (empty_channel_minutes >= 0),
                    inactivity_enabled INTEGER NOT NULL CHECK (inactivity_enabled IN (0, 1)),
                    inactivity_minutes INTEGER NOT NULL CHECK (inactivity_minutes >= 0),
                    playlist_max_tracks INTEGER NOT NULL CHECK (playlist_max_tracks >= 1),
                    playlist_default_tracks INTEGER NOT NULL CHECK (playlist_default_tracks >= 1),
                    playlist_default_shuffle INTEGER NOT NULL CHECK (playlist_default_shuffle IN (0, 1)),
                    updated_by INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(guild_config)")
            }
            if "slash_commands_enabled" not in columns:
                connection.execute(
                    "ALTER TABLE guild_config ADD COLUMN slash_commands_enabled INTEGER NOT NULL DEFAULT 1 "
                    "CHECK (slash_commands_enabled IN (0, 1))"
                )
            if "prefix_commands_enabled" not in columns:
                connection.execute(
                    "ALTER TABLE guild_config ADD COLUMN prefix_commands_enabled INTEGER NOT NULL DEFAULT 1 "
                    "CHECK (prefix_commands_enabled IN (0, 1))"
                )

    def get(self, guild_id, defaults):
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT command_prefix, slash_commands_enabled, prefix_commands_enabled,
                    empty_channel_enabled, empty_channel_minutes, inactivity_enabled,
                    inactivity_minutes, playlist_max_tracks, playlist_default_tracks,
                    playlist_default_shuffle
                FROM guild_config WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        if not row:
            return defaults.copy()
        config = {
            "command_prefix": row[0],
            "slash_commands_enabled": bool(row[1]) and defaults["slash_commands_enabled"],
            "prefix_commands_enabled": bool(row[2]) and defaults["prefix_commands_enabled"],
            "empty_channel_enabled": bool(row[3]),
            "empty_channel_minutes": row[4],
            "inactivity_enabled": bool(row[5]),
            "inactivity_minutes": row[6],
            "playlist_max_tracks": row[7],
            "playlist_default_tracks": row[8],
            "playlist_default_shuffle": bool(row[9]),
        }
        if not config["slash_commands_enabled"] and not config["prefix_commands_enabled"]:
            config["slash_commands_enabled"] = defaults["slash_commands_enabled"]
            config["prefix_commands_enabled"] = not config["slash_commands_enabled"]
        return config

    def save(self, guild_id, config, updated_by):
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO guild_config (
                    guild_id, command_prefix, slash_commands_enabled, prefix_commands_enabled,
                    empty_channel_enabled, empty_channel_minutes,
                    inactivity_enabled, inactivity_minutes, playlist_max_tracks,
                    playlist_default_tracks, playlist_default_shuffle, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    command_prefix = excluded.command_prefix,
                    slash_commands_enabled = excluded.slash_commands_enabled,
                    prefix_commands_enabled = excluded.prefix_commands_enabled,
                    empty_channel_enabled = excluded.empty_channel_enabled,
                    empty_channel_minutes = excluded.empty_channel_minutes,
                    inactivity_enabled = excluded.inactivity_enabled,
                    inactivity_minutes = excluded.inactivity_minutes,
                    playlist_max_tracks = excluded.playlist_max_tracks,
                    playlist_default_tracks = excluded.playlist_default_tracks,
                    playlist_default_shuffle = excluded.playlist_default_shuffle,
                    updated_by = excluded.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    guild_id,
                    config["command_prefix"],
                    config["slash_commands_enabled"],
                    config["prefix_commands_enabled"],
                    config["empty_channel_enabled"],
                    config["empty_channel_minutes"],
                    config["inactivity_enabled"],
                    config["inactivity_minutes"],
                    config["playlist_max_tracks"],
                    config["playlist_default_tracks"],
                    config["playlist_default_shuffle"],
                    updated_by,
                ),
            )


def create_store_from_environment():
    return GuildConfigStore(os.getenv("GUILD_CONFIG_DATABASE_PATH", "/app/data/guild_config.db"))
