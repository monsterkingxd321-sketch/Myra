import re as _re
from os import getenv
from dotenv import load_dotenv

load_dotenv()


def _env_list(*names, default=""):
    """Read one or more env vars and split them into a clean list.

    Supports comma / newline / space separated values so both
    ``GEMINI_API_KEYS=k1,k2,k3`` and ``GEMINI_API_KEYS=k1\nk2`` work.
    """
    raw = ""
    for name in names:
        value = getenv(name)
        if value:
            raw = value
            break
    if not raw:
        raw = default
    return [item.strip() for item in _re.split(r"[,\s]+", raw or "") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "y")

# ⚠️ NO SECRETS ARE HARDCODED IN THIS FILE. Everything comes from the
# environment (host Variables panel) or from .env, which is git-ignored.
# `python -m Myra` runs a preflight check and tells you what is missing.

# Telegram API credentials — https://my.telegram.org → "API development tools"
API_ID = int(getenv("API_ID", "0") or 0)
API_HASH = getenv("API_HASH", "")

# Bot configuration — @BotFather
BOT_TOKEN = getenv("BOT_TOKEN", None)

# ── MongoDB: two separate clusters ─────────────────────────────────────────
# MONGO_URL      → the CHAT/TRAINING brain only (``Word.WordDb``: learned
#                  replies + stickers). Nothing else is written here.
# BOT_MONGO_URL  → everything the bot owns: users, chats, admins, userbot
#                  accounts, sessions, clones, settings and AI keys
#                  (``Anonymous``, ``UserbotDB``, ``SessionDB``, ``BotDB``,
#                  ``MYRADb``).
# Leave BOT_MONGO_URL empty to keep the old single-cluster behaviour (both
# then use MONGO_URL).
MONGO_URL = getenv("MONGO_URL", "")
BOT_MONGO_URL = (
    getenv("BOT_MONGO_URL", "")
    or getenv("BOT_DB_URL", "")
    or getenv("BOT_MONGO_URI", "")
    or MONGO_URL
)

# Owner & Support info
OWNER_ID = int(getenv("OWNER_ID", "0") or 0)
SUPPORT_GRP = getenv("SUPPORT_GRP", "")
UPDATE_CHNL = getenv("UPDATE_CHNL", "")
OWNER_USERNAME = getenv("OWNER_USERNAME", "")

# Userbot configuration
USERBOT_AUTOCHAT_DEFAULT = getenv("USERBOT_AUTOCHAT_DEFAULT", "true").lower() == "true"

# Delay (in milliseconds) applied to every userbot reply unless a per-account
# value was configured from the dashboard. Controlled live with /speed <ms>.
USERBOT_SPEED_DEFAULT_MS = int(getenv("USERBOT_SPEED_DEFAULT_MS", "0"))

# ══════════════════════════════════════════════════════════════════
#  HYBRID AI (Database-First)  —  Google Gemini
# ══════════════════════════════════════════════════════════════════

# One or many Gemini API keys. Rotation happens automatically.
# Accepts GEMINI_API_KEYS, GEMINI_API_KEY or the legacy AI_API_KEYS names.
GEMINI_API_KEYS = _env_list(
    "GEMINI_API_KEYS",
    "GEMINI_API_KEY",
    "AI_API_KEYS",
    "AI_API_KEY",
    "GOOGLE_API_KEY",
)
GEMINI_MODEL = getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Optional: path to a text file with one API key per line (unlimited keys).
GEMINI_API_KEYS_FILE = getenv("GEMINI_API_KEYS_FILE", "")

# Master switches
AI_ENABLE = _env_bool("AI_ENABLE", True)                 # Use Gemini fallback at all
AI_DB_FIRST = _env_bool("AI_DB_FIRST", True)             # Search MongoDB before calling Gemini
AI_AUTO_TRAIN = _env_bool("AI_AUTO_TRAIN", True)         # Save cleaned Gemini replies back to DB
AI_PRIVATE_FALLBACK = _env_bool("AI_PRIVATE_FALLBACK", True)
AI_GROUP_FALLBACK = _env_bool("AI_GROUP_FALLBACK", True)
# Use Gemini for group messages that do NOT mention / reply to the bot.
# OFF by default: the trained database already answers common messages, and
# answering every single group message with the AI would burn the free quota.
AI_GROUP_FALLBACK_PLAIN = _env_bool("AI_GROUP_FALLBACK_PLAIN", False)

# Similarity search tuning
AI_SIMILARITY_THRESHOLD = float(getenv("AI_SIMILARITY_THRESHOLD", "0.72"))
AI_SEARCH_CANDIDATES = int(getenv("AI_SEARCH_CANDIDATES", "80"))
AI_MAX_OUTPUT_TOKENS = int(getenv("AI_MAX_OUTPUT_TOKENS", "160"))

# Create the (large) full-text index on the reply text. Only enable it when
# your MongoDB has enough storage — it is used as a last-resort fallback only.
AI_CREATE_TEXT_INDEX = _env_bool("AI_CREATE_TEXT_INDEX", False)
# The 'tokens' multikey index improves recall but is LARGE on big collections.
# Keep it off on the Atlas free tier (512 MB) — search falls back to
# word + prefix matching automatically.
AI_CREATE_TOKENS_INDEX = _env_bool("AI_CREATE_TOKENS_INDEX", False)

# ── OpenAI / ChatGPT (works with ANY OpenAI-compatible endpoint) ────────────
# One or many keys, rotated automatically just like the Gemini pool.
# Accepts OPENAI_API_KEYS, OPENAI_API_KEY, CHATGPT_API_KEYS or CHATGPT_API_KEY.
OPENAI_API_KEYS = _env_list(
    "OPENAI_API_KEYS",
    "OPENAI_API_KEY",
    "CHATGPT_API_KEYS",
    "CHATGPT_API_KEY",
)
OPENAI_MODEL = getenv("OPENAI_MODEL", "gpt-4o-mini")
# Point this at OpenRouter / Groq / any OpenAI-compatible proxy when you use a
# free key that is not issued by OpenAI itself.
OPENAI_BASE_URL = getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
# Optional: a text file with one key per line (# = comment).
OPENAI_API_KEYS_FILE = getenv("OPENAI_API_KEYS_FILE", "")
OPENAI_ENABLE = _env_bool("OPENAI_ENABLE", True)

# Which provider is tried first when the database has no answer.
# Accepts "gemini,openai" (default) or "openai,gemini".
AI_PROVIDER_ORDER = [
    p.strip().lower()
    for p in getenv("AI_PROVIDER_ORDER", "gemini,openai").replace(";", ",").split(",")
    if p.strip()
] or ["gemini", "openai"]

# ══════════════════════════════════════════════════════════════════
#  AUDIENCE  (who is allowed to trigger an AutoChat reply)
# ══════════════════════════════════════════════════════════════════

# Chat ONLY with real human users. Bots (including this bot itself), our other
# userbot accounts, channel posts and anonymous admins are all ignored.
ONLY_REAL_USERS = _env_bool("ONLY_REAL_USERS", True)

# Seconds a key is parked after a 429 before it is retried.
AI_KEY_COOLDOWN = int(getenv("AI_KEY_COOLDOWN", "60"))

# Seconds to wait for one AI request before giving up. The SDK's own retry
# loop is disabled (max_retries=0) so the KeyRotator is the only retry layer.
AI_REQUEST_TIMEOUT = float(getenv("AI_REQUEST_TIMEOUT", "60"))

# Seconds a key is parked when the account is out of credits / quota
# (e.g. OpenAI "insufficient_quota"). Long, because retrying will not help.
AI_QUOTA_COOLDOWN = int(getenv("AI_QUOTA_COOLDOWN", "1800"))

# ══════════════════════════════════════════════════════════════════
#  HUMAN-LIKE BEHAVIOUR
# ══════════════════════════════════════════════════════════════════

# Probability (0-100) that a reply is actually sent. < 100 makes the bot reply
# "sometimes" instead of to every single message, like a real person.
AI_GROUP_REPLY_CHANCE = float(getenv("AI_GROUP_REPLY_CHANCE", "85"))
AI_PRIVATE_REPLY_CHANCE = float(getenv("AI_PRIVATE_REPLY_CHANCE", "100"))
USERBOT_GROUP_CHANCE = float(getenv("USERBOT_GROUP_CHANCE", "70"))
USERBOT_PRIVATE_CHANCE = float(getenv("USERBOT_PRIVATE_CHANCE", "100"))

# Reply to normal group messages (not only @mentions / replies).
# Set to false if you only want the userbot to answer when it is addressed.
USERBOT_GROUP_REPLY_ALL = _env_bool("USERBOT_GROUP_REPLY_ALL", True)

# Typing speed used for the "typing…" indicator (words per minute).
HUMAN_TYPING_WPM = float(getenv("HUMAN_TYPING_WPM", "220"))

# Occasionally send a long reply as two separate bubbles.
HUMAN_SPLIT_REPLIES = _env_bool("HUMAN_SPLIT_REPLIES", True)

# ══════════════════════════════════════════════════════════════════
#  TIME-TO-TIME MESSAGING  (AutoChat)
# ══════════════════════════════════════════════════════════════════
# The bot and the userbots keep groups alive by sending a message every now
# and then. Bot timing and userbot timing are configured SEPARATELY — both at
# runtime from the /admin panel (these are only the startup defaults).

AUTOCHAT_ENABLED = _env_bool("AUTOCHAT_ENABLED", True)      # master switch
AUTOCHAT_TICK_SECONDS = int(getenv("AUTOCHAT_TICK_SECONDS", "20"))
AUTOCHAT_BATCH = int(getenv("AUTOCHAT_BATCH", "1"))         # chats per tick

# Main bot defaults
AUTOCHAT_BOT_ENABLED = _env_bool("AUTOCHAT_BOT_ENABLED", False)
AUTOCHAT_BOT_INTERVAL_MS = int(getenv("AUTOCHAT_BOT_INTERVAL_MS", "900000"))   # 15 min
AUTOCHAT_BOT_TARGETS = getenv("AUTOCHAT_BOT_TARGETS", "groups")               # groups|dm|both

# Userbot defaults
AUTOCHAT_UB_ENABLED = _env_bool("AUTOCHAT_UB_ENABLED", False)
AUTOCHAT_UB_INTERVAL_MS = int(getenv("AUTOCHAT_UB_INTERVAL_MS", "600000"))    # 10 min
AUTOCHAT_UB_TARGETS = getenv("AUTOCHAT_UB_TARGETS", "groups")                 # groups|dm|both

# ── Cloned bots ────────────────────────────────────────────────────────────
# Anyone can host their own copy of this bot with `/clone <bot_token>`. A clone
# runs inside this same process but with its own owner, admins, userbots,
# groups/DMs and settings. The trained Hinglish database and the Gemini keys are
# shared on purpose — more bots chatting means a better shared brain.
CLONE_ENABLED = _env_bool("CLONE_ENABLED", True)          # allow /clone at all
MAX_CLONES = int(getenv("MAX_CLONES", "20"))              # process is shared → cap it
# Empty = everyone may clone. Comma/space separated ids to restrict it.
CLONE_ALLOWED_USERS = getenv("CLONE_ALLOWED_USERS", "")

# ── Girl voice ─────────────────────────────────────────────────────────────
# Feminize every outgoing line (DB replies included) so the bot always speaks
# like a girl: "karta hoon" → "karti hoon", "raha hoon" → "rahi hoon".
AI_FEMININE = _env_bool("AI_FEMININE", True)
# Occasionally add a cute emoji to a reply that has none.
AI_GIRLY_EMOJI = _env_bool("AI_GIRLY_EMOJI", True)

# Persona written into every Gemini request (kept short & casual on purpose).
AI_PERSONA = getenv(
    "AI_PERSONA",
    "You are a cute, lively Hinglish GIRL chatting casually on Telegram. "
    "You are female — ALWAYS use feminine first-person grammar (Roman Hindi): "
    "'main karti hoon', 'kar rahi hoon', 'nahi jaanti', 'sakti hoon', 'karungi', "
    "never 'karta hoon' / 'raha hoon' / 'karunga'. "
    "Always reply in short Hinglish (Roman Hindi + English mix), max 1-2 lines, "
    "under 120 characters, playful and emotional with emojis like ✨ 🥺 😂 💖 😅 🙈 🌸. "
    "Never claim to be a bot or an AI. Never repeat the user's message back. "
    "STRICT RULES: no links, no http/https, no t.me, no @usernames, no promotion, "
    "no 'join', 'subscribe', 'buy', 'click', no advertising of any kind.",
)

