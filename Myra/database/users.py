from Myra import db
from Myra.database.scope import scope_field, scope_match

usersdb = db.usersdb if db is not None else None


async def is_served_user(user_id: int, scope: str = None) -> bool:
    if usersdb is None:
        return False
    user = await usersdb.find_one({"user_id": user_id, **scope_match(scope)})
    return user is not None


async def get_served_users(scope: str = None) -> list:
    """DM users of the bot in this scope."""
    if usersdb is None:
        return []
    users_list = []
    async for user in usersdb.find({"user_id": {"$gt": 0}, **scope_match(scope)}):
        users_list.append(user)
    return users_list


async def add_served_user(user_id: int, scope: str = None):
    if usersdb is None:
        return None
    is_served = await is_served_user(user_id, scope)
    if is_served:
        return None
    return await usersdb.insert_one({"user_id": user_id, **scope_field(scope)})


async def remove_served_user(user_id: int, scope: str = None):
    if usersdb is None:
        return None
    is_served = await is_served_user(user_id, scope)
    if not is_served:
        return None
    return await usersdb.delete_one({"user_id": user_id, **scope_match(scope)})


# Consolidated helper functions from old db/usersdb.py
async def get_users(scope: str = None) -> list:
    if usersdb is None:
        return []
    user_list = []
    async for user in usersdb.find({"user_id": {"$gt": 0}, **scope_match(scope)}):
        user_list.append(user["user_id"])
    return user_list


async def get_user(user_id: int, scope: str = None) -> bool:
    return await is_served_user(user_id, scope)


async def add_user(user_id: int, scope: str = None):
    await add_served_user(user_id, scope)


async def del_user(user_id: int, scope: str = None):
    await remove_served_user(user_id, scope)


__all__ = [
    "usersdb",
    "is_served_user",
    "get_served_users",
    "add_served_user",
    "remove_served_user",
    "get_users",
    "get_user",
    "add_user",
    "del_user",
]
