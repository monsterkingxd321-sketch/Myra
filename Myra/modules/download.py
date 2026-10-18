import os
import aiohttp
import tempfile
import asyncio
import uuid
import shutil

from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from Myra import Myra

API = "https://last-warning.serv00.net/md.php?url={}"

# In-memory cache for YouTube stream details
YT_CACHE = {}


def is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def get_best_audio_stream(medias):
    audio_streams = [m for m in medias if m.get("type") == "audio"]
    if not audio_streams:
        return None
    return audio_streams[0]


async def download_file(url, path):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            if r.status != 200:
                raise RuntimeError(f"Failed to download, status: {r.status}")
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(10240):
                    f.write(chunk)


@Myra.on_cmd("download")
async def download(_, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "❌ Usage:\n`Myra download <instagram | pinterest | youtube link>`"
        )

    url = message.text.split(None, 1)[1].strip()

    if is_youtube(url):
        wait = await message.reply_text("⏳ Fetching YouTube video details...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(API.format(url), timeout=30) as r:
                    data = await r.json()

            if data.get("statusCode") != 200:
                return await wait.edit("❌ API error.\nContact @candy_caugh")

            medias = data.get("medias", [])
            if not medias:
                return await wait.edit("❌ Media not found...")

            title = data.get("title", "YouTube Video")
            video_id = str(uuid.uuid4())[:8]
            YT_CACHE[video_id] = {
                "url": url,
                "title": title,
                "medias": medias
            }

            # Check available streams
            has_360 = any("360p" in m.get("quality", "") for m in medias)
            has_720 = any("720p" in m.get("quality", "") for m in medias)
            has_1080 = any("1080p" in m.get("quality", "") for m in medias)
            has_audio = any(m.get("type") == "audio" for m in medias)

            buttons = []
            row1 = []
            if has_360:
                row1.append(InlineKeyboardButton("🎬 360p", callback_data=f"yt_dl|{video_id}|360p"))
            if has_720:
                row1.append(InlineKeyboardButton("🎬 720p", callback_data=f"yt_dl|{video_id}|720p"))
            if has_1080:
                row1.append(InlineKeyboardButton("🎬 1080p", callback_data=f"yt_dl|{video_id}|1080p"))

            row2 = []
            if has_audio:
                row2.append(InlineKeyboardButton("🎵 MP3 / Audio", callback_data=f"yt_dl|{video_id}|mp3"))

            if row1:
                buttons.append(row1)
            if row2:
                buttons.append(row2)
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="CLOSE")])

            await wait.edit(
                text=f"🎥 **Title:** {title}\n\nSelect your download option below:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )

        except Exception as e:
            await wait.edit(f"❌ Error:\n`{e}`")
        return

    # Non-YouTube direct downloader logic
    wait = await message.reply_text(
        "⏳ đøωηℓσαđιηg ყσυя яєqυєѕт βαву… ρℓєαѕє ωαιт"
    )
    tmp_path = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API.format(url), timeout=30) as r:
                data = await r.json()

        if data.get("statusCode") != 200:
            return await wait.edit("❌ API error.\nContact @candy_caugh")

        medias = data.get("medias", [])
        if not medias:
            return await wait.edit("❌ Media not found...")

        media = medias[0]
        media_url = media["url"]
        media_type = media.get("type")
        title = data.get("title", "")

        suffix = ".mp4" if media_type == "video" else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name

        await download_file(media_url, tmp_path)

        if media_type == "video":
            await message.reply_video(video=tmp_path, caption=title)
        else:
            await message.reply_photo(photo=tmp_path, caption=title)

        await wait.delete()

    except Exception as e:
        try:
            await wait.edit(f"❌ Error:\n`{e}`")
        except Exception:
            await message.reply_text(f"❌ Error:\n`{e}`")

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@Myra.on_callback_query(filters.regex(r"^yt_dl\|"))
async def yt_callback(client: Myra, query: CallbackQuery):
    data_parts = query.data.split("|")
    if len(data_parts) < 3:
        return await query.answer("Invalid data", show_alert=True)

    video_id = data_parts[1]
    quality = data_parts[2]

    if video_id not in YT_CACHE:
        return await query.answer("This request has expired. Please try again.", show_alert=True)

    cache_data = YT_CACHE[video_id]
    title = cache_data["title"]
    medias = cache_data["medias"]

    selected_media = None
    audio_media = None

    if quality == "mp3":
        audio_streams = [m for m in medias if m.get("type") == "audio"]
        if audio_streams:
            selected_media = audio_streams[0]
    else:
        video_streams = [m for m in medias if m.get("type") == "video" and quality in m.get("quality", "")]
        if video_streams:
            selected_media = video_streams[0]
            if quality in ["720p", "1080p"] and not selected_media.get("is_audio"):
                audio_media = get_best_audio_stream(medias)

    if not selected_media:
        return await query.answer(f"Selected format ({quality}) not found.", show_alert=True)

    await query.answer("Starting download...", show_alert=False)
    await query.message.edit_text(f"⏳ Downloading `{title}` in `{quality}`...")

    tmp_video_path = None
    tmp_audio_path = None
    tmp_merged_path = None

    try:
        has_ffmpeg = shutil.which("ffmpeg") is not None

        if audio_media and not has_ffmpeg:
            await query.message.edit_text("⚠️ ffmpeg is missing on the server. Falling back to 360p to ensure video has audio...")
            fallback_streams = [m for m in medias if m.get("type") == "video" and "360p" in m.get("quality", "")]
            if fallback_streams:
                selected_media = fallback_streams[0]
                audio_media = None
            else:
                audio_media = None

        ext = selected_media.get("extension") or selected_media.get("ext") or ("mp3" if quality == "mp3" else "mp4")
        suffix = f".{ext}"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_video_path = tmp.name

        await download_file(selected_media["url"], tmp_video_path)

        if audio_media:
            await query.message.edit_text("⏳ Downloading audio track to merge...")
            audio_ext = audio_media.get("extension") or audio_media.get("ext") or "m4a"
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{audio_ext}") as tmp:
                tmp_audio_path = tmp.name
            await download_file(audio_media["url"], tmp_audio_path)

            await query.message.edit_text("⏳ Merging video and audio track...")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp_merged_path = tmp.name

            cmd = [
                "ffmpeg", "-y",
                "-i", tmp_video_path,
                "-i", tmp_audio_path,
                "-c:v", "copy",
                "-c:a", "aac",
                "-map", "0:v:0",
                "-map", "1:a:0",
                tmp_merged_path
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()

            if process.returncode != 0:
                if os.path.exists(tmp_merged_path):
                    os.remove(tmp_merged_path)
                tmp_merged_path = None

        await query.message.edit_text("⏳ Uploading to Telegram...")
        upload_path = tmp_merged_path if tmp_merged_path else tmp_video_path

        if quality == "mp3":
            await query.message.reply_audio(
                audio=upload_path,
                caption=title,
                title=title
            )
        else:
            await query.message.reply_video(
                video=upload_path,
                caption=title
            )

        await query.message.delete()

    except Exception as e:
        try:
            await query.message.edit_text(f"❌ Error:\n`{e}`")
        except Exception:
            await query.message.reply_text(f"❌ Error:\n`{e}`")

    finally:
        for path in [tmp_video_path, tmp_audio_path, tmp_merged_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
