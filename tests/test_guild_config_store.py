from bot.utils.guild_config_store import GuildConfigStore


DEFAULTS = {
    "command_prefix": ".",
    "empty_channel_enabled": True,
    "empty_channel_minutes": 0,
    "inactivity_enabled": True,
    "inactivity_minutes": 10,
    "playlist_max_tracks": 20,
    "playlist_default_tracks": 20,
    "playlist_default_shuffle": False,
}


def test_get_uses_defaults_until_a_guild_override_is_saved(tmp_path):
    store = GuildConfigStore(tmp_path / "guild_config.db")

    assert store.get(123, DEFAULTS) == DEFAULTS

    override = {
        "command_prefix": "!",
        "empty_channel_enabled": False,
        "empty_channel_minutes": 5,
        "inactivity_enabled": True,
        "inactivity_minutes": 30,
        "playlist_max_tracks": 50,
        "playlist_default_tracks": 25,
        "playlist_default_shuffle": True,
    }
    store.save(123, override, 456)

    assert store.get(123, DEFAULTS) == override
    assert store.get(999, DEFAULTS) == DEFAULTS
