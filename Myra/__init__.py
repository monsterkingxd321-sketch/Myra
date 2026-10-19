import sys
import logging 
import time
import importlib
import importlib.util

# ── Abg ⇄ Kurigram compatibility shim (MUST run before `from Abg import patch`) ─
# Abg 2.3.1's `inline` package begins with `from pyrogram.emoji import *` and then
# uses thirteen `FLAG_*` constants from it. `pyrogram/emoji.py` ships with stock
# Pyrogram, but Kurigram — the maintained fork we have to use so Telegram stops
# rejecting userbot logins with `406 UPDATE_APP_TO_LOGIN` — dropped that module.
# On a *clean* install (Docker image, fresh VPS venv) the import below therefore
# dies with:
#
#     ModuleNotFoundError: No module named 'pyrogram.emoji'
#
# A machine that once had stock Pyrogram installed still has the leftover file,
# which is why the crash only ever showed up in the container.
# `Myra/_compat/pyrogram_emoji.py` re-creates the handful of constants Abg needs;
# when the real module is present we leave everything exactly as it was.
if importlib.util.find_spec("pyrogram.emoji") is None:
    import pyrogram as _pyrogram

    _emoji_shim = importlib.import_module("Myra._compat.pyrogram_emoji")
    sys.modules["pyrogram.emoji"] = _emoji_shim
    setattr(_pyrogram, "emoji", _emoji_shim)

from Abg import patch

from motor.motor_asyncio import AsyncIOMotorClient as MongoCli
import pyrogram.utils
from pyrogram import Client
from pyrogram.enums import ParseMode

# Patch Pyrogram for 64-bit Telegram Channel IDs (> 2 billion)
pyrogram.utils.MIN_CHANNEL_ID = -1009999999999
pyrogram.utils.MIN_CHAT_ID = -999999999999

import config

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[
        logging.FileHandler("log.txt", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    level=logging.INFO,
)

logging.getLogger("pyrogram").setLevel(logging.ERROR)

# Third-party SDKs log every HTTP attempt at INFO ("HTTP Request POST … 429",
# "Retrying request …"), which looks like a wall of errors while the bot is
# simply rotating keys. Keep only their warnings and errors.
for _noisy in (
    "httpx",
    "httpcore",
    "openai",
    "openai._base_client",
    "google_genai",
    "google",
    "google.auth",
    "urllib3",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

LOGGER = logging.getLogger(__name__)
boot = time.time()

# ── MongoDB: two separate clusters ─────────────────────────────────────────
#   mongo      → CHAT/TRAINING brain (``Word.WordDb`` only)
#   bot_mongo  → everything the bot owns (``Anonymous``, ``UserbotDB``,
#                ``SessionDB``, ``BotDB``, ``MYRADb``)
#   db         → handle on the bot cluster used by ``database/chats.py`` and
#                ``database/users.py`` (kept for backwards compatibility)
mongo = None
bot_mongo = None
db = None


def _plain_url(url: str) -> str:
    """A Mongo URI without the credentials, for logs."""
    url = url or ""
    head, sep, tail = url.partition("@")
    if sep:
        scheme, _, _ = head.partition("//")
        return f"{scheme}//***@{tail}"
    return url or "(empty)"


if not config.MONGO_URL:
    LOGGER.warning("⚠️ MONGO_URL environment variable is missing! Chatbot training will not work.")
else:
    mongo = MongoCli(config.MONGO_URL)

bot_url = getattr(config, "BOT_MONGO_URL", "") or ""
if not bot_url:
    LOGGER.warning("⚠️ BOT_MONGO_URL is empty — bot data will use MONGO_URL.")
    bot_url = config.MONGO_URL

if bot_url:
    if mongo is not None and bot_url == config.MONGO_URL:
        # single-cluster setup: reuse the same client, open a second one only
        # when the user actually pointed the bot data somewhere else.
        bot_mongo = mongo
    else:
        bot_mongo = MongoCli(bot_url)
    db = bot_mongo["Anonymous"]
else:
    LOGGER.warning("⚠️ No MongoDB URL configured — bot data (users, admins, sessions) is disabled.")

if mongo is not None and bot_mongo is not None:
    if mongo is bot_mongo:
        LOGGER.info(f"🍃 MongoDB: chat + bot data share one cluster ({_plain_url(config.MONGO_URL)})")
    else:
        LOGGER.info(f"🍃 MongoDB (chat/training): {_plain_url(config.MONGO_URL)}")
        LOGGER.info(f"🍃 MongoDB (bot data):     {_plain_url(bot_url)}")

OWNER = int(config.OWNER_ID) if config.OWNER_ID else None



class Myra(Client):
    def __init__(self):
        super().__init__(
            name="Myra",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            lang_code="en",
            bot_token=config.BOT_TOKEN,
            in_memory=True,
            parse_mode=ParseMode.DEFAULT,
        )
        self.id = None
        self.name = "Myra"
        self.username = None
        self.mention = ""

    async def start(self):
        await super().start()
        self.id = self.me.id
        self.name = self.me.first_name + " " + (self.me.last_name or "")
        self.username = self.me.username
        self.mention = self.me.mention

    async def stop(self):
        await super().stop()


Myra = Myra()
