"""
Time-to-time messaging service ("AutoChat").

Keeps groups (and optionally DMs) alive by sending a natural message every now
and then — from **every bot** (the main bot and every clone) and from **every
userbot**, with completely separate timings:

    bot  : autochat_bot_enabled / autochat_bot_interval_ms / autochat_bot_targets
    bots : autochat_ub_enabled  / autochat_ub_interval_ms  / autochat_ub_targets

Both groups of settings are stored **per scope**, so the main bot and each clone
have their own timings (see :mod:`Myra.database.autochat`).

The message text is picked at random from the trained Hinglish database (via
``$sample``, which is instant even on millions of documents) and then passed
through the girl-voice filter, so it reads like a real person talking. When the
database is empty, a small built-in list of openers is used instead.

Timing is deliberately *jittered* (±40%) so the messages never look scheduled.
Targets are visited round-robin, which guarantees every group is reached.
"""

import asyncio
import random
import time

from pyrogram.enums import ChatAction
from pyrogram.errors import FloodWait

from Myra import config, LOGGER
from Myra.ai.humanizer import feminize, girly_touch
from Myra.database import autochat as ac
from Myra.database import training
from Myra.database.chats import get_chats
from Myra.database.scope import current_scope, owns_scope, reset_scope, set_scope
from Myra.database.users import get_users
from Myra.services.broadcast import userbot_targets

MAX_LINE_LEN = 140
MAX_TARGET_FAILURES = 3


async def random_line() -> str:
    """A random trained reply, feminized; falls back to a built-in opener."""
    candidates = []
    if training.TRAINING is not None:
        try:
            docs = await training.TRAINING.aggregate(
                [{"$sample": {"size": 12}}]
            ).to_list(length=12)
            for doc in docs:
                if (doc.get("check") or "none") == "sticker":
                    continue
                text = (doc.get("text") or "").strip()
                if 3 <= len(text) <= MAX_LINE_LEN * 2:
                    candidates.append(text)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(f"autochat: random_line query failed: {exc}")

    text = ""
    if candidates:
        longish = [c for c in candidates if len(c) >= 8] or candidates
        text = random.choice(longish)
    else:
        text = random.choice(ac.FALLBACK_LINES)

    text = " ".join(text.split())
    if len(text) > MAX_LINE_LEN:
        text = text[:MAX_LINE_LEN].rsplit(" ", 1)[0].rstrip(" ,.-")
    if getattr(config, "AI_FEMININE", True):
        text = feminize(text)
    if getattr(config, "AI_GIRLY_EMOJI", True):
        text = girly_touch(text)
    return text


class AutoChatService:
    """One background task for the bots, one for every userbot account."""

    def __init__(self):
        self.running = False
        self._bot_task = None
        self._ub_task = None
        self._bot_next: dict = {}      # scope -> next allowed time
        self._ub_next: dict = {}       # account_id -> next allowed time
        self._cursors: dict = {}       # cursor key -> round-robin index
        self._failures: dict = {}
        self.last_sent: dict = {}
        self.sent_total = 0
        self.failed_total = 0
        self.last_flood_wait = 0

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def start(self):
        if self.running:
            return
        self.running = True
        self._bot_task = asyncio.create_task(self._bot_loop(), name="autochat-bot")
        self._ub_task = asyncio.create_task(self._ub_loop(), name="autochat-userbots")
        LOGGER.info("⏰ AutoChat service started (bots + userbots)")

    async def stop(self):
        self.running = False
        for task in (self._bot_task, self._ub_task):
            if task is not None:
                task.cancel()
        self._bot_task = self._ub_task = None

    def status(self, scope: str = None) -> dict:
        scope = scope or current_scope()
        upcoming = [value for value in self._bot_next.values() if value]
        return {
            "running": self.running,
            "sent_total": self.sent_total,
            "failed_total": self.failed_total,
            "bot_next_in": max(0, int(self._bot_next.get(scope, 0) - time.time()))
            if self._bot_next.get(scope)
            else 0,
            "next_any_in": max(0, int(min(upcoming) - time.time())) if upcoming else 0,
            "last_flood_wait": self.last_flood_wait,
            "last_sent": dict(self.last_sent),
        }

    def reset_timers(self, scope: str = None):
        """Force the next tick of a scope (or all of them) to send immediately."""
        if scope:
            self._bot_next.pop(scope, None)
        else:
            self._bot_next.clear()
        self._ub_next.clear()

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _tick() -> int:
        try:
            return max(5, int(getattr(config, "AUTOCHAT_TICK_SECONDS", 20)))
        except Exception:  # noqa: BLE001
            return 20

    @staticmethod
    def _batch() -> int:
        try:
            return max(1, int(getattr(config, "AUTOCHAT_BATCH", 1)))
        except Exception:  # noqa: BLE001
            return 1

    @staticmethod
    def _jitter(base_ms: int) -> float:
        """±40% so the messages never look automated."""
        base = max(1.0, base_ms / 1000.0)
        return base * random.uniform(0.6, 1.4)

    def _pick(self, targets: list, cursor_key: str) -> list:
        if not targets:
            return []
        batch = min(self._batch(), len(targets))
        start = self._cursors.get(cursor_key, 0)
        picked = [targets[(start + offset) % len(targets)] for offset in range(batch)]
        self._cursors[cursor_key] = (start + batch) % len(targets)
        return picked

    def _allowed(self, key: tuple) -> bool:
        return self._failures.get(key, 0) < MAX_TARGET_FAILURES

    def _mark(self, key: tuple, ok: bool):
        if ok:
            self._failures.pop(key, None)
        else:
            self._failures[key] = self._failures.get(key, 0) + 1

    async def _send(self, client, chat_id: int, text: str, is_bot: bool, label: str) -> bool:
        key = (label, chat_id)
        if not self._allowed(key):
            return False
        try:
            try:
                await client.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(random.uniform(1.0, 2.5))
            except Exception:  # noqa: BLE001
                pass
            await client.send_message(chat_id=chat_id, text=text)
        except FloodWait as exc:
            # A flood-wait is not a broken target — pause the whole scheduler
            # instead of hammering Telegram, and do NOT count a strike.
            wait = int(getattr(exc, "value", 30) or 30)
            self.last_flood_wait = wait
            LOGGER.warning(f"⏳ AutoChat flood-wait {wait}s — pausing the scheduler")
            await asyncio.sleep(min(wait, 900))
            return False
        except Exception as exc:  # noqa: BLE001
            self._mark(key, False)
            self.failed_total += 1
            LOGGER.debug(f"autochat send failed [{label}] {chat_id}: {exc}")
            return False

        self._mark(key, True)
        self.sent_total += 1
        self.last_sent[label] = time.time()
        LOGGER.info(f"⏰ AutoChat [{label}] → {chat_id}: {text[:40]}")
        return True

    # ── bots (main + clones) ───────────────────────────────────────────────
    async def _bot_loop(self):
        while self.running:
            try:
                await asyncio.sleep(self._tick())
                from Myra.services.clones import running_scopes

                for scope in running_scopes():
                    try:
                        await self._bot_tick(scope)
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.debug(f"autochat bot tick({scope}) error: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(f"autochat bot loop error: {exc}")
                await asyncio.sleep(30)

    async def _bot_tick(self, scope: str):
        if not await ac.get_bot_enabled(scope):
            return

        now = time.time()
        due = self._bot_next.get(scope, 0.0)
        if now < due:
            return

        # stagger the first run of each bot so they never fire together
        if not due:
            self._bot_next[scope] = now + random.uniform(5, 90)
            return

        interval = await ac.get_bot_interval(scope)
        self._bot_next[scope] = now + self._jitter(interval)
        await self._bot_emit(scope)

    async def _bot_emit(self, scope: str = None):
        from Myra.services.clones import bot_client_for

        scope = scope or current_scope()
        client = bot_client_for(scope)
        if client is None:
            return

        token = set_scope(scope)
        try:
            target_mode = await ac.get_bot_targets(scope)
            targets = []
            if ac.targets_include_groups(target_mode):
                targets += await get_chats(scope)
            if ac.targets_include_dm(target_mode):
                targets += await get_users(scope)

            targets = list(dict.fromkeys(targets))
            if not targets:
                return

            text = await random_line()
            label = f"bot:{scope}"
            for chat_id in self._pick(targets, label):
                await self._send(client, chat_id, text, True, label)
        finally:
            reset_scope(token)

    # ── userbots ───────────────────────────────────────────────────────────
    async def _ub_loop(self):
        while self.running:
            try:
                await asyncio.sleep(self._tick())
                from Myra.userbot.storage import accounts as ub_accounts

                for account_id, account in list(ub_accounts.items()):
                    try:
                        await self._ub_tick(account_id, account)
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.debug(f"autochat userbot {account_id} error: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(f"autochat userbot loop error: {exc}")
                await asyncio.sleep(30)

    async def _ub_tick(self, account_id: str, account: dict, force: bool = False):
        client = account.get("client")
        if client is None or account.get("status") != "CONNECTED":
            return

        token = set_scope(account.get("scope") or current_scope())
        try:
            settings = await ac.resolve_account_settings(account)

            if not settings["enabled"] and not force:
                return

            now = time.time()
            due = self._ub_next.get(account_id, 0.0)
            if not force:
                if now < due:
                    return

                # stagger the first run of each account so they never fire together
                if not due:
                    self._ub_next[account_id] = now + random.uniform(5, 90)
                    return

                self._ub_next[account_id] = now + self._jitter(settings["interval"])

            targets = await userbot_targets(client, account_id, settings["targets"])
            if not targets:
                return

            label = account.get("name") or account_id
            text = await random_line()
            for chat_id in self._pick(targets, f"ub:{account_id}"):
                await self._send(client, chat_id, text, False, label)
        finally:
            reset_scope(token)

    async def send_now(self, kind: str = "bot", scope: str = None) -> int:
        """
        Force one AutoChat round right now (used by the panel test button).

        ``kind`` is ``"bot"`` (the bot of ``scope``) or anything else for the
        userbots of ``scope``.
        """
        scope = scope or current_scope()
        before = self.sent_total
        if str(kind).lower() in ("bot", "main", "bots"):
            await self._bot_emit(scope)
            return self.sent_total - before

        from Myra.userbot.storage import accounts as ub_accounts

        for account_id, account in list(ub_accounts.items()):
            if not owns_scope(account.get("scope"), scope):
                continue
            try:
                await self._ub_tick(account_id, account, force=True)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug(f"autochat send_now({account_id}) failed: {exc}")
        return self.sent_total - before


autochat_service = AutoChatService()

__all__ = ["AutoChatService", "autochat_service", "random_line"]
