"""
🛡 Admin Control Panel — everything from one inline keyboard.

    /admin            → open the panel
    /broadcast        → jump straight to the broadcast section

Sections
--------
📢 Broadcast      · from the bot, from **all** userbots, or from one userbot →
                    groups only / DMs only / groups + DMs
⏰ AutoChat       · time-to-time messages: separate timings for the bot and
                    for the userbots, plus a "send now" test button
👥 Userbots       · opens the existing /useraccounts dashboard
📁 Import Sessions· add userbots by uploading `.session` / `.zip` files
🧬 Clones         · (main bot only) every cloned bot, start / stop / delete
🧠 AI & Keys      · hybrid brain status, key reload
🎛 Reply Behaviour· speed + reply chance for every userbot
🧹 Clean DB       · purge links / promo text from the training data
📊 Stats          · users, chats, trained replies, keys, AutoChat status

Owner + authorized admins only. Every panel belongs to the bot that is being
talked to: a cloned bot's panel only ever shows that clone's own admins,
userbots, groups, DMs and settings.
"""

import asyncio
import time
from uuid import uuid4

from pyrogram import filters
from pyrogram.enums import ChatType
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from Myra import Myra, LOGGER
from Myra.database.admins import is_admin
from Myra.database import autochat as ac
from Myra.database import joinreq as jdb
from Myra.database import settings as ai_settings
from Myra.database import training
from Myra.database.bots import bot_info
from Myra.database.chats import get_served_chats
from Myra.database.scope import current_scope, global_scope
from Myra.database.users import get_served_users
from Myra.userbot.storage import accounts as ub_accounts
from Myra.userbot.manager import userbot_manager
from Myra.services import broadcast as bc
from Myra.services import joinreq as joinreq_service
from Myra.services.autochat import autochat_service

# ── ephemeral state ────────────────────────────────────────────────────────
# user_id -> {"kind": "broadcast"|"interval", ...}
pending_input: dict = {}
# token -> {"stop": bool}
active_broadcasts: dict = {}


async def _safe(coro, default=None):
    """
    Await *coro*, returning *default* if it fails.

    The panels gather a dozen independent database reads at once; one broken
    collection must not blank the whole panel, and ``asyncio.gather`` would
    otherwise re-raise the first exception.
    """
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"admin panel query failed: {exc}")
        return default

SPEED_PRESETS = ((0, "⚡ 0s"), (5000, "⏱ 5s"), (10000, "🐢 10s"), (30000, "🕐 30s"))
INTERVAL_PRESETS = (60, 300, 900, 1800, 3600, 10800, 21600)


def _my_accounts() -> dict:
    """
    Every userbot account in the shared pool.

    The admin surface is global, so the panel manages one common set of
    userbots no matter which bot opened it.
    """
    return dict(ub_accounts)


def _bot_title() -> str:
    """``Name (@username)`` of the bot of the current scope."""
    info = bot_info()
    name = info.get("name") or "Myra"
    username = info.get("username")
    return f"{name} (@{username})" if username else name


def _interval_label(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours = seconds / 3600
    return f"{hours:g}h"


def _back(target="ad_main"):
    return InlineKeyboardButton("↩️ Back", callback_data=target)


# ══════════════════════════════════════════════════════════════════════════
#  Panels
# ══════════════════════════════════════════════════════════════════════════
async def _main_panel() -> tuple[str, InlineKeyboardMarkup]:
    from Myra.ai.brain import ai_status
    from Myra.services import userstats
    from Myra.services.clones import clone_manager

    # One gather instead of ~12 sequential round trips. Every value below is
    # independent and each Atlas hop costs 50-150 ms, which is what made the
    # panel take 1.5-2.5 s before rendering its first character.
    (
        bot_auto,
        ub_auto,
        db,
        chats_list,
        users_list,
        audience,
        clones_list,
        bot_interval,
        bot_targets,
        ub_interval,
        ub_targets,
    ) = await asyncio.gather(
        _safe(ac.get_bot_enabled(), False),
        _safe(ac.get_ub_enabled(), False),
        _safe(training.get_stats(), {}),
        _safe(get_served_chats(), []),
        _safe(get_served_users(), []),
        _safe(userstats.collect(), {}),
        _safe(clone_manager.list_clones(), []),
        _safe(ac.get_bot_interval(), 0),
        _safe(ac.get_bot_targets(), "both"),
        _safe(ac.get_ub_interval(), 0),
        _safe(ac.get_ub_targets(), "both"),
    )

    ai = ai_status()
    chats = len(chats_list)
    users = len(users_list)
    totals = (audience or {}).get("totals", {})
    my_accounts = _my_accounts()

    text = (
        "🛡 **ADMIN CONTROL PANEL**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 **Bot:** {_bot_title()}\n"
        f"👥 **Connected userbots:** `{len(my_accounts)}`\n"
        f"💬 **Groups:** `{chats}`\n"
        f"✉️ **DM users — bot:** `{users}` · **userbots:** `{totals.get('userbot_dm', 0)}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ **AutoChat — bot:** {'🟢 ON' if bot_auto else '🔴 OFF'}"
        f" · `{ac.human_interval(bot_interval)}`"
        f" · {ac.label_for_target(bot_targets)}\n"
        f"⏰ **AutoChat — userbots:** {'🟢 ON' if ub_auto else '🔴 OFF'}"
        f" · `{ac.human_interval(ub_interval)}`"
        f" · {ac.label_for_target(ub_targets)}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📚 **Trained replies:** `{db.get('total', 0)}`\n"
        f"🔑 **API keys:** `{ai.get('active', 0)}/{ai.get('total', 0)}`\n"
        f"🧠 **Mode:** {'Hybrid (DB + AI)' if ai.get('active') else 'Offline (DB only)'}\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

    rows = [
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="ad_bc"),
            InlineKeyboardButton("⏰ AutoChat", callback_data="ad_ac"),
        ],
        [
            InlineKeyboardButton("🚪 Group Requests", callback_data="ad_jr"),
            InlineKeyboardButton("📊 Stats", callback_data="ad_stats"),
        ],
        [
            InlineKeyboardButton("👥 Userbots", callback_data="ad_userbots"),
            InlineKeyboardButton("📁 Import Sessions", callback_data="ad_import"),
        ],
        [
            InlineKeyboardButton("🧠 AI & Keys", callback_data="ad_ai"),
            InlineKeyboardButton("🎛 Reply Behaviour", callback_data="ad_behaviour"),
        ],
    ]
    clones = len(clones_list)
    rows.append([
        InlineKeyboardButton(f"🧬 Clones ({clones})", callback_data="ad_clones"),
        InlineKeyboardButton("🧹 Clean DB", callback_data="ad_clean"),
    ])
    rows.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="ad_main"),
        InlineKeyboardButton("❌ Close", callback_data="ad_close"),
    ])
    return text, InlineKeyboardMarkup(rows)


async def _broadcast_sender_panel() -> tuple[str, InlineKeyboardMarkup]:
    accounts = _my_accounts()
    bot_name = bot_info().get("name") or "Main Bot"
    rows = [[InlineKeyboardButton(f"🤖 {bot_name}", callback_data="ad_bc_s:bot")]]
    if accounts:
        rows.append([InlineKeyboardButton("👥 All Userbots", callback_data="ad_bc_s:userbots")])
        for account_id, data in list(accounts.items())[:12]:
            name = data.get("name") or account_id
            status = "🟢" if data.get("status") == "CONNECTED" else "🔴"
            rows.append([
                InlineKeyboardButton(f"{status} {name}", callback_data=f"ad_bc_s:{account_id}")
            ])
    else:
        rows.append([InlineKeyboardButton("👥 All Userbots (none connected)", callback_data="ad_noop")])

    rows.append([_back()])
    text = (
        "📢 **BROADCAST — STEP 1/3**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "**Kis se bhejna hai?** Choose the sender:\n\n"
        "🤖 **Main Bot** — bot ke naam se\n"
        "👥 **All Userbots** — sabhi connected userbot mil kar "
        "(targets round-robin me bat jaate hain)\n"
        "👤 **Single Userbot** — sirf usi account se\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    return text, InlineKeyboardMarkup(rows)


async def _broadcast_target_panel(sender: str) -> tuple[str, InlineKeyboardMarkup]:
    if sender == "bot":
        label = f"🤖 {bot_info().get('name') or 'Main Bot'}"
    elif sender == "userbots":
        label = "👥 All Userbots"
    else:
        account = ub_accounts.get(sender, {})
        label = f"👤 {account.get('name') or sender}"

    text = (
        "📢 **BROADCAST — STEP 2/3**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 **Sender:** {label}\n\n"
        "**Kaha bhejna hai?** Choose the target:\n\n"
        "💬 **Groups only** — bot/userbot jin groups me hai\n"
        "✉️ **DMs only** — jin users ne chat ki hai / dialogs me hain\n"
        "🌐 **Groups + DMs** — dono\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Groups only", callback_data=f"ad_bc_t:{sender}:groups")],
        [InlineKeyboardButton("✉️ DMs only", callback_data=f"ad_bc_t:{sender}:dm")],
        [InlineKeyboardButton("🌐 Groups + DMs", callback_data=f"ad_bc_t:{sender}:both")],
        [_back("ad_bc")],
    ])
    return text, markup


def _broadcast_prompt(sender: str, target: str) -> tuple[str, InlineKeyboardMarkup]:
    label = {"groups": "💬 Groups only", "dm": "✉️ DMs only", "both": "🌐 Groups + DMs"}[target]
    text = (
        "📢 **BROADCAST — STEP 3/3**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 **Sender:** `{sender}`\n"
        f"🎯 **Target:** {label}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "**Ab message bhejo** — jo text bhejoge wahi broadcast hoga.\n\n"
        "• Text message → wahi text jaayega\n"
        "• Kisi message ko **reply** karke bhejo → media + caption jaayega\n"
        "• Cancel karne ke liye neeche button dabao ya `/cancel` bhejo\n\n"
        "⏳ *Broadcast shuru hone ke baad ek Stop button milega.*"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Cancel", callback_data="ad_bc_cancel")],
    ])
    return text, markup


async def _autochat_panel() -> tuple[str, InlineKeyboardMarkup]:
    bot_on, ub_on, bot_interval, bot_targets, ub_interval, ub_targets = await asyncio.gather(
        _safe(ac.get_bot_enabled(), False),
        _safe(ac.get_ub_enabled(), False),
        _safe(ac.get_bot_interval(), 0),
        _safe(ac.get_bot_targets(), "both"),
        _safe(ac.get_ub_interval(), 0),
        _safe(ac.get_ub_targets(), "both"),
    )
    status = autochat_service.status()

    text = (
        "⏰ **AUTOCHAT — TIME-TO-TIME MESSAGES**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Bot aur userbots apne-apne timing par khud messages bhejte rahenge "
        "(groups / DMs), ekdum insaan jaise.\n\n"
        f"🤖 **Main Bot:** {'🟢 ON' if bot_on else '🔴 OFF'}\n"
        f"   ↳ har `{ac.human_interval(bot_interval)}`"
        f" · {ac.label_for_target(bot_targets)}\n"
        f"👥 **All Userbots:** {'🟢 ON' if ub_on else '🔴 OFF'}\n"
        f"   ↳ har `{ac.human_interval(ub_interval)}`"
        f" · {ac.label_for_target(ub_targets)}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📨 **Messages sent:** `{status.get('sent_total', 0)}`"
        f" · ❌ failed `{status.get('failed_total', 0)}`\n"
        f"🐾 **Next bot message in:** `{status.get('bot_next_in', 0)}s`\n"
        + (f"⏳ **Last flood-wait:** `{status.get('last_flood_wait')}s`\n"
           if status.get("last_flood_wait") else "")
        + "━━━━━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ Message ka text trained database se random uthaya jaata hai "
        "(girl voice me), taaki natural lage."
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 Bot Settings", callback_data="ad_ac_bot"),
            InlineKeyboardButton("👥 Userbot Settings", callback_data="ad_ac_ub"),
        ],
        [
            InlineKeyboardButton("⚡ Every 1m", callback_data="ad_ac_preset:900"),
            InlineKeyboardButton("🕐 Every 15m", callback_data="ad_ac_preset:900000"),
        ],
        [
            InlineKeyboardButton("▶️ Send now (bot)", callback_data="ad_ac_now:bot"),
            InlineKeyboardButton("▶️ Send now (userbots)", callback_data="ad_ac_now:ub"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="ad_ac"),
            _back(),
        ],
    ])
    return text, markup


async def _autochat_side_panel(scope: str) -> tuple[str, InlineKeyboardMarkup]:
    if scope == "bot":
        enabled, interval, targets = await asyncio.gather(
            _safe(ac.get_bot_enabled(), False),
            _safe(ac.get_bot_interval(), 0),
            _safe(ac.get_bot_targets(), "both"),
        )
        title = f"🤖 AUTOCHAT — {bot_info().get('name') or 'MAIN BOT'}"
        note = "Bot sirf unhi groups/DMs me bhejega jaha wo already hai."
    else:
        enabled, interval, targets = await asyncio.gather(
            _safe(ac.get_ub_enabled(), False),
            _safe(ac.get_ub_interval(), 0),
            _safe(ac.get_ub_targets(), "both"),
        )
        title = "👥 AUTOCHAT — ALL USERBOTS"
        note = "Ye global setting hai — saare userbots par lagegi (jo connected hain)."

    overrides = 0
    if scope == "ub":
        for data in _my_accounts().values():
            if data.get("auto_interval_ms") is not None or data.get("auto_enabled") is not None:
                overrides += 1
    if scope == "ub" and overrides:
        note += f"\n⚠️ `{overrides}` account(s) have their own interval/state."

    text = (
        f"⏰ **{title}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔌 **Status:** {'🟢 ON' if enabled else '🔴 OFF'}\n"
        f"⏱ **Interval:** `{ac.human_interval(interval)}` ({interval} ms)\n"
        f"🎯 **Targets:** {ac.label_for_target(targets)}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{note}\n\n"
        "**Interval** = average gap (har message ke beech ±40% jitter hota hai).\n"
        "**Targets** = kiss jagah bhejna hai."
    )

    toggle_text = "🔴 Turn OFF" if enabled else "🟢 Turn ON"
    rows = [
        [InlineKeyboardButton(toggle_text, callback_data=f"ad_ac_toggle:{scope}")],
        [
            InlineKeyboardButton(_interval_label(s), callback_data=f"ad_ac_iv:{scope}:{s * 1000}")
            for s in INTERVAL_PRESETS[:3]
        ],
        [
            InlineKeyboardButton(_interval_label(s), callback_data=f"ad_ac_iv:{scope}:{s * 1000}")
            for s in INTERVAL_PRESETS[3:6]
        ],
        [
            InlineKeyboardButton(_interval_label(INTERVAL_PRESETS[6]),
                                 callback_data=f"ad_ac_iv:{scope}:{INTERVAL_PRESETS[6] * 1000}"),
            InlineKeyboardButton("⚙️ Custom", callback_data=f"ad_ac_custom:{scope}"),
        ],
        [InlineKeyboardButton(f"🎯 Target: {ac.label_for_target(targets)}",
                              callback_data=f"ad_ac_tg:{scope}")],
        [_back("ad_ac")],
    ]
    return text, InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════════
#  🚪 Group Request Controller
# ══════════════════════════════════════════════════════════════════════════
def _jr_count(chats: list, key: str) -> int:
    total = 0
    for chat in chats:
        try:
            total += int((chat.get("counters") or {}).get(key) or 0)
        except Exception:  # noqa: BLE001
            pass
    return total


async def _jr_panel() -> tuple[str, InlineKeyboardMarkup]:
    enabled, auto, welcome, chats = await asyncio.gather(
        _safe(ai_settings.get_setting(ai_settings.KEY_JOINREQ_ENABLED, default=False), False),
        _safe(ai_settings.get_setting(ai_settings.KEY_JOINREQ_AUTO, default=True), True),
        _safe(ai_settings.get_setting(ai_settings.KEY_JOINREQ_WELCOME, default=None), None),
        _safe(jdb.list_managed(), []),
    )

    req_total = 0
    for chat in chats:
        req_total += len(chat.get("required") or [])

    preview = await joinreq_service._fill(
        welcome or joinreq_service.DEFAULT_WELCOME,
        name="Naam",
        chat="Group",
        count=3,
        first_name="Naam",
        chat_title="Group",
    )
    if len(preview) > 130:
        preview = preview[:130] + "…"

    text = (
        "🚪 **GROUP REQUEST CONTROLLER**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Jo group/channel **Ask to join** mode par hain, unki request yeh khud "
        "handle karta hai — user ko DM card, must-join list aur **VERIFY** "
        "button, phir seedha approve.\n\n"
        f"🚦 **Status:** {'🟢 ON' if enabled else '🔴 OFF'}\n"
        f"🤖 **Auto-approve:** {'✅ ON' if auto else '❌ OFF — admin approve karega'}\n"
        f"   ↳ ON + 0 required link = request **turant** approve (DM/VERIFY ki "
        f"zarurat nahi)\n"
        f"🏘 **Managed groups:** `{len(chats)}` · 🔗 required links `{req_total}`\n"
        f"📨 **Requests:** `{_jr_count(chats, 'pending')}`"
        f" · ✅ verified `{_jr_count(chats, 'verified')}`"
        f" · 🚪 approved `{_jr_count(chats, 'approved')}`"
        f" · ❌ failed `{_jr_count(chats, 'failed')}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ **Welcome card:**\n"
        f"`{preview}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ Har managed group mein bot ko **admin** hona chahiye "
        "(Invite users + Manage chat). Setup ke baad us group ko kholo → "
        "🧪 **Must-join Setup → 🧪 Test** se poora flow khud check kar lo."
    )
    rows = [
        [
            InlineKeyboardButton(f"🚦 {'🟢 ON' if enabled else '🔴 OFF'}", callback_data="ad_jr_tog"),
            InlineKeyboardButton(f"🤖 Auto {'✅' if auto else '❌'}", callback_data="ad_jr_auto"),
        ],
        [
            InlineKeyboardButton("➕ Add Group", callback_data="ad_jr_add"),
            InlineKeyboardButton(f"🏘 Managed ({len(chats)})", callback_data="ad_jr_chats"),
        ],
        [
            InlineKeyboardButton("✨ Welcome Message", callback_data="ad_jr_wel"),
            InlineKeyboardButton("🔁 Reset Stats", callback_data="ad_jr_reset"),
        ],
        [_back()],
    ]
    return text, InlineKeyboardMarkup(rows)


async def _jr_chats_panel() -> tuple[str, InlineKeyboardMarkup]:
    chats = await jdb.list_managed()
    if not chats:
        return (
            "🏘 **MANAGED GROUPS**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Abhi koi group add nahi hua.\n\n"
            "**Setup ek baar karna hai:**\n"
            "1️⃣ Group mein bot ko **admin** banao "
            "(Invite users + Manage chat)\n"
            "2️⃣ Group settings → **Members → Join requests** ON karo\n"
            "3️⃣ Wapas yahan aakar **➕ Add Group** dabao\n\n"
            "Phir jo bhi banda *Ask to join* karega, usse DM card jaayega "
            "aur verify hote hi request approve ho jaayegi 🎉",
            InlineKeyboardMarkup([[_back("ad_jr")]]),
        )

    lines = ["🏘 **MANAGED GROUPS**", "━━━━━━━━━━━━━━━━━━━━━━"]
    rows = []
    for index, chat in enumerate(chats[:28], 1):
        title = (chat.get("title") or str(chat.get("chat_id")))[:40]
        uname = f"@{chat['username']}" if chat.get("username") else ""
        counters = chat.get("counters") or {}
        lines.append(
            f"`{index}.` **{title}** {uname}\n"
            f"    📨 `{counters.get('pending', 0)}` · ✅ `{counters.get('verified', 0)}`"
            f" · 🚪 `{counters.get('approved', 0)}`"
            f" · 🔗 `{len(chat.get('required') or [])}`"
        )
        rows.append([
            InlineKeyboardButton("🧪 Must-join Setup", callback_data=f"ad_jr_set:{chat['chat_id']}"),
            InlineKeyboardButton("❌ Remove", callback_data=f"ad_jr_del:{chat['chat_id']}"),
        ])
    if len(chats) > 28:
        lines.append(f"\n… aur `{len(chats) - 28}` groups.")
    rows.append([_back("ad_jr")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _jr_req_panel(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await jdb.get_chat(chat_id)
    if not chat:
        return "❌ Ye group ab managed list mein nahi hai.", InlineKeyboardMarkup([[_back("ad_jr_chats")]])

    required = chat.get("required") or []
    usable = joinreq_service._usable_required(required)
    skipped = len(required) - len(usable)
    title = chat.get("title") or str(chat_id)
    uname = f"@{chat['username']}" if chat.get("username") else ""

    auto_doc = chat.get("auto_approve")
    auto_global = await ai_settings.get_setting(ai_settings.KEY_JOINREQ_AUTO, default=True)
    auto = auto_global if auto_doc is None else bool(auto_doc)
    auto_src = "global" if auto_doc is None else "is group"
    try:
        timeout = int(chat.get("timeout_minutes") or jdb.DEFAULT_TIMEOUT_MIN)
    except Exception:  # noqa: BLE001
        timeout = jdb.DEFAULT_TIMEOUT_MIN
    notify = chat.get("notify_admins", True)
    welcome = chat.get("welcome")
    counters = chat.get("counters") or {}

    lines = [
        f"🧪 **MUST-JOIN SETUP — {title}** {uname}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🤖 **Auto-approve:** {'✅ ON' if auto else '❌ OFF'} _{auto_src}_",
        f"⏱ **Session timeout:** `{timeout} min`",
        f"🔔 **Admin alerts:** {'✅ ON' if notify else '❌ OFF'}",
        f"✨ **Welcome:** {'custom' if welcome else 'default'}",
        f"📨 Requests: `{counters.get('pending', 0)}` · ✅ `{counters.get('verified', 0)}`"
        f" · 🚪 `{counters.get('approved', 0)}` · ❌ `{counters.get('failed', 0)}`",
        "",
        "VERIFY dabne se pehle user ko ye sab join karna hoga:",
        "",
    ]
    if not required:
        lines.append("_Abhi kuch add nahi hua — user turant verify ho jaayega._")
    else:
        for index, entry in enumerate(required, 1):
            label = entry.get("label") or entry.get("username") or str(entry.get("chat_id"))
            handle = f"@{entry['username']}" if entry.get("username") else "private"
            bad = " ⚠️" if entry not in usable else ""
            lines.append(f"`{index}.` **{label}** — {handle}{bad}")
    if skipped:
        lines.append(
            f"\n⚠️ `{skipped}` entry skip ho rahi hai (bot/user id — membership check "
            f"nahi ho sakta). Woh hata do."
        )

    rows = [
        [
            InlineKeyboardButton(
                f"🤖 Auto {'✅' if auto else '❌'}", callback_data=f"ad_jr_cauto:{chat_id}"
            ),
            InlineKeyboardButton(f"⏱ {timeout}m", callback_data=f"ad_jr_ctime:{chat_id}"),
        ],
        [
            InlineKeyboardButton(
                f"🔔 Alerts {'✅' if notify else '❌'}", callback_data=f"ad_jr_cnotify:{chat_id}"
            ),
            InlineKeyboardButton("✨ Welcome", callback_data=f"ad_jr_cwel:{chat_id}"),
        ],
        [InlineKeyboardButton("➕ Add Channel/Group", callback_data=f"ad_jr_radd:{chat_id}")],
    ]
    for index, entry in enumerate(required):
        label = (entry.get("label") or entry.get("username") or str(entry.get("chat_id")))[:18]
        prefix = "⚠️" if entry not in usable else "🗑"
        rows.append([
            InlineKeyboardButton(f"{prefix} {label}", callback_data=f"ad_jr_rdel:{chat_id}:{index}"),
        ])
    rows.append([
        InlineKeyboardButton("🧪 Test (DM card)", callback_data=f"ad_jr_test:{chat_id}"),
    ])
    rows.append([_back("ad_jr_chats")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _ai_panel() -> tuple[str, InlineKeyboardMarkup]:
    from Myra.ai.brain import ai_status, provider_order

    ai = ai_status()
    lines = [
        "🧠 **AI & API KEYS**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🔌 **AI enabled:** `{ai.get('enabled')}` · 🔑 **Active keys:** `{ai.get('active')}/{ai.get('total')}`",
        f"📚 **DB-first:** `{ai.get('db_first')}` · 🎓 **Auto-train:** `{ai.get('auto_train')}`",
        f"🎯 **Threshold:** `{ai.get('threshold')}` · 🔀 **Order:** `{' → '.join(ai.get('order') or [])}`",
        "",
    ]

    providers = ai.get("providers") or {}
    for name in provider_order():
        info = providers.get(name) or {}
        lines.append(
            f"**{name.upper()}** — `{info.get('active')}`/`{info.get('total')}` active · "
            f"model `{info.get('model')}`"
        )
    lines.append("")

    for item in (ai.get("details") or [])[:14]:
        icon = "🟢" if item["status"] == "active" else "🔴"
        lines.append(
            f"{icon} [{item.get('provider', '?')}] `{item['key']}` — "
            f"{item['status']} (uses {item['uses']})"
        )
    if not ai.get("details"):
        lines.append("ℹ️ *No keys — bot offline DB-only mode me chal raha hai.*")
    lines += [
        "",
        "➕ Gemini key: `/addkey gemini <KEY>`",
        "➕ OpenAI/ChatGPT key: `/addkey openai <KEY> [base_url]`",
        "🗑 Hatane ke liye: `/delkey [provider] <masked|index>`",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Reload keys", callback_data="ad_ai_reload")],
        [_back()],
    ])
    return "\n".join(lines), markup


async def _behaviour_panel() -> tuple[str, InlineKeyboardMarkup]:
    delay, group_chance, dm_chance = await asyncio.gather(
        _safe(ai_settings.get_reply_delay(), 0),
        _safe(ai_settings.get_group_chance(), 100),
        _safe(ai_settings.get_private_chance(), 100),
    )

    text = (
        "🎛 **REPLY BEHAVIOUR (all userbots)**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ **Reply delay:** `{delay} ms` ({delay / 1000:g}s)\n"
        f"🎲 **Group chance:** `{group_chance:g}%`\n"
        f"✉️ **DM chance:** `{dm_chance:g}%`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Delay = reply se pehle kitna rukna hai.\n"
        "Chance = kitne % messages ka reply dena hai.\n"
        "Per-userbot override `/useraccounts` se kar sakte ho."
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(text, callback_data=f"ad_bev_delay:{ms}")
            for ms, text in SPEED_PRESETS
        ],
        [
            InlineKeyboardButton("⚙️ Custom delay", callback_data="ad_bev_custom"),
        ],
        [
            InlineKeyboardButton("🎲 Group chance", callback_data="ad_bev_chance:group"),
            InlineKeyboardButton("🎲 DM chance", callback_data="ad_bev_chance:private"),
        ],
        [_back()],
    ])
    return text, markup


async def _stats_panel() -> tuple[str, InlineKeyboardMarkup]:
    from Myra.services import userstats

    db, chats_list, users_list, audience = await asyncio.gather(
        _safe(training.get_stats(), {}),
        _safe(get_served_chats(), []),
        _safe(get_served_users(), []),
        _safe(userstats.collect(), {}),
    )
    status = autochat_service.status()
    chats = len(chats_list)
    users = len(users_list)
    totals = (audience or {}).get("totals", {})

    text = (
        "📊 **BOT STATISTICS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 **Bot:** {_bot_title()}\n"
        f"💬 **Groups:** `{chats}`\n"
        f"✉️ **DM users — bot:** `{users}` · **userbots:** `{totals.get('userbot_dm', 0)}`\n"
        f"👥 **Userbots connected:** `{len(_my_accounts())}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📚 **Trained replies:** `{db.get('total', 0)}`\n"
        f"💬 **Text:** `{db.get('texts', 0)}` · 🎨 **Stickers:** `{db.get('stickers', 0)}`\n"
        f"💾 **DB size:** `{training.human_bytes(db.get('collection_bytes', 0))}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ **AutoChat sent:** `{status.get('sent_total', 0)}`"
        f" · failed `{status.get('failed_total', 0)}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        + userstats.render(audience)
        + "\n━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="ad_stats"),
            InlineKeyboardButton("📡 Re-scan audience", callback_data="ad_stats_refresh"),
        ],
        [_back()],
    ])
    return text, markup


def _stop_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Stop broadcast", callback_data=f"ad_bc_stop:{token}")],
    ])


async def _import_panel() -> tuple[str, InlineKeyboardMarkup]:
    my_accounts = _my_accounts()
    text = (
        "📁 **IMPORT USERBOTS (.session)**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "**Pyrogram** aur **Telethon** dono ke `.session` file chalte hain — "
        "format khud detect ho jata hai.\n\n"
        "Bahut se accounts ek saath add karne ke liye **ZIP** bhejo "
        "(zip ke andar sirf `.session` files).\n\n"
        "**Kaise karein:**\n"
        "1️⃣ Is private chat me `.session` file ya `.zip` bhej do\n"
        "2️⃣ Bot sabko login karke userbot list me add kar dega\n"
        "3️⃣ Uploaded message safety ke liye delete kar diya jaayega\n\n"
        f"👥 **Abhi connected:** `{len(my_accounts)}` account(s)\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Ek Telegram account sirf ek hi bot (main ya kisi clone) me add ho sakta hai."
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Userbot Dashboard", callback_data="ad_userbots")],
        [_back()],
    ])
    return text, markup


async def _clones_panel() -> tuple[str, InlineKeyboardMarkup]:
    from Myra.services.clones import clone_manager

    clones = await clone_manager.list_clones()
    lines = [
        "🧬 **CLONED BOTS**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Koi bhi user `/clone <bot_token>` se apna bot host kar sakta hai. "
        "Har clone ka apna panel, apne userbots, groups, DMs aur settings hote hain "
        "— main bot ke admins usme access nahi kar sakte.",
        "",
    ]
    if not clones:
        lines.append("ℹ️ *Abhi koi clone nahi hai.*")
    for doc in clones:
        icon = "🟢" if doc.get("running") else "🔴"
        alias = f"@{doc['username']}" if doc.get("username") else str(doc.get("bot_id"))
        lines.append(f"{icon} **{alias}** · owner `{doc.get('owner_id')}`")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    rows = []
    for doc in clones[:10]:
        scope = doc.get("scope")
        alias = f"@{doc['username']}" if doc.get("username") else str(doc.get("bot_id"))
        rows.append([
            InlineKeyboardButton(
                f"{'🛑 Stop' if doc.get('running') else '▶️ Start'} {alias}",
                callback_data=f"ad_cl_toggle:{scope}",
            ),
            InlineKeyboardButton("🔄", callback_data=f"ad_cl_restart:{scope}"),
            InlineKeyboardButton("🗑", callback_data=f"ad_cl_del:{scope}"),
        ])
    rows.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="ad_clones"),
        _back(),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════════
#  Commands
# ══════════════════════════════════════════════════════════════════════════
@Myra.on_cmd(["admin", "apanel"])
@global_scope
async def admin_panel_cmd(_, m: Message):
    """Open the admin control panel (main owner + admins only, one shared data set)."""
    if not await is_admin(m.from_user.id):
        return await m.reply("❌ You are not authorized to use the admin panel.")
    if m.chat.type != ChatType.PRIVATE:
        return await m.reply("❌ Please use `/admin` in my private chat.")

    text, markup = await _main_panel()
    await m.reply_text(text, reply_markup=markup, disable_web_page_preview=True)


@Myra.on_cmd(["broadcast", "bc"])
@global_scope
async def broadcast_cmd(_, m: Message):
    """Open the broadcast section of the admin panel."""
    if not await is_admin(m.from_user.id):
        return await m.reply("❌ You are not authorized to broadcast.")
    if m.chat.type != ChatType.PRIVATE:
        return await m.reply("❌ Please use `/broadcast` in my private chat.")

    text, markup = await _broadcast_sender_panel()
    await m.reply_text(text, reply_markup=markup, disable_web_page_preview=True)


# ══════════════════════════════════════════════════════════════════════════
#  Callbacks
# ══════════════════════════════════════════════════════════════════════════
@Myra.on_callback_query(filters.regex(r"^ad_"), group=-1)
@global_scope
async def admin_panel_callbacks(client: Myra, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data or ""

    if data == "ad_noop":
        return await query.answer()

    if not await is_admin(user_id):
        return await query.answer("❌ Unauthorized", show_alert=True)

    async def show(text_markup, alert: str = None):
        text, markup = text_markup
        # Answer before editing: this is what stops the button spinner, so the
        # tap feels instant instead of waiting for the whole edit round trip.
        await query.answer(alert or "", show_alert=bool(alert))
        try:
            await query.message.edit_text(
                text, reply_markup=markup, disable_web_page_preview=True
            )
        except Exception:  # noqa: BLE001 - "message is not modified" etc.
            pass

    # ── main ───────────────────────────────────────────────────────────────
    if data == "ad_main":
        await show(await _main_panel())
        return

    if data == "ad_close":
        try:
            await query.message.delete()
        except Exception:  # noqa: BLE001
            pass
        await query.answer("Closed")
        return

    if data == "ad_userbots":
        from Myra.modules.useraccounts import _build_main_dashboard

        await show(await _build_main_dashboard(user_id))
        return

    if data == "ad_stats":
        await show(await _stats_panel())
        return

    if data == "ad_stats_refresh":
        from Myra.services import userstats

        await userstats.collect(force=True)
        await show(await _stats_panel(), "📡 Audience re-scanned")
        return

    if data == "ad_import":
        await show(await _import_panel())
        return

    # ── group request controller ───────────────────────────────────────────
    if data == "ad_jr":
        await show(await _jr_panel())
        return

    if data == "ad_jr_tog":
        now = not bool(await ai_settings.get_setting(ai_settings.KEY_JOINREQ_ENABLED, default=False))
        await ai_settings.set_setting(ai_settings.KEY_JOINREQ_ENABLED, now)
        await show(await _jr_panel(), "🟢 Controller ON" if now else "🔴 Controller OFF")
        return

    if data == "ad_jr_auto":
        now = not bool(await ai_settings.get_setting(ai_settings.KEY_JOINREQ_AUTO, default=True))
        await ai_settings.set_setting(ai_settings.KEY_JOINREQ_AUTO, now)
        await show(await _jr_panel(), "🤖 Auto-approve ON" if now else "🤖 Auto-approve OFF")
        return

    if data == "ad_jr_reset":
        reset = await jdb.reset_stats()
        await show(await _jr_panel(), f"🔁 Stats reset ({reset} groups)")
        return

    if data == "ad_jr_chats":
        await show(await _jr_chats_panel())
        return

    if data == "ad_jr_wel":
        pending_input[user_id] = {"kind": "jr_welcome"}
        try:
            await query.message.edit_text(
                "✨ **Welcome message (global default)**\n\n"
                "DM me jo card jaayega wo text bhejo.\n"
                "Placeholders: `{first_name}` · `{chat_title}` · `{username}` · "
                "`{user_id}` · `{chat_username}` · `{count}`\n"
                "(purane `{name}` · `{chat}` bhi chalte hain)\n\n"
                "Default text wapas laane ke liye bhejo `default`.\n"
                "Kisi ek group ke liye alag text: Must-join Setup → ✨ Welcome.\n"
                "`/cancel` se cancel kar sakte ho.",
                reply_markup=InlineKeyboardMarkup([[_back("ad_jr")]]),
            )
        except Exception:  # noqa: BLE001
            pass
        await query.answer()
        return

    if data == "ad_jr_add":
        pending_input[user_id] = {"kind": "jr_add"}
        try:
            await query.message.edit_text(
                "➕ **Group add karo**\n\n"
                "Pehle ye dono cheezein kar lo:\n"
                "1️⃣ Group mein bot ko **admin** banao "
                "(Invite users + Manage chat)\n"
                "2️⃣ Group settings → **Members → Join requests** ON karo\n\n"
                "Ab group ka `@username` ya `-100…` id bhejo.\n"
                "Private group ke liye: group ke andar `/id` bhejo, ya group ke "
                "kisi message ko yahan **forward** kar do, ya uske "
                "*Copy Link* (`t.me/c/…`) bhejo.\n\n"
                "`/cancel` se cancel kar sakte ho.",
                reply_markup=InlineKeyboardMarkup([[_back("ad_jr")]]),
            )
        except Exception:  # noqa: BLE001
            pass
        await query.answer()
        return

    if data.startswith("ad_jr_del:"):
        try:
            chat_id = int(data.split(":", 1)[1])
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Galat group id.", show_alert=True)
        await jdb.remove(chat_id)
        await jdb.purge_sessions(chat_id)
        await show(await _jr_chats_panel(), "🗑 Group hata diya")
        return

    if data.startswith("ad_jr_set:"):
        try:
            chat_id = int(data.split(":", 1)[1])
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Galat group id.", show_alert=True)
        await show(await _jr_req_panel(chat_id))
        return

    # ── per-chat settings ─────────────────────────────────────────────────
    if data.startswith("ad_jr_cauto:"):
        try:
            chat_id = int(data.split(":", 1)[1])
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Galat group id.", show_alert=True)
        doc = await jdb.get_chat(chat_id)
        if not doc:
            return await query.answer("❌ Ye group ab managed list mein nahi hai.", show_alert=True)
        current = doc.get("auto_approve")
        # global (None) → ON → OFF → global
        new_value = {None: True, True: False, False: None}.get(current, True)
        await jdb.set_option(chat_id, "auto_approve", new_value)
        label = "global default" if new_value is None else ("ON" if new_value else "OFF")
        await show(await _jr_req_panel(chat_id), f"🤖 Auto-approve: {label}")
        return

    if data.startswith("ad_jr_ctime:"):
        try:
            chat_id = int(data.split(":", 1)[1])
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Galat group id.", show_alert=True)
        doc = await jdb.get_chat(chat_id)
        if not doc:
            return await query.answer("❌ Ye group ab managed list mein nahi hai.", show_alert=True)
        try:
            current = int(doc.get("timeout_minutes") or jdb.DEFAULT_TIMEOUT_MIN)
        except Exception:  # noqa: BLE001
            current = jdb.DEFAULT_TIMEOUT_MIN
        choices = jdb.TIMEOUT_CHOICES
        try:
            new_value = choices[(choices.index(current) + 1) % len(choices)]
        except ValueError:
            new_value = jdb.DEFAULT_TIMEOUT_MIN
        await jdb.set_option(chat_id, "timeout_minutes", new_value)
        await show(await _jr_req_panel(chat_id), f"⏱ Timeout: {new_value} min")
        return

    if data.startswith("ad_jr_cnotify:"):
        try:
            chat_id = int(data.split(":", 1)[1])
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Galat group id.", show_alert=True)
        doc = await jdb.get_chat(chat_id)
        if not doc:
            return await query.answer("❌ Ye group ab managed list mein nahi hai.", show_alert=True)
        new_value = not bool(doc.get("notify_admins", True))
        await jdb.set_option(chat_id, "notify_admins", new_value)
        await show(await _jr_req_panel(chat_id), "🔔 Admin alerts ON" if new_value else "🔕 Admin alerts OFF")
        return

    if data.startswith("ad_jr_cwel:"):
        try:
            chat_id = int(data.split(":", 1)[1])
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Galat group id.", show_alert=True)
        pending_input[user_id] = {"kind": "jr_welcome_chat", "chat_id": chat_id}
        try:
            await query.message.edit_text(
                "✨ **Welcome message — sirf is group ke liye**\n\n"
                "DM me jo card jaayega wo text bhejo.\n"
                "Placeholders: `{first_name}` · `{chat_title}` · `{username}` · "
                "`{user_id}` · `{chat_username}`\n"
                "(purane `{name}` · `{chat}` · `{count}` bhi chalte hain)\n\n"
                "`default` bhejo to is group ka custom text hata kar global/default "
                "wapas lagega.\n"
                "`/cancel` se cancel kar sakte ho.",
                reply_markup=InlineKeyboardMarkup([[_back(f"ad_jr_set:{chat_id}")]]),
            )
        except Exception:  # noqa: BLE001
            pass
        await query.answer()
        return

    if data.startswith("ad_jr_test:"):
        try:
            chat_id = int(data.split(":", 1)[1])
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Galat group id.", show_alert=True)
        try:
            report = await joinreq_service.diagnose(client, chat_id, user_id)
        except Exception as exc:  # noqa: BLE001
            report = f"❌ Diagnose fail: {type(exc).__name__}: {exc}"
        sent = False
        try:
            sent = await joinreq_service.send_test_card(client, chat_id, query.from_user)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(f"joinreq test card failed: {exc}")
        try:
            await query.message.reply_text(report)
        except Exception:  # noqa: BLE001
            pass
        await query.answer(
            "🧪 Report bhej di + test card DM mein aaya"
            if sent
            else "🧪 Report bhej di (test card DM nahi gaya)",
            show_alert=True,
        )
        return

    if data.startswith("ad_jr_radd:"):
        try:
            chat_id = int(data.split(":", 1)[1])
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Galat group id.", show_alert=True)
        doc = await jdb.get_chat(chat_id)
        if not doc:
            return await query.answer("❌ Ye group ab managed list mein nahi hai.", show_alert=True)
        if len(doc.get("required") or []) >= jdb.MAX_REQUIRED:
            return await query.answer(f"❌ Max {jdb.MAX_REQUIRED} required links.", show_alert=True)
        pending_input[user_id] = {"kind": "jr_req_add", "chat_id": chat_id}
        try:
            await query.message.edit_text(
                "➕ **Required channel/group bhejo**\n\n"
                "VERIFY se pehle user ko ye join karna hoga.\n"
                "`@username`, `-100…` id ya `t.me/c/…` link bhejo — ya kisi "
                "message ko yahan **forward** karo.\n"
                "`/cancel` se cancel kar sakte ho.",
                reply_markup=InlineKeyboardMarkup([[_back(f"ad_jr_set:{chat_id}")]]),
            )
        except Exception:  # noqa: BLE001
            pass
        await query.answer()
        return

    if data.startswith("ad_jr_rdel:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            return await query.answer("❌ Galat data.", show_alert=True)
        try:
            chat_id, index = int(parts[1]), int(parts[2])
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Galat data.", show_alert=True)
        doc = await jdb.get_chat(chat_id)
        if not doc:
            return await query.answer("❌ Ye group ab managed list mein nahi hai.", show_alert=True)
        required = list(doc.get("required") or [])
        try:
            removed = required.pop(index)
        except Exception:  # noqa: BLE001
            return await query.answer("❌ Ye entry nahi mili.", show_alert=True)
        await jdb.set_required(chat_id, required)
        label = removed.get("label") or removed.get("username") or str(removed.get("chat_id"))
        await show(await _jr_req_panel(chat_id), f"🗑 {label} removed")
        return

    # ── clones ─────────────────────────────────────────────────────────────
    if data == "ad_clones":
        await show(await _clones_panel())
        return

    if data.startswith("ad_cl_toggle:") or data.startswith("ad_cl_del:"):
        _, action, scope = data.split(":", 2)
        from Myra.services.clones import clone_manager

        if action == "toggle":
            if clone_manager.is_running(scope):
                await clone_manager.stop_clone(scope)
                alert = "🛑 Stopped"
            else:
                client = await clone_manager.restart_clone(scope)
                alert = "▶️ Started" if client else "❌ Could not start"
        else:
            await clone_manager.delete_clone(scope)
            alert = "🗑 Deleted"
        await show(await _clones_panel(), alert)
        return

    if data.startswith("ad_cl_restart:"):
        scope = data.split(":", 1)[1]
        from Myra.services.clones import clone_manager

        client = await clone_manager.restart_clone(scope)
        await show(await _clones_panel(), "🔄 Restarted" if client else "❌ Restart failed")
        return

    if data == "ad_ai":
        await show(await _ai_panel())
        return

    if data == "ad_ai_reload":
        from Myra.ai.brain import PROVIDERS, provider_order

        parts = []
        for name in provider_order():
            info = await PROVIDERS[name].refresh_keys()
            parts.append(f"{name} {info.get('active')}/{info.get('total')}")
        await show(await _ai_panel(), "🔄 " + " · ".join(parts))
        return

    if data == "ad_behaviour":
        await show(await _behaviour_panel())
        return

    if data.startswith("ad_bev_delay:"):
        ms = int(data.split(":", 1)[1])
        await ai_settings.set_reply_delay(ms)
        applied = 0
        for account_id in list(_my_accounts().keys()):
            try:
                await userbot_manager.set_speed(account_id, delay=ms, mode="custom")
                applied += 1
            except Exception:  # noqa: BLE001
                pass
        await show(await _behaviour_panel(), f"✅ Delay {ms}ms · {applied} userbots")
        return

    if data == "ad_bev_custom":
        pending_input[user_id] = {"kind": "delay"}
        try:
            await query.message.edit_text(
                "⚙️ **Custom reply delay**\n\n"
                "Milliseconds me bhejo (e.g. `500`, `1500`, `7000`).\n"
                "`/cancel` se cancel kar sakte ho.",
                reply_markup=InlineKeyboardMarkup([[_back("ad_behaviour")]]),
            )
        except Exception:  # noqa: BLE001
            pass
        await query.answer()
        return

    if data.startswith("ad_bev_chance:"):
        scope = data.split(":", 1)[1]
        current = (
            await ai_settings.get_group_chance()
            if scope == "group"
            else await ai_settings.get_private_chance()
        )
        cycle = [100.0, 85.0, 70.0, 50.0, 30.0, 15.0]
        try:
            index = cycle.index(float(current))
        except ValueError:
            index = 0
        new_value = cycle[(index + 1) % len(cycle)]
        if scope == "group":
            await ai_settings.set_group_chance(new_value)
        else:
            await ai_settings.set_private_chance(new_value)
        await show(await _behaviour_panel(), f"🎲 {scope}: {new_value:g}%")
        return

    if data == "ad_clean":
        if training.TRAINING is None:
            return await query.answer("❌ MongoDB not connected", show_alert=True)
        try:
            await query.message.edit_text(
                "🧹 **Scanning the training database...**\nLarge collections may take a while.",
            )
        except Exception:  # noqa: BLE001
            pass
        await query.answer()
        try:
            report = await training.clean_database()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"admin panel clean_db failed: {exc}")
            await show(await _main_panel(), f"❌ Clean failed: {exc}")
            return
        reasons = report.get("reasons") or {}
        reason_text = "\n".join(
            f"• `{k}` → {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])
        ) or "• none"
        text = (
            "🧹 **DATABASE CLEANED**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔍 **Scanned:** `{report.get('scanned', 0)}`\n"
            f"🗑 **Deleted:** `{report.get('deleted', 0)}`\n\n"
            f"**Reasons:**\n{reason_text}\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        try:
            await query.message.edit_text(
                text, reply_markup=InlineKeyboardMarkup([[_back()]])
            )
        except Exception:  # noqa: BLE001
            pass
        return

    # ── broadcast ──────────────────────────────────────────────────────────
    if data == "ad_bc":
        await show(await _broadcast_sender_panel())
        return

    if data.startswith("ad_bc_s:"):
        await show(await _broadcast_target_panel(data.split(":", 1)[1]))
        return

    if data.startswith("ad_bc_t:"):
        _, sender, target = data.split(":", 2)
        pending_input[user_id] = {
            "kind": "broadcast",
            "sender": sender,
            "target": target,
        }
        await show(_broadcast_prompt(sender, target))
        return

    if data == "ad_bc_cancel":
        pending_input.pop(user_id, None)
        await show(await _broadcast_sender_panel(), "🛑 Cancelled")
        return

    if data.startswith("ad_bc_stop:"):
        token = data.split(":", 1)[1]
        state = active_broadcasts.get(token)
        if state:
            state["stop"] = True
            await query.answer("🛑 Stopping...", show_alert=True)
        else:
            await query.answer("Already finished")
        return

    # ── autochat ───────────────────────────────────────────────────────────
    if data == "ad_ac":
        await show(await _autochat_panel())
        return

    if data in ("ad_ac_bot", "ad_ac_ub"):
        await show(await _autochat_side_panel("bot" if data.endswith("bot") else "ub"))
        return

    if data.startswith("ad_ac_toggle:"):
        scope = data.split(":", 1)[1]
        if scope == "bot":
            new_state = not await ac.get_bot_enabled()
            await ac.set_bot_enabled(new_state)
        else:
            new_state = not await ac.get_ub_enabled()
            await ac.set_ub_enabled(new_state)
        autochat_service.reset_timers()
        await show(await _autochat_side_panel(scope), f"{'🟢 ON' if new_state else '🔴 OFF'}")
        return

    if data.startswith("ad_ac_iv:"):
        _, scope, ms = data.split(":", 2)
        if scope == "bot":
            await ac.set_bot_interval(int(ms))
        else:
            await ac.set_ub_interval(int(ms))
        autochat_service.reset_timers()
        await show(await _autochat_side_panel(scope),
                   f"⏱ {ac.human_interval(int(ms))}")
        return

    if data.startswith("ad_ac_custom:"):
        scope = data.split(":", 1)[1]
        pending_input[user_id] = {"kind": "interval", "scope": scope}
        try:
            await query.message.edit_text(
                f"⚙️ **Custom interval ({'bot' if scope == 'bot' else 'userbots'})**\n\n"
                "Kitne **minutes** ke gap par message bhejna hai? (e.g. `7`, `45`, `180`)\n"
                "Minimum 1 minute.\n\n"
                "`/cancel` se cancel karo.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ Back", callback_data=f"ad_ac_{scope}")]
                ]),
            )
        except Exception:  # noqa: BLE001
            pass
        await query.answer()
        return

    if data.startswith("ad_ac_tg:"):
        scope = data.split(":", 1)[1]
        if scope == "bot":
            current = await ac.get_bot_targets()
        else:
            current = await ac.get_ub_targets()
        order = (ac.TARGET_GROUPS, ac.TARGET_DM, ac.TARGET_BOTH)
        try:
            new_target = order[(order.index(current) + 1) % len(order)]
        except ValueError:
            new_target = ac.TARGET_GROUPS
        if scope == "bot":
            await ac.set_bot_targets(new_target)
        else:
            await ac.set_ub_targets(new_target)
        await show(await _autochat_side_panel(scope), ac.label_for_target(new_target))
        return

    if data == "ad_ac_preset:900":
        await ac.set_bot_interval(60_000)
        await ac.set_ub_interval(60_000)
        autochat_service.reset_timers()
        await show(await _autochat_panel(), "⚡ Both set to 1m")
        return

    if data == "ad_ac_preset:900000":
        await ac.set_bot_interval(900_000)
        await ac.set_ub_interval(900_000)
        autochat_service.reset_timers()
        await show(await _autochat_panel(), "🕐 Both set to 15m")
        return

    if data.startswith("ad_ac_now:"):
        kind = data.split(":", 1)[1]
        await query.answer("⏳ Sending...")
        try:
            sent = await autochat_service.send_now(kind, scope=current_scope())
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"autochat send_now failed: {exc}")
            sent = 0
        await show(await _autochat_panel(),
                   f"✅ Sent {sent} message(s)" if sent else "❌ Nothing sent (check status/targets)")
        return


# ══════════════════════════════════════════════════════════════════════════
#  Pending text input (broadcast content, custom delay / interval)
# ══════════════════════════════════════════════════════════════════════════
async def _start_broadcast(user_id: int, flow: dict, source: Message):
    sender = flow.get("sender", "bot")
    target = flow.get("target", "groups")

    content = bc.content_from_message(source)
    if content.kind != "text":
        status_line = f"📎 media ({content.kind}) + caption"
    else:
        status_line = content.text[:60] + ("..." if len(content.text) > 60 else "")

    token = uuid4().hex[:10]
    cancel = {"stop": False}
    active_broadcasts[token] = cancel

    status = await source.reply_text(
        "📢 **Broadcast starting...**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 Sender: `{sender}`\n🎯 Target: `{target}`\n"
        f"✉️ Content: {status_line}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ Collecting chats...",
        reply_markup=_stop_keyboard(token),
    )

    async def progress(stats: dict):
        try:
            await status.edit_text(
                bc.describe(sender, target, stats),
                reply_markup=_stop_keyboard(token),
                disable_web_page_preview=True,
            )
        except Exception:  # noqa: BLE001
            pass

    started = time.time()
    try:
        stats = await bc.run_broadcast(
            sender, content, target,
            progress=progress, cancel=cancel, scope=current_scope(),
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"broadcast failed: {exc}")
        active_broadcasts.pop(token, None)
        return await status.edit_text(f"❌ **Broadcast failed:**\n`{exc}`")

    active_broadcasts.pop(token, None)

    names = {aid: (data.get("name") or aid) for aid, data in _my_accounts().items()}
    report = bc.describe(sender, target, stats, time.time() - started, names)
    try:
        await status.edit_text(report, disable_web_page_preview=True)
    except Exception:  # noqa: BLE001
        pass


async def _pending_chat(message: Message, text: str):
    """
    Resolve the group the admin pasted / forwarded.

    A **forwarded** message from the group is the friendliest input (Telegram
    hands us ``forward_from_chat``), and it is the only one that works for a
    private group whose invite link a bot is not allowed to read.
    """
    forwarded = getattr(message, "forward_from_chat", None)
    if forwarded is not None and getattr(forwarded, "type", None) != ChatType.PRIVATE:
        return forwarded, None
    return await joinreq_service.resolve_chat(message._client, text)


async def _handle_pending_text(user_id: int, flow: dict, message: Message) -> bool:
    """Handle a pending text input. Returns True when the message was consumed."""
    kind = flow.get("kind")
    # NOTE: every branch below reads ``text``; because some branches also assign
    # it, Python treats it as a local — so it must be bound up front or the
    # ``jr_*`` branches die with UnboundLocalError (silently, in the log).
    text = (message.text or "").strip()

    if kind == "delay":
        if not text.isdigit():
            await message.reply("❌ Number of milliseconds chahiye (e.g. `1500`). `/cancel` to cancel.")
            return True
        ms = int(text)
        if ms > 300_000:
            await message.reply("❌ Maximum 300000 ms (5 minutes).")
            return True
        pending_input.pop(user_id, None)
        await ai_settings.set_reply_delay(ms)
        applied = 0
        for account_id in list(_my_accounts().keys()):
            try:
                await userbot_manager.set_speed(account_id, delay=ms, mode="custom")
                applied += 1
            except Exception:  # noqa: BLE001
                pass
        text_markup = await _behaviour_panel()
        await message.reply_text(
            f"✅ **Delay {ms} ms** set for `{applied}` userbot(s).",
            reply_markup=text_markup[1],
        )
        return True

    if kind == "interval":
        scope = flow.get("scope", "bot")
        if not text.isdigit() or int(text) < 1:
            await message.reply("❌ Minutes me number bhejo (minimum 1). `/cancel` to cancel.")
            return True
        minutes = int(text)
        ms = minutes * 60_000
        if ms < ac.MIN_INTERVAL_MS:
            await message.reply("❌ Minimum 1 minute.")
            return True
        if ms > ac.MAX_INTERVAL_MS:
            await message.reply("❌ Maximum 1440 minutes (24 hours).")
            return True
        pending_input.pop(user_id, None)
        if scope == "bot":
            await ac.set_bot_interval(ms)
        else:
            await ac.set_ub_interval(ms)
        autochat_service.reset_timers()
        text_markup = await _autochat_side_panel(scope)
        await message.reply_text(
            f"✅ Interval set to **{ac.human_interval(ms)}**.",
            reply_markup=text_markup[1],
        )
        return True

    # ── group request controller ───────────────────────────────────────────
    if kind == "jr_add":
        chat, err = await _pending_chat(message, text)
        if chat is None:
            await message.reply_text(f"❌ {err}\n\n`/cancel` se cancel kar sakte ho.")
            return True
        pending_input.pop(user_id, None)
        await jdb.enable(chat.id, chat.title, chat.username)
        title = chat.title or str(chat.id)

        # Telegram only sends `chat_join_request` updates to a bot that is an
        # admin in that chat **with the "Invite users" right**. Without it the
        # controller can never fire, so say so right away instead of silently
        # doing nothing later.
        rights_warn = ""
        try:
            me = message._client.me
            member = await message._client.get_chat_member(chat.id, me.id)
            if not getattr(getattr(member, "privileges", None), "can_invite_users", False):
                rights_warn = (
                    "\n\n⚠️ **Dhyan do:** bot is group me admin nahi hai (ya "
                    "'Invite users' permission off hai). Aise Telegram koi join "
                    "request update nahi bhejega. Bot ko admin banao — "
                    "Invite users + Manage chat ON — phir test karo."
                )
        except Exception as exc:  # noqa: BLE001
            rights_warn = f"\n\n⚠️ Admin rights check nahi ho paaya ({type(exc).__name__})."

        try:
            await message._client.send_message(
                chat.id,
                "🚪 **Group Request Controller** ON ✅\n\n"
                "Ab jo bhi banda *Ask to join* karega, usse bot DM card bhejega "
                "aur verify hote hi request seedha approve ho jaayegi 🎉\n\n"
                "Bot ko yahan **admin** rehna chahiye "
                "(Invite users + Manage chat).",
            )
        except Exception:  # noqa: BLE001
            pass
        await message.reply_text(
            f"✅ **{title[:60]}** add ho gaya 🎉{rights_warn}",
            reply_markup=(await _jr_chats_panel())[1],
        )
        return True

    if kind == "jr_req_add":
        chat_id = flow.get("chat_id")
        doc = await jdb.get_chat(chat_id)
        if not doc:
            pending_input.pop(user_id, None)
            await message.reply_text("❌ Wo group ab managed list mein nahi hai.")
            return True
        chat, err = await _pending_chat(message, text)
        if chat is None:
            await message.reply_text(f"❌ {err}\n\n`/cancel` se cancel kar sakte ho.")
            return True
        required = list(doc.get("required") or [])
        if len(required) >= jdb.MAX_REQUIRED:
            pending_input.pop(user_id, None)
            await message.reply_text(f"❌ Max {jdb.MAX_REQUIRED} required links ho sakte hain.")
            return True
        if int(chat.id) >= 0:
            # A positive id can only be a user/bot; membership can't be checked.
            await message.reply_text(
                "❌ Ye ek bot/user account hai — sirf channel ya group add karo."
            )
            return True
        if any(int(entry.get("chat_id") or 0) == int(chat.id) for entry in required):
            await message.reply_text("❌ Ye chat pehle se required list mein hai.")
            return True
        label = (
            getattr(chat, "title", None)
            or getattr(chat, "username", None)
            or str(chat.id)
        )
        required.append({
            "chat_id": int(chat.id),
            "username": getattr(chat, "username", None),
            "label": label[:60],
            "invite": await joinreq_service.invite_for(message._client, chat),
        })
        await jdb.set_required(chat_id, required)
        label = label[:40]
        await message.reply_text(
            f"➕ **{label}** add ho gaya — ab user ko VERIFY se pehle "
            f"ye join karna hoga.",
            reply_markup=(await _jr_req_panel(chat_id))[1],
        )
        return True

    if kind == "jr_welcome":
        pending_input.pop(user_id, None)
        if text.lower() in ("default", "reset"):
            await ai_settings.set_setting(ai_settings.KEY_JOINREQ_WELCOME, None)
        elif len(text) > 900:
            await message.reply_text("❌ Bahut lamba hai — 900 char tak allowed hain.")
            return True
        else:
            await ai_settings.set_setting(ai_settings.KEY_JOINREQ_WELCOME, text)
        await message.reply_text(
            "✨ Welcome message set ho gaya.",
            reply_markup=(await _jr_panel())[1],
        )
        return True

    if kind == "jr_welcome_chat":
        chat_id = flow.get("chat_id")
        pending_input.pop(user_id, None)
        doc = await jdb.get_chat(chat_id)
        if not doc:
            await message.reply_text("❌ Wo group ab managed list mein nahi hai.")
            return True
        if text.lower() in ("default", "reset", "clear"):
            await jdb.set_option(chat_id, "welcome", None)
            note = "✨ Is group ka custom text hata diya (global/default lagega)."
        elif len(text) > 900:
            await message.reply_text("❌ Bahut lamba hai — 900 char tak allowed hain.")
            return True
        else:
            await jdb.set_option(chat_id, "welcome", text)
            note = "✨ Is group ka welcome message set ho gaya."
        await message.reply_text(
            note,
            reply_markup=(await _jr_req_panel(chat_id))[1],
        )
        return True

    if kind == "broadcast":
        pending_input.pop(user_id, None)
        await _start_broadcast(user_id, flow, message)
        return True

    return False


@Myra.on_message(filters.private & ~filters.bot, group=-2)
@global_scope
async def admin_panel_input(client: Myra, m: Message):
    """Collect panel input (broadcast content, custom delay/interval)."""
    user_id = m.from_user.id if m.from_user else None
    if user_id is None:
        m.continue_propagation()
        return

    text = (m.text or "").strip()
    if text == "/cancel":
        flow = pending_input.pop(user_id, None)
        if flow:
            kind = flow.get("kind")
            if kind == "broadcast":
                text_markup = await _broadcast_sender_panel()
            elif kind == "delay":
                text_markup = await _behaviour_panel()
            elif kind == "interval":
                text_markup = await _autochat_side_panel(flow.get("scope", "bot"))
            else:
                text_markup = await _main_panel()
            await m.reply("❌ Cancelled.", reply_markup=text_markup[1])
            m.stop_propagation()
            return

    flow = pending_input.get(user_id)
    if not flow:
        m.continue_propagation()
        return

    # Commands always win (e.g. the admin starts another section).
    if text.startswith("/"):
        pending_input.pop(user_id, None)
        m.continue_propagation()
        return

    try:
        consumed = await _handle_pending_text(user_id, flow, m)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"admin panel input failed: {exc}")
        consumed = False

    if consumed:
        m.stop_propagation()
    else:
        m.continue_propagation()


__all__ = ["admin_panel_cmd", "broadcast_cmd", "admin_panel_callbacks", "admin_panel_input"]
