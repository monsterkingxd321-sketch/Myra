"""
Add userbots by uploading Telegram **session files**.

    • send a single ``*.session`` file → that one account is added
    • send a ``*.zip``               → every ``*.session`` inside is added

Both **Pyrogram** and **Telethon** ``.session`` files are supported, plus a plain
text **session string** saved as ``*.session`` (what session-generator bots hand
out). The format is detected from the file itself:

* Pyrogram files are read straight out of SQLite (``dc_id``/``api_id``/
  ``auth_key``/``user_id``). Pyrogram is **never** allowed to open the file,
  because its own migrations run ``ALTER TABLE sessions ADD server_address`` on
  any file whose ``version`` row looks old, which fails with
  ``OperationalError: duplicate column name: server_address``.
* Telethon files only store the ``dc_id`` and the raw ``auth_key``; every DC row
  is tried, the account's user id is looked up with one ``get_me()`` call and the
  final string is stored.
* Text files are decoded as base64; the current ``>BI?256sQ?`` layout is used
  as-is and the old ``>B?256sI?`` layout is upgraded with the configured api_id.

In every case only the session string is kept (in ``SessionDB.sessions``), so
nothing stays on the host disk and every account is restored automatically after
a restart. Exactly what ``complete_login()`` does for the OTP flow.
"""

import asyncio
import base64
import hashlib
import os
import re
import shutil
import sqlite3
import struct
import tempfile
import time
import zipfile

from pyrogram import Client

from Myra import config, LOGGER
from Myra.database.scope import current_scope
from Myra.userbot.storage import (
    accounts as ub_accounts,
    add_account,
    default_account_settings,
    save_session,
)

MAX_SESSIONS_PER_ZIP = 200
SLEEP_BETWEEN = 1.5       # be gentle with Telegram when importing many accounts
SESSION_EXT = ".session"

# Pyrogram's current session-string layout (see pyrogram.storage.Storage):
#
#     > B   I   ?   256s        Q   ?
#       dc  api  test  auth_key  uid  is_bot
#
# There is NO "version" field. Writing one here (the old code packed a constant
# 1 first) shifted every field by one: the client then connected to DC 1 with a
# bogus api_id, Telegram silently ignored the auth key and Pyrogram ended with
# `TimeoutError: Failed to invoke "users.GetFullUser" after 10 retries`.
PYROGRAM_SESSION_STRING_FORMAT = ">BI?256sQ?"
AUTH_KEY_LEN = 256
SESSION_STRING_BYTES = 271          # len(struct.pack(SESSION_STRING_FORMAT, ...))
# Pyrogram < 2.0 (and Telethon's old StringSession) used `>B?256sI?` with no
# api_id, so text files in that shape are upgraded too.
OLD_SESSION_STRING_FORMAT = ">B?256sI?"
OLD_SESSION_STRING_BYTES = 263
PROBE_TIMEOUT = 30                 # seconds per connect+get_me attempt
LOGIN_TIMEOUT = 60                 # seconds for the real userbot start

# Columns every Pyrogram session schema has (v1 … v7). Reading these ourselves
# avoids Pyrogram's own `ALTER TABLE` migrations — see read_pyrogram_auth().
PYROGRAM_AUTH_COLUMNS = ("dc_id", "api_id", "test_mode", "auth_key", "user_id", "is_bot")


# ── helpers ────────────────────────────────────────────────────────────────
def safe_basename(name: str) -> str:
    """Take only the file name and strip any path components (zip-slip guard)."""
    return os.path.basename(str(name or "").replace("\\", "/")).strip()


def looks_like_session(name: str) -> bool:
    low = safe_basename(name).lower()
    return low.endswith(SESSION_EXT) and not low.endswith(".session-journal")


def looks_like_zip(name: str) -> bool:
    return safe_basename(name).lower().endswith(".zip")


def _probe_name() -> str:
    return f"ubprobe_{int(time.time() * 1000)}_{os.getpid()}"


# ── format detection (Pyrogram vs Telethon) ────────────────────────────────
def _open_sqlite_readonly(path: str):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _table_columns(conn, table: str) -> set:
    try:
        return {str(row[1]).lower() for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:  # noqa: BLE001
        return set()


def detect_session_format(path: str) -> str:
    """
    Identify a session file by its SQLite schema.

    Returns ``"pyrogram"``, ``"telethon"`` or ``"unknown"``.

    * Pyrogram → ``dc_id, api_id, test_mode, auth_key, date, user_id, is_bot``
    * Telethon → ``dc_id, server_address, port, auth_key, date, takeout_id``
    """
    if not os.path.exists(path):
        return "unknown"
    try:
        conn = _open_sqlite_readonly(path)
    except Exception:  # noqa: BLE001
        return "unknown"
    try:
        cols = _table_columns(conn, "sessions")
    finally:
        conn.close()

    if not cols or "auth_key" not in cols:
        return "unknown"
    if "api_id" in cols or "user_id" in cols or "is_bot" in cols:
        return "pyrogram"
    if "server_address" in cols or "port" in cols or "takeout_id" in cols:
        return "telethon"
    return "unknown"


def read_telethon_auth_all(path: str) -> list:
    """
    Every ``(dc_id, auth_key)`` pair in a Telethon ``.session`` file.

    Telethon stores one row per data-centre. Which row is "the" account DC depends
    on the Telethon version (newer files have no ``date`` column at all), so the
    importer tries them in order instead of guessing one.
    """
    try:
        conn = _open_sqlite_readonly(path)
    except Exception:  # noqa: BLE001
        return []
    try:
        cols = _table_columns(conn, "sessions")
        order = "date DESC" if "date" in cols else "dc_id ASC"
        rows = conn.execute(
            f"SELECT dc_id, auth_key FROM sessions ORDER BY {order}"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"read_telethon_auth_all({path}) failed: {exc}")
        rows = []
    finally:
        conn.close()

    found, seen = [], set()
    for row in rows:
        if not row or row[1] is None:
            continue
        try:
            dc_id = int(row[0])
        except Exception:  # noqa: BLE001
            continue
        auth_key = bytes(row[1])
        if dc_id <= 0 or len(auth_key) < 8 or (dc_id, auth_key) in seen:
            continue
        seen.add((dc_id, auth_key))
        found.append((dc_id, auth_key))
    return found


def read_telethon_auth(path: str):
    """``(dc_id, auth_key)`` for the primary Telethon row, or ``None``."""
    rows = read_telethon_auth_all(path)
    return rows[0] if rows else None


def read_pyrogram_auth(path: str):
    """
    Read a Pyrogram ``.session`` database directly, without Pyrogram's help.

    Why not just ``Client(name=path)``? Opening a foreign file through Pyrogram
    makes it run its own schema migrations. A Telethon file (or any file with a
    ``version`` row below Pyrogram's) then hits:

        OperationalError: duplicate column name: server_address

    because Pyrogram blindly ``ALTER TABLE sessions ADD server_address`` on a
    table that already has it. Every Pyrogram schema version (v1 … v7) does have
    the columns below, so reading them here is both safe and version-proof — and
    it removes one full client start, so imports get faster too.

    Returns a dict, or ``None`` when the file is not a usable Pyrogram session.
    """
    try:
        conn = _open_sqlite_readonly(path)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"read_pyrogram_auth({path}) could not open: {exc}")
        return None
    try:
        cols = _table_columns(conn, "sessions")
        if "auth_key" not in cols or "dc_id" not in cols:
            return None
        wanted = [name for name in PYROGRAM_AUTH_COLUMNS if name in cols]
        row = conn.execute(
            f"SELECT {', '.join(wanted)} FROM sessions LIMIT 1"
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"read_pyrogram_auth({path}) failed: {exc}")
        return None
    finally:
        conn.close()

    if not row:
        return None
    data = dict(zip(wanted, row))
    auth_key = data.get("auth_key")
    if auth_key is None:
        return None
    auth_key = bytes(auth_key)
    try:
        dc_id = int(data.get("dc_id") or 0)
    except Exception:  # noqa: BLE001
        return None
    if dc_id <= 0 or not auth_key:
        return None
    return {
        "dc_id": dc_id,
        "auth_key": auth_key,
        "api_id": int(data.get("api_id") or 0),
        "test_mode": bool(data.get("test_mode")),
        "user_id": int(data.get("user_id") or 0),
        "is_bot": bool(data.get("is_bot")),
    }


# ── plain-text session strings ─────────────────────────────────────────────
# Session-generator bots hand out a *string*, and users very often save it as
# "name.session". That file is not SQLite, so it used to fail with
# `DatabaseError: file is not a database`. Accept it instead.
_TEXT_SESSION_RE = re.compile(r"^[A-Za-z0-9_\-+/]+=*$")


def _decode_session_string(text: str):
    """Base64-decode a candidate session string, or ``None``."""
    cleaned = (text or "").strip().strip('"\'').strip()
    if len(cleaned) < OLD_SESSION_STRING_BYTES - 1 or not _TEXT_SESSION_RE.match(cleaned):
        return None
    try:
        raw = base64.urlsafe_b64decode(cleaned + "=" * (-len(cleaned) % 4))
    except Exception:  # noqa: BLE001
        try:
            raw = base64.b64decode(cleaned + "=" * (-len(cleaned) % 4))
        except Exception:  # noqa: BLE001
            return None
    return raw


def read_text_session(path: str):
    """
    Turn a text ``.session`` file (a session *string*) into a Pyrogram string.

    Returns ``None`` when the file is not a session string.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read(8192)
    except OSError:
        return None
    if b"\x00" in raw:          # binary → it is an SQLite session, not text
        return None

    text = raw.decode("utf-8", "ignore").strip()
    if not text:
        return None
    candidate = next((line.strip() for line in text.splitlines() if line.strip()), "")

    payload = _decode_session_string(candidate)
    if payload is None:
        return None

    if len(payload) == SESSION_STRING_BYTES:
        # Current Pyrogram/Telethon layout, api_id included.
        return candidate.rstrip("=")

    if len(payload) == OLD_SESSION_STRING_BYTES:
        # Old Pyrogram layout without api_id — upgrade it.
        try:
            dc_id, test_mode, auth_key, user_id, is_bot = struct.unpack(
                OLD_SESSION_STRING_FORMAT, payload
            )
        except Exception:  # noqa: BLE001
            return None
        return build_session_string(
            dc_id, auth_key, config.API_ID,
            user_id=user_id, test_mode=bool(test_mode), is_bot=bool(is_bot),
        )

    return None


def build_session_string(
    dc_id: int,
    auth_key: bytes,
    api_id=None,
    user_id: int = 0,
    test_mode: bool = False,
    is_bot: bool = False,
) -> str:
    """
    Pack a raw auth key into a Pyrogram session string.

    Field order follows ``>BI?256sQ?`` exactly:
    ``dc_id, api_id, test_mode, auth_key, user_id, is_bot``.
    """
    key = bytes(auth_key)
    if len(key) < AUTH_KEY_LEN:
        key = key.ljust(AUTH_KEY_LEN, b"\x00")
    elif len(key) > AUTH_KEY_LEN:
        key = key[:AUTH_KEY_LEN]

    packed = struct.pack(
        PYROGRAM_SESSION_STRING_FORMAT,
        int(dc_id),
        int(api_id or config.API_ID),
        bool(test_mode),
        key,
        int(user_id),
        bool(is_bot),
    )
    return base64.urlsafe_b64encode(packed).decode().rstrip("=")


async def _probe_session(dc_id: int, auth_key: bytes, api_id, api_hash):
    """
    Connect with a provisional string (user id ``0``) and return ``(me, error)``.

    ``asyncio.wait_for`` is not decoration: without it a dead session sits inside
    Pyrogram's invoke loop for ~10 retries × 10 s and the whole upload looks
    frozen ("bot bhut slow hai").
    """
    client = Client(
        name=_probe_name(),
        api_id=api_id,
        api_hash=api_hash,
        session_string=build_session_string(dc_id, auth_key, api_id, user_id=0),
        in_memory=True,
        no_updates=True,
    )

    me, error = None, ""
    try:
        await asyncio.wait_for(client.connect(), PROBE_TIMEOUT)
        me = await asyncio.wait_for(client.get_me(), PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        error = f"Telegram ne {PROBE_TIMEOUT}s me jawab nahi diya (dead session ya galat DC)"
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), 10)
        except Exception:  # noqa: BLE001
            pass
    return me, error


async def session_string_from_telethon_file(path: str, api_id=None, api_hash=None):
    """
    Convert a Telethon ``.session`` into a Pyrogram session string.

    Telethon keeps one row per data-centre and only the row's ``dc_id`` +
    ``auth_key`` are stored, so a provisional string is used to call ``get_me()``.
    Rows are tried in order: picking a single "newest" row used to time out when
    the file belonged to another DC.
    """
    rows = read_telethon_auth_all(path)
    if not rows:
        return None, "Telethon session khaali/corrupt hai (auth key nahi mili)"

    api_id = api_id or config.API_ID
    api_hash = api_hash or config.API_HASH

    errors = []
    for dc_id, auth_key in rows:
        me, error = await _probe_session(dc_id, auth_key, api_id, api_hash)
        if me is not None:
            return build_session_string(
                dc_id, auth_key, api_id,
                user_id=me.id,
                is_bot=bool(getattr(me, "is_bot", False)),
            ), ""
        errors.append(f"DC{dc_id}: {error}")

    return None, " | ".join(errors)[:300]


# ── .session file → session string ─────────────────────────────────────────
async def session_string_from_file(path: str, api_id=None, api_hash=None):
    """
    Convert any supported ``.session`` file into a Pyrogram session string.

    Returns ``(session_string, "")`` on success, or ``(None, reason)`` when the
    file is unreadable / expired / not logged in.

    Three shapes are accepted:

    * Pyrogram SQLite (read directly — Pyrogram's own migrations are never run,
      so a foreign file can no longer fail with "duplicate column name")
    * Telethon SQLite (auth key repacked, account id looked up)
    * a plain-text session string saved as ``*.session``
    """
    fmt = detect_session_format(path)

    if fmt == "pyrogram":
        info = read_pyrogram_auth(path)
        if not info:
            return None, "Pyrogram session file padhi nahi ja saki (corrupt ya khaali)"
        if not info["auth_key"].strip(b"\x00"):
            return None, "session me login nahi hai (auth key khaali) — dobara login karo"
        return build_session_string(
            info["dc_id"],
            info["auth_key"],
            info["api_id"] or api_id or config.API_ID,
            user_id=info["user_id"],
            test_mode=info["test_mode"],
            is_bot=info["is_bot"],
        ), ""

    if fmt == "telethon":
        return await session_string_from_telethon_file(path, api_id, api_hash)

    text = read_text_session(path)
    if text:
        return text, ""

    return None, (
        "file samajh nahi aayi: na Pyrogram/Telethon `.session` database hai, "
        "na session string"
    )


# ── session string → live userbot ──────────────────────────────────────────
async def add_from_session_string(
    session_string: str,
    added_by: int,
    phone: str = "",
    source: str = "session_file",
) -> dict:
    """
    Build a live client from a session string, persist it and register handlers.

    Result: ``{"ok": bool, "account_id", "name", "username", "reason"}``.
    """
    from Myra.userbot.handlers import register_userbot_handlers

    client = Client(
        name=_probe_name(),
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_string,
        in_memory=True,
    )

    try:
        # Fast-fail instead of letting Pyrogram retry for 100+ seconds: a dead
        # session used to make the whole ZIP look frozen.
        await asyncio.wait_for(client.start(), LOGIN_TIMEOUT)
        me = await asyncio.wait_for(client.get_me(), PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            await asyncio.wait_for(client.stop(), 10)
        except Exception:  # noqa: BLE001
            pass
        reason = (
            f"Telegram ne {LOGIN_TIMEOUT}s me jawab nahi diya — session dead/expired "
            "ya galat DC ka hai, naya session banao"
        )
        LOGGER.warning(f"❌ session string timed out: {reason}")
        return {"ok": False, "reason": reason}
    except Exception as exc:  # noqa: BLE001
        try:
            await asyncio.wait_for(client.stop(), 10)
        except Exception:  # noqa: BLE001
            pass
        try:
            from Myra.userbot.auth import explain_login_error

            reason = " ".join(explain_login_error(exc).split())
        except Exception:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
        LOGGER.warning(f"❌ session string could not be started: {type(exc).__name__}: {exc}")
        return {"ok": False, "reason": reason}

    account_id = f"ub_{me.id}"
    full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Telegram User"
    phone = phone or (f"+{me.phone_number}" if getattr(me, "phone_number", None) else "")

    existing = ub_accounts.get(account_id)
    if existing and existing.get("status") == "CONNECTED":
        try:
            await client.stop()
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": False,
            "skipped": True,
            "account_id": account_id,
            "name": full_name,
            "username": me.username or "",
            "reason": "already added & connected",
        }

    if existing:  # refresh a stale / errored entry
        old = existing.get("client")
        if old is not None:
            try:
                await old.stop()
            except Exception:  # noqa: BLE001
                pass
        ub_accounts.pop(account_id, None)

    live = {
        "client": client,
        "telegram_id": me.id,
        "username": me.username or "",
        "name": full_name,
        "phone": phone,
        "added_by": int(added_by),
        "scope": current_scope(),
        **default_account_settings(),
        "status": "CONNECTED",
    }

    stored = await add_account({
        "account_id": account_id,
        "telegram_id": me.id,
        "username": me.username or "",
        "name": full_name,
        "phone": phone,
        "added_by": int(added_by),
        "enabled": True,
        **default_account_settings(),
        "status": "CONNECTED",
        "source": source,
        "created_at": time.time(),
    })
    if stored is None:
        # The account already belongs to another bot (main bot or another clone)
        # — never run the same Telegram account from two bots.
        try:
            await client.stop()
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": False,
            "account_id": account_id,
            "name": full_name,
            "reason": "already added to another bot",
        }
    await save_session(account_id, session_string)

    ub_accounts[account_id] = live
    await register_userbot_handlers(client, account_id)

    LOGGER.info(f"✅ Imported userbot {full_name} ({account_id}) from {source}")
    return {
        "ok": True,
        "account_id": account_id,
        "name": full_name,
        "username": me.username or "",
        "telegram_id": me.id,
    }


async def add_from_session_file(path: str, added_by: int, phone: str = "") -> dict:
    """Import one ``.session`` file."""
    session_string, reason = await session_string_from_file(path)
    if not session_string:
        LOGGER.warning(f"❌ session file {safe_basename(path)} unusable: {reason}")
        return {"ok": False, "reason": reason}
    return await add_from_session_string(session_string, added_by, phone=phone)


# ── .zip → many userbots ───────────────────────────────────────────────────
def extract_sessions(zip_path: str, dest: str):
    """
    Extract every ``*.session`` member of a zip into ``dest``.

    Returns ``(paths, over_limit_names)``. Paths are flattened to file names so
    a hostile archive can never escape ``dest``.
    """
    extracted, over_limit, used = [], [], set()

    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            if member.endswith("/"):
                continue
            base = safe_basename(member)
            if not looks_like_session(base):
                continue
            if base.lower() in used:
                base = f"{len(used)}_{base}"
            used.add(base.lower())

            if len(extracted) >= MAX_SESSIONS_PER_ZIP:
                over_limit.append(base)
                continue

            target = os.path.join(dest, base)
            with archive.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(target)

    return extracted, over_limit


def _file_digest(path: str) -> str:
    """SHA-256 of a file, used to spot the same session packed twice."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def import_zip(zip_path: str, added_by: int, progress=None) -> dict:
    """
    Import every ``*.session`` inside a zip.

    Identical files are only tried once: zips from session generators very often
    carry the same account in several folders (that is exactly what made one
    upload attempt three logins for one account).

    ``progress(index, total, file_name)`` is awaited before each account when
    given. Returns ``{"imported": [...], "failed": [...], "skipped": [...],
    "missing": int}``.
    """
    report = {"imported": [], "failed": [], "skipped": [], "missing": 0}
    tmpdir = tempfile.mkdtemp(prefix="ubzip_")
    try:
        try:
            files, over_limit = extract_sessions(zip_path, tmpdir)
        except zipfile.BadZipFile as exc:
            report["failed"].append(
                {"name": safe_basename(zip_path), "reason": f"invalid zip ({exc})"}
            )
            return report
        except Exception as exc:  # noqa: BLE001
            report["failed"].append(
                {"name": safe_basename(zip_path), "reason": f"{type(exc).__name__}: {exc}"}
            )
            return report

        if not files:
            report["missing"] = 1
            return report

        seen_digests: dict = {}
        for index, path in enumerate(sorted(files), start=1):
            file_name = safe_basename(path)

            # Same bytes = same Telegram account: never log in twice for it.
            try:
                digest = _file_digest(path)
            except OSError:
                digest = ""
            if digest and digest in seen_digests:
                report["skipped"].append({
                    "name": file_name,
                    "reason": f"duplicate of {seen_digests[digest]}",
                })
                continue
            if digest:
                seen_digests[digest] = file_name

            if progress is not None:
                try:
                    await progress(index, len(files), file_name)
                except Exception:  # noqa: BLE001
                    pass

            try:
                result = await add_from_session_file(path, added_by)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

            if result.get("ok"):
                report["imported"].append({
                    "name": result.get("name") or file_name,
                    "username": result.get("username", ""),
                    "file": file_name,
                })
            elif result.get("skipped"):
                report["skipped"].append({
                    "name": result.get("name") or file_name,
                    "reason": result.get("reason", "skipped"),
                })
            else:
                report["failed"].append({
                    "name": file_name,
                    "reason": result.get("reason", "unknown error"),
                })

            await asyncio.sleep(SLEEP_BETWEEN)

        for name in over_limit:
            report["skipped"].append({"name": name, "reason": "over the per-zip limit"})

        return report
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


__all__ = [
    "MAX_SESSIONS_PER_ZIP",
    "safe_basename",
    "looks_like_session",
    "looks_like_zip",
    "detect_session_format",
    "read_telethon_auth",
    "read_telethon_auth_all",
    "read_pyrogram_auth",
    "read_text_session",
    "build_session_string",
    "session_string_from_file",
    "session_string_from_telethon_file",
    "add_from_session_string",
    "add_from_session_file",
    "extract_sessions",
    "import_zip",
]
