from pyrogram.types import InlineKeyboardButton

from Myra import OWNER
from Myra import Myra

# NOTE: the start/help keyboards are built at runtime by
# `build_start_buttons()` / `build_group_buttons()` below, because `Myra.username`
# is unknown until the client has started (module-level URLs used to break).

BACK = [
    [
        InlineKeyboardButton(text="Back", callback_data="BACK"),
    ],
]


# The help screen deliberately lists nothing but the clone flow — the bot must
# look as simple as possible, so /ping, /id, /stats, /useraccounts etc. are
# never advertised.
HELP_BTN = [
    [
        InlineKeyboardButton(text="Clone your bot", callback_data="CLONE_MENU"),
    ],
    [
        InlineKeyboardButton(text="Back", callback_data="BACK"),
        InlineKeyboardButton(text="Close", callback_data="CLOSE"),
    ],
]


CLOSE_BTN = [
    [
        InlineKeyboardButton(text="Close", callback_data="CLOSE"),
    ],
]


CHATBOT_ON = [
    [
        InlineKeyboardButton(text="Enable", callback_data="addchat"),
        InlineKeyboardButton(text="Disable", callback_data="rmchat"),
    ],
]


MUSIC_BACK_BTN = [
    [
        InlineKeyboardButton(text="Soon", callback_data="soom"),
    ],
]

S_BACK = [
    [
        InlineKeyboardButton(text="Back", callback_data="SBACK"),
        InlineKeyboardButton(text="Close", callback_data="CLOSE"),
    ],
]


CHATBOT_BACK = [
    [
        InlineKeyboardButton(text="Back", callback_data="CHATBOT_BACK"),
        InlineKeyboardButton(text="Close", callback_data="CLOSE"),
    ],
]


HELP_START = [
    [
        InlineKeyboardButton(text="Help", callback_data="HELP"),
        InlineKeyboardButton(text="Close", callback_data="CLOSE"),
    ],
]


ABOUT_BTN = [
    [
        InlineKeyboardButton(text="Help", callback_data="HELP"),
        InlineKeyboardButton(text="Clone your bot", callback_data="CLONE_MENU"),
    ],
    [
        InlineKeyboardButton(text="Close", callback_data="CLOSE"),
    ],
]


def _add_me_url() -> str:
    """``?startgroup`` link, built at call time (the username is unknown at import)."""
    username = Myra.username
    try:
        from Myra.database.bots import bot_info

        username = bot_info().get("username") or username
    except Exception:  # noqa: BLE001
        pass
    if username:
        return f"https://t.me/{username}?startgroup=true"
    return f"tg://user?id={OWNER}"


def build_start_buttons() -> list:
    """
    Inline keyboard for /start — built at runtime, and deliberately clean:
    no owner, no support group, no update channel, no credits, no emoji, and
    no Help row either. Exactly two things: add the bot to a group, or clone it.
    """
    return [
        [
            InlineKeyboardButton(text="Add me to your group", url=_add_me_url()),
            InlineKeyboardButton(text="Clone your bot", callback_data="CLONE_MENU"),
        ],
    ]


def build_group_buttons() -> list:
    """Inline keyboard shown when the bot is added to / started in a group."""
    return [
        [
            InlineKeyboardButton(text="Help", callback_data="HELP"),
            InlineKeyboardButton(text="Clone your bot", callback_data="CLONE_MENU"),
        ],
    ]


def build_clone_menu_buttons() -> list:
    """
    Everything clone-related, reachable from the start card's
    **Clone your bot** button.
    """
    return [
        [
            InlineKeyboardButton(text="Step by step guide", callback_data="CLONE_GUIDE"),
            InlineKeyboardButton(text="My bots", callback_data="CLONE_MYBOTS"),
        ],
        [
            InlineKeyboardButton(text="Add me to your group", url=_add_me_url()),
        ],
        [
            InlineKeyboardButton(text="Back", callback_data="BACK"),
            InlineKeyboardButton(text="Close", callback_data="CLOSE"),
        ],
    ]
