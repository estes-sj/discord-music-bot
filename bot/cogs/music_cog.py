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

# List of active sessions.
sessions = []

# YouTube will sometimes try to disconnect the bot from its servers. Use this to reconnect instantly.
# (Because of this disconnect/reconnect cycle, sometimes you will listen a sudden and brief stop)
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

logger = logging.getLogger("discord")

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.spotify_import_locks = {}
        self.spotify_store = getattr(self.bot, "spotify_store", None)
        self.song_stats_store = getattr(self.bot, "song_stats_store", None)
        if self.spotify_store is None:
            logger.warning("Spotify support is not configured for this bot.")
        if self.song_stats_store is None:
            logger.warning("Song statistics are not configured for this bot.")


    @property
    def spotify_max_tracks(self):
        return int(os.getenv("PLAYLIST_MAX_TRACKS", "20"))

    @property
    def spotify_default_tracks(self):
        return min(int(os.getenv("PLAYLIST_DEFAULT_TRACKS", "20")), self.spotify_max_tracks)

    @property
    def spotify_default_shuffle(self):
        return os.getenv("PLAYLIST_DEFAULT_SHUFFLE", "false").lower() == "true"

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
                    await ctx.message.add_reaction("😵")
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
                await ctx.send("*Queue has ended* ✅")
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
        ffmpeg_options = FFMPEG_OPTIONS.copy()
        if start_position:
            ffmpeg_options["before_options"] += f" -ss {start_position:.3f}"
        source = await discord.FFmpegOpusAudio.from_probe(session.q.current_music.url, **ffmpeg_options)

        completed_track = session.q.current_music
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

        controls = MusicControls(self, ctx.guild.id, completed_track.ytube)
        if (
            session.now_playing_message
            and session.now_playing_track_url == completed_track.ytube
        ):
            try:
                await session.now_playing_message.edit(embed=embed, view=controls)
                return
            except (discord.Forbidden, discord.NotFound):
                session.now_playing_message = None
                session.now_playing_track_url = None

        session.now_playing_message = await ctx.send(embed=embed, view=controls)
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
        Automatically disconnects the bot if no one is in the voice channel 
        or if nothing has been playing for 10 minutes.
        """
        inactivity_duration = 600  # 10 minutes in seconds
        check_interval = 60  # Check every 60 seconds
        elapsed_time = 0

        while True:
            await asyncio.sleep(check_interval)
            elapsed_time += check_interval

            # Check if bot is not in the voice channel
            if not voice.is_connected():
                break

            # Check if voice channel is empty
            if len(voice.channel.members) == 1:  # Only the bot is left
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

            # Check if nothing is playing
            if not voice.is_playing() and not voice.is_paused():
                if elapsed_time >= inactivity_duration:
                    await ctx.send("🔇 *No activity detected for 10 minutes. Disconnecting...*")
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
                elapsed_time = 0  # Reset the timer if something is playing

    async def ensure_user_in_voice(self, ctx):
        """
        Ensures that the user issuing the command is in a voice channel.

        :param ctx: The command context.
        :return: True if the user is in a voice channel, False otherwise.
        """
        if not ctx.author.voice:
            await ctx.send("*You are not connected to a voice channel.*")
            await ctx.message.add_reaction("❌")
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
            await ctx.message.add_reaction("🙅‍♂️")
            return False
        return True

    async def get_spotify_credentials(self, ctx):
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            await ctx.send("Spotify support is not configured by this bot operator.")
            return None
        try:
            credentials = await asyncio.to_thread(store.get_credentials, ctx.guild.id)
        except SpotifyStoreError:
            await ctx.send("Stored Spotify credentials are unavailable. Run `.spotifyclear` and try again.")
            return None
        if credentials:
            return credentials

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
            return None

        def valid_reply(message):
            return message.author.id == ctx.author.id and message.channel.id == dm.id

        try:
            reply = await self.bot.wait_for("message", check=valid_reply, timeout=300)
        except asyncio.TimeoutError:
            await ctx.send("Spotify configuration timed out. Run `.play` with the Spotify link again to retry.")
            return None

        values = [line.strip() for line in reply.content.splitlines() if line.strip()]
        if len(values) != 2:
            await dm.send("I need exactly two non-empty lines: Client ID, then Client Secret.")
            return None
        client_id, client_secret = values
        try:
            client = SpotifyClient(client_id, client_secret)
            await asyncio.to_thread(client.validate_credentials)
            await asyncio.to_thread(store.save_credentials, ctx.guild.id, client_id, client_secret, ctx.author.id)
        except (SpotifyError, requests.RequestException, SpotifyStoreError):
            await dm.send("Spotify could not validate those credentials. Nothing was saved; try again with a new `.play` request.")
            return None
        await dm.send("Spotify credentials were saved for this server.")
        await ctx.send("Spotify is configured for this server. Re-run your `.play` command.")
        return None

    async def ensure_spotify_voice_access(self, ctx):
        if not await self.ensure_user_in_voice(ctx):
            return False
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if voice and voice.is_connected() and ctx.author.voice.channel != voice.channel:
            await ctx.send("Join my voice channel before changing Spotify configuration.")
            return False
        return True

    async def get_spotify_playlist_token(self, ctx, credentials):
        store = self.bot.spotify_store
        now = int(time.time())
        try:
            token_record = await asyncio.to_thread(store.get_playlist_token, ctx.guild.id)
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
                await asyncio.to_thread(
                    store.save_playlist_token,
                    ctx.guild.id,
                    access_token,
                    refresh_token,
                    now + expires_in,
                    ctx.author.id,
                )
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
                "Authorize Spotify playlist access for this server using this link:\n"
                f"<{authorization_url}>\n\n"
                "After Spotify redirects, copy the complete URL beginning with `http://127.0.0.1` "
                "from your browser address bar and reply with it here. A browser connection error after the "
                "redirect is expected. If the address bar does not update, open Developer Tools, select the "
                "Network tab, refresh the authorization page, click Agree, then copy the request URL for "
                "`spotify-callback`."
            )
            await ctx.send("I sent you a DM to authorize Spotify playlist access for this server.")
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
            await asyncio.to_thread(
                store.save_playlist_token,
                ctx.guild.id,
                access_token,
                refresh_token,
                now + expires_in,
                ctx.author.id,
            )
        except (SpotifyPlaylistAuthorizationError, requests.RequestException, SpotifyStoreError):
            await dm.send("Spotify could not complete playlist authorization. Nothing was saved; run `.play` with the playlist again to retry.")
            return None
        await dm.send("Spotify playlist access was authorized for this server.")
        return access_token

    async def resolve_youtube_track(self, query):
        def extract():
            with youtube_dl.YoutubeDL({'format': 'bestaudio', 'noplaylist': True}) as ydl:
                return ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]

        return await asyncio.to_thread(extract)

    @staticmethod
    def is_youtube_playlist_url(value):
        parsed = urlparse(value)
        return parsed.netloc.lower() in {"youtube.com", "www.youtube.com", "m.youtube.com"} and bool(
            parse_qs(parsed.query).get("list")
        )

    async def get_youtube_playlist_entries(self, url):
        def extract():
            with youtube_dl.YoutubeDL({'format': 'bestaudio', 'extract_flat': False}) as ydl:
                return ydl.extract_info(url, download=False).get('entries', [])

        return await asyncio.to_thread(extract)

    async def import_youtube_playlist(self, ctx, url, options):
        try:
            entries = await self.get_youtube_playlist_entries(url)
        except Exception:
            await ctx.send("YouTube could not load that playlist.")
            return
        selected_entries = select_tracks([entry for entry in entries if entry], options)
        if not selected_entries:
            await ctx.send("No playable YouTube videos matched that selection.")
            return

        session = await self.get_session(ctx)
        if session is None:
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
        await ctx.send(f"YouTube playlist import: queued {queued} video{'s' if queued != 1 else ''}. Use `.q` to view the queue.")

    async def import_spotify(self, ctx, resource, options):
        credentials = await self.get_spotify_credentials(ctx)
        if not credentials:
            return
        lock = self.spotify_import_locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            try:
                client = SpotifyClient(*credentials, market=os.getenv("SPOTIFY_MARKET", "US"))
                playlist_token = None
                if resource.resource_type == "playlist":
                    playlist_token = await self.get_spotify_playlist_token(ctx, credentials)
                    if not playlist_token:
                        return
                tracks = await asyncio.to_thread(client.get_tracks, resource, playlist_token)
            except (SpotifyError, requests.RequestException) as error:
                message = f"Spotify could not load that link: {error}"
                if resource.resource_type == "playlist" and isinstance(error, SpotifyPlaylistAuthorizationError):
                    authorizer_id = await asyncio.to_thread(
                        self.spotify_store.get_playlist_authorizer, ctx.guild.id
                    )
                    if authorizer_id:
                        message += f" Playlist access was configured by <@{authorizer_id}>."
                await ctx.send(message)
                return
            selected_tracks = select_tracks(tracks, options)
            if not selected_tracks:
                await ctx.send("No playable Spotify tracks matched that selection.")
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

            session = await self.get_session(ctx)
            if session is None:
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
                    duration_str = await convert_duration_pretty(queued_track.duration)
                    dominant_color = await get_dominant_color(queued_track.thumb)
                    embed = discord.Embed(
                        title=escape_markdown(truncate_text(queued_track.title)),
                        url=queued_track.ytube,
                        color=discord.Color(dominant_color),
                        description=f"*🎵 Added to queue in <#{session.channel}>*",
                    )
                    embed.set_thumbnail(url=queued_track.thumb)
                    embed.set_author(name="Music Stream Link", url=queued_track.url)
                    embed.add_field(name="Duration", value=duration_str, inline=True)
                    embed.add_field(name="Added By", value=f"<@{ctx.author.id}>", inline=True)
                    queued_message = await ctx.send(
                        embed=embed,
                        view=QueuedTrackControls(self, ctx.guild.id, queued_track),
                    )
                    session.queued_track_messages[id(queued_track)] = (queued_track, queued_message)
                return

            summary = f"Spotify import: queued {len(resolved)} match{'es' if len(resolved) != 1 else ''}"
            if skipped:
                summary += f"; skipped {skipped} track{'s' if skipped != 1 else ''} with no YouTube match"
            await ctx.send(summary + ". Use `.q` to view the queue.")

    @commands.command(name='spotifyclear')
    async def spotify_clear(self, ctx):
        """Delete this server's stored Spotify application credentials."""
        if not await self.ensure_spotify_voice_access(ctx):
            return
        store = getattr(self.bot, "spotify_store", None)
        if store is None:
            await ctx.send("Spotify support is not configured by this bot operator.")
            return
        deleted = await asyncio.to_thread(store.clear_credentials, ctx.guild.id)
        await ctx.send("Spotify credentials cleared for this server." if deleted else "No Spotify credentials are configured for this server.")

    @commands.command(name='spotifystatus')
    async def spotify_status(self, ctx):
        """Show whether this server has Spotify application credentials configured."""
        if not await self.ensure_spotify_voice_access(ctx):
            return
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
        rows = await asyncio.to_thread(
            getattr(self.song_stats_store, method_name), ctx.guild.id, *arguments
        )
        await self.send_song_ranking(ctx, title, rows, empty_message)

    @commands.command(name='mostplayed')
    async def most_played(self, ctx):
        """Show this server's 20 most played songs."""
        await self.get_song_ranking(ctx, "top_played", "Most Played Songs", "No songs have been played in this server yet.")

    @commands.command(name='mostliked')
    async def most_liked(self, ctx):
        """Show this server's 20 most liked songs."""
        await self.get_song_ranking(ctx, "top_liked", "Most Liked Songs", "No songs have been liked in this server yet.")

    @commands.command(name='mostdisliked')
    async def most_disliked(self, ctx):
        """Show this server's 20 most disliked songs."""
        await self.get_song_ranking(ctx, "top_disliked", "Most Disliked Songs", "No songs have been disliked in this server yet.")

    @commands.command(name='myliked')
    async def my_liked(self, ctx):
        """Show your 20 most recently liked songs in this server."""
        await self.get_song_ranking(
            ctx,
            "liked_by_user",
            "Your Liked Songs",
            "You have not liked any songs in this server yet.",
            ctx.author.id,
        )

    @commands.command(name='play')
    async def play(self, ctx, *, query):
        """Play a search result, YouTube URL, or Spotify track, album, or playlist.

        :param ctx: discord.ext.commands.Context
        :param query: Search text, a YouTube URL, or a Spotify URL.

        Spotify album and playlist imports support --count N, --range START-END,
        --ordered, and --shuffle. Playlists without options open a configuration prompt.
        """
        try:
            voice_channel = ctx.author.voice.channel
        except AttributeError:
            await ctx.send("*You are not connected to a voice channel.*")
            await ctx.message.add_reaction("❌")
            return
        
        spotify_value, _, spotify_arguments = query.strip().partition(" ")
        spotify_resource = parse_resource(spotify_value)
        if spotify_resource:
            try:
                options = parse_playlist_options(
                    spotify_arguments,
                    self.spotify_max_tracks,
                    self.spotify_default_tracks,
                    self.spotify_default_shuffle,
                )
            except (SpotifyError, ValueError) as error:
                await ctx.send(f"Invalid Spotify playlist options: {error}")
                return
            if spotify_resource.resource_type == "playlist" and not spotify_arguments:
                if not await self.get_spotify_credentials(ctx):
                    return
                await ctx.send(
                    "Configure this Spotify playlist import. Choose **ordered** to keep Spotify's order "
                    "or **shuffle** to randomize the selected tracks.",
                    view=SpotifyPlaylistLauncher(self, ctx, spotify_resource),
                )
                return
            if spotify_resource.resource_type == "track":
                options = {"count": 1, "start": 1, "end": None, "shuffle": False}
            await self.import_spotify(ctx, spotify_resource, options)
            return

        if self.is_youtube_playlist_url(spotify_value):
            try:
                options = parse_playlist_options(
                    spotify_arguments,
                    self.spotify_max_tracks,
                    self.spotify_default_tracks,
                    self.spotify_default_shuffle,
                )
            except (SpotifyError, ValueError) as error:
                await ctx.send(f"Invalid YouTube playlist options: {error}")
                return
            if not spotify_arguments:
                await ctx.send(
                    "Configure this YouTube playlist import. Choose **ordered** to keep YouTube's order "
                    "or **shuffle** to randomize the selected videos.",
                    view=YouTubePlaylistLauncher(self, ctx, spotify_value),
                )
                return
            await self.import_youtube_playlist(ctx, spotify_value, options)
            return

        session = await self.get_session(ctx)
        if session is None:
            return
        
        async with ctx.typing():  # Shows "Bot is typing..." while processing
            with youtube_dl.YoutubeDL({'format': 'bestaudio', 'noplaylist': True}) as ydl:
                try:
                    requests.get(query)
                except:
                    info = ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]
                else:
                    info = ydl.extract_info(query, download=False)

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
                await ctx.message.add_reaction("✅")
            else:
                embed.description = (
                    f"*▶️ Now playing in <#{session.channel}>*"
                )
                embed.add_field(name="Duration", value=duration_str, inline=True)
                embed.add_field(name="Added By", value=f"<@{ctx.author.id}>", inline=True)
                session.now_playing_message = await ctx.send(
                    embed=embed,
                    view=MusicControls(self, ctx.guild.id, session.q.current_music.ytube),
                )
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
                await ctx.message.add_reaction("▶️")
    @commands.command(name='skip', aliases=['next'])
    async def skip(self, ctx):
        """
        Skips the current song and plays the next one in the queue if available. The skipped song is not removed from the queue.
        """
        if not await self.ensure_user_in_voice(ctx):
            return
        if not await self.ensure_bot_in_voice(ctx):
            return

        session = await self.get_session(ctx)
        if session is None:
            return

        if not session.q.theres_next():
            await ctx.send("*There are no more songs in the queue.*")
            await ctx.message.add_reaction("🤷‍♂️")
            return
        
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if voice.is_playing():
            session.q.skip_requested = True
            voice.stop()
            await ctx.message.add_reaction("⏭️")

    @commands.command(name='seek')
    async def seek(self, ctx, *, position):
        """Seek within the current song."""
        await self.seek_current_track(ctx, position)

    @commands.command(name='restart')
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

    @commands.command(name='shuffle')
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
            await ctx.message.add_reaction("🤷‍♂️")
            return

        session.q.shuffle_upcoming()
        await ctx.send("Upcoming songs shuffled.")

    @commands.command(name='leave')
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
                
            await ctx.message.add_reaction("👋")
        else:
            await ctx.send("*The bot is not connected to a voice channel.*")
            await ctx.message.add_reaction("🙅‍♂️")

    @commands.command(name='pause')
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
            await ctx.message.add_reaction("⏸️")
        else:
            await ctx.send("*There is no audio currently playing.*")
            await ctx.message.add_reaction("🤔")

    @commands.command(name='resume')
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
            await ctx.message.add_reaction("▶️")
        else:
            await ctx.send("*The music is not paused.* 🔊🆙")
            await ctx.message.add_reaction("❓")

    @commands.command(name='stop', aliases=['reset'])
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
            await ctx.message.add_reaction("⏹️")
        else:
            await ctx.send("*There is no music playing.*")
            await ctx.message.add_reaction("🦗")

    @commands.command(name='queue', aliases=['q'])
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
            await ctx.message.add_reaction("✅")
            return

        # Get the dominant color of the first song's thumbnail
        first_song_thumb = session.q.queue[0].thumb if session.q.queue else None
        dominant_color = await get_dominant_color(first_song_thumb) if first_song_thumb else 0x3498db  # Default to blue

        # Generate queue list with the new format
        queue_list = [
            f"**{i + 1}.** {escape_markdown(truncate_text(song.title))}\n"
            f"{await convert_duration_pretty(song.duration)} | [Link]({song.ytube}) | <@{song.user}>"
            for i, song in enumerate(session.q.queue)
        ]

        # Split the queue into chunks of 10 songs per page
        chunk_size = 10
        chunks = [queue_list[i:i + chunk_size] for i in range(0, len(queue_list), chunk_size)]

        embeds = []

        for chunk in chunks:
            embed = discord.Embed(title="🎧 Current Queue", color=discord.Color(dominant_color))

            # Join the chunk into a single string for the embed
            embed.description = "\n\n".join(chunk)  # Two newlines for better separation
            embed.description += f"\n\nChannel: <#{session.channel}>" # Append channel to each page

            # Set the thumbnail of the first song in the queue
            if first_song_thumb:
                embed.set_thumbnail(url=first_song_thumb)

            embeds.append(embed)

        await Paginator.CustomPaginator(timeout=120).start(ctx, pages=embeds)

        await ctx.message.add_reaction("📜")

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
            await ctx.message.add_reaction("🤷‍♂️")
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
            await ctx.message.add_reaction("🤷‍♂️")
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

    @commands.command(name='clearqueue', aliases=['clearnext', 'clearNext', 'cn', 'clear_queue', 'cq', 'clear_next', 'clearQueue'])
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
            await ctx.message.add_reaction("✅")
            return
        
        if session.q.size() == 1:
            await ctx.send("*No other songs in queue. Use 'stop' to clear the currently playing song.*")
            await ctx.message.add_reaction("✅")
            return

        await self.retire_queued_track_controls_except(session, session.q.current_music)
        session.q.clear_queue_except_current()
        await ctx.send("*The queue has been cleared.*")
        await ctx.message.add_reaction("🧹")

    @commands.command(name='here', aliases=['join'])
    async def here(self, ctx):
        """
        Moves the bot to the user's current voice channel and updates the session.
        """
        if not ctx.author.voice:
            await ctx.send("*You are not connected to a voice channel.*")
            await ctx.message.add_reaction("❌")
            return

        voice_channel = ctx.author.voice.channel

        session = await self.get_session_in_guild(ctx)

        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)

        # If the bot is already connected, move it
        if voice and voice.is_connected():
            if voice.channel.id == voice_channel.id:
                await ctx.send(f"*I'm already in <#{voice_channel.id}>* 📢")
                await ctx.message.add_reaction("🤔")
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
        await ctx.message.add_reaction("🔄")

    @commands.command(name='playingnow', aliases=['nowPlaying', 'music', 'now', 'musicnow', 'musicNow', 'playing', 'nowplaying', 'playingNow'])
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
            await ctx.message.add_reaction("🚫")
            return
        
        current_music = session.q.get_current_music()

        if not current_music or current_music.title == '':
            await ctx.send("*Nothing is playing.*")
            await ctx.message.add_reaction("🚫")
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

        now_playing_message = await ctx.send(
            embed=embed,
            view=MusicControls(self, ctx.guild.id, current_music.ytube),
        )
        session.now_playing_messages[id(now_playing_message)] = (
            now_playing_message,
            current_music.ytube,
        )
        await ctx.message.add_reaction("🎶")

    @commands.command(name="search")
    async def search(self, ctx, *, query: str):
        """
        Searches YouTube for the top 20 results and allows the user to select one to add to the queue.

        :param ctx: discord.ext.commands.Context - The context in which the command was called.
        :param query: str - The search query to find relevant YouTube results.
        """
        embeds = []
        results = []
        async with ctx.typing():  # Shows "Bot is typing..." while processing
            
            ydl_opts = {
                'quiet': True,
                'extract_flat': True,
                'default_search': 'ytsearch20',
                'skip_download': True,
            }

            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch20:{query}", download=False)

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

        await ctx.message.add_reaction("🔍")

    @play.error
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("*❌ Please provide a search query or YouTube URL when using the `play` command. Usage: `.play <query>`*")
            await ctx.message.add_reaction("❌")
            return

    @search.error
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("*❌ Please provide a search query or YouTube URL when using the `search` command. Usage:`.search <query>`*")
            await ctx.message.add_reaction("❌")
            return

def setup(bot):
    bot.add_cog(Music(bot))


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
        label="Position range (optional)", placeholder="20-40", required=False, max_length=15
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
        self.count.default = str(music_cog.spotify_default_tracks)
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


class YouTubePlaylistModal(discord.ui.Modal, title="YouTube playlist import"):
    count = discord.ui.TextInput(label="Video count", placeholder="1-20", default="20", max_length=2)
    range_value = discord.ui.TextInput(
        label="Position range (optional)", placeholder="20-40", required=False, max_length=15
    )
    ordering = discord.ui.TextInput(
        label="Ordering: ordered or shuffle",
        placeholder="ordered = YouTube order; shuffle = random",
        default="ordered",
        max_length=7,
    )

    def __init__(self, music_cog, ctx, playlist_url):
        super().__init__()
        self.music_cog = music_cog
        self.ctx = ctx
        self.playlist_url = playlist_url
        self.count.default = str(music_cog.spotify_default_tracks)
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
        await self.music_cog.import_youtube_playlist(self.ctx, self.playlist_url, options)
        await interaction.followup.send("Playlist import started in the channel.", ephemeral=True)


class MusicControls(discord.ui.View):
    def __init__(self, music_cog, guild_id, track_url):
        super().__init__(timeout=600)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.track_url = track_url
        session = self.get_session()
        if session and session.q.current_music.ytube == track_url and session.q.loop_current:
            self.loop.style = discord.ButtonStyle.success

    def get_session(self):
        return next((session for session in sessions if session.guild == self.guild_id), None)

    async def interaction_check(self, interaction):
        voice = interaction.guild.voice_client if interaction.guild else None
        user_voice = getattr(interaction.user, "voice", None)
        if not voice or not voice.is_connected() or not user_voice or user_voice.channel != voice.channel:
            await interaction.response.send_message(
                "Join my voice channel before using music controls.", ephemeral=True
            )
            return False
        return True

    async def rate_current_track(self, interaction, rating):
        session = self.get_session()
        store = self.music_cog.song_stats_store
        if not store:
            await interaction.response.send_message("Song ratings are currently unavailable.", ephemeral=True)
            return
        if not session or session.q.current_music.ytube != self.track_url:
            await interaction.response.send_message("This now-playing control is no longer current.", ephemeral=True)
            return
        try:
            resulting_rating = await asyncio.to_thread(
                store.set_rating, self.guild_id, self.track_url, interaction.user.id, rating
            )
            likes, dislikes = await asyncio.to_thread(store.rating_summary, self.guild_id, self.track_url)
        except Exception as error:
            logger.warning("Guild %s: failed to save song rating: %s", self.guild_id, error)
            await interaction.response.send_message("Song rating could not be saved.", ephemeral=True)
            return
        action = "removed your rating" if resulting_rating == 0 else ("liked" if resulting_rating == 1 else "disliked")
        await interaction.response.send_message(
            f"You {action} this song. Likes: {likes} | Dislikes: {dislikes}", ephemeral=True
        )

    @discord.ui.button(emoji="👍", style=discord.ButtonStyle.secondary, row=1)
    async def like(self, interaction, button):
        await self.rate_current_track(interaction, 1)

    @discord.ui.button(emoji="👎", style=discord.ButtonStyle.secondary, row=1)
    async def dislike(self, interaction, button):
        await self.rate_current_track(interaction, -1)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction, button):
        session = self.get_session()
        voice = interaction.guild.voice_client
        if not session or not session.q.theres_next():
            await interaction.response.send_message("There are no more songs in the queue.", ephemeral=True)
            return
        session.q.skip_requested = True
        voice.stop()
        await interaction.response.send_message("Skipped.", ephemeral=True)

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def pause_resume(self, interaction, button):
        voice = interaction.guild.voice_client
        if voice.is_playing():
            voice.pause()
            session = self.get_session()
            if session:
                session.q.pause_playback()
            button.emoji = "▶️"
            await interaction.response.edit_message(view=self)
            return
        if voice.is_paused():
            voice.resume()
            session = self.get_session()
            if session:
                session.q.resume_playback()
            button.emoji = "⏸️"
            await interaction.response.edit_message(view=self)
            return
        await interaction.response.send_message("There is no audio currently playing.", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=0)
    async def loop(self, interaction, button):
        session = self.get_session()
        if not session:
            await interaction.response.send_message("The music session has ended.", ephemeral=True)
            return
        session.q.loop_current = not session.q.loop_current
        button.style = discord.ButtonStyle.success if session.q.loop_current else discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle(self, interaction, button):
        session = self.get_session()
        if not session or not session.q.theres_next():
            await interaction.response.send_message("There are no upcoming songs to shuffle.", ephemeral=True)
            return
        session.q.shuffle_upcoming()
        await interaction.response.send_message("Upcoming songs shuffled.")

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction, button):
        session = self.get_session()
        voice = interaction.guild.voice_client
        if session:
            session.q.clear_queue()
            await self.music_cog.retire_now_playing_controls(session)
            await self.music_cog.retire_queued_track_controls_except(session)
        voice.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)


class QueuedTrackControls(discord.ui.View):
    def __init__(self, music_cog, guild_id, track):
        super().__init__(timeout=600)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.track = track

    def get_session(self):
        return next((session for session in sessions if session.guild == self.guild_id), None)

    async def interaction_check(self, interaction):
        voice = interaction.guild.voice_client if interaction.guild else None
        user_voice = getattr(interaction.user, "voice", None)
        if not voice or not voice.is_connected() or not user_voice or user_voice.channel != voice.channel:
            await interaction.response.send_message(
                "Join my voice channel before using queue controls.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        session = self.get_session()
        if not session or not session.q.remove_queued_track(self.track):
            await interaction.response.send_message("That track is no longer in the queue.", ephemeral=True)
            return

        self.music_cog.forget_queued_track_controls(session, self.track)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="↕️", style=discord.ButtonStyle.secondary)
    async def move(self, interaction, button):
        session = self.get_session()
        if not session:
            await interaction.response.send_message("The music session has ended.", ephemeral=True)
            return

        current_index = session.q.queued_track_index(session.q.current_music)
        track_index = session.q.queued_track_index(self.track)
        if current_index is None or track_index is None or track_index <= current_index:
            await interaction.response.send_message("That track is no longer available to move.", ephemeral=True)
            return

        anchors = [
            item for item in session.q.queue[current_index:]
            if item is not self.track
        ][:25]
        await interaction.response.send_message(
            f"Choose the song after which to place **{truncate_text(self.track.title)}**.",
            ephemeral=True,
            view=QueuePositionSelector(
                self,
                interaction.user.id,
                anchors,
                session.q.current_music,
                interaction.guild,
            ),
        )


class QueuePositionSelector(discord.ui.View):
    def __init__(self, track_controls, owner_id, anchors, current_track, guild):
        super().__init__(timeout=120)
        self.track_controls = track_controls
        self.owner_id = owner_id
        self.anchors = anchors
        self.position_select = discord.ui.Select(
            placeholder="Place after...",
            options=[
                discord.SelectOption(
                    label=anchor.title[:100],
                    description=self.anchor_description(anchor, current_track, guild),
                    value=str(index),
                )
                for index, anchor in enumerate(anchors)
            ],
        )
        self.position_select.callback = self.select_position
        self.add_item(self.position_select)

    @staticmethod
    def anchor_description(anchor, current_track, guild):
        member = guild.get_member(anchor.user) if guild else None
        author = member.display_name if member else f"User {anchor.user}"
        now_playing = "Now playing | " if anchor is current_track else ""
        return f"{now_playing}Added by {author}"[:100]

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who opened this menu can use it.", ephemeral=True)
            return False
        return await self.track_controls.interaction_check(interaction)

    async def select_position(self, interaction):
        session = self.track_controls.get_session()
        anchor = self.anchors[int(self.position_select.values[0])]
        if not session or not session.q.move_queued_track_after(self.track_controls.track, anchor):
            await interaction.response.edit_message(
                content="That track is no longer available to move.", view=None
            )
            return

        await interaction.response.edit_message(
            content=(
                f"Moved **{truncate_text(self.track_controls.track.title)}** "
                f"to play after **{truncate_text(anchor.title)}**."
            ),
            view=None,
        )


class QueueRemoveLauncher(discord.ui.View):
    def __init__(self, music_cog, guild_id, response_channel, owner_id):
        super().__init__(timeout=120)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.response_channel = response_channel
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who ran the command can use this button.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Choose song", emoji="🎵", style=discord.ButtonStyle.secondary)
    async def open_selector(self, interaction, button):
        session = next((session for session in sessions if session.guild == self.guild_id), None)
        current_index = session.q.queued_track_index(session.q.current_music) if session else None
        tracks = session.q.queue[current_index + 1:] if current_index is not None else []
        if not tracks:
            await interaction.response.send_message("There are no upcoming songs to remove.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Choose a queued song to remove.",
            ephemeral=True,
            view=QueueRemoveSelector(
                self.music_cog,
                self.guild_id,
                self.response_channel,
                self.owner_id,
                tracks[:25],
            ),
        )


class QueueRemoveSelector(discord.ui.View):
    def __init__(self, music_cog, guild_id, response_channel, owner_id, tracks):
        super().__init__(timeout=120)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.response_channel = response_channel
        self.owner_id = owner_id
        self.tracks = tracks
        guild = music_cog.bot.get_guild(guild_id)
        self.track_select = discord.ui.Select(
            placeholder="Remove from queue...",
            options=[
                discord.SelectOption(
                    label=track.title[:100],
                    description=self.track_description(track, guild),
                    value=str(index),
                )
                for index, track in enumerate(tracks)
            ],
        )
        self.track_select.callback = self.select_track
        self.add_item(self.track_select)

    @staticmethod
    def track_description(track, guild):
        member = guild.get_member(track.user) if guild else None
        author = member.display_name if member else f"User {track.user}"
        return f"Added by {author}"[:100]

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who opened this menu can use it.", ephemeral=True)
            return False

        guild = self.music_cog.bot.get_guild(self.guild_id)
        voice = guild.voice_client if guild else None
        member = guild.get_member(interaction.user.id) if guild else None
        if not voice or not voice.is_connected() or not member or not member.voice or member.voice.channel != voice.channel:
            await interaction.response.send_message("Join my voice channel before removing a queued song.", ephemeral=True)
            return False
        return True

    async def select_track(self, interaction):
        session = next((session for session in sessions if session.guild == self.guild_id), None)
        track = self.tracks[int(self.track_select.values[0])]
        if not session or not session.q.remove_queued_track(track):
            await interaction.response.edit_message(content="That track is no longer in the queue.", view=None)
            return

        await self.music_cog.retire_queued_track_controls(session, track)
        await self.response_channel.send(f"*Removed from queue:* **{truncate_text(track.title)}**")
        await interaction.response.edit_message(content="Track removed from the queue.", view=None)


class QueueMoveLauncher(discord.ui.View):
    def __init__(self, music_cog, guild_id, owner_id):
        super().__init__(timeout=120)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who ran the command can use this button.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Choose song", emoji="↕️", style=discord.ButtonStyle.secondary)
    async def open_selector(self, interaction, button):
        session = next((session for session in sessions if session.guild == self.guild_id), None)
        current_index = session.q.queued_track_index(session.q.current_music) if session else None
        tracks = session.q.queue[current_index + 1:] if current_index is not None else []
        if not tracks:
            await interaction.response.send_message("There are no upcoming songs to move.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose an upcoming song to move.",
            ephemeral=True,
            view=QueueMoveSelector(self.music_cog, self.guild_id, self.owner_id, tracks[:25]),
        )


class QueueMoveSelector(discord.ui.View):
    def __init__(self, music_cog, guild_id, owner_id, tracks):
        super().__init__(timeout=120)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.tracks = tracks
        self.track_select = discord.ui.Select(
            placeholder="Move which song...",
            options=[
                discord.SelectOption(label=track.title[:100], value=str(index))
                for index, track in enumerate(tracks)
            ],
        )
        self.track_select.callback = self.select_track
        self.add_item(self.track_select)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the user who opened this menu can use it.", ephemeral=True)
            return False
        guild = self.music_cog.bot.get_guild(self.guild_id)
        voice = guild.voice_client if guild else None
        member = guild.get_member(interaction.user.id) if guild else None
        if not voice or not voice.is_connected() or not member or not member.voice or member.voice.channel != voice.channel:
            await interaction.response.send_message("Join my voice channel before moving a queued song.", ephemeral=True)
            return False
        return True

    async def select_track(self, interaction):
        session = next((session for session in sessions if session.guild == self.guild_id), None)
        track = self.tracks[int(self.track_select.values[0])]
        current_index = session.q.queued_track_index(session.q.current_music) if session else None
        track_index = session.q.queued_track_index(track) if session else None
        if current_index is None or track_index is None or track_index <= current_index:
            await interaction.response.edit_message(content="That track is no longer available to move.", view=None)
            return
        anchors = [item for item in session.q.queue[current_index:] if item is not track][:25]
        await interaction.response.edit_message(
            content=f"Choose the song after which to place **{truncate_text(track.title)}**.",
            view=QueueMoveDestinationSelector(self, track, anchors),
        )


class QueueMoveDestinationSelector(discord.ui.View):
    def __init__(self, move_selector, track, anchors):
        super().__init__(timeout=120)
        self.move_selector = move_selector
        self.track = track
        self.anchors = anchors
        self.anchor_select = discord.ui.Select(
            placeholder="Place after...",
            options=[
                discord.SelectOption(label=anchor.title[:100], value=str(index))
                for index, anchor in enumerate(anchors)
            ],
        )
        self.anchor_select.callback = self.select_anchor
        self.add_item(self.anchor_select)

    async def interaction_check(self, interaction):
        return await self.move_selector.interaction_check(interaction)

    async def select_anchor(self, interaction):
        session = next((session for session in sessions if session.guild == self.move_selector.guild_id), None)
        anchor = self.anchors[int(self.anchor_select.values[0])]
        if not session or not session.q.move_queued_track_after(self.track, anchor):
            await interaction.response.edit_message(content="That track is no longer available to move.", view=None)
            return
        await interaction.response.edit_message(
            content=f"Moved **{truncate_text(self.track.title)}** to play after **{truncate_text(anchor.title)}**.",
            view=None,
        )


class YouTubeSearchDropdown(discord.ui.View):
    def __init__(self, ctx, bot, results):
        super().__init__(timeout=60)  # Timeout for interaction
        self.ctx = ctx
        self.bot = bot
        self.results = results

        # Create dropdown options from search results
        options = [
            discord.SelectOption(label=video["title"][:100], description=video["duration"], value=str(i))
            for i, video in enumerate(results)
        ]

        # Create the dropdown select menu
        self.select_menu = discord.ui.Select(
            placeholder="Select a video to play...",
            options=options
        )
        self.select_menu.callback = self.dropdown_callback
        self.add_item(self.select_menu)

    async def dropdown_callback(self, interaction: discord.Interaction):
        """Handles the selection of a video from the dropdown."""
        selected_index = int(self.select_menu.values[0])
        selected_video = self.results[selected_index]

        # Get the play function from the bot's loaded cogs
        music_cog = self.bot.get_cog("Music")
        if not music_cog:
            await interaction.response.send_message("*❌ Music system not available*")
            return

        # Simulate calling !play command
        await interaction.response.send_message(f"*🎶 Selected:* ***{escape_markdown(truncate_text(selected_video['title']))}***", ephemeral=True)
        
        ctx = await self.bot.get_context(interaction.message)
        ctx.author = interaction.user  # Override the author to reflect the user who selected the song
        await music_cog.play(ctx, query=selected_video['url'])

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

        pixels = list(image.getdata())  # Get pixel data
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