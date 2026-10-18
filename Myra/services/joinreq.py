"""
Group Request Controller — the ``chat_join_request`` flow.

Ported logic (from the Telegram-Join-Request-Bot pattern) trimmed down to what a
single bot + its clones actually need:

    1. User taps "Ask to join" on a group with request-to-join switched on.
    2. Telegram fires a ChatJoinRequest. We DM the user a welcome card listing
       the channels/groups they must join first.
    3. The user taps **VERIFY**. We re-check membership with ``get_chat_member``.
    4. ✅ all joined → auto-approve the request (or, if auto-approve is off,
       mark them verified and tell the group's admins).
    ❌ someone skipped a channel → keep the request pending and nudge them.

The bot must be an **admin** in the managed group (with "Manage chat"/"Invite
users" permission) for ``approve_chat_join_request`` to work.

Everything is scope-aware: a clone bot only controls its own groups.
"""

import html as _html
import re
import time

from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import (
    BadRequest,
    ChannelPrivate,
    ChatAdminRequired,
    Forbidden,
    RPCError,
    UserNotParticipant,
)
from pyrogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from Myra import LOGGER
from Myra.database import joinreq as jdb
from Myra.database import settings as sdb
from Myra.database.scope import current_scope

DEFAULT_WELCOME = (
    "Hey {name} ✨\n\n"
    "{chat} mein aapki request aa gayi hai!\n\n"
    "Aage badhne se pehle neeche wali sab communities join kar lo, "
    "phir **VERIFY** dabao 🥺"
)

# {(chat_id, user_id): last verify timestamp} — anti hammering
_last_verify: dict = {}
VERIFY_TTL = 45  # seconds


def _esc(value) -> str:
    return _html.escape(str(value or ""))


def build_verify_keyboard(chat_id: int, required: list) -> InlineKeyboardMarkup:
    rows = []
    for entry in required or []:
        label = entry.get("label") or entry.get("username") or "Join"
        url = None
        if entry.get("username"):
            url = f"https://t.me/{entry['username']}"
        elif entry.get("invite"):
            url = entry["invite"]
        if not url:
            continue
        rows.append([InlineKeyboardButton(f"➕ {_esc(label)}", url=url)])

    rows.append([InlineKeyboardButton("✅ VERIFY", callback_data=f"jr_v:{int(chat_id)}")])
    return InlineKeyboardMarkup(rows)


async def _fill(text: str, *, name: str, chat: str, count: int) -> str:
    """Replace ``{name}`` / ``{chat}`` / ``{count}`` without str.format(), so an
    owner's custom welcome can safely contain literal braces."""
    out = text or DEFAULT_WELCOME
    for token, value in (("{name}", name), ("{chat}", chat), ("{count}", str(count))):
        out = out.replace(token, value)
    return out


async def _approve(client, chat_id: int, user, scope: str = None) -> bool:
    """Approve one pending join request. Returns True on success."""
    try:
        await client.approve_chat_join_request(chat_id, user.id)
    except ChatAdminRequired:
        LOGGER.warning(
            f"joinreq: cannot approve {user.id} in {chat_id} — the bot is not an "
            "admin there (needs 'Invite users' + 'Manage chat')."
        )
        return False
    except RPCError as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq approve failed for {chat_id}: {exc}")
        return False

    await jdb.bump(chat_id, "approved", 1, scope=scope)
    LOGGER.info(f"joinreq: approved {user.id} in {chat_id}")
    return True


async def _dm_welcome(client, user, title: str) -> None:
    """Best-effort "you're in" DM after an instant approval (never blocks)."""
    try:
        await client.send_message(
            int(user.id),
            f"🎉 Welcome to {_esc(title)}!\nAapki join request approve ho gayi ✨",
            disable_web_page_preview=True,
        )
    except Exception:  # noqa: BLE001
        pass


async def on_join_request(client, request: ChatJoinRequest):
    """Entry point — called by the ChatJoinRequest handler."""
    try:
        chat = request.chat
        user = request.user
    except Exception:  # noqa: BLE001
        return

    if user is None or getattr(user, "is_bot", False):
        return

    scope = current_scope()

    enabled = await sdb.get_setting(sdb.KEY_JOINREQ_ENABLED, scope=scope, default=False)
    if not enabled:
        return

    doc = await jdb.get_chat(chat.id, scope=scope)
    if not doc or not doc.get("enabled"):
        return

    required = doc.get("required") or []
    auto = await sdb.get_setting(sdb.KEY_JOINREQ_AUTO, scope=scope, default=True)

    # Nothing to verify → that IS auto-approve. Let the person straight in,
    # without any DM: a user who never started the bot cannot be DM'd at all,
    # so waiting for a VERIFY tap would leave the request pending forever.
    if auto and not required:
        if await _approve(client, chat.id, user, scope=scope):
            await _dm_welcome(client, user, chat.title or "the group")
        return

    welcome = await sdb.get_setting(sdb.KEY_JOINREQ_WELCOME, scope=scope, default=None)
    text = await _fill(
        welcome or DEFAULT_WELCOME,
        name=_esc(user.first_name or "dost"),
        chat=_esc(chat.title or "is group"),
        count=len(required),
    )

    markup = build_verify_keyboard(chat.id, required)
    try:
        await client.send_message(user.id, text, reply_markup=markup, disable_web_page_preview=True)
        await jdb.bump(chat.id, "pending", 1, scope=scope)
    except (Forbidden, UserNotParticipant) as exc:
        # The user never opened the bot, so Telegram refuses the DM. The request
        # stays pending — log it loudly, otherwise the feature looks "dead".
        LOGGER.warning(
            f"joinreq: cannot DM {user.id} ({user.first_name!r}) — they never "
            f"started the bot, so the VERIFY card could not be sent and the "
            f"request for {chat.id} stays pending. ({exc})"
        )
    except BadRequest as exc:
        LOGGER.warning(f"joinreq DM rejected for {user.id}: {exc}")
    except RPCError as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq DM error: {exc}")


async def _member_ok(client, chat_id: int, user_id: int) -> bool:
    """True when the user really is a member of the required chat."""
    try:
        member = await client.get_chat_member(chat_id, user_id)
    except UserNotParticipant:
        return False
    except (ChannelPrivate, ChatAdminRequired):
        # The bot cannot see this chat (not a member / not admin). Enforcing it
        # would reject honest users, so we skip it and just log a warning.
        LOGGER.debug(f"joinreq: cannot check membership for {chat_id}")
        return True
    except RPCError:  # noqa: BLE001
        return False

    status = member.status
    return status not in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT, ChatMemberStatus.RESTRICTED)


async def verify_and_approve(client, query: CallbackQuery, chat_id: int):
    """The VERIFY button. Re-checks every required chat, then approves or notifies."""
    user = query.from_user
    if user is None:
        return await query.answer("Kuch galat ho gaya 😥", show_alert=True)

    scope = current_scope()
    now = time.time()
    key = (chat_id, user.id)
    if now - _last_verify.get(key, 0) < VERIFY_TTL:
        return await query.answer("Thoda ruk jao, phir verify karo 🥺", show_alert=True)
    _last_verify[key] = now

    doc = await jdb.get_chat(chat_id, scope=scope)
    if not doc or not doc.get("enabled"):
        return await query.answer("Is group ka request controller band hai 🚫", show_alert=True)

    required = doc.get("required") or []
    if not required:
        missing = []
    else:
        missing = [
            (entry.get("label") or entry.get("username") or "community")
            for entry in required
            if not await _member_ok(client, int(entry.get("chat_id") or 0), user.id)
        ]

    title = _esc(doc.get("title") or "the group")

    if missing:
        joined = "\n".join(f"  ✗ {m}" for m in missing[:6])
        await query.answer("Pehle ye join karo 😥", show_alert=True)
        try:
            await query.message.edit_text(
                f"❌ **Abahi verify nahi hua.**\n\n"
                f"Inhe join karo, phir wapas **VERIFY** dabao:\n{joined}",
                reply_markup=build_verify_keyboard(chat_id, required),
                disable_web_page_preview=True,
            )
        except Exception:  # noqa: BLE001
            pass
        return

    await jdb.bump(chat_id, "verified", 1, scope=scope)

    auto = await sdb.get_setting(sdb.KEY_JOINREQ_AUTO, scope=scope, default=True)
    if not auto:
        try:
            await query.message.edit_text(
                "✅ **Verified!**\n\n"
                "Ab koi admin aapki request approve kar dega — ruk jao 🥺",
                disable_web_page_preview=True,
            )
        except Exception:  # noqa: BLE001
            pass
        await _notify_admins(client, chat_id, user, verified_only=True)
        return

    try:
        await client.approve_chat_join_request(chat_id, user.id)
    except ChatAdminRequired:
        await query.answer("Bot ko group mein admin banao 🛠", show_alert=True)
        await _notify_admins(client, chat_id, user, verified_only=True)
        return
    except RPCError as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq approve failed for {chat_id}: {exc}")
        await query.answer("Approve fail ho gaya, admin se baat karo 😥", show_alert=True)
        return

    await jdb.bump(chat_id, "approved", 1, scope=scope)
    try:
        await query.message.edit_text(
            f"🎉 **Ho gaya!** {title} mein aapki request approve ho gayi.\n"
            f"Wapas jao aur enjoy karo ✨",
            disable_web_page_preview=True,
        )
    except Exception:  # noqa: BLE001
        pass

    try:
        await client.send_message(
            chat_id,
            f"🚪 **Join approved** — [{_html.escape(user.first_name or 'user')}](tg://user?id={user.id})",
            disable_web_page_preview=True,
        )
    except Exception:  # noqa: BLE001
        pass


async def _notify_admins(client, chat_id: int, user, verified_only: bool = False):
    """Tell the group's admins about a verified (but unapproved) request."""
    lines = (
        "✅ **Verified user** — auto-approve OFF hai, approve kar do:"
        if verified_only
        else "🚪 **Join request**"
    )
    body = f"{lines}\n[{_html.escape(user.first_name or 'user')}](tg://user?id={user.id})"
    try:
        admins = await client.get_chat_members(chat_id, filter="administrators")
        for member in admins:
            if member.user and not member.user.is_bot:
                try:
                    await client.send_message(member.user.id, body, disable_web_page_preview=True)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass


# ══════════════════════════════════════════════════════════════════════════
#  Helpers shared with the admin panel
# ══════════════════════════════════════════════════════════════════════════
_REF_ID = re.compile(r"^-?\d{5,}$")
_REF_USERNAME = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]{3,})|^@([A-Za-z0-9_]{3,})$")


async def resolve_chat(client, text: str):
    """Turn a pasted `-100…` id, an `@username` or a `t.me/…` link into a chat.

    Returns ``(chat, None)`` on success or ``(None, "reason")`` on failure.
    """
    text = (text or "").strip()
    if not text:
        return None, "kuch toh bhejo 😥"
    if len(text) > 120:
        return None, "bahut lamba text aa gaya 😅"

    target = None
    if _REF_ID.match(text):
        target = int(text)
    else:
        m = _REF_USERNAME.search(text)
        if not m:
            return None, "id, @username ya t.me link chahiye 🙂"
        target = m.group(1) or m.group(2)
        if target.lower() in ("joinchat", "addstickers", "share", "proxy", "socks"):
            return None, "koi channel/group ka @username ya link do 🙂"

    try:
        chat = await client.get_chat(target)
    except Exception as exc:  # noqa: BLE001
        return None, f"nahi mila ❌ ({type(exc).__name__})"

    if chat.type == ChatType.PRIVATE:
        return None, "ye toh koi user hai 😅 group/channel do"
    return chat, None


async def invite_for(client, chat) -> str:
    """Best-effort public join link for a required chat (used in the DM buttons)."""
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}"
    try:
        return await client.export_chat_invite_link(chat.id)
    except Exception:  # noqa: BLE001
        return ""


__all__ = [
    "DEFAULT_WELCOME",
    "on_join_request",
    "verify_and_approve",
    "resolve_chat",
    "invite_for",
    "build_verify_keyboard",
]
