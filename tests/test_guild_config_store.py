from bot.utils.guild_config_store import GuildConfigStore


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
