"""
Runtime-managed AI API keys (Gemini + OpenAI/ChatGPT).

Besides the ``.env`` lists, an admin can add **as many keys as they want** at
runtime with ``/addkey``. They are stored in MongoDB so they survive restarts
and are merged with the env keys by the provider services
(:class:`Myra.ai.gemini.GeminiService` /
:class:`Myra.ai.openai_client.OpenAIService`).

Every document knows which provider it belongs to, because one shared key pool
would try an OpenAI key against Google's endpoint (and vice versa):

    {"key": "…", "provider": "gemini"|"openai", "base_url": "…", "label": "…"}

Documents written before providers existed have no ``provider`` field and are
treated as ``"gemini"``.

Collection: ``UserbotDB.ai_keys``
"""

import time

from Myra import LOGGER, bot_mongo

AI_KEYS = bot_mongo["UserbotDB"]["ai_keys"] if bot_mongo is not None else None

GEMINI = "gemini"
OPENAI = "openai"
PROVIDERS = (GEMINI, OPENAI)


def normalize_provider(provider: str) -> str:
    """``None`` / anything unknown becomes ``gemini`` (the legacy default)."""
    value = (provider or "").strip().lower()
    if value in ("openai", "chatgpt", "gpt", "openai-compatible"):
        return OPENAI
    return GEMINI


def provider_of(doc: dict) -> str:
    """Provider stored on a key document (legacy documents = gemini)."""
    return normalize_provider((doc or {}).get("provider"))


def guess_provider(key: str) -> str:
    """Best-effort provider guess from the key's shape (used by /addkey)."""
    value = (key or "").strip()
    if value.startswith("sk-") or value.startswith("sk_"):
        return OPENAI
    if value.startswith("AIza") or value.startswith("AQ."):
        return GEMINI
    return GEMINI


async def add_key(
    key: str,
    added_by: int = None,
    label: str = "",
    provider: str = GEMINI,
    base_url: str = "",
) -> bool:
    """Store an API key (upsert). Returns False when storage is unavailable."""
    if AI_KEYS is None or not key or not key.strip():
        return False
    key = key.strip()
    try:
        await AI_KEYS.update_one(
            {"key": key},
            {
                "$set": {
                    "key": key,
                    "provider": normalize_provider(provider),
                    "base_url": (base_url or "").strip(),
                    "label": label or "",
                    "added_by": int(added_by) if added_by else None,
                    "updated_at": time.time(),
                },
                "$setOnInsert": {"created_at": time.time()},
            },
            upsert=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"⚠️ Failed to add API key: {exc}")
        return False


async def remove_key_by_value(key: str) -> bool:
    if AI_KEYS is None or not key:
        return False
    try:
        result = await AI_KEYS.delete_one({"key": key.strip()})
        return result.deleted_count > 0
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"⚠️ Failed to remove API key: {exc}")
        return False


async def remove_key_by_index(index: int, provider: str = None) -> str | None:
    """Remove the *index*-th stored key (1-based). Returns the removed key.

    When ``provider`` is given the index counts only that provider's keys.
    """
    docs = await list_key_docs(provider)
    if index < 1 or index > len(docs):
        return None
    key = docs[index - 1].get("key")
    if key and await remove_key_by_value(key):
        return key
    return None


async def remove_key_by_mask(masked: str, provider: str = None) -> str | None:
    """Remove a key by its masked form (as shown by /aikeys)."""
    from Myra.ai.key_rotator import mask_key

    for doc in await list_key_docs(provider):
        key = doc.get("key") or ""
        if mask_key(key) == masked.strip():
            return key if await remove_key_by_value(key) else None

    # Fall back to a global search so a masked key copied from another panel
    # still works.
    if provider:
        for doc in await list_key_docs():
            key = doc.get("key") or ""
            if mask_key(key) == masked.strip():
                return key if await remove_key_by_value(key) else None
    return None


async def list_key_docs(provider: str = None) -> list:
    """Key documents, optionally only for one provider (legacy = gemini)."""
    if AI_KEYS is None:
        return []
    try:
        if provider:
            norm = normalize_provider(provider)
            if norm == GEMINI:
                # Legacy documents have no provider field at all.
                query = {"$or": [
                    {"provider": {"$in": [GEMINI, None]}},
                    {"provider": {"$exists": False}},
                ]}
            else:
                query = {"provider": norm}
            return await AI_KEYS.find(query).sort("created_at", 1).to_list(length=5000)
        return await AI_KEYS.find().sort("created_at", 1).to_list(length=5000)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"⚠️ Failed to list API keys: {exc}")
        return []


async def get_keys(provider: str = None) -> list[str]:
    return [doc.get("key") for doc in await list_key_docs(provider) if doc.get("key")]


async def count_keys(provider: str = None) -> int:
    if AI_KEYS is None:
        return 0
    try:
        if provider:
            norm = normalize_provider(provider)
            if norm == GEMINI:
                return await AI_KEYS.count_documents({"$or": [
                    {"provider": {"$in": [GEMINI, None]}},
                    {"provider": {"$exists": False}},
                ]})
            return await AI_KEYS.count_documents({"provider": norm})
        return await AI_KEYS.count_documents({})
    except Exception:  # noqa: BLE001
        return 0


async def clear_keys(provider: str = None) -> int:
    if AI_KEYS is None:
        return 0
    try:
        if provider:
            norm = normalize_provider(provider)
            if norm == GEMINI:
                result = await AI_KEYS.delete_many({"$or": [
                    {"provider": {"$in": [GEMINI, None]}},
                    {"provider": {"$exists": False}},
                ]})
            else:
                result = await AI_KEYS.delete_many({"provider": norm})
            return result.deleted_count
        result = await AI_KEYS.delete_many({})
        return result.deleted_count
    except Exception:  # noqa: BLE001
        return 0


__all__ = [
    "AI_KEYS",
    "GEMINI",
    "OPENAI",
    "PROVIDERS",
    "normalize_provider",
    "provider_of",
    "guess_provider",
    "add_key",
    "remove_key_by_value",
    "remove_key_by_index",
    "remove_key_by_mask",
    "list_key_docs",
    "get_keys",
    "count_keys",
    "clear_keys",
]
