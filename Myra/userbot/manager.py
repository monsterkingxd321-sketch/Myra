import asyncio
import time
from pyrogram import Client
from Myra import config, mongo, LOGGER
from Myra.database.scope import current_scope
from Myra.userbot.storage import (
    USERBOT_ACCOUNTS,
    USERBOT_SESSIONS,
    accounts as ub_accounts,
    get_served_accounts,
    get_all_served_accounts,
    get_account,
    add_account,
    update_account,
    remove_account,
    save_session,
    get_session,
    ACCOUNT_SETTINGS_VERSION,
    default_account_settings,
)


_LOGOUT_MARKERS = (
    "authkeyunregistered",
    "auth_key_unregistered",
    "sessionrevoked",
    "session_revoked",
    "sessionexpired",
    "session_expired",
    "userdeactivated",
    "user_deactivated",
    "unauthorized",
    "authkeyduplicated",
    "auth_key_duplicated",
)


def is_logout_error(exc: Exception) -> bool:
    """True when an exception indicates the userbot session was logged out or revoked."""
    if exc is None:
        return False
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return any(m in name or m in msg for m in _LOGOUT_MARKERS)


class AccountManager:
    """Manages multiple userbot accounts lifecycle and settings."""

    def __init__(self):
        self.cleanup_task = None

    async def startup(self):
        """
        Load every enabled userbot account from MongoDB on startup.

        Accounts of *all* scopes (main bot + every clone) are restored — each
        account keeps its own scope, which decides whose panels may manage it
        and which settings / groups it uses.
        """
        accounts_list = await get_all_served_accounts()
        LOGGER.info(f"Loading {len(accounts_list)} userbot account(s)...")

        for account_doc in accounts_list:
            account_id = account_doc.get("account_id")
            scope = account_doc.get("scope") or current_scope()
            try:
                await self._restore_account(account_doc)
            except Exception as e:
                LOGGER.error(f"⚠️ Failed to restore userbot account {account_id}: {e}")
                status_val = "LOGGED_OUT" if is_logout_error(e) else "ERROR"
                await update_account(account_id, {"status": status_val}, scope=scope)

        # Start background cleanup task for expired auth flows
        from Myra.userbot.auth import cleanup_expired_flows
        self.cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def _restore_account(self, account_doc: dict):
        """Restore and connect a single userbot account from persistent session."""
        account_id = account_doc["account_id"]
        scope = account_doc.get("scope") or current_scope()
        telegram_id = account_doc.get("telegram_id")
        username = account_doc.get("username", "")
        name = account_doc.get("name", "")
        phone = account_doc.get("phone", "")
        defaults = default_account_settings()
        if account_doc.get("settings_version") != ACCOUNT_SETTINGS_VERSION:
            # Accounts created by older builds had no behaviour defaults (fast /
            # global chance / mention-only). Upgrade them once so every userbot
            # starts the same way: AutoChat on, medium pace, every group message,
            # 100% reply chance. After this they keep whatever the admin sets.
            await update_account(account_id, defaults, scope=scope)
            account_doc = {**account_doc, **defaults}
            LOGGER.info(f"⚙️ Applied default userbot settings to {account_id}")

        autochat = account_doc.get("autochat", defaults["autochat"])
        auto_enabled = account_doc.get("auto_enabled", autochat)
        reply_delay = account_doc.get("reply_delay", defaults["reply_delay"])
        speed_mode = account_doc.get("speed_mode") or defaults["speed_mode"]
        group_mode = account_doc.get("group_mode") or defaults["group_mode"]
        reply_chance = account_doc.get("reply_chance")
        reply_scope = account_doc.get("reply_scope") or defaults.get("reply_scope", "both")
        action_mode = account_doc.get("action_mode") or defaults.get("action_mode", "chat")

        added_by = account_doc.get("added_by")
        if not added_by and config.OWNER_ID:
            try:
                added_by = int(config.OWNER_ID)
            except Exception:
                added_by = None

        session_string = await get_session(account_id)
        if not session_string:
            LOGGER.warning(f"⚠️ No session found for userbot account {account_id}")
            await update_account(account_id, {"status": "AUTH_REQUIRED"}, scope=scope)
            return

        client = Client(
            name=account_id,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=session_string,
            in_memory=True,
        )

        try:
            await client.start()
            me = await client.get_me()

            raw_phone = getattr(me, "phone_number", None)
            if raw_phone:
                phone = f"+{str(raw_phone).lstrip('+')}"
            elif phone and not phone.startswith("+"):
                phone = f"+{phone}"

            ub_accounts[account_id] = {
                "client": client,
                "telegram_id": me.id,
                "username": me.username or username,
                "name": name or f"{me.first_name or ''} {me.last_name or ''}".strip(),
                "phone": phone,
                "added_by": added_by,
                "scope": scope,
                "autochat": autochat,
                "auto_enabled": auto_enabled,
                "reply_delay": reply_delay,
                "speed_mode": speed_mode,
                "group_mode": group_mode,
                "reply_chance": reply_chance,
                "reply_scope": reply_scope,
                "action_mode": action_mode,
                "status": "CONNECTED",
            }

            # Update MongoDB status and phone
            up_data = {
                "status": "CONNECTED",
                "username": me.username or username,
                "name": ub_accounts[account_id]["name"],
                "added_by": added_by,
            }
            if phone:
                up_data["phone"] = phone
            await update_account(account_id, up_data, scope=scope)

            # Register message handlers
            from Myra.userbot.handlers import register_userbot_handlers
            await register_userbot_handlers(client, account_id)

            # Warm up in-memory peer cache so updates & reactions work for all groups
            async def _warmup_dialogs(cl: Client, aid: str):
                try:
                    count = 0
                    async for _ in cl.get_dialogs(limit=50):
                        count += 1
                    LOGGER.debug(f"🔄 Userbot ({aid}) warmed {count} dialog(s)")
                except Exception as exc:
                    LOGGER.debug(f"Dialog warmup skipped for {aid}: {exc}")

            asyncio.create_task(_warmup_dialogs(client, account_id))

            LOGGER.info(f"✅ Userbot {ub_accounts[account_id]['name']} ({account_id}) connected! (action_mode={action_mode}, group_mode={group_mode})")

        except Exception as e:
            LOGGER.error(f"❌ Error starting userbot {account_id}: {e}")
            status_val = "LOGGED_OUT" if is_logout_error(e) else "ERROR"
            await update_account(account_id, {"status": status_val}, scope=scope)

    async def mark_logged_out(self, account_id: str, reason: str = ""):
        """Mark a userbot as logged out in memory and MongoDB."""
        scope = (ub_accounts.get(account_id) or {}).get("scope") or current_scope()
        if account_id in ub_accounts:
            ub_accounts[account_id]["status"] = "LOGGED_OUT"
        try:
            await update_account(account_id, {"status": "LOGGED_OUT"}, scope=scope)
        except Exception:
            pass
        if reason:
            LOGGER.warning(f"🚪 Userbot {account_id} marked LOGGED_OUT: {reason}")

    async def check_account_alive(self, account_id: str) -> str:
        """Verify live session status when viewing an account; returns status string."""
        in_mem = ub_accounts.get(account_id)
        if not in_mem:
            doc = await get_account(account_id)
            return (doc or {}).get("status") or "DISCONNECTED"
        status = in_mem.get("status") or "DISCONNECTED"
        client = in_mem.get("client")
        if status == "CONNECTED" and client is not None:
            try:
                await client.get_me()
            except Exception as exc:
                if is_logout_error(exc):
                    await self.mark_logged_out(account_id, str(exc))
                    return "LOGGED_OUT"
        return status

    async def toggle_autochat(self, account_id: str) -> bool:
        """Toggle AutoChat state for an account in-memory and in MongoDB."""
        account_doc = await get_account(account_id)
        current_state = config.USERBOT_AUTOCHAT_DEFAULT

        if account_id in ub_accounts:
            current_state = ub_accounts[account_id].get("autochat", True)
        elif account_doc:
            current_state = account_doc.get("autochat", True)

        new_state = not current_state

        if account_id in ub_accounts:
            ub_accounts[account_id]["autochat"] = new_state
            ub_accounts[account_id]["auto_enabled"] = new_state

        await update_account(account_id, {"autochat": new_state, "auto_enabled": new_state})
        if new_state:
            try:
                from Myra.database import autochat as ac
                from Myra.services.autochat import autochat_service
                scope = (ub_accounts.get(account_id) or account_doc or {}).get("scope") or current_scope()
                await ac.set_ub_enabled(True, scope=scope)
                autochat_service.reset_timers()
            except Exception:
                pass
        return new_state

    async def set_speed(self, account_id: str, delay: int, mode: str):
        """Set reply speed and delay for a userbot."""
        if account_id in ub_accounts:
            ub_accounts[account_id]["reply_delay"] = delay
            ub_accounts[account_id]["speed_mode"] = mode
        await update_account(account_id, {"reply_delay": delay, "speed_mode": mode})

    async def set_group_mode(self, account_id: str, mode: str) -> str:
        """'all' = reply to normal group messages too, 'mention' = only if tagged."""
        mode = "all" if mode == "all" else "mention"
        if account_id in ub_accounts:
            ub_accounts[account_id]["group_mode"] = mode
        await update_account(account_id, {"group_mode": mode})
        return mode

    async def set_reply_scope(self, account_id: str, reply_scope: str) -> str:
        """'both', 'groups', or 'private'."""
        if reply_scope not in ("both", "groups", "private"):
            reply_scope = "both"
        if account_id in ub_accounts:
            ub_accounts[account_id]["reply_scope"] = reply_scope
        await update_account(account_id, {"reply_scope": reply_scope})
        return reply_scope

    async def set_action_mode(self, account_id: str, action_mode: str) -> str:
        """'chat' (reply only), 'react' (emoji reaction only), or 'both' (chat + react)."""
        if action_mode not in ("chat", "react", "both"):
            action_mode = "chat"
        if account_id in ub_accounts:
            ub_accounts[account_id]["action_mode"] = action_mode
        await update_account(account_id, {"action_mode": action_mode})
        return action_mode

    async def set_reply_chance(self, account_id: str, chance):
        """Reply probability 0-100, or None to follow the global value."""
        value = None if chance is None else max(0.0, min(100.0, float(chance)))
        if account_id in ub_accounts:
            ub_accounts[account_id]["reply_chance"] = value
        await update_account(account_id, {"reply_chance": value})
        return value

    async def set_reply_scope(self, account_id: str, mode: str) -> str:
        """'both' = groups and DMs, 'groups' = group chats only, 'private' = DM only."""
        mode = mode if mode in ("both", "groups", "private") else "both"
        if account_id in ub_accounts:
            ub_accounts[account_id]["reply_scope"] = mode
        await update_account(account_id, {"reply_scope": mode})
        return mode

    async def restart_account(self, account_id: str) -> bool:
        """Restart a userbot account using its saved session."""
        account_doc = await get_account(account_id)
        if not account_doc:
            return False

        # Stop current client if active
        if account_id in ub_accounts:
            client = ub_accounts[account_id].get("client")
            if client:
                try:
                    await client.stop()
                except Exception:
                    pass
            ub_accounts.pop(account_id, None)

        # Restore from database
        try:
            await self._restore_account(account_doc)
            return account_id in ub_accounts and ub_accounts[account_id].get("status") == "CONNECTED"
        except Exception as e:
            LOGGER.error(f"⚠️ Error restarting account {account_id}: {e}")
            return False

    async def remove_account(self, account_id: str) -> bool:
        """Remove a userbot account completely."""
        if account_id in ub_accounts:
            client = ub_accounts[account_id].get("client")
            if client:
                try:
                    await client.stop()
                except Exception:
                    pass
            ub_accounts.pop(account_id, None)

        await remove_account(account_id)
        return True

    async def _periodic_cleanup(self):
        """Periodically clean up expired login flows."""
        from Myra.userbot.auth import cleanup_expired_flows
        while True:
            try:
                await asyncio.sleep(60)
                await cleanup_expired_flows()
            except Exception:
                pass

    async def stop(self):
        """Stop all userbot clients on shutdown."""
        if self.cleanup_task:
            self.cleanup_task.cancel()

        for account_id, data in list(ub_accounts.items()):
            client = data.get("client")
            if client:
                try:
                    await client.stop()
                except Exception:
                    pass
        ub_accounts.clear()


userbot_manager = AccountManager()