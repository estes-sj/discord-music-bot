import asyncio
import io
import logging
import logging.handlers

import requests

import discord
from discord.ext import commands
from discord import app_commands

import yt_dlp as youtube_dl
from PIL import Image

import bot.utils.custom_paginator as Paginator
import bot.utils.music_utilities as Utilities

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

    def prepare_continue_queue(self, ctx):
        """
        Calls the next song in the queue after the current one ends.

        :param ctx: discord.ext.commands.Context
        """
        fut = asyncio.run_coroutine_threadsafe(self.continue_queue(ctx), self.bot.loop)
        try:
            fut.result()
        except Exception as e:
            logger.error(e)

    async def continue_queue(self, ctx):
        """
        Plays the next song in the queue if available.

        :param ctx: discord.ext.commands.Context
        """
        session = await self.get_session_in_guild(ctx)
        if session is None:
            return

        if not session.q.theres_next():
            await ctx.send("*Queue has ended* ✅")
            await asyncio.sleep(0)
            if session in sessions:
                sessions.remove(session)
            return

        session.q.next()
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        source = await discord.FFmpegOpusAudio.from_probe(session.q.current_music.url, **FFMPEG_OPTIONS)

        if voice.is_playing():
            voice.stop()

        voice.play(source, after=lambda e: self.prepare_continue_queue(ctx))

        # Convert duration to HH:MM:SS format
        duration = session.q.current_music.duration
        duration_str = await convert_duration_pretty(duration)

        # Get dominant color from thumbnail
        dominant_color = await get_dominant_color(session.q.current_music.thumb)

        # Create an embed with the song details
        embed = discord.Embed(
            title=session.q.current_music.title,
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

        await ctx.send(embed=embed)

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

    @commands.command(name='play')
    async def play(self, ctx, *, query):
        """
        Searches for a song and plays the first result in the voice channel.

        :param ctx: discord.ext.commands.Context
        :param query: str Search query or YouTube URL
        """
        try:
            voice_channel = ctx.author.voice.channel
        except AttributeError:
            await ctx.send("*You are not connected to a voice channel.*")
            await ctx.message.add_reaction("❌")
            return
        
        session = await self.get_session(ctx)
        if session is None:
            return
        
        async with ctx.typing():  # Shows "Bot is typing..." while processing
            with youtube_dl.YoutubeDL({'format': 'bestaudio', 'noplaylist': 'True'}) as ydl:
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

            embed = discord.Embed(title=title, url=ytube_url, color=discord.Color(dominant_color))  
            embed.set_thumbnail(url=thumb)
            embed.set_author(name="Music Stream Link", url=url)

            if voice.is_playing():
                embed.description = (
                    f"*🎵 Added to queue in <#{session.channel}>*"
                )
                embed.add_field(name="Duration", value=duration_str, inline=True)
                embed.add_field(name="Added By", value=f"<@{ctx.author.id}>", inline=True)
                await ctx.send(embed=embed)
                await ctx.message.add_reaction("✅")
            else:
                embed.description = (
                    f"*▶️ Now playing in <#{session.channel}>*"
                )
                embed.add_field(name="Duration", value=duration_str, inline=True)
                embed.add_field(name="Added By", value=f"<@{ctx.author.id}>", inline=True)
                await ctx.send(embed=embed)
                session.q.set_last_as_current()
                source = await discord.FFmpegOpusAudio.from_probe(url, **FFMPEG_OPTIONS)
                voice.play(source, after=lambda e: self.prepare_continue_queue(ctx))
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
            voice.stop()
            await ctx.message.add_reaction("⏭️")

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
            f"**{i + 1}.** {song.title}\n"
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

    @commands.command(name='clearqueue', aliases=['clearnext', 'clearNext', 'cn', 'clear_queue', 'cq', 'clear_next', 'clearQueue'])
    async def clearqueue(self, ctx):
        """
        Clears the current queue of songs, except the currently playing song.
        """
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

        session.q.clear_queue_except_current()
        await ctx.send("*The queue has been cleared.*")
        await ctx.message.add_reaction("🧹")

    @commands.command(name='here')
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
            title=session.q.current_music.title,
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

        await ctx.send(embed=embed)
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
                f"**{i + 1}.** {video['title']}\n"
                f"{video['duration']} | [Link]({video['url']}) | {video['channel']}"
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
        await interaction.response.send_message(f"*🎶 Selected:* ***{selected_video['title']}***", ephemeral=True)
        
        ctx = await self.bot.get_context(interaction.message)
        ctx.author = interaction.user  # Override the author to reflect the user who selected the song
        await music_cog.play(ctx, arg=selected_video['url'])

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
    Convert duration (in seconds) to HH:MM:SS format
    """
    if not duration:
        return "Unknown"

    duration = int(duration)  # Ensure it's an integer
    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"