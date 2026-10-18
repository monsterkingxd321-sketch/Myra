import time

from Myra import bot_mongo, config
from Myra.database.scope import current_scope, scope_match

USERBOT_ACCOUNTS = bot_mongo["UserbotDB"]["accounts"] if bot_mongo is not None else None
USERBOT_SESSIONS = bot_mongo["SessionDB"]["sessions"] if bot_mongo is not None else None

# In-memory account state
# account_id -> {
#     "client": Client,
#     "telegram_id": int,
#     "username": str,
#     "name": str,
#     "phone": str,
#     "added_by": int,
#     "scope": str,
#     "autochat": bool,
#     "reply_delay": int,
#     "speed_mode": str,
#     "status": str,
# }
accounts = {}


def _get_owner_id() -> int | None:
    if config.OWNER_ID:
        try:
            return int(config.OWNER_ID)
        except Exception:  # noqa: BLE001
            return None
    return None


async def get_served_accounts(scope: str = None) -> list:
    """Userbot accounts of the bot in this scope."""
    if USERBOT_ACCOUNTS is None:
        return []
    accounts_list = []
    try:
        async for account in USERBOT_ACCOUNTS.find({"enabled": True, **scope_match(scope)}):
            if "added_by" not in account or not account["added_by"]:
                account["added_by"] = _get_owner_id()
            accounts_list.append(account)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error fetching served accounts: {e}")
    return accounts_list


async def get_all_served_accounts() -> list:
    """Every enabled userbot account, across all scopes (used at startup)."""
    if USERBOT_ACCOUNTS is None:
        return []
    accounts_list = []
    try:
        async for account in USERBOT_ACCOUNTS.find({"enabled": True}):
            if "added_by" not in account or not account["added_by"]:
                account["added_by"] = _get_owner_id()
            accounts_list.append(account)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error fetching served accounts: {e}")
    return accounts_list


async def get_accounts_by_user(user_id: int, scope: str = None) -> list:
    """
    Userbot accounts visible to a specific user, inside one scope.

    The scope's owner sees every account of that scope; an admin only the ones
    they added themselves.
    """
    from Myra.database.admins import is_owner

    scope = scope or current_scope()
    if is_owner(user_id, scope):
        return await get_served_accounts(scope)

    if USERBOT_ACCOUNTS is None:
        return []

    accounts_list = []
    try:
        async for account in USERBOT_ACCOUNTS.find({
            "enabled": True,
            "added_by": int(user_id),
            **scope_match(scope),
        }):
            accounts_list.append(account)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error fetching accounts for user {user_id}: {e}")
    return accounts_list


async def get_account(account_id: str, scope: str = None) -> dict:
    """Get a specific account by account_id (inside one scope)."""
    if USERBOT_ACCOUNTS is None:
        return None
    try:
        acc = await USERBOT_ACCOUNTS.find_one(
            {"account_id": account_id, "enabled": True, **scope_match(scope)}
        )
        if acc and ("added_by" not in acc or not acc["added_by"]):
            acc["added_by"] = _get_owner_id()
        return acc
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error getting account {account_id}: {e}")
        return None


async def add_account(account_data: dict, scope: str = None):
    """
    Add or upsert a userbot account in MongoDB.

    An account belongs to exactly one bot: if it is already registered to a
    different scope the write is refused (returns ``None``) so two bots can
    never run the same Telegram account at the same time.
    """
    if USERBOT_ACCOUNTS is None:
        return None
    scope = scope or account_data.get("scope") or current_scope()
    account_id = account_data.get("account_id")
    try:
        try:
            existing = await USERBOT_ACCOUNTS.find_one({"account_id": account_id})
        except Exception:  # noqa: BLE001
            existing = None

        if existing:
            owner_scope = existing.get("scope")
            if owner_scope and owner_scope != scope:
                print(f"⚠️ Account {account_id} already belongs to scope {owner_scope}")
                return None

        account_data = dict(account_data)
        account_data["scope"] = scope
        account_data["updated_at"] = time.time()
        if "added_by" not in account_data or not account_data["added_by"]:
            account_data["added_by"] = _get_owner_id()
        else:
            account_data["added_by"] = int(account_data["added_by"])

        return await USERBOT_ACCOUNTS.update_one(
            {"account_id": account_id},
            {"$set": account_data},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error adding account {account_data.get('account_id')}: {e}")
        return None


async def update_account(account_id: str, update_data: dict, scope: str = None):
    """Update an account's data in MongoDB (only inside its own scope)."""
    if USERBOT_ACCOUNTS is None:
        return None
    try:
        update_data = dict(update_data)
        update_data["updated_at"] = time.time()
        return await USERBOT_ACCOUNTS.update_one(
            {"account_id": account_id, **scope_match(scope)},
            {"$set": update_data},
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error updating account {account_id}: {e}")
        return None


async def remove_account(account_id: str, scope: str = None):
    """Remove an account completely from MongoDB and delete its session."""
    if USERBOT_ACCOUNTS is None:
        return None
    try:
        result = await USERBOT_ACCOUNTS.delete_one(
            {"account_id": account_id, **scope_match(scope)}
        )
        if result.deleted_count == 0:
            return False
        if USERBOT_SESSIONS is not None:
            await USERBOT_SESSIONS.delete_one({"account_id": account_id})
        return True
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error removing account {account_id}: {e}")
        return False


async def account_scope(account_id: str):
    """Scope that owns an account (None when unknown)."""
    if USERBOT_ACCOUNTS is None:
        return None
    try:
        doc = await USERBOT_ACCOUNTS.find_one({"account_id": account_id})
        return (doc or {}).get("scope")
    except Exception:  # noqa: BLE001
        return None


async def save_session(account_id: str, session_string: str):
    """Save or upsert a session string in MongoDB."""
    if USERBOT_SESSIONS is None:
        return None
    try:
        return await USERBOT_SESSIONS.update_one(
            {"account_id": account_id},
            {"$set": {"account_id": account_id, "session_string": session_string, "updated_at": time.time()}},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error saving session for {account_id}: {e}")
        return None


async def get_session(account_id: str) -> str:
    """Get a session string from MongoDB."""
    if USERBOT_SESSIONS is None:
        return None
    try:
        session = await USERBOT_SESSIONS.find_one({"account_id": account_id})
        if session:
            return session.get("session_string")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error fetching session for {account_id}: {e}")
    return None


async def delete_session(account_id: str):
    """Delete a session string from MongoDB."""
    if USERBOT_SESSIONS is None:
        return None
    try:
        return await USERBOT_SESSIONS.delete_one({"account_id": account_id})
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error deleting session for {account_id}: {e}")
        return None


__all__ = [
    "USERBOT_ACCOUNTS",
    "USERBOT_SESSIONS",
    "accounts",
    "get_served_accounts",
    "get_all_served_accounts",
    "get_accounts_by_user",
    "get_account",
    "add_account",
    "update_account",
    "remove_account",
    "account_scope",
    "save_session",
    "get_session",
    "delete_session",
]
