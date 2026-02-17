# Use lightweight Python
FROM python:3.11-slim

# Install system tools (ffmpeg is required for yt-dlp audio processing)
RUN apt-get update && apt-get install -y \
    ffmpeg \
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
RUN mkdir -p /app/downloads/temp /app/downloads/completed

# Command to run the bot
CMD ["python", "main.py"]