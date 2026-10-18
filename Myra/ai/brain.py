"""
The Brain — hybrid database-first intelligence.

Flow for every incoming message::

    ┌──────────────────────┐
    │ 1. Search MongoDB    │  fuzzy / token match  (high confidence?)
    └──────────┬───────────┘
               │ hit ───────────────► reply from DB   (0 API cost)
               │ miss
    ┌──────────▼───────────┐
    │ 2. AI fallback       │  Gemini, then OpenAI (AI_PROVIDER_ORDER)
    └──────────┬───────────┘        every provider has its own key pool
               │ result
    ┌──────────▼───────────┐
    │ 3. Clean + Auto-train│  save cleaned pair back into MongoDB
    └──────────┬───────────┘
               │
    ┌──────────▼───────────┐
    │ 4. Offline mode      │  keys gone / rate-limited → DB only
    └──────────────────────┘
"""

import asyncio

from Myra import config, LOGGER
from Myra.ai import cleaner
from Myra.ai.gemini import gemini_service
from Myra.ai.openai_client import openai_service
from Myra.ai.humanizer import feminize, girly_touch
from Myra.database import training


# ── provider registry ──────────────────────────────────────────────────────
# Both providers are always registered; `AI_PROVIDER_ORDER` decides which one
# is asked first when the database has no answer.
PROVIDERS = {
    "gemini": gemini_service,
    "openai": openai_service,
}


def provider_order() -> list:
    """Configured providers, in the order they should be tried."""
    configured = [
        str(name).strip().lower()
        for name in (getattr(config, "AI_PROVIDER_ORDER", None) or [])
    ]
    order = [name for name in configured if name in PROVIDERS]
    for name in PROVIDERS:  # keep any provider the user forgot to list
        if name not in order:
            order.append(name)
    return order


def ai_available() -> bool:
    """True when at least one provider has a usable key right now."""
    return any(PROVIDERS[name].is_available() for name in provider_order())


def ai_configured() -> bool:
    """True when at least one provider has any key configured at all."""
    return any(PROVIDERS[name].enabled for name in provider_order())


# ── prompt building ────────────────────────────────────────────────────────
_PROMPT_TEMPLATE = (
    "A user on Telegram sent this message:\n"
    '"""{message}"""\n\n'
    "Write ONLY the reply as a Hinglish GIRL (female first-person grammar: "
    "'karti hoon', 'kar rahi hoon', 'nahi jaanti', 'sakti hoon') — short, casual, "
    "max 1-2 lines, with cute emojis. Do not use links, @usernames or promotional "
    "words. Do not repeat the user's message."
)


def _girl_voice(value: str) -> str:
    """
    Final pass on every outgoing line so the bot always sounds like a girl.

    Applied to **database replies too**, because the migrated training data was
    written without a fixed gender.
    """
    if not value:
        return value
    text = value
    if getattr(config, "AI_FEMININE", True):
        text = feminize(text)
    if getattr(config, "AI_GIRLY_EMOJI", True):
        text = girly_touch(text)
    return text


# tiny in-memory cache so identical messages in a burst don't hit the API twice
_recent: dict = {}
_recent_lock = asyncio.Lock()


def _build_prompt(message: str) -> str:
    return _PROMPT_TEMPLATE.format(message=message[:400])


async def _cache_get(key: str):
    async with _recent_lock:
        entry = _recent.get(key)
    if not entry:
        return None
    value, created = entry
    if asyncio.get_event_loop().time() - created > 300:
        return None
    return value


async def _cache_set(key: str, value):
    async with _recent_lock:
        if len(_recent) > 500:
            _recent.clear()
        _recent[key] = (value, asyncio.get_event_loop().time())


async def _ask_providers(prompt: str) -> tuple[str | None, str | None]:
    """
    Ask each configured provider in order.

    Returns ``(reply, provider_name)`` — ``(None, None)`` when every provider is
    out of usable keys or refused to answer.
    """
    for name in provider_order():
        service = PROVIDERS[name]
        if not service.is_available():
            continue
        try:
            reply = await service.generate(prompt)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"🤖 {name} failed: {exc}")
            continue
        if reply:
            return reply, name
    return None, None


# ── main entry ─────────────────────────────────────────────────────────────
async def get_ai_reply(
    text: str = None,
    sticker_id: str = None,
    *,
    allow_group_ai: bool = True,
    allow_ai: bool = True,
) -> dict | None:
    """
    Produce a reply for an incoming message.

    Returns one of::

        {"text": "...",    "check": "none",    "source": "db"|"gemini"|"openai"}
        {"sticker": "...", "check": "sticker", "source": "db"}
        None   →  nothing to send

    ``allow_group_ai`` / ``allow_ai`` let the caller disable the AI fallback
    (e.g. group chats where AI replies are unwanted).
    """
    text = (text or "").strip()

    # 1. Sticker match (database only — AI never generates stickers)
    if sticker_id:
        hit = await training.search_sticker(sticker_id)
        if hit:
            return hit

    if not text:
        return None

    # 2. Database-first search
    if config.AI_DB_FIRST:
        hit = await training.search_reply(text)
        if hit:
            LOGGER.debug(f"🧠 DB hit ({hit.get('score')}): {text[:40]}")
            asyncio.create_task(training.bump_usage(text))
            if hit.get("text"):
                hit["text"] = _girl_voice(hit["text"])
            return hit

    # 3. AI fallback — Gemini and/or OpenAI, whichever has a live key
    use_ai = (
        allow_ai
        and allow_group_ai
        and config.AI_ENABLE
        and ai_available()
    )

    if not use_ai:
        return None

    cache_key = cleaner.normalize(text)[:160] or text[:160]
    cached = await _cache_get(cache_key)
    if cached:
        return {"text": _girl_voice(cached), "check": "none", "source": "cache"}

    raw_reply, provider = await _ask_providers(_build_prompt(text))
    if not raw_reply:
        return None

    clean_reply = cleaner.clean_text(raw_reply)
    if not clean_reply:
        LOGGER.info(f"🚫 {provider} reply rejected by cleaner, using offline mode")
        return None

    clean_reply = _girl_voice(clean_reply)

    # 4. Auto-train: store the new pair for next time
    if config.AI_AUTO_TRAIN:
        asyncio.create_task(training.save_reply(text, clean_reply, source=provider))

    await _cache_set(cache_key, clean_reply)

    return {"text": clean_reply, "check": "none", "source": provider}


def ai_status() -> dict:
    """Lightweight status snapshot for /stats and dashboards."""
    per_provider = {name: service.stats() for name, service in PROVIDERS.items()}

    details = []
    for name, info in per_provider.items():
        for item in (info.get("details") or []):
            details.append({
                "key": item.get("key"),
                "status": item.get("status"),
                "uses": item.get("uses"),
                "failures": item.get("failures"),
                "provider": name,
            })

    total = sum((info.get("total") or 0) for info in per_provider.values())
    active = sum((info.get("active") or 0) for info in per_provider.values())

    return {
        "enabled": config.AI_ENABLE,
        "db_first": config.AI_DB_FIRST,
        "auto_train": config.AI_AUTO_TRAIN,
        "threshold": config.AI_SIMILARITY_THRESHOLD,
        "total": total,
        "active": active,
        "details": details,
        "order": provider_order(),
        "providers": {
            name: {
                "total": info.get("total") or 0,
                "active": info.get("active") or 0,
                "model": info.get("model"),
                "sdk": info.get("sdk"),
                "base_url": info.get("base_url"),
            }
            for name, info in per_provider.items()
        },
        # kept for backwards compatibility with older panels
        "model": (per_provider.get("gemini") or {}).get("model"),
    }


__all__ = [
    "get_ai_reply",
    "ai_status",
    "PROVIDERS",
    "provider_order",
    "ai_available",
    "ai_configured",
]

