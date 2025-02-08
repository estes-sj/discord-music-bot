from Paginator import Simple
import discord

class CustomPaginator(Simple):
    """
    A subclass of the Paginator's Simple class that customizes the behavior 
    of the next and previous buttons in the pagination system.

    The main purpose is to override the original behavior where only the 
    executor could control the pagination.
    """

    async def next_button_callback(self, interaction: discord.Interaction):
        """
        Handles the 'Next' button click event in the paginator.

        Moves to the next page in the pagination and defers the interaction 
        response to avoid an unnecessary visible loading indicator.

        :param interaction: The Discord interaction triggered by the button click.
        """
        await self.next()
        await interaction.response.defer()

    async def previous_button_callback(self, interaction: discord.Interaction):
        """
        Handles the 'Previous' button click event in the paginator.

        Moves to the previous page in the pagination and defers the interaction 
        response to avoid an unnecessary visible loading indicator.

        :param interaction: The Discord interaction triggered by the button click.
        """
        await self.previous()
        await interaction.response.defer()