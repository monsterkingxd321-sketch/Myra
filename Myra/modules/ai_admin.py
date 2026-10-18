"""
AI / database administration commands.

    /speed [ms]                  → userbot reply delay (global + every userbot)
    /chance [group|private] [%]  → how often replies are sent (human-like)
    /addkey [provider] <key> [label|base_url]
                                 → add a Gemini or OpenAI key (unlimited, validated)
    /delkey [provider] <masked|index>
                                 → remove a stored key
    /aistats                     → hybrid brain status (both providers)
    /aikeys                      → per-key rotation status (both providers)
    /aireload                    → reload keys from .env + file + MongoDB
    /clean_db                    → purge links / promo text from the training DB

The admin surface is global: these commands always act on the ONE shared
database, no matter which bot (main or clone) received the command.

Only the main Bot Owner and the admins the owner added can use these.
"""

from pyrogram.types import Message

from Myra import Myra, LOGGER
from Myra.database.admins import is_admin
from Myra.database import training, settings, ai_keys
from Myra.userbot.storage import accounts as ub_accounts
from Myra.userbot.manager import userbot_manager
from Myra.ai.brain import ai_status, provider_order
from Myra.ai.key_rotator import mask_key
from Myra.database.scope import global_scope


def _my_accounts() -> dict:
    """Every userbot account in the shared pool (global, not per-clone)."""
    return dict(ub_accounts)


async def _require_admin(m: Message) -> bool:
    if await is_admin(m.from_user.id):
        return True
    await m.reply(
        "❌ You are not authorized.\n"
        "Only the Bot Owner and authorized Admins can use this command."
    )
    return False


# ── /speed [ms] ────────────────────────────────────────────────────────────
@Myra.on_cmd("speed")
@global_scope
async def speed_cmd(client: Myra, m: Message):
    """Control the Userbot reply delay. `/speed 1500` = 1.5s, `/speed 0` = instant."""
    if not await _require_admin(m):
        return

    my_accounts = _my_accounts()

    args = list(m.command[1:]) if m.command else []
    current_ms = await settings.get_reply_delay()

    # No argument → show current configuration
    if not args:
        lines = [
            "⚡ **USERBOT SPEED CONTROL**",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🌐 **Global delay:** `{current_ms} ms` ({current_ms / 1000:g}s)",
            f"🎲 **Group chance:** `{await settings.get_group_chance():g}%`",
            f"🎲 **DM chance:** `{await settings.get_private_chance():g}%`",
            "",
        ]
        if my_accounts:
            lines.append(f"🤖 **Connected userbots:** `{len(my_accounts)}`")
            for acc_id, acc in list(my_accounts.items())[:20]:
                per_bot = acc.get("reply_delay")
                label = "global" if per_bot is None else f"{per_bot / 1000:g}s"
                lines.append(f"• `{acc.get('name') or acc_id}` → {label}")
        else:
            lines.append("🤖 *No userbots connected.*")
        lines += [
            "",
            "**Usage:** `/speed <milliseconds>`",
            "Example: `/speed 1500` (1.5s) · `/speed 0` (instant)",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        return await m.reply("\n".join(lines))

    value = args[0].strip().lower()
    if not value.isdigit():
        return await m.reply("❌ Please provide a delay in milliseconds, e.g. `/speed 1500`.")

    delay_ms = int(value)
    if delay_ms > 300000:
        return await m.reply("❌ Maximum delay is `300000` ms (300 seconds).")

    await settings.set_reply_delay(delay_ms)

    applied = 0
    for account_id in list(my_accounts.keys()):
        try:
            await userbot_manager.set_speed(account_id, delay=delay_ms, mode="custom")
            applied += 1
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"⚠️ Failed to set speed for {account_id}: {exc}")

    await m.reply(
        "✅ **Speed updated!**\n\n"
        f"⚡ **New reply delay:** `{delay_ms} ms` ({delay_ms / 1000:g}s)\n"
        f"🤖 **Userbots updated:** `{applied}`\n\n"
        "This delay applies to the AutoChat reply of every userbot."
    )


# ── /chance [group|private] [%] ────────────────────────────────────────────
@Myra.on_cmd(["chance", "replychance"])
@global_scope
async def chance_cmd(client: Myra, m: Message):
    """Control how often the userbots reply (human-like). `/chance 70`."""
    if not await _require_admin(m):
        return

    args = list(m.command[1:]) if m.command else []

    if not args:
        return await m.reply(
            "🎲 **REPLY CHANCE**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💬 **Group chats:** `{await settings.get_group_chance():g}%`\n"
            f"✉️ **DM (private):** `{await settings.get_private_chance():g}%`\n\n"
            "100% = reply to every message · lower = reply sometimes "
            "(more human, less spammy).\n\n"
            "**Usage:**\n"
            "`/chance 70` — group chance 70%\n"
            "`/chance private 100` — DM chance 100%\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )

    scope = "group"
    if args[0].lower() in ("group", "grp", "gc"):
        scope, args = "group", args[1:]
    elif args[0].lower() in ("private", "dm", "pm"):
        scope, args = "private", args[1:]

    if not args:
        return await m.reply("❌ Usage: `/chance [group|private] <0-100>`")

    try:
        value = float(args[0].replace("%", "").strip())
    except Exception:
        return await m.reply("❌ Please give a number between 0 and 100, e.g. `/chance 70`.")

    if value < 0 or value > 100:
        return await m.reply("❌ Chance must be between `0` and `100`.")

    if scope == "group":
        await settings.set_group_chance(value)
        label = "Group chats"
    else:
        await settings.set_private_chance(value)
        label = "DM (private)"

    await m.reply(f"✅ **{label} reply chance set to `{value:g}%`**")


# ── /addkey ────────────────────────────────────────────────────────────────
@Myra.on_cmd(["addkey", "setkey"])
@global_scope
async def addkey_cmd(client: Myra, m: Message):
    """
    Add an AI API key. Works for **Gemini** and **OpenAI / ChatGPT**.

    Usage::
        /addkey <key> [label]                  → provider auto-detected
        /addkey gemini <key> [label]           → force Gemini
        /addkey openai <key> [base_url] [label]→ force OpenAI (optional custom endpoint)
    """
    if not await _require_admin(m):
        return

    args = list(m.command[1:]) if m.command else []
    if not args:
        return await m.reply(
            "Add an API key\n\n"
            "Usage:\n"
            "/addkey <key> [label]  (provider auto-detected)\n"
            "/addkey gemini <key> [label]\n"
            "/addkey openai <key> [base_url] [label]\n\n"
            "You can add as many keys as you want.\n\n"
            "Gemini free keys: https://aistudio.google.com/apikey\n"
            "OpenAI / any OpenAI-compatible key also works."
        )

    provider = None
    if args[0].strip().lower() in ("gemini", "google", "openai", "chatgpt", "gpt"):
        provider = ai_keys.normalize_provider(args.pop(0))

    if not args:
        return await m.reply("Please provide the key value.")

    key = args[0].strip()
    rest = list(args[1:])
    if provider is None:
        provider = ai_keys.guess_provider(key)

    # Optional base URL for OpenAI-compatible proxies (OpenRouter, Groq, …)
    base_url = ""
    if provider == ai_keys.OPENAI and rest and rest[0].lower().startswith(("http://", "https://")):
        base_url = rest.pop(0).strip()

    label = " ".join(rest)[:40]

    # Remove the message so the raw key is not left in the chat history.
    try:
        await m.delete()
    except Exception:  # noqa: BLE001
        pass

    from Myra.ai.brain import PROVIDERS

    service = PROVIDERS.get(provider)
    if service is None:
        return await m.reply(f"Unknown provider: {provider}")

    status = await m.reply(f"Validating {provider} key `{mask_key(key)}` ...")
    if base_url:
        ok, message = await service.validate_key(key, base_url=base_url)
    else:
        ok, message = await service.validate_key(key)
    if not ok:
        return await status.edit_text(f"Key rejected: {message}\n\nNot saved.")

    if not await ai_keys.add_key(
        key,
        added_by=m.from_user.id,
        label=label,
        provider=provider,
        base_url=base_url,
    ):
        return await status.edit_text("Could not save the key to MongoDB.")

    info = await service.refresh_keys()
    await status.edit_text(
        "API key added\n"
        "-------------------------\n"
        f"Provider: {provider}\n"
        f"Key: {mask_key(key)}\n"
        f"Label: {label or '-'}\n"
        + (f"Endpoint: {base_url}\n" if base_url else "")
        + f"Validation: {message}\n\n"
        f"Keys in {provider} pool: {info.get('total')} "
        f"(env {info.get('env_keys', 0)} / file {info.get('file_keys', 0)} / db {info.get('db_keys', 0)})\n"
        f"Active now: {info.get('active')}\n"
        "-------------------------\n"
        "Use /aikeys to see all keys."
    )


# ── /delkey ────────────────────────────────────────────────────────────────
@Myra.on_cmd(["delkey", "rmkey"])
@global_scope
async def delkey_cmd(client: Myra, m: Message):
    """Remove a stored API key by its masked value or by list index."""
    if not await _require_admin(m):
        return

    args = list(m.command[1:]) if m.command else []
    if not args:
        return await m.reply(
            "Remove an API key\n\n"
            "Usage: /delkey <masked-key> or /delkey <index>\n"
            "You can prefix the provider: /delkey openai 2\n"
            "Example: /delkey AIzaSy...7890  or  /delkey 2"
        )

    provider = None
    if args[0].strip().lower() in ("gemini", "google", "openai", "chatgpt", "gpt"):
        provider = ai_keys.normalize_provider(args.pop(0))
    if not args:
        return await m.reply("Please provide the masked key or the index.")

    arg = args[0].strip()
    if arg.isdigit():
        removed = await ai_keys.remove_key_by_index(int(arg), provider=provider)
    else:
        removed = await ai_keys.remove_key_by_mask(arg, provider=provider) \
            or await ai_keys.remove_key_by_value(arg)

    if not removed:
        return await m.reply(
            "Key not found in MongoDB.\n"
            "Note: keys added via .env must be removed from .env directly."
        )

    from Myra.ai.brain import PROVIDERS

    lines = [f"Key removed: {mask_key(removed)}", ""]
    for name in provider_order():
        info = await PROVIDERS[name].refresh_keys()
        lines.append(f"{name}: {info.get('active')}/{info.get('total')} active")
    await m.reply("\n".join(lines))


# ── /clean_db ──────────────────────────────────────────────────────────────
@Myra.on_cmd(["clean_db", "cleandb"])
@global_scope
async def clean_db_cmd(client: Myra, m: Message):
    """Scan the training collection and purge links / usernames / promo text."""
    if not await _require_admin(m):
        return

    if training.TRAINING is None:
        return await m.reply("❌ MongoDB is not connected. Cannot clean the database.")

    status = await m.reply("🧹 **Scanning the training database...**\nThis may take a moment for large collections.")

    try:
        report = await training.clean_database()
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(f"⚠️ clean_db failed: {exc}")
        return await status.edit_text(f"❌ **Database clean failed:**\n`{exc}`")

    reasons = report.get("reasons") or {}
    reason_text = "\n".join(
        f"• `{name}` → {count}" for name, count in sorted(reasons.items(), key=lambda x: -x[1])
    ) or "• none"

    await status.edit_text(
        "🧹 **DATABASE CLEANED**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 **Scanned entries:** `{report.get('scanned', 0)}`\n"
        f"🗑 **Deleted entries:** `{report.get('deleted', 0)}`\n\n"
        "**Reasons:**\n"
        f"{reason_text}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "The bot will no longer repeat any link or promotional reply."
    )


# ── /aistats ───────────────────────────────────────────────────────────────
@Myra.on_cmd(["aistats", "ai"])
@global_scope
async def aistats_cmd(client: Myra, m: Message):
    """Show the hybrid brain + API key status."""
    if not await _require_admin(m):
        return

    status = ai_status()
    db = await training.get_stats()

    key_lines = []
    for item in status.get("details", []):
        icon = "🟢" if item["status"] == "active" else "🔴"
        key_lines.append(
            f"{icon} [{item.get('provider', '?')}] `{item['key']}` — "
            f"{item['status']} (uses: {item['uses']})"
        )
    key_text = "\n".join(key_lines) or "• *No API keys configured (offline mode)*"

    provider_lines = []
    for name, info in (status.get("providers") or {}).items():
        provider_lines.append(
            f"• **{name}** → `{info.get('active')}/{info.get('total')}` active · "
            f"model `{info.get('model')}`"
        )
    provider_text = "\n".join(provider_lines) or "• *none*"

    await m.reply(
        "🧠 **HYBRID AI STATUS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 **AI enabled:** `{status.get('enabled')}`\n"
        f"📚 **DB-first search:** `{status.get('db_first')}`\n"
        f"🎓 **Auto-train:** `{status.get('auto_train')}`\n"
        f"🎯 **Match threshold:** `{status.get('threshold')}`\n"
        f"🔀 **Provider order:** `{' → '.join(status.get('order') or [])}`\n\n"
        "**Providers:**\n"
        f"{provider_text}\n\n"
        f"🔑 **API keys:** `{status.get('active')}/{status.get('total')}` active\n"
        f"🎲 **Chance:** group `{await settings.get_group_chance():g}%` · "
        f"dm `{await settings.get_private_chance():g}%`\n\n"
        "**Keys:**\n"
        f"{key_text}\n\n"
        "**Training DB:**\n"
        f"📦 Total replies: `{db.get('total', 0)}`\n"
        f"💬 Text replies: `{db.get('texts', 0)}`\n"
        f"🎨 Stickers: `{db.get('stickers', 0)}`\n"
        f"💾 Collection size: `{training.human_bytes(db.get('collection_bytes', 0))}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )


# ── /aikeys ────────────────────────────────────────────────────────────────
@Myra.on_cmd(["aikeys", "keys"])
@global_scope
async def aikeys_cmd(client: Myra, m: Message):
    """List each API key (Gemini + OpenAI) and its rotation state."""
    if not await _require_admin(m):
        return

    status = ai_status()
    db_count = await ai_keys.count_keys()

    lines = [
        "🔑 **API KEY ROTATION**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 **Active:** `{status.get('active')}` / `{status.get('total')}`",
        f"🗄 From DB: `{db_count}`",
        "",
    ]

    providers = status.get("providers") or {}
    for name in provider_order():
        info = providers.get(name) or {}
        lines.append(
            f"**{name.upper()}** — `{info.get('active')}`/`{info.get('total')}` active · "
            f"model `{info.get('model')}`"
        )
        rows = [it for it in status.get("details", []) if it.get("provider") == name]
        if not rows:
            lines.append("  • _no keys_")
        for index, item in enumerate(rows, 1):
            icon = "🟢" if item["status"] == "active" else "🔴"
            lines.append(
                f"  {index}. {icon} `{item['key']}`\n"
                f"      ↳ {item['status']} · uses `{item['uses']}` · fails `{item['failures']}`"
            )
        lines.append("")

    if not status.get("details"):
        lines.append("ℹ️ *No API keys configured.* The bot runs in database-only offline mode.")
    lines += [
        "➕ `/addkey <key>` — add unlimited keys (Gemini or OpenAI)",
        "🗑 `/delkey [provider] <masked|index>` — remove a key",
        "🔄 `/aireload` — reload `.env` + file + DB keys",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    await m.reply("\n".join(lines))


# ── /aireload ──────────────────────────────────────────────────────────────
@Myra.on_cmd(["aireload", "reloadkeys"])
@global_scope
async def aireload_cmd(client: Myra, m: Message):
    """Reload keys from .env + key file + MongoDB for every provider."""
    if not await _require_admin(m):
        return

    from Myra.ai.brain import PROVIDERS

    blocks = []
    for name in provider_order():
        info = await PROVIDERS[name].refresh_keys()
        detail_lines = []
        for item in info.get("details", []):
            icon = "🟢" if item["status"] == "active" else "🔴"
            detail_lines.append(f"{icon} `{item['key']}` — {item['status']}")
        detail = "\n".join(detail_lines) or "• *No keys found*"
        blocks.append(
            f"**{name.upper()}** — `{info.get('active')}`/`{info.get('total')}` active "
            f"(env `{info.get('env_keys', 0)}` · file `{info.get('file_keys', 0)}` · "
            f"db `{info.get('db_keys', 0)}`)\n"
            f"✨ Model: `{info.get('model')}`\n{detail}"
        )

    await m.reply(
        "🔄 **API keys reloaded**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        + "\n\n".join(blocks)
        + "\n━━━━━━━━━━━━━━━━━━━━━━\nNo restart required."
    )


__all__ = [
    "speed_cmd",
    "chance_cmd",
    "addkey_cmd",
    "delkey_cmd",
    "clean_db_cmd",
    "aistats_cmd",
    "aikeys_cmd",
    "aireload_cmd",
]
