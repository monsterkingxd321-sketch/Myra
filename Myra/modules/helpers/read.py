"""
Text builders for the menu screens.

These are FUNCTIONS (not constants) on purpose: the bot's username is only
known after the client starts, so building the strings at import time produced
broken ``t.me/None`` links.
"""

from Myra import Myra


def _bot() -> dict:
    """Cached identity of the bot serving the current update (main or a clone)."""
    try:
        from Myra.database.bots import bot_info

        return bot_info() or {}
    except Exception:  # noqa: BLE001
        return {}


def _name() -> str:
    return _bot().get("name") or Myra.name or "Myra"


def start_text() -> str:
    return (
        f"Hey, I am {_name()}.\n\n"
        "I talk like a normal person, in your groups and in private chat.\n\n"
        "Add me to your group and I will start chatting on my own.\n\n"
        "Press a button below to continue."
    )


def group_start_text() -> str:
    return (
        f"Hey, I am {_name()}.\n\n"
        "Thanks for adding me.\n\n"
        "I will chat here from time to time."
    )


def clone_menu_text() -> str:
    return (
        "Host your own bot\n\n"
        "Send a bot token and your bot goes live right away.\n"
        "It is free and it runs on this same setup.\n\n"
        "Commands:\n"
        "/clone <bot_token> - Host a bot.\n"
        "/clonehelp - Step by step guide.\n"
        "/mybots - Bots you have hosted.\n"
        "/delbot <bot_id> - Remove a bot."
    )


def help_text() -> str:
    return (
        "Help\n\n"
        "Add me to your group and I will start chatting there on my own.\n\n"
        "To host your own bot:\n"
        "/clone - Send this with your bot token.\n"
        "/clonehelp - Step by step guide.\n"
        "/mybots - Bots you have hosted.\n"
        "/delbot - Remove a bot you hosted."
    )


def tools_text() -> str:
    return (
        "Tools\n\n"
        "/clone - Host your own bot.\n"
        "/clonehelp - Step by step guide.\n"
        "/mybots - Bots you have hosted.\n"
        "/delbot - Remove a bot you hosted.\n\n"
        "Add me to your group and I will start chatting on my own."
    )


def chatbot_text() -> str:
    return (
        "Chatbot\n\n"
        "/chatbot on - Turn me on in a group.\n"
        "/chatbot off - Turn me off in a group.\n\n"
        "This only works inside groups."
    )


def source_text() -> str:
    return (
        "Source code\n\n"
        "This project is private right now.\n"
        "The owner can give access."
    )


def admin_text() -> str:
    return (
        "Admin panel\n\n"
        "/admin - Opens the full control panel.\n\n"
        "Broadcast, AutoChat, Group Requests, Userbots, AI and keys,\n"
        "reply behaviour, imported sessions, clones, database and stats.\n\n"
        "/addadmin <user_id> - The owner can hand out access."
    )


def about_text() -> str:
    return (
        f"{_name()}\n\n"
        "An AI based chat bot.\n"
        "It replies on its own, to users and in groups.\n"
        "MongoDB first, with Google Gemini and OpenAI as fallback.\n\n"
        "Press a button below."
    )
