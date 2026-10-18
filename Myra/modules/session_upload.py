"""
Upload userbots as files: send a ``.session`` file (Pyrogram **or** Telethon) or
a ``.zip`` full of them in the bot's private chat and every account inside is
added automatically.

Registered at ``group=-1`` so it runs *before* the normal private-chat handlers,
and it always calls ``continue_propagation()`` for documents it does not handle.
The uploaded file is deleted immediately after processing (privacy) and the
temporary download directory is always removed.
"""

import os
import shutil
import tempfile

from pyrogram import filters
from pyrogram.enums import ChatType
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Myra import Myra, LOGGER
from Myra.database.admins import is_admin
from Myra.database.scope import global_scope
from Myra.userbot.importer import (
    add_from_session_file,
    import_zip,
    looks_like_session,
    looks_like_zip,
    safe_basename,
)


def _cancel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Open userbots", callback_data="ad_userbots")],
    ])


async def _progress_edit(message: Message, text: str):
    try:
        await message.edit_text(text)
    except Exception:  # noqa: BLE001
        pass


@Myra.on_message(
    filters.private & filters.document & ~filters.bot,
    group=-1,
)
@global_scope
async def session_upload_handler(client: Myra, m: Message):
    """Add userbots from an uploaded ``.session`` / ``.zip`` file."""
    document = m.document
    file_name = safe_basename(getattr(document, "file_name", "") or "")

    is_session = looks_like_session(file_name)
    is_zip = looks_like_zip(file_name)
    if not (is_session or is_zip):
        m.continue_propagation()
        return

    user_id = m.from_user.id if m.from_user else None

    LOGGER.info(
        f"📥 session upload from {user_id}: name={file_name!r} "
        f"mime={getattr(document, 'mime_type', None)!r} "
        f"size={getattr(document, 'file_size', None)}"
    )

    # Only the bot's owner / admins may add userbots — of *this* bot.
    if user_id is None or not await is_admin(user_id):
        await m.reply_text("❌ Aap is bot ke admin nahi ho.")
        m.stop_propagation()
        return

    if m.chat.type != ChatType.PRIVATE:
        m.continue_propagation()
        return

    status = await m.reply_text(
        f"📥 **File mil gayi:** `{file_name}`\n"
        f"{'🗜 ZIP → saare `.session` add karunga' if is_zip else '📄 Single session add karunga'}\n"
        "⏳ Download ho raha hai..."
    )

    tmpdir = tempfile.mkdtemp(prefix="ubsession_")
    try:
        try:
            local_path = await m.download(file_name=os.path.join(tmpdir, file_name or "file.session"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"session upload download failed: {exc}")
            await _progress_edit(status, f"❌ Download failed:\n`{exc}`")
            return

        if not local_path or not os.path.exists(local_path):
            await _progress_edit(status, "❌ File download nahi ho paayi.")
            return

        await _progress_edit(status, f"🔐 **Login kar raha hoon** — `{file_name}`\n⏳ Thoda time lag sakta hai...")

        if is_session:
            result = await add_from_session_file(local_path, user_id)
            if result.get("ok"):
                LOGGER.info(f"✅ userbot added from file {file_name}: {result.get('account_id')}")
                await _progress_edit(
                    status,
                    "✅ **Userbot add ho gaya!**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **{result.get('name')}**"
                    + (f" (@{result['username']})" if result.get("username") else "")
                    + f"\n🆔 `{result.get('telegram_id')}`\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "`/useraccounts` se manage karo.",
                )
            else:
                LOGGER.warning(
                    f"❌ userbot NOT added from file {file_name}: {result.get('reason')}"
                )
                await _progress_edit(
                    status,
                    "❌ **Add nahi ho paaya**\n"
                    f"Reason: `{result.get('reason', 'unknown')}`\n\n"
                    "ℹ️ Ensure karo ki session valid hai (logout nahi hua).",
                )
            return

        # ── zip ────────────────────────────────────────────────────────────
        total_files = 0

        async def progress(index: int, total: int, name: str):
            nonlocal total_files
            total_files = total
            await _progress_edit(
                status,
                f"🗜 **ZIP import:** `{index}/{total}`\n"
                f"⏳ Processing `{name}`...\n"
                "Har account ke beech ~1.5s ka gap (Telegram safety).",
            )

        report = await import_zip(local_path, user_id, progress=progress)

        lines = [
            "🗜 **ZIP IMPORT COMPLETE**",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"✅ **Added:** `{len(report['imported'])}`",
            f"⏭ **Skipped:** `{len(report['skipped'])}`",
            f"❌ **Failed:** `{len(report['failed'])}`",
        ]
        if report["imported"]:
            lines.append("")
            lines.append("**New userbots:**")
            for entry in report["imported"][:20]:
                handle = f" (@{entry['username']})" if entry.get("username") else ""
                lines.append(f"• {entry['name']}{handle}")
            if len(report["imported"]) > 20:
                lines.append(f"• … +{len(report['imported']) - 20} more")
        if report["failed"]:
            lines.append("")
            lines.append("**Failed:**")
            for entry in report["failed"][:10]:
                lines.append(f"• `{entry['name']}` → {entry['reason']}")
        if report["skipped"]:
            lines.append("")
            lines.append("**Skipped:**")
            for entry in report["skipped"][:10]:
                lines.append(f"• `{entry['name']}` → {entry['reason']}")
        if not report["imported"] and report.get("missing"):
            lines.append("")
            lines.append("ℹ️ ZIP ke andar koi `.session` file nahi mili.")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        try:
            await status.edit_text("\n".join(lines), reply_markup=_cancel_markup())
        except Exception:  # noqa: BLE001
            await m.reply_text("\n".join(lines)[:4000], reply_markup=_cancel_markup())

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        # Never keep the uploaded file in the chat history.
        try:
            await m.delete()
        except Exception:  # noqa: BLE001
            pass

    m.stop_propagation()


__all__ = ["session_upload_handler"]
