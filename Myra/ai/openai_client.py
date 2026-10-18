"""
OpenAI / ChatGPT wrapper — works with **any OpenAI-compatible endpoint**.

The bot's brain prefers whichever provider answers first: the trained MongoDB
database, then Gemini, then OpenAI (the order is `AI_PROVIDER_ORDER`). This
service is deliberately built on the official ``openai`` SDK but the import is
*lazy*: if the package is missing the bot simply keeps running on Gemini and/or
the database (offline mode) instead of crashing.

Because so many "free ChatGPT key" providers are OpenAI-compatible proxies
(OpenRouter, Groq, Together, a local gateway, …), the endpoint is configurable
through ``OPENAI_BASE_URL`` in ``.env`` **and** per key with
``/addkey openai <key> <base_url>``.

Every call goes through :class:`~Myra.ai.key_rotator.KeyRotator`, so a dead or
rate-limited key never takes the service down.
"""

import asyncio

from Myra import config, LOGGER
from Myra.ai.key_rotator import KeyRotator, AllKeysExhausted

try:  # pragma: no cover - optional dependency
    from openai import AsyncOpenAI as _AsyncOpenAI

    _OPENAI_IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001
    _AsyncOpenAI = None
    _OPENAI_IMPORT_ERROR = str(exc)


def _looks_like_gemini(key: str) -> bool:
    """True for Google keys, which must never enter the OpenAI pool."""
    value = (key or "").strip()
    return value.startswith("AIza") or value.startswith("AQ.")


def _read_key_file(path: str) -> list[str]:
    """Read API keys from a plain text file (one per line, # = comment)."""
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"⚠️ Could not read OPENAI_API_KEYS_FILE ({path}): {exc}")
        return []

    keys = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keys.extend(part.strip() for part in line.replace(",", " ").split() if part.strip())
    return keys


class OpenAIService:
    """Async OpenAI-compatible client with multi-key rotation."""

    def __init__(
        self,
        keys=None,
        model: str = None,
        base_url: str = None,
        cooldown: int = None,
    ):
        self.model = model or config.OPENAI_MODEL
        self.base_url = (base_url or config.OPENAI_BASE_URL or "").rstrip("/")
        self.rotator = KeyRotator(
            keys if keys is not None else config.OPENAI_API_KEYS,
            cooldown=cooldown if cooldown is not None else config.AI_KEY_COOLDOWN,
            name="openai",
        )
        self._base_urls: dict = {}
        self._clients: dict = {}
        self._client_lock = asyncio.Lock()

    # ── status ──────────────────────────────────────────────────────────
    @property
    def sdk_ready(self) -> bool:
        return _AsyncOpenAI is not None

    @property
    def enabled(self) -> bool:
        return bool(getattr(config, "OPENAI_ENABLE", True)) and self.sdk_ready and self.rotator.enabled

    def is_available(self) -> bool:
        return self.enabled and self.rotator.is_available()

    def stats(self) -> dict:
        info = self.rotator.stats()
        info["model"] = self.model
        info["base_url"] = self.base_url
        info["sdk"] = self.sdk_ready
        if not self.sdk_ready:
            info["import_error"] = _OPENAI_IMPORT_ERROR
        return info

    # ── internals ───────────────────────────────────────────────────────
    def _url_for(self, key: str) -> str:
        return (self._base_urls.get(key) or self.base_url or "").rstrip("/")

    async def _client_for(self, key: str):
        async with self._client_lock:
            client = self._clients.get(key)
            if client is None:
                url = self._url_for(key)
                # max_retries=0: our KeyRotator owns the retry/rotation logic.
                # Letting the SDK retry too caused a 3x (SDK) * N (rotator)
                # storm of 429s and "Retrying request" log lines.
                client = _AsyncOpenAI(
                    api_key=key,
                    base_url=url or None,
                    max_retries=0,
                    timeout=config.AI_REQUEST_TIMEOUT,
                )
                self._clients[key] = client
            return client

    async def generate(
        self,
        prompt: str,
        system_instruction: str = None,
        temperature: float = 0.9,
        max_output_tokens: int = None,
    ) -> str | None:
        """
        Generate text from a single prompt.

        Returns the stripped reply, or ``None`` when OpenAI is unavailable or
        every key failed (the caller then falls back to the database only).
        """
        if not self.enabled:
            return None
        if not prompt or not prompt.strip():
            return None

        messages = [
            {"role": "system", "content": system_instruction or config.AI_PERSONA},
            {"role": "user", "content": prompt},
        ]

        async def _call(key: str):
            client = await self._client_for(key)
            return await client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_output_tokens or config.AI_MAX_OUTPUT_TOKENS,
            )

        try:
            response = await self.rotator.execute(_call)
        except AllKeysExhausted as exc:
            LOGGER.debug(f"🤖 OpenAI unavailable (offline mode): {exc}")
            return None
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"🤖 OpenAI unexpected error: {exc}")
            return None

        return self._extract_text(response)

    @staticmethod
    def _extract_text(response) -> str | None:
        if response is None:
            return None
        try:
            choices = getattr(response, "choices", None) or []
            if not choices:
                return None
            message = getattr(choices[0], "message", None)
            text = getattr(message, "content", None)
            if not text:
                # Some proxies answer in the legacy ``text`` field.
                text = getattr(choices[0], "text", None)
        except Exception:  # noqa: BLE001
            text = None
        return text.strip() if text else None

    def reset(self) -> None:
        self.rotator.reset_cooldowns()

    async def refresh_keys(self, include_db: bool = True) -> dict:
        """
        Rebuild the key pool from **.env + key file + MongoDB**.

        Lets an admin add as many keys as they want with ``/addkey openai …``
        and apply them (or freshly edited ``.env`` values) with ``/aireload`` —
        no restart needed.
        """
        import importlib
        from Myra import config as _config

        try:
            importlib.reload(_config)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"⚠️ Could not reload config: {exc}")

        env_keys = list(getattr(_config, "OPENAI_API_KEYS", []) or [])
        file_keys = _read_key_file(getattr(_config, "OPENAI_API_KEYS_FILE", ""))

        db_keys = []
        base_urls = {}
        if include_db:
            try:
                from Myra.database import ai_keys

                docs = await ai_keys.list_key_docs(provider="openai")
                for doc in docs:
                    key = (doc.get("key") or "").strip()
                    if not key:
                        continue
                    db_keys.append(key)
                    if doc.get("base_url"):
                        base_urls[key] = str(doc["base_url"]).strip()
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug(f"could not load DB OpenAI keys: {exc}")

        merged = list(
            dict.fromkeys(
                k
                for k in (env_keys + file_keys + db_keys)
                if k and not _looks_like_gemini(k)
            )
        )

        self.model = getattr(_config, "OPENAI_MODEL", self.model)
        self.base_url = (getattr(_config, "OPENAI_BASE_URL", self.base_url) or "").rstrip("/")
        self.rotator = KeyRotator(
            merged,
            cooldown=getattr(_config, "AI_KEY_COOLDOWN", 60),
            name="openai",
        )
        self._base_urls = base_urls
        self._clients.clear()

        LOGGER.info(
            f"🔄 OpenAI keys refreshed: {len(merged)} total "
            f"(env={len(env_keys)}, file={len(file_keys)}, db={len(db_keys)})"
        )
        info = self.stats()
        info["env_keys"] = len(env_keys)
        info["file_keys"] = len(file_keys)
        info["db_keys"] = len(db_keys)
        return info

    async def validate_key(self, key: str, base_url: str = None) -> tuple[bool, str]:
        """
        Quick sanity check for a user-supplied key.

        Returns ``(usable, message)``. A rate-limited key is still considered
        usable — only clearly invalid keys are rejected.
        """
        from Myra.ai.key_rotator import is_invalid_key_error, is_rate_limit_error

        if not self.sdk_ready:
            return False, "openai SDK is not installed"
        if not key or not key.strip():
            return False, "empty key"

        url = (base_url or self.base_url or "").rstrip("/")
        try:
            client = _AsyncOpenAI(
                api_key=key.strip(),
                base_url=url or None,
                max_retries=0,
                timeout=config.AI_REQUEST_TIMEOUT,
            )
            await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
            )
            return True, "valid ✅"
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if is_invalid_key_error(message):
                return False, "invalid key ❌"
            if is_rate_limit_error(message):
                return True, "valid but currently rate-limited ⏳"
            return True, f"accepted (warning: {message[:100]})"


# Single shared instance used by the bot AND the userbot (same logic).
openai_service = OpenAIService()

__all__ = ["OpenAIService", "openai_service"]
