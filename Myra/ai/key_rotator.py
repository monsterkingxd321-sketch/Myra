"""
Multi API-key rotator.

Keeps a rotating pool of Gemini API keys. When a key hits a rate limit
(HTTP 429 / RESOURCE_EXHAUSTED) or is invalid, it is parked for a cooldown
period and the next key is tried automatically — the request is never dropped.
"""

import time
import asyncio

from Myra import config, LOGGER


class AIKeyError(Exception):
    """Base error for the rotator."""


class AllKeysExhausted(AIKeyError):
    """Raised when every key failed or is currently cooling down."""


_RATE_LIMIT_MARKERS = (
    "429",
    "resource_exhausted",
    "resource exhausted",
    "rate limit",
    "ratelimit",
    "rate_limit",
    "too many requests",
    "quota",
    "quota exceeded",
    "exceeded your current quota",
    "overloaded",
    "capacity",
    "503",
)

_INVALID_KEY_MARKERS = (
    "api key not valid",
    "api_key_invalid",
    "invalid api key",
    "api key expired",
    "invalid key",
    "unauthenticated",
    "permission_denied",
    "permission denied",
    "401",
    "403",
)


def is_rate_limit_error(message: str) -> bool:
    msg = (message or "").lower()
    return any(marker in msg for marker in _RATE_LIMIT_MARKERS)


# Permanent "you have no money" errors. These are NOT transient 429s: retrying
# the key every 60s just spams the log, so the key is parked for a long time.
_QUOTA_MARKERS = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "no credits remaining",
    "add credits",
    "billing",
)


def is_quota_error(message: str) -> bool:
    """True when the account/key is out of credits (permanent, not transient)."""
    msg = (message or "").lower()
    return any(marker in msg for marker in _QUOTA_MARKERS)


def is_invalid_key_error(message: str) -> bool:
    msg = (message or "").lower()
    return any(marker in msg for marker in _INVALID_KEY_MARKERS)


def mask_key(key: str) -> str:
    """Show only the first/last characters of a key for safe logging."""
    if not key:
        return "—"
    if len(key) <= 10:
        return key[:2] + "***"
    return f"{key[:6]}...{key[-4:]}"


class KeyRotator:
    """Thread/task-safe round-robin API key pool."""

    def __init__(self, keys, cooldown: int = 60, name: str = "gemini"):
        self.name = name
        self.cooldown = max(int(cooldown or 0), 5)
        # Out-of-credits keys are parked far longer than rate-limited ones.
        self.quota_cooldown = max(
            int(getattr(config, "AI_QUOTA_COOLDOWN", 1800) or 0), 60
        )
        # de-duplicate while keeping the original order
        self._keys = list(dict.fromkeys([str(k).strip() for k in (keys or []) if str(k).strip()]))
        self._state = {
            key: {
                "cooldown_until": 0.0,
                "uses": 0,
                "failures": 0,
                "last_error": "",
                "invalid": False,
            }
            for key in self._keys
        }
        self._cursor = 0
        self._lock = asyncio.Lock()

    # ── introspection ───────────────────────────────────────────────────
    @property
    def total(self) -> int:
        return len(self._keys)

    @property
    def enabled(self) -> bool:
        return bool(self._keys)

    def active_keys(self) -> list[str]:
        now = time.time()
        return [
            key
            for key in self._keys
            if self._state[key]["cooldown_until"] <= now and not self._state[key]["invalid"]
        ]

    @property
    def active_count(self) -> int:
        return len(self.active_keys())

    def is_available(self) -> bool:
        return self.active_count > 0

    def stats(self) -> dict:
        now = time.time()
        details = []
        for key in self._keys:
            state = self._state[key]
            if state["invalid"]:
                status = "invalid"
            elif state["cooldown_until"] > now:
                status = f"cooldown {int(state['cooldown_until'] - now)}s"
            else:
                status = "active"
            details.append(
                {
                    "key": mask_key(key),
                    "status": status,
                    "uses": state["uses"],
                    "failures": state["failures"],
                }
            )
        return {
            "total": self.total,
            "active": self.active_count,
            "details": details,
        }

    # ── execution ───────────────────────────────────────────────────────
    async def execute(self, factory):
        """
        Run ``await factory(key)`` against the next healthy key.

        ``factory`` is an async callable that receives the API key.
        Raises :class:`AllKeysExhausted` when no key could serve the request.
        """
        if not self._keys:
            raise AllKeysExhausted("no API keys configured")

        async with self._lock:
            available = self.active_keys()
            if not available:
                raise AllKeysExhausted("all API keys are rate-limited or invalid")
            # rotate the starting point so load is spread evenly
            self._cursor %= len(available)
            order = available[self._cursor:] + available[: self._cursor]
            self._cursor = (self._cursor + 1) % len(available)

        last_error = None
        for index, key in enumerate(order):
            try:
                result = await factory(key)
                self._state[key]["uses"] += 1
                self._state[key]["failures"] = 0
                self._state[key]["last_error"] = ""
                if index > 0:
                    LOGGER.info(f"🔁 {self.name}: switched to key {mask_key(key)}")
                return result
            except Exception as exc:  # noqa: BLE001 - we classify by message
                message = str(exc)
                last_error = message
                state = self._state[key]
                state["failures"] += 1
                state["last_error"] = message[:200]

                if is_invalid_key_error(message):
                    state["invalid"] = True
                    LOGGER.error(
                        f"❌ {self.name}: key {mask_key(key)} is invalid, removing from pool"
                    )
                elif is_quota_error(message):
                    # Out of credits: parking for an hour beats a 429 log storm
                    # on every single message.
                    state["cooldown_until"] = time.time() + self.quota_cooldown
                    LOGGER.error(
                        f"💳 {self.name}: key {mask_key(key)} has no credits/quota left — "
                        f"paused for {self.quota_cooldown}s "
                        f"(add credits or another key in /admin → AI & Keys)"
                    )
                elif is_rate_limit_error(message):
                    state["cooldown_until"] = time.time() + self.cooldown
                    LOGGER.warning(
                        f"⏳ {self.name}: key {mask_key(key)} rate-limited, "
                        f"cooling down for {self.cooldown}s"
                    )
                else:
                    state["cooldown_until"] = time.time() + min(self.cooldown, 15)
                    LOGGER.warning(
                        f"⚠️ {self.name}: key {mask_key(key)} failed: {message[:120]}"
                    )
                continue

        raise AllKeysExhausted(last_error or "unknown error")

    def reset_cooldowns(self) -> None:
        """Bring every key back online (used by /aireload)."""
        now = time.time()
        for key in self._keys:
            self._state[key]["cooldown_until"] = now
            self._state[key]["invalid"] = False
            self._state[key]["failures"] = 0


__all__ = [
    "KeyRotator",
    "AIKeyError",
    "AllKeysExhausted",
    "is_rate_limit_error",
    "is_invalid_key_error",
    "is_quota_error",
    "mask_key",
]
