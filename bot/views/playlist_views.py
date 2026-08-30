import discord

from bot.utils.spotify_client import SpotifyError, parse_playlist_options


class PersonalSpotifySetupView(discord.ui.View):
    def __init__(self, music_cog, ctx, reason, retry):
        super().__init__(timeout=300)
        self.music_cog = music_cog
        self.ctx = ctx
        self.reason = reason
        self.retry = retry
        self.owner_id = ctx.author.id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who made this request can configure personal Spotify access.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Use my Spotify", emoji="🎵", style=discord.ButtonStyle.primary)
    async def use_personal_spotify(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        credentials = await self.music_cog.configure_personal_spotify(self.ctx)
        if not credentials:
            await interaction.edit_original_response(content="Personal Spotify setup was not completed. Nothing was queued.", view=None)
            return
        await interaction.edit_original_response(content="Personal Spotify access is configured. Continuing your request...", view=None)
        await self.retry(credentials)

    @discord.ui.button(label="Cancel", emoji="✖", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Spotify setup cancelled.", view=self)


class PlaylistImportCancelView(discord.ui.View):
    def __init__(self, owner_id, guild_id, require_voice=True):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.require_voice = require_voice
        self.cancelled = False
        self.task = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who started this import can cancel it.", ephemeral=True)
            return False
        if self.require_voice:
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
    def __init__(self, music_cog, ctx, resource, tracks):
        super().__init__(timeout=300)
        self.music_cog = music_cog
        self.ctx = ctx
        self.resource = resource
        self.tracks = tracks
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
        await interaction.response.send_modal(SpotifyPlaylistModal(self.music_cog, self.ctx, self.resource, self.tracks))


class SpotifyPlaylistModal(discord.ui.Modal, title="Spotify playlist import"):
    count = discord.ui.TextInput(label="Track count", placeholder="1-20", default="20", max_length=2)
    range_value = discord.ui.TextInput(
        label="Positions or ranges (optional)", placeholder="1-3,5,7,9-10,40-", required=False, max_length=100
    )
    ordering = discord.ui.TextInput(
        label="Ordering: ordered or shuffle",
        placeholder="ordered = Spotify order; shuffle = random",
        default="ordered",
        max_length=7,
    )

    def __init__(self, music_cog, ctx, resource, tracks):
        super().__init__()
        self.music_cog = music_cog
        self.ctx = ctx
        self.resource = resource
        self.tracks = tracks
        self.config = music_cog.get_guild_config(ctx.guild.id)
        max_tracks = self.config["playlist_max_tracks"]
        self.count.label = f"Track count (of {len(tracks)})"
        self.count.placeholder = f"1-{max_tracks}"
        self.count.default = str(self.config["playlist_default_tracks"])
        self.count.max_length = len(str(max_tracks))
        self.ordering.default = "shuffle" if self.config["playlist_default_shuffle"] else "ordered"

    async def on_submit(self, interaction):
        arguments = f"--count {self.count.value} --{self.ordering.value.strip().lower()}"
        if self.range_value.value.strip():
            arguments += f" --range {self.range_value.value.strip()}"
        try:
            options = parse_playlist_options(
                arguments,
                self.config["playlist_max_tracks"],
                self.config["playlist_default_tracks"],
                self.config["playlist_default_shuffle"],
            )
        except (SpotifyError, ValueError) as error:
            await interaction.response.send_message(f"Invalid playlist configuration: {error}", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.music_cog.import_spotify(self.ctx, self.resource, options, tracks=self.tracks)
        await interaction.followup.send("Playlist import started in the channel.", ephemeral=True)


class YouTubePlaylistLauncher(discord.ui.View):
    def __init__(self, music_cog, ctx, playlist_url, entries):
        super().__init__(timeout=300)
        self.music_cog = music_cog
        self.ctx = ctx
        self.playlist_url = playlist_url
        self.entries = entries
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
        await interaction.response.send_modal(YouTubePlaylistModal(self.music_cog, self.ctx, self.playlist_url, self.entries))


class YouTubePlaylistModal(SpotifyPlaylistModal, title="YouTube playlist import"):
    def __init__(self, music_cog, ctx, playlist_url, entries):
        super().__init__(music_cog, ctx, playlist_url, entries)
        self.playlist_url = playlist_url

    async def on_submit(self, interaction):
        arguments = f"--count {self.count.value} --{self.ordering.value.strip().lower()}"
        if self.range_value.value.strip():
            arguments += f" --range {self.range_value.value.strip()}"
        try:
            options = parse_playlist_options(
                arguments,
                self.config["playlist_max_tracks"],
                self.config["playlist_default_tracks"],
                self.config["playlist_default_shuffle"],
            )
        except (SpotifyError, ValueError) as error:
            await interaction.response.send_message(f"Invalid playlist configuration: {error}", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.music_cog.import_youtube_playlist(self.ctx, self.playlist_url, options, entries=self.tracks)
        await interaction.followup.send("Playlist import started in the channel.", ephemeral=True)


class SavedPlaylistLauncher(discord.ui.View):
    def __init__(self, music_cog, ctx, playlist_name, source, service, maximum_tracks):
        super().__init__(timeout=300)
        self.music_cog = music_cog
        self.ctx = ctx
        self.playlist_name = playlist_name
        self.source = source
        self.service = service
        self.maximum_tracks = maximum_tracks
        self.owner_id = ctx.author.id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who added this playlist can configure it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Configure tracks", emoji="🎵", style=discord.ButtonStyle.secondary)
    async def configure(self, interaction, button):
        await interaction.response.send_modal(SavedPlaylistModal(self))


class SavedPlaylistModal(discord.ui.Modal, title="Save playlist tracks"):
    count = discord.ui.TextInput(label="Track count", placeholder="1-20", default="20", max_length=2)
    range_value = discord.ui.TextInput(
        label="Positions or ranges (optional)", placeholder="1-3,5,7,9-10,40-", required=False, max_length=100
    )
    ordering = discord.ui.TextInput(
        label="Ordering: ordered or shuffle",
        placeholder="ordered = source order; shuffle = random",
        default="ordered",
        max_length=7,
    )

    def __init__(self, launcher):
        super().__init__()
        self.launcher = launcher
        config = launcher.music_cog.get_guild_config(launcher.ctx.guild.id)
        maximum_tracks = min(config["playlist_max_tracks"], launcher.maximum_tracks)
        self.maximum_tracks = maximum_tracks
        self.default_tracks = min(config["playlist_default_tracks"], maximum_tracks)
        self.default_shuffle = config["playlist_default_shuffle"]
        self.count.placeholder = f"1-{maximum_tracks}"
        self.count.default = str(self.default_tracks)
        self.count.max_length = len(str(maximum_tracks))
        self.ordering.default = "shuffle" if self.default_shuffle else "ordered"

    async def on_submit(self, interaction):
        arguments = f"--count {self.count.value} --{self.ordering.value.strip().lower()}"
        if self.range_value.value.strip():
            arguments += f" --range {self.range_value.value.strip()}"
        try:
            options = parse_playlist_options(
                arguments,
                self.maximum_tracks,
                self.default_tracks,
                self.default_shuffle,
            )
        except (SpotifyError, ValueError) as error:
            await interaction.response.send_message(f"Invalid playlist configuration: {error}", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.launcher.music_cog.start_save_user_playlist_source(
            self.launcher.ctx, self.launcher.playlist_name, self.launcher.source, options, interaction
        )


class SavedPlaylistResultPaginator(discord.ui.View):
    def __init__(self, owner_id, pages):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.pages = pages
        self.page_index = 0
        self.update_buttons()

    def update_buttons(self):
        self.previous.disabled = self.page_index == 0
        self.next.disabled = self.page_index >= len(self.pages) - 1

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who saved these songs can change pages.", ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        self.page_index -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page_index], view=self)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        self.page_index += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page_index], view=self)


class UserPlaylistPlayLauncher(discord.ui.View):
    def __init__(self, music_cog, ctx, owner, playlist_name, maximum_tracks, track_count):
        super().__init__(timeout=300)
        self.music_cog = music_cog
        self.ctx = ctx
        self.owner = owner
        self.playlist_name = playlist_name
        self.maximum_tracks = maximum_tracks
        self.track_count = track_count
        self.owner_id = ctx.author.id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who requested this playlist can configure playback.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Configure playback", emoji="🎵", style=discord.ButtonStyle.secondary)
    async def configure(self, interaction, button):
        await interaction.response.send_modal(UserPlaylistPlayModal(self))


class UserPlaylistPlayModal(discord.ui.Modal, title="Play saved playlist"):
    count = discord.ui.TextInput(label="Track count", placeholder="1-20", default="20", max_length=2)
    range_value = discord.ui.TextInput(
        label="Positions or ranges (optional)", placeholder="1-3,5,7,9-10,40-", required=False, max_length=100
    )
    ordering = discord.ui.TextInput(
        label="Ordering: ordered or shuffle",
        placeholder="ordered = saved order; shuffle = random",
        default="ordered",
        max_length=7,
    )

    def __init__(self, launcher):
        super().__init__()
        self.launcher = launcher
        config = launcher.music_cog.get_guild_config(launcher.ctx.guild.id)
        self.maximum_tracks = min(config["playlist_max_tracks"], launcher.maximum_tracks)
        self.default_tracks = min(config["playlist_default_tracks"], self.maximum_tracks)
        self.default_shuffle = config["playlist_default_shuffle"]
        self.count.label = f"Track count (of {launcher.track_count})"
        self.count.placeholder = f"1-{self.maximum_tracks}"
        self.count.default = str(self.default_tracks)
        self.count.max_length = len(str(self.maximum_tracks))
        self.ordering.default = "shuffle" if self.default_shuffle else "ordered"

    async def on_submit(self, interaction):
        arguments = f"--count {self.count.value} --{self.ordering.value.strip().lower()}"
        if self.range_value.value.strip():
            arguments += f" --range {self.range_value.value.strip()}"
        try:
            options = parse_playlist_options(
                arguments,
                self.maximum_tracks,
                self.default_tracks,
                self.default_shuffle,
            )
        except (SpotifyError, ValueError) as error:
            await interaction.response.send_message(f"Invalid playlist configuration: {error}", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.launcher.music_cog.start_queue_user_playlist(
            self.launcher.ctx, self.launcher.owner, self.launcher.playlist_name, options, interaction
        )
