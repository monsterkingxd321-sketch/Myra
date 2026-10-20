"""
Broadcast engine — send one message from the main bot **or** from any userbot
to groups, DMs, or both.

Used by ``/broadcast`` and the ``/admin`` inline panel.
"""

import asyncio
import os
import time

from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.errors import (
    ChatAdminRequired,
    ChatWriteForbidden,
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    RPCError,
    UserIsBlocked,
    UserPrivacyRestricted,
)

from Myra import LOGGER
from Myra.ai.audience import own_account_ids
from Myra.database.chats import get_chats
from Myra.database.users import get_users

DIALOG_CACHE_TTL = 30 * 60  # 30 minutes
_DIALOG_CACHE: dict = {}

# Expired lists are refreshed in the background so a caller using
# ``allow_stale=True`` (the admin panel) never waits on Telegram.
_REFRESH_TASKS: set = set()
_REFRESHING: set = set()


def _schedule_dialog_refresh(client: Client, account_id: str) -> None:
    """Re-read one expired dialog list without blocking the current caller."""
    if account_id in _REFRESHING:
        return
    _REFRESHING.add(account_id)

    def _done(task) -> None:
        _REFRESH_TASKS.discard(task)
        _REFRESHING.discard(account_id)
        try:
            task.exception()
        except Exception:  # noqa: BLE001 - cancelled/failed refreshes are noise
            pass

    try:
        task = asyncio.ensure_future(userbot_dialogs(client, account_id, force=True))
    except Exception as exc:  # noqa: BLE001
        _REFRESHING.discard(account_id)
        LOGGER.debug(f"broadcast: could not refresh dialogs for {account_id}: {exc}")
        return
    _REFRESH_TASKS.add(task)
    task.add_done_callback(_done)

# Human-ish pause between two sends from a *user account* (Telegram bans fast
# mass-messaging), the bot can go a bit faster.
USERBOT_SLEEP = 1.4
BOT_SLEEP = 0.35


# ══════════════════════════════════════════════════════════════════════════
#  Content
# ══════════════════════════════════════════════════════════════════════════
class Content:
    """A broadcast payload: plain text, or a message with/without media."""

    def __init__(self, kind: str = "text", text: str = "", message=None, file_path: str = ""):
        self.kind = kind
        self.text = text or ""
        self.message = message
        self.file_path = file_path

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Content {self.kind} text={self.text[:30]!r}>"


def _detect_kind(message) -> str:
    if message is None:
        return "text"
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "animation", None):
        return "animation"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "document", None):
        return "document"
    return "text"


def content_from_message(message) -> Content:
    """Build a :class:`Content` from a Telegram message (reply target)."""
    kind = _detect_kind(message)
    text = message.text or message.caption or ""
    return Content(kind=kind, text=text, message=message)


def content_from_text(text: str) -> Content:
    return Content(kind="text", text=text or "")


async def prepare_userbot_content(content: Content) -> Content:
    """
    User accounts cannot reuse a bot's ``file_id``, so media is downloaded once
    and re-uploaded from disk afterwards.
    """
    if content.kind == "text" or content.message is None or content.file_path:
        return content

    try:
        path = await content.message.download()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"⚠️ broadcast: could not download media ({exc}) — sending text only")
        return Content(kind="text", text=content.text)

    if not path:
        return Content(kind="text", text=content.text)

    content.file_path = path
    return content


# ══════════════════════════════════════════════════════════════════════════
#  Targets
# ══════════════════════════════════════════════════════════════════════════
async def bot_targets(kind: str, scope: str = None) -> list:
    """
    ``kind`` is "groups" or "dm".

    Groups come from the served-chats collection, DMs from the served users —
    both of the bot in ``scope``.
    """
    if kind == "groups":
        return await get_chats(scope)
    if kind == "dm":
        return await get_users(scope)
    return []


async def userbot_dialogs(
    client: Client,
    account_id: str,
    force: bool = False,
    allow_stale: bool = False,
) -> dict:
    """
    Cached {"groups": [...], "dm": [...]} for one userbot account.

    ``allow_stale=True`` returns the last known list even when it is older than
    ``DIALOG_CACHE_TTL`` instead of asking Telegram again. The read-only admin
    views use that so opening the panel never waits on ``get_dialogs()`` for
    every connected account; the "re-scan" button is what refreshes them.
    """
    now = time.time()
    cached = _DIALOG_CACHE.get(account_id)
    if cached and not force:
        if now - cached.get("ts", 0) < DIALOG_CACHE_TTL:
            return cached
        if allow_stale:
            _schedule_dialog_refresh(client, account_id)
            return cached

    groups, dm = [], []
    skip_ids = await own_account_ids()
    try:
        async for dialog in client.get_dialogs():
            chat = getattr(dialog, "chat", None)
            if chat is None:
                continue
            if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                groups.append(chat.id)
            elif chat.type == ChatType.PRIVATE:
                if getattr(chat, "is_bot", False):
                    continue
                if chat.id in skip_ids:
                    continue
                dm.append(chat.id)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"⚠️ broadcast: could not read dialogs for {account_id}: {exc}")

    data = {"ts": now, "groups": groups, "dm": dm}
    _DIALOG_CACHE[account_id] = data
    return data


async def userbot_targets(client: Client, account_id: str, kind: str, force: bool = False) -> list:
    data = await userbot_dialogs(client, account_id, force=force)
    if kind == "groups":
        return data["groups"]
    if kind == "dm":
        return data["dm"]
    return data["groups"] + data["dm"]


# ══════════════════════════════════════════════════════════════════════════
#  Sending
# ══════════════════════════════════════════════════════════════════════════
async def _send_one(client: Client, chat_id: int, content: Content, is_bot: bool) -> tuple[bool, str]:
    """Send to a single chat. Returns (ok, reason)."""
    try:
        if is_bot and content.message is not None:
            await content.message.copy(chat_id=chat_id)
            return True, ""

        text = content.text or None
        if content.kind == "text" or not content.file_path:
            if not text:
                return False, "empty"
            await client.send_message(chat_id=chat_id, text=text)
            return True, ""

        kwargs = {"chat_id": chat_id, "caption": text or None, "file_name": os.path.basename(content.file_path)}
        if content.kind == "photo":
            await client.send_photo(photo=content.file_path, **kwargs)
        elif content.kind == "video":
            await client.send_video(video=content.file_path, **kwargs)
        elif content.kind == "animation":
            await client.send_animation(animation=content.file_path, **kwargs)
        elif content.kind == "audio":
            await client.send_audio(audio=content.file_path, **kwargs)
        elif content.kind == "voice":
            await client.send_voice(voice=content.file_path, **kwargs)
        else:
            await client.send_document(document=content.file_path, **kwargs)
        return True, ""

    except FloodWait as exc:
        wait = int(getattr(exc, "value", 30) or 30)
        LOGGER.warning(f"⏳ broadcast flood-wait {wait}s")
        await asyncio.sleep(wait)
        try:
            return await _send_one(client, chat_id, content, is_bot)
        except Exception:  # noqa: BLE001
            return False, "flood"
    except UserIsBlocked:
        return False, "blocked"
    except InputUserDeactivated:
        return False, "deactivated"
    except PeerIdInvalid:
        return False, "invalid"
    except UserPrivacyRestricted:
        return False, "privacy"
    except (ChatWriteForbidden, ChatAdminRequired):
        return False, "no-permission"
    except RPCError as exc:
        return False, f"rpc:{type(exc).__name__}"
    except Exception as exc:  # noqa: BLE001
        return False, f"error:{type(exc).__name__}"


async def _run(
    sender_label: str,
    senders: list,
    targets: list,
    content: Content,
    sleep_between: float,
    progress=None,
    cancel=None,
) -> dict:
    """
    Round-robin the targets across all senders.

    ``senders`` is a list of ``(client, is_bot, account_id)`` tuples.
    """
    stats = {
        "sender": sender_label,
        "total": len(targets),
        "sent": 0,
        "failed": 0,
        "cancelled": False,
        "reasons": {},
    }
    if not targets or not senders:
        return stats

    for index, chat_id in enumerate(targets):
        if cancel is not None and cancel.get("stop"):
            stats["cancelled"] = True
            break

        client, is_bot, _account_id = senders[index % len(senders)]
        ok, reason = await _send_one(client, chat_id, content, is_bot)
        if ok:
            stats["sent"] += 1
        else:
            stats["failed"] += 1
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1

        if progress is not None and (index % 10 == 0 or index == len(targets) - 1):
            try:
                await progress(stats)
            except Exception:  # noqa: BLE001
                pass

        await asyncio.sleep(sleep_between)

    return stats


async def broadcast_bot(content: Content, target: str, progress=None, cancel=None, scope: str = None) -> dict:
    """Broadcast from the bot of ``scope`` (main bot by default) to groups / DMs / both."""
    from Myra.services.clones import bot_client_for

    bot_client = bot_client_for(scope)
    if bot_client is None:
        return {"sender": "bot", "total": 0, "sent": 0, "failed": 0, "cancelled": False, "reasons": {}}

    targets = []
    if target in ("groups", "both"):
        targets += await bot_targets("groups", scope)
    if target in ("dm", "both"):
        targets += await bot_targets("dm", scope)

    # de-duplicate while keeping the order
    targets = list(dict.fromkeys(targets))
    from Myra.database.bots import bot_info

    label = f"🤖 {bot_info(scope).get('name') or 'Main Bot'}"
    return await _run(label, [(bot_client, True, "bot")], targets, content, BOT_SLEEP, progress, cancel)


async def broadcast_userbots(
    content: Content,
    target: str,
    account_ids: list = None,
    progress=None,
    cancel=None,
    scope: str = None,
) -> dict:
    """Broadcast from one, several, or **all** connected userbots of ``scope``."""
    from Myra.database.scope import current_scope, owns_scope
    from Myra.userbot.storage import accounts as ub_accounts

    scope = scope or current_scope()

    if not ub_accounts:
        return {"sender": "userbots", "total": 0, "sent": 0, "failed": 0, "cancelled": False, "reasons": {}}

    selected = account_ids or [
        account_id
        for account_id, data in ub_accounts.items()
        if owns_scope(data.get("scope"), scope)
    ]
    senders = []
    targets = []

    prepared = await prepare_userbot_content(content)

    for account_id in selected:
        data = ub_accounts.get(account_id)
        if not data:
            continue
        if not owns_scope(data.get("scope"), scope):
            continue
        client = data.get("client")
        if client is None or data.get("status") != "CONNECTED":
            continue
        senders.append((client, False, account_id))
        targets += await userbot_targets(client, account_id, target)

    targets = list(dict.fromkeys(targets))
    label = "👥 All Userbots" if not account_ids else f"👤 {len(account_ids)} userbot(s)"
    return await _run(label, senders, targets, prepared, USERBOT_SLEEP, progress, cancel)


async def run_broadcast(
    sender: str,
    content: Content,
    target: str,
    progress=None,
    cancel=None,
    scope: str = None,
) -> dict:
    """
    ``sender`` = "bot", "userbots" (all), or a specific ``account_id``.
    ``target`` = "groups" | "dm" | "both".
    """
    if sender == "bot":
        return await broadcast_bot(content, target, progress, cancel, scope)
    if sender in ("userbots", "all"):
        return await broadcast_userbots(content, target, None, progress, cancel, scope)
    return await broadcast_userbots(content, target, [sender], progress, cancel, scope)


def describe(
    sender: str,
    target: str,
    stats: dict,
    seconds: float = 0.0,
    account_names: dict = None,
) -> str:
    """Human readable broadcast report."""
    if sender == "bot":
        sender_label = "🤖 Main Bot"
    elif sender in ("userbots", "all"):
        sender_label = "👥 All Userbots"
    else:
        name = (account_names or {}).get(sender, sender)
        sender_label = f"👤 {name}"

    target_label = {
        "groups": "💬 Groups only",
        "dm": "✉️ DMs only",
        "both": "🌐 Groups + DMs",
    }.get(target, target)

    lines = [
        "📢 **BROADCAST " + ("CANCELLED 🛑" if stats.get("cancelled") else "FINISHED ✅") + "**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📤 **Sender:** {sender_label}",
        f"🎯 **Target:** {target_label}",
        f"📦 **Total chats:** `{stats.get('total', 0)}`",
        f"✅ **Sent:** `{stats.get('sent', 0)}`",
        f"❌ **Failed:** `{stats.get('failed', 0)}`",
    ]
    if seconds:
        lines.append(f"⏱ **Time:** `{seconds:.1f}s`")

    reasons = stats.get("reasons") or {}
    if reasons:
        top = sorted(reasons.items(), key=lambda item: -item[1])[:6]
        lines.append("")
        lines.append("**Failure reasons:**")
        lines += [f"• `{name}` → {count}" for name, count in top]

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


__all__ = [
    "Content",
    "content_from_message",
    "content_from_text",
    "prepare_userbot_content",
    "bot_targets",
    "userbot_targets",
    "userbot_dialogs",
    "broadcast_bot",
    "broadcast_userbots",
    "run_broadcast",
    "describe",
]
