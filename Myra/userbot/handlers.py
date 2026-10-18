import functools
import re
from pyrogram import Client, filters, enums
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from Myra.userbot.chatbot_engine import get_chatbot_response, clean_word
from Myra.ai import cleaner
from Myra.ai.humanizer import respond_like_human
from Myra.ai.audience import is_real_user, own_account_ids
from Myra.database import training, settings
from Myra.database.scope import current_scope, reset_scope, set_scope
from Myra.userbot.storage import accounts as ub_accounts
from Myra import mongo, config, LOGGER


async def _resolve_delay_ms(account: dict) -> int:
    """
    Reply delay in milliseconds.

    A per-account value wins; otherwise the global ``/speed`` value is used.
    """
    delay_ms = account.get("reply_delay")
    if delay_ms is None:
        delay_ms = await settings.get_reply_delay()
    try:
        return max(0, int(delay_ms))
    except Exception:
        return 0


async def _resolve_chance(account: dict, group: bool) -> float:
    """Reply probability (0-100): per-account wins, else the global value."""
    per_account = account.get("reply_chance")
    if per_account is not None:
        try:
            return max(0.0, min(100.0, float(per_account)))
        except Exception:
            pass
    if group:
        return await settings.get_group_chance()
    return await settings.get_private_chance()


def _group_mode(account: dict) -> str:
    """'all' = reply to normal messages too, 'mention' = only when addressed."""
    mode = account.get("group_mode")
    if mode in ("all", "mention"):
        return mode
    return "all" if config.USERBOT_GROUP_REPLY_ALL else "mention"


async def _learn_from_message(message: Message, my_id: int):
    """Learn a trigger → reply pair from a human-to-human reply (cleaned)."""
    if mongo is None or not message.reply_to_message:
        return
    replied = message.reply_to_message
    if not replied.from_user or replied.from_user.id == my_id or replied.outgoing:
        return
    # Never learn from a bot's message (the main bot, another userbot, ...).
    if replied.from_user.is_bot or replied.from_user.id in await own_account_ids():
        return
    if not replied.text:
        return

    trigger = clean_word(replied.text) or replied.text.strip().lower()
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


# NOTE: the old `_is_outgoing_or_self()` / `_is_other_connected_userbot()`
# helpers were replaced by `Myra.ai.audience.is_real_user()`, which also
# filters out every bot account (including the main bot), channel posts and
# anonymous admins. See the "ONLY REAL USERS" guard inside the handlers below.


def _check_group_mention_or_reply(message: Message, my_telegram_id: int, my_username: str) -> tuple[bool, str]:
    """
    Check if a group message mentions the userbot or is a direct reply to the userbot.
    Returns (should_respond: bool, cleaned_text: str).
    """
    text = message.text or message.caption or ""
    has_mention = False

    # 1. Check if direct reply to a message sent by this userbot
    if message.reply_to_message:
        replied = message.reply_to_message
        if replied.from_user and replied.from_user.id == my_telegram_id:
            has_mention = True
        elif replied.outgoing:
            has_mention = True

    # 2. Check message entities for mention or text_mention
    entities = message.entities or message.caption_entities or []
    mention_spans = []

    for entity in entities:
        if entity.type == enums.MessageEntityType.MENTION:
            start = entity.offset
            end = entity.offset + entity.length
            mention_word = text[start:end]
            if my_username and mention_word.lower() == f"@{my_username.lower()}":
                has_mention = True
                mention_spans.append((start, end))
        elif entity.type == enums.MessageEntityType.TEXT_MENTION:
            if entity.user and entity.user.id == my_telegram_id:
                has_mention = True
                mention_spans.append((entity.offset, entity.offset + entity.length))

    # 3. Fallback: check raw text for @username if username is set
    if not has_mention and my_username and f"@{my_username.lower()}" in text.lower():
        has_mention = True
        pattern = re.compile(rf"@{re.escape(my_username)}\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            mention_spans.append(match.span())

    if not has_mention:
        return False, ""

    # Remove mention spans from text
    if mention_spans:
        char_list = list(text)
        for start, end in sorted(mention_spans, key=lambda x: x[0], reverse=True):
            char_list[start:end] = []
        cleaned_text = "".join(char_list).strip()
    else:
        cleaned_text = text.strip()

    return True, cleaned_text


async def _reply_like_human(client: Client, message: Message, account: dict, response: dict, chance: float):
    """Send the reply with the userbot's configured speed + human pacing."""
    delay_ms = await _resolve_delay_ms(account)
    await respond_like_human(
        client,
        message,
        response,
        chance=chance,
        base_delay_ms=delay_ms,
        wpm=config.HUMAN_TYPING_WPM,
        allow_split=config.HUMAN_SPLIT_REPLIES,
        logger=LOGGER,
    )


async def register_userbot_handlers(client: Client, account_id: str):
    """
    Register incoming message handlers for a connected userbot client using direct MessageHandler.
    """

    def _within_scope(func):
        """
        Run a userbot handler with the scope its account belongs to.

        A userbot client is a *user* session, so nothing else sets the scope for
        it — without this the global defaults (/speed, reply chance, AutoChat)
        of the main bot would be used for a clone's userbot.
        """
        @functools.wraps(func)
        async def wrapper(c, message, *args, **kwargs):
            account = ub_accounts.get(account_id) or {}
            token = set_scope(account.get("scope") or current_scope())
            try:
                return await func(c, message, *args, **kwargs)
            finally:
                reset_scope(token)

        return wrapper

    async def group_autochat_handler(c: Client, message: Message):
        """
        Handle group messages.

        Mentions / replies are always answered. Normal messages ("hy", "hello")
        are answered too when the account's group mode is "all", like a real
        person taking part in the chat.
        """
        account = ub_accounts.get(account_id)
        if not account:
            return

        # AutoChat ON/OFF check
        if not account.get("autochat", True):
            return

        my_id = account["telegram_id"]
        my_username = account.get("username", "")

        # ── ONLY REAL USERS ────────────────────────────────────────────────
        # Ignore our own messages, every bot (including the main bot), our
        # other userbot accounts, channel posts and anonymous admins.
        if not await is_real_user(message, my_id=my_id):
            return

        # Skip commands
        raw_text = message.text or message.caption or ""
        if raw_text.startswith(("/", "!", "?", ".")):
            return

        # Learn from replies between other users (not the userbot itself)
        try:
            await _learn_from_message(message, my_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(f"learn(userbot) failed: {exc}")

        # Was the userbot addressed?
        addressed, cleaned_text = _check_group_mention_or_reply(message, my_id, my_username)

        if not addressed and _group_mode(account) != "all":
            return

        # When not addressed we reply using the whole message, otherwise the
        # mention is stripped from the text first.
        query_text = cleaned_text if addressed else raw_text
        sticker_id = message.sticker.file_unique_id if message.sticker else None
        if not query_text and not sticker_id:
            return

        # Gemini only for messages that address the userbot (unless the operator
        # opted in) so plain "hy" messages do not burn the API quota.
        allow_ai = config.AI_GROUP_FALLBACK if addressed else config.AI_GROUP_FALLBACK_PLAIN

        response = await get_chatbot_response(
            query_text,
            sticker_id=sticker_id,
            allow_ai=allow_ai,
        )
        if not response:
            return

        chance = 100.0 if addressed else await _resolve_chance(account, group=True)
        try:
            await _reply_like_human(c, message, account, response, chance)
        except Exception as e:
            LOGGER.error(f"⚠️ Userbot ({account_id}) group reply error: {e}")

    async def private_autochat_handler(c: Client, message: Message):
        """Handle private (DM) chats when AutoChat is ON — real chatting."""
        account = ub_accounts.get(account_id)
        if not account:
            return

        # AutoChat ON/OFF check
        if not account.get("autochat", True):
            return

        my_id = account["telegram_id"]

        # ── ONLY REAL USERS ────────────────────────────────────────────────
        # A DM from the main bot or from another of our userbots is ignored.
        if not await is_real_user(message, my_id=my_id):
            return

        # Skip commands
        raw_text = message.text or message.caption or ""
        if raw_text.startswith(("/", "!", "?", ".")):
            return

        sticker_id = message.sticker.file_unique_id if message.sticker else None
        if not raw_text and not sticker_id:
            return

        response = await get_chatbot_response(
            raw_text,
            sticker_id=sticker_id,
            allow_ai=config.AI_PRIVATE_FALLBACK,
        )
        if not response:
            return

        chance = await _resolve_chance(account, group=False)
        try:
            await _reply_like_human(c, message, account, response, chance)
        except Exception as e:
            LOGGER.error(f"⚠️ Userbot ({account_id}) private reply error: {e}")

    # Register handlers directly on client using MessageHandler
    group_filter = filters.group & (filters.text | filters.sticker)
    private_filter = filters.private & (filters.text | filters.sticker)

    client.add_handler(MessageHandler(_within_scope(group_autochat_handler), group_filter), group=1)
    client.add_handler(MessageHandler(_within_scope(private_autochat_handler), private_filter), group=2)
    LOGGER.info(f"✅ Handlers registered successfully for userbot {account_id}")
