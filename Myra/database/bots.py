"""
Cloned bots registry.

``BotDB.clones``

    {
        "scope":      "c123456789",   # unique key, "main" is never stored here
        "token":      "123456:ABC…",
        "bot_id":     123456789,
        "owner_id":   <the user who ran /clone>,
        "username":   "my_clone_bot",
        "name":       "My Clone Bot",
        "enabled":    True,
        "created_at": 1699999999.0,
        "last_start": 1699999999.0,
    }

``BOT_INFO`` caches the handful of values the panels need (name, username,
mention, owner) so that resolving them never costs a database round trip.
"""

import time

from Myra import config, LOGGER, bot_mongo
from Myra.database.scope import MAIN_SCOPE, clone_scope, current_scope

BOTS = bot_mongo["BotDB"]["clones"] if bot_mongo is not None else None

TOKEN_RE = r"^\d{6,}:[A-Za-z0-9_\-]{30,}$"

# scope -> {"bot_id", "username", "name", "mention", "owner_id"}
BOT_INFO: dict = {}


def main_info() -> dict:
    return BOT_INFO.get(MAIN_SCOPE, {
        "scope": MAIN_SCOPE,
        "bot_id": None,
        "username": None,
        "name": None,
        "mention": "",
        "owner_id": int(config.OWNER_ID) if config.OWNER_ID else None,
    })


def set_bot_info(scope: str, **fields) -> dict:
    info = BOT_INFO.setdefault(scope or MAIN_SCOPE, {"scope": scope or MAIN_SCOPE})
    info.update({k: v for k, v in fields.items() if v is not None})
    return info


def bot_info(scope: str = None) -> dict:
    """Cached info of a bot — the one serving the update by default."""
    scope = scope or current_scope() or MAIN_SCOPE
    if scope == MAIN_SCOPE:
        info = BOT_INFO.get(MAIN_SCOPE)
        if info:
            return info
        return main_info()
    return BOT_INFO.get(scope, {"scope": scope})


def owner_id_sync(scope: str = None):
    """Owner id of a scope without touching the database (None when unknown)."""
    scope = scope or current_scope() or MAIN_SCOPE
    if scope == MAIN_SCOPE:
        return int(config.OWNER_ID) if config.OWNER_ID else None
    info = BOT_INFO.get(scope) or {}
    owner = info.get("owner_id")
    if owner is not None:
        return owner
    return None


async def owner_id(scope: str = None):
    """Owner id of a scope, reading the database when the cache is cold."""
    scope = scope or current_scope() or MAIN_SCOPE
    value = owner_id_sync(scope)
    if value is not None:
        return value
    doc = await get_bot(scope)
    if doc and doc.get("owner_id"):
        set_bot_info(scope, owner_id=int(doc["owner_id"]))
        return int(doc["owner_id"])
    return None


# ── CRUD ───────────────────────────────────────────────────────────────────
async def get_bot(scope: str) -> dict | None:
    if BOTS is None:
        return None
    try:
        return await BOTS.find_one({"scope": scope})
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"get_bot({scope}) failed: {exc}")
        return None


async def list_bots(enabled_only: bool = False) -> list:
    if BOTS is None:
        return []
    query = {"enabled": True} if enabled_only else {}
    try:
        return [doc async for doc in BOTS.find(query)]
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"list_bots failed: {exc}")
        return []


async def find_bot_by_id(bot_id: int) -> dict | None:
    if BOTS is None:
        return None
    try:
        return await BOTS.find_one({"bot_id": int(bot_id)})
    except Exception:  # noqa: BLE001
        return None


async def find_bot_by_owner(owner_id: int) -> list:
    if BOTS is None:
        return []
    try:
        return [doc async for doc in BOTS.find({"owner_id": int(owner_id)})]
    except Exception:  # noqa: BLE001
        return []


async def register_bot(bot_id: int, token: str, owner_id: int, username: str = "", name: str = "") -> dict:
    """Create or refresh a clone entry and warm the caches."""
    scope = clone_scope(bot_id)
    doc = {
        "scope": scope,
        "bot_id": int(bot_id),
        "token": token,
        "username": username or "",
        "name": name or username or str(bot_id),
        "enabled": True,
        "last_start": time.time(),
    }
    if owner_id is not None:
        doc["owner_id"] = int(owner_id)
    if BOTS is not None:
        try:
            await BOTS.update_one(
                {"scope": scope},
                {"$set": doc, "$setOnInsert": {"created_at": time.time()}},
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"register_bot({scope}) failed: {exc}")
    set_bot_info(scope, bot_id=int(bot_id), username=username or "",
                 name=doc["name"], owner_id=int(owner_id) if owner_id is not None else None)
    return doc


async def update_bot(scope: str, data: dict) -> bool:
    if BOTS is None:
        return False
    try:
        await BOTS.update_one({"scope": scope}, {"$set": data})
        set_bot_info(scope, **data)
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"update_bot({scope}) failed: {exc}")
        return False


async def delete_bot(scope: str) -> bool:
    if BOTS is None:
        return False
    try:
        result = await BOTS.delete_one({"scope": scope})
        BOT_INFO.pop(scope, None)
        return result.deleted_count > 0
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"delete_bot({scope}) failed: {exc}")
        return False


__all__ = [
    "BOTS",
    "BOT_INFO",
    "TOKEN_RE",
    "set_bot_info",
    "bot_info",
    "main_info",
    "owner_id_sync",
    "owner_id",
    "get_bot",
    "list_bots",
    "find_bot_by_id",
    "find_bot_by_owner",
    "register_bot",
    "update_bot",
    "delete_bot",
]
