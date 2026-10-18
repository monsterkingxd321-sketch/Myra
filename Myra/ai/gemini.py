"""
Google Gemini wrapper built on top of the official ``google-genai`` SDK.

All calls go through the :class:`~Myra.ai.key_rotator.KeyRotator` so a dead or
rate-limited key never takes the service down. If the SDK is missing or no key
is available the service silently reports "no reply" and the bot keeps running
on the database only (offline mode).
"""

import asyncio

from Myra import config, LOGGER
from Myra.ai.key_rotator import KeyRotator, AllKeysExhausted

try:  # pragma: no cover - optional dependency
    from google import genai as _genai
    from google.genai import types as _genai_types

    _GENAI_IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001
    _genai = None
    _genai_types = None
    _GENAI_IMPORT_ERROR = str(exc)


def _read_key_file(path: str) -> list[str]:
    """Read API keys from a plain text file (one per line, # = comment)."""
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"⚠️ Could not read GEMINI_API_KEYS_FILE ({path}): {exc}")
        return []

    keys = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keys.extend(part.strip() for part in line.replace(",", " ").split() if part.strip())
    return keys


def _looks_like_openai(key: str) -> bool:
    """True for OpenAI-style keys, which must never enter the Gemini pool."""
    value = (key or "").strip().lower()
    return value.startswith("sk-") or value.startswith("sk_")


class GeminiService:
    """Async Gemini client with multi-key rotation and per-key client cache."""

    def __init__(self, keys=None, model: str = None, cooldown: int = None):
        self.model = model or config.GEMINI_MODEL
        self.rotator = KeyRotator(
            keys if keys is not None else config.GEMINI_API_KEYS,
            cooldown=cooldown if cooldown is not None else config.AI_KEY_COOLDOWN,
            name="gemini",
        )
        self._clients: dict = {}
        self._client_lock = asyncio.Lock()

    # ── status ──────────────────────────────────────────────────────────
    @property
    def sdk_ready(self) -> bool:
        return _genai is not None

    @property
    def enabled(self) -> bool:
        return self.sdk_ready and self.rotator.enabled

    def is_available(self) -> bool:
        return self.enabled and self.rotator.is_available()

    def stats(self) -> dict:
        info = self.rotator.stats()
        info["model"] = self.model
        info["sdk"] = self.sdk_ready
        if not self.sdk_ready:
            info["import_error"] = _GENAI_IMPORT_ERROR
        return info

    # ── internals ───────────────────────────────────────────────────────
    async def _client_for(self, key: str):
        async with self._client_lock:
            client = self._clients.get(key)
            if client is None:
                client = _genai.Client(api_key=key)
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

        Returns the stripped reply or ``None`` when Gemini is unavailable /
        every key failed (caller then falls back to the database only).
        """
        if not self.enabled:
            return None
        if not prompt or not prompt.strip():
            return None

        gen_config = _genai_types.GenerateContentConfig(
            system_instruction=system_instruction or config.AI_PERSONA,
            temperature=temperature,
            max_output_tokens=max_output_tokens or config.AI_MAX_OUTPUT_TOKENS,
            top_p=0.95,
        )

        async def _call(key: str):
            client = await self._client_for(key)
            return await client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=gen_config,
            )

        try:
            response = await self.rotator.execute(_call)
        except AllKeysExhausted as exc:
            LOGGER.debug(f"🤖 Gemini unavailable (offline mode): {exc}")
            return None
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"🤖 Gemini unexpected error: {exc}")
            return None

        return self._extract_text(response)

    @staticmethod
    def _extract_text(response) -> str | None:
        try:
            text = getattr(response, "text", None)
        except Exception:  # noqa: BLE001
            text = None

        if not text:
            # Newer SDKs can return candidates with parts instead of .text
            try:
                candidates = getattr(response, "candidates", None) or []
                chunks = []
                for candidate in candidates:
                    content = getattr(candidate, "content", None)
                    for part in (getattr(content, "parts", None) or []):
                        part_text = getattr(part, "text", None)
                        if part_text:
                            chunks.append(part_text)
                text = " ".join(chunks) if chunks else None
            except Exception:  # noqa: BLE001
                text = None

        return text.strip() if text else None

    def reset(self) -> None:
        self.rotator.reset_cooldowns()

    async def refresh_keys(self, include_db: bool = True) -> dict:
        """
        Rebuild the key pool from **.env + key file + MongoDB**.

        Lets an admin add as many keys as they want with ``/addkey`` and apply
        them (or freshly edited ``.env`` values) with ``/aireload`` — no restart.
        """
        import importlib
        from Myra import config as _config

        try:
            importlib.reload(_config)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"⚠️ Could not reload config: {exc}")

        env_keys = list(getattr(_config, "GEMINI_API_KEYS", []) or [])
        file_keys = _read_key_file(getattr(_config, "GEMINI_API_KEYS_FILE", ""))

        db_keys = []
        if include_db:
            try:
                from Myra.database import ai_keys

                # Only Google keys — a shared pool used to drag OpenAI keys
                # (sk-…) in here, which Google rejects as invalid and which
                # then spams the log with "key is invalid, removing from pool".
                db_keys = await ai_keys.get_keys(provider="gemini")
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug(f"could not load DB keys: {exc}")

        merged = list(
            dict.fromkeys(
                k for k in (env_keys + file_keys + db_keys) if k and not _looks_like_openai(k)
            )
        )

        self.model = getattr(_config, "GEMINI_MODEL", self.model)
        self.rotator = KeyRotator(
            merged,
            cooldown=getattr(_config, "AI_KEY_COOLDOWN", 60),
            name="gemini",
        )
        self._clients.clear()

        LOGGER.info(
            f"🔄 Gemini keys refreshed: {len(merged)} total "
            f"(env={len(env_keys)}, file={len(file_keys)}, db={len(db_keys)})"
        )
        info = self.stats()
        info["env_keys"] = len(env_keys)
        info["file_keys"] = len(file_keys)
        info["db_keys"] = len(db_keys)
        return info

    async def validate_key(self, key: str) -> tuple[bool, str]:
        """
        Quick sanity check for a user-supplied key.

        Returns ``(usable, message)``. A rate-limited key is still considered
        usable — only clearly invalid keys are rejected.
        """
        from Myra.ai.key_rotator import is_invalid_key_error, is_rate_limit_error

        if not self.sdk_ready:
            return False, "google-genai SDK is not installed"
        if not key or not key.strip():
            return False, "empty key"

        try:
            client = _genai.Client(api_key=key.strip())
            await client.aio.models.generate_content(
                model=self.model,
                contents="hi",
                config=_genai_types.GenerateContentConfig(max_output_tokens=5),
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
gemini_service = GeminiService()

__all__ = ["GeminiService", "gemini_service"]
