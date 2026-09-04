import discord


def truncate_text(text, max_length=100):
    return text[:max_length] + "..." if len(text) > max_length else text


class QueuedTrackControls(discord.ui.View):
    def __init__(self, music_cog, guild_id, track):
        super().__init__(timeout=600)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.track = track

    def get_session(self):
        return self.music_cog.get_session_for_guild(self.guild_id)

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
        anchors = [item for item in session.q.queue[current_index:] if item is not self.track][:25]
        await interaction.response.send_message(
            f"Choose the song after which to place **{truncate_text(self.track.title)}**.",
            ephemeral=True,
            view=QueuePositionSelector(self, interaction.user.id, anchors, session.q.current_music, interaction.guild),
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
            await interaction.response.edit_message(content="That track is no longer available to move.", view=None)
            return
        await interaction.response.edit_message(
            content=f"Moved **{truncate_text(self.track_controls.track.title)}** to play after **{truncate_text(anchor.title)}**.",
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
        session = self.music_cog.get_session_for_guild(self.guild_id)
        current_index = session.q.queued_track_index(session.q.current_music) if session else None
        tracks = session.q.queue[current_index + 1:] if current_index is not None else []
        if not tracks:
            await interaction.response.send_message("There are no upcoming songs to remove.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose a queued song to remove.",
            ephemeral=True,
            view=QueueRemoveSelector(self.music_cog, self.guild_id, self.response_channel, self.owner_id, tracks[:25]),
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
                discord.SelectOption(label=track.title[:100], description=self.track_description(track, guild), value=str(index))
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
        session = self.music_cog.get_session_for_guild(self.guild_id)
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
        session = self.music_cog.get_session_for_guild(self.guild_id)
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
            options=[discord.SelectOption(label=track.title[:100], value=str(index)) for index, track in enumerate(tracks)],
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
        session = self.music_cog.get_session_for_guild(self.guild_id)
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
            options=[discord.SelectOption(label=anchor.title[:100], value=str(index)) for index, anchor in enumerate(anchors)],
        )
        self.anchor_select.callback = self.select_anchor
        self.add_item(self.anchor_select)

    async def interaction_check(self, interaction):
        return await self.move_selector.interaction_check(interaction)

    async def select_anchor(self, interaction):
        session = self.move_selector.music_cog.get_session_for_guild(self.move_selector.guild_id)
        anchor = self.anchors[int(self.anchor_select.values[0])]
        if not session or not session.q.move_queued_track_after(self.track, anchor):
            await interaction.response.edit_message(content="That track is no longer available to move.", view=None)
            return
        await interaction.response.edit_message(
            content=f"Moved **{truncate_text(self.track.title)}** to play after **{truncate_text(anchor.title)}**.",
            view=None,
        )
