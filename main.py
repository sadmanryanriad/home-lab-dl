import os
import time
import asyncio
import logging
import shutil
import re
from urllib.parse import unquote
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp
import aiohttp
import aiofiles

# --- CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AUTHORIZED_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# Paths
BASE_DIR = "downloads"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "completed")
TEMP_DIR = os.path.join(BASE_DIR, "temp")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- UTILS ---
def sanitize_filename(name):
    """Clean up filenames and limit length"""
    # Remove bad chars
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    # Decode URL encoded chars (e.g. %20 -> space)
    name = unquote(name)
    
    # If name is insanely long (like Google tokens), truncate it
    if len(name) > 100:
        name_parts = os.path.splitext(name)
        ext = name_parts[1]
        if not ext: ext = ".bin"
        # Keep first 50 chars + timestamp + ext
        name = f"{name_parts[0][:50]}_{int(time.time())}{ext}"
    
    return name

def get_filename_from_headers(headers, url):
    """Try to find the real filename from Content-Disposition"""
    filename = None
    cd = headers.get("Content-Disposition")
    
    if cd:
        # Look for filename="example.mp4"
        fname_match = re.findall(r'filename="?([^"]+)"?', cd)
        if fname_match:
            filename = fname_match[0]
            
    if not filename:
        # Fallback: Parse URL
        filename = url.split("/")[-1].split("?")[0]
        
    if not filename or len(filename) < 2:
        filename = f"download_{int(time.time())}.bin"
        
    return sanitize_filename(filename)

# --- PROGRESS TRACKER ---
class ProgressTracker:
    def __init__(self, status_message, loop):
        self.status_message = status_message
        self.last_update_time = 0
        self.loop = loop
        self.filename = "Unknown"

    async def update(self, current, total):
        now = time.time()
        if (now - self.last_update_time < 3) and (current != total):
            return

        percent = (current / total) * 100 if total > 0 else 0
        bar_length = 10
        filled = int(bar_length * current // total) if total > 0 else 0
        bar = '■' * filled + '□' * (bar_length - filled)
        
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
        except Exception:
            pass

    def yt_dlp_hook(self, d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes')
            if total:
                asyncio.run_coroutine_threadsafe(
                    self.update(downloaded, total), self.loop
                )

async def cleanup_temp(filename):
    path = os.path.join(TEMP_DIR, filename)
    if os.path.exists(path):
        os.remove(path)

# --- DIRECT DOWNLOADER ---
async def download_direct(url, update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("⏳ Connecting...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    await status_msg.edit_text(f"❌ Error: HTTP {response.status}")
                    return

                # Get smart filename
                filename = get_filename_from_headers(response.headers, url)
                
                tracker = ProgressTracker(status_msg, asyncio.get_running_loop())
                tracker.filename = filename
                
                temp_path = os.path.join(TEMP_DIR, filename)
                final_path = os.path.join(DOWNLOAD_DIR, filename)
                total_size = int(response.headers.get('content-length', 0))

                async with aiofiles.open(temp_path, mode='wb') as f:
                    downloaded = 0
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            await tracker.update(downloaded, total_size)

        shutil.move(temp_path, final_path)
        await status_msg.edit_text(f"✅ <b>Complete!</b>\n📂 <code>{filename}</code>", parse_mode='HTML')

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)[:100]}") # Shorten error msg
        await cleanup_temp(filename)

# --- YOUTUBE DOWNLOADER ---
async def download_video(url, update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("⏳ Processing Video...")
    loop = asyncio.get_running_loop()
    tracker = ProgressTracker(status_msg, loop)
    
    ydl_opts = {
        # FIX 1: Best Video + Best Audio merged
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
        'progress_hooks': [tracker.yt_dlp_hook],
        'quiet': True,
        'noplaylist': True,
        # FIX 2: Restrict filename length for YouTube too
        'restrictfilenames': True
    }

    def run_yt_dlp():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            tracker.filename = info.get('title', 'Video')
            ydl.download([url])

    try:
        await loop.run_in_executor(None, run_yt_dlp)
        await status_msg.edit_text(f"✅ <b>Video Saved!</b>\n🎬 {tracker.filename}", parse_mode='HTML')
    except Exception as e:
        await status_msg.edit_text(f"❌ YT Error: {str(e)}")

# --- HANDLER ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != AUTHORIZED_CHAT_ID: return
    
    url = update.message.text.strip()
    if any(x in url for x in ["youtube.com", "youtu.be", "tiktok.com", "facebook.com", "instagram.com"]):
        await download_video(url, update, context)
    elif url.startswith("http"):
        await download_direct(url, update, context)
    else:
        await update.message.reply_text("⚠️ Invalid Link.")

if __name__ == '__main__':
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    if not TOKEN: exit(1)
    
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    print("🤖 Bot Updated & Online!")
    app.run_polling()