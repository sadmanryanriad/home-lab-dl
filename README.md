# 📥 Home-Lab-DL

> **A Headless, Dockerized Telegram Bot for your Home Server.**
> Send links from anywhere; files appear on your server. Powered by `yt-dlp`, `aria2`, and qBittorrent integration.

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-2CA5E0?logo=telegram&logoColor=white)

---

## 🚀 Features

- **Intelligent Link Routing** — The bot auto-detects link type and takes the right action:
  - 🧲 **Torrent / Magnet** — Saved to the autoload folder for qBittorrent to pick up.
  - 🎥 **Video Sites** — Shows an inline keyboard for quality selection (Best / 1080p / Audio).
  - 📥 **Direct Links** — Downloaded immediately via `aria2c` with multi-connection acceleration.
- **Interactive Format Menu** — For video sites, choose quality via inline buttons:
  - 🎬 **Best Video** — Highest available resolution (4K/8K)
  - 📱 **1080p** — Capped at 1080p for smaller files
  - 🎧 **Audio MP3** — Extract audio only (192kbps MP3)
  - ❌ **Cancel** — Discard the link
- **Universal Downloader** — Supports YouTube, TikTok, Facebook, Instagram, Twitter/X, Reddit, Vimeo, Dailymotion, and any direct HTTP file link.
- **Reverse Cloud Upload** — After download, get a `/get_XXXXX` command to upload the file back to Telegram chat (files ≤ 50 MB).
- **Multi-User Access** — Add multiple authorized Telegram User IDs (comma-separated) so family members can use the bot.
- **Auto-Upgrade yt-dlp** — Container upgrades `yt-dlp` on every start, keeping site extractors up to date (critical for Instagram, TikTok, etc.).
- **Robustness** — aria2c multi-connection downloads with resume support, yt-dlp socket timeout (30s), and a browser User-Agent to prevent login-required errors.
- **Live Progress Bar** — Edits a single message with a visual progress bar (no notification spam). Shows speed & ETA for aria2 downloads.
- **Smart Filenames** — Auto-detects real filenames from HTTP headers and sanitizes them.
- **Auto-Cleanup** — Wipes temp files on failed downloads.

---

## 📋 Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/install/) installed on your server.
- A **Telegram Bot Token** from [@BotFather](https://t.me/BotFather).
- Your **Telegram User ID** from [@userinfobot](https://t.me/userinfobot).

---

## 🛠️ Project Structure

```
home-lab-dl/
├── Dockerfile          # Container image (Python 3.11 + ffmpeg + aria2)
├── requirements.txt    # Python dependencies
├── main.py             # Bot application
├── .env                # Environment variables (not committed)
├── .gitignore          # Git ignore rules
└── downloads/
    ├── completed/      # Finished downloads (mapped to HDD)
    ├── temp/           # In-progress downloads
    └── autoload/       # .torrent / .magnet files for qBittorrent
```

---

## ⚙️ Configuration

Create a `.env` file in the project root with the following variables:

```env
# Telegram Bot Token (from @BotFather)
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Authorized Telegram User IDs (comma-separated for multiple users)
TELEGRAM_CHAT_ID=123456789,987654321
```

| Variable             | Required | Description                                         |
| -------------------- | -------- | --------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN` | ✅       | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID`   | ✅       | Comma-separated list of authorized user IDs         |

> **Tip:** Get your User ID by messaging [@userinfobot](https://t.me/userinfobot) on Telegram.

---

## 🐳 Deployment

### Option 1: Coolify (Recommended for Home Labs)

1. Create a **Private Repository (with GitHub App)** resource in Coolify.
2. Select this repository.
3. Set **Build Pack** to `Dockerfile`.
4. Add environment variables: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. Add a **Storage Mapping**:
   - Host Path: `/path/to/your/downloads`
   - Container Path: `/app/downloads`
6. Deploy.

### Option 2: Docker Compose

1. Clone the repository:

   ```bash
   git clone https://github.com/yourusername/home-lab-dl.git
   cd home-lab-dl
   ```

2. Create the `.env` file as shown in the [Configuration](#-configuration) section.

3. Create a `docker-compose.yml`:

   ```yaml
   services:
     home-lab-dl:
       build: .
       container_name: home-lab-dl
       restart: unless-stopped
       env_file:
         - .env
       volumes:
         - /mnt/storage/downloads/completed:/app/downloads/completed
         - /mnt/storage/downloads/temp:/app/downloads/temp
         - /mnt/storage/downloads/autoload:/app/downloads/autoload
   ```

4. Build and start:

   ```bash
   docker compose up --build -d
   ```

5. Check logs:

   ```bash
   docker logs -f home-lab-dl
   ```

   You should see yt-dlp upgrading, followed by `🤖 Bot Online!`

### Option 3: Run Locally (Development)

1. Clone the repo and create a virtual environment:

   ```bash
   git clone https://github.com/yourusername/home-lab-dl.git
   cd home-lab-dl
   python -m venv venv
   source venv/bin/activate   # Linux/Mac
   .\venv\Scripts\activate    # Windows
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Make sure `ffmpeg` and `aria2` are installed on your system:

   ```bash
   # Ubuntu/Debian
   sudo apt install ffmpeg aria2

   # macOS
   brew install ffmpeg aria2

   # Windows — download from https://ffmpeg.org and https://aria2.github.io
   ```

4. Create the `.env` file as shown in the [Configuration](#-configuration) section.

5. Run the bot:
   ```bash
   python main.py
   ```

---

## 🧩 Usage

### Downloading Content

1. Open your Telegram Bot.
2. Send `/start` to see the welcome message.
3. Paste any link (YouTube, Instagram, TikTok, direct file URL, etc.).
4. The bot replies with an **inline keyboard** — tap your preferred format:
   - 🎬 **Best Video** — Maximum quality
   - 📱 **1080p** — HD quality, smaller file
   - 🎧 **Audio MP3** — Audio extraction only
   - ❌ **Cancel** — Discard
5. Wait for the progress bar to complete.
6. The bot replies: `✅ Saved: filename.mp4. /get_12345 to upload.`

### Uploading to Chat (Reverse Cloud)

1. After a successful download, the bot provides a `/get_XXXXX` command.
2. Tap or type the command to upload the file directly into the chat.
3. **Limit:** Telegram Bot API allows uploads up to **50 MB**. Larger files remain on the server — access them via your mapped volume.

### Supported Sites

YouTube, TikTok, Facebook, Instagram, Twitter/X, Reddit, Vimeo, Dailymotion, and [1000+ sites supported by yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md). Any direct HTTP/HTTPS file link also works. `.torrent` files and `magnet:` URIs are routed to qBittorrent.

---

## 🔒 Security

- **Authorized users only** — The bot ignores messages from any Telegram user not in the `TELEGRAM_CHAT_ID` list.
- **No exposed ports** — The bot uses long-polling (outbound connections only). No inbound ports needed.
- **Secrets in `.env`** — The `.env` file is git-ignored and never committed.

---

## 📦 Dependencies

| Package                    | Purpose                                            |
| -------------------------- | -------------------------------------------------- |
| `python-telegram-bot` v20+ | Async Telegram Bot framework                       |
| `yt-dlp`                   | Video/audio extraction (auto-upgraded at start)    |
| `aiohttp`                  | Async HTTP client (HEAD requests, torrent fetches) |
| `python-dotenv`            | Load `.env` variables                              |
| `ffmpeg` (system)          | Audio extraction & video muxing                    |
| `aria2` (system)           | Multi-connection direct file downloader            |

## 🔧 Troubleshooting

### Torrent files aren't being picked up by qBittorrent

The bot saves `.torrent` files and `.magnet` URIs to the `/app/downloads/autoload` directory (mapped to `/mnt/storage/downloads/autoload` on the host). For qBittorrent to automatically start these downloads:

1. Open qBittorrent Web UI → **Options** → **Downloads**.
2. Enable **"Automatically add torrents from"** and point it to `/mnt/storage/downloads/autoload`.
3. (Optional) Enable **"Delete .torrent files afterwards"** to keep the folder clean.

### aria2 download fails with "connection refused"

This usually means the remote server is blocking multi-connection downloads. The bot will show the error; try the link again or download it through a video site if applicable.

### Large files timing out

`AIOHTTP_TIMEOUT` is set to `None` (no timeout) for metadata fetches. aria2c handles its own connection retries and timeouts internally.

---

## 🤝 Contributing

Built with ❤️ by Sadman Ryan Riad for the BakeArtHome Project.
Open a Pull Request if you have cool ideas!
