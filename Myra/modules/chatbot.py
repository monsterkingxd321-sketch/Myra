import asyncio
import re

from Abg.chat_status import adminsOnly

from pyrogram import Client, filters
from pyrogram.enums import MessageEntityType
from pyrogram.types import InlineKeyboardMarkup, Message

from Myra import Myra, mongo, config, LOGGER
from Myra.database import MYRA, training
from Myra.database.scope import scope_match
from Myra.ai import cleaner
from Myra.ai.audience import is_real_user
from Myra.ai.brain import get_ai_reply
from Myra.ai.humanizer import respond_like_human
from Myra.modules.helpers import CHATBOT_ON


# Automatically create the indexes needed for similarity search on startup.
async def create_indexes():
    await training.ensure_indexes()


try:
    loop = asyncio.get_running_loop()
    loop.create_task(create_indexes())
except RuntimeError:
    pass


def clean_word(word: str) -> str:
    return cleaner.normalize(word) or (word or "").strip().lower()


async def _learn_from_reply(client: Client, message: Message):
    """Learn a human reply → trigger pair, cleaned before it is stored."""
    if not (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id != client.id
    ):
        return

    replied_msg = message.reply_to_message
    if not replied_msg.text:
        return

    trigger = clean_word(replied_msg.text)
    if not trigger:
        return

    if message.sticker:
        await training.save_sticker_reply(
            trigger, message.sticker.file_id, message.sticker.file_unique_id
        )
    elif message.text:
        clean_reply = cleaner.clean_text(message.text)
        if clean_reply:
            await training.save_reply(trigger, clean_reply, source="learned")


async def _send_response(client: Client, message: Message, response: dict, chance: float):
    """Send the reply with human-like pacing (typing delay, reply chance)."""
    return await respond_like_human(
        client,
        message,
        response,
        chance=chance,
        wpm=config.HUMAN_TYPING_WPM,
        allow_split=config.HUMAN_SPLIT_REPLIES,
        logger=LOGGER,
    )


def _is_bot_message(message: Message, me) -> bool:
    """True when the message replies to one of the bot's own messages."""
    replied = message.reply_to_message
    if replied is None:
        return False
    sender = getattr(replied, "from_user", None)
    return bool((sender and sender.id == me.id) or getattr(replied, "outgoing", False))


def _group_query(client: Client, message: Message) -> tuple[bool, str]:
    """
    Work out whether a group message addresses the bot, and return the text with
    the @mention removed.

    The bot answers when it is @mentioned, replied to, or its username appears
    anywhere in the message — and the mention is stripped so the database search
    sees "hello bhai" instead of "@mybot hello bhai".
    """
    raw = message.text or message.caption or ""
    me = getattr(client, "me", None)
    if me is None:
        return False, raw.strip()

    addressed = _is_bot_message(message, me)
    username = (me.username or "").lower()
    spans = []

    for entity in message.entities or message.caption_entities or []:
        if entity.type == MessageEntityType.MENTION:
            word = raw[entity.offset : entity.offset + entity.length].lstrip("@").lower()
            if username and word == username:
                addressed = True
                spans.append((entity.offset, entity.offset + entity.length))
        elif entity.type == MessageEntityType.TEXT_MENTION:
            if entity.user and entity.user.id == me.id:
                addressed = True
                spans.append((entity.offset, entity.offset + entity.length))

    if not addressed and username and f"@{username}" in raw.lower():
        addressed = True
        for match in re.finditer(rf"@{re.escape(username)}\b", raw, re.IGNORECASE):
            spans.append(match.span())

    if not spans:
        return addressed, raw.strip()

    chars = list(raw)
    for start, end in sorted(spans, key=lambda span: span[0], reverse=True):
        del chars[start:end]
    return addressed, "".join(chars).strip()


@Myra.on_cmd("chatbot", group_only=True)
@adminsOnly("can_delete_messages")
async def chaton_(_, m: Message):
    await m.reply_text(
        f"Chat: {m.chat.title}\nChoose an option to enable or disable the chatbot.",
        reply_markup=InlineKeyboardMarkup(CHATBOT_ON),
    )


@Myra.on_message(
    (filters.text | filters.sticker) & ~filters.private & ~filters.bot,
    group=4,
)
async def chatbot_group(client: Client, message: Message):
    raw_text = message.text or message.caption or ""

    # Ignore commands / noise, but NOT an @mention of the bot itself.
    if raw_text.startswith(("!", "/", "?", "#")):
        return

    if mongo is None or MYRA is None:
        return

    # Chatbot disabled in this group? (per-bot setting)
    if await MYRA.find_one({"chat_id": message.chat.id, **scope_match()}):
        return

    # ── ONLY REAL USERS ────────────────────────────────────────────────────
    # Ignore every bot account (including this bot) and any of our own
    # userbot accounts, so the fleet never chats with itself.
    if not await is_real_user(message, my_id=client.me.id if client.me else None):
        return

    try:
        await _learn_from_reply(client, message)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"learn(group) failed: {exc}")

    # Addressed = @mention, reply-to-bot, or its username anywhere in the text.
    addressed, query_text = _group_query(client, message)

    # A message that @-mentions somebody else is not for us.
    if raw_text.lstrip().startswith("@") and not addressed:
        return

    if not query_text and not message.sticker:
        return

    # Hybrid brain: MongoDB first, Gemini fallback.
    # Works on plain messages too — no mention or reply needed.
    #
    # Gemini is only used for messages that actually address the bot (or when
    # AI_GROUP_FALLBACK_PLAIN is enabled), otherwise every "hy" in every group
    # would spend an API request.
    allow_ai = config.AI_GROUP_FALLBACK if addressed else config.AI_GROUP_FALLBACK_PLAIN

    response = await get_ai_reply(
        query_text or None,
        sticker_id=message.sticker.file_unique_id if message.sticker else None,
        allow_group_ai=allow_ai,
    )

    # A direct mention / reply is always answered, plain messages follow the chance.
    chance = 100.0 if addressed else config.AI_GROUP_REPLY_CHANCE
    await _send_response(client, message, response, chance)


@Myra.on_message(
    (filters.text | filters.sticker) & filters.private & ~filters.bot,
    group=4,
)
async def chatbot_private(client: Client, message: Message):
    raw_text = message.text or message.caption or ""

    # Ignore commands / noise, but NOT an @mention of the bot itself.
    if raw_text.startswith(("!", "/", "?", "#")):
        return

    if mongo is None:
        return

    # ── ONLY REAL USERS ────────────────────────────────────────────────────
    # A DM from a bot or from one of our own userbot accounts is ignored.
    if not await is_real_user(message, my_id=client.me.id if client.me else None):
        return

    # Strip our own @username so "@mybot hello" is searched as "hello".
    _, query_text = _group_query(client, message)

    # DM chat: MongoDB first, Gemini fallback, human-like pacing.
    # A DM is directed at the bot by definition, so it is always answered.
    response = await get_ai_reply(
        query_text or None,
        sticker_id=message.sticker.file_unique_id if message.sticker else None,
        allow_ai=config.AI_PRIVATE_FALLBACK,
    )
    await _send_response(client, message, response, config.AI_PRIVATE_REPLY_CHANCE)
