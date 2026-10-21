"""
Runtime settings shared by the main bot and every clone.

Stored in ``UserbotDB.settings`` as ``{"key": ..., "value": ...}`` documents so
a restart keeps the configuration. Used by ``/speed`` (reply delay), ``/chance``
(reply probability), the AutoChat panel and the Group Request Controller.

The admin surface is **global**: there is exactly one set of values. Change
something in one bot's panel and every bot — the primary bot and all clones —
obeys it.
"""

import time

from Myra import config, LOGGER, bot_mongo
from Myra.database.scope import MAIN_SCOPE

SETTINGS = bot_mongo["UserbotDB"]["settings"] if bot_mongo is not None else None

KEY_REPLY_DELAY = "reply_delay_ms"
KEY_GROUP_CHANCE = "group_reply_chance"
KEY_PRIVATE_CHANCE = "private_reply_chance"

# Group Request Controller (auto-approve join requests)
KEY_JOINREQ_ENABLED = "joinreq_enabled"
KEY_JOINREQ_AUTO = "joinreq_autoapprove"
KEY_JOINREQ_WELCOME = "joinreq_welcome"
KEY_JOINREQ_PHOTO = "joinreq_welcome_photo"


def _default_delay() -> int:
    try:
        return int(config.USERBOT_SPEED_DEFAULT_MS or 0)
    except Exception:  # noqa: BLE001
        return 0


async def get_setting(key: str, default=None, scope: str = None):
    """Read a global setting. The newest document for the key wins."""
    if SETTINGS is None:
        return default
    try:
        docs = await SETTINGS.find({"key": key}).sort("updated_at", -1).to_list(length=1)
        if not docs:
            return default
        return docs[0].get("value", default)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"get_setting({key}) failed: {exc}")
        return default


async def set_setting(key: str, value, scope: str = None) -> bool:
    """Write a global setting (one shared value for every bot)."""
    if SETTINGS is None:
        return False
    try:
        result = await SETTINGS.update_one(
            {"key": key},
            {
                "$set": {
                    "key": key,
                    "value": value,
                    "scope": MAIN_SCOPE,
                    "updated_at": time.time(),
                },
                "$setOnInsert": {"created_at": time.time()},
            },
            upsert=True,
        )
        return bool(result.acknowledged)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"set_setting({key}) failed: {exc}")
        return False


async def get_reply_delay(scope: str = None) -> int:
    """Reply delay in milliseconds for this bot (falls back to config)."""
    value = await get_setting(KEY_REPLY_DELAY, None, scope)
    if value is None:
        return _default_delay()
    try:
        return max(0, int(value))
    except Exception:  # noqa: BLE001
        return _default_delay()


async def set_reply_delay(ms: int, scope: str = None) -> bool:
    return await set_setting(KEY_REPLY_DELAY, max(0, int(ms)), scope)


async def _get_chance(key: str, default: float, scope: str = None) -> float:
    value = await get_setting(key, None, scope)
    if value is None:
        return float(default)
    try:
        return max(0.0, min(100.0, float(value)))
    except Exception:  # noqa: BLE001
        return float(default)


async def get_group_chance(scope: str = None) -> float:
    """Probability (0-100) of replying in group chats."""
    return await _get_chance(KEY_GROUP_CHANCE, getattr(config, "USERBOT_GROUP_CHANCE", 70), scope)


async def set_group_chance(percent: float, scope: str = None) -> bool:
    return await set_setting(KEY_GROUP_CHANCE, max(0.0, min(100.0, float(percent))), scope)


async def get_private_chance(scope: str = None) -> float:
    """Probability (0-100) of replying in private chats."""
    return await _get_chance(KEY_PRIVATE_CHANCE, getattr(config, "USERBOT_PRIVATE_CHANCE", 100), scope)


async def set_private_chance(percent: float, scope: str = None) -> bool:
    return await set_setting(KEY_PRIVATE_CHANCE, max(0.0, min(100.0, float(percent))), scope)


__all__ = [
    "SETTINGS",
    "KEY_REPLY_DELAY",
    "KEY_GROUP_CHANCE",
    "KEY_PRIVATE_CHANCE",
    "get_setting",
    "set_setting",
    "get_reply_delay",
    "set_reply_delay",
    "get_group_chance",
    "set_group_chance",
    "get_private_chance",
    "set_private_chance",
]
