"""
Host your own clone of this bot.

    /clone <bot_token>     → host your own copy of this bot
    /mybots                → the bots you cloned, with live status
    /clonehelp             → how to get a bot token from @BotFather
    /delbot <id|@username> → stop and forget one of your clones

Anyone may clone. The clone replies in groups and DMs on its own, inside the
same process as the main bot, so it is cheap and it shares the trained Hinglish
database and the AI keys.

**Cloning gives you reach, not control.** The ``/admin`` control panel is
global: only the main bot owner and the admins the owner added can open it, and
it shows the same shared data in every bot. Whoever cloned the bot does not get
a panel from it. See :mod:`Myra.database.admins` and :mod:`Myra.database.scope`.
"""

import re
import time

from pyrogram import filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from Myra import Myra, LOGGER, config
from Myra.database import bots as botdb
from Myra.services.clones import clone_manager

CLONE_GUIDE = (
    "Host your own bot\n"
    "--------------------\n\n"
    "Step 1 - Open @BotFather in Telegram.\n"
    "Step 2 - Send /newbot and pick a name and a username.\n"
    "Step 3 - Copy the token BotFather gives you.\n"
    "Step 4 - Send it here:\n"
    "/clone 123456789:AAH...\n\n"
    "Your bot goes live immediately.\n"
    "It answers in groups and DMs on its own.\n"
    "Userbots, broadcast and autochat all work on it.\n"
    "The trained database and the AI keys are shared.\n\n"
    "One token can be added only once."
)


def _allowed(user_id: int) -> bool:
    allowed = str(getattr(config, "CLONE_ALLOWED_USERS", "") or "").strip()
    if not allowed:
        return True
    try:
        return int(user_id) in {int(x) for x in allowed.replace(",", " ").split()}
    except Exception:  # noqa: BLE001
        return True


def _back_close_rows() -> list:
    """``[Back] [Close]`` — the way out of the clone sub-screens."""
    return [
        [
            InlineKeyboardButton("Back", callback_data="CLONE_MENU"),
            InlineKeyboardButton("Close", callback_data="CLOSE"),
        ],
    ]


def _guide_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Open @BotFather", url="https://t.me/BotFather")]]
        + _back_close_rows()
    )


# ── shared with the /start card's "Clone your bot" menu ─────────────────────
def guide_markup() -> InlineKeyboardMarkup:
    """Public alias used by the inline clone menu."""
    return _guide_markup()


async def mybots_text(user_id: int) -> tuple:
    """Body of the ``/mybots`` reply — shared with the inline clone menu.

    Returns ``(text, markup)``.
    """
    docs = await botdb.find_bot_by_owner(user_id)
    if not docs:
        return "You have not hosted any bot yet.\n\n" + CLONE_GUIDE, _guide_markup()

    lines = ["Your bots", "--------------------", ""]
    for doc in docs:
        state = "running" if clone_manager.is_running(doc.get("scope")) else "stopped"
        lines.append(f"@{doc.get('username') or doc.get('bot_id')} - id {doc.get('bot_id')} ({state})")
    lines += [
        "",
        "Send /delbot <bot_id> to remove one.",
    ]
    return "\n".join(lines), InlineKeyboardMarkup(_back_close_rows())


async def safe_reply(m: Message, text: str, **kwargs):
    """``reply_text`` that never surfaces an entity-bounds crash to the user.

    Pyrogram builds the message entities locally before sending over MTProto;
    if that ever produces invalid bounds the send is retried once with all
    parsing turned off, so the user gets the text instead of an error.
    """
    try:
        return await m.reply_text(text, **kwargs)
    except RPCError as exc:
        if "ENTITY" not in str(exc).upper():
            raise
        kwargs["parse_mode"] = ParseMode.DISABLED
        return await m.reply_text(text, **kwargs)


# ── token secrecy ──────────────────────────────────────────────────────────
# A bot token is a password. Whoever pastes one gets that message deleted at
# once. The user is remembered briefly so ``/clone`` followed by a bare token
# works too.
_PENDING: dict = {}
PENDING_TTL = 600  # seconds a bare token is accepted after ``/clone``
TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_\-]{30,}")


def _awaiting_token(user_id: int) -> bool:
    return _PENDING.get(user_id, 0) > time.time()


def _bare_token(text: str) -> bool:
    return bool(TOKEN_RE.fullmatch((text or "").strip()))


async def _delete_token_message(m: Message) -> bool:
    """Best-effort delete of the message that carried the token."""
    try:
        await m.delete()
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"token message delete failed: {exc}")
        return False


async def _send(m: Message, text: str, **kwargs):
    """Send in the same chat — safe even when ``m`` itself was just deleted."""
    client = m._client
    try:
        return await client.send_message(m.chat.id, text, **kwargs)
    except RPCError as exc:
        if "ENTITY" not in str(exc).upper():
            LOGGER.debug(f"clone reply failed: {exc}")
            return None
        # Pyrogram built bad message entities locally; retry with parsing off.
        try:
            kwargs["parse_mode"] = ParseMode.DISABLED
            return await client.send_message(m.chat.id, text, **kwargs)
        except Exception as retry_exc:  # noqa: BLE001
            LOGGER.debug(f"clone reply retry failed: {retry_exc}")
            return None
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"clone reply failed: {exc}")
        return None


async def _do_clone(m: Message, token: str, owner_id: int, token_deleted: bool = True):
    """Validate the token and host it. Every reply is a fresh message."""
    note = "" if token_deleted else (
        "\n\nNote: the message with the token could not be deleted. "
        "Please delete it manually."
    )
    wait = await _send(m, "Hosting your bot. Please wait a moment." + note)
    ok, message, doc = await clone_manager.create_clone(token, owner_id)

    if not ok:
        text = f"{message}\n\n{CLONE_GUIDE}"
        if wait is not None:
            try:
                await wait.edit_text(text, reply_markup=_guide_markup())
                return
            except Exception:  # noqa: BLE001
                pass
        await _send(m, message)
        return

    username = (doc or {}).get("username") or ""
    link = f"https://t.me/{username}" if username else ""
    text = (
        "Your bot is live.\n"
        "--------------------\n\n"
        f"Bot: @{username or doc.get('bot_id')}\n"
        f"Owner: you ({m.from_user.id})\n\n"
        "What to do now:\n"
        "1 - Open your bot's private chat and send /start\n"
        "2 - Add it to your group\n"
        "3 - It answers in groups and DMs by itself\n\n"
        "The trained database and the AI keys are shared."
    )
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Open my bot", url=link or "https://t.me/BotFather")]]
        + _back_close_rows()
    )
    if wait is not None:
        try:
            await wait.edit_text(text, reply_markup=markup)
        except Exception:  # noqa: BLE001
            await _send(m, text, reply_markup=markup)
    else:
        await _send(m, text, reply_markup=markup)
    LOGGER.info(f"{m.from_user.id} cloned @{username}")


@Myra.on_cmd(["clone", "addbot"])
async def clone_cmd(_, m: Message):
    """Turn a bot token into your own hosted clone."""
    if m.chat.type != ChatType.PRIVATE:
        return await m.reply("Send /clone to me in a private chat.")

    parts = (m.text or "").split(maxsplit=1)
    token = parts[1].strip() if len(parts) > 1 else ""

    if not token:
        # Remember them briefly so the *next* message may be the token.
        _PENDING[m.from_user.id] = time.time() + PENDING_TTL
        return await _send(
            m,
            CLONE_GUIDE + "\n\nNow send your token. "
                          "The message containing it will be deleted immediately.",
            reply_markup=_guide_markup(),
        )

    # A token is a secret: delete the message that carries it first thing.
    deleted = await _delete_token_message(m)
    _PENDING.pop(m.from_user.id, None)

    if not _allowed(m.from_user.id):
        return await _send(m, "Cloning is not allowed for your account.")

    await _do_clone(m, token, m.from_user.id, deleted)


@Myra.on_cmd("clonehelp")
async def clone_help_cmd(_, m: Message):
    await safe_reply(m, CLONE_GUIDE, reply_markup=_guide_markup(),
                     disable_web_page_preview=True)


@Myra.on_cmd(["mybots", "myclones"])
async def my_bots_cmd(_, m: Message):
    """List the clones owned by this user."""
    text, markup = await mybots_text(m.from_user.id)
    await safe_reply(m, text, reply_markup=markup, disable_web_page_preview=True)


@Myra.on_cmd(["delbot", "delclone"])
async def del_bot_cmd(_, m: Message):
    """Stop and forget one of *your* clones."""
    parts = (m.text or "").split(maxsplit=1)
    target = parts[1].strip().lstrip("@") if len(parts) > 1 else ""
    if not target:
        return await m.reply("Usage: `/delbot <bot_id|@username>`")

    doc = None
    if target.isdigit():
        doc = await botdb.find_bot_by_id(int(target))
    if doc is None:
        docs = await botdb.list_bots()
        for entry in docs:
            if (entry.get("username") or "").lower() == target.lower():
                doc = entry
                break
    if doc is None:
        return await m.reply("No such bot was found.")

    owner = doc.get("owner_id")
    main_owner = int(config.OWNER_ID) if config.OWNER_ID else None
    if m.from_user.id not in (owner, main_owner):
        return await m.reply("This bot does not belong to you. Only its owner can remove it.")

    await clone_manager.delete_clone(doc.get("scope"))
    await m.reply(
        f"@{(doc.get('username') or doc.get('bot_id'))} has been stopped and removed.\n"
        "You can host it again with /clone."
    )


@Myra.on_message(filters.private & filters.text & ~filters.bot, group=-1)
async def token_guard(_, m: Message):
    """
    A bare bot token pasted in a private chat is deleted the moment it arrives.

    Runs *before* the other private handlers (``group=-1``): if the message is
    not a token it simply propagates. If the user had just used ``/clone`` the
    token is hosted right away; otherwise they get a short hint.
    """
    text = (m.text or "").strip()
    if not _bare_token(text):
        m.continue_propagation()
        return

    deleted = await _delete_token_message(m)

    if not _allowed(m.from_user.id):
        await _send(m, "Cloning is not allowed for your account.")
        m.stop_propagation()
        return

    if _awaiting_token(m.from_user.id):
        _PENDING.pop(m.from_user.id, None)
        await _do_clone(m, text, m.from_user.id, deleted)
        m.stop_propagation()
        return

    await _send(
        m,
        "A bot token was detected and the message was deleted.\n\n"
        "To host your own bot, send:\n"
        "/clone <your_token>",
    )
    m.stop_propagation()


__all__ = [
    "clone_cmd",
    "clone_help_cmd",
    "my_bots_cmd",
    "del_bot_cmd",
    "token_guard",
]
