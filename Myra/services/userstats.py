"""
Audience statistics — how many groups and DM chats the bot and each userbot have.

Used by the ``/admin`` panel (📊 Stats) and available to any other module.

The numbers come from the same caches the broadcast engine uses, so opening the
panel is cheap: the main bot's lists are read from MongoDB and a userbot's lists
from its dialog cache (refreshed at most every 30 minutes, or on demand with
``force=True``).
"""

import time

from Myra import LOGGER
from Myra.database.scope import current_scope, owns_scope
from Myra.services.broadcast import bot_targets, userbot_dialogs


async def collect(force: bool = False, scope: str = None) -> dict:
    """
    Build the full audience report for one bot (``scope``).

    ``force=True`` re-reads every connected userbot's dialog list (slow).
    """
    scope = scope or current_scope()
    report = {
        "time": time.time(),
        "scope": scope,
        "bot": {"groups": 0, "dm": 0},
        "userbots": {},
        "totals": {
            "bot_groups": 0,
            "bot_dm": 0,
            "userbot_groups": 0,
            "userbot_dm": 0,
            "unique_groups": 0,
            "unique_dm": 0,
            "userbots": 0,
            "connected": 0,
        },
    }

    bot_group_ids, bot_dm_ids = set(), set()
    try:
        bot_group_ids = set(await bot_targets("groups", scope))
        bot_dm_ids = set(await bot_targets("dm", scope))
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug(f"userstats: bot targets failed: {exc}")

    report["bot"]["groups"] = len(bot_group_ids)
    report["bot"]["dm"] = len(bot_dm_ids)

    from Myra.userbot.storage import accounts as ub_accounts

    all_groups = set(bot_group_ids)
    all_dm = set(bot_dm_ids)

    for account_id, data in list(ub_accounts.items()):
        if not owns_scope(data.get("scope"), scope):
            continue
        entry = {
            "name": data.get("name") or account_id,
            "username": data.get("username") or "",
            "status": data.get("status") or "UNKNOWN",
            "groups": 0,
            "dm": 0,
            "error": "",
        }
        client = data.get("client")
        if client is not None and entry["status"] == "CONNECTED":
            try:
                dialogs = await userbot_dialogs(client, account_id, force=force)
                entry["groups"] = len(dialogs.get("groups", []))
                entry["dm"] = len(dialogs.get("dm", []))
                all_groups |= set(dialogs.get("groups", []))
                all_dm |= set(dialogs.get("dm", []))
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}"
                LOGGER.debug(f"userstats: dialogs for {account_id} failed: {exc}")
        report["userbots"][account_id] = entry

    report["totals"] = {
        "bot_groups": report["bot"]["groups"],
        "bot_dm": report["bot"]["dm"],
        "userbot_groups": sum(e["groups"] for e in report["userbots"].values()),
        "userbot_dm": sum(e["dm"] for e in report["userbots"].values()),
        "unique_groups": len(all_groups),
        "unique_dm": len(all_dm),
        "userbots": len(report["userbots"]),
        "connected": sum(
            1 for e in report["userbots"].values() if e["status"] == "CONNECTED"
        ),
    }
    return report


def render(report: dict) -> str:
    """Markdown block for the panel / /stats command."""
    totals = report.get("totals", {})
    bot = report.get("bot", {})
    userbots = report.get("userbots", {})

    from Myra.database.bots import bot_info

    info = bot_info(report.get("scope"))
    label = info.get("name") or "Main Bot"
    if info.get("username"):
        label = f"{label} (@{info['username']})"

    lines = [
        "👤 **AUDIENCE / CHAT STATS**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🤖 **{label}**",
        f"   ➻ 💬 Groups: `{bot.get('groups', 0)}`",
        f"   ➻ ✉️ DM users: `{bot.get('dm', 0)}`",
    ]

    if userbots:
        lines.append("")
        lines.append(f"👥 **Userbots** — `{totals.get('connected', 0)}`/`{totals.get('userbots', 0)}` connected")
        for entry in sorted(userbots.values(), key=lambda e: -(e.get("dm", 0))):
            icon = "🟢" if entry.get("status") == "CONNECTED" else "🔴"
            label = entry.get("name") or "—"
            if entry.get("username"):
                label = f"{label} (@{entry['username']})"
            line = f"   {icon} **{label}** · 💬 `{entry.get('groups', 0)}` · ✉️ `{entry.get('dm', 0)}`"
            if entry.get("error"):
                line += f" ⚠️ `{entry['error']}`"
            lines.append(line)
    else:
        lines.append("")
        lines.append("👥 **Userbots:** `0` — koi add nahi hua abhi")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🧮 **Totals**",
        f"   ➻ 💬 Groups (unique): `{totals.get('unique_groups', 0)}`",
        f"   ➻ ✉️ DM users (unique): `{totals.get('unique_dm', 0)}`",
        f"   ➻ ✉️ DM (bot): `{totals.get('bot_dm', 0)}` · ✉️ DM (userbots): `{totals.get('userbot_dm', 0)}`",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


__all__ = ["collect", "render"]
