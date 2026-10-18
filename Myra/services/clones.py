"""
Cloned bots.

Any user can host their own bot from the main bot with ``/clone <bot_token>``.
A clone runs **inside the same process** as the main bot (cheap, no extra
interpreter) but with its own Telegram client and its own bot token, so it can
chat in its own groups and DMs.

Deliberately **shared** between the main bot and every clone:

    * the trained Hinglish database (``Word.WordDb``)
    * the Gemini and OpenAI API keys
    * **the whole admin surface**: one owner (``config.OWNER_ID``), one admin
      list, one set of userbots/groups/settings — see :mod:`Myra.database.scope`

so every clone that chats also makes the shared brain smarter — more reach,
better database. Cloning gives reach, **not control**: ``/admin`` belongs to the
main owner and the admins the owner added, in every bot, and always shows the
same shared data.

How the handlers work
---------------------
The clone's dispatcher runs the *same* handlers as the main bot. They are copied
once and every callback is wrapped so it executes with the clone's scope in a
ContextVar (:mod:`Myra.database.scope`). That keeps a clone's own chat serving
isolated, while the admin-surface handlers opt into the global view with
``@global_scope``.
"""

import functools
import re
import time
from collections import OrderedDict
from uuid import uuid4

from pyrogram import Client
from pyrogram.enums import ParseMode

from Myra import config, LOGGER
from Myra.database import bots as botdb
from Myra.database.scope import MAIN_SCOPE, clone_scope, reset_scope, set_scope

TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_\-]{30,}$")

# scope -> live Client (main bot is not stored here, see bot_client_for)
CLIENTS: dict = {}

# scope -> asyncio.Lock, so a clone is never started twice in parallel
_LOCKS: dict = {}


def _lock_for(scope: str):
    import asyncio

    lock = _LOCKS.get(scope)
    if lock is None:
        lock = _LOCKS[scope] = asyncio.Lock()
    return lock


def _full_name(me) -> str:
    name = f"{me.first_name or ''} {(me.last_name or '')}".strip()
    return name or (me.username or str(me.id))


def attach_bot_attrs(client, me):
    """Give a clone client the same convenience attributes as ``Myra``."""
    client.id = me.id
    client.name = _full_name(me)
    client.username = me.username
    client.mention = me.mention
    return client


def bot_client_for(scope: str = None):
    """The bot client that serves a scope (the main bot for ``main``)."""
    from Myra.database.scope import current_scope

    scope = scope or current_scope()
    if scope == MAIN_SCOPE:
        from Myra import Myra as main_client

        return main_client
    return CLIENTS.get(scope)


def running_scopes() -> list:
    """Every scope that currently has a live bot client (main included)."""
    scopes = [MAIN_SCOPE]
    scopes += [scope for scope, client in CLIENTS.items() if client is not None]
    return scopes


# ── handler wrapping ───────────────────────────────────────────────────────
def wrap_callback(callback, scope: str):
    @functools.wraps(callback)
    async def wrapper(client, update, *args, **kwargs):
        token = set_scope(scope)
        try:
            return await callback(client, update, *args, **kwargs)
        finally:
            reset_scope(token)

    return wrapper


def wrapped_handlers(source_groups, scope: str) -> "OrderedDict":
    """
    Copy the main bot's handler groups for a clone.

    Every callback is wrapped so it runs with ``scope``; everything else
    (filters, group order, propagation rules) stays identical.
    """
    groups = OrderedDict()
    for group, handlers in source_groups.items():
        copied = []
        for handler in handlers:
            callback = wrap_callback(handler.callback, scope)
            new_handler = None
            try:
                new_handler = type(handler)(callback=callback, filters=handler.filters)
            except TypeError:
                try:
                    new_handler = type(handler)(callback)
                except Exception:  # noqa: BLE001
                    new_handler = None
            copied.append(new_handler or handler)
        groups[group] = copied
    return groups


# ── manager ────────────────────────────────────────────────────────────────
class CloneManager:
    """Starts / stops the cloned bots of the main process."""

    def __init__(self):
        self._source_groups = None
        self.started = False

    # -- wiring ------------------------------------------------------------
    def capture_handlers(self):
        """
        Snapshot the main bot's handler groups.

        Must be called *after* every module has been imported (the ``on_cmd`` /
        ``on_message`` decorators register asynchronously), see
        :func:`start_all`.
        """
        from Myra import Myra as main_bot

        if main_bot is None or main_bot.dispatcher is None:
            return None
        self._source_groups = main_bot.dispatcher.groups
        return self._source_groups

    def _groups_for(self, scope: str):
        if self._source_groups is None:
            self.capture_handlers()
        if not self._source_groups:
            return OrderedDict()
        return wrapped_handlers(self._source_groups, scope)

    def max_clones(self) -> int:
        try:
            return int(getattr(config, "MAX_CLONES", 20) or 20)
        except Exception:  # noqa: BLE001
            return 20

    def clones_enabled(self) -> bool:
        return bool(getattr(config, "CLONE_ENABLED", True))

    # -- lifecycle ---------------------------------------------------------
    async def start_all(self):
        """Restore every enabled clone (called once on main-bot startup)."""
        self.started = True
        self.capture_handlers()
        if not self.clones_enabled():
            LOGGER.info("🧬 Clones disabled (CLONE_ENABLED=false)")
            return
        docs = await botdb.list_bots(enabled_only=True)
        if not docs:
            return
        LOGGER.info(f"🧬 Restoring {len(docs)} cloned bot(s)...")
        for doc in docs:
            try:
                await self.start_clone(doc)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(f"🧬 Could not start clone {doc.get('scope')}: {exc}")

    async def start_clone(self, doc: dict, client=None):
        """Start one cloned bot from its stored document."""
        scope = doc.get("scope") or clone_scope(doc.get("bot_id"))
        if CLIENTS.get(scope) is not None:
            return CLIENTS[scope]

        async with _lock_for(scope):
            if CLIENTS.get(scope) is not None:
                return CLIENTS[scope]

            if client is None:
                client = Client(
                    name=f"clone_{doc.get('bot_id') or scope}",
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    bot_token=doc["token"],
                    in_memory=True,
                    parse_mode=ParseMode.DEFAULT,
                )

            await client.start()
            me = await client.get_me()
            scope = clone_scope(me.id)

            # The handlers are attached only now, so nothing is dispatched with
            # the wrong scope while the client was still connecting.
            client.dispatcher.groups = self._groups_for(scope)
            attach_bot_attrs(client, me)
            CLIENTS[scope] = client

            try:
                from Myra.services.commands import publish_commands

                await publish_commands(client, f"clone @{me.username or me.id}")
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug(f"clone command menu failed: {exc}")

            await botdb.register_bot(
                bot_id=me.id,
                token=doc["token"],
                owner_id=doc.get("owner_id"),
                username=me.username or "",
                name=client.name,
            )
            await botdb.update_bot(scope, {"enabled": True, "last_start": time.time()})
            LOGGER.info(
                f"🧬 Clone started: @{me.username or me.id} "
                f"(scope {scope}, owner {doc.get('owner_id')})"
            )
            return client

    async def create_clone(self, token: str, owner_id: int):
        """
        Validate a bot token and start it as a clone.

        Returns ``(ok, message, doc)``.
        """
        if not self.clones_enabled():
            return False, "Cloning is disabled on this bot.", None

        token = (token or "").strip()
        if not TOKEN_RE.match(token):
            return False, "That does not look like a bot token.\\nGet one from @BotFather.", None
        if config.BOT_TOKEN and token == config.BOT_TOKEN.strip():
            return False, "That is the main bot's own token.", None

        existing = await botdb.list_bots()
        if len(existing) >= self.max_clones():
            return False, f"Clone limit reached ({self.max_clones()}).", None
        if any(doc.get("token") == token for doc in existing):
            return False, "This bot token is already added.", None

        probe = Client(
            name=f"clone_probe_{uuid4().hex[:12]}",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=token,
            in_memory=True,
            parse_mode=ParseMode.DEFAULT,
        )
        try:
            await probe.start()
            me = await probe.get_me()
        except Exception as exc:  # noqa: BLE001
            try:
                await probe.stop()
            except Exception:  # noqa: BLE001
                pass
            text = str(exc) or exc.__class__.__name__
            return False, f"Invalid bot token: {text}", None

        if not getattr(me, "is_bot", False):
            try:
                await probe.stop()
            except Exception:  # noqa: BLE001
                pass
            return False, "That token does not belong to a bot.", None

        scope = clone_scope(me.id)
        if CLIENTS.get(scope) is not None or await botdb.get_bot(scope):
            try:
                await probe.stop()
            except Exception:  # noqa: BLE001
                pass
            return False, f"@{me.username or me.id} is already cloned.", None

        try:
            probe.dispatcher.groups = self._groups_for(scope)
            attach_bot_attrs(probe, me)
            CLIENTS[scope] = probe
            try:
                from Myra.services.commands import publish_commands

                await publish_commands(probe, f"clone @{me.username or me.id}")
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug(f"clone command menu failed: {exc}")

            doc = await botdb.register_bot(
                bot_id=me.id,
                token=token,
                owner_id=owner_id,
                username=me.username or "",
                name=probe.name,
            )
            LOGGER.info(f"🧬 New clone created: @{me.username or me.id} by {owner_id} (scope {scope})")
            return True, f"@{me.username or me.id} is live!", doc
        except Exception as exc:  # noqa: BLE001
            CLIENTS.pop(scope, None)
            try:
                await probe.stop()
            except Exception:  # noqa: BLE001
                pass
            LOGGER.error(f"🧬 create_clone failed: {exc}")
            return False, f"Could not start the clone: {exc}", None

    async def stop_clone(self, scope: str) -> bool:
        """Stop a clone's client (the DB entry stays, so it can be resumed)."""
        client = CLIENTS.pop(scope, None)
        if client is None:
            return False
        try:
            await client.stop()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(f"🧬 stop_clone({scope}) error: {exc}")
        return True

    async def restart_clone(self, scope: str):
        """Stop and start a clone again from its stored document."""
        doc = await botdb.get_bot(scope)
        if not doc:
            return None
        await self.stop_clone(scope)
        return await self.start_clone(doc)

    async def delete_clone(self, scope: str, keep_data: bool = False) -> bool:
        """Stop a clone and forget it (optionally keeping its chats/settings)."""
        await self.stop_clone(scope)
        return await botdb.delete_bot(scope)

    async def stop_all(self):
        """Stop every clone (main-process shutdown)."""
        for scope in list(CLIENTS.keys()):
            client = CLIENTS.pop(scope, None)
            if client is None:
                continue
            try:
                await client.stop()
            except Exception:  # noqa: BLE001
                pass

    # -- info --------------------------------------------------------------
    async def list_clones(self) -> list:
        """Stored clones enriched with their live status."""
        docs = await botdb.list_bots()
        for doc in docs:
            doc["running"] = CLIENTS.get(doc.get("scope")) is not None
        return sorted(docs, key=lambda d: d.get("created_at") or 0)

    def is_running(self, scope: str) -> bool:
        return CLIENTS.get(scope) is not None


clone_manager = CloneManager()

__all__ = [
    "TOKEN_RE",
    "CLIENTS",
    "CloneManager",
    "clone_manager",
    "bot_client_for",
    "running_scopes",
    "wrap_callback",
    "wrapped_handlers",
    "attach_bot_attrs",
]
