"""
Hybrid AI layer (Database-first + Gemini / OpenAI fallback).

Modules
-------
cleaner      : strict anti-spam / link / mention cleaner
humanizer    : human-like pacing — reply chance, typing time, bubble split
key_rotator  : multi API-key rotation with rate-limit handling
gemini       : google-genai client wrapper (shared instance)
openai_client: OpenAI / ChatGPT compatible client wrapper (shared instance)
brain        : the hybrid decision engine used by bot AND userbot
"""

from Myra.ai.cleaner import clean_text, normalize, is_clean, find_violations
from Myra.ai.humanizer import (
    should_reply,
    typing_seconds,
    reading_pause,
    human_touch,
    maybe_split,
    mark_seen,
    respond_like_human,
)
from Myra.ai.key_rotator import KeyRotator, AllKeysExhausted
from Myra.ai.gemini import gemini_service, GeminiService
from Myra.ai.openai_client import openai_service, OpenAIService
from Myra.ai.brain import (
    get_ai_reply,
    ai_status,
    PROVIDERS,
    provider_order,
    ai_available,
    ai_configured,
)

__all__ = [
    "clean_text",
    "normalize",
    "is_clean",
    "find_violations",
    "should_reply",
    "typing_seconds",
    "reading_pause",
    "human_touch",
    "maybe_split",
    "mark_seen",
    "respond_like_human",
    "KeyRotator",
    "AllKeysExhausted",
    "gemini_service",
    "GeminiService",
    "openai_service",
    "OpenAIService",
    "get_ai_reply",
    "ai_status",
    "PROVIDERS",
    "provider_order",
    "ai_available",
    "ai_configured",
]
