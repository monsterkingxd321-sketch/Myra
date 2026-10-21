"""
Group Request Controller — the ``chat_join_request`` flow.

Same behaviour as the reference "Telegram-Join-Request-Bot":

    1. User taps "Ask to join" on a group with request-to-join switched on.
    2. Telegram fires a ChatJoinRequest → we count it, tell the admins, and DM
       the user a verification card (one join button per required chat + VERIFY).
    3. Each card belongs to a **verification session** (uuid, 30 min by default),
       so a leaked/old button can never approve a later request.
    4. VERIFY re-checks every required chat with ``get_chat_member``:
        ❌ someone missing → card turns into "MISSING REQUIREMENTS" and only the
           still-missing chats are shown ("VERIFY AGAIN");
        ✅ all joined → auto-approve (default) or notify the admins.
    5. A required entry that Telegram cannot check (deleted chat, a *bot*
       username, …) is skipped instead of blocking the applicant forever.

The bot must be an **admin** in the managed group (with "Invite users" +
"Manage chat") for ``approve_chat_join_request`` to work.

Everything is scope-aware: a clone bot only controls its own groups.
"""

import re
import time
from types import SimpleNamespace

from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import (
    BadRequest,
    ChannelInvalid,
    ChannelPrivate,
    ChatAdminRequired,
    Forbidden,
    PeerIdInvalid,
    RPCError,
    UserAlreadyParticipant,
    UserNotParticipant,
    UsernameInvalid,
    UsernameNotOccupied,
)
from pyrogram.raw import functions as raw_functions
from pyrogram.raw import types as raw_types
from pyrogram.types import (
    CallbackQuery,
    Chat as PyroChat,
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from Myra import LOGGER, config
from Myra.database import admins as adminsdb
from Myra.database import joinreq as jdb
from Myra.database import settings as sdb
from Myra.database.scope import current_scope

DEFAULT_TIMEOUT_MIN = 30

DEFAULT_WELCOME = (
    "Welcome, {first_name}! 👋\n\n"
    "Aapki request {chat_title} join karne ki aa gayi hai.\n\n"
    "Approve hone se pehle neeche wali saari channels/groups join karo, "
    "phir VERIFY dabao ✅"
)

#: Minimum gap between two VERIFY taps by the same user (anti hammering).
VERIFY_TTL = 5  # seconds
_last_verify: dict = {}

#: Bumped counters beyond the original three.
COUNTERS = ("pending", "verified", "approved", "failed", "expired")


def _handle(user) -> str:
    """``@username`` when there is one, otherwise the numeric id."""
    username = getattr(user, "username", None)
    if username:
        return f"@{username}"
    return f"ID: {getattr(user, 'id', 0)}"


def _u(user):
    """Normalise a Telegram user to the fields we print.

    Old cached ``User`` objects, clone clients and the offline tests can all
    hand us an object without ``last_name``/``username``; normalising once
    keeps the rest of the file simple.
    """
    return SimpleNamespace(
        id=int(getattr(user, "id", 0) or 0),
        first_name=getattr(user, "first_name", None) or "user",
        last_name=getattr(user, "last_name", None) or "",
        username=getattr(user, "username", None),
        is_bot=bool(getattr(user, "is_bot", False)),
    )


def _flag(value, default: bool) -> bool:
    """``None`` means "use the global default"."""
    return default if value is None else bool(value)


def _usable_required(required) -> list:
    """Drop entries Telegram can never verify (bots, users, broken ids).

    The reference bot skips ``type == "bot"``; a positive id can only be a
    user/bot, so both are filtered here. Without this one bad entry makes
    every applicant fail VERIFY forever.
    """
    out = []
    for entry in required or []:
        if _is_manual(entry):
            continue  # checked by a human, not by get_chat_member
        try:
            chat_id = int(entry.get("chat_id") or 0)
        except Exception:  # noqa: BLE001
            chat_id = 0
        kind = str(entry.get("type") or "").lower()
        if kind == "bot" or chat_id >= 0:
            LOGGER.warning(
                f"joinreq: ignoring unverifiable required entry "
                f"{entry.get('label') or entry.get('username') or chat_id!r} "
                f"(bots/users can't be membership-checked)"
            )
            continue
        out.append(entry)
    return out


#: Entry types that a bot can never membership-check. They are shown as join
#: buttons, but the group admin approves the request by hand.
MANUAL_TYPES = ("manual", "bot", "link")


def _is_manual(entry) -> bool:
    """A manual entry = a link we can only *show* (bot referral, website…)."""
    if not isinstance(entry, dict):
        return False
    if str(entry.get("type") or "").lower() in MANUAL_TYPES:
        return True
    # no chat id and only a URL → nothing to check either
    try:
        chat_id = int(entry.get("chat_id") or 0)
    except Exception:  # noqa: BLE001
        chat_id = 0
    return chat_id == 0 and bool(entry.get("invite")) and not entry.get("username")


def _manual_required(required) -> list:
    return [entry for entry in required or [] if _is_manual(entry)]


def manual_entry(text: str):
    """
    Turn a pasted **bot referral link** or any external URL into a manual
    required entry. Returns ``None`` when the text is a normal group/channel
    reference (so the regular resolver handles it).
    """
    text = (text or "").strip()
    if not text or len(text) > 300:
        return None
    # private invites + t.me/c copy-links go through the normal resolver
    if _REF_INVITE.search(text) or _REF_PRIVATE.search(text):
        return None

    m = _REF_TME.search(text)
    if m:
        name = m.group(1)
        tail = m.group(2) or ""
        if not re.search(r"[?&](start|startgroup|startapp|ref)=", tail, re.IGNORECASE):
            return None  # plain group / channel link
        return {
            "chat_id": 0,
            "username": None,
            "label": f"@{name}",
            "invite": f"https://t.me/{name}{tail}",
            "type": "manual",
        }

    m = _REF_URL.search(text)
    if not m:
        return None
    url = m.group(0)
    host = url.split("://", 1)[-1].split("/", 1)[0]
    if host.lower().endswith(("t.me", "telegram.me")):
        return None  # a t.me link we don't understand → let the resolver talk
    return {
        "chat_id": 0,
        "username": None,
        "label": host[:60],
        "invite": url,
        "type": "manual",
    }


def _button_url(entry):
    if entry.get("invite"):
        return entry["invite"]
    username = entry.get("username")
    if username:
        return f"https://t.me/{str(username).lstrip('@')}"
    return None


def _button_label(entry) -> str:
    return str(
        entry.get("label")
        or entry.get("title")
        or entry.get("username")
        or "Join"
    )[:40]


def build_verify_keyboard(
    chat_id: int,
    required: list = None,
    session_id: str = None,
    *,
    missing: bool = False,
) -> InlineKeyboardMarkup:
    """Join buttons for every required chat + the VERIFY action."""
    rows = []
    for entry in required or []:
        url = _button_url(entry)
        if not url:
            continue
        prefix = "🔄" if missing else "➕"
        hand = "🖐 " if _is_manual(entry) else ""
        rows.append(
            [InlineKeyboardButton(f"{prefix} {hand}{_button_label(entry)}", url=url)]
        )

    action = "✅ VERIFY AGAIN" if missing else "✅ VERIFY"
    rows.append(
        [InlineKeyboardButton(action, callback_data=f"jr_v:{session_id or int(chat_id)}")]
    )
    return InlineKeyboardMarkup(rows)


async def _fill(
    text: str,
    *,
    name: str = "dost",
    chat: str = "is group",
    count: int = 0,
    first_name: str = None,
    last_name: str = None,
    username: str = None,
    user_id: int = None,
    chat_title: str = None,
    chat_username: str = None,
) -> str:
    """Replace ``{name}``/``{chat}``/``{count}`` **and** the reference bot's
    variables (``{first_name}``, ``{chat_title}``, …) without ``str.format``,
    so a custom welcome can safely contain literal braces."""
    first = first_name or name
    title = chat_title or chat
    values = {
        "{first_name}": first,
        "{last_name}": last_name or "",
        "{username}": f"@{username}" if username else first,
        "{user_id}": str(user_id or ""),
        "{chat_title}": title,
        "{chat_username}": f"@{chat_username}" if chat_username else title,
        "{count}": str(count),
        "{name}": name,
        "{chat}": chat,
    }
    out = text or DEFAULT_WELCOME
    # longest first: "{chat}" must not eat the start of "{chat_title}"
    for token in sorted(values, key=len, reverse=True):
        out = out.replace(token, values[token])
    return out


# ══════════════════════════════════════════════════════════════════════════
#  Admin notifications
# ══════════════════════════════════════════════════════════════════════════
async def _recipients() -> list:
    """Owner first, then every panel admin (deduped, order preserved)."""
    ids: list = []
    try:
        owner = int(getattr(config, "OWNER_ID", 0) or 0)
        if owner:
            ids.append(owner)
    except Exception:  # noqa: BLE001
        pass
    try:
        for admin_id in await adminsdb.get_admin_ids() or []:
            try:
                admin_id = int(admin_id)
            except Exception:  # noqa: BLE001
                continue
            if admin_id and admin_id not in ids:
                ids.append(admin_id)
    except Exception:  # noqa: BLE001
        pass
    return ids


async def _notify_admins(client, title: str, body: str) -> None:
    await _notify_admins_markup(client, title, body)


async def _notify_admins_markup(client, title: str, body: str, markup=None) -> None:
    text = f"{title}\n\n{body}"
    for user_id in await _recipients():
        try:
            await client.send_message(
                user_id,
                text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception:  # noqa: BLE001 - admin may not have started this bot
            LOGGER.debug(f"joinreq: could not notify admin {user_id}")


# ══════════════════════════════════════════════════════════════════════════
#  Approve / welcome
# ══════════════════════════════════════════════════════════════════════════
async def _approve(client, chat_id: int, user, scope: str = None) -> bool:
    """Approve one pending join request. Returns True on success."""
    try:
        await client.approve_chat_join_request(chat_id, user.id)
    except UserAlreadyParticipant:
        return True
    except ChatAdminRequired:
        LOGGER.warning(
            f"joinreq: cannot approve {user.id} in {chat_id} — the bot is not an "
            "admin there (needs 'Invite users' + 'Manage chat')."
        )
        return False
    except RPCError as exc:  # noqa: BLE001
        message = str(getattr(exc, "MESSAGE", "") or exc).lower()
        if "hide_requester_missing" in message or "request not found" in message:
            # The request was already approved/declined in Telegram.
            return True
        LOGGER.warning(f"joinreq approve failed for {chat_id}: {exc}")
        return False

    LOGGER.info(f"joinreq: approved {user.id} in {chat_id}")
    return True


async def _dm_approved(client, user, title: str) -> None:
    """Best-effort "you're in" DM after an instant approval (never blocks)."""
    try:
        await client.send_message(
            int(user.id),
            f"🎉 Welcome to {title}!\nAapki join request approve ho gayi ✨",
            disable_web_page_preview=True,
        )
    except Exception:  # noqa: BLE001
        pass


# ══════════════════════════════════════════════════════════════════════════
#  Incoming request
# ══════════════════════════════════════════════════════════════════════════
async def on_join_request(client, request: ChatJoinRequest):
    """Entry point — called by the ChatJoinRequest handler."""
    chat = getattr(request, "chat", None)
    # Newer Pyrogram forks (Kurigram, the one we ship) renamed ``user`` to
    # ``from_user``. Accept both, otherwise ``user`` is None and every request
    # is silently ignored — the whole feature looks dead.
    user = getattr(request, "from_user", None) or getattr(request, "user", None)
    if chat is None or user is None or getattr(user, "is_bot", False):
        return

    user = _u(user)
    chat_title = getattr(chat, "title", None) or str(chat.id)
    chat_username = getattr(chat, "username", None)

    scope = current_scope()

    enabled = await sdb.get_setting(sdb.KEY_JOINREQ_ENABLED, scope=scope, default=False)
    if not enabled:
        LOGGER.debug(f"joinreq: controller is OFF — ignoring request in {chat.id}")
        return

    doc = await jdb.get_chat(chat.id, scope=scope)
    if not doc:
        LOGGER.info(
            f"joinreq: request in {chat.id} ({chat_title!r}) ignored — group is not "
            "in the managed list"
        )
        return
    if not doc.get("enabled"):
        LOGGER.info(f"joinreq: request in {chat.id} ignored — group is paused")
        return

    LOGGER.info(f"joinreq: request user={user.id} chat={chat.id} ({chat_title!r})")
    await jdb.bump(chat.id, "pending", 1, scope=scope)

    all_required = list(doc.get("required") or [])
    required = _usable_required(all_required)          # bot-checkable chats
    manual = _manual_required(all_required)            # admin-checked links
    auto = _flag(
        doc.get("auto_approve"),
        await sdb.get_setting(sdb.KEY_JOINREQ_AUTO, scope=scope, default=True),
    )
    notify = doc.get("notify_admins", True)

    if notify:
        await _notify_admins(
            client,
            "🚪 NEW JOIN REQUEST",
            f"👤 {user.first_name} ({_handle(user)})\n"
            f"🏘 {chat_title}\n"
            + (
                f"🖐 {len(manual)} manual link(s) — admin verify karega\n"
                if manual
                else ""
            )
            + f"📨 Verification DM bhej rahe hain…",
        )

    # Nothing to verify (no chats, no manual links) → that IS auto-approve. Let
    # the person straight in, without any DM: a user who never started the bot
    # cannot be DM'd at all, so waiting for a VERIFY tap would leave the request
    # pending forever. Manual links always need a human, so they never qualify.
    if auto and not all_required:
        if await _approve(client, chat.id, user, scope=scope):
            await jdb.bump(chat.id, "approved", 1, scope=scope)
            await _dm_approved(client, user, chat_title)
        return

    try:
        minutes = int(doc.get("timeout_minutes") or DEFAULT_TIMEOUT_MIN)
    except Exception:  # noqa: BLE001
        minutes = DEFAULT_TIMEOUT_MIN
    minutes = max(1, min(minutes, 1440))

    session = await jdb.create_session(user.id, chat.id, minutes=minutes, scope=scope)

    welcome = doc.get("welcome") or await sdb.get_setting(
        sdb.KEY_JOINREQ_WELCOME, scope=scope, default=None
    )
    text = await _fill(
        welcome or DEFAULT_WELCOME,
        name=user.first_name,
        chat=chat_title,
        count=len(all_required),
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        user_id=user.id,
        chat_title=chat_title,
        chat_username=chat_username,
    )

    markup = build_verify_keyboard(chat.id, all_required, session["session_id"])
    try:
        await client.send_message(
            user.id, text, reply_markup=markup, disable_web_page_preview=True
        )
        LOGGER.info(
            f"joinreq: verification card sent to user={user.id} chat={chat.id} "
            f"session={session['session_id'][:8]}"
        )
    except (Forbidden, UserNotParticipant) as exc:
        # The user never opened the bot, so Telegram refuses the DM. The request
        # stays pending — say so loudly (admin + log), otherwise the feature
        # looks "dead" even though everything else worked.
        LOGGER.warning(
            f"joinreq: cannot DM {user.id} ({user.first_name!r}) — they never "
            f"started the bot, so the VERIFY card could not be sent and the "
            f"request for {chat.id} stays pending. ({exc})"
        )
        if notify:
            await _notify_admins(
                client,
                "⚠️ VERIFICATION DM FAILED",
                f"👤 {user.first_name} ({_handle(user)})\n"
                f"🏘 {chat_title}\n\n"
                f"Usne bot ko /start nahi kiya, isliye VERIFY card nahi gaya.\n"
                f"Request pending hai — Telegram se manually approve/decline karo.",
            )
    except BadRequest as exc:
        LOGGER.warning(f"joinreq DM rejected for {user.id}: {exc}")
    except RPCError as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq DM error: {exc}")


# ══════════════════════════════════════════════════════════════════════════
#  Membership check
# ══════════════════════════════════════════════════════════════════════════
async def _member_ok(client, entry, user_id: int) -> bool:
    """True when the user really is a member of the required chat.

    ``entry`` is the required-chat dict (or a plain chat id). The id is tried
    first; if this bot session has never seen that chat (``PeerIdInvalid`` /
    ``ChannelInvalid`` — very common for a freshly added required chat) we retry
    with the public ``@username``, which always resolves.

    An entry we cannot check at all (deleted chat, a bot/user id, missing
    rights) is treated as satisfied: blocking honest users because of a broken
    config is worse than letting one through.
    """
    username = None
    if isinstance(entry, dict):
        username = entry.get("username")
        chat_id = entry.get("chat_id")
    else:
        chat_id = entry
    try:
        chat_id = int(chat_id or 0)
    except Exception:  # noqa: BLE001
        chat_id = 0
    if chat_id >= 0:  # user / bot / missing id — nothing to check
        return True

    targets = [chat_id]
    if username:
        handle = f"@{str(username).lstrip('@')}"
        if handle not in targets:
            targets.append(handle)

    member = None
    last_error = None
    for target in targets:
        try:
            member = await client.get_chat_member(target, user_id)
            break
        except UserNotParticipant:
            return False
        except (ChannelPrivate, ChatAdminRequired):
            LOGGER.debug(f"joinreq: cannot check membership for {chat_id} (no access)")
            return True
        except (PeerIdInvalid, UsernameInvalid, UsernameNotOccupied, ChannelInvalid) as exc:
            last_error = exc  # unknown peer for this session → try the next target
            continue
        except RPCError as exc:  # noqa: BLE001
            LOGGER.warning(f"joinreq: membership check failed for {chat_id}: {exc}")
            return True

    if member is None:
        LOGGER.warning(
            f"joinreq: required chat {chat_id} is not resolvable — skipping it "
            f"({last_error})"
        )
        return True

    status = member.status
    return status not in (
        ChatMemberStatus.BANNED,
        ChatMemberStatus.LEFT,
        ChatMemberStatus.RESTRICTED,
    )


async def _edit_card(query: CallbackQuery, text: str, markup=None):
    """Works for text cards and (future) photo cards."""
    message = query.message
    try:
        if getattr(message, "photo", None) or getattr(message, "media", None):
            return await message.edit_caption(caption=text, reply_markup=markup)
        return await message.edit_text(
            text, reply_markup=markup, disable_web_page_preview=True
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"joinreq: card edit failed: {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════════
#  VERIFY button
# ══════════════════════════════════════════════════════════════════════════
async def verify_and_approve(client, query: CallbackQuery, payload):
    """The VERIFY button.

    ``payload`` is a session id (new cards) or a chat id (cards sent before
    sessions existed — they still work).
    """
    user = query.from_user
    if user is None:
        return await query.answer("Kuch galat ho gaya 😥", show_alert=True)
    user = _u(user)

    scope = current_scope()
    payload = str(payload or "")

    if re.fullmatch(r"-?\d+", payload):
        chat_id = int(payload)
        doc = await jdb.get_chat(chat_id, scope=scope)
        if not doc or not doc.get("enabled"):
            return await query.answer("Ye group ab controller mein nahi hai 🚫", show_alert=True)
        session = await jdb.get_pending_session(user.id, chat_id, scope=scope)
        if session is None:
            if not _usable_required(doc.get("required")):
                # Nothing to verify; keep the old button usable.
                session = await jdb.create_session(user.id, chat_id, scope=scope)
            else:
                return await query.answer(
                    "⏰ Ye verification expire ho gaya — group me dobara join request bhejo",
                    show_alert=True,
                )
    else:
        session = await jdb.get_session(payload, scope=scope)
        if session is None:
            return await query.answer(
                "⏰ Verification session expire ho gaya — dobara join request bhejo",
                show_alert=True,
            )
        chat_id = int(session.get("chat_id") or 0)

    if int(session.get("user_id") or 0) != user.id:
        return await query.answer("Ye verification kisi aur ke liye hai 🙃", show_alert=True)

    status = session.get("status")
    if status == "approved":
        return await query.answer("✅ Aap already approved ho 🎉", show_alert=True)

    now = time.time()
    if status == "expired" or float(session.get("expires_at") or 0) < now:
        await jdb.set_session_status(session["session_id"], "expired")
        await jdb.bump(chat_id, "expired", 1, scope=scope)
        await _edit_card(
            query,
            "⏰ Verification session expire ho gaya.\n\n"
            "Group me dobara join request bhejo, naya card aa jayega 🙂",
        )
        return await query.answer("⏰ Session expire ho gaya", show_alert=True)

    key = (chat_id, user.id)
    if now - _last_verify.get(key, 0) < VERIFY_TTL:
        return await query.answer("Thoda ruk jao, phir verify karo 🥺", show_alert=True)
    if len(_last_verify) > 5000:  # keep the dict from growing forever
        _last_verify.clear()
    _last_verify[key] = now

    doc = await jdb.get_chat(chat_id, scope=scope)
    if not doc or not doc.get("enabled"):
        return await query.answer("Is group ka request controller band hai 🚫", show_alert=True)

    all_required = list(doc.get("required") or [])
    required = _usable_required(all_required)
    manual = _manual_required(all_required)
    attempts = await jdb.bump_attempt(session["session_id"])

    missing = []
    for entry in required:
        if not await _member_ok(client, entry, user.id):
            missing.append(entry)

    title = doc.get("title") or "the group"

    # ── still missing something ──────────────────────────────────────────
    if missing:
        await jdb.bump(chat_id, "failed", 1, scope=scope)
        lines = "\n".join(f"  ✗ {_button_label(entry)}" for entry in missing[:10])
        await query.answer("❌ Abhi saari required chats join nahi ki 😥", show_alert=True)
        await _edit_card(
            query,
            f"❌ Verification incomplete!\n\n"
            f"Aapne ye join nahi kiya:\n{lines}\n\n"
            f"Neeche ke buttons se join karo, phir VERIFY AGAIN dabao 🙂\n"
            f"Attempt: {attempts}",
            build_verify_keyboard(chat_id, missing, session["session_id"], missing=True),
        )
        return

    # ── everything joined ────────────────────────────────────────────────
    await jdb.bump(chat_id, "verified", 1, scope=scope)
    await jdb.set_session_status(session["session_id"], "verified")

    if session.get("is_test"):
        extra = (
            f"\n🖐 {len(manual)} manual link(s) hain — real request par admin "
            f"unhe khud check karega."
            if manual
            else ""
        )
        await _edit_card(
            query,
            "🧪 TEST PASSED\n\n"
            f"Saare bot-checkable chats check ho gaye ✅{extra}\n"
            f"(Ye test card tha, isliye koi join request approve nahi ki.)",
        )
        return await query.answer("✅ Test pass! Sab checks theek hain", show_alert=True)

    auto = _flag(
        doc.get("auto_approve"),
        await sdb.get_setting(sdb.KEY_JOINREQ_AUTO, scope=scope, default=True),
    )
    notify = doc.get("notify_admins", True)

    # ── manual links → a human must approve ──────────────────────────────
    if manual:
        names = ", ".join(_button_label(entry) for entry in manual[:5])
        await _edit_card(
            query,
            "✅ Bot checks complete!\n\n"
            f"Aapne saare group/channel join kar liye.\n\n"
            f"🖐 Ab in links ko group admin khud check karega:\n{names}\n\n"
            "Admin approve karega — thoda wait karo 🥺",
        )
        if notify:
            await _notify_admins_markup(
                client,
                "🖐 MANUAL VERIFICATION NEEDED",
                f"👤 {user.first_name} ({_handle(user)})\n"
                f"🏘 {doc.get('title') or chat_id}\n"
                f"🆔 User ID: {user.id}\n\n"
                f"Bot-checkable chats: ✅ ho gaye\n"
                f"🖐 Manual links: {names}\n\n"
                f"User ne in links ko join kiya hai ya nahi, khud check karke "
                f"decide karo:",
                InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ Approve", callback_data=f"jr_ok:{session['session_id']}"
                            ),
                            InlineKeyboardButton(
                                "❌ Decline", callback_data=f"jr_no:{session['session_id']}"
                            ),
                        ]
                    ]
                ),
            )
        return await query.answer("✅ Verified! Admin manual check karega", show_alert=True)

    if not auto:
        await _edit_card(
            query,
            "✅ Verified!\n\n"
            f"Aapne {title} ke saare requirements complete kar liye.\n"
            "Ab admin aapki request approve karega — thoda wait karo 🥺",
        )
        if notify:
            await _notify_admins(
                client,
                "✅ USER VERIFIED (manual approve)",
                f"👤 {user.first_name} ({_handle(user)})\n"
                f"🏘 {doc.get('title') or chat_id}\n\n"
                f"Auto-approve OFF hai — Telegram se request approve karo.",
            )
        return await query.answer("✅ Verified! Admin approve karega", show_alert=True)

    if not await _approve(client, chat_id, user, scope=scope):
        await _edit_card(
            query,
            "⚠️ Verify ho gaya, par approve nahi hua.\n\n"
            "Bot ko group mein admin banao (Invite users + Manage chat), "
            "phir dobara try karo.",
        )
        if notify:
            await _notify_admins(
                client,
                "⚠️ APPROVE FAILED",
                f"👤 {user.first_name} ({_handle(user)})\n"
                f"🏘 {doc.get('title') or chat_id}\n\n"
                f"Bot ke admin rights check karo.",
            )
        return await query.answer("Approve fail ho gaya 😥", show_alert=True)

    await jdb.bump(chat_id, "approved", 1, scope=scope)
    await jdb.set_session_status(session["session_id"], "approved")

    await _edit_card(
        query,
        f"🎉 APPROVED!\n\n"
        f"{title} mein aapki request approve ho gayi.\n"
        f"Ab group kholo aur enjoy karo ✨",
    )
    await query.answer("🎉 Approved! Welcome", show_alert=True)

    if notify:
        await _notify_admins(
            client,
            "🎉 REQUEST APPROVED",
            f"👤 {user.first_name} ({_handle(user)})\n"
            f"🏘 {doc.get('title') or chat_id}\n\n"
            f"Verification complete → auto-approved.",
        )


# ══════════════════════════════════════════════════════════════════════════
#  Manual approval (bot referral links etc.)
# ══════════════════════════════════════════════════════════════════════════
async def _is_admin(user_id: int) -> bool:
    try:
        if int(user_id) == int(getattr(config, "OWNER_ID", 0) or 0):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(await adminsdb.is_admin(user_id))
    except Exception:  # noqa: BLE001
        return False


async def admin_decision(client, query: CallbackQuery, session_id: str, approve: bool):
    """The ✅ Approve / ❌ Decline buttons on the admin's manual-check alert."""
    scope = current_scope()
    user = query.from_user
    if user is None:
        return await query.answer("Kuch galat ho gaya 😥", show_alert=True)

    if not await _is_admin(user.id):
        return await query.answer("Sirf admin ye kar sakta hai 🚫", show_alert=True)

    session = await jdb.get_session(session_id, scope=scope)
    if session is None:
        return await query.answer("⏰ Session expire ho gaya", show_alert=True)

    status = session.get("status")
    if status in ("approved", "declined"):
        return await query.answer(
            f"Ye request pehle hi {status} ho chuki hai 🙂", show_alert=True
        )
    if session.get("is_test"):
        return await query.answer("🧪 Ye test card tha, koi request nahi", show_alert=True)
    # NOTE: no expiry check here — the admin may verify the manual link much
    # later, and only *they* can decide.

    chat_id = int(session.get("chat_id") or 0)
    applicant = int(session.get("user_id") or 0)
    doc = await jdb.get_chat(chat_id, scope=scope) or {}
    title = doc.get("title") or str(chat_id)

    if approve:
        ok = await _approve(
            client, chat_id, SimpleNamespace(id=applicant), scope=scope
        )
        if not ok:
            return await query.answer(
                "❌ Approve nahi hua — bot ke admin rights check karo", show_alert=True
            )
        await jdb.set_session_status(session_id, "approved")
        await jdb.bump(chat_id, "approved", 1, scope=scope)
        try:
            await client.send_message(
                applicant,
                f"🎉 Aapki request {title} ke liye approve ho gayi!\n"
                f"Group kholo aur enjoy karo ✨",
                disable_web_page_preview=True,
            )
        except Exception:  # noqa: BLE001
            pass
        verdict = f"✅ Approved by {user.first_name or 'admin'}"
        alert = "✅ Request approved"
    else:
        try:
            await client.decline_chat_join_request(chat_id, applicant)
        except RPCError as exc:  # noqa: BLE001
            message = str(getattr(exc, "MESSAGE", "") or exc).lower()
            if "hide_requester_missing" not in message and "not found" not in message:
                LOGGER.warning(f"joinreq decline failed for {chat_id}: {exc}")
                return await query.answer("❌ Decline nahi hua", show_alert=True)
        await jdb.set_session_status(session_id, "declined")
        await jdb.bump(chat_id, "failed", 1, scope=scope)
        try:
            await client.send_message(
                applicant,
                f"😕 Aapki join request {title} ke liye approve nahi hui.\n\n"
                f"🖐 Jo manual link diye gaye the wo complete nahi mile. "
                f"Group admin se baat karo.",
                disable_web_page_preview=True,
            )
        except Exception:  # noqa: BLE001
            pass
        verdict = f"❌ Declined by {user.first_name or 'admin'}"
        alert = "❌ Request declined"

    message = query.message
    try:
        base = (getattr(message, "text", None) or getattr(message, "caption", None) or "")
        if getattr(message, "photo", None) or getattr(message, "media", None):
            await message.edit_caption(caption=f"{base}\n\n{verdict}")
        else:
            await message.edit_text(f"{base}\n\n{verdict}")
    except Exception:  # noqa: BLE001
        pass
    return await query.answer(alert, show_alert=True)


# ══════════════════════════════════════════════════════════════════════════
#  Admin panel helpers
# ══════════════════════════════════════════════════════════════════════════
async def diagnose(client, chat_id: int, user_id: int) -> str:
    """Explain why VERIFY would/wouldn't work — straight from Telegram's view."""
    doc = await jdb.get_chat(chat_id)
    if not doc:
        return "❌ Ye group managed list mein nahi hai."

    lines = [f"🧪 CHECK — {doc.get('title') or chat_id}", ""]

    # 1. bot rights in the target chat
    try:
        me = client.me
        member = await client.get_chat_member(chat_id, me.id)
        priv = getattr(member, "privileges", None)
        if member.status != ChatMemberStatus.ADMINISTRATOR:
            lines.append("❌ Bot is group mein admin NAHI hai")
        elif not getattr(priv, "can_invite_users", False):
            lines.append("❌ Bot admin hai par 'Invite users' permission OFF hai")
        else:
            lines.append("✅ Bot admin hai (invite users + manage chat)")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"⚠️ Bot rights check fail: {type(exc).__name__}")

    # 2. every required entry
    required = doc.get("required") or []
    manual = _manual_required(required)
    usable = _usable_required(required)
    if not required:
        lines.append("ℹ️ Koi required chat nahi — auto-approve turant hoga")
    for entry in manual:
        lines.append(
            f"🖐 {_button_label(entry)} — manual link (admin verify karega, "
            f"bot check nahi kar sakta)"
        )
    for entry in required:
        label = _button_label(entry)
        if _is_manual(entry):
            continue
        try:
            cid_int = int(entry.get("chat_id") or 0)
        except Exception:  # noqa: BLE001
            cid_int = 0
        if entry not in usable:
            lines.append(f"⚠️ {label} — skip (bot/user id, check nahi ho sakta)")
            continue
        try:
            username = entry.get("username")
            target = f"@{str(username).lstrip('@')}" if username else cid_int
            chat = await client.get_chat(target)
            bot_member = await client.get_chat_member(target, client.me.id)
            ok_bot = bot_member.status not in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.BANNED,
            )
            joined = await _member_ok(client, entry, user_id)
            mark = "✅" if joined else "❌"
            note = "" if ok_bot else " (bot is chat mein nahi hai!)"
            chat_username = getattr(chat, "username", None)
            lines.append(
                f"{mark} {label} — @{chat_username}"
                if chat_username
                else f"{mark} {label}{note}"
            )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"❌ {label} — {type(exc).__name__} (id galat ya bot ko access nahi)")

    lines.append("")
    lines.append("ℹ️ Neeche wala card aapko DM mein bheja ja raha hai — usme VERIFY dabao.")
    return "\n".join(lines)


async def send_test_card(client, chat_id: int, user) -> bool:
    """DM the admin a real verification card, marked as a test."""
    user = _u(user)
    doc = await jdb.get_chat(chat_id)
    if not doc:
        return False
    all_required = list(doc.get("required") or [])
    required = _usable_required(all_required)
    session = await jdb.create_session(user.id, chat_id, minutes=30, is_test=True)
    text = await _fill(
        doc.get("welcome")
        or await sdb.get_setting(sdb.KEY_JOINREQ_WELCOME, default=None)
        or DEFAULT_WELCOME,
        name=user.first_name,
        chat=doc.get("title") or "is group",
        count=len(all_required),
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        user_id=user.id,
        chat_title=doc.get("title"),
        chat_username=doc.get("username"),
    )
    text = "🧪 TEST CARD — ye sirf aapke liye hai, kisi ko approve nahi karega.\n\n" + text
    try:
        await client.send_message(
            user.id,
            text,
            reply_markup=build_verify_keyboard(chat_id, all_required, session["session_id"]),
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq: test card failed for {user.id}: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════
#  Helpers shared with the admin panel
# ══════════════════════════════════════════════════════════════════════════
_REF_ID = re.compile(r"^-?\d{5,}$")
_REF_USERNAME = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]{3,})|^@([A-Za-z0-9_]{3,})$")
# A private group's "Copy Link" looks like t.me/c/<internal>/<msg>: the id is
# right there, so this one always works.
_REF_PRIVATE = re.compile(
    r"(?:https?://)?(?:www\.)?t\.me/c/(\d{5,})(?:/\d+)?", re.IGNORECASE
)
# Private invite links: t.me/+hash, t.me/joinchat/hash (and the tg:// variant).
_REF_INVITE = re.compile(
    r"(?:https?://)?(?:www\.)?t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]{6,})"
    r"|tg://join\?invite=([A-Za-z0-9_-]{6,})",
    re.IGNORECASE,
)
# t.me/<name><tail> — used to spot bot referral links (?start=…).
_REF_TME = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me/([A-Za-z0-9_]{3,})([^\s]*)",
    re.IGNORECASE,
)
_REF_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)


async def _as_group(chat):
    """Reject anything that is not a group/channel."""
    if chat is None:
        return None, "chat nahi mila ❌"
    if chat.type == ChatType.PRIVATE:
        return None, "ye toh koi user hai 😅 group/channel do"
    return chat, None


def _chat_from_raw(client, raw_chat):
    """Raw TL chat (inside ``ChatInviteAlready``) -> panel-friendly chat."""
    try:
        parsed = PyroChat._parse_chat(client, raw_chat)
        if parsed is not None:
            return parsed
    except Exception:  # noqa: BLE001 - optional TL fields can be None
        pass

    raw_id = int(getattr(raw_chat, "id", 0) or 0)
    if isinstance(raw_chat, raw_types.Channel):
        raw_id = int(f"-100{abs(raw_id)}")
        chat_type = ChatType.SUPERGROUP
    else:
        chat_type = ChatType.GROUP
    return SimpleNamespace(
        id=raw_id,
        title=getattr(raw_chat, "title", "") or "",
        username=getattr(raw_chat, "username", None),
        type=chat_type,
    )


async def _invite_via_userbots(invite_hash: str):
    """
    Ask the connected userbots what this invite link points to.

    A **bot** account is not allowed to call ``messages.CheckChatInvite`` (nor
    ``ImportChatInvite``): Telegram answers ``BOT_METHOD_INVALID``. A normal
    user account can, and when that account is already a member the reply
    carries the full chat - so a userbot that sits in the group resolves the
    link for us.
    """
    try:
        from Myra.userbot.storage import accounts as ub_accounts
    except Exception:  # noqa: BLE001
        return None

    for account_id, data in list(ub_accounts.items()):
        client = data.get("client")
        if client is None or (data.get("status") or "") != "CONNECTED":
            continue
        try:
            invite = await client.invoke(
                raw_functions.messages.CheckChatInvite(hash=invite_hash)
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(f"joinreq: {account_id} could not check invite: {exc}")
            continue
        raw_chat = getattr(invite, "chat", None)
        if raw_chat is None:
            continue  # not a member of that group
        chat = _chat_from_raw(client, raw_chat)
        if getattr(chat, "id", 0):
            LOGGER.info(f"joinreq: invite link resolved through userbot {account_id}")
            return chat
    return None


async def resolve_invite(client, invite_hash: str):
    """
    Resolve a private ``t.me/+hash`` invite link into a chat.

    The bot itself can never do this (Telegram forbids invite-hash methods for
    bots), so we try the connected userbots first. When none of them is in the
    group we return instructions that do work: ``/id`` inside the group, a
    forwarded message, or the ``t.me/c/...`` copy-link.
    """
    chat = await _invite_via_userbots(invite_hash)
    if chat is not None:
        return await _as_group(chat)

    return None, (
        "Ye private invite link hai 🔗\n\n"
        "Telegram bots ko invite link padhne ki permission nahi deta, isliye "
        "main is link se group id nahi nikal sakta 😅\n\n"
        "Koi ek tarika chuno:\n"
        "1️⃣ Group ke andar /id bhejo → jo -100… id aaye wo yahan paste karo\n"
        "2️⃣ Group ke kisi message ko yahan forward karo\n"
        "3️⃣ Message pe Copy Link → t.me/c/… link yahan bhejo"
    )


async def invite_for(client, chat) -> str:
    """Best-effort public join link for a required chat (used in the DM buttons)."""
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}"
    try:
        return await client.export_chat_invite_link(chat.id)
    except Exception:  # noqa: BLE001
        return ""


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
    invite_hash = None
    if _REF_ID.match(text):
        target = int(text)
    else:
        m = _REF_PRIVATE.search(text)
        if m:
            target = int(f"-100{m.group(1)}")
        else:
            m = _REF_INVITE.search(text)
            if m:
                invite_hash = m.group(1) or m.group(2)
            else:
                m = _REF_USERNAME.search(text)
                if not m:
                    return None, "id, @username ya t.me link chahiye 🙂"
                target = m.group(1) or m.group(2)
                if target.lower() in ("joinchat", "addstickers", "share", "proxy", "socks"):
                    return None, "koi channel/group ka @username ya link do 🙂"

    if invite_hash:
        return await resolve_invite(client, invite_hash)

    try:
        chat = await client.get_chat(target)
    except Exception as exc:  # noqa: BLE001
        return None, f"nahi mila ❌ ({type(exc).__name__})"

    if chat.type == ChatType.PRIVATE:
        return None, "ye toh koi user hai 😅 group/channel do"
    return chat, None


__all__ = [
    "DEFAULT_WELCOME",
    "DEFAULT_TIMEOUT_MIN",
    "COUNTERS",
    "VERIFY_TTL",
    "MANUAL_TYPES",
    "build_verify_keyboard",
    "on_join_request",
    "verify_and_approve",
    "admin_decision",
    "manual_entry",
    "diagnose",
    "send_test_card",
    "resolve_chat",
    "resolve_invite",
    "invite_for",
]
