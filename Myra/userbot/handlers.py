import asyncio
import functools
import re
import time
from pyrogram import Client, filters, enums
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from Myra.userbot.chatbot_engine import get_chatbot_response, clean_word
from Myra.ai import cleaner
from Myra.ai.humanizer import respond_like_human, should_reply
from Myra.ai.audience import is_real_user, own_account_ids
from Myra.database import training, settings
from Myra.database.scope import current_scope, reset_scope, set_scope
from Myra.userbot.storage import accounts as ub_accounts
from Myra import mongo, config, LOGGER

# Per-(account_id, chat_id) locks and cooldown tracking to prevent concurrency bursts & flood limits
_chat_locks: dict[tuple[str, int], asyncio.Lock] = {}
_last_reply_at: dict[tuple[str, int], float] = {}
_last_account_reply_at: dict[str, float] = {}
_last_react_at: dict[tuple[str, int], float] = {}
_last_account_react_at: dict[str, float] = {}
_chat_reactions_cache: dict[int, tuple[float, list[str]]] = {}

_POSITIVE_REACTIONS = [
    "❤️", "👍", "🔥", "🥰", "👏", "🤩", "🎉", "😁", "💯", "👌",
]
_SAFE_FALLBACK_REACTIONS = ("❤️", "👍", "🔥")
_NEGATIVE_REACTIONS = {
    "👎", "💔", "😡", "🤬", "💩", "🤮", "😢", "😭", "👿", "💀", "☠️", "🖕", "😤", "🤢", "🤡",
}


async def _get_available_positive_reactions(client: Client, chat_id: int) -> list[str]:
    """Return positive reaction emojis allowed in *chat_id* (cached 5 min)."""
    now = time.time()
    cached = _chat_reactions_cache.get(chat_id)
    if cached and (now - cached[0]) < 300:
        return cached[1]

    allowed = []
    try:
        chat = await client.get_chat(chat_id)
        avail = getattr(chat, "available_reactions", None)
        if avail is None:
            # Reactions disabled in this group (ChatReactionsNone)
            allowed = []
        elif getattr(avail, "all_are_enabled", False):
            allowed = list(_POSITIVE_REACTIONS)
        elif getattr(avail, "reactions", None):
            chat_emojis = [
                getattr(r, "emoji", None)
                for r in avail.reactions
                if getattr(r, "emoji", None)
            ]
            pos = [e for e in chat_emojis if e in _POSITIVE_REACTIONS]
            if not pos:
                pos = [e for e in chat_emojis if e not in _NEGATIVE_REACTIONS]
            allowed = pos
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"Could not fetch available_reactions for {chat_id}: {exc}")
        allowed = []

    _chat_reactions_cache[chat_id] = (now, allowed)
    return allowed


async def _react_to_group_message(client: Client, message: Message, account_id: str) -> bool:
    """Put a positive Telegram message reaction (not a text reply) on a group message."""
    import random

    chat_id = message.chat.id
    now = time.time()
    blocked_until = _last_account_react_at.get(account_id, 0.0)
    if blocked_until > now:
        return False

    choices = await _get_available_positive_reactions(client, chat_id)
    if not choices:
        return False

    emoji = random.choice(choices)
    tried = [emoji] + [e for e in _SAFE_FALLBACK_REACTIONS if e != emoji]
    for candidate in tried:
        try:
            await client.send_reaction(chat_id, message.id, candidate)
            LOGGER.info(f"✨ Userbot ({account_id}) reacted {candidate} in group {chat_id}")
            return True
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__.lower()
            err_s = str(exc).lower()
            if "reactioninvalid" in name or "reaction_invalid" in err_s:
                _chat_reactions_cache.pop(chat_id, None)
                continue
            if any(k in name or k in err_s for k in ("reactions_disabled", "chatwriteforbidden", "rightforbidden", "userbanned", "channelprivate")):
                _chat_reactions_cache[chat_id] = (time.time(), [])
                return False
            if "flood" in name or "peerflood" in name:
                wait_s = float(getattr(exc, "value", 60) or 60)
                _last_account_react_at[account_id] = time.time() + min(wait_s, 300.0)
                return False
            LOGGER.debug(f"Userbot ({account_id}) reaction failed in {chat_id}: {exc}")
            return False
    _chat_reactions_cache[chat_id] = (time.time(), [])
    return False


def _get_chat_lock(account_id: str, chat_id: int) -> asyncio.Lock:
    key = (account_id, chat_id)
    if key not in _chat_locks:
        _chat_locks[key] = asyncio.Lock()
    return _chat_locks[key]


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


async def _resolve_chance(account: dict, group: bool, addressed: bool = False) -> float:
    """Reply probability (0-100): per-account wins, else the global value."""
    per_account = account.get("reply_chance")
    if per_account is not None:
        try:
            val = max(0.0, min(100.0, float(per_account)))
            if addressed:
                return 100.0 if val >= 50.0 else max(val * 1.5, val)
            return val
        except Exception:
            pass
    if addressed:
        return 100.0
    if group:
        return await settings.get_group_chance()
    return await settings.get_private_chance()


async def _resolve_reply_scope(account: dict) -> str:
    """'both', 'groups', or 'private'."""
    scope = account.get("reply_scope")
    if scope in ("both", "groups", "private"):
        return scope
    return await settings.get_reply_scope()


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


def _check_group_mention_or_reply(
    message: Message, my_telegram_id: int, my_username: str, client: Client = None
) -> tuple[bool, str]:
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

    # 4. Check userbot name triggers if called by name
    if not has_mention and client:
        try:
            from Myra.modules.chatbot import _extract_name_triggers, normalize_fancy_font
            text_plain = normalize_fancy_font(text)
            for trigger in _extract_name_triggers(client, include_default_myra=False):
                pattern = re.compile(rf"(?<!\w){re.escape(trigger)}(?!\w)", re.IGNORECASE)
                matches = list(pattern.finditer(text))
                if matches:
                    has_mention = True
                    for m in matches:
                        mention_spans.append(m.span())
                    break
                if text_plain != text:
                    matches = list(pattern.finditer(text_plain))
                    if matches:
                        has_mention = True
                        for m in matches:
                            mention_spans.append(m.span())
                        break
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(f"Userbot name trigger check failed: {exc}")

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


async def _reply_like_human(client: Client, message: Message, account: dict, response: dict, chance: float = 100.0) -> bool:
    """Send the reply with the userbot's configured speed + human pacing."""
    delay_ms = await _resolve_delay_ms(account)
    return await respond_like_human(
        client,
        message,
        response,
        chance=chance,
        base_delay_ms=delay_ms,
        wpm=config.HUMAN_TYPING_WPM,
        allow_split=False,  # Single message bubble prevents Telegram flood limits
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

        Mentions / replies are answered. Normal messages ("hy", "hello")
        are answered when group mode is "all".
        Respects reply scope ('both', 'groups', 'private'), reply delay,
        and reply probability without spamming or triggering flood limits.
        """
        account = ub_accounts.get(account_id)
        if not account:
            return

        action_mode = account.get("action_mode") or "chat"
        # AutoChat ON/OFF check (React-only mode still reacts even if chatting is OFF)
        if action_mode == "chat" and not account.get("autochat", True):
            return

        # ── 1. REPLY SCOPE CHECK (Both / Groups Only / DMs Only) ─────────
        reply_scope = await _resolve_reply_scope(account)
        if reply_scope == "private":
            return  # DM-only mode active: ignore group messages

        my_id = account["telegram_id"]
        my_username = account.get("username", "")

        # Auto-sync newly joined groups (via addlist / invite link) without manual restart
        try:
            from Myra.services.broadcast import invalidate_dialog_cache, is_chat_cached
            if not is_chat_cached(account_id, message.chat.id):
                invalidate_dialog_cache(account_id)
                _chat_reactions_cache.pop(message.chat.id, None)
        except Exception:
            pass

        # ── 2. ONLY REAL USERS ───────────────────────────────────────────
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

        # ── ACTION MODE: 'chat' | 'react' | 'both' ───────────────────────
        if action_mode in ("react", "both"):
            asyncio.create_task(_react_to_group_message(c, message, account_id))
        if action_mode == "react":
            return
        if not account.get("autochat", True):
            return
        if not message.text and not message.sticker:
            return

        # Was the userbot addressed?
        addressed, cleaned_text = _check_group_mention_or_reply(message, my_id, my_username, client=client)

        if not addressed and _group_mode(account) != "all":
            return

        query_text = cleaned_text if addressed else raw_text
        sticker_id = message.sticker.file_unique_id if message.sticker else None
        if not query_text and not sticker_id:
            return

        chat_id = message.chat.id
        key = (account_id, chat_id)
        lock = _get_chat_lock(account_id, chat_id)

        # ── 3. ANTI-SPAM CONCURRENCY CHECK ───────────────────────────────
        # If userbot is ALREADY preparing/sending a reply in this chat, drop
        # incoming messages so they don't queue up to send consecutive spam replies.
        if lock.locked():
            LOGGER.debug(f"Userbot ({account_id}) already responding in chat {chat_id}, skipping rapid message")
            return

        # ── 4. CHAT & ACCOUNT COOLDOWN CHECK ─────────────────────────────
        delay_ms = await _resolve_delay_ms(account)
        cooldown_sec = max(5.0, delay_ms / 1000.0)
        now = time.time()
        if (now - _last_reply_at.get(key, 0.0)) < cooldown_sec:
            LOGGER.debug(f"Userbot ({account_id}) cooldown in chat {chat_id}, skipping")
            return
        if (now - _last_account_reply_at.get(account_id, 0.0)) < 3.0:
            LOGGER.debug(f"Userbot ({account_id}) global anti-limit cooldown, skipping")
            return

        # ── 5. % REPLY CHANCE CHECK (BEFORE GEMINI/DB LOOKUP) ────────────
        chance = await _resolve_chance(account, group=True, addressed=addressed)
        if not should_reply(chance):
            LOGGER.debug(f"Userbot ({account_id}) skipped group reply by chance ({chance}%)")
            return

        # ── 6. PROCESS UNDER LOCK ────────────────────────────────────────
        async with lock:
            now = time.time()
            if (now - _last_reply_at.get(key, 0.0)) < cooldown_sec:
                return

            allow_ai = config.AI_GROUP_FALLBACK if addressed else config.AI_GROUP_FALLBACK_PLAIN
            response = await get_chatbot_response(
                query_text,
                sticker_id=sticker_id,
                allow_ai=allow_ai,
            )
            if not response:
                return

            try:
                sent = await _reply_like_human(c, message, account, response, chance=100.0)
                if sent:
                    now_ts = time.time()
                    _last_reply_at[key] = now_ts
                    _last_account_reply_at[account_id] = now_ts
                    LOGGER.info(f"💬 Userbot ({account_id}) replied in group {chat_id}")
            except Exception as e:
                LOGGER.error(f"⚠️ Userbot ({account_id}) group reply error: {e}")

    async def private_autochat_handler(c: Client, message: Message):
        """
        Handle private (DM) chats when AutoChat is ON.
        Respects reply scope ('both', 'groups', 'private'), reply delay,
        and reply probability without spamming or triggering flood limits.
        """
        account = ub_accounts.get(account_id)
        if not account:
            return

        # AutoChat ON/OFF check
        if not account.get("autochat", True):
            return

        # ── 1. REPLY SCOPE CHECK (Both / Groups Only / DMs Only) ─────────
        reply_scope = await _resolve_reply_scope(account)
        if reply_scope == "groups":
            return  # Groups-only mode active: ignore private DM messages

        my_id = account["telegram_id"]

        # ── 2. ONLY REAL USERS ───────────────────────────────────────────
        if not await is_real_user(message, my_id=my_id):
            return

        # Skip commands
        raw_text = message.text or message.caption or ""
        if raw_text.startswith(("/", "!", "?", ".")):
            return

        sticker_id = message.sticker.file_unique_id if message.sticker else None
        if not raw_text and not sticker_id:
            return

        chat_id = message.chat.id
        key = (account_id, chat_id)
        lock = _get_chat_lock(account_id, chat_id)

        # ── 3. ANTI-SPAM CONCURRENCY CHECK ───────────────────────────────
        if lock.locked():
            LOGGER.debug(f"Userbot ({account_id}) already responding in DM {chat_id}, skipping rapid message")
            return

        # ── 4. CHAT COOLDOWN CHECK ───────────────────────────────────────
        delay_ms = await _resolve_delay_ms(account)
        cooldown_sec = max(2.0, delay_ms / 1000.0)
        now = time.time()
        if (now - _last_reply_at.get(key, 0.0)) < cooldown_sec:
            LOGGER.debug(f"Userbot ({account_id}) cooldown in DM {chat_id}, skipping")
            return

        # ── 5. % REPLY CHANCE CHECK (BEFORE GEMINI/DB LOOKUP) ────────────
        chance = await _resolve_chance(account, group=False, addressed=True)
        if not should_reply(chance):
            LOGGER.debug(f"Userbot ({account_id}) skipped private reply by chance ({chance}%)")
            return

        # ── 6. PROCESS UNDER LOCK ────────────────────────────────────────
        async with lock:
            now = time.time()
            if (now - _last_reply_at.get(key, 0.0)) < cooldown_sec:
                return

            response = await get_chatbot_response(
                raw_text,
                sticker_id=sticker_id,
                allow_ai=config.AI_PRIVATE_FALLBACK,
            )
            if not response:
                return

            try:
                sent = await _reply_like_human(c, message, account, response, chance=100.0)
                if sent:
                    _last_reply_at[key] = time.time()
                    LOGGER.info(f"💬 Userbot ({account_id}) replied in DM {chat_id}")
            except Exception as e:
                LOGGER.error(f"⚠️ Userbot ({account_id}) private reply error: {e}")

    async def group_join_sync_handler(c: Client, message: Message):
        """Auto-refresh dialog cache when userbot joins a new group via addlist or invite link."""
        account = ub_accounts.get(account_id)
        if not account:
            return
        my_id = account.get("telegram_id")
        joined = getattr(message, "new_chat_members", None) or []
        if any(getattr(u, "id", None) == my_id for u in joined):
            try:
                from Myra.services.broadcast import invalidate_dialog_cache
                invalidate_dialog_cache(account_id)
                _chat_reactions_cache.pop(message.chat.id, None)
                LOGGER.info(f"🆕 Userbot ({account_id}) joined group {message.chat.id} — dialogs auto-synced")
            except Exception:
                pass

    # Register handlers directly on client using MessageHandler
    group_filter = filters.group & ~filters.service
    join_filter = filters.group & filters.new_chat_members
    private_filter = filters.private & (filters.text | filters.sticker)

    client.add_handler(MessageHandler(_within_scope(group_autochat_handler), group_filter), group=1)
    client.add_handler(MessageHandler(_within_scope(private_autochat_handler), private_filter), group=2)
    client.add_handler(MessageHandler(_within_scope(group_join_sync_handler), join_filter), group=3)
    LOGGER.info(f"✅ Handlers registered successfully for userbot {account_id}")
