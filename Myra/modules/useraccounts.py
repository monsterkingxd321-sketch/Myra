import re
import time
from pyrogram import filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Myra import Myra, LOGGER, config
from Myra.database.admins import (
    is_owner,
    is_admin,
    add_admin,
    remove_admin,
    get_admins,
)
from Myra.database.bots import owner_id_sync
from Myra.database.scope import global_scope
from Myra.userbot.storage import (
    get_served_accounts,
    get_accounts_by_user,
    get_account,
    accounts as ub_accounts,
)
from Myra.userbot.manager import userbot_manager
from Myra.database import settings as ai_settings
from Myra.userbot.auth import (
    pending_auth,
    start_login_flow,
    process_otp_input,
    process_2fa_input,
)


def _mask_phone(phone: str) -> str:
    """Format phone number for clear display."""
    if not phone:
        return "Not available"
    phone_clean = phone.strip()
    return f"+{phone_clean.lstrip('+')}"


def _format_speed_label(mode: str, delay_ms) -> str:
    """Format speed mode for display. Reply delay is stored in milliseconds."""
    if delay_ms is None:
        return "🌐 Default (global)"
    try:
        delay_ms = int(delay_ms)
    except Exception:
        delay_ms = 0
    seconds = delay_ms / 1000
    if mode == "fast" or delay_ms == 0:
        return "Fast (0s)"
    elif mode == "medium" or delay_ms == 5000:
        return f"Medium ({seconds:g}s)"
    elif mode == "slow" or delay_ms == 10000:
        return f"Slow ({seconds:g}s)"
    else:
        return f"Custom ({seconds:g}s)"


async def _can_manage_account(user_id: int, account_id: str) -> tuple[bool, dict | None]:
    """
    Check if a user has permission to manage a specific userbot account.
    Owner can manage all userbots.
    Admins can only manage userbots they added.
    """
    if is_owner(user_id):
        acc = await get_account(account_id)
        return True, acc

    acc = await get_account(account_id)
    if not acc:
        return False, None

    added_by = acc.get("added_by")
    if added_by and int(added_by) == int(user_id):
        return True, acc

    return False, acc


# ---------- UI Builders ----------

def _format_ub_status(account_id: str, acc: dict, in_mem: dict) -> tuple[str, str]:
    """Return (short_badge, full_status_label) for a userbot account."""
    raw_status = (in_mem.get("status") or acc.get("status") or "DISCONNECTED").upper()
    autochat_state = in_mem.get("autochat", acc.get("autochat", True))
    action_mode = in_mem.get("action_mode") or acc.get("action_mode") or "chat"
    if raw_status in ("LOGGED_OUT", "AUTH_REQUIRED"):
        return "[Logged Out]", "Logged Out (Re-login required)"
    if raw_status == "ERROR":
        return "[Error]", "Error (Restart / Re-login)"
    if account_id not in ub_accounts or raw_status != "CONNECTED":
        return "[Offline]", "Disconnected"
    mode_tag = {"chat": "Chat", "react": "React", "both": "Chat+React"}.get(action_mode, "Chat")
    return (f"[✓ {mode_tag}]" if autochat_state or action_mode == "react" else f"[x {mode_tag}]"), "Connected"


async def _build_main_dashboard(viewer_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """
    Build the main userbot management dashboard.
    If viewer is Owner: displays ALL userbots (categorized) + Manage Admins button.
    If viewer is Admin: displays ONLY userbots added by this admin.
    """
    user_is_owner = is_owner(viewer_id)

    if user_is_owner:
        all_accounts = await get_served_accounts()
        owner_id = owner_id_sync() or viewer_id

        owner_accs = []
        admin_accs = []

        for acc in all_accounts:
            acc_added_by = acc.get("added_by")
            if not acc_added_by or int(acc_added_by) == owner_id:
                owner_accs.append(acc)
            else:
                admin_accs.append(acc)

        total_count = len(all_accounts)
        admins_list = await get_admins()

        text = (
            "╭───「 Userbots Pool 」\n"
            f"│ Connected: {total_count}  │  Owner: {len(owner_accs)}  │  Admin: {len(admin_accs)}\n"
            f"│ Authorized Admins: {len(admins_list)}\n"
            "╰──────────────────────\n\n"
        )

        keyboard_rows = []

        if not all_accounts:
            text += "ℹ️ *No userbot accounts connected yet.*\nClick **[ Add Userbot ]** to link an account."
        else:
            text += "Connected Userbots:\n"
            for idx, acc in enumerate(all_accounts, 1):
                account_id = acc["account_id"]
                in_mem = ub_accounts.get(account_id, {})
                name = in_mem.get("name", acc.get("name", "Unknown"))
                username = in_mem.get("username", acc.get("username", ""))
                u_str = f"@{username}" if username else f"ID: {acc.get('telegram_id', '')}"
                phone = in_mem.get("phone") or acc.get("phone") or ""
                if not phone:
                    cl = in_mem.get("client")
                    c_me = getattr(cl, "me", None) if cl else None
                    if c_me and getattr(c_me, "phone_number", None):
                        phone = f"+{str(c_me.phone_number).lstrip('+')}"
                        in_mem["phone"] = phone
                p_str = f" · Phone: `{phone}`" if phone else ""
                badge, full_st = _format_ub_status(account_id, acc, in_mem)
                st_note = f" · **{full_st}**" if full_st != "Connected" else ""
                text += f"• {name} ({u_str}){p_str}{st_note}\n"
            text += "\nSelect any userbot below to open its dedicated settings & controls:\n"

            # Owner accounts section
            if owner_accs:
                for idx, acc in enumerate(owner_accs, 1):
                    account_id = acc["account_id"]
                    in_mem = ub_accounts.get(account_id, {})
                    name = in_mem.get("name", acc.get("name", "Unknown"))
                    username = in_mem.get("username", acc.get("username", ""))
                    phone = in_mem.get("phone") or acc.get("phone") or ""
                    if not phone:
                        cl = in_mem.get("client")
                        c_me = getattr(cl, "me", None) if cl else None
                        if c_me and getattr(c_me, "phone_number", None):
                            phone = f"+{str(c_me.phone_number).lstrip('+')}"
                            in_mem["phone"] = phone
                    user_tag = phone if phone else (f"@{username}" if username else f"#{idx}")
                    status_sym, _ = _format_ub_status(account_id, acc, in_mem)
                    btn_text = f"{name} ({user_tag}) • {status_sym}"
                    keyboard_rows.append([InlineKeyboardButton(btn_text, callback_data=f"ub_panel:{account_id}")])

            # Admin accounts section
            if admin_accs:
                for idx, acc in enumerate(admin_accs, 1):
                    account_id = acc["account_id"]
                    in_mem = ub_accounts.get(account_id, {})
                    name = in_mem.get("name", acc.get("name", "Unknown"))
                    username = in_mem.get("username", acc.get("username", ""))
                    phone = in_mem.get("phone") or acc.get("phone") or ""
                    if not phone:
                        cl = in_mem.get("client")
                        c_me = getattr(cl, "me", None) if cl else None
                        if c_me and getattr(c_me, "phone_number", None):
                            phone = f"+{str(c_me.phone_number).lstrip('+')}"
                            in_mem["phone"] = phone
                    user_tag = phone if phone else (f"@{username}" if username else f"#{idx}")
                    added_by_id = acc.get("added_by", "Unknown")
                    status_sym, _ = _format_ub_status(account_id, acc, in_mem)
                    btn_text = f"{name} ({user_tag}) [Admin: {added_by_id}] • {status_sym}"
                    keyboard_rows.append([InlineKeyboardButton(btn_text, callback_data=f"ub_panel:{account_id}")])

        from Myra.database import autochat as ac
        ub_auto_on = await ac.get_ub_enabled()
        ub_interval = await ac.get_ub_interval()
        ub_targets = await ac.get_ub_targets()
        text += (
            f"\n⏰ **AutoChat Loop:** `{'Active' if ub_auto_on else 'Paused'}` "
            f"({ac.human_interval(ub_interval)} · {ac.label_for_target(ub_targets)})\n"
        )

        notify_enabled = await ai_settings.get_joinreq_notify()
        notify_str = "ON" if notify_enabled else "OFF"

        keyboard_rows.append([
            InlineKeyboardButton("All: React Only", callback_data="ub_all_action:react"),
            InlineKeyboardButton("All: Chat Only", callback_data="ub_all_action:chat"),
            InlineKeyboardButton("All: Chat+React", callback_data="ub_all_action:both"),
        ])
        keyboard_rows.append([
            InlineKeyboardButton("All: Group All Msgs", callback_data="ub_all_gmode:all"),
            InlineKeyboardButton("All: Tag Only", callback_data="ub_all_gmode:mention"),
        ])
        keyboard_rows.append([
            InlineKeyboardButton("All Settings (Bulk)", callback_data="ub_all_panel"),
            InlineKeyboardButton("Add Userbot", callback_data="ub_add"),
        ])
        keyboard_rows.append([
            InlineKeyboardButton("Refresh", callback_data="ub_main_refresh"),
            InlineKeyboardButton("Back", callback_data="ad_main"),
        ])

        return text, InlineKeyboardMarkup(keyboard_rows)

    else:
        # Admin view: Only accounts added by this admin
        my_accounts = await get_accounts_by_user(viewer_id)
        notify_enabled = await ai_settings.get_joinreq_notify()
        notify_str = "ON" if notify_enabled else "OFF"

        if not my_accounts:
            text = (
                "╭───「 Userbot Dashboard 」\n"
                "╰──────────────────────\n\n"
                "You have not connected any userbot account yet.\n"
                "Click [ Add Userbot ] below to link your Telegram account."
            )
            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Add Admin", callback_data="ub_add_admin_prompt"),
                    InlineKeyboardButton("Remove Admin", callback_data="ub_manage_admins"),
                ],
                [
                    InlineKeyboardButton(f"Invite Notify: {notify_str}", callback_data="ub_toggle_notify"),
                    InlineKeyboardButton("Add Userbot", callback_data="ub_add"),
                ],
                [
                    InlineKeyboardButton("Manage Admins", callback_data="ub_manage_admins"),
                    InlineKeyboardButton("Refresh", callback_data="ub_main_refresh"),
                ],
            ])
            return text, markup

        text = (
            "╭───「 Userbot Dashboard 」\n"
            f"│ Connected Accounts: {len(my_accounts)}\n"
            "╰──────────────────────\n\n"
            "Connected Userbots:\n"
        )
        for idx, acc in enumerate(my_accounts, 1):
            account_id = acc["account_id"]
            in_mem = ub_accounts.get(account_id, {})
            name = in_mem.get("name", acc.get("name", "Unknown"))
            username = in_mem.get("username", acc.get("username", ""))
            u_str = f"@{username}" if username else f"ID: {acc.get('telegram_id', '')}"
            phone = in_mem.get("phone") or acc.get("phone") or ""
            if not phone:
                cl = in_mem.get("client")
                c_me = getattr(cl, "me", None) if cl else None
                if c_me and getattr(c_me, "phone_number", None):
                    phone = f"+{str(c_me.phone_number).lstrip('+')}"
                    in_mem["phone"] = phone
            p_str = f" · Phone: `{phone}`" if phone else ""
            _, full_st = _format_ub_status(account_id, acc, in_mem)
            st_note = f" · **{full_st}**" if full_st != "Connected" else ""
            text += f"{idx}. {name} ({u_str}){p_str}{st_note}\n"
        text += "\nSelect a userbot below to open settings:"

        keyboard_rows = []
        for index, acc in enumerate(my_accounts, 1):
            account_id = acc["account_id"]
            in_mem = ub_accounts.get(account_id, {})

            name = in_mem.get("name", acc.get("name", "Unknown"))
            username = in_mem.get("username", acc.get("username", ""))
            phone = in_mem.get("phone") or acc.get("phone") or ""
            if not phone:
                cl = in_mem.get("client")
                c_me = getattr(cl, "me", None) if cl else None
                if c_me and getattr(c_me, "phone_number", None):
                    phone = f"+{str(c_me.phone_number).lstrip('+')}"
                    in_mem["phone"] = phone
            user_tag = phone if phone else (f"@{username}" if username else f"#{index}")

            status_sym, _ = _format_ub_status(account_id, acc, in_mem)

            btn_text = f"{index}. {name} ({user_tag}) • {status_sym}"
            keyboard_rows.append([InlineKeyboardButton(btn_text, callback_data=f"ub_panel:{account_id}")])

        keyboard_rows.append([
            InlineKeyboardButton("All: React Only", callback_data="ub_all_action:react"),
            InlineKeyboardButton("All: Chat Only", callback_data="ub_all_action:chat"),
            InlineKeyboardButton("All: Chat+React", callback_data="ub_all_action:both"),
        ])
        keyboard_rows.append([
            InlineKeyboardButton("All: Group All Msgs", callback_data="ub_all_gmode:all"),
            InlineKeyboardButton("All: Tag Only", callback_data="ub_all_gmode:mention"),
        ])
        keyboard_rows.append([
            InlineKeyboardButton("All Settings (Bulk)", callback_data="ub_all_panel"),
            InlineKeyboardButton("Add Userbot", callback_data="ub_add"),
        ])
        keyboard_rows.append([
            InlineKeyboardButton("Refresh", callback_data="ub_main_refresh"),
            InlineKeyboardButton("Back", callback_data="ad_main"),
        ])

        return text, InlineKeyboardMarkup(keyboard_rows)


async def _build_all_userbots_panel(viewer_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Bulk management panel to configure ALL userbots at once."""
    if is_owner(viewer_id):
        accs = await get_served_accounts()
    else:
        accs = await get_accounts_by_user(viewer_id)

    connected_count = sum(
        1 for a in accs
        if a.get("account_id") in ub_accounts and ub_accounts[a["account_id"]].get("status") == "CONNECTED"
    )
    text = (
        "╭───「 Manage All Userbots 」\n"
        f"│ Total Accounts: {len(accs)}  │  Connected: {connected_count}\n"
        "╰──────────────────────\n\n"
        "Apply any setting below to **all** your userbots in one tap:\n\n"
        "• **AutoChat:** Turn chatting ON or OFF for all\n"
        "• **Mode:** Chat Only / React Only / Chat + React\n"
        "• **Group:** All Messages vs Tagged Only\n"
        "• **Scope:** Both / Groups Only / DMs Only\n"
        "• **Speed:** Fast (0s) / Medium (5s) / Slow (10s)"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("All AutoChat: ON", callback_data="ub_all_ac:on"),
            InlineKeyboardButton("All AutoChat: OFF", callback_data="ub_all_ac:off"),
        ],
        [
            InlineKeyboardButton("Chat Only", callback_data="ub_all_action:chat"),
            InlineKeyboardButton("React Only", callback_data="ub_all_action:react"),
            InlineKeyboardButton("Chat + React", callback_data="ub_all_action:both"),
        ],
        [
            InlineKeyboardButton("Group: All Msgs", callback_data="ub_all_gmode:all"),
            InlineKeyboardButton("Group: Tagged Only", callback_data="ub_all_gmode:mention"),
        ],
        [
            InlineKeyboardButton("In: Both", callback_data="ub_all_scope:both"),
            InlineKeyboardButton("In: Groups", callback_data="ub_all_scope:groups"),
            InlineKeyboardButton("In: DMs", callback_data="ub_all_scope:private"),
        ],
        [
            InlineKeyboardButton("Speed: Fast", callback_data="ub_all_speed:fast"),
            InlineKeyboardButton("Medium", callback_data="ub_all_speed:medium"),
            InlineKeyboardButton("Slow", callback_data="ub_all_speed:slow"),
        ],
        [
            InlineKeyboardButton("Send AutoChat Now", callback_data="ub_ac_now"),
            InlineKeyboardButton("Restart All", callback_data="ub_all_restart"),
        ],
        [
            InlineKeyboardButton("Back to Userbots", callback_data="ub_back_main"),
        ],
    ])
    return text, markup


async def _build_userbot_panel(account_id: str, viewer_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build dedicated management panel for a single userbot."""
    can_manage, acc = await _can_manage_account(viewer_id, account_id)
    if not can_manage or not acc:
        return "Unauthorized to view this userbot account.", InlineKeyboardMarkup([
            [InlineKeyboardButton("Back to Accounts", callback_data="ub_back_main")]
        ])

    in_mem = ub_accounts.get(account_id, {})
    telegram_id = in_mem.get("telegram_id", acc.get("telegram_id", 0))
    username = in_mem.get("username", acc.get("username", ""))
    username_str = f"@{username}" if username else "—"
    name = in_mem.get("name", acc.get("name", "Unknown"))

    phone = in_mem.get("phone") or acc.get("phone") or ""
    if not phone:
        cl = in_mem.get("client")
        c_me = getattr(cl, "me", None) if cl else None
        if c_me and getattr(c_me, "phone_number", None):
            phone = f"+{str(c_me.phone_number).lstrip('+')}"
            in_mem["phone"] = phone
            try:
                from Myra.userbot.storage import update_account
                import asyncio
                asyncio.create_task(update_account(account_id, {"phone": phone}))
            except Exception:
                pass
    elif phone and not phone.startswith("+"):
        phone = f"+{phone}"

    phone_display = phone if phone else "Not available"

    await userbot_manager.check_account_alive(account_id)
    in_mem = ub_accounts.get(account_id, {})
    _, status_str = _format_ub_status(account_id, acc, in_mem)

    autochat_state = in_mem.get("autochat", acc.get("autochat", True))
    autochat_label = "ON" if autochat_state else "OFF"
    autochat_btn_text = "Turn AutoChat OFF" if autochat_state else "Turn AutoChat ON"

    delay = in_mem.get("reply_delay", acc.get("reply_delay", 0))
    speed_mode = in_mem.get("speed_mode", acc.get("speed_mode", "fast"))
    speed_label = _format_speed_label(speed_mode, delay)

    _gm = in_mem.get("group_mode") or acc.get("group_mode") or (
        "all" if config.USERBOT_GROUP_REPLY_ALL else "mention"
    )
    group_label = "All messages" if _gm == "all" else "Only when tagged"
    _chance = in_mem.get("reply_chance", acc.get("reply_chance"))
    chance_label = "Global" if _chance is None else f"{float(_chance):g}%"

    _scope = in_mem.get("reply_scope") or acc.get("reply_scope") or "both"
    scope_label_map = {
        "both": "Both (Groups & DMs)",
        "groups": "Groups Only",
        "private": "DMs Only",
    }
    scope_label = scope_label_map.get(_scope, "Both")

    _action = in_mem.get("action_mode") or acc.get("action_mode") or "chat"
    action_label_map = {
        "chat": "Chat Only",
        "react": "React Only (Positive)",
        "both": "Chat + React",
    }
    action_label = action_label_map.get(_action, "Chat Only")

    added_by = acc.get("added_by")
    owner_id = owner_id_sync()
    if added_by and owner_id and int(added_by) == owner_id:
        creator_str = "Owner"
    elif added_by:
        creator_str = f"Admin ({added_by})"
    else:
        creator_str = "Owner"

    panel_text = (
        f"╭───「 Userbot: {name} 」\n"
        f"│ ID: {telegram_id}  │  {username_str}\n"
        f"│ Phone: {phone_display}  │  By: {creator_str}\n"
        f"│ Status: {status_str}\n"
        "╰──────────────────────\n\n"
        f"• AutoChat: **{autochat_label}**\n"
        f"• Action Mode: **{action_label}**\n"
        f"• Group Reply: **{group_label}**\n"
        f"• Reply In: **{scope_label}**\n"
        f"• Speed: **{speed_label}**  │  Chance: **{chance_label}**\n\n"
        "Tip: Tap **Mode** below to switch Chat Only / React Only / Chat + React."
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(autochat_btn_text, callback_data=f"ub_toggle:{account_id}"),
            InlineKeyboardButton(f"Mode: {action_label}", callback_data=f"ub_action:{account_id}"),
        ],
        [
            InlineKeyboardButton(f"Group: {group_label}", callback_data=f"ub_gmode:{account_id}"),
            InlineKeyboardButton(f"In: {scope_label}", callback_data=f"ub_scope:{account_id}"),
        ],
        [
            InlineKeyboardButton("Speed Settings", callback_data=f"ub_speed:{account_id}"),
            InlineKeyboardButton("Send AutoChat Now", callback_data=f"ub_sendnow:{account_id}"),
        ],
        [
            InlineKeyboardButton("Restart", callback_data=f"ub_restart:{account_id}"),
            InlineKeyboardButton("Info", callback_data=f"ub_info:{account_id}"),
            InlineKeyboardButton("Remove", callback_data=f"ub_remove:{account_id}"),
        ],
        [InlineKeyboardButton("Back to Accounts List", callback_data="ub_back_main")],
    ])

    return panel_text, markup


async def _build_speed_menu(account_id: str, viewer_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build speed configuration menu for a userbot."""
    can_manage, acc = await _can_manage_account(viewer_id, account_id)
    if not can_manage or not acc:
        return "❌ Unauthorized.", InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Back", callback_data="ub_back_main")]
        ])

    in_mem = ub_accounts.get(account_id, {})
    name = in_mem.get("name", acc.get("name", "Userbot"))
    username = in_mem.get("username", acc.get("username", ""))
    username_str = f"@{username}" if username else ""

    delay = in_mem.get("reply_delay", acc.get("reply_delay", 0))
    speed_mode = in_mem.get("speed_mode", acc.get("speed_mode", "fast"))
    current_speed_str = _format_speed_label(speed_mode, delay)

    group_mode = in_mem.get("group_mode") or acc.get("group_mode") or (
        "all" if config.USERBOT_GROUP_REPLY_ALL else "mention"
    )
    group_label = "All messages 💬" if group_mode == "all" else "Only when tagged 👤"

    chance = in_mem.get("reply_chance", acc.get("reply_chance"))
    if chance is None:
        chance_label = f"Global ({await ai_settings.get_group_chance():g}%)"
    else:
        chance_label = f"{float(chance):g}%"

    _scope = in_mem.get("reply_scope") or acc.get("reply_scope") or "both"
    scope_map = {
        "both": "Both (Groups & DMs) 🌐",
        "groups": "Groups Only 👥",
        "private": "DMs Only ✉️",
    }
    scope_label = scope_map.get(_scope, "Both 🌐")

    speed_text = (
        f"⚡ **Reply Behaviour: {name}** {username_str}\n\n"
        f"📊 **Current Speed:** `{current_speed_str}`\n"
        f"🌐 **Reply In:** `{scope_label}`\n"
        f"💬 **Group Reply:** `{group_label}`\n"
        f"🎲 **Reply Chance:** `{chance_label}`\n\n"
        "**Speed** — how fast it replies (sets typing delay & prevents spam):\n"
        "• ⚡ **Fast**: Instant reply (0s delay)\n"
        "• ⏱ **Medium**: 5s delay (human pace)\n"
        "• 🐢 **Slow**: 10s delay (relaxed pace)\n"
        "• ⚙️ **Custom**: Enter custom delay (e.g. `5s` or `1500`)\n"
        "**Reply In** — reply in groups only, DMs only, or both.\n"
        "**Group Reply** — reply to every message, or only when tagged.\n"
        "**Reply Chance** — how often it replies (% probability).\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Fast (0s)", callback_data=f"ub_setspeed:{account_id}:fast"),
            InlineKeyboardButton("Medium (5s)", callback_data=f"ub_setspeed:{account_id}:medium"),
        ],
        [
            InlineKeyboardButton("Slow (10s)", callback_data=f"ub_setspeed:{account_id}:slow"),
            InlineKeyboardButton("Custom Delay", callback_data=f"ub_setspeed:{account_id}:custom"),
        ],
        [
            InlineKeyboardButton(f"Reply In: {_scope.title()}", callback_data=f"ub_scope:{account_id}"),
            InlineKeyboardButton("Group Mode", callback_data=f"ub_gmode:{account_id}"),
        ],
        [
            InlineKeyboardButton("Reply Chance", callback_data=f"ub_chance:{account_id}"),
        ],
        [InlineKeyboardButton(f"Back to {name}", callback_data=f"ub_panel:{account_id}")],
    ])

    return speed_text, markup


async def _build_admin_management_panel() -> tuple[str, InlineKeyboardMarkup]:
    """Build the Admin Management panel for the Bot Owner."""
    admins_list = await get_admins()

    text = (
        "╭───「 Bot Admin Management 」\n"
        f"│ Authorized Admins: {len(admins_list)}\n"
        "╰──────────────────────\n\n"
        "Admins added here can use /useraccounts to add and manage their own userbots.\n\n"
    )

    keyboard_rows = []

    if not admins_list:
        text += "No admins added yet.\nClick [ Add Admin ] to authorize a user."
    else:
        for idx, adm in enumerate(admins_list, 1):
            adm_id = adm["user_id"]
            adm_name = adm.get("name", "Admin")
            adm_user = adm.get("username", "")
            user_tag = f"@{adm_user}" if adm_user else f"ID: {adm_id}"
            text += f"{idx}. {adm_name} ({user_tag})\n"
            keyboard_rows.append([
                InlineKeyboardButton(f"{adm_name} ({adm_id})", callback_data="ub_noop"),
                InlineKeyboardButton("Remove", callback_data=f"ub_deladmin_btn:{adm_id}"),
            ])

    keyboard_rows.append([
        InlineKeyboardButton("Add Admin", callback_data="ub_add_admin_prompt"),
        InlineKeyboardButton("Refresh", callback_data="ub_manage_admins"),
    ])
    keyboard_rows.append([
        InlineKeyboardButton("Back to Accounts", callback_data="ub_back_main"),
    ])

    return text, InlineKeyboardMarkup(keyboard_rows)


# ---------- Command Handlers ----------

@Myra.on_cmd("useraccounts")
@global_scope
async def useraccounts_cmd(_, m: Message):
    """Open the userbot management dashboard (Owner & Admins only)."""
    user_id = m.from_user.id
    if not (await is_admin(user_id)):
        return await m.reply("❌ You are not authorized to use this command.\nOnly Bot Owner and authorized Admins can manage userbots.")

    if m.chat.type != enums.ChatType.PRIVATE:
        return await m.reply("❌ This command works only in private chat with the bot.")

    text, markup = await _build_main_dashboard(user_id)
    await m.reply_text(text, reply_markup=markup, disable_web_page_preview=True)


@Myra.on_cmd("addadmin")
@global_scope
async def addadmin_cmd(client: Myra, m: Message):
    """Command to add an authorized admin via /addadmin <user_id/username/reply>."""
    if not is_owner(m.from_user.id) and not await is_admin(m.from_user.id):
        return await m.reply("❌ Only the Bot Owner and authorized Admins can add admins.")

    target_user_id = None
    target_name = ""
    target_username = ""

    if m.reply_to_message and m.reply_to_message.from_user:
        u = m.reply_to_message.from_user
        target_user_id = u.id
        target_name = f"{u.first_name or ''} {u.last_name or ''}".strip()
        target_username = u.username or ""
    elif len(m.command) > 1:
        query = m.command[1].strip()
        try:
            if query.isdigit() or (query.startswith("-") and query[1:].isdigit()):
                target_user_id = int(query)
            else:
                user_obj = await client.get_users(query)
                target_user_id = user_obj.id
                target_name = f"{user_obj.first_name or ''} {user_obj.last_name or ''}".strip()
                target_username = user_obj.username or ""
        except Exception as e:
            return await m.reply(f"❌ User not found: {e}")

    if not target_user_id:
        pending_auth[m.from_user.id] = {
            "step": "add_admin",
            "created_at": time.time(),
        }
        return await m.reply(
            "👑 **Add New Admin**\n\n"
            "Please send the **Telegram User ID** or **@username** of the user you want to add as Admin.\n\n"
            "Send /cancel to cancel.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Cancel", callback_data="ub_cancel_admin_prompt")]
            ])
        )

    # Try resolving user info if not fetched
    if not target_name:
        try:
            user_obj = await client.get_users(target_user_id)
            target_name = f"{user_obj.first_name or ''} {user_obj.last_name or ''}".strip()
            target_username = user_obj.username or ""
        except Exception:
            target_name = f"User {target_user_id}"

    success = await add_admin(target_user_id, added_by=m.from_user.id, name=target_name, username=target_username)
    if success:
        await m.reply(
            "✅ **Admin Added Successfully!**\n\n"
            f"👤 **Name:** {target_name}\n"
            f"🆔 **ID:** `{target_user_id}`\n"
            f"🔗 **Username:** @{target_username if target_username else '—'}\n\n"
            "Now this user can use `/useraccounts` in bot PM to add and manage their own userbots!"
        )
    else:
        await m.reply("❌ Failed to add admin to database.")


@Myra.on_cmd(["deladmin", "rmadmin"])
@global_scope
async def deladmin_cmd(client: Myra, m: Message):
    """Command to remove an authorized admin via /deladmin <user_id/reply>."""
    if not is_owner(m.from_user.id) and not await is_admin(m.from_user.id):
        return await m.reply("❌ Only the Bot Owner and authorized Admins can remove admins.")

    target_user_id = None
    if m.reply_to_message and m.reply_to_message.from_user:
        target_user_id = m.reply_to_message.from_user.id
    elif len(m.command) > 1:
        query = m.command[1].strip()
        try:
            if query.isdigit() or (query.startswith("-") and query[1:].isdigit()):
                target_user_id = int(query)
            else:
                user_obj = await client.get_users(query)
                target_user_id = user_obj.id
        except Exception as e:
            return await m.reply(f"❌ User not found: {e}")

    if not target_user_id:
        return await m.reply("❌ Usage: `/deladmin <user_id>` or reply to a user message.")

    success = await remove_admin(target_user_id)
    if success:
        await m.reply(f"🗑 **Admin `{target_user_id}` removed successfully!**")
    else:
        await m.reply(f"❌ Admin `{target_user_id}` was not found in admin list.")


@Myra.on_cmd("admins")
@global_scope
async def admins_list_cmd(_, m: Message):
    """View list of all authorized bot admins (Owner & Admins only)."""
    user_id = m.from_user.id
    if not (await is_admin(user_id)):
        return await m.reply("❌ You are not authorized to view the admin list.")

    owner_id = owner_id_sync() or "—"
    owner_user = config.OWNER_USERNAME or "—"
    admins_list = await get_admins()

    text = (
        "🛡 **BOT ADMINS LIST**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 **Owner:** `{owner_id}` (@{owner_user})\n\n"
        f"👥 **Authorized Admins:** ({len(admins_list)})\n"
    )

    if not admins_list:
        text += "*(No sub-admins added yet)*\n"
    else:
        for idx, adm in enumerate(admins_list, 1):
            adm_id = adm["user_id"]
            adm_name = adm.get("name", "Admin")
            adm_user = adm.get("username", "")
            user_tag = f"@{adm_user}" if adm_user else "—"
            text += f"{idx}. 👤 **{adm_name}** (`{adm_id}`) | {user_tag}\n"

    text += "━━━━━━━━━━━━━━━━━━━━━━"
    await m.reply(text)


# ---------- Callback Query Handlers (Group -1 to ensure priority) ----------

@Myra.on_callback_query(filters.regex(r"^ub_"), group=-1)
@global_scope
async def useraccounts_callbacks(client: Myra, query: CallbackQuery):
    """Handle all useraccounts dashboard inline callbacks."""
    user_id = query.from_user.id
    data = query.data

    # Noop callback
    if data == "ub_noop":
        return await query.answer()

    # Permission check: User must be admin or owner
    if not (await is_admin(user_id)):
        return await query.answer("❌ Unauthorized", show_alert=True)

    # Main Dashboard / Refresh
    if data in ("ub_main_refresh", "ub_back_main"):
        text, markup = await _build_main_dashboard(user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        await query.answer("🔄 Dashboard refreshed")
        return

    # Admin Management Panel
    if data == "ub_manage_admins":
        if not is_owner(user_id) and not await is_admin(user_id):
            return await query.answer("❌ Only Bot Owner/Admin can manage admins.", show_alert=True)

        text, markup = await _build_admin_management_panel()
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        await query.answer()
        return

    # Add Admin prompt
    if data == "ub_add_admin_prompt":
        if not is_owner(user_id) and not await is_admin(user_id):
            return await query.answer("❌ Only Bot Owner/Admin can add admins.", show_alert=True)

        pending_auth[user_id] = {
            "step": "add_admin",
            "created_at": time.time(),
        }
        await query.message.edit_text(
            "👑 **Add New Admin**\n\n"
            "Please send the **Telegram User ID** or **@username** of the user you want to authorize as Bot Admin.\n\n"
            "Send /cancel to cancel.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Cancel", callback_data="ub_cancel_admin_prompt")]
            ])
        )
        await query.answer()
        return

    # Cancel Add Admin prompt
    if data == "ub_cancel_admin_prompt":
        pending_auth.pop(user_id, None)
        text, markup = await _build_admin_management_panel()
        await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        await query.answer("❌ Cancelled")
        return

    # Owner: Delete Admin by button
    if data.startswith("ub_deladmin_btn:"):
        if not is_owner(user_id):
            return await query.answer("❌ Only Bot Owner can remove admins.", show_alert=True)

        target_id = int(data.split(":", 1)[1])
        await remove_admin(target_id)
        await query.answer(f"🗑 Admin {target_id} removed!", show_alert=True)
        text, markup = await _build_admin_management_panel()
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Toggle Group Join Request / Invite Notifications
    if data == "ub_toggle_notify":
        if not is_owner(user_id) and not await is_admin(user_id):
            return await query.answer("❌ Only Bot Owner/Admin can change notification settings.", show_alert=True)
        now = not await ai_settings.get_joinreq_notify()
        await ai_settings.set_joinreq_notify(now)
        state_str = "🟢 ON (Notifications enabled)" if now else "🔴 OFF (Notifications muted)"
        await query.answer(f"Invite Notifications: {state_str}", show_alert=True)
        text, markup = await _build_main_dashboard(user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Open Individual Userbot Panel
    if data.startswith("ub_panel:"):
        account_id = data.split(":", 1)[1]
        can_manage, _ = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to manage this userbot.", show_alert=True)

        text, markup = await _build_userbot_panel(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        await query.answer()
        return

    # Add New Userbot (Owner or Admin)
    if data == "ub_add":
        pending_auth[user_id] = {
            "step": "phone",
            "created_at": time.time(),
        }
        await query.message.edit_text(
            "📱 **Add New Userbot**\n\n"
            "Please send your Telegram phone number with country code.\n\n"
            "Example: `+919876543210`\n\n"
            "Tip: OTP flow ki zarurat nahi — isi chat me apni `.session` file "
            "(ya many accounts ki `.zip`) bhej do, turant add ho jayegi.\n\n"
            "Send /cancel to cancel this operation.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Cancel", callback_data="ub_cancel_auth")]
            ])
        )
        await query.answer()
        return

    if data == "ub_cancel_auth":
        pending_auth.pop(user_id, None)
        text, markup = await _build_main_dashboard(user_id)
        await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        await query.answer("❌ Operation cancelled")
        return

    # Toggle AutoChat
    if data.startswith("ub_toggle:"):
        account_id = data.split(":", 1)[1]
        can_manage, _ = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to manage this userbot.", show_alert=True)

        new_state = await userbot_manager.toggle_autochat(account_id)
        state_str = "ON" if new_state else "OFF"
        await query.answer(f"✅ AutoChat is now {state_str}!", show_alert=True)
        text, markup = await _build_userbot_panel(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Cycle Action Mode (Chat Only -> React Only -> Chat + React)
    if data.startswith("ub_action:"):
        account_id = data.split(":", 1)[1]
        can_manage, acc = await _can_manage_account(user_id, account_id)
        if not can_manage or not acc:
            return await query.answer("❌ Unauthorized.", show_alert=True)
        in_mem = ub_accounts.get(account_id, {})
        cur = in_mem.get("action_mode") or acc.get("action_mode") or "chat"
        order = ("chat", "react", "both")
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "chat"
        await userbot_manager.set_action_mode(account_id, nxt)
        labels = {
            "chat": "Chat Only",
            "react": "React Only (Positive Emoji)",
            "both": "Chat + Emoji React",
        }
        await query.answer(f"Action Mode: {labels[nxt]}", show_alert=True)
        text, markup = await _build_userbot_panel(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Toggle Group Mode (All vs Mention)
    if data.startswith("ub_gmode:"):
        account_id = data.split(":", 1)[1]
        can_manage, acc = await _can_manage_account(user_id, account_id)
        if not can_manage or not acc:
            return await query.answer("❌ Unauthorized.", show_alert=True)
        in_mem = ub_accounts.get(account_id, {})
        cur = in_mem.get("group_mode") or acc.get("group_mode") or "all"
        new_mode = "mention" if cur == "all" else "all"
        await userbot_manager.set_group_mode(account_id, new_mode)
        await query.answer(
            "Group Reply: All messages" if new_mode == "all" else "Group Reply: Only when tagged",
            show_alert=True,
        )
        text, markup = await _build_userbot_panel(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Cycle Reply Scope (Both -> Groups -> DMs)
    if data.startswith("ub_scope:"):
        account_id = data.split(":", 1)[1]
        can_manage, acc = await _can_manage_account(user_id, account_id)
        if not can_manage or not acc:
            return await query.answer("❌ Unauthorized.", show_alert=True)
        in_mem = ub_accounts.get(account_id, {})
        cur = in_mem.get("reply_scope") or acc.get("reply_scope") or "both"
        order = ("both", "groups", "private")
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "both"
        await userbot_manager.set_reply_scope(account_id, nxt)
        labels = {"both": "Both (Groups & DMs)", "groups": "Groups Only", "private": "DMs Only"}
        await query.answer(f"Reply In: {labels[nxt]}", show_alert=True)
        text, markup = await _build_userbot_panel(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Send AutoChat Now for this specific userbot
    if data.startswith("ub_sendnow:"):
        account_id = data.split(":", 1)[1]
        can_manage, _ = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ Unauthorized.", show_alert=True)
        from Myra.services.autochat import autochat_service
        sent, total = await autochat_service.send_now_account(account_id)
        if total == 0:
            msg = "No groups/chats found for this userbot (or userbot disconnected)."
        else:
            msg = f"Sent AutoChat to {sent}/{total} group(s)/chat(s)!"
        await query.answer(msg, show_alert=True)
        return

    # Global Userbot AutoChat Loop Toggle from Userbots Pool
    if data == "ub_ac_toggle":
        if not await is_admin(user_id):
            return await query.answer("❌ Unauthorized.", show_alert=True)
        from Myra.database import autochat as ac
        from Myra.services.autochat import autochat_service
        new_state = not await ac.get_ub_enabled()
        await ac.set_ub_enabled(new_state)
        for aid, acc in list(ub_accounts.items()):
            acc["auto_enabled"] = new_state
            if new_state:
                acc["autochat"] = True
            await update_account(aid, {"auto_enabled": new_state, **({"autochat": True} if new_state else {})})
        autochat_service.reset_timers()
        await query.answer(f"Userbots AutoChat Loop: {'ON' if new_state else 'OFF'}", show_alert=True)
        text, markup = await _build_main_dashboard(user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Global Send AutoChat Now for all userbots from Userbots Pool
    if data == "ub_ac_now":
        if not await is_admin(user_id):
            return await query.answer("❌ Unauthorized.", show_alert=True)
        from Myra.services.autochat import autochat_service
        from Myra.database.scope import current_scope
        sent = await autochat_service.send_now("ub", scope=current_scope())
        await query.answer(f"Sent AutoChat message(s): {sent}", show_alert=True)
        return

    # ── Manage All Userbots Together ───────────────────────────────────────
    if data == "ub_all_panel":
        if not await is_admin(user_id):
            return await query.answer("❌ Unauthorized.", show_alert=True)
        cur_text = (query.message.text or "") if query.message else ""
        if "Manage All Userbots" in cur_text:
            text, markup = await _build_all_userbots_panel(user_id)
        else:
            text, markup = await _build_main_dashboard(user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        await query.answer()
        return

    if data.startswith(("ub_all_ac:", "ub_all_action:", "ub_all_gmode:", "ub_all_scope:", "ub_all_speed:")) or data == "ub_all_restart":
        if not await is_admin(user_id):
            return await query.answer("❌ Unauthorized.", show_alert=True)
        if is_owner(user_id):
            from Myra.userbot.storage import get_all_served_accounts
            target_accs = await get_all_served_accounts()
        else:
            target_accs = await get_accounts_by_user(user_id)
        # Ensure all live in-memory accounts owned by this viewer are also included
        seen_ids = {a["account_id"] for a in target_accs}
        for aid, acc_mem in list(ub_accounts.items()):
            if aid not in seen_ids and (is_owner(user_id) or acc_mem.get("added_by") == user_id):
                target_accs.append({"account_id": aid, **acc_mem})
        if not target_accs:
            return await query.answer("No userbot accounts found.", show_alert=True)

        count = 0
        if data.startswith("ub_all_ac:"):
            state = data.split(":", 1)[1] == "on"
            for a in target_accs:
                aid = a["account_id"]
                sc = (ub_accounts.get(aid) or a).get("scope")
                if aid in ub_accounts:
                    ub_accounts[aid]["autochat"] = state
                    ub_accounts[aid]["auto_enabled"] = state
                await update_account(aid, {"autochat": state, "auto_enabled": state}, scope=sc)
                count += 1
            alert = f"AutoChat {'ON' if state else 'OFF'} for {count} userbot(s)!"
        elif data.startswith("ub_all_action:"):
            mode = data.split(":", 1)[1]
            for a in target_accs:
                aid = a["account_id"]
                sc = (ub_accounts.get(aid) or a).get("scope")
                if aid in ub_accounts:
                    ub_accounts[aid]["action_mode"] = mode
                await update_account(aid, {"action_mode": mode}, scope=sc)
                count += 1
            try:
                from Myra.userbot.handlers import _chat_reactions_cache, _last_account_react_at
                _chat_reactions_cache.clear()
                _last_account_react_at.clear()
            except Exception:
                pass
            labels = {"chat": "Chat Only", "react": "React Only", "both": "Chat + React"}
            LOGGER.info(f"⚡ Bulk action_mode='{mode}' set for {count} userbot(s) by {user_id}")
            alert = f"Mode '{labels.get(mode, mode)}' set for {count} userbot(s)!"
        elif data.startswith("ub_all_gmode:"):
            gmode = data.split(":", 1)[1]
            for a in target_accs:
                await userbot_manager.set_group_mode(a["account_id"], gmode)
                count += 1
            alert = f"Group mode '{'All Msgs' if gmode == 'all' else 'Tagged Only'}' set for {count} userbot(s)!"
        elif data.startswith("ub_all_scope:"):
            rscope = data.split(":", 1)[1]
            for a in target_accs:
                await userbot_manager.set_reply_scope(a["account_id"], rscope)
                count += 1
            alert = f"Reply scope '{rscope}' set for {count} userbot(s)!"
        elif data.startswith("ub_all_speed:"):
            sp = data.split(":", 1)[1]
            dmap = {"fast": 0, "medium": 5000, "slow": 10000}
            delay = dmap.get(sp, 5000)
            for a in target_accs:
                await userbot_manager.set_speed(a["account_id"], delay=delay, mode=sp)
                count += 1
            alert = f"Speed '{sp}' set for {count} userbot(s)!"
        else:  # ub_all_restart
            await query.answer("Restarting all userbots...", show_alert=False)
            for a in target_accs:
                if await userbot_manager.restart_account(a["account_id"]):
                    count += 1
            alert = f"Restarted {count}/{len(target_accs)} userbot(s)!"

        cur_text = (query.message.text or "") if query.message else ""
        if "Manage All Userbots" in cur_text:
            text, markup = await _build_all_userbots_panel(user_id)
        else:
            text, markup = await _build_main_dashboard(user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        if data != "ub_all_restart":
            await query.answer(alert, show_alert=True)
        else:
            try:
                await query.message.reply_text(alert)
            except Exception:
                pass
        return

    # Open Speed Menu
    if data.startswith("ub_speed:"):
        account_id = data.split(":", 1)[1]
        can_manage, _ = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to manage this userbot.", show_alert=True)

        text, markup = await _build_speed_menu(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        await query.answer()
        return

    # Set Speed Presets
    if data.startswith("ub_setspeed:"):
        parts = data.split(":")
        account_id = parts[1]
        mode = parts[2]

        can_manage, _ = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to manage this userbot.", show_alert=True)

        if mode == "fast":
            await userbot_manager.set_speed(account_id, delay=0, mode="fast")
            await query.answer("⚡ Fast mode set! (Instant reply)", show_alert=True)
            text, markup = await _build_speed_menu(account_id, user_id)
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            return

        elif mode == "medium":
            await userbot_manager.set_speed(account_id, delay=5000, mode="medium")
            await query.answer("⏱ Medium mode set! (5s delay)", show_alert=True)
            text, markup = await _build_speed_menu(account_id, user_id)
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            return

        elif mode == "slow":
            await userbot_manager.set_speed(account_id, delay=10000, mode="slow")
            await query.answer("🐢 Slow mode set! (10s delay)", show_alert=True)
            text, markup = await _build_speed_menu(account_id, user_id)
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            return

        elif mode == "custom":
            pending_auth[user_id] = {
                "step": "custom_delay",
                "account_id": account_id,
                "created_at": time.time(),
            }
            await query.message.edit_text(
                "⚙️ **Enter Custom Reply Delay**\n\n"
                "Aap delay **seconds** ya **milliseconds** mein bhej sakte ho:\n"
                "• `5s` ya `5` = 5 seconds (5000 ms)\n"
                "• `10s` ya `10` = 10 seconds (10000 ms)\n"
                "• `1500` = 1.5 seconds (1500 ms)\n"
                "• `0` = Instant (no delay)\n\n"
                "Send /cancel to cancel.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Cancel", callback_data=f"ub_speed:{account_id}")]
                ])
            )
            await query.answer()
            return

    # Toggle Reply Scope (both -> groups -> private -> both)
    if data.startswith("ub_scope:"):
        account_id = data.split(":", 1)[1]
        can_manage, acc = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to manage this userbot.", show_alert=True)

        in_mem = ub_accounts.get(account_id, {})
        current = in_mem.get("reply_scope") or (acc or {}).get("reply_scope") or "both"
        cycle = ["both", "groups", "private"]
        try:
            idx = cycle.index(current)
        except ValueError:
            idx = 0
        new_scope = cycle[(idx + 1) % len(cycle)]
        await userbot_manager.set_reply_scope(account_id, new_scope)

        lbl = {"both": "Both (Groups & DMs) 🌐", "groups": "Groups Only 👥", "private": "DMs Only ✉️"}.get(new_scope)
        await query.answer(f"🌐 Reply Scope: {lbl}", show_alert=True)
        text, markup = await _build_speed_menu(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Toggle Group Reply Mode (all messages vs only when tagged)
    if data.startswith("ub_gmode:"):
        account_id = data.split(":", 1)[1]
        can_manage, acc = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to manage this userbot.", show_alert=True)

        in_mem = ub_accounts.get(account_id, {})
        current = in_mem.get("group_mode") or (acc or {}).get("group_mode") or (
            "all" if config.USERBOT_GROUP_REPLY_ALL else "mention"
        )
        new_mode = "mention" if current == "all" else "all"
        await userbot_manager.set_group_mode(account_id, new_mode)

        label = "ALL messages 💬" if new_mode == "all" else "only when tagged 👤"
        await query.answer(f"✅ Group reply: {label}", show_alert=True)
        text, markup = await _build_speed_menu(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Cycle Reply Chance (None -> 100 -> 85 -> 70 -> 50 -> 30 -> 15 -> None)
    if data.startswith("ub_chance:"):
        account_id = data.split(":", 1)[1]
        can_manage, acc = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to manage this userbot.", show_alert=True)

        in_mem = ub_accounts.get(account_id, {})
        current = in_mem.get("reply_chance", (acc or {}).get("reply_chance"))
        cycle = [None, 100, 85, 70, 50, 30, 15]
        try:
            index = cycle.index(current)
        except (ValueError, TypeError):
            index = 0
        new_value = cycle[(index + 1) % len(cycle)]
        await userbot_manager.set_reply_chance(account_id, new_value)

        label = "Global default" if new_value is None else f"{new_value:g}%"
        await query.answer(f"🎲 Reply chance: {label}", show_alert=True)
        text, markup = await _build_speed_menu(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Restart Userbot
    if data.startswith("ub_restart:"):
        account_id = data.split(":", 1)[1]
        can_manage, _ = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to manage this userbot.", show_alert=True)

        await query.answer("🔄 Restarting userbot...", show_alert=False)
        success = await userbot_manager.restart_account(account_id)
        if success:
            await query.answer("✅ Userbot restarted successfully!", show_alert=True)
        else:
            await query.answer("❌ Failed to restart userbot. Session may be expired.", show_alert=True)
        text, markup = await _build_userbot_panel(account_id, user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    # Account Info
    if data.startswith("ub_info:"):
        account_id = data.split(":", 1)[1]
        can_manage, acc = await _can_manage_account(user_id, account_id)
        if not can_manage or not acc:
            await query.answer("❌ Unauthorized / Account not found", show_alert=True)
            return

        in_mem = ub_accounts.get(account_id, {})
        telegram_id = in_mem.get("telegram_id", acc.get("telegram_id", 0))
        username = in_mem.get("username", acc.get("username", ""))
        name = in_mem.get("name", acc.get("name", "Unknown"))

        phone = in_mem.get("phone") or acc.get("phone") or ""
        if not phone:
            cl = in_mem.get("client")
            c_me = getattr(cl, "me", None) if cl else None
            if c_me and getattr(c_me, "phone_number", None):
                phone = f"+{str(c_me.phone_number).lstrip('+')}"
                in_mem["phone"] = phone
        elif phone and not phone.startswith("+"):
            phone = f"+{phone}"
        phone_display = phone if phone else "Not available"

        await userbot_manager.check_account_alive(account_id)
        in_mem = ub_accounts.get(account_id, {})
        _, status_str = _format_ub_status(account_id, acc, in_mem)
        autochat_state = in_mem.get("autochat", acc.get("autochat", True))
        autochat_str = "ON" if autochat_state else "OFF"
        delay = in_mem.get("reply_delay", acc.get("reply_delay", 0))
        speed_mode = in_mem.get("speed_mode", acc.get("speed_mode", "fast"))
        speed_label = _format_speed_label(speed_mode, delay)

        added_by = acc.get("added_by", "Owner")
        owner_id = owner_id_sync()
        creator_str = "Owner" if (owner_id and int(added_by) == owner_id) else f"Admin ({added_by})"

        info_text = (
            "Userbot Account Info\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ID: `{telegram_id}`\n"
            f"Username: @{username if username else '—'}\n"
            f"Name: {name}\n"
            f"Phone: `{phone_display}`\n"
            f"Added By: {creator_str}\n"
            f"Connection: {status_str}\n"
            f"AutoChat: {autochat_str}\n"
            f"Speed: {speed_label}\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        info_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Back to {name}", callback_data=f"ub_panel:{account_id}")]
        ])
        await query.message.edit_text(info_text, reply_markup=info_markup, disable_web_page_preview=True)
        await query.answer()
        return

    # Remove Confirmation
    if data.startswith("ub_remove:"):
        account_id = data.split(":", 1)[1]
        can_manage, acc = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to remove this userbot.", show_alert=True)

        name = acc.get("name", "this userbot") if acc else "this userbot"
        confirm_text = (
            f"⚠️ **Are you sure you want to remove {name}?**\n\n"
            "Its Pyrogram client will be stopped and its session will be deleted."
        )
        confirm_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Confirm Remove", callback_data=f"ub_rm_confirm:{account_id}"),
                InlineKeyboardButton("Cancel", callback_data=f"ub_panel:{account_id}"),
            ]
        ])
        await query.message.edit_text(confirm_text, reply_markup=confirm_markup)
        await query.answer()
        return

    # Remove Confirmed
    if data.startswith("ub_rm_confirm:"):
        account_id = data.split(":", 1)[1]
        can_manage, _ = await _can_manage_account(user_id, account_id)
        if not can_manage:
            return await query.answer("❌ You do not have permission to remove this userbot.", show_alert=True)

        await userbot_manager.remove_account(account_id)
        await query.answer("🗑 Userbot removed successfully!", show_alert=True)
        text, markup = await _build_main_dashboard(user_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        return


# ---------- Message Handler (Auth Flow, Custom Delay & Interactive Admin Prompt) ----------

@Myra.on_message(filters.private & ~filters.bot & ~filters.forwarded, group=-1)
@global_scope
async def userbot_auth_messages_handler(client: Myra, m: Message):
    """Handle owner & admin auth messages (Phone, OTP, 2FA, Custom Delay, Add Admin input)."""
    user_id = m.from_user.id

    if not (await is_admin(user_id)):
        m.continue_propagation()
        return

    if user_id not in pending_auth:
        m.continue_propagation()
        return

    text = (m.text or "").strip()

    # Cancel handling
    if text == "/cancel":
        flow = pending_auth.pop(user_id, None)
        if flow and "account_id" in flow:
            text_p, markup_p = await _build_userbot_panel(flow["account_id"], user_id)
            await m.reply("❌ Setting cancelled.", reply_markup=markup_p)
        elif flow and flow.get("step") == "add_admin":
            text_a, markup_a = await _build_admin_management_panel()
            await m.reply("❌ Add admin cancelled.", reply_markup=markup_a)
        else:
            text_m, markup_m = await _build_main_dashboard(user_id)
            await m.reply("❌ Operation cancelled.", reply_markup=markup_m)
        m.stop_propagation()
        return

    flow = pending_auth[user_id]
    step = flow.get("step")

    # Step: Add Admin interactive input
    if step == "add_admin":
        if not is_owner(user_id) and not await is_admin(user_id):
            pending_auth.pop(user_id, None)
            m.continue_propagation()
            return

        target_user_id = None
        target_name = ""
        target_username = ""

        try:
            if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
                target_user_id = int(text)
            else:
                user_obj = await client.get_users(text)
                target_user_id = user_obj.id
                target_name = f"{user_obj.first_name or ''} {user_obj.last_name or ''}".strip()
                target_username = user_obj.username or ""
        except Exception as e:
            await m.reply(f"❌ Could not resolve user `{text}`: {e}\nPlease check and try again (or /cancel):")
            m.stop_propagation()
            return

        if not target_name:
            try:
                user_obj = await client.get_users(target_user_id)
                target_name = f"{user_obj.first_name or ''} {user_obj.last_name or ''}".strip()
                target_username = user_obj.username or ""
            except Exception:
                target_name = f"User {target_user_id}"

        pending_auth.pop(user_id, None)
        success = await add_admin(target_user_id, added_by=user_id, name=target_name, username=target_username)
        if success:
            await m.reply(
                "✅ **Admin Added Successfully!**\n\n"
                f"👤 **Name:** {target_name}\n"
                f"🆔 **ID:** `{target_user_id}`\n"
                f"🔗 **Username:** @{target_username if target_username else '—'}\n\n"
                "This user can now use `/useraccounts` in bot PM to add and manage their own userbots!"
            )
            admin_text, admin_markup = await _build_admin_management_panel()
            await m.reply_text(admin_text, reply_markup=admin_markup)
        else:
            await m.reply("❌ Failed to add admin to database.")

        m.stop_propagation()
        return

    # Step: Custom Delay input
    if step == "custom_delay":
        account_id = flow.get("account_id")
        can_manage, _ = await _can_manage_account(user_id, account_id)
        if not can_manage:
            pending_auth.pop(user_id, None)
            await m.reply("❌ Unauthorized for this account.")
            m.stop_propagation()
            return

        delay_ms = ai_settings.parse_delay_text(text)
        if delay_ms is None:
            await m.reply(
                "❌ Valid number ya seconds bhejo (e.g. `5`, `5s`, `2000`, `15000`):\n"
                "• `5` ya `5s` = 5 seconds (5000 ms)\n"
                "• `1500` = 1.5 seconds (1500 ms)\n"
                "• `0` = Instant\n"
                "`/cancel` se cancel kar sakte ho."
            )
            m.stop_propagation()
            return

        pending_auth.pop(user_id, None)
        await userbot_manager.set_speed(account_id, delay=delay_ms, mode="custom")
        await m.reply(f"✅ **Custom delay set to {delay_ms} ms ({delay_ms / 1000:g}s)!**")

        text_panel, markup_panel = await _build_userbot_panel(account_id, user_id)
        await m.reply_text(text_panel, reply_markup=markup_panel, disable_web_page_preview=True)
        m.stop_propagation()
        return

    # Step: Phone number received
    if step == "phone":
        progress_msg = await m.reply("⏳ Connecting to Telegram and sending OTP code...")
        success, response = await start_login_flow(user_id, text)
        await progress_msg.edit_text(response)
        m.stop_propagation()
        return

    # Step: OTP received in ONE SINGLE MESSAGE (e.g. "1 2 3 4 5")
    if step == "otp":
        progress_msg = await m.reply("⏳ Verifying OTP...")
        success, status, result = await process_otp_input(user_id, text)

        if success and status == "SUCCESS":
            acc_info = result
            success_card = (
                "✅ **Userbot connected successfully!**\n\n"
                f"👤 **Name:** {acc_info.get('name')}\n"
                f"🆔 **ID:** `{acc_info.get('telegram_id')}`\n"
                f"🔗 **Username:** @{acc_info.get('username') or '—'}\n"
                f"🟢 **Status:** Connected\n"
                f"🤖 **AutoChat:** ON\n\n"
                "Opening userbot settings panel..."
            )
            await progress_msg.edit_text(success_card)

            # Show individual panel for new userbot
            p_text, p_markup = await _build_userbot_panel(acc_info["account_id"], user_id)
            await m.reply_text(p_text, reply_markup=p_markup, disable_web_page_preview=True)

        elif status == "2FA_REQUIRED":
            await progress_msg.edit_text(result)

        elif status == "INVALID_FORMAT":
            await progress_msg.edit_text(result)

        elif status == "INVALID_OTP":
            await progress_msg.edit_text(result)

        else:
            await progress_msg.edit_text(str(result))

        m.stop_propagation()
        return

    # Step: 2FA Password received
    if step == "2fa":
        progress_msg = await m.reply("⏳ Verifying 2FA password...")
        success, status, result = await process_2fa_input(user_id, text)

        if success and status == "SUCCESS":
            acc_info = result
            success_card = (
                "✅ **Userbot connected successfully!**\n\n"
                f"👤 **Name:** {acc_info.get('name')}\n"
                f"🆔 **ID:** `{acc_info.get('telegram_id')}`\n"
                f"🔗 **Username:** @{acc_info.get('username') or '—'}\n"
                f"🟢 **Status:** Connected\n"
                f"🤖 **AutoChat:** ON\n\n"
                "Opening userbot settings panel..."
            )
            await progress_msg.edit_text(success_card)

            # Show individual panel for new userbot
            p_text, p_markup = await _build_userbot_panel(acc_info["account_id"], user_id)
            await m.reply_text(p_text, reply_markup=p_markup, disable_web_page_preview=True)

        else:
            await progress_msg.edit_text(str(result))

        m.stop_propagation()
        return

    m.continue_propagation()