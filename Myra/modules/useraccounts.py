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
    """Mask phone number for safe display (e.g. +91********10)."""
    if not phone:
        return "—"
    phone_clean = phone.strip()
    if len(phone_clean) <= 6:
        return phone_clean
    prefix = phone_clean[:3]
    suffix = phone_clean[-2:]
    return f"{prefix}********{suffix}"


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
        return "⚡ Fast (0s)"
    elif mode == "medium" or delay_ms == 5000:
        return f"⏱ Medium ({seconds:g}s)"
    elif mode == "slow" or delay_ms == 10000:
        return f"🐢 Slow ({seconds:g}s)"
    else:
        return f"⚙️ Custom ({seconds:g}s)"


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
            "👑 **MASTER USERBOT DASHBOARD (Owner View)**\n\n"
            f"📦 **Total Connected Accounts:** `{total_count}`\n"
            f"👑 **Owner Accounts:** `{len(owner_accs)}`\n"
            f"👥 **Admin Accounts:** `{len(admin_accs)}`\n"
            f"🛡 **Authorized Admins:** `{len(admins_list)}`\n\n"
        )

        keyboard_rows = []

        if not all_accounts:
            text += "ℹ️ *No userbot accounts connected yet.*\nClick **[ ➕ Add Userbot ]** to link an account."
        else:
            text += "Select any userbot below to open its dedicated settings & controls:\n"

            # Owner accounts section
            if owner_accs:
                for idx, acc in enumerate(owner_accs, 1):
                    account_id = acc["account_id"]
                    in_mem = ub_accounts.get(account_id, {})
                    name = in_mem.get("name", acc.get("name", "Unknown"))
                    username = in_mem.get("username", acc.get("username", ""))
                    user_tag = f"@{username}" if username else f"#{idx}"
                    autochat_state = in_mem.get("autochat", acc.get("autochat", True))
                    status_icon = "🟢" if autochat_state else "🔴"
                    btn_text = f"👑 {name} ({user_tag}) • {status_icon}"
                    keyboard_rows.append([InlineKeyboardButton(btn_text, callback_data=f"ub_panel:{account_id}")])

            # Admin accounts section
            if admin_accs:
                for idx, acc in enumerate(admin_accs, 1):
                    account_id = acc["account_id"]
                    in_mem = ub_accounts.get(account_id, {})
                    name = in_mem.get("name", acc.get("name", "Unknown"))
                    username = in_mem.get("username", acc.get("username", ""))
                    user_tag = f"@{username}" if username else f"#{idx}"
                    added_by_id = acc.get("added_by", "Unknown")
                    autochat_state = in_mem.get("autochat", acc.get("autochat", True))
                    status_icon = "🟢" if autochat_state else "🔴"
                    btn_text = f"👤 {name} ({user_tag}) [Admin: {added_by_id}] • {status_icon}"
                    keyboard_rows.append([InlineKeyboardButton(btn_text, callback_data=f"ub_panel:{account_id}")])

        keyboard_rows.append([
            InlineKeyboardButton("➕ Add Userbot", callback_data="ub_add"),
            InlineKeyboardButton("👑 Manage Admins", callback_data="ub_manage_admins"),
        ])
        keyboard_rows.append([
            InlineKeyboardButton("🔄 Refresh", callback_data="ub_main_refresh"),
        ])

        return text, InlineKeyboardMarkup(keyboard_rows)

    else:
        # Admin view: Only accounts added by this admin
        my_accounts = await get_accounts_by_user(viewer_id)

        if not my_accounts:
            text = (
                "👥 **USERBOT MANAGEMENT DASHBOARD**\n\n"
                "📦 **You have not connected any userbot account yet.**\n\n"
                "Click **[ ➕ Add Userbot ]** below to link your Telegram account."
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Userbot", callback_data="ub_add")],
                [InlineKeyboardButton("🔄 Refresh", callback_data="ub_main_refresh")],
            ])
            return text, markup

        text = (
            "👥 **YOUR USERBOT DASHBOARD**\n\n"
            f"📦 **Your Connected Accounts:** `{len(my_accounts)}`\n\n"
            "Select a userbot below to open its dedicated settings & controls:"
        )

        keyboard_rows = []
        for index, acc in enumerate(my_accounts, 1):
            account_id = acc["account_id"]
            in_mem = ub_accounts.get(account_id, {})

            name = in_mem.get("name", acc.get("name", "Unknown"))
            username = in_mem.get("username", acc.get("username", ""))
            user_tag = f"@{username}" if username else f"#{index}"

            autochat_state = in_mem.get("autochat", acc.get("autochat", True))
            status_icon = "🟢" if autochat_state else "🔴"

            btn_text = f"👤 {index}. {name} ({user_tag}) • {status_icon}"
            keyboard_rows.append([InlineKeyboardButton(btn_text, callback_data=f"ub_panel:{account_id}")])

        keyboard_rows.append([
            InlineKeyboardButton("➕ Add Userbot", callback_data="ub_add"),
            InlineKeyboardButton("🔄 Refresh", callback_data="ub_main_refresh"),
        ])

        return text, InlineKeyboardMarkup(keyboard_rows)


async def _build_userbot_panel(account_id: str, viewer_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build dedicated management panel for a single userbot."""
    can_manage, acc = await _can_manage_account(viewer_id, account_id)
    if not can_manage or not acc:
        return "❌ You are not authorized to view this userbot account.", InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Back to Accounts", callback_data="ub_back_main")]
        ])

    in_mem = ub_accounts.get(account_id, {})
    telegram_id = in_mem.get("telegram_id", acc.get("telegram_id", 0))
    username = in_mem.get("username", acc.get("username", ""))
    username_str = f"@{username}" if username else "—"
    name = in_mem.get("name", acc.get("name", "Unknown"))
    phone_masked = _mask_phone(in_mem.get("phone", acc.get("phone", "")))

    is_connected = account_id in ub_accounts and ub_accounts[account_id].get("status") == "CONNECTED"
    status_str = "🟢 Connected" if is_connected else "🔴 Disconnected"

    autochat_state = in_mem.get("autochat", acc.get("autochat", True))
    autochat_label = "🟢 ON" if autochat_state else "🔴 OFF"
    autochat_btn_text = "🔴 Turn AutoChat OFF" if autochat_state else "🟢 Turn AutoChat ON"

    delay = in_mem.get("reply_delay", acc.get("reply_delay", 0))
    speed_mode = in_mem.get("speed_mode", acc.get("speed_mode", "fast"))
    speed_label = _format_speed_label(speed_mode, delay)

    _gm = in_mem.get("group_mode") or acc.get("group_mode") or (
        "all" if config.USERBOT_GROUP_REPLY_ALL else "mention"
    )
    group_label = "All messages 💬" if _gm == "all" else "Only when tagged 👤"
    _chance = in_mem.get("reply_chance", acc.get("reply_chance"))
    chance_label = "Global" if _chance is None else f"{float(_chance):g}%"

    added_by = acc.get("added_by")
    owner_id = owner_id_sync()
    if added_by and owner_id and int(added_by) == owner_id:
        creator_str = "👑 Owner"
    elif added_by:
        creator_str = f"👤 Admin (`{added_by}`)"
    else:
        creator_str = "👑 Owner"

    panel_text = (
        f"👤 **Userbot Panel: {name}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **ID:** `{telegram_id}`\n"
        f"👤 **Username:** {username_str}\n"
        f"📱 **Phone:** `{phone_masked}`\n"
        f"🛡 **Added By:** {creator_str}\n"
        f"📊 **Connection:** {status_str}\n"
        f"🤖 **AutoChat:** {autochat_label}\n"
        f"⚡ **Reply Speed:** {speed_label}\n"
        f"💬 **Group Reply:** {group_label}\n"
        f"🎲 **Reply Chance:** {chance_label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(autochat_btn_text, callback_data=f"ub_toggle:{account_id}")],
        [
            InlineKeyboardButton("⚡ Speed Settings", callback_data=f"ub_speed:{account_id}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"ub_restart:{account_id}"),
        ],
        [
            InlineKeyboardButton("ℹ️ Info", callback_data=f"ub_info:{account_id}"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"ub_remove:{account_id}"),
        ],
        [InlineKeyboardButton("↩️ Back to Accounts List", callback_data="ub_back_main")],
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

    speed_text = (
        f"⚡ **Reply Behaviour: {name}** {username_str}\n\n"
        f"📊 **Current Speed:** `{current_speed_str}`\n"
        f"💬 **Group Reply:** `{group_label}`\n"
        f"🎲 **Reply Chance:** `{chance_label}`\n\n"
        "**Speed** — how fast it replies:\n"
        "• ⚡ **Fast**: Instant reply (0s delay)\n"
        "• ⏱ **Medium**: 5s delay (human pace)\n"
        "• 🐢 **Slow**: 10s delay (relaxed pace)\n"
        "• ⚙️ **Custom**: Enter custom delay in milliseconds\n"
        "**Group Reply** — reply to every message, or only when tagged.\n"
        "**Reply Chance** — how often it replies (100% = always).\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Fast (0s)", callback_data=f"ub_setspeed:{account_id}:fast"),
            InlineKeyboardButton("⏱ Medium (5s)", callback_data=f"ub_setspeed:{account_id}:medium"),
        ],
        [
            InlineKeyboardButton("🐢 Slow (10s)", callback_data=f"ub_setspeed:{account_id}:slow"),
            InlineKeyboardButton("⚙️ Custom Delay", callback_data=f"ub_setspeed:{account_id}:custom"),
        ],
        [
            InlineKeyboardButton("💬 Group Reply Mode", callback_data=f"ub_gmode:{account_id}"),
        ],
        [
            InlineKeyboardButton("🎲 Reply Chance", callback_data=f"ub_chance:{account_id}"),
        ],
        [InlineKeyboardButton(f"↩️ Back to {name}", callback_data=f"ub_panel:{account_id}")],
    ])

    return speed_text, markup


async def _build_admin_management_panel() -> tuple[str, InlineKeyboardMarkup]:
    """Build the Admin Management panel for the Bot Owner."""
    admins_list = await get_admins()

    text = (
        "👑 **BOT ADMIN MANAGEMENT**\n\n"
        "Admins added here can use `/useraccounts` to add and manage their own userbots.\n"
        "Admins will **NOT** see your accounts, but you will have full control over all accounts.\n\n"
        f"🛡 **Current Admins:** `{len(admins_list)}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    keyboard_rows = []

    if not admins_list:
        text += "ℹ️ *No admins added yet.*\nClick **[ ➕ Add Admin ]** to authorize a user."
    else:
        for idx, adm in enumerate(admins_list, 1):
            adm_id = adm["user_id"]
            adm_name = adm.get("name", "Admin")
            adm_user = adm.get("username", "")
            user_tag = f"@{adm_user}" if adm_user else f"ID: `{adm_id}`"
            text += f"{idx}. 👤 **{adm_name}** ({user_tag})\n"
            keyboard_rows.append([
                InlineKeyboardButton(f"👤 {adm_name} ({adm_id})", callback_data="ub_noop"),
                InlineKeyboardButton("🗑 Remove", callback_data=f"ub_deladmin_btn:{adm_id}"),
            ])

    keyboard_rows.append([
        InlineKeyboardButton("➕ Add Admin", callback_data="ub_add_admin_prompt"),
        InlineKeyboardButton("🔄 Refresh", callback_data="ub_manage_admins"),
    ])
    keyboard_rows.append([
        InlineKeyboardButton("↩️ Back to Accounts", callback_data="ub_back_main"),
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
    """Owner command to add an authorized admin via /addadmin <user_id/username/reply>."""
    if not is_owner(m.from_user.id):
        return await m.reply("❌ Only the Bot Owner can add admins.")

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
                [InlineKeyboardButton("❌ Cancel", callback_data="ub_cancel_admin_prompt")]
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
    """Owner command to remove an authorized admin via /deladmin <user_id/reply>."""
    if not is_owner(m.from_user.id):
        return await m.reply("❌ Only the Bot Owner can remove admins.")

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

    # Owner Admin Management Panel
    if data == "ub_manage_admins":
        if not is_owner(user_id):
            return await query.answer("❌ Only Bot Owner can manage admins.", show_alert=True)

        text, markup = await _build_admin_management_panel()
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        await query.answer()
        return

    # Owner: Add Admin prompt
    if data == "ub_add_admin_prompt":
        if not is_owner(user_id):
            return await query.answer("❌ Only Bot Owner can add admins.", show_alert=True)

        pending_auth[user_id] = {
            "step": "add_admin",
            "created_at": time.time(),
        }
        await query.message.edit_text(
            "👑 **Add New Admin**\n\n"
            "Please send the **Telegram User ID** or **@username** of the user you want to authorize as Bot Admin.\n\n"
            "Send /cancel to cancel.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="ub_cancel_admin_prompt")]
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
                [InlineKeyboardButton("❌ Cancel", callback_data="ub_cancel_auth")]
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
                "Please send the delay in **milliseconds** (e.g. `500`, `1500`, `7000`, `30000`).\n"
                "`1000` ms = 1 second.\n\n"
                "Send /cancel to cancel.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data=f"ub_speed:{account_id}")]
                ])
            )
            await query.answer()
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
        phone_masked = _mask_phone(in_mem.get("phone", acc.get("phone", "")))
        is_conn = account_id in ub_accounts and ub_accounts[account_id].get("status") == "CONNECTED"
        status_str = "🟢 Connected" if is_conn else "🔴 Disconnected"
        autochat_state = in_mem.get("autochat", acc.get("autochat", True))
        autochat_str = "ON" if autochat_state else "OFF"
        delay = in_mem.get("reply_delay", acc.get("reply_delay", 0))
        speed_mode = in_mem.get("speed_mode", acc.get("speed_mode", "fast"))
        speed_label = _format_speed_label(speed_mode, delay)

        added_by = acc.get("added_by", "Owner")
        owner_id = owner_id_sync()
        creator_str = "👑 Owner" if (owner_id and int(added_by) == owner_id) else f"👤 Admin (`{added_by}`)"

        info_text = (
            "👤 **Userbot Account Info**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 **Telegram ID:** `{telegram_id}`\n"
            f"👤 **Username:** @{username if username else '—'}\n"
            f"📛 **Name:** {name}\n"
            f"📱 **Phone:** `{phone_masked}`\n"
            f"🛡 **Added By:** {creator_str}\n"
            f"📊 **Connection:** {status_str}\n"
            f"🤖 **AutoChat:** {autochat_str}\n"
            f"⚡ **Speed:** {speed_label}\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        info_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"↩️ Back to {name}", callback_data=f"ub_panel:{account_id}")]
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
                InlineKeyboardButton("✅ Confirm Remove", callback_data=f"ub_rm_confirm:{account_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"ub_panel:{account_id}"),
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

    # Step: Owner Add Admin interactive input
    if step == "add_admin":
        if not is_owner(user_id):
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

        if not re.fullmatch(r"^\d+$", text):
            await m.reply("❌ Please enter a valid positive number in milliseconds (e.g. `500`, `2000`, `15000`):")
            m.stop_propagation()
            return

        delay_ms = int(text)
        if delay_ms < 0 or delay_ms > 300000:
            await m.reply("❌ Please enter a delay between 0 and 300000 milliseconds (300 seconds):")
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