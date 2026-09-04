import importlib.util
import sqlite3
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "bot" / "utils" / "guild_config_store.py"
SPEC = importlib.util.spec_from_file_location("guild_config_store_for_tests", MODULE_PATH)
GUILD_CONFIG_STORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GUILD_CONFIG_STORE
SPEC.loader.exec_module(GUILD_CONFIG_STORE)
GuildConfigStore = GUILD_CONFIG_STORE.GuildConfigStore


DEFAULTS = {
    "command_prefix": ".",
    "slash_commands_enabled": True,
    "prefix_commands_enabled": True,
    "empty_channel_enabled": True,
    "empty_channel_minutes": 0,
    "inactivity_enabled": True,
    "inactivity_minutes": 10,
    "playlist_max_tracks": 20,
    "playlist_default_tracks": 20,
    "playlist_default_shuffle": False,
    "rating_history_enabled": True,
    "lastfm_enabled": True,
}


def test_get_uses_defaults_until_a_guild_override_is_saved(tmp_path):
    store = GuildConfigStore(tmp_path / "guild_config.db")

    assert store.get(123, DEFAULTS) == DEFAULTS

    override = {
        "command_prefix": "!",
        "slash_commands_enabled": True,
        "prefix_commands_enabled": False,
        "empty_channel_enabled": False,
        "empty_channel_minutes": 5,
        "inactivity_enabled": True,
        "inactivity_minutes": 30,
        "playlist_max_tracks": 50,
        "playlist_default_tracks": 25,
        "playlist_default_shuffle": True,
        "rating_history_enabled": False,
        "lastfm_enabled": False,
    }
    store.save(123, override, 456)

    assert store.get(123, DEFAULTS) == override
    assert store.get(999, DEFAULTS) == DEFAULTS


def test_get_does_not_reenable_a_command_mode_disabled_by_environment(tmp_path):
    store = GuildConfigStore(tmp_path / "guild_config.db")
    disabled_prefix_defaults = DEFAULTS | {"prefix_commands_enabled": False}
    override = disabled_prefix_defaults | {"prefix_commands_enabled": True}

    store.save(123, override, 456)

    config = store.get(123, disabled_prefix_defaults)
    assert config["slash_commands_enabled"] is True
    assert config["prefix_commands_enabled"] is False


def test_get_does_not_enable_lastfm_when_disabled_by_environment(tmp_path):
    store = GuildConfigStore(tmp_path / "guild_config.db")
    disabled_defaults = DEFAULTS | {"lastfm_enabled": False}
    override = DEFAULTS | {"lastfm_enabled": True}

    store.save(123, override, 456)

    assert store.get(123, disabled_defaults)["lastfm_enabled"] is False


def test_existing_database_adds_lastfm_disabled_by_default(tmp_path):
    database_path = tmp_path / "guild_config.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE guild_config (
                guild_id INTEGER PRIMARY KEY,
                command_prefix TEXT NOT NULL,
                slash_commands_enabled INTEGER NOT NULL DEFAULT 1,
                prefix_commands_enabled INTEGER NOT NULL DEFAULT 1,
                empty_channel_enabled INTEGER NOT NULL,
                empty_channel_minutes INTEGER NOT NULL,
                inactivity_enabled INTEGER NOT NULL,
                inactivity_minutes INTEGER NOT NULL,
                playlist_max_tracks INTEGER NOT NULL,
                playlist_default_tracks INTEGER NOT NULL,
                playlist_default_shuffle INTEGER NOT NULL,
                rating_history_enabled INTEGER NOT NULL DEFAULT 1,
                updated_by INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    store = GuildConfigStore(database_path)

    with store.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(guild_config)")}
        default_value = connection.execute(
            "SELECT dflt_value FROM pragma_table_info('guild_config') WHERE name = 'lastfm_enabled'"
        ).fetchone()[0]
    assert "lastfm_enabled" in columns
    assert default_value == "0"
