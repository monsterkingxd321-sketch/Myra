from pyrogram import filters
from pyrogram.enums import ChatType
from pyrogram.types import InlineKeyboardMarkup, Message

from Myra import Myra
from Myra.database.chats import add_served_chat
from Myra.database.users import add_served_user
from Myra.modules.helpers import (
    CLOSE_BTN,
    HELP_BTN,
    build_group_buttons,
    build_start_buttons,
    group_start_text,
    help_text,
    source_text,
    start_text,
)


async def _send_menu(
    m: Message,
    caption: str,
    buttons: list,
    keyboard: bool = True,
):
    """Send the menu as plain text — no photo, no sticker, nothing else."""
    markup = InlineKeyboardMarkup(buttons) if keyboard else None
    return await m.reply_text(
        text=caption,
        reply_markup=markup,
        disable_web_page_preview=True,
    )


@Myra.on_cmd(["start", "aistart"])
async def start(_, m: Message):
    """A completely clean /start: the message text and the buttons, nothing else.

    No intro animation, no sticker, no follow-up greeting — just the card.
    """
    if m.chat.type == ChatType.PRIVATE:
        await add_served_user(m.from_user.id)
        await _send_menu(m, start_text(), build_start_buttons())
    else:
        await add_served_chat(m.chat.id)
        await _send_menu(m, group_start_text(), build_group_buttons())


@Myra.on_cmd("help")
async def help(client: Myra, m: Message):
    if m.chat.type == ChatType.PRIVATE:
        await add_served_user(m.from_user.id)
        await _send_menu(m, help_text(), HELP_BTN)
    else:
        await add_served_chat(m.chat.id)
        await _send_menu(
            m,
            "Message me in private for the full list of commands.",
            build_group_buttons(),
        )


@Myra.on_cmd("repo")
async def repo(_, m: Message):
    await m.reply_text(
        text=source_text(),
        reply_markup=InlineKeyboardMarkup(CLOSE_BTN),
        disable_web_page_preview=True,
    )


@Myra.on_message(filters.new_chat_members)
async def welcome(client: Myra, m: Message):
    me = await client.get_me()
    for member in m.new_chat_members:
        if member.id != me.id:
            continue
        await add_served_chat(m.chat.id)
        chat_title = m.chat.title or "this group"
        bot_name = me.first_name or "Myra"
        tag_handle = f"@{me.username}" if me.username else bot_name
        text = (
            f"🎉 **Thanks for adding me to {chat_title}!**\n\n"
            f"Hey everyone, I am **{bot_name}**! ✨\n\n"
            f"⚙️ Group Admins can send **/mode** to select my replying mode in this group.\n\n"
            f"*(By default, I only reply when tagged, mentioned ({tag_handle}), or replied to, keeping the group clean.)*"
        )
        markup = [
            [InlineKeyboardButton("⚙️ Configure Mode (/mode)", callback_data=f"gmode:{m.chat.id}:open_from_welcome")],
            [InlineKeyboardButton("❓ Help", callback_data="HELP")],
        ]
        await _send_menu(m, text, markup)


__all__ = ["start", "help", "repo", "welcome"]
