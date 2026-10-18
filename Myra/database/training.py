"""
Training store — the "memory" of the hybrid AI.

Uses the SAME collection for the bot and every userbot so all instances share
one brain::

    MongoDB  →  Database: Word  →  Collection: WordDb

Document schema (backwards compatible with the old AyushChatBot data)::

    {
        "_id":         ObjectId,
        "word":        "hello bhai",          # normalized trigger (indexed)
        "text":        "hlooo ✨",            # reply text  /  sticker file_id
        "check":       "none" | "sticker",
        "id":          "<sticker file_unique_id>",   # stickers only

        # ── fields added by the hybrid upgrade ──
        "trigger_raw": "Hello Bhai!!",        # original message text
        "tokens":      ["hello", "bhai"],     # token index for fuzzy search
        "source":      "gemini" | "learned" | "manual",
        "usage_count": 0,
        "created_at":  1699999999.0,
        "updated_at":  1699999999.0,
    }
"""

import re
import time
from difflib import SequenceMatcher

from Myra import config, LOGGER, mongo
from Myra.ai import cleaner

try:  # optional fast path
    from rapidfuzz import fuzz as _rf_fuzz
except Exception:  # noqa: BLE001
    _rf_fuzz = None

# Same collection the legacy bot used → shared between bot and userbots.
TRAINING = mongo["Word"]["WordDb"] if mongo is not None else None

_TOKEN_RE = re.compile(r"[a-z0-9\u0900-\u097F]{2,}")


# ────────────────────────────── helpers ────────────────────────────────────
def tokenize(text: str) -> list[str]:
    """Lower-case tokens (Latin + Devanagari) used for candidate lookup."""
    if not text:
        return []
    tokens = _TOKEN_RE.findall(str(text).lower())
    # de-duplicate, keep order, cap the list
    return list(dict.fromkeys(tokens))[:15]


def similarity(a: str, b: str) -> float:
    """Return a 0..1 similarity score between two trigger strings."""
    if not a or not b:
        return 0.0
    a = re.sub(r"\s+", " ", a.strip().lower())
    b = re.sub(r"\s+", " ", b.strip().lower())
    if a == b:
        return 1.0

    score = 0.0

    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        score = max(score, 0.82 + 0.15 * (len(shorter) / max(len(longer), 1)))

    ta, tb = set(tokenize(a)), set(tokenize(b))
    if ta and tb:
        inter = len(ta & tb)
        union = len(ta | tb)
        jaccard = inter / union if union else 0.0
        containment = inter / min(len(ta), len(tb))
        score = max(score, jaccard, containment * 0.92)

    if _rf_fuzz is not None:
        ratio = _rf_fuzz.token_set_ratio(a, b) / 100.0
    else:
        tokens_a = " ".join(sorted(ta))
        tokens_b = " ".join(sorted(tb))
        ratio = SequenceMatcher(None, tokens_a or a, tokens_b or b).ratio()
    score = max(score, ratio * 0.9)

    return round(min(score, 1.0), 4)


async def ensure_indexes() -> None:
    """Create the indexes required for fast similarity search."""
    if TRAINING is None:
        return
    try:
        await TRAINING.create_index("word")
        LOGGER.info("✅ Chatbot index ready: word")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"⚠️ Could not create 'word' index: {exc}")
    # The 'tokens' multikey index gives better recall but it is LARGE (it can
    # add 100-200 MB on a multi-million document collection). It is therefore
    # opt-in — leave it off on the Atlas free tier (512 MB).
    if getattr(config, "AI_CREATE_TOKENS_INDEX", False):
        try:
            await TRAINING.create_index("tokens")
            LOGGER.info("✅ Chatbot index ready: tokens")
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(f"⚠️ Could not create 'tokens' index: {exc}")
    else:
        LOGGER.info("ℹ️ 'tokens' index disabled (AI_CREATE_TOKENS_INDEX=false) — "
                    "searching by word + prefix")
    # The full-text index is optional: it is large and only used as a last
    # resort fallback. Enable it only when you have enough storage.
    if getattr(config, "AI_CREATE_TEXT_INDEX", False):
        try:
            await TRAINING.create_index([("text", "text")])
            LOGGER.info("✅ Chatbot index ready: text (full-text)")
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(f"⚠️ Could not create 'text' index: {exc}")


# ────────────────────────────── search ─────────────────────────────────────
_tokens_index_available = None


async def has_tokens_index() -> bool:
    """Detect once whether the (large) 'tokens' index exists."""
    global _tokens_index_available
    if _tokens_index_available is not None:
        return _tokens_index_available
    try:
        info = await TRAINING.index_information()
        _tokens_index_available = any(
            any(str(field) == "tokens" for field, _ in spec.get("key", []))
            for spec in info.values()
        )
    except Exception:  # noqa: BLE001
        _tokens_index_available = False
    return _tokens_index_available


async def _candidates(trigger: str, limit: int) -> list[dict]:
    """Fetch a bounded candidate set from MongoDB (never a full collection scan)."""
    normalized = cleaner.normalize(trigger) or trigger.strip().lower()
    tokens = tokenize(normalized)
    pool: dict = {}

    async def _pull(query: dict, cap: int):
        try:
            cursor = TRAINING.find(query).limit(cap)
            async for doc in cursor:
                pool[doc["_id"]] = doc
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(f"candidate query failed: {exc}")

    # 1. exact / normalized variants (word index)
    variants = list(dict.fromkeys([trigger.strip(), trigger.strip().lower(), normalized]))
    await _pull({"word": {"$in": variants}}, 20)

    # 2. token overlap (only when the multi-key index exists — otherwise this
    #    would be a full collection scan on huge datasets)
    if tokens and await has_tokens_index():
        await _pull({"tokens": {"$in": tokens}}, limit)

    # 3. index-friendly prefix match on the leading token (indexed)
    if tokens:
        first = re.escape(tokens[0])
        await _pull({"word": {"$regex": f"^{first}", "$options": "i"}}, 25)

    # 4. optional MongoDB full-text index as an extra fallback
    if not pool and normalized and getattr(config, "AI_CREATE_TEXT_INDEX", False):
        await _pull({"$text": {"$search": normalized}}, 20)

    return list(pool.values())


async def search_reply(trigger: str, threshold: float = None) -> dict | None:
    """
    Database-first lookup.

    Returns ``{"text": ..., "check": "none", "source": ...}`` when a
    high-confidence match exists, otherwise ``None``.
    """
    if TRAINING is None or not trigger:
        return None

    threshold = config.AI_SIMILARITY_THRESHOLD if threshold is None else threshold
    limit = config.AI_SEARCH_CANDIDATES

    try:
        docs = await _candidates(trigger, limit)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"⚠️ Chatbot search error: {exc}")
        return None

    if not docs:
        return None

    normalized = (cleaner.normalize(trigger) or trigger).strip().lower()
    best_doc = None
    best_score = 0.0

    for doc in docs:
        word = (doc.get("word") or "").strip().lower()
        score = similarity(normalized, word)
        # if a raw variant matches exactly, boost it
        if word and word == trigger.strip().lower():
            score = 1.0
        if score > best_score:
            best_score = score
            best_doc = doc

    if not best_doc or best_score < threshold:
        return None

    if best_doc.get("check") == "sticker" and best_doc.get("text"):
        return {
            "sticker": best_doc["text"],
            "check": "sticker",
            "score": best_score,
            "source": "db",
        }

    if best_doc.get("text"):
        return {
            "text": best_doc["text"],
            "check": "none",
            "score": best_score,
            "source": "db",
        }

    return None


async def search_sticker(sticker_id: str) -> dict | None:
    """Look up a learned sticker reply by its unique id."""
    if TRAINING is None or not sticker_id:
        return None
    try:
        doc = await TRAINING.find_one({"id": sticker_id, "check": "sticker"})
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"sticker lookup failed: {exc}")
        return None
    if doc and doc.get("text"):
        return {"sticker": doc["text"], "check": "sticker", "source": "db"}
    return None


# ────────────────────────────── writes ─────────────────────────────────────
async def save_reply(trigger: str, reply: str, source: str = "gemini") -> bool:
    """
    Auto-train: store a cleaned trigger → reply pair.

    The reply is cleaned again here (defence in depth) and rejected when it
    still contains links / mentions / promotional text.
    """
    if TRAINING is None or not trigger or not reply:
        return False

    clean_reply = cleaner.clean_text(reply)
    if not clean_reply:
        LOGGER.debug("🚫 Rejected reply that failed cleaning")
        return False

    # Store it in the bot's (girl) voice so future DB hits are already correct.
    if getattr(config, "AI_FEMININE", True):
        try:
            from Myra.ai.humanizer import feminize

            clean_reply = feminize(clean_reply)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(f"feminize skipped: {exc}")

    clean_trigger = cleaner.normalize(trigger)
    if not clean_trigger:
        return False

    now = time.time()
    doc = {
        "word": clean_trigger,
        "text": clean_reply,
        "check": "none",
        "trigger_raw": trigger.strip()[:300],
        "tokens": tokenize(clean_trigger),
        "source": source,
        "updated_at": now,
    }

    try:
        result = await TRAINING.update_one(
            {"word": clean_trigger, "text": clean_reply},
            {
                "$set": doc,
                "$setOnInsert": {"created_at": now, "usage_count": 0},
            },
            upsert=True,
        )
        return bool(result.upserted_id or result.modified_count)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"⚠️ Failed to save trained reply: {exc}")
        return False


async def save_sticker_reply(trigger: str, file_id: str, unique_id: str) -> bool:
    """Store a learned sticker reply for a trigger."""
    if TRAINING is None or not trigger or not file_id:
        return False
    clean_trigger = cleaner.normalize(trigger)
    if not clean_trigger:
        return False
    try:
        existing = await TRAINING.find_one({"word": clean_trigger, "id": unique_id})
        if existing:
            return False
        await TRAINING.insert_one(
            {
                "word": clean_trigger,
                "text": file_id,
                "check": "sticker",
                "id": unique_id,
                "trigger_raw": trigger.strip()[:300],
                "tokens": tokenize(clean_trigger),
                "source": "learned",
                "usage_count": 0,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
        )
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"⚠️ Failed to save sticker reply: {exc}")
        return False


async def bump_usage(trigger: str) -> None:
    """Increment the usage counter for analytics (best-effort)."""
    if TRAINING is None:
        return
    try:
        await TRAINING.update_one(
            {"word": cleaner.normalize(trigger) or trigger},
            {"$inc": {"usage_count": 1}},
        )
    except Exception:
        pass


# ────────────────────────────── maintenance ────────────────────────────────
async def clean_database() -> dict:
    """
    Scan the whole training collection and delete every entry whose trigger or
    reply contains links, @usernames or blacklisted promotional words.
    """
    report = {"scanned": 0, "deleted": 0, "reasons": {}}
    if TRAINING is None:
        return report

    buffer = []

    async def _flush():
        nonlocal buffer
        if not buffer:
            return
        try:
            result = await TRAINING.delete_many({"_id": {"$in": buffer}})
            report["deleted"] += result.deleted_count
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"⚠️ clean_db delete failed: {exc}")
        buffer = []

    try:
        cursor = TRAINING.find({}, {"word": 1, "text": 1, "check": 1})
        async for doc in cursor:
            report["scanned"] += 1
            if doc.get("check") == "sticker":
                continue  # sticker file_ids are not text
            text = doc.get("text") or ""
            word = doc.get("word") or ""
            violations = cleaner.find_violations(text) + cleaner.find_violations(word)
            if not text.strip() or not cleaner.is_clean(text, allow_emoji_only=True):
                violations.append("unclean_reply")
            if violations:
                for reason in set(violations):
                    report["reasons"][reason] = report["reasons"].get(reason, 0) + 1
                buffer.append(doc["_id"])
                if len(buffer) >= 500:
                    await _flush()
        await _flush()
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"⚠️ clean_db scan failed: {exc}")

    invalidate_stats()
    return report


_STATS_CACHE = {"ts": 0.0, "data": None}
STATS_TTL = 300          # seconds — keeps /admin and /stats instant
_STATS_SCAN = 50_000     # documents examined to estimate sticker ratios


def invalidate_stats() -> None:
    """Force the next ``get_stats()`` call to recompute (after a big change)."""
    _STATS_CACHE["ts"] = 0.0
    _STATS_CACHE["data"] = None


async def get_stats(use_cache: bool = True) -> dict:
    """
    Collection + database size statistics for /stats and the admin panel.

    Exact ``count_documents()`` on a multi-million document collection takes
    many seconds (and the ``check``/``source`` fields are not indexed), so the
    totals come from the collection metadata and the sticker ratio from a
    bounded scan of the first 50 000 documents. The result is cached for
    ``STATS_TTL`` seconds.
    """
    now = time.time()
    if use_cache and _STATS_CACHE["data"] and now - _STATS_CACHE["ts"] < STATS_TTL:
        return _STATS_CACHE["data"]

    stats = {
        "total": 0,
        "texts": 0,
        "stickers": 0,
        "stickers_exact": True,
        "gemini_trained": 0,
        "learned": 0,
        "collection_bytes": 0,
        "db_bytes": 0,
        "available": TRAINING is not None,
    }

    if TRAINING is None:
        return stats

    try:
        total = await TRAINING.estimated_document_count()
    except Exception:  # noqa: BLE001
        total = 0
    stats["total"] = total

    if total:
        try:
            rows = await TRAINING.aggregate([
                {"$limit": min(total, _STATS_SCAN)},
                {"$group": {"_id": {"c": "$check", "s": "$source"}, "n": {"$sum": 1}}},
            ]).to_list(length=50)
            scanned = sum(int(r.get("n", 0)) for r in rows) or 1
            scale = total / scanned
            stickers = 0
            gemini = 0
            learned = 0
            for row in rows:
                key = row.get("_id") or {}
                count = int(row.get("n", 0))
                if (key.get("c") or "none") == "sticker":
                    stickers += count
                source = key.get("s")
                if source == "gemini":
                    gemini += count
                elif source == "learned":
                    learned += count
            stats["stickers"] = int(round(stickers * scale))
            stats["gemini_trained"] = int(round(gemini * scale))
            stats["learned"] = int(round(learned * scale))
            stats["stickers_exact"] = total <= _STATS_SCAN
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(f"training stats aggregation failed: {exc}")

        stats["texts"] = max(0, stats["total"] - stats["stickers"])

    try:
        coll = await mongo["Word"].command("collstats", TRAINING.name)
        stats["collection_bytes"] = int(
            coll.get("storageSize") or coll.get("size") or 0
        )
    except Exception:
        pass

    try:
        db_stats = await mongo["Word"].command("dbstats")
        stats["db_bytes"] = int(db_stats.get("dataSize") or 0)
    except Exception:
        pass

    _STATS_CACHE["ts"] = now
    _STATS_CACHE["data"] = stats
    return stats


def human_bytes(size: float) -> str:
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} GB"


__all__ = [
    "TRAINING",
    "tokenize",
    "similarity",
    "ensure_indexes",
    "has_tokens_index",
    "search_reply",
    "search_sticker",
    "save_reply",
    "save_sticker_reply",
    "bump_usage",
    "clean_database",
    "get_stats",
    "invalidate_stats",
    "human_bytes",
]
