# Define module metadata
__title__ = "discord-music-bot"
__author__ = "estes-sj"
__license__ = "GNU General Public License v3"
__version__ = "0.0.1"

# Import the bot instance from main.py
from .main import bot

# Import cogs
from .cogs.music_cog import Music
from .cogs.server_assistant_cog import ServerAssistant

# Import utilities
from .utils.music_utilities import Queue, Session
from .utils.custom_paginator import CustomPaginator