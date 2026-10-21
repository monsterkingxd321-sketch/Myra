"""
Per-group configuration for the Group Request Controller.

Telegram fires a ``ChatJoinRequest`` update whenever somebody taps "Ask to join"
on a group/channel that has request-to-join switched on. The controller decides
what happens to that request.

Scope-aware: ``scope`` is the bot id (``c8619725063``) or ``"main"`` — so every
cloned bot manages its OWN groups and never sees the main bot's.

Documents live in ``UserbotDB.joinreq_chats``:

    {
        "scope": "main",
        "chat_id": -100123,
        "title": "My Group",
        "username": "mygroup",
        "enabled": True,
        "required": [{"chat_id": -100456, "username": "news", "label": "News"}],
        "counters": {"pending": 12, "verified": 9, "approved": 9},
        "updated_at": 1730000000.0,
    }
"""

import time
import uuid

from Myra import LOGGER, bot_mongo
from Myra.database.scope import scope_field, scope_match

_CHATS = bot_mongo["UserbotDB"]["joinreq_chats"] if bot_mongo is not None else None
_SESSIONS = bot_mongo["UserbotDB"]["joinreq_sessions"] if bot_mongo is not None else None

INDEX_READY = False
INDEX_ERROR = None

MAX_REQUIRED = 10

#: A finished/expired verification session is kept this long (for "expired"
#: messages + debugging), then MongoDB's TTL index removes it.
SESSION_KEEP_SECONDS = 7 * 24 * 3600

#: Default verification window, in minutes (same as the reference bot).
DEFAULT_TIMEOUT_MIN = 30

#: Presets cycled by the admin panel.
TIMEOUT_CHOICES = (5, 15, 30, 60, 120, 1440)


async def _ensure_index():
    global INDEX_READY, INDEX_ERROR
    if _CHATS is None or INDEX_READY:
        return
    try:
        await _CHATS.create_index([("scope", 1), ("chat_id", 1)], unique=True)
        if _SESSIONS is not None:
            await _SESSIONS.create_index("session_id", unique=True)
            await _SESSIONS.create_index(
                "expires_at", expireAfterSeconds=SESSION_KEEP_SECONDS
            )
            await _SESSIONS.create_index([("user_id", 1), ("chat_id", 1)])
        INDEX_READY = True
        LOGGER.info("joinreq_chats index ready")
    except Exception as exc:  # noqa: BLE001
        INDEX_ERROR = exc
        LOGGER.warning(f"joinreq_chats index failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════
#  Reads
# ══════════════════════════════════════════════════════════════════════════
async def get_chat(chat_id: int, scope: str = None):
    if _CHATS is None:
        return None
    try:
        return await _CHATS.find_one({"chat_id": int(chat_id), **scope_match(scope)})
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"joinreq get_chat failed: {exc}")
        return None


async def list_managed(scope: str = None) -> list:
    if _CHATS is None:
        return []
    try:
        await _ensure_index()
        return await _CHATS.find(
            {"enabled": True, **scope_match(scope)},
            projection={"_id": False},
        ).sort("counters.pending", -1).to_list(500)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"joinreq list_managed failed: {exc}")
        return []


async def all_chats(scope: str = None) -> list:
    if _CHATS is None:
        return []
    try:
        return await _CHATS.find(
            scope_match(scope), projection={"_id": False}
        ).to_list(500)
    except Exception:  # noqa: BLE001
        return []


# ══════════════════════════════════════════════════════════════════════════
#  Writes
# ══════════════════════════════════════════════════════════════════════════
async def enable(chat_id: int, title: str = None, username: str = None, scope: str = None):
    """Create (or reactivate) the controller for one group."""
    if _CHATS is None:
        return
    try:
        await _ensure_index()
        await _CHATS.update_one(
            {"chat_id": int(chat_id), **scope_match(scope)},
            {
                "$set": {
                    "enabled": True,
                    "title": (title or "")[:120],
                    "username": (username or "")[1:] if str(username or "").startswith("@") else (username or ""),
                    "updated_at": time.time(),
                    **scope_field(scope),
                },
                "$setOnInsert": {"required": [], "counters": {}},
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq enable failed: {exc}")


async def disable(chat_id: int, scope: str = None):
    if _CHATS is None:
        return
    try:
        await _CHATS.update_one(
            {"chat_id": int(chat_id), **scope_match(scope)},
            {"$set": {"enabled": False, "updated_at": time.time()}},
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"joinreq disable failed: {exc}")


async def remove(chat_id: int, scope: str = None):
    if _CHATS is None:
        return
    try:
        await _CHATS.delete_one({"chat_id": int(chat_id), **scope_match(scope)})
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"joinreq remove failed: {exc}")


async def set_required(chat_id: int, items: list, scope: str = None):
    if _CHATS is None:
        return
    try:
        await _CHATS.update_one(
            {"chat_id": int(chat_id), **scope_match(scope)},
            {"$set": {"required": (items or [])[:MAX_REQUIRED], "updated_at": time.time()}},
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"joinreq set_required failed: {exc}")


async def set_option(chat_id: int, key: str, value, scope: str = None):
    """Per-chat override (``auto_approve`` / ``timeout_minutes`` / ...)."""
    if _CHATS is None:
        return
    try:
        await _CHATS.update_one(
            {"chat_id": int(chat_id), **scope_match(scope)},
            {"$set": {key: value, "updated_at": time.time()}},
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"joinreq set_option({key}) failed: {exc}")


async def bump(chat_id: int, key: str, n: int = 1, scope: str = None):
    if _CHATS is None:
        return
    try:
        await _CHATS.update_one(
            {"chat_id": int(chat_id), **scope_match(scope)},
            {"$inc": {f"counters.{key}": int(n)}},
        )
    except Exception:  # noqa: BLE001
        pass


async def reset_stats(chat_id: int = None, scope: str = None) -> int:
    if _CHATS is None:
        return 0
    try:
        query = {**scope_match(scope)}
        if chat_id is not None:
            query["chat_id"] = int(chat_id)
        res = await _CHATS.update_many(query, {"$set": {"counters": {}}})
        return res.modified_count
    except Exception:  # noqa: BLE001
        return 0


# ══════════════════════════════════════════════════════════════════════════
#  Verification sessions
# ══════════════════════════════════════════════════════════════════════════
async def create_session(
    user_id: int,
    chat_id: int,
    *,
    minutes: int = DEFAULT_TIMEOUT_MIN,
    scope: str = None,
    is_test: bool = False,
) -> dict:
    """Open a verification window for one applicant.

    Any earlier *pending* window for the same (user, chat) is closed first, so
    an old card can never approve a newer request.
    """
    session = {
        "session_id": uuid.uuid4().hex,
        "user_id": int(user_id),
        "chat_id": int(chat_id),
        "status": "pending",
        "attempts": 0,
        "is_test": bool(is_test),
        "created_at": time.time(),
        "expires_at": time.time() + max(1, int(minutes)) * 60,
        **scope_field(scope),
    }
    if _SESSIONS is None:
        return session
    try:
        await _ensure_index()
        if not is_test:
            await _SESSIONS.update_many(
                {
                    "user_id": int(user_id),
                    "chat_id": int(chat_id),
                    "status": "pending",
                    "is_test": {"$ne": True},
                },
                {"$set": {"status": "expired"}},
            )
        await _SESSIONS.insert_one(dict(session))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq create_session failed: {exc}")
    return session


async def get_session(session_id: str, scope: str = None):
    if _SESSIONS is None or not session_id:
        return None
    try:
        return await _SESSIONS.find_one(
            {"session_id": str(session_id), **scope_match(scope)}
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"joinreq get_session failed: {exc}")
        return None


async def get_pending_session(user_id: int, chat_id: int, scope: str = None):
    """Newest still-valid pending window for this applicant (legacy buttons)."""
    if _SESSIONS is None:
        return None
    try:
        return await _SESSIONS.find_one(
            {
                "user_id": int(user_id),
                "chat_id": int(chat_id),
                "status": "pending",
                "expires_at": {"$gt": time.time()},
                **scope_match(scope),
            },
            sort=[("created_at", -1)],
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"joinreq get_pending_session failed: {exc}")
        return None


async def bump_attempt(session_id: str) -> int:
    if _SESSIONS is None:
        return 0
    try:
        doc = await _SESSIONS.find_one_and_update(
            {"session_id": str(session_id)},
            {"$inc": {"attempts": 1}},
            return_document=True,
        )
        return int((doc or {}).get("attempts") or 0)
    except Exception:  # noqa: BLE001
        return 0


async def set_session_status(session_id: str, status: str) -> bool:
    if _SESSIONS is None:
        return False
    patch = {"status": status}
    if status in ("verified", "approved"):
        patch[f"{status}_at"] = time.time()
    try:
        res = await _SESSIONS.update_one({"session_id": str(session_id)}, {"$set": patch})
        return bool(res.modified_count)
    except Exception:  # noqa: BLE001
        return False


async def purge_sessions(chat_id: int = None, scope: str = None) -> int:
    """Drop sessions (used when a group is removed)."""
    if _SESSIONS is None:
        return 0
    try:
        query = {**scope_match(scope)}
        if chat_id is not None:
            query["chat_id"] = int(chat_id)
        res = await _SESSIONS.delete_many(query)
        return res.deleted_count
    except Exception:  # noqa: BLE001
        return 0
