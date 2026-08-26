import os
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SpotifyStoreError(Exception):
    pass


class SpotifyStore:
    def __init__(self, database_path, encryption_key):
        if not encryption_key:
            raise SpotifyStoreError("SPOTIFY_CREDENTIAL_ENCRYPTION_KEY is not configured")
        try:
            self.fernet = Fernet(encryption_key.encode())
        except (TypeError, ValueError) as error:
            raise SpotifyStoreError("SPOTIFY_CREDENTIAL_ENCRYPTION_KEY is invalid") from error

        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        return sqlite3.connect(self.database_path)

    def initialize(self):
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS spotify_credentials (
                    guild_id INTEGER PRIMARY KEY,
                    client_id BLOB NOT NULL,
                    client_secret BLOB NOT NULL,
                    updated_by INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS spotify_playlist_tokens (
                    guild_id INTEGER PRIMARY KEY,
                    access_token BLOB NOT NULL,
                    refresh_token BLOB NOT NULL,
                    expires_at INTEGER NOT NULL,
                    authorized_by INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get_credentials(self, guild_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT client_id, client_secret FROM spotify_credentials WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return tuple(self.fernet.decrypt(value).decode() for value in row)
        except InvalidToken as error:
            raise SpotifyStoreError("Stored Spotify credentials cannot be decrypted") from error

    def save_credentials(self, guild_id, client_id, client_secret, updated_by):
        encrypted_id = self.fernet.encrypt(client_id.encode())
        encrypted_secret = self.fernet.encrypt(client_secret.encode())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO spotify_credentials (guild_id, client_id, client_secret, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    client_id = excluded.client_id,
                    client_secret = excluded.client_secret,
                    updated_by = excluded.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (guild_id, encrypted_id, encrypted_secret, updated_by),
            )

    def clear_credentials(self, guild_id):
        with self.connect() as connection:
            credentials_deleted = connection.execute(
                "DELETE FROM spotify_credentials WHERE guild_id = ?", (guild_id,)
            ).rowcount > 0
            connection.execute("DELETE FROM spotify_playlist_tokens WHERE guild_id = ?", (guild_id,))
            return credentials_deleted

    def get_playlist_token(self, guild_id):
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT access_token, refresh_token, expires_at
                FROM spotify_playlist_tokens WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        if not row:
            return None
        try:
            access_token, refresh_token = (self.fernet.decrypt(value).decode() for value in row[:2])
        except InvalidToken as error:
            raise SpotifyStoreError("Stored Spotify playlist authorization cannot be decrypted") from error
        return access_token, refresh_token, row[2]

    def get_playlist_authorizer(self, guild_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT authorized_by FROM spotify_playlist_tokens WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        return row[0] if row else None

    def save_playlist_token(self, guild_id, access_token, refresh_token, expires_at, authorized_by):
        encrypted_access = self.fernet.encrypt(access_token.encode())
        encrypted_refresh = self.fernet.encrypt(refresh_token.encode())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO spotify_playlist_tokens
                    (guild_id, access_token, refresh_token, expires_at, authorized_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    authorized_by = excluded.authorized_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (guild_id, encrypted_access, encrypted_refresh, expires_at, authorized_by),
            )

    def status(self, guild_id):
        with self.connect() as connection:
            return connection.execute(
                "SELECT updated_by, updated_at FROM spotify_credentials WHERE guild_id = ?", (guild_id,)
            ).fetchone()


def create_store_from_environment():
    return SpotifyStore(
        os.getenv("SPOTIFY_DATABASE_PATH", "/app/data/spotify.db"),
        os.getenv("SPOTIFY_CREDENTIAL_ENCRYPTION_KEY"),
    )