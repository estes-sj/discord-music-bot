import logging
import logging.handlers
import subprocess
import time as t
from datetime import datetime, timedelta

from discord.ext import commands

logger = logging.getLogger("discord")

# Record the start time of this instance
start_time = t.time()

class ServerAssistant(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def time(self, ctx):
        """
        Current time

        Usage: ?time
        """
        now = datetime.now()
        await ctx.channel.send(f'The current time is {now}')
        return

    @commands.command()
    async def up(self, ctx):
        """
        Report container ID and uptime

        Usage: ?up
        """
        # Record the end time
        end_time = t.time()
        # Calculate the elapsed time
        elapsed_time_seconds = end_time - start_time
        # Format the elapsed time
        elapsed_time_formatted = str(timedelta(seconds=int(elapsed_time_seconds)))

        container_id = "Debug"
        try:
            container_name = "discord-music-bot"
            # Run the Docker command to get container ID
            command = f'docker ps -q -f name={container_name}'
            container_id = subprocess.check_output(command, shell=True, text=True).strip()
            # Keep only the first 12 characters of the container ID
            container_id = container_id[:12]
        except subprocess.CalledProcessError:
            return None

        await ctx.channel.send(f"Discord Music Bot ({container_id}) Uptime: {elapsed_time_formatted}")
        return

    @commands.command()
    async def ping(self, ctx):
        """
        Test command to check for basic bot responsiveness.

        Usage: ?ping
        """
        logger.info(f"Pong")
        await ctx.channel.send(f'Pong')
        return

def setup(bot):
    bot.add_cog(ServerAssistant(bot))

