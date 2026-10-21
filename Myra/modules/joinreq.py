"""
Group Request Controller — Telegram update handlers.

* ``ChatJoinRequest`` → DM the user a verification card with the must-join list.
* ``jr_v:<session_id>`` → the VERIFY button; re-checks membership and approves.
  Old cards carried ``jr_v:<chat_id>`` — that still works.
* ``jr_ok:<session_id>`` / ``jr_no:<session_id>`` → the admin's Approve/Decline
  buttons for **manual** requirements (bot referral links etc.), which a bot
  cannot membership-check.

Logic lives in :mod:`Myra.services.joinreq`; this module only wires it to
Telegram. Handlers here are copied to every clone by
:mod:`Myra.services.clones` (``ChatJoinRequestHandler`` and
``CallbackQueryHandler`` are both supported by the copier).
"""

from pyrogram import filters
from pyrogram.types import CallbackQuery, ChatJoinRequest

from Myra import LOGGER, Myra
from Myra.database.scope import global_scope
from Myra.services.joinreq import admin_decision, on_join_request, verify_and_approve


@Myra.on_chat_join_request()
@global_scope
async def _join_request(client, request: ChatJoinRequest):
    try:
        await on_join_request(client, request)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq handler error: {exc}", exc_info=True)


@Myra.on_callback_query(filters.regex(r"^jr_(?:v|ok|no):[\w-]+$"))
@global_scope
async def _verify_callback(client, query: CallbackQuery):
    try:
        action, payload = query.data.split(":", 1)
    except Exception:  # noqa: BLE001
        return await query.answer("Kuch galat ho gaya 😥", show_alert=True)

    try:
        if action == "jr_v":
            await verify_and_approve(client, query, payload)
        else:
            await admin_decision(client, query, payload, approve=(action == "jr_ok"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(f"joinreq verify error: {exc}", exc_info=True)
        try:
            await query.answer("Kuch gadbad ho gayi 😥 dobara try karo", show_alert=True)
        except Exception:  # noqa: BLE001
            pass
