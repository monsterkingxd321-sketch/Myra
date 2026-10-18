from pyrogram import Client, filters
from pyrogram.types import Message
from Myra import Myra  # Your bot instance
import random


# ---------------- GAMES ---------------- #

async def play_dice(message: Message, emoji: str, game_name: str):
    # Send dice
    sent = await message.reply_dice(emoji=emoji)
    value = sent.dice.value

    # Prepare fancy Myra-style score message
    score_msg = (
        f"🥀 đøηє 💫\n"
        f"Hey {message.from_user.mention} your {game_name} score is : `{value}`\n"
        f"📝 Sคcσяє: {value}/6"  # Max is 6 for dice, for others adjust if needed
    )

    # Reply with score below the dice
    await message.reply_text(score_msg, quote=True)


@Myra.on_message(filters.command("dice"))
async def dice_game(bot, message: Message):
    await play_dice(message, "🎲", "Dιcє")


@Myra.on_message(filters.command("dart"))
async def dart_game(bot, message: Message):
    await play_dice(message, "🎯", "Dαят")


@Myra.on_message(filters.command("basket"))
async def basket_game(bot, message: Message):
    await play_dice(message, "🏀", "Bαѕкєт Bαℓℓ")


@Myra.on_message(filters.command("ball"))
async def bowling_game(bot, message: Message):
    await play_dice(message, "🎳", "Bσωℓιηg Bαℓℓ")


@Myra.on_message(filters.command("football"))
async def football_game(bot, message: Message):
    await play_dice(message, "⚽", "Fσσтвαℓℓ")


@Myra.on_message(filters.command("jackpot"))
async def jackpot_game(bot, message: Message):
    await play_dice(message, "🎰", "Jα¢кρσт")


# ---------------- HELP & INFO ---------------- #

__help__ = """
Play Game With Emojis:

/dice - Dice 🎲
/dart - Dart 🎯
/basket - Basket Ball 🏀
/ball - Bowling Ball 🎳
/football - Football ⚽
/jackpot - Spin slot machine 🎰

"""

__mod_name__ = "Gαмєѕ 🎮"
