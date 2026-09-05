import sys
import os
import sqlite3

import logging
import logging.handlers
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Third-party dependencies
from dotenv import load_dotenv
import discord
from discord.ext import commands

# Local imports
from bot.cogs import Music, ServerAssistant
from bot import __version__
from bot.utils.guild_config_store import create_store_from_environment as create_guild_config_store
from bot.utils.song_stats_store import create_store_from_environment as create_song_stats_store
from bot.utils.spotify_store import SpotifyStoreError, create_store_from_environment
from bot.utils.user_playlist_store import create_store_from_environment as create_user_playlist_store

######################### SETUP #########################
load_dotenv()

COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", ".")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(8 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))


def environment_boolean(name, default):
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"true", "yes", "1", "on"}:
        return True
    if value in {"false", "no", "0", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def environment_nonnegative_integer(name, default, minimum=0):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a whole number") from error
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def environment_timezone(name, default):
    value = os.getenv(name, default).strip() or default
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"{name} must be a valid IANA time zone") from error

# Bot intents configuration
intents = discord.Intents(
    messages=True,
    guilds=True,
    members=True,
    message_content=True,
    presences=True,
    voice_states=True
)

def command_prefix_for_message(bot, message):
    if not message.guild or not getattr(bot, "guild_config_store", None):
        return COMMAND_PREFIX
    return bot.guild_config_store.get(message.guild.id, bot.guild_config_defaults)["command_prefix"]


class ConfigurableBot(commands.Bot):
    async def setup_hook(self):
        await self.add_cog(Music(self))
        await self.add_cog(ServerAssistant(self))

    async def process_commands(self, message):
        if message.author.bot:
            return
        if not self.guild_config_defaults["prefix_commands_enabled"]:
            return
        if message.guild and self.guild_config_store:
            config = self.guild_config_store.get(message.guild.id, self.guild_config_defaults)
            if not config["prefix_commands_enabled"]:
                return
        await super().process_commands(message)


# Initialize bot with a command prefix
activity = discord.Activity(type=discord.ActivityType.listening, name="/help")
client = ConfigurableBot(
    command_prefix=command_prefix_for_message,
    intents=intents,
    activity=activity,
    help_command=None,
    )

# Load bot token from environment variables
TOKEN = os.getenv("DISCORD_TOKEN")
client.time_zone = environment_timezone("TZ", "UTC")
client.guild_config_defaults = {
    "command_prefix": COMMAND_PREFIX,
    "slash_commands_enabled": environment_boolean("SLASH_COMMANDS_ENABLED", True),
    "prefix_commands_enabled": environment_boolean("PREFIX_COMMANDS_ENABLED", True),
    "empty_channel_enabled": os.getenv("AUTO_DISCONNECT_EMPTY_CHANNEL_ENABLED", "true").lower() == "true",
    "empty_channel_minutes": max(0, int(os.getenv("AUTO_DISCONNECT_EMPTY_CHANNEL_MINUTES", "0"))),
    "inactivity_enabled": os.getenv("AUTO_DISCONNECT_INACTIVITY_ENABLED", "true").lower() == "true",
    "inactivity_minutes": max(0, int(os.getenv("AUTO_DISCONNECT_INACTIVITY_MINUTES", "10"))),
    "playlist_max_tracks": max(1, int(os.getenv("PLAYLIST_MAX_TRACKS", "20"))),
    "playlist_default_tracks": max(1, int(os.getenv("PLAYLIST_DEFAULT_TRACKS", "20"))),
    "playlist_default_shuffle": os.getenv("PLAYLIST_DEFAULT_SHUFFLE", "false").lower() == "true",
    "rating_history_enabled": environment_boolean("RATING_HISTORY_ENABLED", True),
    "lastfm_enabled": environment_boolean("LASTFM_ENABLED", False),
}
client.ytdlp_timeout_seconds = environment_nonnegative_integer("YTDLP_TIMEOUT_SECONDS", 45, minimum=1)
client.play_cooldown_seconds = environment_nonnegative_integer("PLAY_COOLDOWN_SECONDS", 0)
client.search_cooldown_seconds = environment_nonnegative_integer("SEARCH_COOLDOWN_SECONDS", 1)
client.lastfm_radio_cooldown_seconds = environment_nonnegative_integer("LASTFM_RADIO_COOLDOWN_SECONDS", 10)
client.lastfm_timeout_seconds = environment_nonnegative_integer("LASTFM_TIMEOUT_SECONDS", 15, minimum=1)
client.playlist_import_concurrency_per_guild = environment_nonnegative_integer(
    "PLAYLIST_IMPORT_CONCURRENCY_PER_GUILD", 1, minimum=1
)
client.max_playlists_per_user = environment_nonnegative_integer("MAX_PLAYLISTS_PER_USER", 3, minimum=1)
client.max_songs_per_user = environment_nonnegative_integer("MAX_SONGS_PER_USER", 50, minimum=1)
client.now_playing_controls_minimum_timeout_seconds = environment_nonnegative_integer(
    "NOW_PLAYING_CONTROLS_MINIMUM_TIMEOUT_SECONDS", 600, minimum=1
)
client.now_playing_controls_timeout_buffer_seconds = environment_nonnegative_integer(
    "NOW_PLAYING_CONTROLS_TIMEOUT_BUFFER_SECONDS", 60
)
client.stream_url_cache_safety_margin_seconds = environment_nonnegative_integer(
    "STREAM_URL_CACHE_SAFETY_MARGIN_SECONDS", 300
)
client.stream_url_cache_max_entries = environment_nonnegative_integer("STREAM_URL_CACHE_MAX_ENTRIES", 200, minimum=1)
client.saved_playlist_resolution_concurrency = environment_nonnegative_integer(
    "SAVED_PLAYLIST_RESOLUTION_CONCURRENCY", 3, minimum=1
)
logging.getLogger("discord").info(
    "Safeguards configured: yt-dlp timeout=%ss, play cooldown=%ss, search cooldown=%ss, playlist imports/guild=%s, controls minimum=%ss, controls buffer=%ss, stream cache margin=%ss, stream cache entries=%s, saved playlist resolution concurrency=%s",
    client.ytdlp_timeout_seconds,
    client.play_cooldown_seconds,
    client.search_cooldown_seconds,
    client.playlist_import_concurrency_per_guild,
    client.now_playing_controls_minimum_timeout_seconds,
    client.now_playing_controls_timeout_buffer_seconds,
    client.stream_url_cache_safety_margin_seconds,
    client.stream_url_cache_max_entries,
    client.saved_playlist_resolution_concurrency,
)
if not (
    client.guild_config_defaults["slash_commands_enabled"]
    or client.guild_config_defaults["prefix_commands_enabled"]
):
    raise ValueError("At least one of SLASH_COMMANDS_ENABLED or PREFIX_COMMANDS_ENABLED must be true")
client.guild_config_defaults["playlist_default_tracks"] = min(
    client.guild_config_defaults["playlist_default_tracks"],
    client.guild_config_defaults["playlist_max_tracks"],
)
try:
    client.guild_config_store = create_guild_config_store()
except (OSError, sqlite3.Error) as error:
    client.guild_config_store = None
    logging.getLogger("discord").warning("Guild configuration is unavailable: %s", error)
try:
    client.song_stats_store = create_song_stats_store()
except (OSError, sqlite3.Error) as error:
    client.song_stats_store = None
    logging.getLogger("discord").warning("Song statistics are unavailable: %s", error)
try:
    client.user_playlist_store = create_user_playlist_store()
except (OSError, sqlite3.Error) as error:
    client.user_playlist_store = None
    logging.getLogger("discord").warning("User playlists are unavailable: %s", error)
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

async def sync_guild_commands(guild):
    client.tree.copy_global_to(guild=guild)
    client.tree.remove_command("help", guild=guild)
    try:
        commands_synced = await client.tree.sync(guild=guild)
        logger.info("Synced %s application commands to guild %s", len(commands_synced), guild.id)
    except discord.HTTPException as error:
        logger.warning("Could not sync application commands to guild %s: %s", guild.id, error)


async def sync_profile_help_command():
    global_commands = client.tree.get_commands()
    help_command = client.tree.get_command("help")
    client.tree.clear_commands(guild=None)
    if help_command:
        client.tree.add_command(help_command)
    try:
        commands_synced = await client.tree.sync()
        logger.info("Synced %s global profile application command(s)", len(commands_synced))
    except discord.HTTPException as error:
        logger.warning("Could not sync global profile application commands: %s", error)
    finally:
        client.tree.clear_commands(guild=None)
        for command in global_commands:
            client.tree.add_command(command)


async def application_commands_enabled(interaction):
    if not interaction.guild:
        return client.guild_config_defaults["slash_commands_enabled"]
    config = client.guild_config_store.get(
        interaction.guild.id,
        client.guild_config_defaults,
    ) if client.guild_config_store else client.guild_config_defaults
    if config["slash_commands_enabled"]:
        return True
    await interaction.response.send_message(
        "Slash commands are disabled for this server. Use the configured prefix commands instead.",
        ephemeral=True,
    )
    return False


client.tree.interaction_check = application_commands_enabled


async def send_error_response(target, message):
    try:
        if isinstance(target, discord.Interaction):
            if target.response.is_done():
                await target.followup.send(message, ephemeral=True)
            else:
                await target.response.send_message(message, ephemeral=True)
            return
        await target.send(message, ephemeral=bool(target.interaction))
    except discord.HTTPException as response_error:
        logger.warning("Could not send command error response: %s", response_error)


def command_error_message(error):
    if isinstance(error, commands.MissingRequiredArgument):
        return "A required command option is missing. Use `/help` or the configured prefix help for usage."
    if isinstance(error, discord.app_commands.TransformerError):
        return "A command option is missing or invalid. Check the command options and try again."
    if isinstance(error, (commands.MissingPermissions, discord.app_commands.MissingPermissions)):
        return "You do not have permission to use that command."
    if isinstance(error, (commands.CommandOnCooldown, discord.app_commands.CommandOnCooldown)):
        return f"That command is rate limited. Try again in {max(1, int(error.retry_after) + 1)} seconds."
    if isinstance(error, (commands.CheckFailure, discord.app_commands.CheckFailure)):
        return "You cannot use that command in the current server or channel."
    return "That command could not be completed due to an internal error. Please try again shortly."


@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    original_error = getattr(error, "original", error)
    if not isinstance(original_error, (commands.UserInputError, commands.CheckFailure)):
        logger.error(
            "Prefix command %s failed in guild %s",
            getattr(ctx.command, "qualified_name", "unknown"),
            getattr(ctx.guild, "id", "direct-message"),
            exc_info=original_error,
        )
    await send_error_response(ctx, command_error_message(original_error))


@client.tree.error
async def on_application_command_error(interaction, error):
    original_error = getattr(error, "original", error)
    if not isinstance(original_error, (discord.app_commands.AppCommandError, commands.CheckFailure)):
        logger.error(
            "Application command %s failed in guild %s",
            getattr(interaction.command, "qualified_name", "unknown"),
            getattr(interaction.guild, "id", "direct-message"),
            exc_info=original_error,
        )
    await send_error_response(interaction, command_error_message(original_error))


@client.event
async def on_ready():
    """
    To execute once the bot is online
    """
    if not getattr(client, "commands_synced", False):
        for guild in client.guilds:
            await sync_guild_commands(guild)
        await sync_profile_help_command()
        client.commands_synced = True
    logger.info('We have successfully logged in as {0.user} (Bot version: v{1})'.format(client, __version__))


@client.event
async def on_guild_join(guild):
    await sync_guild_commands(guild)


# Runs bot's loop.
client.run(TOKEN, log_handler=None)