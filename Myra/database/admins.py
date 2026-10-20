import time

from Myra import bot_mongo, config
from Myra.database.scope import (
    MAIN_SCOPE,
    scope_field,
    scope_match,
)

adminsdb = bot_mongo["UserbotDB"]["bot_admins"] if bot_mongo is not None else None

# The admin surface is ONE shared thing: no matter which bot (the primary bot
# or any clone) receives the command, the same owner and the same admin list
# apply. See Myra/database/scope.py.
ADMIN_SCOPE = MAIN_SCOPE

# ``is_admin`` runs on every panel button press. The owner never reaches the
# database, but added admins used to pay a full round trip per tap, so the
# non-owner answer is cached briefly and invalidated by add/remove.
_ADMIN_CACHE: dict = {}
_ADMIN_TTL = 60.0


def _forget_admin(user_id) -> None:
    try:
        _ADMIN_CACHE.pop(int(user_id), None)
    except Exception:  # noqa: BLE001
        pass


def is_owner(user_id: int, scope: str = None) -> bool:
    """
    Is this user the **main** bot owner?

    Clones never grant ownership: ``/admin`` is reserved for the primary owner
    (``config.OWNER_ID``) and the admins they added, in every bot. A user who
    cloned the bot does **not** get the panel from that.
    """
    if not config.OWNER_ID:
        return True
    try:
        return int(user_id) == int(config.OWNER_ID)
    except Exception:  # noqa: BLE001
        return False


async def is_admin(user_id: int, scope: str = None) -> bool:
    """
    Check if the user may use the admin surface (panel + admin commands).

    Global: the main owner or one of the admins the main owner added — the same
    answer everywhere, because there is only one shared admin list.
    """
    if is_owner(user_id):
        return True
    if adminsdb is None:
        return False
    try:
        uid = int(user_id)
    except Exception:  # noqa: BLE001
        return False

    now = time.time()
    cached = _ADMIN_CACHE.get(uid)
    if cached and now - cached[0] < _ADMIN_TTL:
        return cached[1]

    try:
        doc = await adminsdb.find_one(
            {"user_id": uid, **scope_match(ADMIN_SCOPE)}
        )
        value = doc is not None
        _ADMIN_CACHE[uid] = (now, value)
        return value
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error checking admin status for {user_id}: {e}")
        return False


async def add_admin(
    user_id: int,
    added_by: int = None,
    name: str = "",
    username: str = "",
    scope: str = None,
) -> bool:
    """Add a new admin to the one shared admin list."""
    if adminsdb is None:
        return False
    try:
        fields = {
            "user_id": int(user_id),
            "added_by": int(added_by) if added_by else config.OWNER_ID,
            "name": name or "",
            "username": username or "",
            "updated_at": time.time(),
        }
        result = await adminsdb.update_one(
            {"user_id": int(user_id), **scope_match(ADMIN_SCOPE)},
            {"$set": {**fields, **scope_field(ADMIN_SCOPE)}},
            upsert=False,
        )
        if result.matched_count == 0:
            await adminsdb.insert_one(
                {**fields, **scope_field(ADMIN_SCOPE), "added_at": time.time()}
            )
        _forget_admin(user_id)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error adding admin {user_id}: {e}")
        return False


async def remove_admin(user_id: int, scope: str = None) -> bool:
    """Remove an admin from the one shared admin list."""
    if adminsdb is None:
        return False
    try:
        result = await adminsdb.delete_one(
            {"user_id": int(user_id), **scope_match(ADMIN_SCOPE)}
        )
        _forget_admin(user_id)
        return result.deleted_count > 0
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error removing admin {user_id}: {e}")
        return False


async def get_admins(scope: str = None) -> list:
    """The one shared admin list."""
    if adminsdb is None:
        return []
    admin_list = []
    try:
        async for admin in adminsdb.find(
            {"user_id": {"$gt": 0}, **scope_match(ADMIN_SCOPE)}
        ):
            admin_list.append(admin)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Error fetching admins: {e}")
    return admin_list


async def get_admin_ids(scope: str = None) -> list:
    """Admin ids of the shared list (the main owner included)."""
    admin_list = await get_admins()
    ids = [adm["user_id"] for adm in admin_list]
    owner = config.OWNER_ID
    if owner and owner not in ids:
        ids.append(owner)
    return ids


__all__ = [
    "adminsdb",
    "is_owner",
    "is_admin",
    "add_admin",
    "remove_admin",
    "get_admins",
    "get_admin_ids",
]
