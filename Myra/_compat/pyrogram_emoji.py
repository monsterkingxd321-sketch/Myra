"""Drop-in stand-in for the ``pyrogram.emoji`` module that Kurigram dropped.

Why this exists
---------------
Stock Pyrogram ships ``pyrogram/emoji.py``: a ~4000-line table of emoji
constants generated from Unicode data. Kurigram — the maintained fork the bot
*have* to use, because plain Pyrogram's API layer is too old for Telegram and
userbot logins fail with ``406 UPDATE_APP_TO_LOGIN`` — removed that file.

``Abg`` 2.3.1 (the Pyrogram helper the bot was built on) starts its
``Abg/inline/inline_keyboard.py`` with ``from pyrogram.emoji import *`` and then
uses thirteen flag constants from it. On a clean install (Docker image, fresh VPS
venv) that import therefore dies with::

    ModuleNotFoundError: No module named 'pyrogram.emoji'

A machine that once had stock Pyrogram installed still has the leftover file,
which is why the crash only ever appeared inside the container.

``Myra/__init__.py`` registers this module as ``pyrogram.emoji`` **only** when the
real one is missing, so a normal Pyrogram environment is left untouched.

The flags are Unicode regional-indicator pairs, built here from their ISO 3166
alpha-2 codes rather than copied out of Pyrogram's table. The names match
Pyrogram's, so ``Abg`` works and anything expecting ``Emoji.FLAG_*`` keeps
working too.
"""

from __future__ import annotations

_REGIONAL_INDICATOR_A = 0x1F1E6


def _flag(iso2: str) -> str:
    """Turn an ISO 3166 alpha-2 code into its flag emoji (``"BY"`` -> ``"🇧🇾"``).

    Every letter A-Z maps to one of the 26 regional indicator symbols, so a flag
    is simply the two indicators of its country code::

        0x1F1E6 + ord("B") - ord("A") -> 🇧
        0x1F1E6 + ord("Y") - ord("A") -> 🇾
    """
    code = iso2.strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"not an ISO 3166 alpha-2 code: {iso2!r}")
    return "".join(chr(_REGIONAL_INDICATOR_A + ord(ch) - ord("A")) for ch in code)


# Exactly the constants Abg 2.3.1 pulls out of pyrogram.emoji.
FLAG_BELARUS = _flag("BY")
FLAG_CHINA = _flag("CN")
FLAG_FRANCE = _flag("FR")
FLAG_GERMANY = _flag("DE")
FLAG_INDONESIA = _flag("ID")
FLAG_ITALY = _flag("IT")
FLAG_RUSSIA = _flag("RU")
FLAG_SOUTH_KOREA = _flag("KR")
FLAG_SPAIN = _flag("ES")
FLAG_TURKEY = _flag("TR")
FLAG_UKRAINE = _flag("UA")
FLAG_UNITED_KINGDOM = _flag("GB")
FLAG_UZBEKISTAN = _flag("UZ")

__all__ = [
    "FLAG_BELARUS",
    "FLAG_CHINA",
    "FLAG_FRANCE",
    "FLAG_GERMANY",
    "FLAG_INDONESIA",
    "FLAG_ITALY",
    "FLAG_RUSSIA",
    "FLAG_SOUTH_KOREA",
    "FLAG_SPAIN",
    "FLAG_TURKEY",
    "FLAG_UKRAINE",
    "FLAG_UNITED_KINGDOM",
    "FLAG_UZBEKISTAN",
]
