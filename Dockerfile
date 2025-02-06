FROM python:3.10-alpine

# Install required packages: Docker CLI, Bash, FFmpeg, and dependencies for yt-dlp
RUN apk add --no-cache docker bash ffmpeg musl-dev libffi-dev gcc

COPY requirements.txt /app/

WORKDIR /app

RUN pip install -r requirements.txt

COPY . .

CMD [ "python3", "main.py" ]
