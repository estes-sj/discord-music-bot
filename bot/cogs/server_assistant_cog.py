import logging
import logging.handlers
import subprocess
import time as t
from datetime import datetime, timedelta

from discord.ext import commands

from bot import __version__

logger = logging.getLogger("discord")

class ServerAssistant(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = t.time()  # Store the bot's start time
        self.version = __version__

    @commands.hybrid_command()
    async def time(self, ctx):
        """Show the current server time."""
        now = datetime.now(self.bot.time_zone)
        await ctx.send(f'The current time is {now}')
        return

    @commands.hybrid_command()
    async def up(self, ctx):
        """Show the container hostname, bot version, and uptime."""
        # Record the end time
        end_time = t.time()
        # Calculate the elapsed time
        elapsed_time_seconds = end_time - self.start_time
        # Format the elapsed time
        elapsed_time_formatted = str(timedelta(seconds=int(elapsed_time_seconds)))

        # Get the hostname from /etc/hostname
        try:
            with open("/etc/hostname", "r") as f:
                container_id = f.read().strip()
        except Exception:
            container_id = "Unknown"

        await ctx.send(f"Discord Music Bot [`{container_id}`] | Version [`v{self.version}`] | Uptime: [`{elapsed_time_formatted}`]")
        return

    @commands.hybrid_command()
    async def ping(self, ctx):
        """Check whether the bot is responsive."""
        logger.info("Pong")
        await ctx.send("Pong")
        return

def setup(bot):
    bot.add_cog(ServerAssistant(bot))

