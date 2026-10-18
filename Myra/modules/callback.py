from pyrogram.enums import ChatMemberStatus as CMS, ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup

from Myra import Myra
from Myra.database import MYRA
from Myra.database.scope import scope_field, scope_match
from Myra.modules.helpers import (
    ABOUT_BTN,
    BACK,
    CHATBOT_BACK,
    HELP_BTN,
    MUSIC_BACK_BTN,
    about_text,
    admin_text,
    build_clone_menu_buttons,
    build_start_buttons,
    chatbot_text,
    clone_menu_text,
    help_text,
    source_text,
    start_text,
    tools_text,
)


async def _edit(query: CallbackQuery, text: str, markup=None):
    """``edit_text`` that never throws an entity-bounds error at the user.

    The texts are deliberately plain, but if the local parser ever produced
    invalid bounds the message is retried once with parsing switched off, so
    the card still shows instead of a Telegram error.
    """
    try:
        return await query.message.edit_text(
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except RPCError as exc:
        if "ENTITY" not in str(exc).upper():
            raise
        return await query.message.edit_text(
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
            parse_mode=ParseMode.DISABLED,
        )


@Myra.on_callback_query()
async def cb_handler(_, query: CallbackQuery):
    if query.data == "HELP":
        await _edit(query, help_text(), InlineKeyboardMarkup(HELP_BTN))

    elif query.data == "CLONE_MENU":
        await _edit(query, clone_menu_text(),
                    InlineKeyboardMarkup(build_clone_menu_buttons()))

    elif query.data == "CLONE_GUIDE":
        from Myra.modules.clone import CLONE_GUIDE, guide_markup

        await _edit(query, CLONE_GUIDE, guide_markup())

    elif query.data == "CLONE_MYBOTS":
        from Myra.modules.clone import mybots_text

        try:
            text, markup = await mybots_text(query.from_user.id)
            await _edit(query, text, markup)
            await query.answer()
        except Exception:  # noqa: BLE001
            await query.answer("Something went wrong. Please try again.", show_alert=True)

    elif query.data == "CLOSE":
        await query.message.delete()
        await query.answer("Menu closed.", show_alert=True)

    elif query.data == "BACK":
        await _edit(query, start_text(), InlineKeyboardMarkup(build_start_buttons()))

    elif query.data == "SOURCE":
        await _edit(query, source_text(), InlineKeyboardMarkup(BACK))

    elif query.data == "ABOUT":
        await _edit(query, about_text(), InlineKeyboardMarkup(ABOUT_BTN))

    elif query.data == "ADMINS":
        await _edit(query, admin_text(), InlineKeyboardMarkup(MUSIC_BACK_BTN))

    elif query.data == "TOOLS_DATA":
        await _edit(query, tools_text(), InlineKeyboardMarkup(CHATBOT_BACK))

    elif query.data == "BACK_HELP":
        await _edit(query, help_text(), InlineKeyboardMarkup(HELP_BTN))

    elif query.data == "CHATBOT_CMD":
        await _edit(query, chatbot_text(), InlineKeyboardMarkup(CHATBOT_BACK))

    elif query.data == "CHATBOT_BACK":
        await _edit(query, help_text(), InlineKeyboardMarkup(HELP_BTN))

    elif query.data == "addchat":
        user_id = query.from_user.id
        user_status = (await query.message.chat.get_member(user_id)).status

        if user_status not in [CMS.OWNER, CMS.ADMINISTRATOR]:
            return await query.answer(
                "Only a group admin can do this.",
                show_alert=True,
            )

        if MYRA is None:
            return await query.answer("Database is not connected.", show_alert=True)

        is_MYRA = await MYRA.find_one({"chat_id": query.message.chat.id, **scope_match()})
        if not is_MYRA:
            await query.edit_message_text("Chatbot is already enabled.")
        else:
            await MYRA.delete_one({"chat_id": query.message.chat.id, **scope_match()})
            await query.edit_message_text(
                f"Chatbot enabled by {query.from_user.mention}."
            )

    elif query.data == "rmchat":
        user_id = query.from_user.id
        user_status = (await query.message.chat.get_member(user_id)).status

        if user_status not in [CMS.OWNER, CMS.ADMINISTRATOR]:
            return await query.answer(
                "Only a group admin can do this.",
                show_alert=True,
            )

        if MYRA is None:
            return await query.answer("Database is not connected.", show_alert=True)

        is_MYRA = await MYRA.find_one({"chat_id": query.message.chat.id, **scope_match()})
        if not is_MYRA:
            await MYRA.insert_one({"chat_id": query.message.chat.id, **scope_field()})
            await query.edit_message_text(
                f"Chatbot disabled by {query.from_user.mention}."
            )
        else:
            await query.edit_message_text("Chatbot is already disabled.")

    else:
        query.continue_propagation()
