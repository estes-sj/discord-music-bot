import sys
import os
import sqlite3

import logging
import logging.handlers

# Third-party dependencies
from dotenv import load_dotenv
import discord
from discord.ext import commands

# Local imports
from bot.cogs import Music, ServerAssistant
from bot import __version__
from bot.utils.song_stats_store import create_store_from_environment as create_song_stats_store
from bot.utils.spotify_store import SpotifyStoreError, create_store_from_environment

######################### SETUP #########################
load_dotenv()

COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", ".")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(8 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

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
activity = discord.Activity(type=discord.ActivityType.listening, name=f"{COMMAND_PREFIX}help")
# Parameters are written in the doc string already
help_command = commands.DefaultHelpCommand(show_parameter_descriptions=False)
client = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    intents=intents,
    activity=activity,
    help_command=help_command
    )

# Load bot token from environment variables
TOKEN = os.getenv("DISCORD_TOKEN")
try:
    client.song_stats_store = create_song_stats_store()
except (OSError, sqlite3.Error) as error:
    client.song_stats_store = None
    logging.getLogger("discord").warning("Song statistics are unavailable: %s", error)
try:
    client.spotify_store = create_store_from_environment()
except SpotifyStoreError as error:
    client.spotify_store = None
    logging.getLogger("discord").warning("Spotify support is unavailable: %s", error)
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
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
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
    if not client.get_cog("Music"):
        await client.add_cog(Music(client))
    if not client.get_cog("ServerAssistant"):
        await client.add_cog(ServerAssistant(client))
    if not getattr(client, "commands_synced", False):
        await client.tree.sync()
        client.commands_synced = True
    logger.info('We have successfully logged in as {0.user} (Bot version: v{1})'.format(client, __version__))

# Runs bot's loop.
client.run(TOKEN, log_handler=None)