from datetime import datetime

from pyrogram.enums import ChatType
from pyrogram.types import InlineKeyboardMarkup, Message

from Myra import Myra
from Myra.database.chats import add_served_chat
from Myra.database.users import add_served_user
from Myra.database.bots import bot_info
from Myra.modules.helpers import CLOSE_BTN


@Myra.on_cmd("ping")
async def ping(_, message: Message):
    """Plain text latency check. No sticker, no photo."""
    start = datetime.now()
    sent = await message.reply_text("Pinging...")
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    ms = (datetime.now() - start).microseconds / 1000
    await sent.edit_text(
        text=(
            f"{bot_info().get('name') or Myra.name} is alive and working fine.\n"
            f"Ping: {ms} ms"
        ),
        reply_markup=InlineKeyboardMarkup(CLOSE_BTN),
    )

    if message.chat.type == ChatType.PRIVATE:
        await add_served_user(message.from_user.id)
    else:
        await add_served_chat(message.chat.id)


__all__ = ["ping"]
