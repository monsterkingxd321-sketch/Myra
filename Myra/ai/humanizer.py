"""
Human-like behaviour helpers.

Real people do not reply instantly, do not reply to *every* message and do not
type at a constant speed. This module adds that imperfection:

* :func:`should_reply`      — probabilistic reply (so the bot replies "kabhi kabhi")
* :func:`typing_seconds`    — typing duration based on reply length
* :func:`reading_pause`     — small pause before starting to type
* :func:`human_touch`       — cosmetic touches (drop trailing full stop, etc.)
* :func:`maybe_split`       — occasionally send a long reply in 2 bubbles
* :func:`respond_like_human`— the full send-with-pacing routine
"""

import asyncio
import random
import re
import time

from pyrogram.enums import ChatAction
from pyrogram.errors import FloodWait, SlowmodeWait

DEFAULT_CHANCE = 85.0
DEFAULT_WPM = 220.0

# Chats where Telegram told us to wait (group slowmode / flood). Keyed by chat id,
# value is a ``time.monotonic()`` deadline. A chat in cooldown is skipped without
# typing or a seen-mark, which is what stops the retry storm: with slowmode on,
# every incoming message used to trigger another failed send, another ERROR line
# and another 420 — dozens per second, on Railway that also means log I/O.
_CHAT_COOLDOWN: dict = {}
_COOLDOWN_MAX = 15 * 60          # never trust a longer "wait" than 15 min


def _cooldown_left(chat_id) -> float:
    """Seconds left on a chat's slowmode cooldown (0 when free)."""
    left = _CHAT_COOLDOWN.get(chat_id, 0.0) - time.monotonic()
    if left <= 0:
        _CHAT_COOLDOWN.pop(chat_id, None)
        return 0.0
    return left


def _cool_down(chat_id, seconds) -> float:
    try:
        seconds = float(seconds or 0)
    except Exception:  # noqa: BLE001
        seconds = 60.0
    seconds = max(1.0, min(seconds, _COOLDOWN_MAX))
    _CHAT_COOLDOWN[chat_id] = time.monotonic() + seconds + 3.0
    return seconds


_WAIT_RE = re.compile(r"(\d+)\s*(?:seconds?|secs?)", re.IGNORECASE)


def _wait_seconds(exc, default: float = 60.0) -> float:
    """
    How long Telegram asked us to wait.

    ``FloodWait.value`` is normally an int, but the raw RPC error value can be a
    string, and a slowmode error reads "wait 172 seconds" — so fall back to the
    number inside the message instead of always assuming 60 s.
    """
    value = getattr(exc, "value", None)
    if isinstance(value, (int, float)):
        return max(1.0, float(value))
    match = _WAIT_RE.search(str(exc))
    if match:
        return max(1.0, float(match.group(1)))
    try:
        return max(1.0, float(value))
    except Exception:  # noqa: BLE001
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def should_reply(chance) -> bool:
    """Return True with probability *chance* (0-100). 100 => always."""
    try:
        chance = float(chance)
    except Exception:  # noqa: BLE001
        chance = DEFAULT_CHANCE
    if chance >= 100:
        return True
    if chance <= 0:
        return False
    return random.random() * 100.0 < chance


def typing_seconds(
    text: str,
    wpm: float = DEFAULT_WPM,
    min_s: float = 0.9,
    max_s: float = 9.0,
    jitter: float = 0.35,
) -> float:
    """How long a human would take to type *text*."""
    chars = max(len(text or ""), 1)
    chars_per_second = max(float(wpm or DEFAULT_WPM), 60.0) * 5.0 / 60.0
    base = chars / chars_per_second
    base = clamp(base, min_s, max_s)
    base += random.uniform(0.2, 0.9)  # thinking time
    spread = jitter * 0.5
    return base * random.uniform(1.0 - spread, 1.0 + spread)


def reading_pause(text: str, max_s: float = 1.8) -> float:
    """Short delay before the person starts typing."""
    length = len(text or "")
    return random.uniform(0.15, min(max_s, 0.15 + length / 300.0))


def human_touch(text: str) -> str:
    """Small cosmetic cleanups so replies do not look machine-perfect."""
    if not text:
        return text
    out = text.strip()
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\.{3,}", "..", out)
    # Telegram chat rarely ends with a formal full stop
    if len(out) > 3 and out.endswith(".") and not out.endswith(".."):
        out = out[:-1].rstrip()
    return out or text


# ══════════════════════════════════════════════════════════════════════════
#  GIRL VOICE
#  The bot is a girl, so every outgoing line has to use feminine Hinglish
#  first-person grammar ("karta hoon" → "karti hoon"). Only FIRST-PERSON
#  forms are touched, so a reply that talks about the user ("tu kar raha hai")
#  is never broken.
# ══════════════════════════════════════════════════════════════════════════

_FIRST_PERSON = r"(?:hoon|hoo|hun|hu)"
_HOON = rf"(?=\s*{_FIRST_PERSON}\b)"

_FEMININE_RULES = (
    # "kar raha hoon" → "kar rahi hoon"
    (re.compile(rf"\braha\b{_HOON}", re.IGNORECASE), "rahi"),
    (re.compile(r"\braha\s+tha\b", re.IGNORECASE), "rahi thi"),
    # "karta hoon" → "karti hoon"  (only with a 1st-person verb after it)
    (re.compile(rf"\b(\w{{2,}})ta\b{_HOON}", re.IGNORECASE), r"\1ti"),
    (re.compile(r"(\w{2,})ta\s+tha\b", re.IGNORECASE), r"\1ti thi"),
    # "thak gaya hoon" → "thak gayi hoon"
    (re.compile(rf"\bgaya\b{_HOON}", re.IGNORECASE), "gayi"),
    (re.compile(rf"\baaya\b{_HOON}", re.IGNORECASE), "aayi"),
    (re.compile(rf"\bhua\b{_HOON}", re.IGNORECASE), "hui"),
    # future: "karunga" → "karungi", "dunga" → "dungi"
    (re.compile(r"\b(\w+)unga\b", re.IGNORECASE), r"\1ungi"),
)

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u2764\u2728]"
)

GIRLY_EMOJIS = ("✨", "🥺", "😅", "🙈", "💖", "😂", "😌", "🌸", "🤭", "😹")


def feminize(text: str) -> str:
    """Convert first-person Hinglish masculine forms to feminine ones."""
    if not text:
        return text
    out = text
    for pattern, replacement in _FEMININE_RULES:
        out = pattern.sub(replacement, out)
    return out


def girly_touch(text: str, chance: float = 0.25) -> str:
    """Occasionally add a cute emoji when the reply has none at all."""
    if not text or _EMOJI_RE.search(text):
        return text
    if random.random() * 100 >= chance * 100:
        return text
    return f"{text} {random.choice(GIRLY_EMOJIS)}"


def make_feminine(text: str, emoji_chance: float = 0.25) -> str:
    """Full girl-voice pass: grammar + optional emoji."""
    return girly_touch(feminize(text), chance=emoji_chance)


def maybe_split(text: str, prob: float = 0.15, min_len: int = 70):
    """
    Occasionally split a longer reply into two messages, like a real person
    who types one thought and then adds another.
    """
    if not text or len(text) < min_len or random.random() > prob:
        return [text]
    mid = len(text) // 2
    breaks = [m.start() for m in re.finditer(r"[.!?]\s+", text)]
    if not breaks:
        return [text]
    best = min(breaks, key=lambda index: abs(index - mid))
    first, second = text[: best + 1].strip(), text[best + 1 :].strip()
    if not first or not second or len(first) < 15 or len(second) < 10:
        return [text]
    return [first, second]


async def _typing_for(client, chat_id, seconds: float, action=ChatAction.TYPING, chunk: float = 4.0):
    """Keep the 'typing…' indicator alive while we wait."""
    remaining = max(float(seconds or 0), 0.0)
    if remaining <= 0:
        return
    try:
        while remaining > 0:
            await client.send_chat_action(chat_id, action)
            step = min(chunk, remaining)
            await asyncio.sleep(step)
            remaining -= step
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - chat action is best-effort
        await asyncio.sleep(min(remaining, chunk))


async def mark_seen(client, chat_id, logger=None) -> bool:
    """
    Open the chat — i.e. mark it as read, the blue double tick ("seen").

    Only **user** accounts have read receipts: a bot can never mark a chat as
    read, so this is a no-op for the main bot and the clones. Best effort by
    design — a failed read must never stop the reply.
    """
    try:
        if getattr(getattr(client, "me", None), "is_bot", False):
            return False
    except Exception:  # noqa: BLE001
        pass

    try:
        await client.read_chat_history(int(chat_id))
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.debug(f"mark_seen failed for {chat_id}: {exc}")
        return False


async def respond_like_human(
    client,
    message,
    response: dict,
    *,
    chance=DEFAULT_CHANCE,
    base_delay_ms=0,
    wpm: float = DEFAULT_WPM,
    allow_split: bool = True,
    logger=None,
) -> bool:
    """
    Send *response* the way a person would. Returns True when something was sent.

    ``chance``          – probability (0-100) that we reply at all
    ``base_delay_ms``   – extra fixed delay from /speed
    ``wpm``             – typing speed used for the typing indicator
    ``allow_split``     – allow splitting long replies into two bubbles
    """
    if not response:
        return False
    if not should_reply(chance):
        return False

    chat_id = message.chat.id
    if _cooldown_left(chat_id):
        # Telegram already asked us to wait here. Staying silent is the fix —
        # retrying on every new message produced a 420 + an ERROR line per
        # message (the slowmode storm in the logs).
        return False

    # A real person opens the chat first — your message turns "seen" — and only
    # then starts typing. Read receipts are user-only, so this silently does
    # nothing when the main bot (or a clone) is the one replying.
    await mark_seen(client, chat_id, logger=logger)

    try:
        # ── sticker ──────────────────────────────────────────────────────
        if response.get("sticker"):
            delay = reading_pause("sticker") + max(base_delay_ms, 0) / 1000.0
            await _typing_for(
                client, message.chat.id, delay, action=ChatAction.CHOOSE_STICKER, chunk=2.0
            )
            await message.reply_sticker(response["sticker"])
            return True

        # ── text ─────────────────────────────────────────────────────────
        text = human_touch(response.get("text") or "")
        if not text:
            return False

        await asyncio.sleep(reading_pause(text))
        if base_delay_ms and base_delay_ms > 0:
            await asyncio.sleep(base_delay_ms / 1000.0)

        bubble_delay = random.uniform(0.9, 2.2)
        for part in maybe_split(text) if allow_split else [text]:
            if not part:
                continue
            await _typing_for(client, message.chat.id, typing_seconds(part, wpm))
            await message.reply_text(part)
            await asyncio.sleep(random.uniform(0.2, 0.5))
            if part is not text:
                await asyncio.sleep(bubble_delay)
        return True

    except asyncio.CancelledError:
        raise
    except SlowmodeWait as exc:
        # Group slowmode. Telegram tells us exactly how long to wait; mute the
        # chat for that long instead of retrying on the very next message.
        wait = _cool_down(chat_id, _wait_seconds(exc))
        if logger is not None:
            logger.debug(f"slowmode in {chat_id}: muted for {int(wait)}s")
        return False
    except FloodWait as exc:
        wait = _cool_down(chat_id, _wait_seconds(exc))
        if logger is not None:
            logger.warning(f"flood wait in {chat_id}: muted for {int(wait)}s")
        return False
    except Exception as exc:  # noqa: BLE001
        # Expected Telegram refusals (expired sticker file reference, no write
        # rights, message already gone…) are not ERROR-worthy and used to flood
        # the log at one line per message.
        name = type(exc).__name__.lower()
        quiet = (
            "filereference" in name
            or "chatwriteforbidden" in name
            or "messagenotmodified" in name
            or "messageid_invalid" in name
            or "peeridinvalid" in name
        )
        if logger is not None:
            if quiet:
                logger.debug(f"human reply skipped in {chat_id}: {type(exc).__name__}")
            else:
                logger.warning(f"human reply failed: {exc}")
        return False


__all__ = [
    "DEFAULT_CHANCE",
    "DEFAULT_WPM",
    "should_reply",
    "typing_seconds",
    "reading_pause",
    "human_touch",
    "maybe_split",
    "mark_seen",
    "respond_like_human",
    "feminize",
    "girly_touch",
    "make_feminine",
    "GIRLY_EMOJIS",
]
