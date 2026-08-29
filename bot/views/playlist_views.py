import discord

from bot.utils.spotify_client import SpotifyError, parse_playlist_options


class PlaylistImportCancelView(discord.ui.View):
    def __init__(self, owner_id, guild_id):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.cancelled = False
        self.task = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who started this import can cancel it.", ephemeral=True)
            return False
        voice = interaction.guild.voice_client if interaction.guild else None
        user_voice = getattr(interaction.user, "voice", None)
        if not voice or not user_voice or user_voice.channel != voice.channel:
            await interaction.response.send_message("Join my voice channel before cancelling this import.", ephemeral=True)
            return False
        return True

    def finish(self):
        for child in self.children:
            child.disabled = True
        self.stop()

    @discord.ui.button(label="Cancel import", emoji="🛑", style=discord.ButtonStyle.danger)
    async def cancel_import(self, interaction, button):
        self.cancelled = True
        self.finish()
        await interaction.response.edit_message(
            content="🛑 **Cancelling playlist import**\nThe current batch will finish; no further tracks will be added.",
            view=self,
        )


class SpotifyPlaylistLauncher(discord.ui.View):
    def __init__(self, music_cog, ctx, resource):
        super().__init__(timeout=300)
        self.music_cog = music_cog
        self.ctx = ctx
        self.resource = resource
        self.owner_id = ctx.author.id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who requested this playlist can configure it.", ephemeral=True)
            return False
        user_voice = getattr(interaction.user, "voice", None)
        if not user_voice:
            await interaction.response.send_message("Join a voice channel before importing this playlist.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Configure import", emoji="🎵", style=discord.ButtonStyle.secondary)
    async def configure(self, interaction, button):
        await interaction.response.send_modal(SpotifyPlaylistModal(self.music_cog, self.ctx, self.resource))


class SpotifyPlaylistModal(discord.ui.Modal, title="Spotify playlist import"):
    count = discord.ui.TextInput(label="Track count", placeholder="1-20", default="20", max_length=2)
    range_value = discord.ui.TextInput(
        label="Positions or ranges (optional)", placeholder="1-3,5,7,9-10", required=False, max_length=100
    )
    ordering = discord.ui.TextInput(
        label="Ordering: ordered or shuffle",
        placeholder="ordered = Spotify order; shuffle = random",
        default="ordered",
        max_length=7,
    )

    def __init__(self, music_cog, ctx, resource):
        super().__init__()
        self.music_cog = music_cog
        self.ctx = ctx
        self.resource = resource
        max_tracks = music_cog.spotify_max_tracks
        self.count.placeholder = f"1-{max_tracks}"
        self.count.default = str(music_cog.spotify_default_tracks)
        self.count.max_length = len(str(max_tracks))
        self.ordering.default = "shuffle" if music_cog.spotify_default_shuffle else "ordered"

    async def on_submit(self, interaction):
        arguments = f"--count {self.count.value} --{self.ordering.value.strip().lower()}"
        if self.range_value.value.strip():
            arguments += f" --range {self.range_value.value.strip()}"
        try:
            options = parse_playlist_options(
                arguments,
                self.music_cog.spotify_max_tracks,
                self.music_cog.spotify_default_tracks,
                self.music_cog.spotify_default_shuffle,
            )
        except (SpotifyError, ValueError) as error:
            await interaction.response.send_message(f"Invalid playlist configuration: {error}", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.music_cog.import_spotify(self.ctx, self.resource, options)
        await interaction.followup.send("Playlist import started in the channel.", ephemeral=True)


class YouTubePlaylistLauncher(discord.ui.View):
    def __init__(self, music_cog, ctx, playlist_url):
        super().__init__(timeout=300)
        self.music_cog = music_cog
        self.ctx = ctx
        self.playlist_url = playlist_url
        self.owner_id = ctx.author.id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who requested this playlist can configure it.", ephemeral=True)
            return False
        user_voice = getattr(interaction.user, "voice", None)
        if not user_voice:
            await interaction.response.send_message("Join a voice channel before importing this playlist.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Configure import", emoji="🎵", style=discord.ButtonStyle.secondary)
    async def configure(self, interaction, button):
        await interaction.response.send_modal(YouTubePlaylistModal(self.music_cog, self.ctx, self.playlist_url))


class YouTubePlaylistModal(SpotifyPlaylistModal, title="YouTube playlist import"):
    def __init__(self, music_cog, ctx, playlist_url):
        super().__init__(music_cog, ctx, playlist_url)
        self.playlist_url = playlist_url

    async def on_submit(self, interaction):
        arguments = f"--count {self.count.value} --{self.ordering.value.strip().lower()}"
        if self.range_value.value.strip():
            arguments += f" --range {self.range_value.value.strip()}"
        try:
            options = parse_playlist_options(
                arguments,
                self.music_cog.spotify_max_tracks,
                self.music_cog.spotify_default_tracks,
                self.music_cog.spotify_default_shuffle,
            )
        except (SpotifyError, ValueError) as error:
            await interaction.response.send_message(f"Invalid playlist configuration: {error}", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.music_cog.import_youtube_playlist(self.ctx, self.playlist_url, options)
        await interaction.followup.send("Playlist import started in the channel.", ephemeral=True)
