"""
Broadcast commands.

``/broadcast`` (or ``/bc``) opens the full inline panel (see
``Myra/modules/admin_panel.py``) — choose sender (current bot / all cloned bots /
all bots / all userbots / one userbot) and target (groups only / DMs only / both).

``/br`` is the quick version: reply to any message and it is broadcast from the
**current bot** (main bot or clone) to its groups and DMs.

``/cbr`` (or ``/clonesbroadcast``) broadcasts across **all cloned bots** simultaneously.

``/allbr`` (or ``/allbroadcast``) broadcasts across **main bot + all cloned bots** simultaneously.

``/an`` forwards a post to all groups and users.
"""

import asyncio
import traceback

from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    UserIsBlocked,
)
from pyrogram.types import Message

from Myra import Myra, config
from Myra.database import get_chats, get_users
from Myra.database.admins import is_admin
from Myra.database.scope import clone_scope, global_scope
from Myra.services.broadcast import content_from_message, describe, run_broadcast


async def send_msg(user_id, message):
    try:
        await message.copy(chat_id=user_id)
        return 200, None
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await send_msg(user_id, message)
    except InputUserDeactivated:
        return 400, f"{user_id} : deactivated\n"
    except UserIsBlocked:
        return 400, f"{user_id} : blocked the bot\n"
    except PeerIdInvalid:
        return 400, f"{user_id} : user id invalid\n"
    except Exception:
        return 500, f"{user_id} : {traceback.format_exc()}\n"


@Myra.on_cmd(["br", "gcast"])
@global_scope
async def broadcast(client: Client, message: Message):
    """Quick broadcast from this bot to all groups + all DM users."""
    if not (await is_admin(message.from_user.id)):
        return await message.reply_text("😏 **ʏσυ ¢αη'τ υѕє τнιѕ ¢σммαη∂**")

    if not message.reply_to_message:
        return await message.reply_text(
            "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ɪᴛ.\n\n"
            "ℹ️ **More options:**\n"
            "• `/cbr` → broadcast to all cloned bots\n"
            "• `/allbr` → broadcast to main bot + all cloned bots\n"
            "• `/broadcast` → full interactive control panel"
        )

    scope = None
    if getattr(client, "id", None) and client.id != config.BOT_ID:
        scope = clone_scope(client.id)

    exmsg = await message.reply_text("📢 sᴛᴀʀᴛᴇᴅ ʙʀᴏᴀᴅᴄᴀsᴛɪɴɢ!")

    async def progress(stats):
        try:
            await exmsg.edit_text(describe("bot", "both", stats))
        except Exception:  # noqa: BLE001
            pass

    stats = await run_broadcast(
        "bot", content_from_message(message.reply_to_message), "both", progress=progress, scope=scope
    )
    await exmsg.edit_text(describe("bot", "both", stats))


@Myra.on_cmd(["cbr", "clonesbroadcast", "cbg"])
@global_scope
async def broadcast_clones(client: Client, message: Message):
    """Quick broadcast from all cloned bots to their served groups and/or DM users."""
    if not (await is_admin(message.from_user.id)):
        return await message.reply_text("😏 **ʏσυ ¢αη'τ υѕє τнιѕ ¢σммαη∂**")

    if not message.reply_to_message:
        return await message.reply_text(
            "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ɪᴛ ᴛᴏ ᴀʟʟ ᴄʟᴏɴᴇᴅ ʙᴏᴛs.\n\n"
            "Usage: `/cbr` (reply to message) or `/cbr groups` / `/cbr dm` / `/cbr both`\n"
            "ℹ️ Full control panel → `/broadcast`"
        )

    target = "both"
    args = (message.text or "").split()[1:] if message.text else []
    if args and args[0].lower() in ("groups", "group", "grp", "g"):
        target = "groups"
    elif args and args[0].lower() in ("dm", "dms", "private", "user", "users", "u"):
        target = "dm"

    exmsg = await message.reply_text("📢 sᴛᴀʀᴛɪɴɢ ʙʀᴏᴀᴅᴄᴀsᴛ ᴛᴏ ᴀʟʟ ᴄʟᴏɴᴇᴅ ʙᴏᴛs...")

    async def progress(stats):
        try:
            await exmsg.edit_text(describe("all_clones", target, stats))
        except Exception:  # noqa: BLE001
            pass

    stats = await run_broadcast(
        "all_clones", content_from_message(message.reply_to_message), target, progress=progress
    )
    await exmsg.edit_text(describe("all_clones", target, stats))


@Myra.on_cmd(["allbr", "allbroadcast"])
@global_scope
async def broadcast_all_bots(client: Client, message: Message):
    """Quick broadcast from Main bot + ALL cloned bots to their groups and/or DM users."""
    if not (await is_admin(message.from_user.id)):
        return await message.reply_text("😏 **ʏσυ ¢αη'τ υѕє τнιѕ ¢σммαη∂**")

    if not message.reply_to_message:
        return await message.reply_text(
            "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ɪᴛ ᴛᴏ ᴍᴀɪɴ ʙᴏᴛ + ᴀʟʟ ᴄʟᴏɴᴇᴅ ʙᴏᴛs.\n\n"
            "Usage: `/allbr` (reply to message) or `/allbr groups` / `/allbr dm` / `/allbr both`\n"
            "ℹ️ Full control panel → `/broadcast`"
        )

    target = "both"
    args = (message.text or "").split()[1:] if message.text else []
    if args and args[0].lower() in ("groups", "group", "grp", "g"):
        target = "groups"
    elif args and args[0].lower() in ("dm", "dms", "private", "user", "users", "u"):
        target = "dm"

    exmsg = await message.reply_text("📢 sᴛᴀʀᴛɪɴɢ ʙʀᴏᴀᴅᴄᴀsᴛ ᴛᴏ ᴀʟʟ ʙᴏᴛs (ᴍᴀɪɴ + ᴄʟᴏɴᴇs)...")

    async def progress(stats):
        try:
            await exmsg.edit_text(describe("all_bots", target, stats))
        except Exception:  # noqa: BLE001
            pass

    stats = await run_broadcast(
        "all_bots", content_from_message(message.reply_to_message), target, progress=progress
    )
    await exmsg.edit_text(describe("all_bots", target, stats))


@Myra.on_cmd("an")
@global_scope
async def announced(client: Client, message: Message):
    """Forward a post to every group and every user."""
    if not (await is_admin(message.from_user.id)):
        return await message.reply_text("😏 **ʏσυ ¢αη'τ υѕє τнιѕ ¢σммαη∂**")

    if not message.reply_to_message:
        return await message.reply_text("Reply To Some Post To Broadcast")

    to_send = message.reply_to_message.id
    chats = await get_chats()
    users = await get_users()

    failed = 0
    for chat in chats:
        try:
            await client.forward_messages(
                chat_id=int(chat),
                from_chat_id=message.chat.id,
                message_ids=to_send,
            )
            await asyncio.sleep(1)
        except Exception:  # noqa: BLE001
            failed += 1

    failed_user = 0
    for user in users:
        try:
            await client.forward_messages(
                chat_id=int(user),
                from_chat_id=message.chat.id,
                message_ids=to_send,
            )
            await asyncio.sleep(1)
        except Exception:  # noqa: BLE001
            failed_user += 1

    await message.reply_text(
        "Bʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴍᴘʟᴇᴛᴇ. {} ɢʀᴏᴜᴘs ғᴀɪʟᴇᴅ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴛʜᴇ ᴍᴇssᴀɢᴇ, "
        "ᴘʀᴏʙᴀʙʟʏ ᴅᴜᴇ ᴛᴏ ʙᴇɪɴɢ ᴋɪᴄᴋᴇᴅ. {} ᴜsᴇʀs ғᴀɪʟᴇᴅ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴛʜᴇ ᴍᴇssᴀɢᴇ, "
        "ᴘʀᴏʙᴀʙʟʏ ᴅᴜᴇ ᴛᴏ ʙᴇɪɴɢ ʙᴀɴɴᴇᴅ.".format(failed, failed_user)
    )
