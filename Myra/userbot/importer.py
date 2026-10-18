"""
Add userbots by uploading Telegram **session files**.

    • send a single ``*.session`` file → that one account is added
    • send a ``*.zip``               → every ``*.session`` inside is added

Both **Pyrogram** and **Telethon** ``.session`` files are supported. The format
is detected from the SQLite schema of the file:

* Pyrogram files are read by Pyrogram itself, then ``export_session_string()``
  turns them into a session string.
* Telethon files only store the ``dc_id`` and the raw ``auth_key``; those are
  packed into a Pyrogram session string, the account's user id is looked up with
  a single ``get_me()`` call and the final string is stored.

In both cases only the session string is kept (in ``SessionDB.sessions``), so
nothing stays on the host disk and every account is restored automatically after
a restart. Exactly what ``complete_login()`` does for the OTP flow.
"""

import asyncio
import base64
import os
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
    save_session,
)

MAX_SESSIONS_PER_ZIP = 200
SLEEP_BETWEEN = 1.5       # be gentle with Telegram when importing many accounts
SESSION_EXT = ".session"

# Pyrogram's current session-string layout (see pyrogram.storage.Storage)
PYROGRAM_SESSION_STRING_FORMAT = ">BI?256sQ?"
SESSION_STRING_VERSION = 1
AUTH_KEY_LEN = 256


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


def read_telethon_auth(path: str):
    """
    ``(dc_id, auth_key)`` from a Telethon ``.session`` file, or ``None``.

    Telethon keeps one row per data-centre; the primary one is the row with the
    newest ``date``.
    """
    try:
        conn = _open_sqlite_readonly(path)
    except Exception:  # noqa: BLE001
        return None
    try:
        cols = _table_columns(conn, "sessions")
        order = "date DESC" if "date" in cols else "dc_id ASC"
        row = conn.execute(
            f"SELECT dc_id, auth_key FROM sessions ORDER BY {order} LIMIT 1"
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"read_telethon_auth({path}) failed: {exc}")
        row = None
    finally:
        conn.close()

    if not row or row[1] is None:
        return None
    try:
        dc_id = int(row[0])
    except Exception:  # noqa: BLE001
        return None
    auth_key = bytes(row[1])
    if dc_id <= 0 or len(auth_key) < 8:
        return None
    return dc_id, auth_key


def build_session_string(
    dc_id: int,
    auth_key: bytes,
    api_id=None,
    user_id: int = 0,
    test_mode: bool = False,
    is_bot: bool = False,
) -> str:
    """Pack a raw auth key into a Pyrogram session string."""
    key = bytes(auth_key)
    if len(key) < AUTH_KEY_LEN:
        key = key.ljust(AUTH_KEY_LEN, b"\x00")
    elif len(key) > AUTH_KEY_LEN:
        key = key[:AUTH_KEY_LEN]

    packed = struct.pack(
        PYROGRAM_SESSION_STRING_FORMAT,
        SESSION_STRING_VERSION,
        int(dc_id),
        bool(test_mode),
        key,
        int(user_id),
        bool(is_bot),
    )
    return base64.urlsafe_b64encode(packed).decode().rstrip("=")


async def session_string_from_telethon_file(path: str, api_id=None, api_hash=None):
    """
    Convert a Telethon ``.session`` into a Pyrogram session string.

    The file only holds the ``dc_id`` and the raw ``auth_key``, so a provisional
    string (user id ``0``) is used to call ``get_me()`` and learn the account id;
    the final string carries the real id.
    """
    info = read_telethon_auth(path)
    if not info:
        return None, "Telethon session padhi nahi ja saki (corrupt ya khaali file)"

    dc_id, auth_key = info
    api_id = api_id or config.API_ID
    api_hash = api_hash or config.API_HASH

    provisional = build_session_string(dc_id, auth_key, api_id, user_id=0)
    client = Client(
        name=_probe_name(),
        api_id=api_id,
        api_hash=api_hash,
        session_string=provisional,
        in_memory=True,
        no_updates=True,
    )

    me, error = None, ""
    try:
        await client.connect()
        me = await client.get_me()
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    if error:
        return None, error
    if me is None:
        return None, "session expired (ya logout ho chuka) — dobara login karo"

    return build_session_string(
        dc_id, auth_key, api_id,
        user_id=me.id,
        is_bot=bool(getattr(me, "is_bot", False)),
    ), ""


# ── .session file → session string ─────────────────────────────────────────
async def session_string_from_file(path: str, api_id=None, api_hash=None):
    """
    Convert any supported ``.session`` file into a Pyrogram session string.

    Returns ``(session_string, "")`` on success, or ``(None, reason)`` when the
    file is corrupt / expired / not logged in.
    """
    if detect_session_format(path) == "telethon":
        return await session_string_from_telethon_file(path, api_id, api_hash)

    if path.lower().endswith(SESSION_EXT):
        path = path[: -len(SESSION_EXT)]

    client = Client(
        name=path,
        api_id=api_id or config.API_ID,
        api_hash=api_hash or config.API_HASH,
        no_updates=True,
    )
    try:
        authorized = await client.connect()
        if not authorized:
            return None, "session not logged in (no user id stored)"
        session_string = await client.export_session_string()
        if not session_string:
            return None, "could not export a session string"
        return session_string, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


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
        await client.start()
        me = await client.get_me()
    except Exception as exc:  # noqa: BLE001
        try:
            await client.stop()
        except Exception:  # noqa: BLE001
            pass
        LOGGER.warning(f"❌ session string could not be started: {type(exc).__name__}: {exc}")
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

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
        "autochat": config.USERBOT_AUTOCHAT_DEFAULT,
        "reply_delay": None,
        "speed_mode": "fast",
        "group_mode": None,
        "reply_chance": None,
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
        "autochat": config.USERBOT_AUTOCHAT_DEFAULT,
        "reply_delay": None,
        "speed_mode": "fast",
        "group_mode": None,
        "reply_chance": None,
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


async def import_zip(zip_path: str, added_by: int, progress=None) -> dict:
    """
    Import every ``*.session`` inside a zip.

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

        for index, path in enumerate(sorted(files), start=1):
            file_name = safe_basename(path)
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
    "build_session_string",
    "session_string_from_file",
    "session_string_from_telethon_file",
    "add_from_session_string",
    "add_from_session_file",
    "extract_sessions",
    "import_zip",
]
