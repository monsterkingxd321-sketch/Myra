"""
Group Request Controller — Telegram update handlers.

* ``ChatJoinRequest`` → DM the user a welcome card with the must-join list.
* ``jr_v:<chat_id>``  → the VERIFY button; re-checks membership and approves.

Logic lives in :mod:`Myra.services.joinreq`; this module only wires it to
Telegram. Handlers here are copied to every clone by
:mod:`Myra.services.clones` (``ChatJoinRequestHandler`` and
``CallbackQueryHandler`` are both supported by the copier).
"""

from pyrogram import filters
from pyrogram.types import CallbackQuery, ChatJoinRequest

from Myra import LOGGER, Myra
from Myra.database.scope import global_scope
from Myra.services.joinreq import on_join_request, verify_and_approve


@Myra.on_chat_join_request()
@global_scope
async def _join_request(client, request: ChatJoinRequest):
    try:
        await on_join_request(client, request)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq handler error: {exc}")


@Myra.on_callback_query(filters.regex(r"^jr_v:-?\d+$"))
@global_scope
async def _verify_callback(client, query: CallbackQuery):
    try:
        chat_id = int(query.data.split(":", 1)[1])
    except Exception:  # noqa: BLE001
        return await query.answer("Kuch galat ho gaya 😥", show_alert=True)

    try:
        await verify_and_approve(client, query, chat_id)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq verify error: {exc}")
        try:
            await query.answer("Kuch gadbad ho gayi 😥 dobara try karo", show_alert=True)
        except Exception:  # noqa: BLE001
            pass
