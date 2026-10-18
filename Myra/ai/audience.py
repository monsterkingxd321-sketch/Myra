"""
Audience guard — *only real human users* may trigger an AutoChat reply.

The bot and every userbot ignore each other so the fleet can never talk to
itself (which would burn API quota and look obviously fake):

* any **bot** account (including this bot itself)
* any **other connected userbot** (all of our own accounts, from memory + DB)
* the sender itself
* **channel / anonymous** posts and service messages (no ``from_user``)

Toggle with ``ONLY_REAL_USERS=false`` in ``.env``.

All imports are done lazily inside the functions so this module can be imported
from anywhere (bot side or userbot side) without circular-import problems.
"""

import time

from Myra import config, LOGGER

_CACHE = {"ids": set(), "ts": 0.0}
_CACHE_TTL = 60.0


def in_memory_ids() -> set:
    """Telegram ids of the currently connected userbot accounts."""
    ids = set()
    try:
        from Myra.userbot.storage import accounts as ub_accounts

        for account in (ub_accounts or {}).values():
            tid = account.get("telegram_id")
            if tid:
                ids.add(int(tid))
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"audience: in-memory ids unavailable: {exc}")
    return ids


async def own_account_ids(force: bool = False) -> set:
    """Every Telegram id that belongs to our own fleet (memory + database)."""
    now = time.time()
    if not force and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["ids"]

    ids = in_memory_ids()
    try:
        from Myra.userbot.storage import get_served_accounts

        for doc in await get_served_accounts():
            tid = doc.get("telegram_id")
            if tid:
                ids.add(int(tid))
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"audience: could not load stored accounts: {exc}")

    _CACHE["ids"] = ids
    _CACHE["ts"] = now
    return ids


def invalidate_cache() -> None:
    """Force the next lookup to re-read the account list."""
    _CACHE["ts"] = 0.0


async def is_real_user(message, my_id: int = None) -> bool:
    """
    True only when *message* was written by a genuine human user.

    Rejected: our own outgoing messages, our own accounts, any bot account,
    channel/anonymous posts and service messages.
    """
    if not getattr(config, "ONLY_REAL_USERS", True):
        return True

    if getattr(message, "outgoing", False):
        return False

    sender = getattr(message, "from_user", None)
    if sender is None:  # channel post / anonymous admin / service message
        return False

    sender_id = getattr(sender, "id", None)

    if my_id is not None and sender_id == my_id:
        return False
    if getattr(sender, "is_bot", False):
        return False
    if sender_id in await own_account_ids():
        return False

    return True


__all__ = ["is_real_user", "own_account_ids", "in_memory_ids", "invalidate_cache"]
