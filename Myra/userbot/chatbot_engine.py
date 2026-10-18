"""
Shared chatbot engine used by BOTH the bot and every userbot.

All intelligence now lives in :mod:`Myra.ai.brain` (hybrid database-first +
Gemini fallback + auto-train). This module only keeps the legacy helpers so
existing imports keep working.
"""

import string

from Myra import LOGGER
from Myra.ai.brain import get_ai_reply, ai_status


def clean_word(word: str) -> str:
    """Normalize a word for MongoDB search (kept for backwards compatibility)."""
    if not word:
        return ""
    return word.strip().lower().strip(string.punctuation)


async def get_chatbot_response(
    text: str = None,
    sticker_id: str = None,
    *,
    allow_ai: bool = True,
) -> dict:
    """
    Hybrid lookup: MongoDB similarity search first, Gemini fallback second.

    Returns
    -------
    dict | None
        ``{"text": ..., "check": "none", "source": ...}``
        ``{"sticker": ..., "check": "sticker", "source": "db"}``
        ``None`` when there is nothing to send (offline mode with no match).
    """
    try:
        return await get_ai_reply(text, sticker_id=sticker_id, allow_ai=allow_ai)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"⚠️ Error fetching chatbot response: {exc}")
        return None


__all__ = ["clean_word", "get_chatbot_response", "get_ai_reply", "ai_status"]
