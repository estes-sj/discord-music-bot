import asyncio
import io
import logging
import logging.handlers
import re
import os
import secrets
import time
from urllib.parse import parse_qs, urlparse

import requests

import discord
from discord.ext import commands
from discord import app_commands

import yt_dlp as youtube_dl
from PIL import Image

import bot.utils.custom_paginator as Paginator
import bot.utils.music_utilities as Utilities
from bot.views.playback_views import MusicControls
from bot.views.config_views import GuildConfigLauncher, GuildConfigModal
from bot.views.playlist_views import (
    PlaylistImportCancelView,
    PersonalSpotifySetupView,
    SavedPlaylistLauncher,
    SavedPlaylistResultPaginator,
    SpotifyPlaylistLauncher,
    YouTubePlaylistLauncher,
    UserPlaylistPlayLauncher,
)
from bot.views.queue_views import (
    QueuedTrackControls,
    QueueRemoveLauncher,
    QueueMoveLauncher,
)
from bot.views.search_views import YouTubeSearchDropdown
from bot.utils.spotify_client import (
    SpotifyClient,
    SpotifyCredentialsError,
    SpotifyError,
    SpotifyPlaylistAuthorizationError,
    parse_playlist_options,
    parse_resource,
    select_tracks,
    track_query,
)
from bot.utils.spotify_store import SpotifyStoreError
from bot.utils.user_playlist_store import UserPlaylistStore

# List of active sessions.
sessions = []

# YouTube will sometimes try to disconnect the bot from its servers. Use this to reconnect instantly.
# (Because of this disconnect/reconnect cycle, sometimes you will listen a sudden and brief stop)
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

# To avoid large load times for long playlists, do an initial load of the set # of tracks, then load the rest in the background.
PLAYLIST_INITIAL_BATCH_SIZE = 5
# After each # loaded, update the progress message to the user.
PLAYLIST_PROGRESS_BATCH_SIZE = 5
YTDLP_TIMEOUT_SECONDS = 45
PLAY_COOLDOWN_SECONDS = 0
SEARCH_COOLDOWN_SECONDS = 1

logger = logging.getLogger("discord")

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.spotify_import_locks = {}
        self.active_playlist_imports = {}
        self.active_expensive_commands = set()
        self.command_cooldowns = {}
        self.stream_url_cache = {}
        self.spotify_store = getattr(self.bot, "spotify_store", None)
        self.song_stats_store = getattr(self.bot, "song_stats_store", None)
        self.user_playlist_store = getattr(self.bot, "user_playlist_store", None)
        self.guild_config_store = getattr(self.bot, "guild_config_store", None)
        if self.spotify_store is None:
            logger.warning("Spotify support is not configured for this bot.")
        if self.song_stats_store is None:
            logger.warning("Song statistics are not configured for this bot.")
        if self.user_playlist_store is None:
            logger.warning("User playlists are not configured for this bot.")


    def get_guild_config(self, guild_id):
        defaults = self.bot.guild_config_defaults
        if not self.guild_config_store:
            return defaults.copy()
        return self.guild_config_store.get(guild_id, defaults)

    def save_guild_config(self, guild_id, config, updated_by):
        if not self.guild_config_store:
            raise RuntimeError("Guild configuration storage is unavailable")
        self.guild_config_store.save(guild_id, config, updated_by)

    def get_session_for_guild(self, guild_id):
        return next((session for session in sessions if session.guild == guild_id), None)

    async def add_reaction(self, ctx, emoji):
        if not ctx.interaction:
            await ctx.message.add_reaction(emoji)

    async def enforce_command_cooldown(self, ctx, command_name, cooldown_seconds):
        key = (ctx.guild.id, ctx.author.id, command_name)
        now = time.monotonic()
        available_at = self.command_cooldowns.get(key, 0)
        if now < available_at:
            logger.info(
                "Guild %s: rejected %s cooldown for user %s",
                ctx.guild.id,
                command_name,
                ctx.author.id,
            )
            await ctx.send(
                f"Please wait {max(1, int(available_at - now) + 1)} seconds before using `/{command_name}` again.",
                ephemeral=bool(ctx.interaction),
            )
            return False
        self.command_cooldowns[key] = now + cooldown_seconds
        return True

    async def claim_expensive_command(self, ctx, command_name):
        key = (ctx.guild.id, ctx.author.id, command_name)
        if key in self.active_expensive_commands:
            logger.info(
                "Guild %s: rejected overlapping %s for user %s",
                ctx.guild.id,
                command_name,
                ctx.author.id,
            )
            await ctx.send(
                f"Your previous `/{command_name}` request is still processing. Wait for it to finish before trying again.",
                ephemeral=bool(ctx.interaction),
            )
            return False
        self.active_expensive_commands.add(key)
        logger.info("Guild %s: accepted %s for user %s", ctx.guild.id, command_name, ctx.author.id)
        return True

    def release_expensive_command(self, ctx, command_name):
        self.active_expensive_commands.discard((ctx.guild.id, ctx.author.id, command_name))

    async def claim_playlist_import(self, ctx):
        guild_id = ctx.guild.id
        active_imports = self.active_playlist_imports.get(guild_id, 0)
        import_limit = getattr(self.bot, "playlist_import_concurrency_per_guild", 1)
        if active_imports >= import_limit:
            await ctx.send(
                "The maximum number of playlist imports is already running in this server. "
                "Wait for one to finish or cancel it before starting another.",
                ephemeral=bool(ctx.interaction),
            )
            return False
        self.active_playlist_imports[guild_id] = active_imports + 1
        return True

    def release_playlist_import(self, guild_id):
        active_imports = self.active_playlist_imports.get(guild_id, 0)
        if active_imports <= 1:
            self.active_playlist_imports.pop(guild_id, None)
        else:
            self.active_playlist_imports[guild_id] = active_imports - 1

    async def get_session(self, ctx):
        """
        Retrieves the session (or creates if none) for the current guild and voice channel.
        Updates the session's channel if the bot was moved.
        Prevents multiple instances in the same guild but different channels.

        :param ctx: discord.ext.commands.Context
        :return: session() or None if an error occurs
        """
        for session in sessions:
            if session.guild == ctx.guild.id:
                # Get bot's actual voice channel
                bot_voice_state = ctx.guild.me.voice
                if bot_voice_state and bot_voice_state.channel:
                    actual_channel_id = bot_voice_state.channel.id

                    # Update session if bot moved
                    if session.channel != actual_channel_id:
                        session.channel = actual_channel_id

                # Ensure session matches user's current voice channel
                if session.channel == ctx.author.voice.channel.id:
                    return session
                else:
                    await ctx.send("⚠️ *There is already an active session in this server. Multiple sessions in different channels are not supported.*")
                    await self.add_reaction(ctx, "😵")
                    return None  # Prevents creating a new session
        
        session = Utilities.Session(ctx.guild.id, ctx.author.voice.channel.id)
        sessions.append(session)
        return session

    async def get_session_in_guild(self, ctx):
        """
        Similar to get_session() but for grabbing the guild's session regardless of channel ID.
        Updates the session's channel if the bot has moved.

        Checks and gets the session if there is a session with the same guild as ctx.
        If there is no session in the guild, creates a new one.
        Prevents multiple instances in the same guild but different voice channels, sending an error when this occurs.

        :param ctx: discord.ext.commands.Context
        :return: session() or None if an error occurs
        """
        for session in sessions:
            if session.guild == ctx.guild.id:
                # Get bot's actual voice channel
                bot_voice_state = ctx.guild.me.voice
                if bot_voice_state and bot_voice_state.channel:
                    actual_channel_id = bot_voice_state.channel.id

                    # Update session if bot moved
                    if session.channel != actual_channel_id:
                        session.channel = actual_channel_id

                return session
        
        # Create new session if none exists
        session = Utilities.Session(ctx.guild.id, ctx.author.voice.channel.id)
        sessions.append(session)
        return session

    def prepare_continue_queue(self, ctx, completed_track, error):
        """
        Schedules queue continuation after the current track ends normally.

        :param ctx: discord.ext.commands.Context
        """
        logger.info("Guild %s: scheduling music queue continuation", ctx.guild.id)
        fut = asyncio.run_coroutine_threadsafe(
            self.continue_queue(ctx, completed_track, error), self.bot.loop
        )
        try:
            fut.result()
        except Exception as e:
            logger.error("Guild %s: failed to continue the music queue: %s", ctx.guild.id, e)

    async def retire_now_playing_controls(self, session):
        """Disable all now-playing controls and clear their session references."""
        messages = list(session.now_playing_messages.values())
        session.now_playing_message = None
        session.now_playing_track_url = None
        session.now_playing_messages.clear()
        for message, track_url in messages:
            controls = MusicControls(self, session.guild, track_url)
            for child in controls.children:
                child.disabled = True
            try:
                await message.edit(view=controls)
            except (discord.Forbidden, discord.NotFound):
                pass

    def forget_queued_track_controls(self, session, track):
        session.queued_track_messages.pop(id(track), None)

    async def retire_queued_track_controls(self, session, track):
        entry = session.queued_track_messages.pop(id(track), None)
        if not entry:
            return
        _, message = entry
        controls = QueuedTrackControls(self, session.guild, track)
        for child in controls.children:
            child.disabled = True
        try:
            await message.edit(view=controls)
        except (discord.Forbidden, discord.NotFound):
            pass

    async def retire_queued_track_controls_except(self, session, kept_track=None):
        for track, _ in list(session.queued_track_messages.values()):
            if track is not kept_track:
                await self.retire_queued_track_controls(session, track)

    async def continue_queue(self, ctx, completed_track, error):
        """
        Plays the next song in the queue if available.

        :param ctx: discord.ext.commands.Context
        """
        session = await self.get_session_in_guild(ctx)
        if session is None:
            return

        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if (
            error
            or session.q.is_empty()
            or session.q.current_music != completed_track
            or not voice
            or not voice.is_connected()
            or session.q.continuation_pending
        ):
            if error:
                logger.warning("Guild %s: audio playback ended with error: %s", ctx.guild.id, error)
                await self.retire_now_playing_controls(session)
            return

        session.q.continuation_pending = True
        try:
            if session.q.restart_requested:
                restart_position = session.q.restart_position
                session.q.restart_requested = False
                session.q.restart_position = 0
                await self.play_current_track(ctx, session, restart_position, record_play=False)
                return
            if not session.q.theres_next():
                if session.q.loop_current and not session.q.skip_requested:
                    await self.play_current_track(ctx, session)
                    return
                await self.retire_now_playing_controls(session)
                await ctx.channel.send("*Queue has ended* ✅")
                await asyncio.sleep(0)
                if session in sessions:
                    sessions.remove(session)
                return

            if session.q.loop_current and not session.q.skip_requested:
                await self.play_current_track(ctx, session)
                return

            await self.retire_now_playing_controls(session)
            session.q.next()
            await self.retire_queued_track_controls(session, session.q.current_music)
            logger.info("Guild %s: continuing with %s", ctx.guild.id, session.q.current_music.title)
            await self.play_current_track(ctx, session)
        finally:
            session.q.skip_requested = False
            session.q.continuation_pending = False

    async def play_current_track(self, ctx, session, start_position=0, record_play=True):
        """Start the session's current track and send its now-playing controls."""
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        current_track = session.q.current_music
        try:
            stream_info = await self.resolve_stream_from_source(current_track.ytube)
            stream_url = stream_info["url"]
        except (asyncio.TimeoutError, youtube_dl.utils.DownloadError, KeyError) as error:
            logger.warning(
                "Guild %s: could not refresh stream URL for %s: %s",
                ctx.guild.id,
                current_track.title,
                error,
            )
            await ctx.channel.send(
                f"Could not refresh the stream for **{escape_markdown(truncate_text(current_track.title, 150))}**; skipping it."
            )
            session.q.skip_requested = True
            await self.continue_queue(ctx, current_track, None)
            return
        ffmpeg_options = FFMPEG_OPTIONS.copy()
        if start_position:
            ffmpeg_options["before_options"] += f" -ss {start_position:.3f}"
        source = await discord.FFmpegOpusAudio.from_probe(stream_url, **ffmpeg_options)

        completed_track = current_track
        voice.play(
            source,
            after=lambda error: self.prepare_continue_queue(ctx, completed_track, error),
        )
        session.q.start_playback(start_position)
        if record_play and self.song_stats_store:
            try:
                await asyncio.to_thread(
                    self.song_stats_store.record_play,
                    ctx.guild.id,
                    completed_track.ytube,
                    completed_track.title,
                    completed_track.thumb,
                )
            except Exception as error:
                logger.warning("Guild %s: failed to record song play: %s", ctx.guild.id, error)

        # Convert duration to HH:MM:SS format
        duration = session.q.current_music.duration
        duration_str = await convert_duration_pretty(duration)

        # Get dominant color from thumbnail
        dominant_color = await get_dominant_color(session.q.current_music.thumb)

        # Create an embed with the song details
        embed = discord.Embed(
            title=f'{escape_markdown(truncate_text(session.q.current_music.title))}',
            url=session.q.current_music.ytube,
            color=discord.Color(dominant_color),
            description=(
                f"*▶️ Now playing in <#{session.channel}>*"
            )
        )
        embed.set_thumbnail(url=session.q.current_music.thumb)
        embed.set_author(name="Music Stream Link", url=session.q.current_music.url)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Added By", value=f"<@{ctx.author.id}>", inline=True)

        controls = MusicControls(
            self,
            ctx.guild.id,
            completed_track.ytube,
            completed_track.duration,
            start_position,
        )
        if (
            session.now_playing_message
            and session.now_playing_track_url == completed_track.ytube
        ):
            try:
                controls.message = session.now_playing_message
                await session.now_playing_message.edit(embed=embed, view=controls)
                return
            except (discord.Forbidden, discord.NotFound):
                session.now_playing_message = None
                session.now_playing_track_url = None

        session.now_playing_message = await ctx.channel.send(embed=embed, view=controls)
        controls.message = session.now_playing_message
        session.now_playing_track_url = completed_track.ytube
        session.now_playing_messages[id(session.now_playing_message)] = (
            session.now_playing_message,
            completed_track.ytube,
        )

    @staticmethod
    def parse_seek_position(value):
        match = re.fullmatch(r"([+-]?)(\d+)(?::(\d{1,2}))?(?::(\d{1,2}))?", value.strip())
        if not match:
            raise ValueError("use seconds, MM:SS, or HH:MM:SS")
        sign, first, second, third = match.groups()
        if third is not None:
            hours, minutes, seconds = int(first), int(second), int(third)
        elif second is not None:
            hours, minutes, seconds = 0, int(first), int(second)
        else:
            hours, minutes, seconds = 0, 0, int(first)
        if minutes >= 60 or seconds >= 60:
            raise ValueError("minutes and seconds must be below 60")
        position = hours * 3600 + minutes * 60 + seconds
        return sign, position

    @staticmethod
    def format_seek_position(position):
        hours, remainder = divmod(max(0, int(position)), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    async def seek_current_track(self, ctx, value):
        if not await self.ensure_user_in_voice(ctx) or not await self.ensure_bot_in_voice(ctx):
            return
        session = await self.get_session(ctx)
        if session is None:
            return
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if not voice or not (voice.is_playing() or voice.is_paused()):
            await ctx.send("*There is no audio currently playing.*")
            return
        try:
            sign, position = self.parse_seek_position(value)
        except ValueError as error:
            await ctx.send(f"Invalid seek position: {error}.")
            return
        current_position = session.q.current_position()
        target_position = current_position + position if sign == "+" else current_position - position if sign == "-" else position
        duration = session.q.current_music.duration
        if target_position < 0 or (duration and target_position >= duration):
            if duration:
                last_position = max(0, int(duration) - 1)
                await ctx.send(
                    "That seek position is outside the current song. "
                    f"Valid range: `00:00:00` to `{self.format_seek_position(last_position)}`."
                )
            else:
                await ctx.send("That seek position is before the start of the current song. Valid range starts at `00:00:00`.")
            return
        session.q.restart_requested = True
        session.q.restart_position = target_position
        voice.stop()
        await ctx.send(f"Seeked to `{self.format_seek_position(target_position)}`.")

    async def auto_disconnect(self, ctx, voice):
        """
        Automatically disconnects according to the configured empty-channel and
        playback-inactivity policies.
        """
        check_interval = 15
        empty_channel_elapsed = 0
        inactivity_elapsed = 0

        while True:
            await asyncio.sleep(check_interval)

            # Check if bot is not in the voice channel
            if not voice.is_connected():
                break

            config = self.get_guild_config(ctx.guild.id)
            empty_channel_duration = config["empty_channel_minutes"] * 60
            inactivity_duration = config["inactivity_minutes"] * 60

            # Check if voice channel is empty
            if config["empty_channel_enabled"] and len(voice.channel.members) == 1:
                empty_channel_elapsed += check_interval
                if empty_channel_elapsed >= empty_channel_duration:
                    await ctx.send("👋 *No one is in the channel. Disconnecting...*")
                    await voice.disconnect()

                    session = await self.get_session_in_guild(ctx)
                    if session is None:
                        return
                    await self.retire_now_playing_controls(session)
                    await self.retire_queued_track_controls_except(session)
                    await asyncio.sleep(0)
                    if session in sessions:
                        sessions.remove(session)
                    break
            else:
                empty_channel_elapsed = 0

            # Check if nothing is playing
            if config["inactivity_enabled"] and not voice.is_playing() and not voice.is_paused():
                inactivity_elapsed += check_interval
                if inactivity_elapsed >= inactivity_duration:
                    await ctx.send(
                        f"🔇 *No activity detected for {config['inactivity_minutes']} minutes. Disconnecting...*"
                    )
                    await voice.disconnect()

                    session = await self.get_session_in_guild(ctx)
                    if session is None:
                        return
                    await self.retire_now_playing_controls(session)
                    await self.retire_queued_track_controls_except(session)
                    await asyncio.sleep(0)
                    if session in sessions:
                        sessions.remove(session)
                    break
            else:
                inactivity_elapsed = 0

    async def ensure_user_in_voice(self, ctx):
        """
        Ensures that the user issuing the command is in a voice channel.

        :param ctx: The command context.
        :return: True if the user is in a voice channel, False otherwise.
        """
        if not ctx.author.voice:
            await ctx.send("*You are not connected to a voice channel.*")
            await self.add_reaction(ctx, "❌")
            return False
        return True

    async def ensure_bot_in_voice(self, ctx):
        """
        Ensures that the bot is connected to a voice channel.

        :param ctx: The command context.
        :return: True if the bot is in a voice channel, False otherwise.
        """
        voice = ctx.voice_client
        if not voice or not voice.is_connected():
            await ctx.send("*The bot is not connected to a voice channel.*")
            await self.add_reaction(ctx, "🙅‍♂️")
            return False
        return True

    async def configure_guild_spotify(self, ctx):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            await ctx.send("Spotify support is not configured by this bot operator.")
            return False
        try:
            credentials = await asyncio.to_thread(store.get_credentials, ctx.guild.id)
        except SpotifyStoreError:
            await ctx.send("Stored Spotify credentials are unavailable. Run `/spotify server clear` and try again.")
            return False
        if credentials:
            status = await asyncio.to_thread(store.status, ctx.guild.id)
            configured_by, updated_at = status
            await ctx.send(
                f"Spotify is configured for this server by <@{configured_by}> (updated {updated_at} UTC). "
                "Use `/spotify server clear` and then `/spotify server setup` to replace its credentials."
            )
            return True

        try:
            dm = await ctx.author.create_dm()
            await dm.send(
                "This server needs Spotify API credentials before it can resolve Spotify links. "
                "Reply with your Client ID on the first line and Client Secret on the second line. "
                "They will be encrypted and stored for this server only."
            )
            await ctx.send("I sent you a DM to configure Spotify for this server.")
        except discord.Forbidden:
            await ctx.send("I could not DM you. Enable DMs from server members and try again.")
            return False

        def valid_reply(message):
            return message.author.id == ctx.author.id and message.channel.id == dm.id

        try:
            reply = await self.bot.wait_for("message", check=valid_reply, timeout=300)
        except asyncio.TimeoutError:
            await ctx.send("Spotify configuration timed out. Run `/spotify server setup` to retry.")
            return False

        values = [line.strip() for line in reply.content.splitlines() if line.strip()]
        if len(values) != 2:
            await dm.send("I need exactly two non-empty lines: Client ID, then Client Secret.")
            return False
        client_id, client_secret = values
        try:
            client = SpotifyClient(client_id, client_secret)
            await asyncio.to_thread(client.validate_credentials)
            await asyncio.to_thread(store.save_credentials, ctx.guild.id, client_id, client_secret, ctx.author.id)
        except (SpotifyError, requests.RequestException, SpotifyStoreError):
            await dm.send("Spotify could not validate those credentials. Nothing was saved; run `/spotify server setup` to try again.")
            return False
        await dm.send("Spotify credentials were saved for this server.")
        await self.get_spotify_playlist_token(
            ctx, (client_id, client_secret), scope="guild"
        )
        await ctx.send("Spotify is configured for this server.")
        return True

    async def get_personal_spotify_credentials(self, user_id):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            return None
        try:
            return await asyncio.to_thread(store.get_user_credentials, user_id)
        except SpotifyStoreError:
            return None

    async def get_available_spotify_connection(self, ctx):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            await ctx.send("Spotify support is not configured by this bot operator.")
            return None
        try:
            credentials = await asyncio.to_thread(store.get_credentials, ctx.guild.id)
        except SpotifyStoreError:
            credentials = None
        if credentials:
            return credentials, "guild"
        credentials = await self.get_personal_spotify_credentials(ctx.author.id)
        if credentials:
            return credentials, "user"
        return None

    async def configure_personal_spotify(self, ctx):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            return None
        redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI")
        if not redirect_uri:
            await ctx.send(
                "Private Spotify setup needs the bot operator to configure `SPOTIFY_REDIRECT_URI`.",
                ephemeral=bool(ctx.interaction),
            )
            return None
        try:
            dm = await ctx.author.create_dm()
            await dm.send(
                "To create private Spotify credentials, open the Spotify Developer Dashboard, create an app, "
                f"and add `{redirect_uri}` as its Redirect URI. Select **Web API** under APIs used, then copy "
                "the app's Client ID and Client Secret.\n\n"
                "Reply with your Client ID on the first line and Client Secret on the second line, for example:\n"
                "`your-client-id`\n`your-client-secret`\n\n"
                "They will be encrypted and saved privately for your Discord account."
            )
        except discord.Forbidden:
            await ctx.send("I could not DM you. Enable DMs from server members and try again.")
            return None

        def valid_reply(message):
            return message.author.id == ctx.author.id and message.channel.id == dm.id

        try:
            reply = await self.bot.wait_for("message", check=valid_reply, timeout=300)
        except asyncio.TimeoutError:
            await dm.send("Personal Spotify setup timed out.")
            return None
        values = [line.strip() for line in reply.content.splitlines() if line.strip()]
        if len(values) != 2:
            await dm.send("I need exactly two non-empty lines: Client ID, then Client Secret.")
            return None
        client_id, client_secret = values
        try:
            client = SpotifyClient(client_id, client_secret)
            await asyncio.to_thread(client.validate_credentials)
            await asyncio.to_thread(store.save_user_credentials, ctx.author.id, client_id, client_secret)
        except (SpotifyError, requests.RequestException, SpotifyStoreError):
            await dm.send("Spotify could not validate those credentials. Nothing was saved.")
            return None
        await dm.send("Your private Spotify credentials were saved.")
        await self.get_spotify_playlist_token(ctx, (client_id, client_secret), scope="user")
        return client_id, client_secret

    async def offer_personal_spotify_setup(self, ctx, reason, retry):
        await ctx.send(reason, view=PersonalSpotifySetupView(self, ctx, reason, retry), ephemeral=bool(ctx.interaction))

    async def retry_personal_spotify_playlist(self, ctx, resource, options):
        credentials = await self.get_personal_spotify_credentials(ctx.author.id)
        if credentials:
            await self.import_spotify(ctx, resource, options, connection=(credentials, "user"))
            return

        async def retry(configured_credentials):
            await self.import_spotify(ctx, resource, options, connection=(configured_credentials, "user"))

        await self.offer_personal_spotify_setup(
            ctx,
            "The server's Spotify account cannot access this playlist. Use your private Spotify connection instead?",
            retry,
        )

    async def ensure_spotify_voice_access(self, ctx):
        if not await self.ensure_user_in_voice(ctx):
            return False
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if voice and voice.is_connected() and ctx.author.voice.channel != voice.channel:
            await ctx.send("Join my voice channel before changing Spotify configuration.")
            return False
        return True

    async def get_spotify_playlist_token(self, ctx, credentials, scope="guild"):
        store = self.bot.spotify_store
        now = int(time.time())
        try:
            token_record = await asyncio.to_thread(
                store.get_playlist_token if scope == "guild" else store.get_user_playlist_token,
                ctx.guild.id if scope == "guild" else ctx.author.id,
            )
        except SpotifyStoreError:
            token_record = None
        client = SpotifyClient(*credentials, market=os.getenv("SPOTIFY_MARKET", "US"))
        if token_record:
            access_token, refresh_token, expires_at = token_record
            if expires_at > now + 60:
                return access_token
            try:
                access_token, refresh_token, expires_in = await asyncio.to_thread(
                    client.refresh_playlist_access_token, refresh_token
                )
                if scope == "guild":
                    await asyncio.to_thread(store.save_playlist_token, ctx.guild.id, access_token, refresh_token, now + expires_in, ctx.author.id)
                else:
                    await asyncio.to_thread(store.save_user_playlist_token, ctx.author.id, access_token, refresh_token, now + expires_in)
                return access_token
            except (SpotifyPlaylistAuthorizationError, requests.RequestException):
                pass

        redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI")
        if not redirect_uri:
            await ctx.send("Spotify playlist support needs `SPOTIFY_REDIRECT_URI` configured by the bot operator.")
            return None
        state = secrets.token_urlsafe(24)
        authorization_url = client.playlist_authorization_url(redirect_uri, state)
        try:
            dm = await ctx.author.create_dm()
            await dm.send(
                f"Authorize Spotify playlist access for {'this server' if scope == 'guild' else 'your private Spotify connection'} using this link:\n"
                f"<{authorization_url}>\n\n"
                "After Spotify redirects, copy the complete URL beginning with `http://127.0.0.1` "
                "from your browser address bar and reply with it here. A browser connection error after the "
                "redirect is expected. If the address bar does not update, open Developer Tools, select the "
                "Network tab, refresh the authorization page, click Agree, then copy the request URL for "
                "`spotify-callback`."
            )
            await ctx.send(
                "I sent you a DM to authorize Spotify playlist access for this server."
                if scope == "guild" else "I sent you a DM to authorize your private Spotify connection."
            )
        except discord.Forbidden:
            await ctx.send("I could not DM you. Enable DMs from server members and try again.")
            return None

        def valid_reply(message):
            return message.author.id == ctx.author.id and message.channel.id == dm.id

        try:
            reply = await self.bot.wait_for("message", check=valid_reply, timeout=600)
            callback = urlparse(reply.content.strip())
            parameters = parse_qs(callback.query)
            if callback.geturl().split("?", 1)[0] != redirect_uri or parameters.get("state", [None])[0] != state:
                raise ValueError("callback did not match the authorization request")
            code = parameters["code"][0]
        except (asyncio.TimeoutError, KeyError, ValueError):
            await dm.send("Spotify playlist authorization was cancelled or invalid. Run `.play` with the playlist again to retry.")
            return None

        try:
            access_token, refresh_token, expires_in = await asyncio.to_thread(
                client.exchange_playlist_authorization_code, code, redirect_uri
            )
            if scope == "guild":
                await asyncio.to_thread(store.save_playlist_token, ctx.guild.id, access_token, refresh_token, now + expires_in, ctx.author.id)
            else:
                await asyncio.to_thread(store.save_user_playlist_token, ctx.author.id, access_token, refresh_token, now + expires_in)
        except (SpotifyPlaylistAuthorizationError, requests.RequestException, SpotifyStoreError):
            await dm.send("Spotify could not complete playlist authorization. Nothing was saved; run `.play` with the playlist again to retry.")
            return None
        await dm.send(
            "Spotify playlist access was authorized for this server."
            if scope == "guild" else "Your private Spotify playlist access was authorized."
        )
        return access_token

    async def resolve_youtube_track(self, query):
        def extract():
            with youtube_dl.YoutubeDL({
                'format': 'bestaudio', 'noplaylist': True, 'quiet': True, 'no_warnings': True,
            }) as ydl:
                return ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]

        async with asyncio.timeout(getattr(self.bot, "ytdlp_timeout_seconds", YTDLP_TIMEOUT_SECONDS)):
            return await asyncio.to_thread(extract)

    @staticmethod
    def is_youtube_playlist_url(value):
        parsed = urlparse(value)
        return parsed.netloc.lower() in {"youtube.com", "www.youtube.com", "m.youtube.com"} and bool(
            parse_qs(parsed.query).get("list")
        )

    async def get_youtube_playlist_entries(self, url, progress_callback=None):
        loop = asyncio.get_running_loop()
        last_reported = 0
        progress_updates = []

        def report_progress(info, incomplete=False):
            nonlocal last_reported
            if not progress_callback:
                return None
            processed = info.get("playlist_index")
            total = info.get("playlist_count")
            if not isinstance(processed, int) or not isinstance(total, int):
                return None
            if processed <= last_reported:
                return None
            if processed != total and processed - last_reported < PLAYLIST_PROGRESS_BATCH_SIZE:
                return None
            last_reported = processed
            progress_updates.append(
                asyncio.run_coroutine_threadsafe(progress_callback(processed, total), loop)
            )
            return None

        def extract():
            with youtube_dl.YoutubeDL({
                'format': 'bestaudio', 'extract_flat': False, 'quiet': True,
                'no_warnings': True, 'match_filter': report_progress,
            }) as ydl:
                return ydl.extract_info(url, download=False).get('entries', [])

        async with asyncio.timeout(getattr(self.bot, "ytdlp_timeout_seconds", YTDLP_TIMEOUT_SECONDS)):
            entries = await asyncio.to_thread(extract)
        if progress_updates:
            await asyncio.gather(
                *(asyncio.wrap_future(progress_update) for progress_update in progress_updates),
                return_exceptions=True,
            )
        return entries

    async def get_youtube_info(self, source, options):
        def extract():
            with youtube_dl.YoutubeDL({**options, 'quiet': True, 'no_warnings': True}) as ydl:
                return ydl.extract_info(source, download=False)

        async with asyncio.timeout(getattr(self.bot, "ytdlp_timeout_seconds", YTDLP_TIMEOUT_SECONDS)):
            return await asyncio.to_thread(extract)

    def cached_stream_info(self, source_url):
        cached = self.stream_url_cache.get(source_url)
        if not cached:
            return None
        if cached["expires_at"] <= time.time() + self.bot.stream_url_cache_safety_margin_seconds:
            self.stream_url_cache.pop(source_url, None)
            return None
        self.stream_url_cache.pop(source_url)
        self.stream_url_cache[source_url] = cached
        return cached["info"]

    def cache_stream_info(self, info):
        source_url = info.get("webpage_url")
        stream_url = info.get("url")
        expires = parse_qs(urlparse(stream_url or "").query).get("expire", [None])[0]
        try:
            expires_at = float(expires)
        except (TypeError, ValueError):
            return
        if not source_url or expires_at <= time.time() + self.bot.stream_url_cache_safety_margin_seconds:
            return
        self.stream_url_cache.pop(source_url, None)
        self.stream_url_cache[source_url] = {"info": info, "expires_at": expires_at}
        while len(self.stream_url_cache) > self.bot.stream_url_cache_max_entries:
            self.stream_url_cache.pop(next(iter(self.stream_url_cache)))

    async def resolve_stream_from_source(self, source_url):
        cached = self.cached_stream_info(source_url)
        if cached:
            return cached
        info = await self.get_youtube_info(source_url, {"format": "bestaudio", "noplaylist": True})
        self.cache_stream_info(info)
        return info

    async def queue_playlist_items(self, ctx, session, items):
        """Queue resolved YouTube items and begin playback when necessary."""
        queued = 0
        for info in items:
            try:
                thumbnails = info.get('thumbnails') or []
                session.q.enqueue(
                    info['title'], info['url'], thumbnails[0]['url'] if thumbnails else '',
                    info['webpage_url'], info.get('duration') or 0, ctx.author.id,
                )
                queued += 1
            except KeyError:
                continue

        if not queued:
            return 0

        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if not voice:
            await ctx.author.voice.channel.connect()
            voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            asyncio.create_task(self.auto_disconnect(ctx, voice))

        playback_active = (
            voice.is_playing()
            or voice.is_paused()
            or session.q.continuation_pending
            or (session.q.loop_current and not session.q.is_empty())
        )
        if not playback_active:
            await self.play_current_track(ctx, session)
        return queued

    async def send_queued_track_embed(self, ctx, session, track):
        duration_str = await convert_duration_pretty(track.duration)
        dominant_color = await get_dominant_color(track.thumb)
        embed = discord.Embed(
            title=escape_markdown(truncate_text(track.title)),
            url=track.ytube,
            color=discord.Color(dominant_color),
            description=f"*🎵 Added to queue in <#{session.channel}>*",
        )
        embed.set_thumbnail(url=track.thumb)
        embed.set_author(name="Music Stream Link", url=track.url)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Added By", value=f"<@{track.user}>", inline=True)
        queued_message = await ctx.channel.send(
            embed=embed,
            view=QueuedTrackControls(self, ctx.guild.id, track),
        )
        session.queued_track_messages[id(track)] = (track, queued_message)

    async def edit_playlist_import_status(self, message, content, view=...):
        try:
            if view is ...:
                await message.edit(content=content)
            else:
                await message.edit(content=content, view=view)
        except discord.HTTPException as error:
            logger.warning("Could not update playlist import status: %s", error)

    @staticmethod
    def playlist_progress_bar(processed, total, width=10):
        completed_cells = width if not total else min(width, (processed * width) // total)
        return "🟩" * completed_cells + "⬜" * (width - completed_cells)

    def playlist_import_progress(self, service, processed, total, queued, item_name):
        remaining = max(0, total - processed)
        percentage = 100 if not total else (processed * 100) // total
        return (
            f"🎵 **{service} import in progress**\n"
            f"{self.playlist_progress_bar(processed, total)} `{percentage}%` ({processed}/{total})\n"
            f"Queued: **{queued}** {item_name}; adding **{remaining}** remaining in the background..."
        )

    def playlist_loading_progress(self, processed, total):
        percentage = 100 if not total else (processed * 100) // total
        return (
            "🎵 **Loading YouTube playlist**\n"
            f"{self.playlist_progress_bar(processed, total)} `{percentage}%` ({processed}/{total})\n"
            f"Found: **{processed}** available video{'s' if processed != 1 else ''}."
        )

    def playlist_import_complete(self, service, total, queued, item_name, skipped=0):
        summary = (
            f"✅ **{service} import complete**\n"
            f"{self.playlist_progress_bar(total, total)} `100%` ({total}/{total})\n"
            f"Queued: **{queued}** {item_name}."
        )
        if skipped:
            summary += f" Skipped: **{skipped}** with no YouTube match."
        return summary + " Use `.q` to view the queue."

    def playlist_import_cancelled(self, service, processed, total, queued, item_name, skipped=0):
        summary = (
            f"🛑 **{service} import cancelled**\n"
            f"{self.playlist_progress_bar(processed, total)} ({processed}/{total})\n"
            f"Queued before cancellation: **{queued}** {item_name}."
        )
        if skipped:
            summary += f" Skipped: **{skipped}** with no YouTube match."
        return summary + " Use `.q` to view the queue."

    async def finish_youtube_playlist_import(
        self, ctx, session, status_message, cancel_view, remaining_entries, queued, total
    ):
        try:
            for index in range(0, len(remaining_entries), PLAYLIST_PROGRESS_BATCH_SIZE):
                if cancel_view.cancelled:
                    cancel_view.finish()
                    await self.edit_playlist_import_status(
                        status_message,
                        self.playlist_import_cancelled(
                            "YouTube playlist", total - len(remaining_entries) + index, total, queued, "videos"
                        ),
                        view=cancel_view,
                    )
                    return
                batch = remaining_entries[index:index + PLAYLIST_PROGRESS_BATCH_SIZE]
                queued += await self.queue_playlist_items(ctx, session, batch)
                remaining = len(remaining_entries) - index - len(batch)
                if remaining:
                    await self.edit_playlist_import_status(
                        status_message,
                        self.playlist_import_progress(
                            "YouTube playlist", total - remaining, total, queued, "videos"
                        ),
                    )
            cancel_view.finish()
            await self.edit_playlist_import_status(
                status_message,
                self.playlist_import_complete("YouTube playlist", total, queued, "videos"),
                view=cancel_view,
            )
        except Exception as error:
            logger.exception("Guild %s: YouTube playlist background import failed", ctx.guild.id)
            cancel_view.finish()
            await self.edit_playlist_import_status(
                status_message,
                f"YouTube playlist import stopped after queuing {queued} of {total} selected videos due to a source error.",
                view=cancel_view,
            )
        finally:
            self.release_playlist_import(ctx.guild.id)

    async def start_youtube_playlist_import(self, ctx, session, selected_entries):
        initial_entries = selected_entries[:PLAYLIST_INITIAL_BATCH_SIZE]
        remaining_entries = selected_entries[PLAYLIST_INITIAL_BATCH_SIZE:]
        queued = await self.queue_playlist_items(ctx, session, initial_entries)
        cancel_view = PlaylistImportCancelView(ctx.author.id, ctx.guild.id)
        status_message = await ctx.send(
            self.playlist_import_progress(
                "YouTube playlist", len(initial_entries), len(selected_entries), queued, "videos"
            ),
            view=cancel_view,
        )
        cancel_view.task = asyncio.create_task(
            self.finish_youtube_playlist_import(
                ctx, session, status_message, cancel_view, remaining_entries, queued, len(selected_entries)
            )
        )

    async def resolve_spotify_tracks(self, tracks):
        resolved = []
        skipped = 0
        for track in tracks:
            try:
                resolved.append(await self.resolve_youtube_track(track_query(track)))
            except Exception:
                skipped += 1
        return resolved, skipped

    async def finish_spotify_playlist_import(
        self, ctx, session, status_message, cancel_view, remaining_tracks, queued, skipped, total
    ):
        try:
            for index in range(0, len(remaining_tracks), PLAYLIST_PROGRESS_BATCH_SIZE):
                if cancel_view.cancelled:
                    cancel_view.finish()
                    await self.edit_playlist_import_status(
                        status_message,
                        self.playlist_import_cancelled(
                            "Spotify", total - len(remaining_tracks) + index, total, queued, "matches", skipped
                        ),
                        view=cancel_view,
                    )
                    return
                batch = remaining_tracks[index:index + PLAYLIST_PROGRESS_BATCH_SIZE]
                resolved, batch_skipped = await self.resolve_spotify_tracks(batch)
                skipped += batch_skipped
                queued += await self.queue_playlist_items(ctx, session, resolved)
                remaining = len(remaining_tracks) - index - len(batch)
                if remaining:
                    await self.edit_playlist_import_status(
                        status_message,
                        self.playlist_import_progress(
                            "Spotify", total - remaining, total, queued, "matches"
                        ),
                    )
            cancel_view.finish()
            await self.edit_playlist_import_status(
                status_message,
                self.playlist_import_complete("Spotify", total, queued, "matches", skipped),
                view=cancel_view,
            )
        except Exception as error:
            logger.exception("Guild %s: Spotify playlist background import failed", ctx.guild.id)
            cancel_view.finish()
            await self.edit_playlist_import_status(
                status_message,
                f"Spotify import stopped after queuing {queued} of {total} selected tracks due to a source error.",
                view=cancel_view,
            )
        finally:
            self.release_playlist_import(ctx.guild.id)

    async def start_spotify_playlist_import(self, ctx, session, selected_tracks):
        initial_tracks = selected_tracks[:PLAYLIST_INITIAL_BATCH_SIZE]
        remaining_tracks = selected_tracks[PLAYLIST_INITIAL_BATCH_SIZE:]
        async with ctx.typing():
            resolved, skipped = await self.resolve_spotify_tracks(initial_tracks)
        queued = await self.queue_playlist_items(ctx, session, resolved)
        cancel_view = PlaylistImportCancelView(ctx.author.id, ctx.guild.id)
        status_message = await ctx.send(
            self.playlist_import_progress(
                "Spotify", len(initial_tracks), len(selected_tracks), queued, "matches"
            ),
            view=cancel_view,
        )
        cancel_view.task = asyncio.create_task(
            self.finish_spotify_playlist_import(
                ctx, session, status_message, cancel_view, remaining_tracks, queued, skipped, len(selected_tracks)
            )
        )

    async def import_youtube_playlist(self, ctx, url, options, entries=None):
        if not await self.claim_playlist_import(ctx):
            return
        background_import_started = False
        try:
            try:
                entries = entries if entries is not None else await self.get_youtube_playlist_entries(url)
            except (asyncio.TimeoutError, youtube_dl.utils.DownloadError):
                await ctx.send("YouTube could not load that playlist.")
                return
            selected_entries = select_tracks([entry for entry in entries if entry], options)
            if not selected_entries:
                await ctx.send("No playable YouTube videos matched that selection.")
                return

            session = await self.get_session(ctx)
            if session is None:
                return
            if len(selected_entries) > PLAYLIST_INITIAL_BATCH_SIZE:
                await self.start_youtube_playlist_import(ctx, session, selected_entries)
                background_import_started = True
                return
            voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            queued = 0
            async with ctx.typing():
                for info in selected_entries:
                    try:
                        thumbnails = info.get('thumbnails') or []
                        session.q.enqueue(
                            info['title'], info['url'], thumbnails[0]['url'] if thumbnails else '',
                            info['webpage_url'], info.get('duration') or 0, ctx.author.id,
                        )
                        queued += 1
                    except KeyError:
                        continue
            if not queued:
                await ctx.send("No selected YouTube videos could be queued.")
                return
            if not voice:
                await ctx.author.voice.channel.connect()
                voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
                asyncio.create_task(self.auto_disconnect(ctx, voice))
            if not voice.is_playing() and not voice.is_paused():
                await self.play_current_track(ctx, session)
            await ctx.send(
                f"YouTube playlist import: queued {queued} video{'s' if queued != 1 else ''}. Use `.q` to view the queue."
            )
        finally:
            if not background_import_started:
                self.release_playlist_import(ctx.guild.id)

    async def import_spotify(self, ctx, resource, options, tracks=None, connection=None):
        if not await self.claim_playlist_import(ctx):
            return
        background_import_started = False
        try:
            if tracks is None:
                connection = connection or await self.get_available_spotify_connection(ctx)
                if not connection:
                    async def retry(credentials):
                        await self.import_spotify(ctx, resource, options, connection=(credentials, "user"))

                    await self.offer_personal_spotify_setup(
                        ctx,
                        "This server does not have usable Spotify credentials, and you have not configured private Spotify access.",
                        retry,
                    )
                    return
                credentials, scope = connection
                lock = self.spotify_import_locks.setdefault(ctx.guild.id, asyncio.Lock())
                async with lock:
                    client = SpotifyClient(*credentials, market=os.getenv("SPOTIFY_MARKET", "US"))
                    playlist_token = None
                    if resource.resource_type == "playlist":
                        playlist_token = await self.get_spotify_playlist_token(ctx, credentials, scope)
                        if not playlist_token:
                            return
                    async with asyncio.timeout(getattr(self.bot, "ytdlp_timeout_seconds", YTDLP_TIMEOUT_SECONDS)):
                        tracks = await asyncio.to_thread(client.get_tracks, resource, playlist_token)
            
            selected_tracks = select_tracks(tracks, options)
            if not selected_tracks:
                await ctx.send("No playable Spotify tracks matched that selection.")
                return

            session = await self.get_session(ctx)
            if session is None:
                return
            if len(selected_tracks) > PLAYLIST_INITIAL_BATCH_SIZE:
                await self.start_spotify_playlist_import(ctx, session, selected_tracks)
                background_import_started = True
                return

            resolved = []
            skipped = 0
            async with ctx.typing():
                for track in selected_tracks:
                    try:
                        resolved.append(await self.resolve_youtube_track(track_query(track)))
                    except Exception:
                        skipped += 1
            if not resolved:
                await ctx.send("No selected Spotify tracks could be matched on YouTube.")
                return

            voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            playback_active = (
                voice
                and (
                    voice.is_playing()
                    or voice.is_paused()
                    or session.q.continuation_pending
                    or (session.q.loop_current and not session.q.is_empty())
                )
            )
            for info in resolved:
                thumbnails = info.get('thumbnails') or []
                thumb = thumbnails[0]['url'] if thumbnails else ''
                session.q.enqueue(
                    info['title'], info['url'], thumb, info['webpage_url'],
                    info.get('duration') or 0, ctx.author.id,
                )
            if not voice:
                await ctx.author.voice.channel.connect()
                voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
                asyncio.create_task(self.auto_disconnect(ctx, voice))
            if not playback_active:
                await self.play_current_track(ctx, session)

            if len(resolved) == 1:
                if playback_active:
                    queued_track = session.q.queue[-1]
                    await self.send_queued_track_embed(ctx, session, queued_track)
                return

            summary = f"Spotify import: queued {len(resolved)} match{'es' if len(resolved) != 1 else ''}"
            if skipped:
                summary += f"; skipped {skipped} track{'s' if skipped != 1 else ''} with no YouTube match"
            await ctx.send(summary + ". Use `.q` to view the queue.")
        except SpotifyPlaylistAuthorizationError:
            if resource.resource_type == "playlist" and connection and connection[1] == "guild":
                await self.retry_personal_spotify_playlist(ctx, resource, options)
            else:
                await ctx.send("Your Spotify account cannot access that playlist.")
        except (SpotifyError, requests.RequestException, asyncio.TimeoutError):
            await ctx.send("Spotify could not load that link. Try again shortly.")
        finally:
            if not background_import_started:
                self.release_playlist_import(ctx.guild.id)

    @commands.hybrid_group(name="spotify", invoke_without_command=True)
    async def spotify(self, ctx):
        """Manage server and private Spotify connections."""
        await ctx.send(
            "Use `/spotify server setup`, `/spotify server status`, or `/spotify user status`.",
            ephemeral=bool(ctx.interaction),
        )

    @spotify.group(
        name="server",
        description="Manage this server's Spotify credentials.",
        invoke_without_command=True,
    )
    async def spotify_server(self, ctx):
        """Manage this server's Spotify credentials."""
        await ctx.send(
            "Use `/spotify server setup`, `/spotify server clear`, or `/spotify server status`.",
            ephemeral=bool(ctx.interaction),
        )

    @spotify_server.command(name="setup", description="Configure Spotify credentials for this server.")
    @commands.has_guild_permissions(manage_guild=True)
    async def spotify_server_setup(self, ctx):
        await self.configure_guild_spotify(ctx)

    @spotify_server.command(name="clear", description="Remove this server's Spotify credentials.")
    @commands.has_guild_permissions(manage_guild=True)
    async def spotify_server_clear(self, ctx):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            await ctx.send("Spotify support is not configured by this bot operator.")
            return
        deleted = await asyncio.to_thread(store.clear_credentials, ctx.guild.id)
        await ctx.send("Spotify credentials cleared for this server." if deleted else "No Spotify credentials are configured for this server.")

    @spotify_server.command(name="status", description="Show this server's Spotify configuration status.")
    async def spotify_server_status(self, ctx):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            await ctx.send("Spotify support is not configured by this bot operator.")
            return
        status = await asyncio.to_thread(store.status, ctx.guild.id)
        if status:
            configured_by, updated_at = status
            await ctx.send(
                f"Spotify is configured for this server by <@{configured_by}> (updated {updated_at} UTC)."
            )
        else:
            await ctx.send("Spotify is not configured for this server.")

    @spotify.group(
        name="user",
        description="Manage your private Spotify connection.",
        invoke_without_command=True,
    )
    async def spotify_user(self, ctx):
        """Manage your private Spotify connection."""
        await ctx.send("Use `/spotify user setup`, `/spotify user status`, or `/spotify user clear`.", ephemeral=bool(ctx.interaction))

    @spotify_user.command(
        name="setup",
        description="Configure your private Spotify credentials.",
    )
    async def spotify_user_setup(self, ctx):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            await ctx.send("Spotify support is not configured by this bot operator.", ephemeral=bool(ctx.interaction))
            return
        status = await asyncio.to_thread(store.user_status, ctx.author.id)
        if status:
            await ctx.send(
                f"Your private Spotify credentials are configured (updated {status[0]} UTC). "
                "Use `/spotify user clear` and then `/spotify user setup` to replace them.",
                ephemeral=bool(ctx.interaction),
            )
            return
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True, thinking=True)
        credentials = await self.configure_personal_spotify(ctx)
        if credentials:
            await ctx.send("Your private Spotify credentials are configured.", ephemeral=bool(ctx.interaction))

    @spotify_user.command(
        name="status",
        description="Show your private Spotify connection status.",
    )
    async def spotify_user_status(self, ctx):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            await ctx.send("Spotify support is not configured by this bot operator.", ephemeral=bool(ctx.interaction))
            return
        status = await asyncio.to_thread(store.user_status, ctx.author.id)
        await ctx.send(
            f"Your private Spotify credentials are configured (updated {status[0]} UTC)." if status
            else "You do not have private Spotify credentials configured.",
            ephemeral=bool(ctx.interaction),
        )

    @spotify_user.command(
        name="clear",
        description="Remove your private Spotify credentials.",
    )
    async def spotify_user_clear(self, ctx):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            await ctx.send("Spotify support is not configured by this bot operator.", ephemeral=bool(ctx.interaction))
            return
        deleted = await asyncio.to_thread(store.clear_user_credentials, ctx.author.id)
        await ctx.send(
            "Your private Spotify credentials and playlist authorization were cleared." if deleted
            else "You do not have private Spotify credentials configured.",
            ephemeral=bool(ctx.interaction),
        )

    @commands.hybrid_command(name="config")
    @commands.has_guild_permissions(manage_guild=True)
    async def config(self, ctx):
        """Open this server's music configuration form."""
        if not self.guild_config_store:
            await ctx.send("Guild configuration storage is currently unavailable.")
            return
        if ctx.interaction:
            await ctx.interaction.response.send_modal(GuildConfigModal(self, ctx.guild.id))
            return
        await ctx.send(
            "Open the server music configuration form.",
            view=GuildConfigLauncher(self, ctx.guild.id, ctx.author.id),
        )

    @commands.hybrid_command(name="help")
    async def help(self, ctx, command_name: str = None):
        """Show available commands or detailed help for one command."""
        prefix = self.get_guild_config(ctx.guild.id)["command_prefix"]
        if command_name:
            command = self.bot.get_command(command_name.lower())
            if not command:
                await ctx.send(f"No command named `{command_name}` exists.", ephemeral=bool(ctx.interaction))
                return
            embed = discord.Embed(title=f"Help: {command.name}", color=discord.Color.blue())
            embed.description = command.help or "No description is available for this command."
            prefix_usage = f"{prefix}{command.name} {command.signature}".strip()
            embed.add_field(name="Prefix usage", value=f"`{prefix_usage}`", inline=False)
            embed.add_field(name="Slash usage", value=f"`/{command.name}`", inline=False)
            if command.aliases:
                embed.add_field(name="Prefix aliases", value=", ".join(f"`{prefix}{alias}`" for alias in command.aliases), inline=False)
            await ctx.send(embed=embed, ephemeral=bool(ctx.interaction))
            return

        embed = discord.Embed(title="Command reference", color=discord.Color.blue())
        embed.description = (
            f"Use `{prefix}<command>` or `/command`. Run `{prefix}help <command>` or `/help` with a command "
            "for focused usage and aliases."
        )
        embed.add_field(
            name="Playback",
            value=(
                f"`{prefix}play <query or URL>` - Play a YouTube search, video, playlist, or Spotify link.\n"
                f"`{prefix}pause` / `{prefix}resume` - Pause or resume current playback.\n"
                f"`{prefix}skip` - Advance to the next queued track.\n"
                f"`{prefix}seek <seconds|MM:SS|HH:MM:SS>` - Seek within the current track.\n"
                f"`{prefix}restart` - Restart the current track from the beginning.\n"
                f"`{prefix}shuffle` - Shuffle upcoming tracks while preserving the current one.\n"
                f"`{prefix}stop` - Stop playback and clear the queue.\n"
                f"`{prefix}leave` / `{prefix}here` - Disconnect or move the bot to your voice channel."
            ),
            inline=False,
        )
        embed.add_field(
            name="Queue",
            value=(
                f"`{prefix}queue` - Show the queue at the page containing the current track.\n"
                f"`{prefix}playingnow` - Show the track currently playing.\n"
                f"`{prefix}remove` - Choose an upcoming track to remove.\n"
                f"`{prefix}move` - Reorder an upcoming track.\n"
                f"`{prefix}clearqueue` - Remove all upcoming tracks and keep the current one."
            ),
            inline=False,
        )
        embed.add_field(
            name="Discovery and statistics",
            value=(
                f"`{prefix}search <query>` - Search YouTube and select a result.\n"
                f"`{prefix}mostplayed` - Show this server's most played tracks.\n"
                f"`{prefix}mostliked` / `{prefix}mostdisliked` - Show this server's rated tracks.\n"
                f"`{prefix}myliked` - Show tracks you liked in this server."
            ),
            inline=False,
        )
        embed.add_field(
            name="Personal playlists",
            value=(
                f"`{prefix}playlist` - List your saved playlists.\n"
                f"`{prefix}playlist create <name>` - Create a playlist.\n"
                f"`{prefix}playlist add <name> <source>` - Save a YouTube/Spotify song or playlist.\n"
                f"`{prefix}playlist view [@member] [name]` - View saved playlists or songs.\n"
                f"`{prefix}playlist play <name> [@member]` - Queue a saved playlist.\n"
                f"`{prefix}playlist remove|move|delete` - Edit or delete your saved playlists."
            ),
            inline=False,
        )
        embed.add_field(
            name="Spotify and server settings",
            value=(
                f"`{prefix}config` - Open the server music settings form (Manage Server required).\n"
                f"`{prefix}spotify server setup` - Configure Spotify for this server (Manage Server required).\n"
                f"`{prefix}spotify server clear` - Remove this server's Spotify credentials (Manage Server required).\n"
                f"`{prefix}spotify server status` - Show this server's Spotify configuration status.\n"
                f"`{prefix}spotify user setup` - Configure your private Spotify credentials.\n"
                f"`{prefix}spotify user status` - Show your private Spotify connection status.\n"
                f"`{prefix}spotify user clear` - Remove your private Spotify credentials.\n"
                "Playlist imports support `--count`, `--range`, `--ordered`, and `--shuffle`."
            ),
            inline=False,
        )
        embed.add_field(
            name="Server assistant",
            value=(
                f"`{prefix}time` - Show the server time.\n"
                f"`{prefix}up` - Show bot version, container hostname, and uptime.\n"
                f"`{prefix}ping` - Check bot responsiveness."
            ),
            inline=False,
        )
        await ctx.send(embed=embed, ephemeral=bool(ctx.interaction))

    async def send_song_ranking(self, ctx, title, rows, empty_message):
        if not rows:
            await ctx.send(empty_message)
            return
        lines = []
        for index, (track_title, track_url, thumbnail_url, plays, likes, dislikes) in enumerate(rows, start=1):
            lines.append(
                f"**{index}.** [{escape_markdown(truncate_text(track_title))}]({track_url})\n"
                f"Plays: {plays} | Likes: {likes} | Dislikes: {dislikes}"
            )
        embed = discord.Embed(title=title, description="\n\n".join(lines), color=discord.Color.blue())
        if rows[0][2]:
            embed.set_thumbnail(url=rows[0][2])
        await ctx.send(embed=embed)

    async def get_song_ranking(self, ctx, method_name, title, empty_message, *arguments):
        if not self.song_stats_store:
            await ctx.send("Song statistics are currently unavailable.")
            return
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()
        rows = await asyncio.to_thread(
            getattr(self.song_stats_store, method_name), ctx.guild.id, *arguments
        )
        await self.send_song_ranking(ctx, title, rows, empty_message)

    async def ensure_user_playlist_store(self, ctx):
        if self.user_playlist_store:
            return True
        await ctx.send("Personal playlists are currently unavailable.", ephemeral=bool(ctx.interaction))
        return False

    async def resolve_user_playlist_source(
        self, ctx, source, maximum_tracks, options=None, progress_callback=None, cancel_view=None, skipped_titles=None
    ):
        spotify_resource = parse_resource(source)
        if spotify_resource:
            connection = await self.get_available_spotify_connection(ctx)
            if not connection:
                await ctx.send(
                    "This server does not have usable Spotify credentials, and you have not configured private Spotify access. "
                    "Run the Spotify request again and choose **Use my Spotify** to configure it.",
                    ephemeral=bool(ctx.interaction),
                )
                return [], 0
            credentials, scope = connection
            try:
                client = SpotifyClient(*credentials, market=os.getenv("SPOTIFY_MARKET", "US"))
                playlist_token = None
                if spotify_resource.resource_type == "playlist":
                    playlist_token = await self.get_spotify_playlist_token(ctx, credentials, scope)
                    if not playlist_token:
                        return [], 0
                async with asyncio.timeout(getattr(self.bot, "ytdlp_timeout_seconds", YTDLP_TIMEOUT_SECONDS)):
                    tracks = await asyncio.to_thread(client.get_tracks, spotify_resource, playlist_token)
                selected_tracks = select_tracks(tracks, options) if options else tracks[:maximum_tracks]
                resolved = []
                skipped = 0
                for index in range(0, len(selected_tracks), PLAYLIST_PROGRESS_BATCH_SIZE):
                    if cancel_view and cancel_view.cancelled:
                        return [], 0
                    batch = selected_tracks[index:index + PLAYLIST_PROGRESS_BATCH_SIZE]
                    for track in batch:
                        try:
                            resolved.append(await self.resolve_youtube_track(track_query(track)))
                        except Exception:
                            skipped += 1
                            if skipped_titles is not None:
                                skipped_titles.append(track_query(track))
                    if progress_callback:
                        await progress_callback(len(batch) + index, len(selected_tracks), len(resolved), skipped)
                return [self.user_playlist_track_from_info(track) for track in resolved], skipped
            except (SpotifyError, requests.RequestException, asyncio.TimeoutError):
                await ctx.send(
                    "Spotify could not load that source. Try again shortly.",
                    ephemeral=bool(ctx.interaction),
                )
                return [], 0

        try:
            if self.is_youtube_playlist_url(source):
                entries = await self.get_youtube_playlist_entries(source)
                selected_entries = select_tracks([entry for entry in entries if entry], options) if options else entries[:maximum_tracks]
                return [
                    self.user_playlist_track_from_info(entry)
                    for entry in selected_entries
                    if entry
                ], 0
            is_url = urlparse(source).scheme in {"http", "https"}
            info = await self.get_youtube_info(
                source if is_url else f"ytsearch:{source}",
                {"format": "bestaudio", "noplaylist": True},
            )
            if not is_url:
                info = info["entries"][0]
            return [self.user_playlist_track_from_info(info)], 0
        except (asyncio.TimeoutError, youtube_dl.utils.DownloadError, IndexError, KeyError):
            await ctx.send(
                "YouTube could not resolve that source. Try another search or link.",
                ephemeral=bool(ctx.interaction),
            )
            return [], 0

    def user_playlist_track_from_info(self, info):
        thumbnails = info.get("thumbnails") or []
        self.cache_stream_info(info)
        return (
            info["title"],
            thumbnails[0]["url"] if thumbnails else "",
            info["webpage_url"],
            info.get("duration") or 0,
        )

    async def start_save_user_playlist_source(self, ctx, name, source, options, interaction):
        cancel_view = PlaylistImportCancelView(ctx.author.id, ctx.guild.id, require_voice=False)
        await interaction.edit_original_response(
            content="🎵 **Saving playlist tracks...**\nYou can cancel before tracks are saved.",
            view=cancel_view,
        )
        cancel_view.task = asyncio.create_task(
            self.save_user_playlist_source(ctx, name, source, options, interaction, cancel_view)
        )

    async def save_user_playlist_source(self, ctx, name, source, options=None, interaction=None, cancel_view=None):
        progress_message = await interaction.original_response() if interaction else None
        skipped_titles = []

        async def respond(content=None, *, embed=None, view=None):
            if progress_message:
                await progress_message.edit(content=content, embed=embed, view=view)
            elif interaction:
                await interaction.followup.send(content, embed=embed, view=view, ephemeral=True)
            else:
                await ctx.send(content, embed=embed, view=view, ephemeral=bool(ctx.interaction))

        async def update_progress(processed, total, resolved, skipped):
            if not progress_message:
                return
            progress_bar = self.playlist_progress_bar(processed, total)
            percentage = 100 if not total else (processed * 100) // total
            content = (
                "🎵 **Saving Spotify playlist**\n"
                f"{progress_bar} `{percentage}%` ({processed}/{total})\n"
                f"Matched: **{resolved}**"
            )
            if skipped:
                content += f" | Skipped: **{skipped}**"
            await progress_message.edit(content=content)

        used_tracks = await asyncio.to_thread(self.user_playlist_store.track_count, ctx.author.id)
        remaining_tracks = getattr(self.bot, "max_songs_per_user", 50) - used_tracks
        if remaining_tracks <= 0:
            await respond(
                f"You can save up to {getattr(self.bot, 'max_songs_per_user', 50)} songs across your playlists.",
            )
            return
        if progress_message:
            await progress_message.edit(content="🎵 **Loading playlist tracks...**")
        tracks, skipped = await self.resolve_user_playlist_source(
            ctx,
            source,
            remaining_tracks,
            options,
            progress_callback=update_progress,
            cancel_view=cancel_view,
            skipped_titles=skipped_titles,
        )
        if cancel_view and cancel_view.cancelled:
            cancel_view.finish()
            await respond("🛑 **Saving playlist cancelled**\nNo tracks were saved.", view=cancel_view)
            return
        if not tracks:
            return
        try:
            added = await asyncio.to_thread(
                self.user_playlist_store.add_tracks,
                ctx.author.id,
                name,
                tracks,
                getattr(self.bot, "max_songs_per_user", 50),
            )
        except ValueError as error:
            await respond(str(error))
            return
        if added > 1:
            pages = []
            page_tracks = []
            page_lines = []
            for index, (title, thumbnail_url, source_url, duration) in enumerate(tracks, start=1):
                duration_str = await convert_duration_pretty(duration)
                song_line = (
                    f"**{index}.** [{escape_markdown(truncate_text(title, 180))}]({source_url})\n"
                    f"{duration_str}"
                )
                if page_tracks and (len(page_tracks) == 10 or len("\n\n".join(page_lines)) + len(song_line) + 2 > 3800):
                    pages.append((page_tracks, page_lines))
                    page_tracks = []
                    page_lines = []
                page_tracks.append((title, thumbnail_url))
                page_lines.append(song_line)
            if page_tracks:
                pages.append((page_tracks, page_lines))

            embeds = []
            for page_index, (page_tracks, song_lines) in enumerate(pages, start=1):
                embed = discord.Embed(
                    title=f"Added {added} songs to your playlist: {name}",
                    description="\n\n".join(song_lines),
                    color=discord.Color.blue(),
                )
                page_thumbnail = page_tracks[0][1]
                if page_thumbnail:
                    embed.set_thumbnail(url=page_thumbnail)
                embeds.append(embed)
            if interaction:
                await respond(content=f"Saved {added} song{'s' if added != 1 else ''} to **{escape_markdown(name)}**.")
                await Paginator.CustomPaginator(timeout=120, ephemeral=True).start(interaction, pages=embeds)
            else:
                await respond(
                    embed=embeds[0],
                    view=SavedPlaylistResultPaginator(ctx.author.id, embeds),
                )
            await self.send_skipped_spotify_tracks(ctx, interaction, skipped_titles)
            return

        title, thumbnail_url, source_url, duration = tracks[0]
        duration_str = await convert_duration_pretty(duration)
        dominant_color = await get_dominant_color(thumbnail_url) if thumbnail_url else 0x3498db
        embed = discord.Embed(
            title=escape_markdown(truncate_text(title)),
            url=source_url,
            color=discord.Color(dominant_color),
            description=f"*🎵 Added to your playlist **{escape_markdown(name)}***",
        )
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        embed.set_author(name="YouTube Source", url=source_url)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        if skipped:
            embed.set_footer(text=f"Skipped {skipped} Spotify track{'s' if skipped != 1 else ''} with no YouTube match.")
        await respond(embed=embed)
        await self.send_skipped_spotify_tracks(ctx, interaction, skipped_titles)

    async def send_skipped_spotify_tracks(self, ctx, interaction, skipped_titles):
        if not skipped_titles:
            return
        displayed_titles = skipped_titles[:20]
        description = "\n".join(
            f"- {escape_markdown(truncate_text(title, 180))}" for title in displayed_titles
        )
        if len(skipped_titles) > len(displayed_titles):
            description += f"\n- and {len(skipped_titles) - len(displayed_titles)} more"
        embed = discord.Embed(
            title=f"Skipped {len(skipped_titles)} Spotify track{'s' if len(skipped_titles) != 1 else ''}",
            description=description,
            color=discord.Color.orange(),
        )
        if interaction:
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

    @commands.hybrid_group(name="playlist", aliases=["playlists"], invoke_without_command=True)
    async def playlist(self, ctx):
        """List your saved personal playlists."""
        if not await self.ensure_user_playlist_store(ctx):
            return
        playlists = await asyncio.to_thread(self.user_playlist_store.list_playlists, ctx.author.id)
        if not playlists:
            await ctx.send("You have no saved playlists. Use `/playlist create` to make one.")
            return
        lines = [f"**{escape_markdown(name)}** - {count} song{'s' if count != 1 else ''}" for name, count in playlists]
        await ctx.send(embed=discord.Embed(title=f"{ctx.author.display_name}'s Playlists", description="\n".join(lines)))

    @playlist.command(name="create")
    async def playlist_create(self, ctx, *, name: str):
        """Create one of your personal playlists."""
        if not await self.ensure_user_playlist_store(ctx):
            return
        try:
            await asyncio.to_thread(
                self.user_playlist_store.create_playlist,
                ctx.author.id,
                name,
                getattr(self.bot, "max_playlists_per_user", 3),
            )
        except ValueError as error:
            await ctx.send(str(error), ephemeral=bool(ctx.interaction))
            return
        await ctx.send(
            f"Created playlist **{escape_markdown(name.strip())}**.",
            ephemeral=bool(ctx.interaction),
        )

    @playlist.command(name="view")
    async def playlist_view(self, ctx, member: discord.Member = None, *, name: str = None):
        """View your playlists or a member's playlists, optionally by playlist name."""
        if not await self.ensure_user_playlist_store(ctx):
            return
        owner = member or ctx.author
        name = name.strip() if name else None
        playlists = await asyncio.to_thread(self.user_playlist_store.list_playlists, owner.id)

        if not name:
            if not playlists:
                await ctx.send(f"{owner.display_name} has no saved playlists.")
                return
            lines = [f"**{escape_markdown(playlist_name)}** - {count} song{'s' if count != 1 else ''}" for playlist_name, count in playlists]
            await ctx.send(embed=discord.Embed(title=f"{owner.display_name}'s Playlists", description="\n".join(lines)))
            return
        tracks = await asyncio.to_thread(self.user_playlist_store.get_playlist_tracks, owner.id, name)
        exists = any(playlist_name.casefold() == name.casefold() for playlist_name, _ in playlists)
        if not exists:
            await ctx.send(
                f"{owner.display_name} does not have a playlist named **{escape_markdown(name)}**.",
                ephemeral=bool(ctx.interaction),
            )
            return
        if not tracks:
            await ctx.send(f"**{escape_markdown(name)}** is empty.")
            return
        track_lines = [
            f"**{index}.** {escape_markdown(truncate_text(track[0]))}\n"
            f"{await convert_duration_pretty(track[3])} | [Link]({track[2]})"
            for index, track in enumerate(tracks, start=1)
        ]
        embeds = [
            discord.Embed(
                title=f"{owner.display_name}'s Playlist: {name}",
                description="\n\n".join(track_lines[index:index + 10]),
            )
            for index in range(0, len(track_lines), 10)
        ]
        await Paginator.CustomPaginator(timeout=120).start(ctx, pages=embeds)

    @playlist.command(name="add")
    async def playlist_add(self, ctx, name: str, *, source: str):
        """Add a YouTube/Spotify song or playlist to one of your playlists."""
        if not await self.ensure_user_playlist_store(ctx):
            return
        spotify_resource = parse_resource(source)
        if spotify_resource and not await self.get_available_spotify_connection(ctx):
            async def retry(credentials):
                await self.save_user_playlist_source(ctx, name, source)

            await self.offer_personal_spotify_setup(
                ctx,
                "This server does not have usable Spotify credentials, and you have not configured private Spotify access.",
                retry,
            )
            return
        is_playlist = (
            (spotify_resource and spotify_resource.resource_type == "playlist")
            or self.is_youtube_playlist_url(source)
        )
        if is_playlist:
            max_tracks = min(
                self.get_guild_config(ctx.guild.id)["playlist_max_tracks"],
                getattr(self.bot, "max_songs_per_user", 50),
            )
            await ctx.send(
                "Configure which tracks to save to your playlist.",
                view=SavedPlaylistLauncher(self, ctx, name, source, "Spotify" if spotify_resource else "YouTube", max_tracks),
                ephemeral=bool(ctx.interaction),
            )
            return
        async with ctx.typing(ephemeral=bool(ctx.interaction)):
            await self.save_user_playlist_source(ctx, name, source)

    @playlist.command(name="remove")
    async def playlist_remove(self, ctx, name: str, position: int):
        """Remove a song by its displayed position from one of your playlists."""
        if not await self.ensure_user_playlist_store(ctx):
            return
        try:
            title, thumbnail_url, source_url, duration = await asyncio.to_thread(
                self.user_playlist_store.remove_track,
                ctx.author.id,
                name,
                position,
            )
        except ValueError as error:
            await ctx.send(str(error), ephemeral=bool(ctx.interaction))
            return
        duration_str = await convert_duration_pretty(duration)
        dominant_color = await get_dominant_color(thumbnail_url) if thumbnail_url else 0x3498db
        embed = discord.Embed(
            title=escape_markdown(truncate_text(title)),
            url=source_url,
            color=discord.Color(dominant_color),
            description=f"*🗑️ Removed from your playlist **{escape_markdown(name)}***",
        )
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        embed.set_author(name="YouTube Source", url=source_url)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Previous Position", value=f"#{position}", inline=True)
        await ctx.send(embed=embed, ephemeral=bool(ctx.interaction))

    @playlist.command(name="move")
    async def playlist_move(self, ctx, name: str, source_position: int, destination_position: int):
        """Move a saved song to a new displayed position."""
        if not await self.ensure_user_playlist_store(ctx):
            return
        try:
            await asyncio.to_thread(
                self.user_playlist_store.move_track,
                ctx.author.id,
                name,
                source_position,
                destination_position,
            )
        except ValueError as error:
            await ctx.send(str(error), ephemeral=bool(ctx.interaction))
            return
        await ctx.send(
            f"Moved song #{source_position} to position #{destination_position} in **{escape_markdown(name)}**.",
            ephemeral=bool(ctx.interaction),
        )

    @playlist.command(name="delete")
    async def playlist_delete(self, ctx, *, name: str):
        """Delete one of your personal playlists and all of its songs."""
        if not await self.ensure_user_playlist_store(ctx):
            return
        deleted = await asyncio.to_thread(self.user_playlist_store.delete_playlist, ctx.author.id, name)
        if not deleted:
            await ctx.send(
                f"You do not have a playlist named **{escape_markdown(name)}**.",
                ephemeral=bool(ctx.interaction),
            )
            return
        await ctx.send(
            f"Deleted playlist **{escape_markdown(name)}**.",
            ephemeral=bool(ctx.interaction),
        )

    @playlist.command(name="play")
    async def playlist_play(self, ctx, name: str, member: discord.Member = None):
        """Configure and queue a saved playlist from you or another member."""
        if not await self.ensure_user_playlist_store(ctx):
            return
        if not await self.ensure_user_in_voice(ctx):
            return
        owner = member or ctx.author
        tracks = await asyncio.to_thread(self.user_playlist_store.get_playlist_tracks, owner.id, name)
        if not tracks:
            await ctx.send(f"{owner.display_name} has no songs in a playlist named **{escape_markdown(name)}**.")
            return
        maximum_tracks = min(self.get_guild_config(ctx.guild.id)["playlist_max_tracks"], len(tracks))
        await ctx.send(
            f"Configure which songs to queue from {owner.display_name}'s playlist **{escape_markdown(name)}** "
            f"(**{len(tracks)}** available songs).",
            view=UserPlaylistPlayLauncher(self, ctx, owner, name, maximum_tracks, len(tracks)),
        )

    async def start_queue_user_playlist(self, ctx, owner, name, options, interaction):
        cancel_view = PlaylistImportCancelView(ctx.author.id, ctx.guild.id)
        await interaction.edit_original_response(
            content="🎵 **Preparing saved playlist...**\nYou can cancel before tracks are queued.",
            view=cancel_view,
        )
        cancel_view.task = asyncio.create_task(
            self.queue_user_playlist(ctx, owner, name, options, interaction, cancel_view)
        )

    async def queue_user_playlist(self, ctx, owner, name, options, interaction, cancel_view=None):
        tracks = await asyncio.to_thread(self.user_playlist_store.get_playlist_tracks, owner.id, name)
        if not tracks:
            await interaction.edit_original_response(
                f"{owner.display_name}'s playlist **{escape_markdown(name)}** is empty or was deleted.",
            )
            return
        selected_tracks = select_tracks(tracks, options)
        session = await self.get_session(ctx)
        if session is None:
            return
        semaphore = asyncio.Semaphore(self.bot.saved_playlist_resolution_concurrency)
        total = len(selected_tracks)
        progress_interval = max(1, (total + 9) // 10)

        async def update_progress(processed, resolved, queued):
            percentage = 100 if not total else (processed * 100) // total
            content = (
                "🎵 **Preparing saved playlist**\n"
                f"{self.playlist_progress_bar(processed, total)} `{percentage}%` ({processed}/{total})\n"
                f"Ready: **{resolved}** | Queued: **{queued}**"
            )
            try:
                await interaction.edit_original_response(content=content)
            except discord.HTTPException as error:
                logger.warning("Could not update saved playlist progress: %s", error)

        async def resolve_track(index, track):
            title, thumbnail_url, source_url, duration = track
            repaired = False
            try:
                async with semaphore:
                    info = await self.resolve_stream_from_source(source_url)
            except (asyncio.TimeoutError, youtube_dl.utils.DownloadError, KeyError):
                try:
                    async with semaphore:
                        info = await self.resolve_youtube_track(title)
                    self.cache_stream_info(info)
                    repaired = True
                except (asyncio.TimeoutError, youtube_dl.utils.DownloadError, IndexError, KeyError):
                    return index, None, None
            thumbnails = info.get("thumbnails") or []
            refreshed_title = info.get("title") or title
            refreshed_thumbnail_url = thumbnails[0]["url"] if thumbnails else thumbnail_url
            refreshed_source_url = info.get("webpage_url") or source_url
            refreshed_duration = info.get("duration") or duration
            metadata_changed = (
                refreshed_title != title
                or refreshed_thumbnail_url != thumbnail_url
                or refreshed_source_url != source_url
                or refreshed_duration != duration
            )
            metadata_update = None
            if repaired or metadata_changed:
                metadata_update = (
                    source_url,
                    refreshed_title,
                    refreshed_thumbnail_url,
                    refreshed_source_url,
                    refreshed_duration,
                )
            return index, {
                "title": refreshed_title,
                "url": info["url"],
                "thumbnails": [{"url": refreshed_thumbnail_url}] if refreshed_thumbnail_url else [],
                "webpage_url": refreshed_source_url,
                "duration": refreshed_duration,
            }, metadata_update

        await update_progress(0, 0, 0)
        resolved_by_index = {}
        repaired_titles = []
        metadata_updates = []
        resolved_count = 0
        queued = 0
        next_queue_index = 0
        queued_batch = []
        resolution_tasks = [
            asyncio.create_task(resolve_track(index, track))
            for index, track in enumerate(selected_tracks)
        ]
        for processed, completed_task in enumerate(asyncio.as_completed(resolution_tasks), start=1):
            index, item, metadata_update = await completed_task
            if cancel_view and cancel_view.cancelled:
                for resolution_task in resolution_tasks:
                    if not resolution_task.done():
                        resolution_task.cancel()
                await asyncio.gather(*resolution_tasks, return_exceptions=True)
                cancel_view.finish()
                await interaction.edit_original_response(
                    content=f"🛑 **Preparing saved playlist cancelled**\nQueued before cancellation: **{queued}** songs.",
                    view=cancel_view,
                )
                return
            resolved_by_index[index] = item
            if item:
                resolved_count += 1
            if metadata_update:
                metadata_updates.append(metadata_update)
                repaired_titles.append(metadata_update[1])
            while next_queue_index in resolved_by_index:
                resolved_item = resolved_by_index.pop(next_queue_index)
                next_queue_index += 1
                if resolved_item:
                    queued_batch.append(resolved_item)
                if len(queued_batch) == PLAYLIST_PROGRESS_BATCH_SIZE:
                    queued += await self.queue_playlist_items(ctx, session, queued_batch)
                    queued_batch = []
            if processed == total or processed % progress_interval == 0:
                await update_progress(processed, resolved_count, queued)
        if cancel_view and cancel_view.cancelled:
            cancel_view.finish()
            await interaction.edit_original_response(
                content=f"🛑 **Preparing saved playlist cancelled**\nQueued before cancellation: **{queued}** songs.",
                view=cancel_view,
            )
            return
        if queued_batch:
            queued += await self.queue_playlist_items(ctx, session, queued_batch)
        for previous_source_url, title, thumbnail_url, source_url, duration in metadata_updates:
            await asyncio.to_thread(
                self.user_playlist_store.update_track_metadata,
                owner.id,
                name,
                previous_source_url,
                title,
                thumbnail_url,
                source_url,
                duration,
            )
        skipped = len(selected_tracks) - queued
        summary = f"Queued {queued} song{'s' if queued != 1 else ''} from {owner.display_name}'s playlist **{escape_markdown(name)}**."
        if skipped:
            summary += f" Skipped {skipped} unavailable track{'s' if skipped != 1 else ''}."
        if repaired_titles:
            displayed_titles = repaired_titles[:10]
            summary += "\n\nRefreshed saved track metadata:\n" + "\n".join(
                f"- **{escape_markdown(truncate_text(title, 150))}**" for title in displayed_titles
            )
            if len(repaired_titles) > len(displayed_titles):
                summary += f"\n- and {len(repaired_titles) - len(displayed_titles)} more"
        if cancel_view:
            cancel_view.finish()
        await interaction.edit_original_response(content=summary)

    @commands.hybrid_command(name='mostplayed')
    async def most_played(self, ctx):
        """Show this server's 20 most played songs."""
        await self.get_song_ranking(ctx, "top_played", "Most Played Songs", "No songs have been played in this server yet.")

    @commands.hybrid_command(name='mostliked')
    async def most_liked(self, ctx):
        """Show this server's 20 most liked songs."""
        await self.get_song_ranking(ctx, "top_liked", "Most Liked Songs", "No songs have been liked in this server yet.")

    @commands.hybrid_command(name='mostdisliked')
    async def most_disliked(self, ctx):
        """Show this server's 20 most disliked songs."""
        await self.get_song_ranking(ctx, "top_disliked", "Most Disliked Songs", "No songs have been disliked in this server yet.")

    @commands.hybrid_command(name='myliked')
    async def my_liked(self, ctx):
        """Show your 20 most recently liked songs in this server."""
        await self.get_song_ranking(
            ctx,
            "liked_by_user",
            "Your Liked Songs",
            "You have not liked any songs in this server yet.",
            ctx.author.id,
        )

    @commands.hybrid_command(name='play')
    async def play(self, ctx, *, query: str):
        """Play a search result, YouTube URL, or Spotify track, album, or playlist."""
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()
        if not await self.enforce_command_cooldown(
            ctx, "play", getattr(self.bot, "play_cooldown_seconds", PLAY_COOLDOWN_SECONDS)
        ):
            return
        try:
            voice_channel = ctx.author.voice.channel
        except AttributeError:
            await ctx.send("*You are not connected to a voice channel.*")
            await self.add_reaction(ctx, "❌")
            return
        
        spotify_value, _, spotify_arguments = query.strip().partition(" ")
        spotify_resource = parse_resource(spotify_value)
        if spotify_resource:
            try:
                config = self.get_guild_config(ctx.guild.id)
                options = parse_playlist_options(
                    spotify_arguments,
                    config["playlist_max_tracks"],
                    config["playlist_default_tracks"],
                    config["playlist_default_shuffle"],
                )
            except (SpotifyError, ValueError) as error:
                await ctx.send(f"Invalid Spotify playlist options: {error}")
                return
            if spotify_resource.resource_type == "playlist" and not spotify_arguments:
                connection = await self.get_available_spotify_connection(ctx)
                if not connection:
                    async def retry(credentials):
                        await self.play(ctx, query=query)

                    await self.offer_personal_spotify_setup(
                        ctx,
                        "This server does not have usable Spotify credentials, and you have not configured private Spotify access.",
                        retry,
                    )
                    return
                credentials, scope = connection
                try:
                    client = SpotifyClient(*credentials, market=os.getenv("SPOTIFY_MARKET", "US"))
                    playlist_token = await self.get_spotify_playlist_token(ctx, credentials, scope)
                    if not playlist_token:
                        return
                    async with asyncio.timeout(getattr(self.bot, "ytdlp_timeout_seconds", YTDLP_TIMEOUT_SECONDS)):
                        tracks = await asyncio.to_thread(client.get_tracks, spotify_resource, playlist_token)
                except SpotifyPlaylistAuthorizationError:
                    if scope == "guild":
                        self.command_cooldowns.pop((ctx.guild.id, ctx.author.id, "play"), None)
                        await self.retry_personal_spotify_playlist(ctx, spotify_resource, options)
                    else:
                        await ctx.send("Your Spotify account cannot access that playlist.")
                    return
                except (SpotifyError, requests.RequestException, asyncio.TimeoutError):
                    await ctx.send("Spotify could not load that playlist. Try again shortly.")
                    return
                if not tracks:
                    await ctx.send("That Spotify playlist has no playable tracks.")
                    return
                await ctx.send(
                    f"Configure this Spotify playlist import (**{len(tracks)}** available tracks). Choose **ordered** to keep Spotify's order "
                    "or **shuffle** to randomize the selected tracks.",
                    view=SpotifyPlaylistLauncher(self, ctx, spotify_resource, tracks),
                )
                return
            if spotify_resource.resource_type == "track":
                options = {"count": 1, "ranges": [(1, 1)], "shuffle": False}
            await self.import_spotify(ctx, spotify_resource, options)
            return

        if self.is_youtube_playlist_url(spotify_value):
            try:
                config = self.get_guild_config(ctx.guild.id)
                options = parse_playlist_options(
                    spotify_arguments,
                    config["playlist_max_tracks"],
                    config["playlist_default_tracks"],
                    config["playlist_default_shuffle"],
                )
            except (SpotifyError, ValueError) as error:
                await ctx.send(f"Invalid YouTube playlist options: {error}")
                return
            if not spotify_arguments:
                status_message = await ctx.send("🎵 **Loading YouTube playlist**\nFinding available videos...")

                async def update_loading_progress(processed, total):
                    await self.edit_playlist_import_status(
                        status_message, self.playlist_loading_progress(processed, total)
                    )

                try:
                    entries = await self.get_youtube_playlist_entries(
                        spotify_value, progress_callback=update_loading_progress
                    )
                except (asyncio.TimeoutError, youtube_dl.utils.DownloadError):
                    await self.edit_playlist_import_status(
                        status_message, "YouTube could not load that playlist."
                    )
                    return
                entries = [entry for entry in entries if entry]
                if not entries:
                    await self.edit_playlist_import_status(
                        status_message, "That YouTube playlist has no playable videos."
                    )
                    return
                await status_message.edit(
                    content=(
                    f"Configure this YouTube playlist import (**{len(entries)}** available videos). Choose **ordered** to keep YouTube's order "
                    "or **shuffle** to randomize the selected videos."
                    ),
                    view=YouTubePlaylistLauncher(self, ctx, spotify_value, entries),
                )
                return
            await self.import_youtube_playlist(ctx, spotify_value, options)
            return

        session = await self.get_session(ctx)
        if session is None:
            return
        
        try:
            async with ctx.typing():
                is_url = urlparse(query).scheme in {"http", "https"}
                source = query if is_url else f"ytsearch:{query}"
                info = await self.get_youtube_info(source, {'format': 'bestaudio', 'noplaylist': True})
                if not is_url:
                    info = info['entries'][0]
        except (asyncio.TimeoutError, youtube_dl.utils.DownloadError, IndexError, KeyError):
            await ctx.send("YouTube could not resolve that source. Try another search or link.")
            return

        async with ctx.typing():
            url = info['url']
            thumb = info['thumbnails'][0]['url']
            title = info['title']
            ytube_url = info['webpage_url']
            duration = info['duration']
            user = ctx.author.id

            duration_str = await convert_duration_pretty(duration)

            # Get dominant color from thumbnail
            dominant_color = await get_dominant_color(thumb)
            
            session.q.enqueue(title, url, thumb, ytube_url, duration, user)
            voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            if not voice:
                await voice_channel.connect()
                voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
                asyncio.create_task(self.auto_disconnect(ctx, voice))  # Start the auto-disconnect task

            embed = discord.Embed(title=f'{escape_markdown(truncate_text(title))}', url=ytube_url, color=discord.Color(dominant_color))  
            embed.set_thumbnail(url=thumb)
            embed.set_author(name="Music Stream Link", url=url)

            playback_active = (
                voice.is_playing()
                or voice.is_paused()
                or session.q.continuation_pending
                or (session.q.loop_current and not session.q.is_empty())
            )
            if playback_active:
                embed.description = (
                    f"*🎵 Added to queue in <#{session.channel}>*"
                )
                embed.add_field(name="Duration", value=duration_str, inline=True)
                embed.add_field(name="Added By", value=f"<@{ctx.author.id}>", inline=True)
                queued_track = session.q.queue[-1]
                queued_message = await ctx.send(
                    embed=embed,
                    view=QueuedTrackControls(self, ctx.guild.id, queued_track),
                )
                session.queued_track_messages[id(queued_track)] = (queued_track, queued_message)
                await self.add_reaction(ctx, "✅")
            else:
                embed.description = (
                    f"*▶️ Now playing in <#{session.channel}>*"
                )
                embed.add_field(name="Duration", value=duration_str, inline=True)
                embed.add_field(name="Added By", value=f"<@{ctx.author.id}>", inline=True)
                controls = MusicControls(
                    self,
                    ctx.guild.id,
                    session.q.current_music.ytube,
                    session.q.current_music.duration,
                )
                session.now_playing_message = await ctx.send(embed=embed, view=controls)
                controls.message = session.now_playing_message
                session.now_playing_track_url = session.q.current_music.ytube
                session.now_playing_messages[id(session.now_playing_message)] = (
                    session.now_playing_message,
                    session.q.current_music.ytube,
                )
                session.q.set_last_as_current()
                source = await discord.FFmpegOpusAudio.from_probe(url, **FFMPEG_OPTIONS)
                completed_track = session.q.current_music
                voice.play(
                    source,
                    after=lambda error: self.prepare_continue_queue(ctx, completed_track, error),
                )
                session.q.start_playback()
                if self.song_stats_store:
                    try:
                        await asyncio.to_thread(
                            self.song_stats_store.record_play,
                            ctx.guild.id,
                            completed_track.ytube,
                            completed_track.title,
                            completed_track.thumb,
                        )
                    except Exception as error:
                        logger.warning("Guild %s: failed to record song play: %s", ctx.guild.id, error)
                await self.add_reaction(ctx, "▶️")
    @commands.hybrid_command(name='skip', aliases=['next'])
    async def skip(self, ctx):
        """Skip to the next queued song without removing the current song."""
        if not await self.ensure_user_in_voice(ctx):
            return
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session(ctx)
        if session is None:
            return

        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if not session.q.theres_next():
            await self.retire_now_playing_controls(session)
            await self.retire_queued_track_controls_except(session)
            session.q.clear_queue()
            voice.stop()
            await self.add_reaction(ctx, "⏭️")
            return
        
        if voice.is_playing():
            session.q.skip_requested = True
            voice.stop()
            await self.add_reaction(ctx, "⏭️")

    @commands.hybrid_command(name='seek')
    async def seek(self, ctx, *, position: str):
        """Seek within the current song."""
        await self.seek_current_track(ctx, position)

    @commands.hybrid_command(name='restart')
    async def restart(self, ctx):
        """Restart the current song from the beginning."""
        if not await self.ensure_user_in_voice(ctx) or not await self.ensure_bot_in_voice(ctx):
            return
        session = await self.get_session(ctx)
        if session is None:
            return
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if not voice or not (voice.is_playing() or voice.is_paused()):
            await ctx.send("*There is no audio currently playing.*")
            return
        session.q.restart_requested = True
        session.q.restart_position = 0
        voice.stop()
        await ctx.send("Restarted the current song.")

    @commands.hybrid_command(name='shuffle')
    async def shuffle_queue(self, ctx):
        """Shuffle upcoming tracks while keeping the current track in place."""
        if not await self.ensure_user_in_voice(ctx):
            return
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session(ctx)
        if session is None:
            return
        if not session.q.theres_next():
            await ctx.send("*There are no upcoming songs to shuffle.*")
            await self.add_reaction(ctx, "🤷‍♂️")
            return

        session.q.shuffle_upcoming()
        await ctx.send("Upcoming songs shuffled.")

    @commands.hybrid_command(name='leave')
    async def leave(self, ctx):
        """
        Disconnects the bot from the voice channel.
        """
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if voice and voice.is_connected():
            session = await self.get_session(ctx)
            if session is None:
                return
            
            session.q.clear_queue()
            await self.retire_now_playing_controls(session)
            await self.retire_queued_track_controls_except(session)
            await voice.disconnect()

            await asyncio.sleep(0)
            if session in sessions:
                sessions.remove(session)
                
            await self.add_reaction(ctx, "👋")
        else:
            await ctx.send("*The bot is not connected to a voice channel.*")
            await self.add_reaction(ctx, "🙅‍♂️")

    @commands.hybrid_command(name='pause')
    async def pause(self, ctx):
        """
        Pauses the current song if playing.
        """
        if not await self.ensure_user_in_voice(ctx):
            return
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session(ctx)
        if session is None:
            return
        
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if voice.is_playing():
            voice.pause()
            session.q.pause_playback()
            await self.add_reaction(ctx, "⏸️")
        else:
            await ctx.send("*There is no audio currently playing.*")
            await self.add_reaction(ctx, "🤔")

    @commands.hybrid_command(name='resume')
    async def resume(self, ctx):
        """
        Resumes the currently paused song.
        """
        if not await self.ensure_user_in_voice(ctx):
            return
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session(ctx)
        if session is None:
            return
        
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if voice.is_paused():
            voice.resume()
            session.q.resume_playback()
            await self.add_reaction(ctx, "▶️")
        else:
            await ctx.send("*The music is not paused.* 🔊🆙")
            await self.add_reaction(ctx, "❓")

    @commands.hybrid_command(name='stop', aliases=['reset'])
    async def stop(self, ctx):
        """
        Stops playing audio and clears the queue.
        """
        if not await self.ensure_user_in_voice(ctx):
            return
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session(ctx)
        if session is None:
            return

        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if voice.is_playing():
            await self.retire_now_playing_controls(session)
            await self.retire_queued_track_controls_except(session)
            voice.stop()
            session.q.clear_queue()
            await self.add_reaction(ctx, "⏹️")
        else:
            await ctx.send("*There is no music playing.*")
            await self.add_reaction(ctx, "🦗")

    @commands.hybrid_command(name='queue', aliases=['q'])
    async def queue(self, ctx):
        """
        Displays the current queue of songs in groups of 10.
        """
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session_in_guild(ctx)
        if session is None:
            return

        if session.q.is_empty():
            await ctx.send("*The queue is currently empty.*")
            await self.add_reaction(ctx, "✅")
            return

        # Get the dominant color of the first song's thumbnail
        first_song_thumb = session.q.queue[0].thumb if session.q.queue else None
        dominant_color = await get_dominant_color(first_song_thumb) if first_song_thumb else 0x3498db  # Default to blue

        # Generate queue list with the actively playing track called out by identity.
        queue_list = [
            f"{'▶️ **Now Playing** ' if song is session.q.current_music else ''}"
            f"**{i + 1}.** {escape_markdown(truncate_text(song.title))}\n"
            f"{await convert_duration_pretty(song.duration)} | [Link]({song.ytube}) | <@{song.user}>"
            for i, song in enumerate(session.q.queue)
        ]

        # Split the queue into chunks of 10 songs per page
        chunk_size = 10
        chunks = [queue_list[i:i + chunk_size] for i in range(0, len(queue_list), chunk_size)]
        current_track_index = session.q.queued_track_index(session.q.current_music)
        current_track_position = current_track_index + 1 if current_track_index is not None else "Unknown"
        initial_page = current_track_index // chunk_size if current_track_index is not None else 0

        embeds = []

        for chunk in chunks:
            embed = discord.Embed(
                title=f"🎧 Current Queue (Playing #{current_track_position})",
                color=discord.Color(dominant_color),
            )

            # Join the chunk into a single string for the embed
            embed.description = "\n\n".join(chunk)  # Two newlines for better separation
            embed.description += f"\n\nChannel: <#{session.channel}>" # Append channel to each page

            # Set the thumbnail of the first song in the queue
            if first_song_thumb:
                embed.set_thumbnail(url=first_song_thumb)

            embeds.append(embed)

        await Paginator.CustomPaginator(timeout=120, InitialPage=initial_page).start(ctx, pages=embeds)

        await self.add_reaction(ctx, "📜")

    @commands.hybrid_command(name='remove', aliases=['rm'])
    async def remove(self, ctx):
        """Open a private selector for removing an upcoming queued track."""
        if not await self.ensure_user_in_voice(ctx):
            return
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session(ctx)
        if session is None:
            return

        current_index = session.q.queued_track_index(session.q.current_music)
        tracks = session.q.queue[current_index + 1:] if current_index is not None else []
        if not tracks:
            await ctx.send("*There are no upcoming songs to remove.*")
            await self.add_reaction(ctx, "🤷‍♂️")
            return

        if ctx.interaction:
            await ctx.send(
                "Choose a queued song to remove.",
                ephemeral=True,
                view=QueueRemoveSelector(
                    self,
                    ctx.guild.id,
                    ctx.channel,
                    ctx.author.id,
                    tracks[:25],
                ),
            )
            return

        await ctx.send(
            "Select a queued song to remove.",
            view=QueueRemoveLauncher(self, ctx.guild.id, ctx.channel, ctx.author.id),
        )

    @commands.hybrid_command(name='move')
    async def move(self, ctx):
        """Open a menu to move an upcoming queued track."""
        if not await self.ensure_user_in_voice(ctx):
            return
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session(ctx)
        if session is None:
            return
        current_index = session.q.queued_track_index(session.q.current_music)
        tracks = session.q.queue[current_index + 1:] if current_index is not None else []
        if not tracks:
            await ctx.send("*There are no upcoming songs to move.*")
            await self.add_reaction(ctx, "🤷‍♂️")
            return

        if ctx.interaction:
            await ctx.send(
                "Choose an upcoming song to move.",
                ephemeral=True,
                view=QueueMoveSelector(self, ctx.guild.id, ctx.author.id, tracks[:25]),
            )
            return

        await ctx.send(
            "Select a queued song to move.",
            view=QueueMoveLauncher(self, ctx.guild.id, ctx.author.id),
        )

    @commands.hybrid_command(name='clearqueue', aliases=['clearnext', 'clearNext', 'cn', 'clear_queue', 'cq', 'clear_next', 'clearQueue'])
    async def clearqueue(self, ctx):
        """
        Clears the current queue of songs, except the currently playing song.
        """
        if not await self.ensure_user_in_voice(ctx):
            return
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session(ctx)
        if session is None:
            return

        if session.q.is_empty():
            await ctx.send("*The queue is already empty.*")
            await self.add_reaction(ctx, "✅")
            return
        
        if session.q.size() == 1:
            await ctx.send("*No other songs in queue. Use 'stop' to clear the currently playing song.*")
            await self.add_reaction(ctx, "✅")
            return

        await self.retire_queued_track_controls_except(session, session.q.current_music)
        session.q.clear_queue_except_current()
        await ctx.send("*The queue has been cleared.*")
        await self.add_reaction(ctx, "🧹")

    @commands.hybrid_command(name='here', aliases=['join'])
    async def here(self, ctx):
        """
        Moves the bot to the user's current voice channel and updates the session.
        """
        if not ctx.author.voice:
            await ctx.send("*You are not connected to a voice channel.*")
            await self.add_reaction(ctx, "❌")
            return

        voice_channel = ctx.author.voice.channel

        session = await self.get_session_in_guild(ctx)

        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)

        # If the bot is already connected, move it
        if voice and voice.is_connected():
            if voice.channel.id == voice_channel.id:
                await ctx.send(f"*I'm already in <#{voice_channel.id}>* 📢")
                await self.add_reaction(ctx, "🤔")
                return
            await voice.move_to(voice_channel)
            await ctx.send(f"*Moved to:* <#{voice_channel.id}>")
        else:
            # If not connected, join the new channel
            await voice_channel.connect()
            voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            asyncio.create_task(self.auto_disconnect(ctx, voice))  # Start the auto-disconnect task
            await ctx.send(f"*Joined:* <#{voice_channel.id}>")

        # Update the session's channel reference
        session.channel = voice_channel.id
        await self.add_reaction(ctx, "🔄")

    @commands.hybrid_command(name='playingnow', aliases=['nowPlaying', 'music', 'now', 'musicnow', 'musicNow', 'playing', 'nowplaying', 'playingNow'])
    async def playingnow(self, ctx):
        """
        Gets the current song playing.
        """
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session_in_guild(ctx)
        if session is None:
            return

        if session.q.is_empty():
            await ctx.send("*Nothing is in queue.*")
            await self.add_reaction(ctx, "🚫")
            return
        
        current_music = session.q.get_current_music()

        if not current_music or current_music.title == '':
            await ctx.send("*Nothing is playing.*")
            await self.add_reaction(ctx, "🚫")
            return
        
        # Convert duration to HH:MM:SS format
        duration = session.q.current_music.duration
        duration_str = await convert_duration_pretty(duration)

        # Get dominant color from thumbnail
        dominant_color = await get_dominant_color(session.q.current_music.thumb)

        # Create an embed with the song details
        embed = discord.Embed(
            title=f'{escape_markdown(truncate_text(session.q.current_music.title))}',
            url=session.q.current_music.ytube,
            color=discord.Color(dominant_color),
            description=(
                f"🎧 *Currently playing in <#{session.channel}>*"
            )
        )
        embed.set_thumbnail(url=session.q.current_music.thumb)
        embed.set_author(name="Music Stream Link", url=session.q.current_music.url)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Added By", value=f"<@{ctx.author.id}>", inline=True)

        controls = MusicControls(
            self,
            ctx.guild.id,
            current_music.ytube,
            current_music.duration,
            session.q.current_position(),
        )
        now_playing_message = await ctx.send(embed=embed, view=controls)
        controls.message = now_playing_message
        session.now_playing_messages[id(now_playing_message)] = (
            now_playing_message,
            current_music.ytube,
        )
        await self.add_reaction(ctx, "🎶")

    @commands.hybrid_command(name="search")
    async def search(self, ctx, *, query: str):
        """Search YouTube and choose a result to add to the queue."""
        if not await self.enforce_command_cooldown(
            ctx, "search", getattr(self.bot, "search_cooldown_seconds", SEARCH_COOLDOWN_SECONDS)
        ):
            return
        if not await self.claim_expensive_command(ctx, "search"):
            return
        try:
            await self.run_search(ctx, query)
        finally:
            self.release_expensive_command(ctx, "search")

    async def run_search(self, ctx, query):
        embeds = []
        results = []
        async with ctx.typing():  # Shows "Bot is typing..." while processing
            
            ydl_opts = {
                'quiet': True,
                'extract_flat': True,
                'default_search': 'ytsearch20',
                'skip_download': True,
            }

            try:
                info = await self.get_youtube_info(f"ytsearch20:{query}", ydl_opts)
            except (asyncio.TimeoutError, youtube_dl.utils.DownloadError):
                await ctx.send("YouTube search timed out or failed. Try again shortly.")
                return

            if not info or "entries" not in info or not info["entries"]:
                await ctx.send("❌ No results found.")
                return

            results = [
                {
                    "title": entry["title"],
                    "url": entry["url"],
                    "duration": await convert_duration_pretty(entry["duration"]),
                    "channel": entry.get("uploader", "Unknown"),
                    "thumbnail": entry['thumbnails'][0]['url'],
                }
                for entry in info["entries"][:20]
            ]

            first_thumb = results[0]["thumbnail"] if results else None
            dominant_color = await get_dominant_color(first_thumb)

            search_list = [
                f"**{i + 1}.** {escape_markdown(truncate_text(video['title']))}\n"
                f"{video['duration']} | [Link]({video['url']}) | {escape_markdown(truncate_text(video['channel']))}"
                for i, video in enumerate(results)
            ]

            chunk_size = 10
            chunks = [search_list[i:i + chunk_size] for i in range(0, len(search_list), chunk_size)]

            for chunk in chunks:
                embed = discord.Embed(title="🔎 YouTube Search Results", color=discord.Color(dominant_color))
                embed.description = "\n\n".join(chunk)

                if first_thumb:
                    embed.set_thumbnail(url=first_thumb)

                embeds.append(embed)
        
        # Start paginator (this automatically handles page navigation)
        await Paginator.CustomPaginator(timeout=120).start(ctx, pages=embeds)

        await ctx.send(content="", view=YouTubeSearchDropdown(ctx, self.bot, results))

        await self.add_reaction(ctx, "🔍")

    @play.error
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("*❌ Please provide a search query or YouTube URL when using the `play` command. Usage: `.play <query>`*")
            await self.add_reaction(ctx, "❌")
            return

    @search.error
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("*❌ Please provide a search query or YouTube URL when using the `search` command. Usage:`.search <query>`*")
            await self.add_reaction(ctx, "❌")
            return

def setup(bot):
    bot.add_cog(Music(bot))


async def get_dominant_color(image_url):
    """
    Fetches the image from the given URL and returns the dominant color as a Discord-compatible integer.
    Returns blue (default) if the URL is invalid.
    
    :param image_url: str - URL of the image
    :return: int - Discord embed color
    """
    if not image_url or not image_url.startswith(("http://", "https://")):
        logger.warning(f"Invalid or missing image URL: '{image_url}' (Defaulting to blue)")
        return 0x3498db  # Default to blue if the image URL is invalid

    try:
        response = requests.get(image_url, timeout=5)  # 5-second timeout to avoid hanging
        response.raise_for_status()  # Raise an error for bad HTTP responses (e.g., 404, 500)

        image = Image.open(io.BytesIO(response.content))
        image = image.convert("RGB")  # Ensure it's in RGB format
        image = image.resize((50, 50))  # Reduce size for faster processing

        pixels = list(image.get_flattened_data())  # Get pixel data
        avg_color = tuple(sum(x) // len(x) for x in zip(*pixels))  # Average color

        return (avg_color[0] << 16) + (avg_color[1] << 8) + avg_color[2]  # Convert RGB to int
    
    except Exception as e:
        logger.error(f"Failed to get dominant color: {e}")
        return 0x3498db  # Default to blue if an error occurs
    
async def convert_duration_pretty(duration):
    """
    Convert a duration in seconds to a formatted string in HH:MM:SS format.

    If the duration is `None` or invalid, returns "Unknown".

    :param duration: int or None - The duration in seconds.
    :return: str - The formatted duration string in HH:MM:SS format.
    """
    if not duration:
        return "Unknown"

    duration = int(duration)  # Ensure it's an integer
    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def escape_markdown(text):
    """
    Escape Discord markdown characters to prevent unintended formatting.

    This function escapes characters such as *, _, `, ~, and | to ensure they
    are displayed as plain text instead of being interpreted as formatting.

    :param text: str - The input string containing possible markdown characters.
    :return: str - The escaped string.
    """
    return re.sub(r"([*_`~|])", r"\\\1", text)

def truncate_text(text, max_length=100):
    """
    Truncate a string to a specified maximum length and append '...' if truncated.

    This function ensures that long text strings do not exceed a given length,
    preventing display issues in embeds or UI elements.

    :param text: str - The input string to be truncated.
    :param max_length: int - The maximum allowed length before truncation (default: 100).
    :return: str - The truncated string with '...' appended if necessary.
    """
    return text[:max_length] + "..." if len(text) > max_length else text