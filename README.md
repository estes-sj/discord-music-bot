# Discord Music Bot

<p align="center">
    <img src="docs/queuemessageonly.png" alt="Music Bot Peek" width="50%"/>
</p>

A Discord bot that allows users to play music and playlists from YouTube in a voice channel with custom commands for search, play, pause, and more. Spotify track, album, and playlist links are resolved to matching YouTube audio.
Supports multiple tracks, queue management, and interactive selection.

The main tools used are `yt-dlp` for pulling YouTube data and [FFmpeg](https://www.ffmpeg.org/) for audio streaming.

I used cogs since I adapt this code onto other bots that I have. It makes it a bit more modular since I can simply add the `Music` cog to a pre-existing bot.

## Features
See [Usage](#usage) for more information and examples on specific commands and features. Some of the music bot's features include:
- Search and play YouTube music directly in voice channels
- Resolve Spotify track, album, and playlist metadata to YouTube equivalents
- Queue management with pagination
- Like or dislike currently playing songs, with persistent guild statistics
- Support for multiple guilds via Sessions
- Select music using Discord interactions
- Auto leaving when the channel is empty or no music is playing

## Usage

Both slash-commands (`/`) and prefix commands (`.`) are supported. Prefix commands are available for servers that have disabled slash commands, but at least one command mode must be enabled. The default command prefix is `.` and can be changed per server with `.config` or in the `.env`. The bot's help message lists all available commands.

<details>
  <summary><code>/search &lt;song name&gt;</code> - Searches YouTube results</summary>

  Searches YouTube for the top 20 results and allows the user to select which one to add to the queue.
  
  <div class="image-container" align="center">
      <img src="docs/search.png" alt="Search Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/play &lt;song name, YouTube URL, or YouTube playlist URL&gt;</code> - Plays a song or adds it to queue</summary>

  ```text
  .play Neverender by Tame Impala
  .play https://www.youtube.com/watch?v=E7FU_mqhFGk
  ```

  Searches for a song and plays the first result in the voice channel. 

  <div class="image-container" align="center">
      <img src="docs/play_now.png" alt="Play Now Example" width="40%"/>
      <img src="docs/play_queue.png" alt="Add to Queue Example" width="40%"/>
  </div>

  For playlists, you'll receive a prompt to select the number of tracks to queue and whether to shuffle them. Imports larger than 10 tracks queue the first 10 before the remaining tracks are added in the background; one import-status message is updated with progress and completion. The bot queues matches it finds and reports how many selected tracks could not be resolved. Some of the default options can be configured in the `.env` file, including the maximum number of tracks accepted from a playlist or album import, the default number of tracks to queue, and whether to shuffle them by default.

  ```text
  .play https://www.youtube.com/playlist?list=PLAYLIST_ID
  ```

  <div class="image-container" align="center">
      <img src="docs/playlist_import_form.png" alt="Playlist Import Example" width="40%"/>
  </div>

  Alternatively, arguments can be used in the command. Use `--count`, `--range POSITION[,POSITION|START-END|START-...]`, `--ordered`, or `--shuffle` to skip the prompt. Positions are 1-based and inclusive, so `1-3,5,7,9-10` selects positions 1, 2, 3, 5, 7, 9, and 10 in that order. `START-` selects every track from that position through the end of the playlist. Duplicate positions from overlapping ranges are included once.

  `--count` determines the final number of songs. If the requested ranges contain more songs than `--count`, the bot keeps the first selected songs in range order. If they contain fewer, it fills the remainder from the tracks immediately after the final requested range, stopping at the end of the playlist. For example, `--range 1-3,5,7,9-10 --count 10` selects positions 1, 2, 3, 5, 7, 9, 10, 11, 12, and 13. `--shuffle` randomizes the final selected songs after this expansion or trimming. If `--shuffle` is used without `--range`, the bot samples from every track in the playlist. If `--shuffle` is used with `--range`, the bot samples only from the selected tracks. For example, `--range 40- --count 40 --shuffle` randomly selects 40 tracks from position 40 through the end of the playlist.

  ```text
  .play https://www.youtube.com/playlist?list=PLAYLIST_ID --count 20 --ordered
  .play https://www.youtube.com/playlist?list=PLAYLIST_ID --range 20-40 --count 10 --shuffle
  .play https://www.youtube.com/playlist?list=PLAYLIST_ID --range 40- --count 40 --shuffle
  .play https://www.youtube.com/playlist?list=PLAYLIST_ID --range 1-3,5,7,9-10 --count 10 --ordered
  ```

</details>

<details>
  <summary><code>/play &lt;Spotify song URL or playlist URL&gt;</code> - Imports Spotify metadata as YouTube matches</summary>

  Spotify links do not stream Spotify audio directly. The bot reads Spotify metadata using credentials configured for this server, finds equivalent YouTube audio, and queues the successful matches.

  Before either setup flow, set `SPOTIFY_REDIRECT_URI` in the bot's `.env` file and register that exact address in the Spotify Developer Dashboard. Spotify permits HTTP only for an explicit loopback IP, so use `http://127.0.0.1:8888/spotify-callback`, not `localhost` or a LAN/server address.

  ### Server Setup

  Use this setup when the server should share one Spotify application connection. It requires **Manage Server**.

  1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/) and login using your Spotify account. After logging in, you may need to revisit the [dashboard link](https://developer.spotify.com/dashboard/). Hit the "Create App" button or select an existing app and add the configured `SPOTIFY_REDIRECT_URI` (`http://127.0.0.1:8888/spotify-callback`) as its Redirect URI. Select **Web API** under APIs used. The "App name" and "App description" can be anything.
  2. Run `/spotify server setup` on the Discord server with the bot.
  3. In the bot DM, send the app's Client ID on the first line and Client Secret on the second line (as a single message).
  4. Open the authorization link sent by the bot, approve access, and send the complete redirected callback URL to the bot DM (see below for details for getting the authorization link).

  The credentials and refresh token are encrypted and stored for this server. Use `/spotify server status` to inspect the connection or `/spotify server clear` to remove it.

  ### User Setup

  Use this setup for a private Spotify connection that only you can use, including when a server has no usable Spotify connection or its playlist access is insufficient.

  1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/) and login using your Spotify account. After logging in, you may need to revisit the [dashboard link](https://developer.spotify.com/dashboard/). Hit the "Create App" button or select an existing app and add the configured `SPOTIFY_REDIRECT_URI` (`http://127.0.0.1:8888/spotify-callback`) as its Redirect URI. Select **Web API** under APIs used. The "App name" and "App description" can be anything.
  2. Run `/spotify user setup` on the Discord server with the bot.
  3. In the bot DM, send the app's Client ID on the first line and Client Secret on the second line (as a single message).
  4. Open the authorization link sent by the bot, approve access, and send the complete redirected callback URL to the bot DM (see below for details for getting the authorization link).

  Your credentials and refresh token are encrypted in `data/spotify.db`, belong only to your Discord account, and can be used by you in other servers. Use `/spotify user status` to inspect the connection or `/spotify user clear` to remove it.

  For either setup, the browser may show a connection error after redirecting; this is expected. Copy the complete `http://127.0.0.1:8888/spotify-callback?...` address from the browser bar into the bot DM. Do not post it in a server channel because it includes a short-lived authorization code.

  <div class="image-container" align="center">
      <img src="docs/spotify_developer_portal.png" alt="Spotify Developer Portal Example" width="70%"/>
  </div>

  If the browser never displays the callback URL, open its Developer Tools, select the **Network** tab, refresh the Spotify authorization page, click **Agree**, then copy the request URL whose path is `spotify-callback` into the bot DM.

  <div class="image-container" align="center">
      <img src="docs/spotify_setup.png" alt="Spotify Setup Example" width="70%"/>
  </div>
  <div class="image-container" align="center">
      <img src="docs/spotify_callback_url.png" alt="Spotify Callback URL Example" width="70%"/>
  </div>

  > [!IMPORTANT]
  > Track and album links use the configured server connection when available. Spotify currently returns playlist items only for playlists owned by the authorizing account or where that account is a collaborator. When server authorization cannot access a playlist, the requester can use their private connection instead.

  ```text
  # Various command examples:
  /play https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC
  /play https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX --count 8 --ordered
  /play https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M --count 10 --shuffle
  /play https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M --range 20-40 --count 15 --ordered
  /play https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M --range 1-3,5,7,9-10 --count 10 --ordered
  /spotify server setup
  /spotify server clear
  /spotify server status
  /spotify user setup
  /spotify user status
  /spotify user clear
  ```

  The bot queues matches it finds and reports how many selected tracks could not be resolved. Spotify credentials, access tokens, and secrets are never sent to a server channel or logged.

  <div class="image-container" align="center">
      <img src="docs/playlist_import_progress.png" alt="Playlist Import Progress Example" width="65%"/>
  </div>

</details>

<details>
  <summary><code>/playlist</code> - Creates, manages, and plays personal saved playlists</summary>

  Personal playlists belong to a Discord user rather than a server, so they are available anywhere that user can use the bot. Other members can view or queue a user's saved playlists, but only the owner can add, remove, move, or delete their songs. The default limits are three playlists and 50 saved songs across all of a user's playlists; bot operators can change them with `MAX_PLAYLISTS_PER_USER` and `MAX_SONGS_PER_USER`.

  `playlist add` accepts a YouTube search, a YouTube video or playlist URL, or a Spotify track, album, or playlist URL. When adding a YouTube or Spotify playlist URL, it always opens the same requester-only track-selection form used by `/play`: choose a count, positions/ranges, and ordered or shuffled results before tracks are saved. The form permits at most `min(PLAYLIST_MAX_TRACKS, MAX_SONGS_PER_USER)` tracks. It saves resolved YouTube matches, so playing a saved playlist queues those tracks without resolving them again. `/playlist play` also opens a requester-only configuration control, letting you choose which saved positions to queue and whether to preserve saved order or shuffle them. Slash-command add and remove confirmations are private to the requester and include a song embed.

  ```text
  /playlist                                      # List your playlists
  /playlist create Test Playlist
  /playlist add "Test Playlist" zenzenzen
  /playlist add "Test Playlist" https://www.youtube.com/playlist?list=PLAYLIST_ID
  /playlist add "Test Playlist" https://open.spotify.com/playlist/PLAYLIST_ID
  /playlist view                                 # List your playlists
  /playlist view Test Playlist                   # View songs in one of your playlists
  /playlist view @Member                         # List another member's playlists
  /playlist view @Member Test Playlist           # View another member's playlist
  /playlist remove "Test Playlist" 3
  /playlist move "Test Playlist" 3 1
  /playlist play "Test Playlist"
  /playlist play "Test Playlist" @Member
  /playlist delete "Test Playlist"
  ```

  Prefix commands use the same subcommands, for example `.playlist add "Test Playlist" zenzenzen`. Prefix responses are visible in the channel because Discord only supports private responses for slash commands.
</details>

<details>
  <summary><code>/pause</code> - Pauses the actively playing song</summary>

  <div class="image-container" align="center">
      <img src="docs/pause.png" alt="Pause Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/resume</code> - Resumes the actively paused song</summary>

  <div class="image-container" align="center">
      <img src="docs/resume.png" alt="Resume Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/skip</code> - Skips to the next song in the queue</summary>

  - Alias: `.next`

  Skips the current song and plays the next one in the queue if available. The skipped song is not removed from the queue.

  <div class="image-container" align="center">
      <img src="docs/skip.png" alt="Skip Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/seek &lt;position&gt;</code> - Jumps within the current song</summary>

  Use seconds, `MM:SS`, or `HH:MM:SS` for an absolute position. Prefix the value with `+` or `-` to move relative to the current position.

  ```text
  /seek +15       # Forward 15 seconds
  /seek +00:15    # Forward 15 seconds
  /seek 00:42     # Jump to 42 seconds from the beginning
  /seek -15       # Back 15 seconds
  /seek -00:15    # Back 15 seconds
  ```
</details>

<details>
  <summary><code>/restart</code> - Restarts the current song</summary>

  Starts the current song again from the beginning without changing the queue.
</details>

<details>
  <summary><code>/shuffle</code> - Shuffles upcoming songs</summary>

  Keeps the current song in place and randomizes the remaining queued songs.
</details>

<details>
  <summary><code>/stop</code> - Stops playing audio and clears the queue</summary>

  - Alias: `.reset`

  <div class="image-container" align="center">
      <img src="docs/stop.png" alt="Stop Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/here</code> - Moves the bot into the user's voice channel</summary>

  - Alias: `.join`

  Moves the bot to the user's current voice channel and updates the session.

  <div class="image-container" align="center">
      <img src="docs/here.png" alt="Here Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/leave</code> - Disconnects the bot from the voice channel and clears the queue</summary>

  <div class="image-container" align="center">
      <img src="docs/leave.png" alt="Leave Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/clearqueue</code> - Clears the queue, except the currently playing song</summary>

  - Alias: `.clearQueue`, `.cq`, `.clear_next`, `.clearnext`, `.clearNext`, `.cn`

  <div class="image-container" align="center">
      <img src="docs/clearqueue.png" alt="Clear Queue Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/playingnow</code> - Shows the current song</summary>
    
  - Alias: `.playingNow`, `.playing`, `.music`, `.nowplaying`, `.nowPlaying`, `.now`, `.musicnow`, `.musicNow`
    
  <div class="image-container" align="center">
      <img src="docs/now.png" alt="Playing Now Example" width="50%"/>
  </div>
</details>

<details>
  <summary>Song ratings and guild statistics</summary>

  The now-playing message includes Like and Dislike buttons. Each member has one rating per song in each server: choosing the other action replaces the current rating, and choosing the same action again removes it. Ratings are only available while the button's song is still playing.

  Song play counts are recorded when playback begins, including loop replays. All statistics are scoped to the current server and persist in `data/song_stats.db`.

  ```text
  /mostplayed       # Top 20 songs by playback starts
  /mostliked        # Top 20 songs by likes
  /mostdisliked     # Top 20 songs by dislikes
  /myliked          # Your 20 most recently liked songs in this server
  ```
</details>

<details>
  <summary><code>/queue</code> - Displays the current queue of songs</summary>

  - Alias: `.q`

  Displays the current queue of songs in groups of 10.

  <div class="image-container" align="center">
      <img src="docs/queue.png" alt="Queue Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/remove</code> - Removes a selected upcoming song from the queue</summary>

  - Prefix command aliases: `.remove`, `.rm`

  `.remove` opens an ephemeral dropdown of upcoming songs. The prefix commands open a small in-channel button first, because Discord only supports ephemeral messages as interaction responses. After a selection, the bot posts the removed song in the server channel.
</details>

<details>
  <summary><code>/move</code> - Moves a selected upcoming song in the queue</summary>

  - Prefix command: `.move`

  Select an upcoming song, then select the song after which it should play. `.move` opens the private selector immediately; `.move` first posts a requester-only launcher button.
</details>


**Other Utility Commands**
| Command                            | Description                                                                                   |
|------------------------------------|-----------------------------------------------------------------------------------------------|
| `/help`                             | Shows the help message with all available commands.                                           |
| `/config`                           | Displays the server's current configuration and allows administrators to change it.           |
| `/ping`                             | Test command to check for basic bot responsiveness.                                           |
| `/time`                             | Displays the current time.                                                                    |
| `/up`                               | Reports container ID and uptime.                                                              |

### Interactions
This bot also includes Discord interactions for quick pause/play, skip, queue management, and like/dislike actions. The below image shows the interaction buttons that appear in the now-playing message. From left-to-right, the buttons are: ⏭️ Skip, ▶️ Play / ⏸️ Pause, 🔁 Loop, 🔀 Shuffle, ⏹️ Stop, 👍 Like, 👎 Dislike. The Like and Dislike buttons are only available while the song is actively playing. If the interactions expire when they should still be active (e.g. hitting it returns "the bot did not respond on time"), you can use `.now`/`.playingNow` to get refreshed interactions.
  <div class="image-container" align="center">
      <img src="docs/interaction_buttons_currently_playing.png" alt="Currently Playing Interactions" width="50%"/>
  </div>

Then for a queued song, the interaction buttons are 🗑️ Remove and ↕️ Move.
  <div class="image-container" align="center">
      <img src="docs/interaction_buttons_queued_song.png" alt="Queued Song Interactions" width="50%"/>
  </div>

## Installation Steps

### Creating a bot

1. **Create a Discord bot**:
   - Go to the [Discord Developer Portal](https://discord.com/developers/applications).
   - Click "New Application" and give your bot a name.
   - Under the "Bot" tab, click "Add Bot" (if needed) and confirm.
   - In the "TOKEN" section, click "Copy" to save your bot token, which will be used in your `.env` file.

2. **Installation Context**:
   - Under the installation tab, switch to "Guild Install" and change the "Install Link" to "None".
    <div class="image-container" align="center">
      <img src="docs/discord_installation_tab.png" alt="Discord Installation Tab" width="50%"/>
    </div>

3. **Set up permissions**:
   - In the "OAuth2" tab, select "bot" and "applications.commands" under "scopes."
   - Under "Bot Permissions," select the necessary permissions (such as "Send Messages," "Manage Messages," "Connect," "Speak," etc.). See the example below for recommended permissions.
    <div class="image-container" align="center">
      <img src="docs/bot_permissions_example.png" alt="Bot Permissions Example" width="50%"/>
    </div>

4. **Intents**:
   - In the "Bot" tab, enable the necessary intents (such as "Presence Intent", "Server Members Intent," "Message Content Intent," etc.) to allow the bot to function properly.
    <div class="image-container" align="center">
      <img src="docs/additional_discord_bot_intents_settings.png" alt="Bot Intents Example" width="50%"/>
    </div>

5. **Invite your bot to a server**:
   - In the "OAuth2" tab, use the generated URL to invite the bot to your Discord server.

### Running through Docker

Use one of the following options to run the bot with Docker Compose.

#### **Option 1: Pull the published image**

1. **Create a directory for the deployment**:
  ```bash
  mkdir discord-music-bot
  cd discord-music-bot
  ```

2. **Create a `docker-compose.yaml` file**:
  ```yaml
  services:
    discord-music-bot:
     image: estessj/discord-music-bot:latest
     container_name: discord-music-bot
     restart: unless-stopped
     volumes:
      - ./logs:/app/logs
      - ./data:/app/data
     env_file:
      - .env
  ```

3. **Create a `.env` file in the root of the created directory**:
  ```env
  DISCORD_TOKEN=your-bot-token

  # Optional: defaults to daily at 04:00 in the container's time zone.
  YTDLP_UPDATE_SCHEDULE="0 4 * * *"

  # Required only for Spotify link support. This must be a Fernet key.
  SPOTIFY_CREDENTIAL_ENCRYPTION_KEY=replace-with-a-fernet-key
  SPOTIFY_MARKET=US
  SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/spotify-callback
  ```

4. **Pull and start the bot**:
  ```bash
  docker compose pull
  docker compose up -d
  ```

#### **Option 2: Build from source**

1. **Clone the repository**:
   ```bash
   git clone https://github.com/estes-sj/discord-music-bot.git
   cd discord-music-bot
   ```

2. **Create the environment file in the root of the cloned repository**:
   ```env
   DISCORD_TOKEN=your-bot-token

   # Optional: defaults to daily at 04:00 in the container's time zone.
   YTDLP_UPDATE_SCHEDULE="0 4 * * *"

   # Required only for Spotify link support. This must be a Fernet key.
   SPOTIFY_CREDENTIAL_ENCRYPTION_KEY=replace-with-a-fernet-key
   SPOTIFY_MARKET=US
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/spotify-callback
   ```

3. **Build and start the bot**:
   ```bash
   docker compose up -d --build
   ```

Once the bot is running, it will appear online in your Discord server and be able to join voice channels and play music.

## Configuration

### Environment Variables

#### Required

| Variable | Default | Example | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | None | `DISCORD_TOKEN=your-bot-token` | Bot token from the Discord Developer Portal. |

#### Guild-Configurable Defaults

These values are the defaults for new guilds. A member with **Manage Server** can change them per guild with `/config`; once saved, that guild's stored configuration overrides these environment values.

| Variable | Default | Example | Description |
| --- | --- | --- | --- |
| `COMMAND_PREFIX` | `.` | `COMMAND_PREFIX=!` | Prefix used for bot commands and the displayed help activity. |
| `AUTO_DISCONNECT_EMPTY_CHANNEL_ENABLED` | `true` | `AUTO_DISCONNECT_EMPTY_CHANNEL_ENABLED=false` | Whether the bot leaves when it is the only member left in its voice channel. |
| `AUTO_DISCONNECT_EMPTY_CHANNEL_MINUTES` | `0` | `AUTO_DISCONNECT_EMPTY_CHANNEL_MINUTES=5` | Minutes to wait after the bot is alone before leaving. `0` leaves at the next check. |
| `AUTO_DISCONNECT_INACTIVITY_ENABLED` | `true` | `AUTO_DISCONNECT_INACTIVITY_ENABLED=false` | Whether the bot leaves after playback inactivity. |
| `AUTO_DISCONNECT_INACTIVITY_MINUTES` | `10` | `AUTO_DISCONNECT_INACTIVITY_MINUTES=30` | Minutes without playback before the bot leaves. |
| `PLAYLIST_MAX_TRACKS` | `20` | `PLAYLIST_MAX_TRACKS=50` | Maximum number of tracks accepted from an album or playlist import. |
| `PLAYLIST_DEFAULT_TRACKS` | `20` | `PLAYLIST_DEFAULT_TRACKS=10` | Track count used by the playlist configuration modal and imports without an explicit count. |
| `PLAYLIST_DEFAULT_SHUFFLE` | `false` | `PLAYLIST_DEFAULT_SHUFFLE=true` | Whether playlist imports shuffle eligible tracks by default. |

#### Bot-Wide Behavior and Safeguards

| Variable | Default | Example | Description |
| --- | --- | --- | --- |
| `SLASH_COMMANDS_ENABLED` | `true` | `SLASH_COMMANDS_ENABLED=false` | Makes slash commands available to guilds. A value of `false` cannot be overridden by guild configuration. |
| `PREFIX_COMMANDS_ENABLED` | `true` | `PREFIX_COMMANDS_ENABLED=false` | Makes prefix commands available to guilds. A value of `false` cannot be overridden by guild configuration. At least one mode must be enabled. |
| `YTDLP_TIMEOUT_SECONDS` | `45` | `YTDLP_TIMEOUT_SECONDS=60` | Maximum seconds to wait for one yt-dlp source-resolution operation before returning a safe failure. |
| `PLAY_COOLDOWN_SECONDS` | `0` | `PLAY_COOLDOWN_SECONDS=10` | Per-user, per-server cooldown for `/play` and the prefix equivalent. `0` disables this cooldown. |
| `SEARCH_COOLDOWN_SECONDS` | `1` | `SEARCH_COOLDOWN_SECONDS=5` | Per-user, per-server cooldown for `/search` and the prefix equivalent. `0` disables this cooldown. |
| `PLAYLIST_IMPORT_CONCURRENCY_PER_GUILD` | `1` | `PLAYLIST_IMPORT_CONCURRENCY_PER_GUILD=2` | Maximum simultaneous playlist imports within one server. Keep `1` unless host capacity testing supports more. |
| `NOW_PLAYING_CONTROLS_MINIMUM_TIMEOUT_SECONDS` | `600` | `NOW_PLAYING_CONTROLS_MINIMUM_TIMEOUT_SECONDS=900` | Minimum lifetime for currently-playing controls. Known-duration tracks use the greater of this value or their remaining duration plus `NOW_PLAYING_CONTROLS_TIMEOUT_BUFFER_SECONDS`. |
| `NOW_PLAYING_CONTROLS_TIMEOUT_BUFFER_SECONDS` | `60` | `NOW_PLAYING_CONTROLS_TIMEOUT_BUFFER_SECONDS=120` | Extra time added to a known track's remaining duration when setting its currently-playing control lifetime. |
| `STREAM_URL_CACHE_SAFETY_MARGIN_SECONDS` | `300` | `STREAM_URL_CACHE_SAFETY_MARGIN_SECONDS=600` | Refresh a cached signed YouTube stream URL when it has this many seconds or less remaining. The cache is memory-only and is cleared when the bot restarts. |
| `STREAM_URL_CACHE_MAX_ENTRIES` | `200` | `STREAM_URL_CACHE_MAX_ENTRIES=500` | Maximum signed YouTube stream URLs held in the bot's in-memory cache. |
| `SAVED_PLAYLIST_RESOLUTION_CONCURRENCY` | `3` | `SAVED_PLAYLIST_RESOLUTION_CONCURRENCY=5` | Maximum simultaneous YouTube resolutions when queueing a saved playlist. |
| `MAX_PLAYLISTS_PER_USER` | `3` | `MAX_PLAYLISTS_PER_USER=5` | Maximum personal playlists a Discord user can create. |
| `MAX_SONGS_PER_USER` | `50` | `MAX_SONGS_PER_USER=100` | Maximum tracks a Discord user can save across all of their personal playlists. |

#### Spotify Integration

| Variable | Default | Example | Description |
| --- | --- | --- | --- |
| `SPOTIFY_CREDENTIAL_ENCRYPTION_KEY` | None | `SPOTIFY_CREDENTIAL_ENCRYPTION_KEY=...` | Fernet key used to encrypt guild and personal Spotify credentials at rest. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Required for Spotify support. |
| `SPOTIFY_MARKET` | `US` | `SPOTIFY_MARKET=CA` | Two-letter country code used to determine Spotify catalog availability for the Client Credentials flow. Set this to the bot's intended region. |
| `SPOTIFY_REDIRECT_URI` | None | `SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/spotify-callback` | Exact callback URL registered in the Spotify Developer Dashboard for one-time playlist authorization. Spotify permits HTTP only for an explicit loopback IP, not `localhost` or a LAN/server IP. The authorizing user copies the redirected URL to the bot's DM. Required for playlists. |

#### Persistent Storage

| Variable | Default | Example | Description |
| --- | --- | --- | --- |
| `SONG_STATS_DATABASE_PATH` | `/app/data/song_stats.db` | `SONG_STATS_DATABASE_PATH=/app/data/song_stats.db` | SQLite database for persistent, guild-scoped song play counts and ratings. |
| `USER_PLAYLIST_DATABASE_PATH` | `/app/data/user_playlists.db` | `USER_PLAYLIST_DATABASE_PATH=/app/data/user_playlists.db` | SQLite database for persistent personal playlists, shared by the user's bot account across servers. |
| `GUILD_CONFIG_DATABASE_PATH` | `/app/data/guild_config.db` | `GUILD_CONFIG_DATABASE_PATH=/app/data/guild_config.db` | SQLite database for per-server music configuration overrides. |
| `SPOTIFY_DATABASE_PATH` | `/app/data/spotify.db` | `SPOTIFY_DATABASE_PATH=/app/data/spotify.db` | SQLite database containing encrypted guild and personal Spotify credential records. Mount `/app/data` to retain it across container replacement. |

#### System Logging and Maintenance

| Variable | Default | Example | Description |
| --- | --- | --- | --- |
| `TZ` | `UTC` | `TZ=America/New_York` | IANA time zone used by the server clock and update scheduler. Invalid values prevent startup. |
| `LOG_MAX_BYTES` | `8388608` | `LOG_MAX_BYTES=16777216` | Maximum size, in bytes, of each `logs/discord.log` file before rotation. |
| `LOG_BACKUP_COUNT` | `5` | `LOG_BACKUP_COUNT=10` | Number of rotated `discord.log` files to retain. |
| `YTDLP_UPDATE_SCHEDULE` | `0 4 * * *` | `YTDLP_UPDATE_SCHEDULE="0 8 * * *"` | Cron expression for yt-dlp updates. Output is written to `logs/yt-dlp-update.log`. |

Server administrators can run `.config` or `/config` to set a server-specific command prefix, command modes, auto-disconnect policies, and playlist defaults. Guilds can disable either prefix or slash commands, but must keep one mode enabled. `SLASH_COMMANDS_ENABLED=false` or `PREFIX_COMMANDS_ENABLED=false` is an operator-level restriction and prevents guild administrators from re-enabling that mode. These overrides are stored in `GUILD_CONFIG_DATABASE_PATH`; environment values remain the defaults for servers without an override.

## Troubleshooting

### Stuttering audio
If the container logs include something like `[youtube] player: Signature extraction failed: Some formats may be missing`, chances are yt-dlp has to be updated for those changed/new formats.
If it has been awhile since rebuilding containers, it is recommended to see if it picks up a newer version of yt-dlp and solves the issue. Ensure `YTDLP_UPDATE_SCHEDULE` is set in the `.env` file to automatically update yt-dlp on a schedule.

One way without rebuilding the container is to run the following command in the container to manually update:
```bash
# Update yt-dlp to the latest version
docker compose exec -T discord-music-bot python3 -m pip install --no-cache-dir --upgrade yt-dlp
# Check the version
docker compose exec -T discord-music-bot yt-dlp --version
```

## Audio Quality Limitations

The bot streams the best audio format that `yt-dlp` can obtain from the selected YouTube result, but it cannot improve the quality of that source. YouTube uploads may already be low bitrate, dynamically compressed, clipped, or distorted. This is most noticeable in music with sharp, loud transients or a wide dynamic range, such as orchestral cannon shots, percussion-heavy tracks, and bass-heavy music.

Discord voice channels also impose a bitrate limit and transmit audio with Opus. The available channel bitrate depends on the server's boost level, so listeners may hear additional compression artifacts or reduced detail compared with the original upload. Use the highest voice-channel bitrate your server allows and try a different YouTube recording or remaster when a specific track sounds distorted.

Seeking restarts the stream at the selected position; it may briefly buffer, but it does not increase or intentionally reduce the selected source quality.

## Bugs
Please use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml) for reproducible problems. Review [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Do not report vulnerabilities or expose secrets in a public issue; follow [SECURITY.md](SECURITY.md) instead.

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.