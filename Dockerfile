# Use lightweight Python
FROM python:3.11-slim

# Install system tools (ffmpeg for yt-dlp audio, aria2 for direct downloads)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    aria2 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create the internal download directories
# These will be mapped to your Host storage later
RUN mkdir -p /app/downloads/movies /app/downloads/shows

# Command to run the bot
CMD pip install --upgrade yt-dlp && python main.py