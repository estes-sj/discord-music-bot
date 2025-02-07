FROM python:3.10-alpine

# Install required packages: Docker CLI, Bash, FFmpeg, and dependencies for yt-dlp
RUN apk add --no-cache \
    docker \
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

RUN pip install -r requirements.txt

# Copy the entire project into the container
COPY . .

# Set the PYTHONPATH to include the bot directory
ENV PYTHONPATH=/app

# Set the default command to run the bot
CMD [ "python3", "bot/main.py" ]
