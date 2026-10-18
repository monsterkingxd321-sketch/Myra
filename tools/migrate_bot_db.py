#!/usr/bin/env python3
"""
Move the bot's own data to the second MongoDB cluster.

Since the two-cluster split the databases are:

    MONGO_URL      →  Word            (the chat/training brain — Word.WordDb)
    BOT_MONGO_URL  →  Anonymous       (users + chats)
                      UserbotDB       (settings, ai_keys, bot_admins,
                                       joinreq_chats, userbot `accounts`)
                      SessionDB       (userbot `sessions`)
                      BotDB           (cloned bots)
                      MYRADb          (chatbot on/off per chat)

Only the *bot* databases are copied here — the training database is left alone
(use ``tools/migrate_training_db.py`` for that one). The copy is idempotent:
every document keeps its original ``_id`` and is upserted, so re-running after
an interruption never creates duplicates. Indexes are recreated too.

Usage
-----
    python tools/migrate_bot_db.py --source "$OLD_MONGO_URL" --target "$BOT_MONGO_URL"

Add ``--dry-run`` first to see what would be copied. ``--drop`` empties each
target collection before copying (use it to start clean).
"""

import argparse
import sys
import time

try:
    from pymongo import MongoClient, ReplaceOne
    from pymongo.errors import PyMongoError
except ImportError:  # pragma: no cover
    print("pymongo is required:  pip install pymongo")
    raise SystemExit(1)

# The bot databases, in copy order. "Word" is deliberately NOT here.
DEFAULT_DBS = ["Anonymous", "UserbotDB", "SessionDB", "BotDB", "MYRADb"]

BATCH = 500


def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def copy_collection(src_coll, dst_coll, drop: bool, dry_run: bool, verbose: bool) -> int:
    """Upsert every document of ``src_coll`` into ``dst_coll``. Returns the count."""
    if drop and not dry_run:
        dst_coll.delete_many({})

    total = src_coll.estimated_document_count()

    copied = 0
    buffer = []
    started = time.time()

    def flush():
        nonlocal copied
        if not buffer or dry_run:
            buffer.clear()
            return
        ops = [ReplaceOne({"_id": doc["_id"]}, doc, upsert=True) for doc in buffer]
        try:
            dst_coll.bulk_write(ops, ordered=False)
        except PyMongoError as exc:
            print(f"      !! write failed: {str(exc)[:200]}", flush=True)
        copied += len(buffer)
        if verbose:
            dt = time.time() - started
            print(f"      {human(copied)}/{human(total)} "
                  f"({copied / max(dt, 0.001):,.0f} docs/s)", flush=True)
        buffer.clear()

    cursor = src_coll.find({}, no_cursor_timeout=False)
    try:
        for doc in cursor:
            buffer.append(doc)
            if len(buffer) >= BATCH:
                flush()
        flush()
    except KeyboardInterrupt:
        print("\n      interrupted — flushing ...")
        flush()
        raise

    if not dry_run:
        # Recreate the source indexes (ignore ones MongoDB refuses to rebuild).
        for name, spec in (src_coll.index_information() or {}).items():
            if name == "_id_":
                continue
            try:
                keys = [(field, direction) for field, direction in spec.get("key", [])]
                dst_coll.create_index(keys, name=name, unique=bool(spec.get("unique")))
            except PyMongoError as exc:
                print(f"      ⚠️ index {name} skipped: {str(exc)[:120]}")

    return copied


def main() -> int:
    p = argparse.ArgumentParser(
        description="Copy the bot's databases (NOT the training DB) to another MongoDB cluster.",
    )
    p.add_argument("--source", required=True, help="source MongoDB URI (read-only)")
    p.add_argument("--target", required=True, help="target MongoDB URI (BOT_MONGO_URL)")
    p.add_argument(
        "--dbs",
        default=",".join(DEFAULT_DBS),
        help=f"comma separated databases (default: {','.join(DEFAULT_DBS)})",
    )
    p.add_argument("--dry-run", action="store_true", help="only report what would be copied")
    p.add_argument("--drop", action="store_true", help="empty each target collection first")
    p.add_argument("--verbose", action="store_true", help="per-batch progress")
    args = p.parse_args()

    if args.source.strip() == args.target.strip():
        print("!! source and target are the same cluster — nothing to do.")
        return 1

    wanted = [d.strip() for d in args.dbs.split(",") if d.strip()]

    print("=" * 68)
    print("BOT DATABASE MIGRATION")
    print("=" * 68)
    if args.dry_run:
        print("  DRY RUN — nothing will be written.\n")

    src = MongoClient(args.source, serverSelectionTimeoutMS=20000)
    dst = MongoClient(args.target, serverSelectionTimeoutMS=20000)

    try:
        src.list_database_names()
    except PyMongoError as exc:
        print(f"!! cannot reach the source cluster: {exc}")
        return 2
    try:
        dst.list_database_names()
    except PyMongoError as exc:
        print(f"!! cannot reach the target cluster: {exc}")
        return 2

    present = set(src.list_database_names())
    plan = [db for db in wanted if db in present]
    missing = [db for db in wanted if db not in present]
    if missing:
        print(f"  (not on the source cluster, skipped: {', '.join(missing)})")

    if not plan:
        print("!! nothing to copy.")
        return 1

    grand_total = 0
    started = time.time()

    for db_name in plan:
        src_db = src[db_name]
        dst_db = dst[db_name]
        colls = sorted(src_db.list_collection_names())
        print(f"\n── {db_name}  ({len(colls)} collection(s)) ──")
        for coll_name in colls:
            src_coll = src_db[coll_name]
            n = src_coll.estimated_document_count()
            print(f"   • {coll_name:<28} {human(n):>12} docs", flush=True)
            copied = copy_collection(
                src_coll,
                dst_db[coll_name],
                drop=args.drop,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
            grand_total += copied if not args.dry_run else n

    print("\n" + "=" * 68)
    if args.dry_run:
        print(f"DRY RUN complete — {human(grand_total)} documents would be copied.")
    else:
        print(f"✅ MIGRATION COMPLETE — {human(grand_total)} documents copied "
              f"in {time.time() - started:.1f}s")
        print("\nNext steps:")
        print("  1. set BOT_MONGO_URL in .env to the target cluster")
        print("  2. restart the bot and check the log")
        print("  3. verify with:  /admin → the panels should show the same data")
    print("=" * 68)

    src.close()
    dst.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted.")
        sys.exit(130)
