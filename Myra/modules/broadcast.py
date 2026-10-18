"""
Broadcast commands.

``/broadcast`` (or ``/bc``) opens the full inline panel (see
``Myra/modules/admin_panel.py``) — choose sender (main bot / all userbots /
one userbot) and target (groups only / DMs only / both).

``/br`` is the quick version: reply to any message and it is broadcast from the
**main bot** to every group **and** every DM user.

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

from Myra import Myra
from Myra.database import get_chats, get_users
from Myra.database.admins import is_admin
from Myra.database.scope import global_scope
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
async def broadcast(_, message: Message):
    """Quick broadcast from the main bot to all groups + all DM users."""
    if not (await is_admin(message.from_user.id)):
        return await message.reply_text("😏 **ʏσυ ¢αη'τ υѕє τнιѕ ¢σммαη∂**")

    if not message.reply_to_message:
        return await message.reply_text(
            "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ɪᴛ.\n\n"
            "ℹ️ More options (per-userbot / groups-only / DM-only) → `/broadcast`"
        )

    exmsg = await message.reply_text("📢 sᴛᴀʀᴛᴇᴅ ʙʀᴏᴀᴅᴄᴀsᴛɪɴɢ!")

    async def progress(stats):
        try:
            await exmsg.edit_text(describe("bot", "both", stats))
        except Exception:  # noqa: BLE001
            pass

    stats = await run_broadcast(
        "bot", content_from_message(message.reply_to_message), "both", progress=progress
    )
    await exmsg.edit_text(describe("bot", "both", stats))


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
