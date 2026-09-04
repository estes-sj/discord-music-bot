import discord


def escape_markdown(text):
    for character in "*_`~|":
        text = text.replace(character, f"\\{character}")
    return text


def truncate_text(text, max_length=100):
    return text[:max_length] + "..." if len(text) > max_length else text


class YouTubeSearchDropdown(discord.ui.View):
    def __init__(self, ctx, bot, results):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.bot = bot
        self.results = results
        self.select_menu = discord.ui.Select(
            placeholder="Select a video to play...",
            options=[
                discord.SelectOption(label=video["title"][:100], description=video["duration"], value=str(index))
                for index, video in enumerate(results)
            ],
        )
        self.select_menu.callback = self.dropdown_callback
        self.add_item(self.select_menu)

    async def dropdown_callback(self, interaction):
        selected_video = self.results[int(self.select_menu.values[0])]
        music_cog = self.bot.get_cog("Music")
        if not music_cog:
            await interaction.response.send_message("*❌ Music system not available*")
            return
        await interaction.response.send_message(
            f"*🎶 Selected:* ***{escape_markdown(truncate_text(selected_video['title']))}***", ephemeral=True
        )
        ctx = await self.bot.get_context(interaction.message)
        ctx.author = interaction.user
        await music_cog.play(ctx, query=selected_video["url"])
