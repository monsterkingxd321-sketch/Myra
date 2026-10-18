from pyrogram import Client
from pyrogram.types import Message

from Myra import Myra
from Myra.database.chats import get_served_chats
from Myra.database.users import get_served_users
from Myra.database import training
from Myra.ai.brain import ai_status


@Myra.on_cmd("stats")
async def stats(cli: Client, message: Message):
    users = len(await get_served_users())
    chats = len(await get_served_chats())

    # Hybrid AI / training database statistics
    db = await training.get_stats()
    ai = ai_status()

    caption = (
        f"ᴛᴏᴛᴀʟ sᴛᴀᴛs ᴏғ {(await cli.get_me()).mention} :\n\n"
        f"➻ **ᴄʜᴀᴛs :** {chats}\n"
        f"➻ **ᴜsᴇʀs :** {users}\n\n"
        "🧠 **ʜʏʙʀɪᴅ ᴀɪ :**\n"
        f"➻ **ᴛʀᴀɪɴᴇᴅ ʀᴇᴘʟɪᴇs :** {db.get('total', 0)}\n"
        f"➻ **ᴀᴄᴛɪᴠᴇ ᴀᴘɪ ᴋᴇʏs :** {ai.get('active', 0)}/{ai.get('total', 0)}\n"
        f"➻ **ᴅᴀᴛᴀʙᴀsᴇ sɪᴢᴇ :** {training.human_bytes(db.get('collection_bytes', 0))}\n"
        f"➻ **ᴍᴏᴅᴇ :** {'ʜʏʙʀɪᴅ (ᴅʙ + ᴀɪ)' if ai.get('enabled') and ai.get('active') else 'ᴏғғʟɪɴᴇ (ᴅʙ ᴏɴʟʏ)'}"
    )

    await message.reply_text(caption)
