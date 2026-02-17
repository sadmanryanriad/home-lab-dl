import os
import time
import asyncio
import logging
import shutil
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp
import aiohttp
import aiofiles

# --- CONFIGURATION ---
load_dotenv()
# These env vars will come from Coolify
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AUTHORIZED_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# Container Paths (Mapped to Host)
BASE_DIR = "downloads"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "completed")
TEMP_DIR = os.path.join(BASE_DIR, "temp")

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class ProgressTracker:
    def __init__(self, status_message, loop):
        self.status_message = status_message
        self.last_update_time = 0
        self.loop = loop
        self.filename = "Unknown"

    async def update(self, current, total):
        now = time.time()
        # Update UI every 3 seconds to avoid flooding API
        if (now - self.last_update_time < 3) and (current != total):
            return

        percent = (current / total) * 100 if total > 0 else 0
        bar_length = 10
        filled_length = int(bar_length * current // total) if total > 0 else 0
        bar = '■' * filled_length + '□' * (bar_length - filled_length)
        
        # Convert bytes to MB
        current_mb = current / 1024 / 1024
        total_mb = total / 1024 / 1024
        
        text = (
            f"📥 <b>Downloading:</b> {self.filename}\n"
            f"<code>[{bar}] {percent:.1f}%</code>\n"
            f"💾 {current_mb:.1f}MB / {total_mb:.1f}MB"
        )
        
        try:
            await self.status_message.edit_text(text, parse_mode='HTML')
            self.last_update_time = now
        except Exception as e:
            logging.warning(f"UI Update failed: {e}")

    def yt_dlp_hook(self, d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes')
            if total:
                asyncio.run_coroutine_threadsafe(
                    self.update(downloaded, total), 
                    self.loop
                )

async def cleanup_temp(filename):
    """Deletes temp file if download fails"""
    path = os.path.join(TEMP_DIR, filename)
    if os.path.exists(path):
        os.remove(path)

async def download_direct(url, update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("⏳ Initializing direct download...")
    
    # Extract filename from URL
    filename = url.split("/")[-1].split("?")[0]
    if not filename:
        filename = f"file_{int(time.time())}.bin"
        
    tracker = ProgressTracker(status_msg, asyncio.get_running_loop())
    tracker.filename = filename

    temp_path = os.path.join(TEMP_DIR, filename)
    final_path = os.path.join(DOWNLOAD_DIR, filename)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    await status_msg.edit_text(f"❌ HTTP Error: {response.status}")
                    return
                
                total_size = int(response.headers.get('content-length', 0))
                
                async with aiofiles.open(temp_path, mode='wb') as f:
                    downloaded = 0
                    async for chunk in response.content.iter_chunked(1024 * 1024): # 1MB chunks
                        await f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            await tracker.update(downloaded, total_size)

        # Move to completed folder
        shutil.move(temp_path, final_path)
        await status_msg.edit_text(f"✅ <b>Download Complete!</b>\n📂 Saved: <code>{filename}</code>", parse_mode='HTML')

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")
        await cleanup_temp(filename)

async def download_video(url, update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("⏳ Fetching video info...")
    loop = asyncio.get_running_loop()
    tracker = ProgressTracker(status_msg, loop)
    
    ydl_opts = {
        'format': 'best',
        # Save directly to temp, then yt-dlp moves it? 
        # Easier: Save to final, but use .part file
        'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
        'progress_hooks': [tracker.yt_dlp_hook],
        'quiet': True,
        'noplaylist': True
    }

    def run_yt_dlp():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            tracker.filename = info.get('title', 'Unknown Video')
            ydl.download([url])

    try:
        await loop.run_in_executor(None, run_yt_dlp)
        await status_msg.edit_text(f"✅ <b>Video Saved!</b>\n🎬 Title: {tracker.filename}", parse_mode='HTML')
    except Exception as e:
        await status_msg.edit_text(f"❌ Video Error: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    if user_id != AUTHORIZED_CHAT_ID:
        logging.warning(f"Unauthorized access attempt from ID: {user_id}")
        return # Silent ignore for security

    url = update.message.text.strip()
    
    if "youtube.com" in url or "youtu.be" in url or "tiktok.com" in url or "facebook.com" in url or "instagram.com" in url:
        await download_video(url, update, context)
    elif url.startswith("http"):
        await download_direct(url, update, context)
    else:
        await update.message.reply_text("⚠️ Invalid Link. Send a URL starting with http/https.")

if __name__ == '__main__':
    # Ensure folders exist
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    if not TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not found in env")
        exit(1)

    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print(f"🤖 Home-Lab-DL Bot is Online! Listening for User: {AUTHORIZED_CHAT_ID}")
    application.run_polling()