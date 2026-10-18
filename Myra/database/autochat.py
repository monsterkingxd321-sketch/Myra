"""
Time-to-time messaging ("AutoChat") settings.

The bot and every userbot can keep a group alive by sending a message from time
to time. Everything lives in ``UserbotDB.settings`` (per **scope**, so the main
bot and every clone have their own timings) and survives restarts:

    autochat_bot_enabled      bool
    autochat_bot_interval_ms  int     average gap between messages
    autochat_bot_targets      str     "groups" | "dm" | "both"
    autochat_ub_enabled       bool
    autochat_ub_interval_ms   int
    autochat_ub_targets       str

The bot's timing and the userbots' timing are configured **separately**.
A single userbot can override the global values on its own account document
(``auto_enabled`` / ``auto_interval_ms`` / ``auto_targets``).
"""

from Myra import config
from Myra.database.scope import current_scope
from Myra.database.settings import get_setting, set_setting

KEY_BOT_ENABLED = "autochat_bot_enabled"
KEY_BOT_INTERVAL = "autochat_bot_interval_ms"
KEY_BOT_TARGETS = "autochat_bot_targets"
KEY_UB_ENABLED = "autochat_ub_enabled"
KEY_UB_INTERVAL = "autochat_ub_interval_ms"
KEY_UB_TARGETS = "autochat_ub_targets"

TARGET_GROUPS = "groups"
TARGET_DM = "dm"
TARGET_BOTH = "both"
TARGETS = (TARGET_GROUPS, TARGET_DM, TARGET_BOTH)

MIN_INTERVAL_MS = 30_000          # never faster than 30 s
MAX_INTERVAL_MS = 24 * 60 * 60 * 1000  # 24 h


def _cfg_bool(name: str, default: bool) -> bool:
    return bool(getattr(config, name, default))


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(getattr(config, name, default) or default)
    except Exception:  # noqa: BLE001
        return default


def _cfg_target(name: str, default: str) -> str:
    value = str(getattr(config, name, default) or default).lower()
    return value if value in TARGETS else default


def clamp_interval(ms) -> int:
    try:
        ms = int(ms)
    except Exception:  # noqa: BLE001
        return _cfg_int("AUTOCHAT_BOT_INTERVAL_MS", 900_000)
    return max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, ms))


def normalize_target(value) -> str:
    value = str(value or "").lower()
    if value in ("group", "groups", "grp", "gc"):
        return TARGET_GROUPS
    if value in ("dm", "dms", "private", "pm", "users"):
        return TARGET_DM
    return TARGET_BOTH


def targets_include_groups(target: str) -> bool:
    return target in (TARGET_GROUPS, TARGET_BOTH)


def targets_include_dm(target: str) -> bool:
    return target in (TARGET_DM, TARGET_BOTH)


# ── bot ────────────────────────────────────────────────────────────────────
async def get_bot_enabled(scope: str = None) -> bool:
    value = await get_setting(KEY_BOT_ENABLED, None, scope)
    if value is None:
        return _cfg_bool("AUTOCHAT_BOT_ENABLED", False)
    return bool(value)


async def set_bot_enabled(state: bool, scope: str = None) -> bool:
    return await set_setting(KEY_BOT_ENABLED, bool(state), scope)


async def get_bot_interval(scope: str = None) -> int:
    value = await get_setting(KEY_BOT_INTERVAL, None, scope)
    if value is None:
        return clamp_interval(_cfg_int("AUTOCHAT_BOT_INTERVAL_MS", 900_000))
    return clamp_interval(value)


async def set_bot_interval(ms, scope: str = None) -> bool:
    return await set_setting(KEY_BOT_INTERVAL, clamp_interval(ms), scope)


async def get_bot_targets(scope: str = None) -> str:
    value = await get_setting(KEY_BOT_TARGETS, None, scope)
    if value is None:
        return _cfg_target("AUTOCHAT_BOT_TARGETS", TARGET_GROUPS)
    return normalize_target(value)


async def set_bot_targets(value: str, scope: str = None) -> bool:
    return await set_setting(KEY_BOT_TARGETS, normalize_target(value), scope)


# ── userbots ───────────────────────────────────────────────────────────────
async def get_ub_enabled(scope: str = None) -> bool:
    value = await get_setting(KEY_UB_ENABLED, None, scope)
    if value is None:
        return _cfg_bool("AUTOCHAT_UB_ENABLED", False)
    return bool(value)


async def set_ub_enabled(state: bool, scope: str = None) -> bool:
    return await set_setting(KEY_UB_ENABLED, bool(state), scope)


async def get_ub_interval(scope: str = None) -> int:
    value = await get_setting(KEY_UB_INTERVAL, None, scope)
    if value is None:
        return clamp_interval(_cfg_int("AUTOCHAT_UB_INTERVAL_MS", 600_000))
    return clamp_interval(value)


async def set_ub_interval(ms, scope: str = None) -> bool:
    return await set_setting(KEY_UB_INTERVAL, clamp_interval(ms), scope)


async def get_ub_targets(scope: str = None) -> str:
    value = await get_setting(KEY_UB_TARGETS, None, scope)
    if value is None:
        return _cfg_target("AUTOCHAT_UB_TARGETS", TARGET_GROUPS)
    return normalize_target(value)


async def set_ub_targets(value: str, scope: str = None) -> bool:
    return await set_setting(KEY_UB_TARGETS, normalize_target(value), scope)


# ── per-account resolution ─────────────────────────────────────────────────
async def resolve_account_settings(account: dict, scope: str = None) -> dict:
    """
    Effective AutoChat settings for one userbot account.

    The account document wins; otherwise the global userbot settings of the
    account's own scope apply.
    """
    account = account or {}
    scope = scope or account.get("scope") or current_scope()

    enabled = account.get("auto_enabled")
    if enabled is None:
        enabled = await get_ub_enabled(scope)

    interval = account.get("auto_interval_ms")
    if interval is None:
        interval = await get_ub_interval(scope)
    interval = clamp_interval(interval)

    targets = account.get("auto_targets")
    if not targets:
        targets = await get_ub_targets(scope)

    return {
        "enabled": bool(enabled),
        "interval": interval,
        "targets": normalize_target(targets),
    }


def label_for_target(target: str) -> str:
    return {
        TARGET_GROUPS: "💬 Groups only",
        TARGET_DM: "✉️ DMs only",
        TARGET_BOTH: "🌐 Groups + DMs",
    }.get(normalize_target(target), "🌐 Groups + DMs")


def human_interval(ms) -> str:
    try:
        seconds = max(0, int(ms)) // 1000
    except Exception:  # noqa: BLE001
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s".replace(" 0s", "")
    hours, rem = divmod(seconds, 3600)
    if rem:
        return f"{hours}h {rem // 60}m"
    return f"{hours}h"


# ── graceful fallback (used when the training DB has nothing to say) ───────
FALLBACK_LINES = (
    "hii sab log ✨",
    "kya chal raha hai yaha? 🥺",
    "koi hai online? 😅",
    "aaj mausam acha hai yaar 🌸",
    "bore ho rahi hoon, baat karo na 🙈",
    "hello ji, kaise ho sab? 💖",
    "chai peene ka mann kar raha hai 😌",
    "kuch acha batao yaar 😹",
)


__all__ = [
    "TARGET_GROUPS",
    "TARGET_DM",
    "TARGET_BOTH",
    "TARGETS",
    "MIN_INTERVAL_MS",
    "MAX_INTERVAL_MS",
    "FALLBACK_LINES",
    "clamp_interval",
    "normalize_target",
    "targets_include_groups",
    "targets_include_dm",
    "get_bot_enabled",
    "set_bot_enabled",
    "get_bot_interval",
    "set_bot_interval",
    "get_bot_targets",
    "set_bot_targets",
    "get_ub_enabled",
    "set_ub_enabled",
    "get_ub_interval",
    "set_ub_interval",
    "get_ub_targets",
    "set_ub_targets",
    "resolve_account_settings",
    "label_for_target",
    "human_interval",
]
