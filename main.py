import os
import time
import asyncio
import logging
import shutil
import re
from urllib.parse import unquote
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
import yt_dlp
import aiohttp
import aiofiles

# --- CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Multi-user: comma-separated chat IDs
_raw_ids = os.getenv("TELEGRAM_CHAT_ID", "").split(",")
AUTHORIZED_USERS: set[int] = {int(i.strip()) for i in _raw_ids if i.strip().isdigit()}

# Paths
BASE_DIR = "downloads"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "completed")
TEMP_DIR = os.path.join(BASE_DIR, "temp")

# Timeouts
AIOHTTP_TIMEOUT = aiohttp.ClientTimeout(total=300)  # 5 min for direct downloads
YT_DLP_SOCKET_TIMEOUT = 30  # seconds

# Telegram upload limit
TELEGRAM_MAX_BYTES = 50 * 1024 * 1024  # 50 MB

# Common User-Agent (fixes Instagram login-required errors)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- STATE ---
pending_links: dict[int, str] = {}       # user_id → url
completed_files: dict[str, str] = {}     # timestamp_key → filepath


# --- UTILS ---
def is_authorized(user_id: int) -> bool:
    return user_id in AUTHORIZED_USERS


def sanitize_filename(name: str) -> str:
    """Clean up filenames and limit length."""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = unquote(name)
    if len(name) > 100:
        base, ext = os.path.splitext(name)
        if not ext:
            ext = ".bin"
        name = f"{base[:50]}_{int(time.time())}{ext}"
    return name


def get_filename_from_headers(headers, url: str) -> str:
    """Try to find the real filename from Content-Disposition."""
    filename = None
    cd = headers.get("Content-Disposition")
    if cd:
        match = re.findall(r'filename="?([^"]+)"?', cd)
        if match:
            filename = match[0]
    if not filename:
        filename = url.split("/")[-1].split("?")[0]
    if not filename or len(filename) < 2:
        filename = f"download_{int(time.time())}.bin"
    return sanitize_filename(filename)


def is_video_site(url: str) -> bool:
    """Check if the URL belongs to a site handled by yt-dlp."""
    sites = [
        "youtube.com", "youtu.be",
        "tiktok.com",
        "facebook.com", "fb.watch",
        "instagram.com",
        "twitter.com", "x.com",
        "reddit.com",
        "vimeo.com",
        "dailymotion.com",
    ]
    return any(s in url for s in sites)


# --- PROGRESS TRACKER ---
class ProgressTracker:
    def __init__(self, status_message, loop):
        self.status_message = status_message
        self.last_update_time = 0.0
        self.loop = loop
        self.filename = "Unknown"

    async def update(self, current: int, total: int):
        now = time.time()
        if (now - self.last_update_time < 3) and (current != total):
            return
        percent = (current / total) * 100 if total > 0 else 0
        bar_len = 10
        filled = int(bar_len * current // total) if total > 0 else 0
        bar = "■" * filled + "□" * (bar_len - filled)
        cur_mb = current / 1024 / 1024
        tot_mb = total / 1024 / 1024
        text = (
            f"📥 <b>Downloading:</b> {self.filename}\n"
            f"<code>[{bar}] {percent:.1f}%</code>\n"
            f"💾 {cur_mb:.1f}MB / {tot_mb:.1f}MB"
        )
        try:
            await self.status_message.edit_text(text, parse_mode="HTML")
            self.last_update_time = now
        except Exception:
            pass

    def yt_dlp_hook(self, d: dict):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes")
            if total and downloaded:
                asyncio.run_coroutine_threadsafe(
                    self.update(downloaded, total), self.loop
                )


# --- CLEANUP ---
async def cleanup_temp(path: str):
    """Remove a temp file if it exists."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# --- DIRECT FILE DOWNLOADER ---
async def download_direct(url: str, status_msg, loop):
    """Download a plain HTTP file with progress and timeout."""
    temp_path = ""
    try:
        async with aiohttp.ClientSession(timeout=AIOHTTP_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ HTTP Error {resp.status}")
                    return None

                filename = get_filename_from_headers(resp.headers, url)
                tracker = ProgressTracker(status_msg, loop)
                tracker.filename = filename

                temp_path = os.path.join(TEMP_DIR, filename)
                final_path = os.path.join(DOWNLOAD_DIR, filename)
                total_size = int(resp.headers.get("content-length", 0))

                async with aiofiles.open(temp_path, mode="wb") as f:
                    downloaded = 0
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            await tracker.update(downloaded, total_size)

        shutil.move(temp_path, final_path)
        return final_path

    except Exception as e:
        await cleanup_temp(temp_path)
        raise e


# --- YT-DLP DOWNLOADER ---
def _build_ydl_opts(fmt: str, tracker: ProgressTracker) -> dict:
    """Build yt-dlp options for the given format choice."""
    opts: dict = {
        "outtmpl": f"{DOWNLOAD_DIR}/%(title).80s.%(ext)s",
        "progress_hooks": [tracker.yt_dlp_hook],
        "quiet": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "socket_timeout": YT_DLP_SOCKET_TIMEOUT,
        "http_headers": {"User-Agent": USER_AGENT},
    }

    if fmt == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    elif fmt == "1080p":
        opts["format"] = "bestvideo[height<=1080]+bestaudio/best"
        opts["merge_output_format"] = "mp4"
    else:  # best
        opts["format"] = "bestvideo+bestaudio/best"
        opts["merge_output_format"] = "mp4"

    return opts


async def download_video(url: str, fmt: str, status_msg, loop) -> str | None:
    """Download a video/audio via yt-dlp; returns the final filepath or None."""
    tracker = ProgressTracker(status_msg, loop)
    opts = _build_ydl_opts(fmt, tracker)

    def run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            tracker.filename = info.get("title", "Video")
            ydl.download([url])
            # Determine the actual output path
            prepared = ydl.prepare_filename(info)
            if fmt == "audio":
                prepared = os.path.splitext(prepared)[0] + ".mp3"
            return prepared

    filepath = await loop.run_in_executor(None, run)
    return filepath


# --- INLINE KEYBOARD ---
def format_keyboard() -> InlineKeyboardMarkup:
    """Build the format selection inline keyboard."""
    buttons = [
        [
            InlineKeyboardButton("🎬 Best Video", callback_data="fmt_best"),
            InlineKeyboardButton("📱 1080p", callback_data="fmt_1080p"),
        ],
        [
            InlineKeyboardButton("🎧 Audio MP3", callback_data="fmt_audio"),
            InlineKeyboardButton("❌ Cancel", callback_data="fmt_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# --- HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start."""
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        "👋 <b>Home-Lab Downloader</b>\n\n"
        "Send me any link and I'll let you choose the format.\n"
        "After download, use the provided command to upload the file here.",
        parse_mode="HTML",
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive a URL, store it, and show the format keyboard."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("⚠️ Please send a valid link.")
        return

    pending_links[user_id] = url
    await update.message.reply_text(
        f"🔗 <b>Link received!</b>\n<code>{url[:80]}</code>\n\nChoose format:",
        parse_mode="HTML",
        reply_markup=format_keyboard(),
    )


async def handle_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_authorized(user_id):
        return

    choice = query.data  # fmt_best / fmt_1080p / fmt_audio / fmt_cancel
    url = pending_links.pop(user_id, None)

    if choice == "fmt_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return

    if not url:
        await query.edit_message_text("⚠️ No pending link. Send a new one.")
        return

    fmt = choice.replace("fmt_", "")  # best / 1080p / audio
    labels = {"best": "🎬 Best Video", "1080p": "📱 1080p", "audio": "🎧 Audio MP3"}
    status_msg = await query.edit_message_text(
        f"⏳ Starting download… ({labels.get(fmt, fmt)})"
    )

    loop = asyncio.get_running_loop()
    filepath = None

    try:
        if is_video_site(url):
            filepath = await download_video(url, fmt, status_msg, loop)
        else:
            # Direct download (format choice doesn't apply, just grab the file)
            filepath = await download_direct(url, status_msg, loop)

        if filepath and os.path.exists(filepath):
            filename = os.path.basename(filepath)
            ts_key = str(int(time.time()))
            completed_files[ts_key] = filepath
            size_mb = os.path.getsize(filepath) / 1024 / 1024

            await status_msg.edit_text(
                f"✅ <b>Saved:</b> <code>{filename}</code>\n"
                f"💾 Size: {size_mb:.1f} MB\n\n"
                f"📤 Upload to chat: /get_{ts_key}",
                parse_mode="HTML",
            )
        else:
            await status_msg.edit_text("❌ Download failed — file not found.")

    except Exception as e:
        logger.error("Download error: %s", e, exc_info=True)
        await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")


async def handle_get_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /get_<timestamp> — upload the file to chat."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    text = update.message.text.strip()
    match = re.match(r"/get_(\d+)", text)
    if not match:
        await update.message.reply_text("⚠️ Invalid command format. Use /get_XXXXX")
        return

    ts_key = match.group(1)
    filepath = completed_files.get(ts_key)

    if not filepath or not os.path.exists(filepath):
        await update.message.reply_text("⚠️ File not found or already cleaned up.")
        return

    file_size = os.path.getsize(filepath)
    size_mb = file_size / 1024 / 1024

    if file_size > TELEGRAM_MAX_BYTES:
        await update.message.reply_text(
            f"⚠️ File too big ({size_mb:.1f} MB). "
            f"Telegram limit is {TELEGRAM_MAX_BYTES // 1024 // 1024} MB.\n"
            f"Access it directly on the server."
        )
        return

    status_msg = await update.message.reply_text("📤 Uploading to Telegram…")
    try:
        with open(filepath, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=os.path.basename(filepath),
                read_timeout=120,
                write_timeout=120,
            )
        await status_msg.edit_text("✅ Upload complete!")
        # Clean up from tracking dict (file stays on disk)
        completed_files.pop(ts_key, None)
    except Exception as e:
        logger.error("Upload error: %s", e, exc_info=True)
        await status_msg.edit_text(f"❌ Upload failed: {str(e)[:150]}")


# --- MAIN ---
if __name__ == "__main__":
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    if not TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN not set!")
        exit(1)
    if not AUTHORIZED_USERS:
        logger.warning("No TELEGRAM_CHAT_ID configured — bot will ignore everyone.")

    app = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_format_choice))
    app.add_handler(
        MessageHandler(filters.Regex(r"^/get_\d+"), handle_get_file)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_link)
    )

    logger.info("Authorized users: %s", AUTHORIZED_USERS)
    print("🤖 Bot Online!")
    app.run_polling()