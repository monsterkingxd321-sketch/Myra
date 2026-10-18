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
)


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
                await update_account(account_id, {"status": "ERROR"}, scope=scope)

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
        autochat = account_doc.get("autochat", config.USERBOT_AUTOCHAT_DEFAULT)
        reply_delay = account_doc.get("reply_delay")
        speed_mode = account_doc.get("speed_mode", "fast")
        group_mode = account_doc.get("group_mode") or (
            "all" if config.USERBOT_GROUP_REPLY_ALL else "mention"
        )
        reply_chance = account_doc.get("reply_chance")

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

            ub_accounts[account_id] = {
                "client": client,
                "telegram_id": me.id,
                "username": me.username or username,
                "name": name or f"{me.first_name or ''} {me.last_name or ''}".strip(),
                "phone": phone,
                "added_by": added_by,
                "scope": scope,
                "autochat": autochat,
                "reply_delay": reply_delay,
                "speed_mode": speed_mode,
                "group_mode": group_mode,
                "reply_chance": reply_chance,
                "status": "CONNECTED",
            }

            # Update MongoDB status
            await update_account(account_id, {
                "status": "CONNECTED",
                "username": me.username or username,
                "name": ub_accounts[account_id]["name"],
                "added_by": added_by,
            }, scope=scope)

            # Register message handlers
            from Myra.userbot.handlers import register_userbot_handlers
            await register_userbot_handlers(client, account_id)

            LOGGER.info(f"✅ Userbot {ub_accounts[account_id]['name']} ({account_id}) connected successfully!")

        except Exception as e:
            LOGGER.error(f"❌ Error starting userbot {account_id}: {e}")
            await update_account(account_id, {"status": "ERROR"}, scope=scope)

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

        await update_account(account_id, {"autochat": new_state})
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

    async def set_reply_chance(self, account_id: str, chance):
        """Reply probability 0-100, or None to follow the global value."""
        value = None if chance is None else max(0.0, min(100.0, float(chance)))
        if account_id in ub_accounts:
            ub_accounts[account_id]["reply_chance"] = value
        await update_account(account_id, {"reply_chance": value})
        return value

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