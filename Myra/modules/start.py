import time
from pyrogram import filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message

from Myra import LOGGER, Myra
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

_welcomed_chats: dict[int, float] = {}


async def _send_welcome_greeting(client, chat_id: int, chat_title: str):
    """Send clean thank you and mode setup button when added to a group."""
    now = time.time()
    if chat_id in _welcomed_chats and (now - _welcomed_chats[chat_id] < 20):
        return
    _welcomed_chats[chat_id] = now

    await add_served_chat(chat_id)
    text = (
        f"Thanks for adding me to **{chat_title}**.\n\n"
        "Group Admins can send /mode to configure reply mode."
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Mode Settings (/mode)", callback_data=f"gmode:{chat_id}:open_from_welcome")],
    ])
    try:
        await client.send_message(
            chat_id,
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        LOGGER.warning(f"Could not send group welcome greeting to {chat_id}: {exc}")


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


@Myra.on_chat_member_updated()
async def on_bot_added(client: Myra, u: ChatMemberUpdated):
    """Triggered when bot is added to a supergroup, channel, or forum."""
    if not u.new_chat_member or not u.new_chat_member.user:
        return
    me = client.me or await client.get_me()
    if u.new_chat_member.user.id != me.id:
        return

    old_status = u.old_chat_member.status if u.old_chat_member else None
    new_status = u.new_chat_member.status

    if new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR) and old_status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        chat_title = u.chat.title or "this group"
        await _send_welcome_greeting(client, u.chat.id, chat_title)


@Myra.on_message(filters.new_chat_members)
async def welcome(client: Myra, m: Message):
    """Triggered when bot is added via standard service message."""
    me = client.me or await client.get_me()
    if not m.new_chat_members:
        return
    for member in m.new_chat_members:
        if member.id == me.id:
            chat_title = m.chat.title or "this group"
            await _send_welcome_greeting(client, m.chat.id, chat_title)
            break


__all__ = ["start", "help", "repo", "welcome", "on_bot_added"]
