"""
The bot's command menu.

Telegram shows this list when a user types ``/`` in the chat. It is deliberately
**minimal — only `/start` and `/clone`** so the bot looks clean and no admin
command is advertised. Everything else (`/admin`, `/stats`, `/chatbot`, …) still
works when typed, it is simply not listed.

Published by the **main bot** and by **every clone**, so both show the same menu.

``set_bot_commands`` is called after the client is started (a bot cannot set its
commands before it is authorised).
"""

from pyrogram.types import BotCommand

from Myra import LOGGER

# Shown to everyone in every chat, for the main bot and for each clone.
# Admin commands (/admin, /stats, /addadmin, …) are intentionally NOT listed —
# they keep working when typed, they just never appear in the ``/`` menu.
PUBLIC_COMMANDS = [
    BotCommand("start", "Start the bot"),
    BotCommand("clone", "Host your own bot"),
]


async def publish_commands(client, label: str = "") -> bool:
    """Register the ``/`` command menu on a bot client (main bot or clone)."""
    try:
        await client.set_bot_commands(PUBLIC_COMMANDS)
        LOGGER.info(f"📋 Command menu published{' for ' + label if label else ''}")
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"publish_commands failed: {exc}")
        return False


__all__ = ["PUBLIC_COMMANDS", "publish_commands"]
