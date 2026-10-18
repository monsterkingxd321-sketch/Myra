#!/usr/bin/env python3
"""
Migrate + clean the chatbot training data between two MongoDB clusters.

Copies ``Word.WordDb`` (the chat/training data) from a source cluster to a
target cluster while applying the same anti-spam rules the live bot uses:

  * drops entries containing links, ``t.me`` links, emails or @usernames
  * drops entries containing promotional words ("join here", "buy now", ...)
  * drops empty / junk triggers (``none``, ``null``, ...)
  * normalizes + truncates triggers, cleans replies
  * de-duplicates ``(trigger, reply)`` pairs
  * preserves the original ``_id`` so re-running never duplicates data

Nothing is written to the source cluster (read-only).

Usage
-----
    python tools/migrate_training_db.py \
        --source "mongodb+srv://user:pass@old-cluster.mongodb.net/" \
        --target "mongodb+srv://user:pass@new-cluster.mongodb.net/"

Options
-------
    --source-db / --target-db      database name      (default: Word)
    --source-coll / --target-coll  collection name    (default: WordDb)
    --dry-run                      count only, write nothing
    --limit N                      only migrate the first N source docs (testing)
    --drop                         drop the target collection before migrating
    --no-indexes                   do not create indexes afterwards
    --lean / --full                lean = only _id/word/text/check (default, smallest);
                                   full = also tokens/trigger_raw/timestamps
    --verbose                      print per-batch write timings
    --max-word-len / --max-text-len  truncation lengths (default 200 / 300)

Storage note
------------
The Atlas free tier allows only **512 MB total**. The ``tokens`` index plus the
extra metadata (``trigger_raw``, timestamps) roughly doubled the footprint of a
1.9 M document collection, which is why ``--lean`` is the default. The live bot
detects that the ``tokens`` index is missing and searches by ``word`` +
prefix instead.
"""

import argparse
import importlib.util
import re
import string
import sys
import time
from pathlib import Path

try:
    from pymongo import MongoClient
    from pymongo.errors import BulkWriteError, PyMongoError
except ImportError:
    sys.exit("pymongo is required:  pip install pymongo")


# ── load the project cleaner without importing the whole bot ──────────────
def _load_cleaner():
    path = Path(__file__).resolve().parent.parent / "Myra" / "ai" / "cleaner.py"
    spec = importlib.util.spec_from_file_location("_myra_cleaner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cleaner = _load_cleaner()

JUNK_WORDS = {"none", "null", "undefined", "nan", "true", "false", "nil", "n/a", ""}
_PUNCT = string.punctuation + " \t\n\r"
_WS = re.compile(r"\s+")


def norm_word(word: str, max_len: int) -> str:
    word = _WS.sub(" ", str(word).strip().lower()).strip(_PUNCT)
    return word[:max_len]


class Stats:
    def __init__(self):
        self.scanned = 0
        self.kept = 0
        self.stickers = 0
        self.skipped_empty = 0
        self.skipped_dirty = 0
        self.skipped_junk = 0
        self.skipped_reply = 0
        self.duplicates = 0
        self.inserted = 0
        self.failed = 0

    def report(self):
        return (
            f"    scanned        : {self.scanned:,}\n"
            f"    kept           : {self.kept:,}  (stickers: {self.stickers:,})\n"
            f"    inserted       : {self.inserted:,}\n"
            f"    duplicates     : {self.duplicates:,}\n"
            f"    skipped empty  : {self.skipped_empty:,}\n"
            f"    skipped dirty  : {self.skipped_dirty:,}\n"
            f"    skipped junk   : {self.skipped_junk:,}\n"
            f"    skipped reply  : {self.skipped_reply:,}\n"
            f"    write failures : {self.failed:,}"
        )


def transform(doc, seen, stats, max_word_len, max_text_len, lean=True):
    """Return a cleaned target document or ``None`` when it must be skipped."""
    word = doc.get("word")
    text = doc.get("text")
    if word is None or text is None or not str(word).strip() or not str(text).strip():
        stats.skipped_empty += 1
        return None

    word = str(word)
    text = str(text)
    check = doc.get("check") or "none"

    # ``lean`` keeps only the fields the bot actually needs. This matters a lot
    # on the Atlas free tier (512 MB): the extra metadata roughly doubled the
    # size of a multi-million document collection.
    out = {"_id": doc.get("_id"), "check": check}
    if not lean:
        out.update(
            {
                "source": "migrated",
                "created_at": doc["_id"].generation_time.timestamp()
                if hasattr(doc.get("_id"), "generation_time")
                else time.time(),
                "updated_at": time.time(),
                "usage_count": 0,
            }
        )

    if check == "sticker":
        nword = norm_word(word, 60)
        if not nword or nword in JUNK_WORDS:
            stats.skipped_junk += 1
            return None
        key = hash(("s", nword, text.strip()[:60]))
        if key in seen:
            stats.duplicates += 1
            return None
        seen.add(key)
        out.update({"word": nword, "text": text.strip()})
        if not lean:
            out.update({"trigger_raw": word[:200], "tokens": []})
        stats.kept += 1
        stats.stickers += 1
        return out

    if cleaner.find_violations(word) or cleaner.find_violations(text):
        stats.skipped_dirty += 1
        return None

    nword = norm_word(word, max_word_len)
    if not nword or nword in JUNK_WORDS:
        stats.skipped_junk += 1
        return None

    clean_reply = cleaner.clean_text(text, max_length=max_text_len)
    if not clean_reply:
        stats.skipped_reply += 1
        return None

    # hash-based de-duplication keeps memory low on multi-million doc sets
    key = hash(("t", nword, clean_reply))
    if key in seen:
        stats.duplicates += 1
        return None
    seen.add(key)

    out.update({"word": nword, "text": clean_reply})
    if not lean:
        out.update(
            {
                "trigger_raw": word[:max_word_len],
                "tokens": sorted(set(re.findall(r"[a-z0-9\u0900-\u097F]{2,}", nword)))[:15],
            }
        )
    stats.kept += 1
    return out


def migrate(args):
    print("=" * 68)
    print("CHATBOT TRAINING DATA MIGRATION")
    print("=" * 68)
    print(f"  source : {args.source.split('@')[-1]}  [{args.source_db}.{args.source_coll}]")
    print(f"  target : {args.target.split('@')[-1]}  [{args.target_db}.{args.target_coll}]")
    print(f"  mode   : {'DRY RUN (no writes)' if args.dry_run else 'LIVE'}"
          f"{'  limit=' + str(args.limit) if args.limit else ''}"
          f"  {'lean' if args.lean else 'full'}")
    print()

    src_client = MongoClient(args.source, serverSelectionTimeoutMS=30000)
    src = src_client[args.source_db][args.source_coll]

    try:
        total_src = src.estimated_document_count()
        print(f"  source documents: {total_src:,}\n")
    except PyMongoError as exc:
        sys.exit(f"  cannot read source: {exc}")

    stats = Stats()
    seen = set()
    started = time.time()

    dst = None
    if not args.dry_run:
        dst_client = MongoClient(args.target, serverSelectionTimeoutMS=30000)
        dst = dst_client[args.target_db][args.target_coll]
        if args.drop:
            print("  dropping target collection ...")
            dst.drop()
            print("  done.\n")

    buffer = []
    BATCH = 1000

    def flush():
        if args.dry_run or not buffer:
            return
        t0 = time.time()
        size = len(buffer)
        try:
            result = dst.insert_many(buffer, ordered=False)
            stats.inserted += len(result.inserted_ids)
        except BulkWriteError as exc:
            inserted = exc.details.get("nInserted", 0)
            stats.inserted += inserted
            stats.duplicates += max(size - inserted, 0)
        except PyMongoError as exc:
            message = str(exc)
            stats.failed += size
            if "quota" in message.lower() or "8000" in message:
                buffer.clear()
                print("\n" + "!" * 68)
                print("  TARGET CLUSTER IS OUT OF STORAGE — migration stopped.")
                print("!" * 68)
                print("  The Atlas free tier only allows 512 MB. Free space by deleting")
                print("  unused data (e.g. the 'sample_mflix' database) or upgrade the tier.")
                print("  Run with --drop to start the target collection from scratch.")
                print("  This tool is idempotent (original _id is preserved), so simply")
                print("  re-running it resumes without creating duplicates.\n")
                raise SystemExit(2)
            print(f"  !! write failed: {message[:300]}", flush=True)
        finally:
            # ALWAYS empty the buffer, otherwise the next document would
            # re-insert the same batch over and over (thousands of duplicates
            # per second and a crawl-slow migration).
            buffer.clear()
            if args.verbose:
                dt = time.time() - t0
                print(f"    [batch {size}] write {dt:.2f}s -> "
                      f"{size / max(dt, 0.001):,.0f} docs/s", flush=True)

    cursor = src.find({}, {"word": 1, "text": 1, "check": 1}, batch_size=5000)
    if args.limit:
        cursor = cursor.limit(args.limit)

    try:
        for doc in cursor:
            stats.scanned += 1
            transformed = transform(
                doc, seen, stats, args.max_word_len, args.max_text_len, lean=args.lean
            )
            if transformed is not None:
                buffer.append(transformed)
                if len(buffer) >= BATCH:
                    flush()
            if stats.scanned % 100000 == 0:
                rate = stats.scanned / max(time.time() - started, 1)
                print(f"  ... {stats.scanned:,} scanned | {stats.kept:,} kept | "
                      f"{stats.duplicates:,} dupes | {rate:,.0f} docs/s", flush=True)
        flush()
    except KeyboardInterrupt:
        print("\n  interrupted — flushing buffer ...")
        flush()
    finally:
        src_client.close()

    elapsed = time.time() - started
    print("\n" + "=" * 68)
    print("RESULT")
    print("=" * 68)
    print(stats.report())
    print(f"    elapsed        : {elapsed:.1f}s")

    if args.dry_run:
        print("\n  DRY RUN complete — nothing was written.")
        return

    if not args.no_indexes:
        print("\n  creating indexes ...")
        try:
            dst.create_index("word")
            print("    ✅ word index")
        except PyMongoError as exc:
            print(f"    ⚠️ word index failed: {exc}")
        if not args.lean:
            try:
                dst.create_index("tokens")
                print("    ✅ tokens index")
            except PyMongoError as exc:
                print(f"    ⚠️ tokens index failed: {exc}")
        else:
            print("    (lean mode: skipping the tokens index to save space)")

    try:
        print(f"\n  target documents now: {dst.estimated_document_count():,}")
        info = dst.database.command("collstats", dst.name)
        print(f"  target collection size: {info.get('storageSize', 0) / 1024 / 1024:.1f} MB")
    except PyMongoError:
        pass

    print("\n✅ MIGRATION COMPLETE")


def parse_args():
    p = argparse.ArgumentParser(description="Migrate + clean chatbot training data between MongoDB clusters.")
    p.add_argument("--source", required=True, help="source MongoDB URI (read-only)")
    p.add_argument("--target", required=True, help="target MongoDB URI")
    p.add_argument("--source-db", default="Word")
    p.add_argument("--source-coll", default="WordDb")
    p.add_argument("--target-db", default="Word")
    p.add_argument("--target-coll", default="WordDb")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--drop", action="store_true")
    p.add_argument("--no-indexes", action="store_true")
    p.add_argument(
        "--lean",
        dest="lean",
        action="store_true",
        default=True,
        help="store only _id/word/text/check — smallest footprint (default)",
    )
    p.add_argument(
        "--full",
        dest="lean",
        action="store_false",
        help="also store tokens/trigger_raw/timestamps (needs more space)",
    )
    p.add_argument("--max-word-len", type=int, default=200)
    p.add_argument("--max-text-len", type=int, default=300)
    p.add_argument("--verbose", action="store_true", help="print per-batch write timings")
    return p.parse_args()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    migrate(parse_args())
