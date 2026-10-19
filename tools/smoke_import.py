"""Smoke test: import the whole bot the same way `python -m Myra` does.

This exists because "it works on my machine" is not good enough for a container:
a local venv can silently keep *leftover files from an older package* (for
example stock Pyrogram's `pyrogram/emoji.py`, which Kurigram dropped, while Abg
imports it), so code that only works because of those leftovers explodes on a
clean install.

Run it in the Docker build (and any time you like)::

    python tools/smoke_import.py

It imports the client, every module and the userbot manager — exactly the import
graph that runs before Telegram is contacted — and exits non-zero on the first
problem. No network, no database writes and no credentials are required.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import traceback

# Running this as `python tools/smoke_import.py` puts `tools/` (not the repo
# root) on sys.path, so `import Myra` / `import config` would fail. Add the repo
# root explicitly — the Dockerfile runs this from /app too.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

FAILED: list[str] = []


def step(label: str, func) -> None:  # noqa: ANN001
    try:
        result = func()
    except Exception as exc:  # noqa: BLE001
        FAILED.append(f"{label}: {type(exc).__name__}: {exc}")
        print(f"  FAIL {label}")
        traceback.print_exc()
        return
    print(f"  OK   {label}{f' -> {result}' if result else ''}")


def main() -> int:
    print("[1/5] third-party imports")

    def _pyrogram_layer():
        import pyrogram

        from pyrogram import raw

        return f"v{pyrogram.__version__} / layer {raw.all.layer}"

    step("pyrogram (kurigram)", _pyrogram_layer)

    print("[2/5] config")
    step("config", lambda: f"AI_ENABLE={__import__('config').AI_ENABLE}")

    # Importing Myra installs the `pyrogram.emoji` compatibility shim (see
    # Myra/__init__.py) *before* pulling in Abg — so this must come first.
    print("[3/5] Myra package")

    def _client():
        from Myra import Myra

        return type(Myra).__name__

    step("Myra client", _client)

    def _abg():
        # `Abg.inline` is what needs `pyrogram.emoji`; on Railway this very
        # import failed with ModuleNotFoundError.
        from Abg import patch  # noqa: F401
        from Abg.chat_status import adminsOnly  # noqa: F401
        from Abg.inline import InlineKeyboard  # noqa: F401

        # Check the constants Abg's star-import actually needs, read straight
        # from Abg's source so this cannot drift.
        import inspect

        import pyrogram.emoji as pyrogram_emoji

        from Abg.inline import inline_keyboard

        needed = sorted(set(re.findall(r"\bFLAG_[A-Z_]+\b", inspect.getsource(inline_keyboard))))
        missing = [name for name in needed if not getattr(pyrogram_emoji, name, None)]
        if missing:
            raise RuntimeError(f"pyrogram.emoji is missing {missing}")
        return f"{len(needed)} flags via {pyrogram_emoji.__name__}"

    step("Abg (patch, chat_status, inline)", _abg)

    print("[4/5] Myra modules")

    def _modules():
        from Myra.modules import ALL_MODULES

        for name in ALL_MODULES:
            importlib.import_module("Myra.modules." + name)
        return f"{len(ALL_MODULES)} modules"

    step("ALL_MODULES", _modules)

    print("[5/5] userbot + services")

    def _services():
        from Myra.services import clones, joinreq  # noqa: F401
        from Myra.userbot import userbot_manager  # noqa: F401

        return "clones + joinreq + userbot_manager"

    step("services & userbot manager", _services)

    print()
    if FAILED:
        print("SMOKE IMPORT FAILED:")
        for item in FAILED:
            print("  -", item)
        return 1

    print("SMOKE IMPORT PASSED - the app can be imported on a clean install.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
