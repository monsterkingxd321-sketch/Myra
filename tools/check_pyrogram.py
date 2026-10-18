"""Build-time gate: is the installed ``pyrogram`` package really Kurigram?

Why this exists
---------------
``kurigram`` is a maintained Pyrogram fork that speaks a **current** Telegram API
layer. Plain ``pyrogram`` (2.0.x) speaks layer 151/158, and Telegram answers
every user login with::

    406 UPDATE_APP_TO_LOGIN

…which makes the whole "Add Userbot" phone/OTP flow impossible. Because Kurigram
installs *as the ``pyrogram`` package*, a stray ``pip install pyrogram`` (a
transitive dependency, a typo, or "just upgrade everything") silently replaces it
and breaks userbots — with no error until somebody tries to log in.

This script turns that silent breakage into a **failed Docker build**.

Run it manually any time::

    python tools/check_pyrogram.py

Exit code 0 = safe, 1 = the image would ship a bot that cannot add userbots.
"""

from __future__ import annotations

import sys

MIN_LAYER = 200  # Kurigram 2.2.x speaks layer 227; plain pyrogram = 151/158.


def main() -> int:
    try:
        import pyrogram
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: pyrogram is not importable: {exc}", file=sys.stderr)
        return 1

    version = str(getattr(pyrogram, "__version__", "unknown"))
    location = getattr(pyrogram, "__file__", "unknown")

    try:
        # 'UpdateAppToLogin' only exists in the modern fork. Importing it proves
        # the current error map is in place, which is exactly what the login flow
        # needs in order to fail gracefully instead of writing unknown_errors.txt.
        from pyrogram.errors import UpdateAppToLogin  # noqa: F401

        error_map = True
    except Exception:  # noqa: BLE001
        error_map = False

    try:
        from pyrogram import raw

        layer = int(getattr(raw.all, "layer", 0) or 0)
    except Exception:  # noqa: BLE001
        layer = 0

    print(f"pyrogram package : {location}")
    print(f"pyrogram version : {version}")
    print(f"telegram layer   : {layer or 'unknown'}")
    print(f"UpdateAppToLogin : {'mapped' if error_map else 'MISSING'}")

    if layer and layer < MIN_LAYER:
        print(
            f"FATAL: API layer {layer} is too old (need >= {MIN_LAYER}).\n"
            "       Telegram will reject every userbot login with\n"
            "       `406 UPDATE_APP_TO_LOGIN`. Install kurigram==2.2.24 and make\n"
            "       sure nothing installs plain `pyrogram` on top of it.",
            file=sys.stderr,
        )
        return 1

    if not error_map:
        print(
            "FATAL: this looks like plain `pyrogram`, not Kurigram.\n"
            "       Fix: pip install --force-reinstall --no-deps kurigram==2.2.24",
            file=sys.stderr,
        )
        return 1

    print("OK: Kurigram is in place — userbot phone/OTP logins will work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
