from Myra import db
from Myra.database.scope import scope_field, scope_match

chatsdb = db.chatsdb if db is not None else None


async def get_served_chats(scope: str = None) -> list:
    """Served groups of the bot in this scope."""
    if chatsdb is None:
        return []
    chats = chatsdb.find({"chat_id": {"$lt": 0}, **scope_match(scope)})
    chats_list = []
    async for chat in chats:
        chats_list.append(chat)
    return chats_list


async def is_served_chat(chat_id: int, scope: str = None) -> bool:
    if chatsdb is None:
        return False
    chat = await chatsdb.find_one({"chat_id": chat_id, **scope_match(scope)})
    return chat is not None


async def add_served_chat(chat_id: int, scope: str = None):
    if chatsdb is None:
        return None
    is_served = await is_served_chat(chat_id, scope)
    if is_served:
        return None
    return await chatsdb.insert_one({"chat_id": chat_id, **scope_field(scope)})


async def remove_served_chat(chat_id: int, scope: str = None):
    if chatsdb is None:
        return None
    is_served = await is_served_chat(chat_id, scope)
    if not is_served:
        return None
    return await chatsdb.delete_one({"chat_id": chat_id, **scope_match(scope)})


# Consolidated helper functions from old db/chatsdb.py
async def get_chats(scope: str = None) -> list:
    if chatsdb is None:
        return []
    chats_list = []
    async for chat in chatsdb.find({"chat_id": {"$lt": 0}, **scope_match(scope)}):
        chats_list.append(chat["chat_id"])
    return chats_list


async def get_chat(chat_id: int, scope: str = None) -> bool:
    return await is_served_chat(chat_id, scope)


async def add_chat(chat_id: int, scope: str = None):
    await add_served_chat(chat_id, scope)


async def del_chat(chat_id: int, scope: str = None):
    await remove_served_chat(chat_id, scope)


__all__ = [
    "chatsdb",
    "get_served_chats",
    "is_served_chat",
    "add_served_chat",
    "remove_served_chat",
    "get_chats",
    "get_chat",
    "add_chat",
    "del_chat",
]
