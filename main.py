import os
import time
import asyncio
import logging
import shutil
import re
import subprocess
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

# --- CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Multi-user: comma-separated chat IDs
_raw_ids = os.getenv("TELEGRAM_CHAT_ID", "").split(",")
AUTHORIZED_USERS: set[int] = {int(i.strip()) for i in _raw_ids if i.strip().isdigit()}

# Paths
BASE_DIR = "downloads"

# Timeouts
AIOHTTP_TIMEOUT = None  # no global timeout — prevents premature kills on large metadata fetches
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
# pending_downloads: user_id → {"url": url, "category": cat}
pending_downloads: dict[int, dict] = {}
completed_files: dict[str, str] = {}     # timestamp_key → filepath
active_downloads: dict[str, 'ProgressTracker'] = {} # cancel_id -> tracker


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




def get_output_dir(category: str) -> str:
    """Return the output directory path based on the chosen category."""
    if category == "Movie":
        return os.path.join(BASE_DIR, "movies")
    elif category == "Show":
        return os.path.join(BASE_DIR, "shows")
    else:  # "Others" or fallback
        return BASE_DIR


# --- PROGRESS TRACKER ---
def cancel_keyboard(cancel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{cancel_id}")]])

class ProgressTracker:
    def __init__(self, status_message, loop, cancel_id: str):
        self.status_message = status_message
        self.last_update_time = 0.0
        self.loop = loop
        self.filename = "Unknown"
        self.cancel_id = cancel_id
        self.is_cancelled = False
        self.aria2_process = None
        
        active_downloads[cancel_id] = self

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
            if not self.is_cancelled:
                await self.status_message.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard(self.cancel_id))
            self.last_update_time = now
        except Exception:
            pass

    async def update_from_aria2_line(self, line: str):
        """Parse an aria2c progress line and update the Telegram message.

        Typical aria2c output:
        [#abc123  45MiB/100MiB(45%)  CN:5  DL:12MiB  ETA:4s]
        """
        now = time.time()
        if now - self.last_update_time < 3:
            return

        percent_match = re.search(r"(\d+)%", line)
        speed_match = re.search(r"DL:(\S+)", line)
        eta_match = re.search(r"ETA:(\S+)", line)

        percent = percent_match.group(1) if percent_match else "?"
        speed = speed_match.group(1) if speed_match else "?"
        eta = eta_match.group(1) if eta_match else "?"

        bar_len = 10
        try:
            pct_int = int(percent)
            filled = bar_len * pct_int // 100
        except (ValueError, TypeError):
            filled = 0
        bar = "■" * filled + "□" * (bar_len - filled)

        text = (
            f"📥 <b>Downloading:</b> {self.filename}\n"
            f"<code>[{bar}] {percent}%</code>\n"
            f"⚡ Speed: {speed}  ⏳ ETA: {eta}"
        )
        try:
            if not self.is_cancelled:
                await self.status_message.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard(self.cancel_id))
            self.last_update_time = now
        except Exception:
            pass

    def yt_dlp_hook(self, d: dict):
        if self.is_cancelled:
            raise ValueError("Cancelled by user")
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


# --- DIRECT FILE DOWNLOADER (aria2c) ---
async def download_direct(url: str, status_msg, loop, cancel_id: str, category: str = "Others"):
    """Download a direct HTTP file using aria2c with progress tracking."""
    # Resolve the real filename via a HEAD request if it's a URL
    filename = f"download_{int(time.time())}.bin"
    if url.startswith("http"):
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
                async with session.head(url, allow_redirects=True) as resp:
                    filename = get_filename_from_headers(resp.headers, url)
        except Exception:
            pass  # fall back to the timestamped name
    else:
        # local file or magnet
        filename = sanitize_filename(os.path.basename(url))

    tracker = ProgressTracker(status_msg, loop, cancel_id)
    tracker.filename = filename

    out_dir_abs = os.path.abspath(get_output_dir(category))
    os.makedirs(out_dir_abs, exist_ok=True)
    final_path = os.path.join(out_dir_abs, filename)

    cmd = [
        "aria2c",
        url,
        f"--dir={out_dir_abs}",
        f"--out={filename}",
        "--max-connection-per-server=5",
        "--split=10",
        "--min-split-size=1M",
        "--max-concurrent-downloads=5",
        "--continue=true",
        "--summary-interval=1",
        "--console-log-level=notice",
        "--download-result=hide",
        "--seed-time=0",
        f"--user-agent={USER_AGENT}",
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    tracker.aria2_process = process

    downloaded_paths = []
    # Stream aria2c output and feed progress tracker
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        decoded = line.decode("utf-8", errors="replace").strip()
        if decoded:
            logger.info("aria2c: %s", decoded)
            if "%" in decoded:
                await tracker.update_from_aria2_line(decoded)
            match = re.search(r"Download complete: (.*)", decoded)
            if match:
                downloaded_paths.append(match.group(1).strip())

    returncode = await process.wait()
    active_downloads.pop(cancel_id, None)

    if downloaded_paths:
        final_path = downloaded_paths[-1]

    if tracker.is_cancelled:
        await cleanup_temp(final_path)
        await cleanup_temp(final_path + ".aria2")
        return None

    if returncode != 0:
        await cleanup_temp(final_path)
        await cleanup_temp(final_path + ".aria2")
        raise RuntimeError(f"aria2c exited with code {returncode}")

    # Remove the local .torrent temp file if we used one
    if not url.startswith("http") and not url.startswith("magnet:") and os.path.exists(url):
        await cleanup_temp(url)

    # Remove leftover .torrent/.aria2 files in the dir
    try:
        if os.path.exists(out_dir_abs):
            for f in os.listdir(out_dir_abs):
                if f.endswith(".torrent") or f.endswith(".aria2"):
                    os.remove(os.path.join(out_dir_abs, f))
    except Exception as e:
        logger.error("Cleanup error: %s", e)

    return final_path


# --- YT-DLP DOWNLOADER ---
def _build_ydl_opts(fmt: str, tracker: ProgressTracker, category: str) -> dict:
    """Build yt-dlp options for the given format choice."""
    out_dir = get_output_dir(category)
    os.makedirs(out_dir, exist_ok=True)
    
    opts: dict = {
        "outtmpl": f"{out_dir}/%(title).80s.%(ext)s",
        "progress_hooks": [tracker.yt_dlp_hook],
        "quiet": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "socket_timeout": YT_DLP_SOCKET_TIMEOUT,
        "http_headers": {"User-Agent": USER_AGENT},
        "concurrent_fragment_downloads": 5,
        "retries": 3,
        "fragment_retries": 3,
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


async def download_video(url: str, fmt: str, status_msg, loop, cancel_id: str, category: str) -> str | None:
    """Download a video/audio via yt-dlp; returns the final filepath or None."""
    tracker = ProgressTracker(status_msg, loop, cancel_id)
    opts = _build_ydl_opts(fmt, tracker, category)

    def run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            # Setting 'download=True' downloads it in one go instead of fetching info then passing to download() again
            info = ydl.extract_info(url, download=True)
            if not info:
                raise Exception("Failed to extract video info")
            
            tracker.filename = info.get("title", "Video")
            # Determine the actual output path
            prepared = ydl.prepare_filename(info)
            if fmt == "audio":
                prepared = os.path.splitext(prepared)[0] + ".mp3"
            return prepared

    try:
        filepath = await loop.run_in_executor(None, run)
        return filepath
    except Exception as e:
        if tracker.is_cancelled:
            return None
        raise
    finally:
        active_downloads.pop(cancel_id, None)


# --- INLINE KEYBOARD ---
def category_keyboard() -> InlineKeyboardMarkup:
    """Build the category selection inline keyboard."""
    buttons = [
        [
            InlineKeyboardButton("🎬 Movie", callback_data="cat_Movie"),
            InlineKeyboardButton("📺 Show", callback_data="cat_Show"),
        ],
        [
            InlineKeyboardButton("📱 Others", callback_data="cat_Others"),
            InlineKeyboardButton("❌ Dismiss", callback_data="dismiss"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def format_keyboard() -> InlineKeyboardMarkup:
    """Build the format selection inline keyboard."""
    buttons = [
        [
            InlineKeyboardButton("🎬 Best Video", callback_data="fmt_best"),
            InlineKeyboardButton("📱 1080p", callback_data="fmt_1080p"),
        ],
        [
            InlineKeyboardButton("🎧 Audio", callback_data="fmt_audio"),
            InlineKeyboardButton("❌ Dismiss", callback_data="dismiss"),
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


async def _handle_successful_download(filepath: str, status_msg):
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


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive a URL and route it."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    url = update.message.text.strip()

    # Accept both http(s) links and magnet URIs
    if not url.startswith("http") and not url.startswith("magnet:"):
        await update.message.reply_text("⚠️ Please send a valid link or magnet URI.")
        return


    # --- Route 2: Video or Direct Link → Category/Format Selection ---
    is_social = any(s in url for s in ["tiktok.com", "instagram.com", "facebook.com", "fb.watch", "twitter.com", "x.com"])
    
    if is_social:
        # Smart routing: Skip category selection
        pending_downloads[user_id] = {"url": url, "category": "Others"}
        await update.message.reply_text(
            f"🔗 <b>Social link detected!</b>\n<code>{url[:80]}</code>\n\nChoose format:",
            parse_mode="HTML",
            reply_markup=format_keyboard(),
        )
    else:
        # General link (video site or direct download)
        pending_downloads[user_id] = {"url": url, "category": None}
        await update.message.reply_text(
            f"🔗 <b>Link received!</b>\n<code>{url[:80]}</code>\n\n📂 Select Category:",
            parse_mode="HTML",
            reply_markup=category_keyboard(),
        )


async def handle_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_authorized(user_id):
        return

    data = query.data

    if data == "dismiss":
        await query.message.delete()
        pending_downloads.pop(user_id, None)
        return

    if data.startswith("cancel_"):
        cancel_id = data.split("_")[1]
        tracker = active_downloads.get(cancel_id)
        if tracker:
            tracker.is_cancelled = True
            if tracker.aria2_process:
                try:
                    tracker.aria2_process.terminate()
                except Exception:
                    pass
            active_downloads.pop(cancel_id, None)
            await query.edit_message_text("❌ Download cancelled.")
        else:
            await query.answer("Download already finished or not found!")
        return

    state = pending_downloads.get(user_id)
    if not state:
        await query.edit_message_text("⚠️ No pending link. Send a new one.")
        return

    url = state.get("url")
    loop = asyncio.get_running_loop()

    if data.startswith("cat_"):
        cat = data.split("_")[1]
        pending_downloads[user_id]["category"] = cat
        
        # If it's not a video site, skip format selection and start aria2 download
        if not is_video_site(url):
            pending_downloads.pop(user_id, None)
            status_msg = await query.edit_message_text(f"⏳ Starting direct download in {cat}…")
            cancel_id = str(int(time.time() * 1000)) + str(user_id)
            try:
                filepath = await download_direct(url, status_msg, loop, cancel_id, category=cat)
                if filepath:
                    await _handle_successful_download(filepath, status_msg)
            except Exception as e:
                logger.error("Direct download error: %s", e, exc_info=True)
                await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")
            return

        # It's a video site, ask for format
        await query.edit_message_text(
            f"🔗 <b>Category:</b> {cat}\n<code>{url[:80]}</code>\n\nChoose format:",
            parse_mode="HTML",
            reply_markup=format_keyboard(),
        )
        return

    if data.startswith("fmt_"):
        fmt = data.replace("fmt_", "")
        cat = state.get("category", "Others")
        pending_downloads.pop(user_id, None)

        labels = {"best": "🎬 Best Video", "1080p": "📱 1080p", "audio": "🎧 Audio"}
        status_msg = await query.edit_message_text(
            f"⏳ Starting download… ({labels.get(fmt, fmt)} -> {cat})"
        )
        cancel_id = str(int(time.time() * 1000)) + str(user_id)

        try:
            filepath = await download_video(url, fmt, status_msg, loop, cancel_id, category=cat)
            if filepath:
                await _handle_successful_download(filepath, status_msg)
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


async def handle_torrent_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive a .torrent file upload and route it."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".torrent"):
        await update.message.reply_text("⚠️ Please send a valid .torrent file.")
        return

    status_msg = await update.message.reply_text("📥 Receiving .torrent file…")
    file = await context.bot.get_file(doc.file_id)
    
    os.makedirs(os.path.join(BASE_DIR, "temp"), exist_ok=True)
    temp_path = os.path.join(BASE_DIR, "temp", sanitize_filename(doc.file_name))
    await file.download_to_drive(temp_path)
    
    # Route to category
    pending_downloads[user_id] = {"url": temp_path, "category": None}
    await status_msg.edit_text(
        f"🔗 <b>Torrent File Received!</b>\n<code>{doc.file_name}</code>\n\n📂 Select Category:",
        parse_mode="HTML",
        reply_markup=category_keyboard(),
    )


# --- MAIN ---
if __name__ == "__main__":
    os.makedirs(BASE_DIR, exist_ok=True)

    if not TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN not set!")
        exit(1)
    if not AUTHORIZED_USERS:
        logger.warning("No TELEGRAM_CHAT_ID configured — bot will ignore everyone.")

    app = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_inline_buttons))
    app.add_handler(
        MessageHandler(filters.Regex(r"^/get_\d+"), handle_get_file)
    )
    app.add_handler(
        MessageHandler(filters.Document.FileExtension("torrent"), handle_torrent_document)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_link)
    )

    logger.info("Authorized users: %s", AUTHORIZED_USERS)
    print("🤖 Bot Online!")
    app.run_polling()