import asyncio
import logging

import discord

logger = logging.getLogger("discord")


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
        return self.music_cog.get_session_for_guild(self.guild_id)

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
