# 📥 Home-Lab-DL (The "OvenDrop" Downloader)

> **A Headless, Dockerized Telegram Bot for your Home Server.**
> Send links from anywhere; files appear on your server. Powered by `yt-dlp` and `aiohttp`.

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-2CA5E0?logo=telegram&logoColor=white)

## 🚀 Features

- **Universal Downloader:** Supports YouTube (4K+), TikTok, Facebook, Instagram, and direct HTTP file links.
- **Smart Quality:** Automatically merges Best Video + Best Audio for highest possible quality (1080p/4K).
- **Silent Progress Bars:** Live-updating download progress (edits a single message to avoid notification spam).
- **Smart Filenames:** Auto-detects real filenames from headers and sanitizes them.
- **Auto-Cleanup:** Automatically wipes temp files if a download fails.
- **Secure:** Restricts access to your specific Telegram User ID.

## 🛠️ Deployment (The Easy Way)

### Option 1: Coolify (Recommended)

1.  Create a **Private Repository (with GitHub App)** resource.
2.  Select this repository.
3.  **Build Pack:** `Dockerfile`.
4.  **Environment Variables:**
    - `TELEGRAM_BOT_TOKEN`: Your BotFather token.
    - `TELEGRAM_CHAT_ID`: Your Telegram User ID (get it from @userinfobot).
5.  **Storage Mapping:**
    - Host Path: `/path/to/your/downloads`
    - Container Path: `/app/downloads`

### Option 2: Docker Compose

Create a `docker-compose.yml` file:

```yaml
version: "3.8"
services:
  home-lab-dl:
    image: ghcr.io/yourusername/home-lab-dl:latest
    build: .
    container_name: home-lab-dl
    restart: unless-stopped
    environment:
      - TELEGRAM_BOT_TOKEN=your_token_here
      - TELEGRAM_CHAT_ID=123456789
    volumes:
      - ./downloads:/app/downloads
```

## 🧩 Usage

1. Open your Telegram Bot.
2. Paste a link (e.g., `https://youtube.com/watch?v=...` or `https://example.com/file.zip`).
3. The bot will reply: "⏳ Processing...".
4. Wait for the "✅ Download Complete" notification.
5. Check your server's download folder!

## 🤝 Contributing

Built with ❤️ by Sadman Ryan Riad for the BakeArtHome Project.
Open a Pull Request if you have cool ideas!
