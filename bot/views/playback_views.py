import asyncio
import logging

import discord

logger = logging.getLogger("discord")


class MusicControls(discord.ui.View):
    def __init__(self, music_cog, guild_id, track_url, duration=None, start_position=0):
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.track_url = track_url
        self.message = None
        self.minimum_timeout_seconds = music_cog.bot.now_playing_controls_minimum_timeout_seconds
        self.timeout_buffer_seconds = music_cog.bot.now_playing_controls_timeout_buffer_seconds
        super().__init__(timeout=self.timeout_for_track(duration, start_position))
        session = self.get_session()
        if session and session.q.current_music.ytube == track_url and session.q.loop_current:
            self.loop.style = discord.ButtonStyle.success

    def timeout_for_track(self, duration, start_position):
        try:
            remaining_seconds = max(0, float(duration) - float(start_position))
        except (TypeError, ValueError):
            return self.minimum_timeout_seconds
        return max(self.minimum_timeout_seconds, remaining_seconds + self.timeout_buffer_seconds)

    async def on_timeout(self):
        if not self.message:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except (discord.Forbidden, discord.NotFound):
            pass

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

    @staticmethod
    def rating_summary_line(members, action):
        displayed_members = members[:3]
        names = [
            getattr(member, "display_name", f"<@{member.id}>")
            for member in displayed_members
        ]
        if len(members) > len(displayed_members):
            names.append(f"{len(members) - len(displayed_members)}+ others")
        if len(names) == 1:
            voters = names[0]
        elif len(names) == 2:
            voters = " and ".join(names)
        else:
            voters = ", ".join(names[:-1]) + f", and {names[-1]}"
        emoji = "👍" if action == "liked" else "👎"
        return f"{emoji} {voters} {action} this song"

    def rating_summary_text(self, guild, like_user_ids, dislike_user_ids):
        if not self.music_cog.get_guild_config(self.guild_id)["rating_history_enabled"]:
            return ""

        def resolve_members(user_ids):
            return [
                guild.get_member(user_id) or discord.Object(id=user_id)
                for user_id in user_ids
            ]

        lines = []
        if like_user_ids:
            lines.append(self.rating_summary_line(resolve_members(like_user_ids), "liked"))
        if dislike_user_ids:
            lines.append(self.rating_summary_line(resolve_members(dislike_user_ids), "disliked"))
        return "\n".join(lines)

    async def add_historical_rating_summary(self, embed, guild):
        store = self.music_cog.song_stats_store
        if not store or not self.music_cog.get_guild_config(self.guild_id)["rating_history_enabled"]:
            return
        try:
            like_user_ids = await asyncio.to_thread(store.rating_users, self.guild_id, self.track_url, 1)
            dislike_user_ids = await asyncio.to_thread(store.rating_users, self.guild_id, self.track_url, -1)
        except Exception as error:
            logger.warning("Guild %s: failed to load historical song ratings: %s", self.guild_id, error)
            return
        summary = self.rating_summary_text(guild, like_user_ids, dislike_user_ids)
        logger.debug(
            "Guild %s: loaded historical ratings for %s (likes=%s, dislikes=%s)",
            self.guild_id,
            self.track_url,
            len(like_user_ids),
            len(dislike_user_ids),
        )
        if summary:
            embed.set_footer(text=summary)

    async def update_rating_summary(self, interaction, like_user_ids, dislike_user_ids):
        if not interaction.message or not interaction.message.embeds:
            return
        embed = interaction.message.embeds[0].copy()
        summary = self.rating_summary_text(interaction.guild, like_user_ids, dislike_user_ids)
        if summary:
            embed.set_footer(text=summary)
        else:
            embed.remove_footer()
        await interaction.message.edit(embed=embed, view=self)

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
            like_user_ids = await asyncio.to_thread(store.rating_users, self.guild_id, self.track_url, 1)
            dislike_user_ids = await asyncio.to_thread(store.rating_users, self.guild_id, self.track_url, -1)
        except Exception as error:
            logger.warning("Guild %s: failed to save song rating: %s", self.guild_id, error)
            await interaction.response.send_message("Song rating could not be saved.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.update_rating_summary(interaction, like_user_ids, dislike_user_ids)
        action = "removed your rating" if resulting_rating == 0 else ("liked" if resulting_rating == 1 else "disliked")
        await interaction.followup.send(
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
        if not session:
            await interaction.response.send_message("There are no more songs in the queue.", ephemeral=True)
            return
        if not session.q.theres_next():
            session.q.clear_queue()
            await self.music_cog.retire_now_playing_controls(session)
            await self.music_cog.retire_queued_track_controls_except(session)
            voice.stop()
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)
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
