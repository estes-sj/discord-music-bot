FROM python:3.10-alpine

# Labels
LABEL org.opencontainers.image.title="discord-music-bot" \
      org.opencontainers.image.description="A Discord music bot that uses discord.py, youtube_dl, and FFmpeg for audio streaming" \
      org.opencontainers.image.url="https://hub.docker.com/repository/docker/estessj/discord-music-bot" \
      org.opencontainers.image.source="https://github.com/estes-sj/discord-music-bot" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.authors="Samuel Estes <samuel.estes2000@gmail.com>"

# Install required packages: Bash, FFmpeg, and dependencies for yt-dlp
RUN apk add --no-cache \
    bash \
    ffmpeg \
    musl-dev \
    libffi-dev \
    gcc \
    && pip install --upgrade pip  # Make sure pip is up to date

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container and install dependencies
COPY requirements.txt .

# Install base dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Always install the latest yt-dlp version
RUN pip install --no-cache-dir --upgrade yt-dlp

# Copy the entire project into the container
COPY . .

# Set the PYTHONPATH to include the bot directory
ENV PYTHONPATH=/app

# Set the default command to run the bot
CMD [ "python3", "bot/main.py" ]
