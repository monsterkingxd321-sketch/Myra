import os
import re
import asyncio
import importlib
from threading import Thread
from flask import Flask

from pyrogram import idle

import config
from Myra import LOGGER, Myra
from Myra.modules import ALL_MODULES
from Myra.userbot import userbot_manager

# ─────────────────────────────────────
# 1️⃣ Flask health server
#     Every cloud host (Railway, Render, Fly) needs the container to listen on
#     the port it injects via $PORT — and uses it for the health check.
# ─────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def home():
    return "✅ Myra Bot is Alive!"


@app.route("/health")
def health():
    """Liveness probe used by Railway (`healthcheckPath`) and Docker HEALTHCHECK.

    Deliberately does not touch Telegram or MongoDB: the platform only needs to
    know the process is up and listening. Returns 200 within milliseconds even
    while the clients are still connecting.
    """
    return {"status": "ok", "bot": getattr(Myra, "username", None)}


def run_flask():
    """Serve the health check on $PORT (defaults to 5000 for local runs)."""
    port = int(os.environ.get("PORT", 5000))
    LOGGER.info(f"🌐 Health server listening on http://0.0.0.0:{port}/health")
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

# ─────────────────────────────────────
# 2️⃣ Pyrogram Bot Starter
# ─────────────────────────────────────
async def start_bot():
    max_boot_attempts = 15
    dc_candidates = [getattr(config, "TELEGRAM_DC", 5)]
    for dc in (5, 4, 1, 2):
        if dc not in dc_candidates:
            dc_candidates.append(dc)

    for boot_attempt in range(1, max_boot_attempts + 1):
        target_dc = dc_candidates[(boot_attempt - 1) % len(dc_candidates)]
        try:
            if hasattr(Myra, "set_target_dc"):
                Myra.set_target_dc(target_dc)
            try:
                await Myra.storage.open()
                await Myra.storage.auth_key(None)
                await Myra.storage.user_id(None)
                await Myra.storage.is_bot(None)
            except Exception:
                pass
            LOGGER.info(f"🚀 Starting Myra client (attempt {boot_attempt}/{max_boot_attempts} on DC {target_dc})...")
            await Myra.start()
            LOGGER.info(f"✅ Myra bot client started successfully on DC {target_dc}!")
            break
        except Exception as ex:
            LOGGER.warning(f"⚠️ Myra.start() attempt {boot_attempt} (DC {target_dc}) failed: {ex}")
            try:
                if getattr(Myra, "is_connected", False):
                    await Myra.stop()
                elif hasattr(Myra, "disconnect"):
                    await Myra.disconnect()
            except Exception:
                pass
            try:
                await Myra.storage.open()
                await Myra.storage.auth_key(None)
            except Exception:
                pass

            if boot_attempt < max_boot_attempts:
                next_dc = dc_candidates[boot_attempt % len(dc_candidates)]
                backoff_wait = min(20, 2 * boot_attempt)
                LOGGER.info(
                    f"⏳ Telegram DC {target_dc} returned: {ex}. "
                    f"Rotating to DC {next_dc} in {backoff_wait}s (health server staying alive)..."
                )
                await asyncio.sleep(backoff_wait)
            else:
                LOGGER.error(f"❌ Failed to start Myra bot after {max_boot_attempts} attempts: {ex}", exc_info=True)
                raw_tok = str(config.BOT_TOKEN or "")
                preview = f"{raw_tok[:7]}...{raw_tok[-4:]}" if len(raw_tok) > 12 else "(empty/invalid)"
                LOGGER.error(
                    "⚠️ Bot Authorization Failed!\n"
                    "If you see 'auth.ImportBotAuthorization' TimeoutError or AccessTokenInvalid:\n"
                    " 1. Telegram requires BOTH a valid BOT_TOKEN AND a valid API_ID + API_HASH pair from https://my.telegram.org.\n"
                    " 2. If you changed BOT_TOKEN, make sure your API_ID and API_HASH are also 100% correct and not blocked/expired.\n"
                    " 3. If Telegram DC 2 returned 500 RPC_CALL_FAIL, you can explicitly set TELEGRAM_DC=4 or TELEGRAM_DC=1 in host variables.\n"
                    f" 4. Current BOT_TOKEN preview: {preview}."
                )
                quit(1)

    for module in ALL_MODULES:
        importlib.import_module("Myra.modules." + module)

    # Register the main bot's identity (used by the panels, the /start links and
    # by every "is this the main scope?" check).
    try:
        from Myra.database.bots import set_bot_info
        from Myra.database.scope import MAIN_SCOPE

        set_bot_info(
            MAIN_SCOPE,
            bot_id=Myra.id,
            username=Myra.username,
            name=Myra.name,
            mention=Myra.mention,
            owner_id=int(config.OWNER_ID) if config.OWNER_ID else None,
        )
    except Exception as ex:  # noqa: BLE001
        LOGGER.warning(f"⚠️ Could not register bot identity: {ex}")

    # Publish the '/' command menu so /start and /clone appear when typing '/'
    try:
        from Myra.services.commands import publish_commands

        await publish_commands(Myra, f"@{Myra.username}")
    except Exception as ex:  # noqa: BLE001
        LOGGER.warning(f"⚠️ Could not publish command menu: {ex}")

    # Prepare MongoDB indexes for the hybrid AI similarity search
    try:
        from Myra.database import training
        await training.ensure_indexes()
    except Exception as ex:
        LOGGER.warning(f"⚠️ Could not prepare AI indexes: {ex}")

    # Load AI keys from .env + optional key file + MongoDB (unlimited).
    # Gemini and OpenAI / ChatGPT keys both live in the same shared pool.
    try:
        from Myra.ai.brain import PROVIDERS, provider_order

        for name in provider_order():
            info = await PROVIDERS[name].refresh_keys()
            LOGGER.info(
                f"🔑 {name} keys ready: {info.get('active')}/{info.get('total')} active "
                f"(env {info.get('env_keys', 0)} · file {info.get('file_keys', 0)} · db {info.get('db_keys', 0)})"
            )
    except Exception as ex:
        LOGGER.warning(f"⚠️ Could not load AI API keys: {ex}")

    LOGGER.info(f"@{Myra.username} Started Successfully")

    # Startup recovery for userbot accounts
    try:
        await userbot_manager.startup()
        LOGGER.info("✅ Multi-Userbot Manager Initialized Successfully")
    except Exception as ex:
        LOGGER.error(f"⚠️ Userbot Manager Startup Error: {ex}")

    # Restore every cloned bot (they share this process, this database and the
    # AI keys, but each one has its own owner, admins, userbots and settings).
    try:
        from Myra.services.clones import clone_manager

        await asyncio.sleep(1)  # let every module's handler registration finish
        await clone_manager.start_all()
    except Exception as ex:  # noqa: BLE001
        LOGGER.warning(f"⚠️ Could not restore cloned bots: {ex}")

    # Time-to-time messaging (AutoChat) for the bot + every userbot
    try:
        from Myra.services.autochat import autochat_service
        if getattr(config, "AUTOCHAT_ENABLED", True):
            await autochat_service.start()
        else:
            LOGGER.info("⏰ AutoChat disabled (AUTOCHAT_ENABLED=false)")
    except Exception as ex:
        LOGGER.warning(f"⚠️ Could not start AutoChat service: {ex}")

    # Warm the admin views in the background: the training stats and the
    # audience report (which walks every userbot's dialog list once). Without
    # this, the first /admin open after a restart paid for all of it.
    async def _warm_admin_caches():
        await asyncio.sleep(10)  # let startup traffic settle first
        try:
            from Myra.database import training
            from Myra.services import userstats

            await training.get_stats()
            await userstats.collect(force=True)
            LOGGER.info("⚡ Admin caches warmed")
        except Exception as ex:  # noqa: BLE001
            LOGGER.debug(f"admin cache warm-up skipped: {ex}")

    warm_task = asyncio.create_task(_warm_admin_caches())

    await idle()

    try:
        warm_task.cancel()
    except Exception:  # noqa: BLE001
        pass

    # Graceful stop on shutdown
    try:
        from Myra.services.autochat import autochat_service
        await autochat_service.stop()
    except Exception:
        pass

    try:
        from Myra.services.clones import clone_manager
        await clone_manager.stop_all()
    except Exception:
        pass

    try:
        await userbot_manager.stop()
    except Exception:
        pass

# ─────────────────────────────────────
# 4️⃣ Preflight + Start Flask & the Bot together
# ─────────────────────────────────────
def preflight() -> bool:
    """Fail fast with a readable message instead of a cryptic Pyrogram error."""
    fatal = [n for n in ("API_ID", "API_HASH", "BOT_TOKEN", "OWNER_ID") if not os.environ.get(n)]
    soft = [n for n in ("MONGO_URL",) if not os.environ.get(n)]

    if soft:
        LOGGER.warning(
            "⚠️ Missing environment variable(s): " + ", ".join(soft) +
            " — the bot starts, but the trained database will not be used."
        )

    if fatal:
        LOGGER.error(
            "❌ Missing required environment variable(s): " + ", ".join(fatal) +
            "\n   ➜ In Koyeb/Railway/Render add them in the service's Variables tab."
            "\n   ➜ Locally put them in .env (see sample.env)."
        )
        return False

    api_id_str = str(config.API_ID or "").strip()
    api_hash_str = str(config.API_HASH or "").strip()
    bot_tok_str = str(config.BOT_TOKEN or "").strip()

    # Credential format validation to catch typos, swaps, and invalid formats
    format_issues = []
    if not api_id_str.isdigit() or int(api_id_str) <= 1000:
        format_issues.append(f"API_ID '{api_id_str}' is invalid (must be your Telegram App ID number from https://my.telegram.org)")

    if len(api_hash_str) != 32 or not re.match(r"^[0-9a-fA-F]{32}$", api_hash_str):
        format_issues.append(
            f"API_HASH length is {len(api_hash_str)} (must be exactly 32 hex characters from https://my.telegram.org). "
            "Make sure you did not swap API_ID and API_HASH or paste a bot token here."
        )

    if ":" not in bot_tok_str:
        format_issues.append("BOT_TOKEN is missing ':' — must be in format '123456789:ABCDefGh...' from @BotFather")

    if format_issues:
        LOGGER.error("❌ Invalid Telegram Credential Format in Environment Variables:")
        for err in format_issues:
            LOGGER.error(f"   ➜ {err}")
        return False

    # Log clean credential previews so user can verify what values are actually running
    api_hash_preview = f"{api_hash_str[:4]}...{api_hash_str[-4:]}" if len(api_hash_str) >= 8 else "(too short)"
    bot_tok_preview = f"{bot_tok_str[:7]}...{bot_tok_str[-4:]}" if len(bot_tok_str) >= 12 else "(too short)"
    LOGGER.info(
        f"🔐 Telegram Credentials: API_ID={api_id_str} · "
        f"API_HASH={api_hash_preview} (len {len(api_hash_str)}) · BOT_TOKEN={bot_tok_preview}"
    )

    # Verify token with Telegram HTTP Bot API
    try:
        import requests
        r = requests.get(f"https://api.telegram.org/bot{bot_tok_str}/getMe", timeout=25)
        data = r.json()
        if data.get("ok"):
            bot_obj = data.get("result", {})
            LOGGER.info(
                f"✅ Verified Bot Token via Telegram API: @{bot_obj.get('username')} "
                f"('{bot_obj.get('first_name')}', ID: {bot_obj.get('id')})"
            )
        else:
            LOGGER.error(
                f"❌ Telegram BotFather Token Rejected: {data.get('description', 'Unknown error')} "
                f"(Error code: {data.get('error_code')})"
            )
    except Exception as exc:
        LOGGER.warning(f"⚠️ Could not verify token via Telegram HTTP API: {exc}")

    if not config.OWNER_ID:
        LOGGER.warning("⚠️ OWNER_ID is not a valid user id — /admin will not open for anyone.")

    # Remove any stale .session file to ensure completely fresh auth
    for stale_session in ("Myra.session", "Myra.session-journal"):
        if os.path.exists(stale_session):
            try:
                os.remove(stale_session)
                LOGGER.info(f"🧹 Removed stale {stale_session}")
            except Exception:
                pass

    return True


if __name__ == "__main__":
    # Health server first: the platform must see an open port immediately.
    Thread(target=run_flask, daemon=True).start()

    if not preflight():
        raise SystemExit(1)

    # NOTE: keep `get_event_loop()` — Pyrogram captures the loop in Client.__init__
    # (which already ran at import time), so `asyncio.run()` would create a *second*
    # loop and handler registration would silently target the wrong one.
    asyncio.get_event_loop().run_until_complete(start_bot())
