import os
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
    try:
        await Myra.start()
    except Exception as ex:
        LOGGER.error(ex)
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

    await idle()

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
    """Fail fast with a readable message instead of a cryptic Pyrogram error.

    Reads ``os.environ`` directly, so it sees both a local ``.env`` (loaded by
    ``config`` at import time) and the host's Variables panel.
    """
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
            "\n   ➜ On Railway/Render add them in the service's Variables tab."
            "\n   ➜ Locally put them in .env (see sample.env)."
        )
        return False

    if not config.OWNER_ID:
        LOGGER.warning("⚠️ OWNER_ID is not a valid user id — /admin will not open for anyone.")
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
