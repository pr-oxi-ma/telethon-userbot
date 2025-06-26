import os
import base64
import uuid
import asyncio
import shutil
import tempfile
from pathlib import Path
from telethon import TelegramClient, events, Button
from yt_dlp import YoutubeDL

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = 'telethon_userbot'
COOKIES_FILE = 'cookies.txt'

# Reconstruct session from 4 parts
session_b64 = ""
for suffix in ["AA", "AB", "AC", "AD"]:
    session_b64 += os.getenv(f"SESSION_PART_{suffix}", "")

if session_b64 and not os.path.exists(f"{SESSION_NAME}.session"):
    with open(f"{SESSION_NAME}.session", "wb") as f:
        f.write(base64.b64decode(session_b64))

# Reconstruct cookies.txt from COOKIES_B64
cookies_b64 = os.getenv("COOKIES_B64")
if cookies_b64:
    with open(COOKIES_FILE, "wb") as f:
        f.write(base64.b64decode(cookies_b64))

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
SESSION_STORE = {}

def has_cookies():
    return os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0

def sanitize_filename(name):
    return "".join(c if c.isalnum() or c in " -_." else "_" for c in name)[:100].strip()

def format_size(bytes_):
    return f"{bytes_ / (1024 * 1024):.1f} MB" if bytes_ else "?"

def format_duration(seconds):
    minutes = int(seconds) // 60
    seconds = int(seconds) % 60
    return f"{minutes}m {seconds}s"

def extract_formats(url):
    ydl_opts = {'quiet': True, 'skip_download': True, 'forcejson': True}
    if has_cookies():
        ydl_opts['cookiefile'] = COOKIES_FILE
    with YoutubeDL(ydl_opts) as ydl:
        data = ydl.extract_info(url, download=False)

    title = sanitize_filename(data.get("title", "Untitled"))
    formats = data.get("formats", [])
    buttons = []
    seen = set()

    best_audio = None
    best_audio_size = None
    for fmt in formats:
        if fmt.get("vcodec") == "none" and fmt.get("acodec") != "none":
            if not best_audio or (fmt.get("abr") or 0) > (best_audio.get("abr") or 0):
                best_audio = fmt
                best_audio_size = fmt.get("filesize") or fmt.get("filesize_approx") or 0

    for fmt in sorted(formats, key=lambda f: f.get("height") or 0, reverse=True):
        height = fmt.get("height")
        if not height or fmt.get("vcodec") == "none":
            continue
        if height in seen:
            continue
        seen.add(height)
        size_bytes = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        size_str = format_size(size_bytes)
        has_audio = fmt.get("acodec") != "none"
        label = f"{height}p {'~audio' if has_audio else '+merged'} - {size_str}"
        token = uuid.uuid4().hex[:10]
        SESSION_STORE[token] = {"url": url, "height": height, "type": "video", "title": title}
        buttons.append([Button.inline(label, data=token)])

    if best_audio:
        token = uuid.uuid4().hex[:10]
        SESSION_STORE[token] = {
            "url": url,
            "fmt_id": best_audio["format_id"],
            "type": "audio",
            "abr": best_audio.get("abr", 0),
            "duration": best_audio.get("duration", 0),
            "filesize": best_audio_size,
            "ext": best_audio.get("ext", "audio"),
            "title": title
        }
        size_label = format_size(best_audio_size)
        label = f"🎵 Audio Only ({best_audio.get('ext')}) - {size_label}"
        buttons.append([Button.inline(label, data=token)])

    return title, buttons

async def progress_hook(current, total, msg, prefix):
    percent = current * 100 / total
    bar = "▓" * int(percent / 10) + "░" * (10 - int(percent / 10))
    try:
        await msg.edit(f"{prefix}\n`{bar}` {percent:.1f}%")
    except:
        pass

async def download_media(url, height, output_path, progress_msg):
    format_str = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
    ydl_opts = {
        'format': format_str,
        'outtmpl': f"{output_path}.%(ext)s",
        'quiet': True,
        'noplaylist': True,
        'merge_output_format': 'mp4',
        'progress_hooks': [lambda d: asyncio.get_event_loop().create_task(
            progress_hook(d.get('downloaded_bytes', 0),
                          d.get('total_bytes', 1),
                          progress_msg,
                          "⬇️ Downloading" if d['status'] == 'downloading' else "✅ Done"))
        ]
    }
    if has_cookies():
        ydl_opts['cookiefile'] = COOKIES_FILE
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    for file in Path(output_path).parent.iterdir():
        if file.stem == output_path.name:
            return file
    raise FileNotFoundError("Download failed")

async def download_audio_only(url, fmt_id, output_path, progress_msg):
    ydl_opts = {
        'format': fmt_id,
        'outtmpl': f"{output_path}.%(ext)s",
        'quiet': True,
        'noplaylist': True,
        'progress_hooks': [lambda d: asyncio.get_event_loop().create_task(
            progress_hook(d.get('downloaded_bytes', 0),
                          d.get('total_bytes', 1),
                          progress_msg,
                          "⬇️ Downloading" if d['status'] == 'downloading' else "✅ Done"))
        ]
    }
    if has_cookies():
        ydl_opts['cookiefile'] = COOKIES_FILE
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    for file in Path(output_path).parent.iterdir():
        if file.stem == output_path.name:
            return file
    raise FileNotFoundError("Audio download failed")

async def send_with_progress(event, file_path, caption):
    async def upload_progress(sent, total):
        percent = sent * 100 / total
        bar = "▓" * int(percent / 10) + "░" * (10 - int(percent / 10))
        try:
            await event.edit(f"📤 Uploading...\n`{bar}` {percent:.1f}%")
        except:
            pass
    await client.send_file(event.chat_id, file_path, caption=caption, progress_callback=upload_progress)

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.reply("👋 *Video Downloader Userbot*\nSend a YouTube URL to download.", parse_mode='md')

@client.on(events.NewMessage(pattern=r'^https?://'))
async def url_handler(event):
    url = event.text.strip()
    msg = await event.reply("🔍 Scanning formats...")
    try:
        title, buttons = await asyncio.get_event_loop().run_in_executor(None, extract_formats, url)
    except Exception as e:
        await msg.edit(f"❌ Error:\n`{str(e)}`")
        return
    if not buttons:
        await msg.edit("❌ No downloadable formats found.")
        return
    await msg.edit(f"🎬 *{title}*\nSelect a resolution or audio:", buttons=buttons, parse_mode='md')

@client.on(events.CallbackQuery)
async def callback_handler(event):
    await event.answer()
    token = event.data.decode()
    if token not in SESSION_STORE:
        await event.edit("⚠️ Invalid or expired selection.")
        return
    data = SESSION_STORE.pop(token)
    url = data["url"]
    title = data["title"]
    temp_dir = Path(tempfile.mkdtemp())
    output_path = temp_dir / title
    progress_msg = await event.edit("📥 Starting download...")

    try:
        if data["type"] == "audio":
            file_path = await download_audio_only(url, data["fmt_id"], output_path, progress_msg)
            caption = f"🎧 *{title}*\n- Bitrate: `{data.get('abr')} kbps`\n- Duration: `{format_duration(data.get('duration'))}`\n- Size: `{format_size(data.get('filesize'))}`"
        else:
            file_path = await download_media(url, data["height"], output_path, progress_msg)
            if file_path.stat().st_size > MAX_FILE_SIZE:
                await event.edit("⚠️ File exceeds 2GB limit. Try a lower resolution.")
                return
            caption = f"✅ *{title}*\nHere is your file 🎬"

        await send_with_progress(event, file_path, caption)
    except Exception as e:
        await event.edit(f"❌ Failed:\n`{str(e)}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await event.delete()

async def main():
    print("🚀 Bot is running...")
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            print("Bot crashed, retrying in 5s...")
            import time; time.sleep(5)