# Discord Music Bot

<p align="center">
    <img src="docs/queuemessageonly.png" alt="Music Bot Peek" width="50%"/>
</p>

A Discord bot that allows users to play music from YouTube in a voice channel with custom commands for search, play, pause, and more.
Supports multiple tracks, queue management, and interactive selection.

The main tools used are `youtube-dl` for pulling YouTube data and [FFmpeg](https://www.ffmpeg.org/) for audio streaming.

I used cogs since I adapt this code onto other bots that I have. It makes it a bit more modular since I can simply add the `Music` cog to a pre-existing bot.

## Features
See [Usage](#usage) for more information and examples on specific commands and features. Some of the music bot's features include:
- Search and play YouTube music directly in voice channels
- Queue management with pagination
- Support for multiple guilds via Sessions
- Select music using Discord interactions
- Auto leaving when the channel is empty or no music is playing

## Usage

<details>
  <summary><code>.search &lt;song name&gt;</code> - Searches Youtube results</summary>

  Searches YouTube for the top 20 results and allows the user to select which one to add to the queue.
  
  <div class="image-container" align="center">
      <img src="docs/search.png" alt="Search Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>.play &lt;song name or YouTube URL&gt;</code> - Plays a song or adds it to queue</summary>

  Searches for a song and plays the first result in the voice channel.

  <div class="image-container" align="center">
      <img src="docs/play_now.png" alt="Play Now Example" width="40%"/>
      <img src="docs/play_queue.png" alt="Add to Queue Example" width="40%"/>
  </div>
</details>

<details>
  <summary><code>.pause</code> - Pauses the actively playing song</summary>

  <div class="image-container" align="center">
      <img src="docs/pause.png" alt="Pause Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>.resume</code> - Resumes the actively paused song</summary>

  <div class="image-container" align="center">
      <img src="docs/resume.png" alt="Resume Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>.skip</code> - Skips to the next song in the queue</summary>

  - Alias: `.next`

  Skips the current song and plays the next one in the queue if available. The skipped song is not removed from the queue.

  <div class="image-container" align="center">
      <img src="docs/skip.png" alt="Skip Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>.stop</code> - Stops playing audio and clears the queue</summary>

  - Alias: `.reset`

  <div class="image-container" align="center">
      <img src="docs/stop.png" alt="Stop Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>.here</code> - Moves the bot into the user's voice channel</summary>

  - Alias: `.join`

  Moves the bot to the user's current voice channel and updates the session.

  <div class="image-container" align="center">
      <img src="docs/here.png" alt="Here Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>.leave</code> - Disconnects the bot from the voice channel and clears the queue</summary>

  <div class="image-container" align="center">
      <img src="docs/leave.png" alt="Leave Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>.clearqueue</code> - Clears the queue, except the currently playing song</summary>

  - Alias: `.clearQueue`, `.cq`, `.clear_next`, `.clearnext`, `.clearNext`, `.cn`

  <div class="image-container" align="center">
      <img src="docs/clearqueue.png" alt="Clear Queue Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>.playingnow</code> - Shows the current song</summary>
    
  - Alias: `.playingNow`, `.playing`, `.music`, `.nowplaying`, `.nowPlaying`, `.now`, `.musicnow`, `.musicNow`
    
  <div class="image-container" align="center">
      <img src="docs/now.png" alt="Playing Now Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>.queue</code> - Displays the current queue of songs</summary>

  - Alias: `.q`

  Displays the current queue of songs in groups of 10.

  <div class="image-container" align="center">
      <img src="docs/queue.png" alt="Queue Example" width="50%"/>
  </div>
</details>

<details>
  <summary><code>/remove</code> - Removes a selected upcoming song from the queue</summary>

  - Prefix command aliases: `.remove`, `.rm`

  `/remove` immediately opens an ephemeral dropdown of upcoming songs. The prefix commands open a small in-channel button first, because Discord only supports ephemeral messages as interaction responses. After a selection, the bot posts the removed song in the server channel.
</details>

**Other Utility Commands**
| Command                            | Description                                                                                   |
|------------------------------------|-----------------------------------------------------------------------------------------------|
| `.help`                             | Shows the help message with all available commands.                                           |
| `.ping`                             | Test command to check for basic bot responsiveness.                                           |
| `.time`                             | Displays the current time.                                                                    |
| `.up`                               | Reports container ID and uptime.                                                              |

## Installation Steps

### Creating a bot

1. **Create a Discord bot**:
   - Go to the [Discord Developer Portal](https://discord.com/developers/applications).
   - Click "New Application" and give your bot a name.
   - Under the "Bot" tab, click "Add Bot" and confirm.
   - In the "TOKEN" section, click "Copy" to save your bot token, which will be used in your `.env` file.

2. **Set up permissions**:
   - In the "OAuth2" tab, select "bot" under "scopes."
   - Under "Bot Permissions," select the necessary permissions (such as "Send Messages," "Manage Messages," "Connect," "Speak," etc.). See the example below for recommended permissions.
    <div class="image-container" align="center">
      <img src="docs/bot_permissions_example.png" alt="Bot Permissions Example" width="50%"/>
    </div>

3. **Invite your bot to a server**:
   - In the "OAuth2" tab, use the generated URL to invite the bot to your Discord server.

### Running through Docker

Use one of the following options to run the bot with Docker Compose.

#### Pull the published image

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
     env_file:
      - .env
  ```

3. **Create a `.env` file in the root of the created directory**:
  ```env
  DISCORD_TOKEN=your-bot-token

  # Optional: defaults to daily at 04:00 in the container's time zone.
  YTDLP_UPDATE_SCHEDULE="0 4 * * *"
  ```

4. **Pull and start the bot**:
  ```bash
  docker compose pull
  docker compose up -d
  ```

#### Build from source

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
   ```

3. **Build and start the bot**:
   ```bash
   docker compose up -d --build
   ```

Once the bot is running, it will appear online in your Discord server and be able to join voice channels and play music.

## Configuration

### Environment Variables
| Variable | Default | Example | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | None | `DISCORD_TOKEN=your-bot-token` | Bot token from the Discord Developer Portal. |
| `COMMAND_PREFIX` | `.` | `COMMAND_PREFIX=!` | Prefix used for bot commands and the displayed help activity. |
| `LOG_MAX_BYTES` | `8388608` | `LOG_MAX_BYTES=16777216` | Maximum size, in bytes, of each `logs/discord.log` file before rotation. |
| `LOG_BACKUP_COUNT` | `5` | `LOG_BACKUP_COUNT=10` | Number of rotated `discord.log` files to retain. |
| `YTDLP_UPDATE_SCHEDULE` | `0 4 * * *` | `YTDLP_UPDATE_SCHEDULE="0 8 * * *"` | Cron expression for yt-dlp updates. Output is written to `logs/yt-dlp-update.log`. |

## Troubleshooting

### Stuttering audio
If the container logs include something like `[youtube] player: Signature extraction failed: Some formats may be missing`, chances are yt-dlp has to be updated for those changed/new formats.
If it has been awhile since rebuilding containers, it is recommended to see if picks up a newer version of yt-dlp and solves the issue.

One way without rebuilding the container is to run the following command in the container:
```bash
# Update yt-dlp to the latest version
docker compose exec -T discord-music-bot python3 -m pip install --no-cache-dir --upgrade yt-dlp
# Check the version
docker compose exec -T discord-music-bot yt-dlp --version
```

## Future Work

### New Features
Feel free to suggest others.

- Adding a database to allow features:
  - "Liking" a song (requires storing user/guild data)
  - Most played/liked songs in the guild
  - Most played/liked songs for all bot users (across multiple guilds)
  - Playlists
- Restarting a song
- Removing an index from the queue
- Skipping to a particular index in the queue
- Shuffling a queue
- Public Discord app/bot auto-running `master` (via pipelines)
  - `ENV` variable that can be passed in for scheduled restarts/maintenance (seen in the footer of embeds)

### Bugs
Will address bugs as I identify them through my own personal use of the bot. Feel free to open an issue, create a PR, or reach out to me for other issues found.

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.