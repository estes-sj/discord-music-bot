import sys
import os

import logging
import logging.handlers

# Third-party dependencies
from dotenv import load_dotenv
import discord
from discord.ext import commands

# Local imports
from bot.cogs import Music, ServerAssistant
from bot import __version__

######################### SETUP #########################
load_dotenv()

# Bot intents configuration
intents = discord.Intents(
    messages=True,
    guilds=True,
    members=True,
    message_content=True,
    presences=True,
    voice_states=True
)

# Initialize bot with a command prefix
# Change the prefix as desired
activity = discord.Activity(type=discord.ActivityType.listening, name=".help")
# Parameters are written in the doc string already
help_command = commands.DefaultHelpCommand(show_parameter_descriptions=False)
client = commands.Bot(
    command_prefix=".",
    intents=intents,
    activity=activity,
    help_command=help_command
    )

# Load bot token from environment variables
TOKEN = os.getenv("DISCORD_TOKEN")
#########################################################

######################## LOGGER #########################
# Logger setup for debugging and tracking bot activity
logger = logging.getLogger("discord")
logger.setLevel(logging.INFO)  # Change to DEUBUG, INFO, WARNING, ERROR as needed
logging.getLogger("discord.http").setLevel(logging.INFO)

# Log file path (defaults to local directory if not set)
log_file_path = "./logs/discord.log"
os.makedirs(os.path.dirname(log_file_path), exist_ok=True)  # Ensure directory exists

# Configure rotating file handler
handler = logging.handlers.RotatingFileHandler(
    filename=log_file_path,
    encoding="utf-8",
    maxBytes=8 * 1024 * 1024,  # 8 MB
    backupCount=5,  # Keep 5 backups
)

# Log format
formatter = logging.Formatter(
    "[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{"
)
handler.setFormatter(formatter)
logger.addHandler(handler)
#########################################################

@client.event
async def on_ready():
    """
    To execute once the bot is online
    """
    await client.add_cog(Music(client))
    await client.add_cog(ServerAssistant(client))
    logger.info('We have successfully logged in as {0.user} (Bot version: v{1})'.format(client, __version__))

# Runs bot's loop.
client.run(TOKEN, log_handler=None)