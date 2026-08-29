import asyncio

import discord


def parse_boolean(value):
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1", "on"}:
        return True
    if normalized in {"false", "no", "0", "off"}:
        return False
    raise ValueError("must be true or false")


def parse_policy(value, label):
    enabled_text, separator, minutes_text = value.partition(",")
    if not separator:
        raise ValueError(f"{label} must use true/false,minutes")
    enabled = parse_boolean(enabled_text)
    try:
        minutes = int(minutes_text.strip())
    except ValueError as error:
        raise ValueError(f"{label} minutes must be a whole number") from error
    if minutes < 0:
        raise ValueError(f"{label} minutes cannot be negative")
    return enabled, minutes


class GuildConfigLauncher(discord.ui.View):
    def __init__(self, music_cog, guild_id, owner_id):
        super().__init__(timeout=120)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the server administrator who ran the command can configure this bot.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Edit configuration", emoji="⚙️", style=discord.ButtonStyle.secondary)
    async def open_modal(self, interaction, button):
        await interaction.response.send_modal(GuildConfigModal(self.music_cog, self.guild_id))


class GuildConfigModal(discord.ui.Modal, title="Music configuration"):
    command_prefix = discord.ui.TextInput(label="Command prefix", max_length=10)
    empty_channel = discord.ui.TextInput(label="Empty channel: enabled, minutes", placeholder="true, 0", max_length=20)
    inactivity = discord.ui.TextInput(label="Inactivity: enabled, minutes", placeholder="true, 10", max_length=20)
    playlist = discord.ui.TextInput(label="Playlist: maximum, default, shuffle", placeholder="20, 20, false", max_length=30)

    def __init__(self, music_cog, guild_id):
        super().__init__()
        self.music_cog = music_cog
        self.guild_id = guild_id
        config = music_cog.get_guild_config(guild_id)
        self.command_prefix.default = config["command_prefix"]
        self.empty_channel.default = f"{str(config['empty_channel_enabled']).lower()}, {config['empty_channel_minutes']}"
        self.inactivity.default = f"{str(config['inactivity_enabled']).lower()}, {config['inactivity_minutes']}"
        self.playlist.default = (
            f"{config['playlist_max_tracks']}, {config['playlist_default_tracks']}, "
            f"{str(config['playlist_default_shuffle']).lower()}"
        )

    async def on_submit(self, interaction):
        prefix = self.command_prefix.value.strip()
        if not prefix:
            await interaction.response.send_message("Command prefix cannot be empty.", ephemeral=True)
            return
        try:
            empty_enabled, empty_minutes = parse_policy(self.empty_channel.value, "Empty channel")
            inactivity_enabled, inactivity_minutes = parse_policy(self.inactivity.value, "Inactivity")
            playlist_values = [value.strip() for value in self.playlist.value.split(",")]
            if len(playlist_values) != 3:
                raise ValueError("Playlist must use maximum,default,true/false")
            playlist_max_tracks = int(playlist_values[0])
            playlist_default_tracks = int(playlist_values[1])
            playlist_default_shuffle = parse_boolean(playlist_values[2])
            if playlist_max_tracks < 1 or playlist_default_tracks < 1:
                raise ValueError("Playlist track counts must be at least 1")
            if playlist_default_tracks > playlist_max_tracks:
                raise ValueError("Playlist default cannot exceed the maximum")
        except ValueError as error:
            await interaction.response.send_message(f"Invalid configuration: {error}.", ephemeral=True)
            return

        config = {
            "command_prefix": prefix,
            "empty_channel_enabled": empty_enabled,
            "empty_channel_minutes": empty_minutes,
            "inactivity_enabled": inactivity_enabled,
            "inactivity_minutes": inactivity_minutes,
            "playlist_max_tracks": playlist_max_tracks,
            "playlist_default_tracks": playlist_default_tracks,
            "playlist_default_shuffle": playlist_default_shuffle,
        }
        await asyncio.to_thread(self.music_cog.save_guild_config, self.guild_id, config, interaction.user.id)
        await interaction.response.send_message("Guild music configuration saved.", ephemeral=True)
