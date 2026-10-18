from Myra import bot_mongo

# ``MYRADb.MYRA`` = chats where the chatbot is toggled ON/OFF. That is bot
# state, not training data, so it lives on the bot cluster.
MYRA = bot_mongo["MYRADb"]["MYRA"] if bot_mongo is not None else None

from .chats import *
from .users import *
from .admins import *
