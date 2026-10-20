import asyncio
import re
import time
from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
    RPCError,
)

from Myra import OWNER, config, LOGGER
from Myra.database.scope import current_scope
from Myra.userbot.storage import (
    USERBOT_ACCOUNTS,
    USERBOT_SESSIONS,
    accounts as ub_accounts,
    add_account,
    save_session,
)

# State machine for login flows
# pending_auth[owner_id] = {
#     "phone": "+91...",
#     "phone_code_hash": "...",
#     "step": "phone" | "otp" | "2fa",
#     "client": tmp_client,
#     "created_at": float,
# }
pending_auth = {}

OTP_TIMEOUT = 300  # 5 minutes
CLEANUP_INTERVAL = 60  # 1 minute

# Telegram refuses to send a login code to clients it considers outdated and
# answers ``406 UPDATE_APP_TO_LOGIN`` instead. That is a *library* problem (old
# API layer), not a phone problem — see the hint text below.
_OLD_APP_MARKERS = ("update_app_to_login", "update app to login")


def explain_login_error(exc) -> str:
    """
    Turn a raw Telegram login error into something a user can act on.

    Keeps the raw message at the end so nothing is hidden, but leads with the
    fix for the errors people actually hit.
    """
    raw = str(exc or "")
    low = raw.lower()

    if any(marker in low for marker in _OLD_APP_MARKERS):
        return (
            "Telegram refused this login: 406 UPDATE_APP_TO_LOGIN\n\n"
            "Iska matlab app/API layer purana hai, phone number galat nahi.\n"
            "Do fixes (koi ek karo):\n"
            "1) Isi chat me `.session` ya `.zip` file bhej do - usme OTP ki "
            "zarurat nahi padti, wahi turant add ho jayega.\n"
            "2) Server pe `pip install -U kurigram` karke bot restart karo, "
            "phir OTP flow chalega.\n\n"
            f"`{raw[:160]}`"
        )
    if "api_id_invalid" in low:
        return (
            "API_ID/API_HASH galat hai. https://my.telegram.org se naya "
            "api_id/api_hash banao aur `.env` me daalo.\n"
            f"`{raw[:160]}`"
        )
    if "phone_number_banned" in low or "phone_number_flood" in low:
        return (
            "Ye phone number Telegram ne block/limit kar diya hai. Doosra "
            "number try karo ya baad me try karo.\n"
            f"`{raw[:160]}`"
        )
    if "flood_wait" in low:
        return f"Telegram ne wait karne ko kaha hai:\n`{raw[:160]}`"
    # ── session-file specific failures ─────────────────────────────────────
    if "auth_key_unregistered" in low or "session_revoked" in low or "session_expired" in low:
        return (
            "Session expired / logout ho chuka hai. Naya session banao "
            "(uska `.session` file ya session string bhejo).\n"
            f"`{raw[:160]}`"
        )
    if "auth_key_duplicated" in low:
        return (
            "Ye account kisi doosre client me already chal raha hai (same session). "
            "Us jagah se logout karo, ya naye session se dobara try karo.\n"
            f"`{raw[:160]}`"
        )
    if "auth_key_invalid" in low or "api_id_published_flood" in low:
        return (
            "Session ki auth key Telegram ne reject kar di. Naya session banao.\n"
            f"`{raw[:160]}`"
        )
    if "user_deactivated" in low:
        return (
            "Ye Telegram account deactivate/delete ho chuka hai — dobara login nahi "
            "hoga.\n"
            f"`{raw[:160]}`"
        )
    if (
        "timed out" in low
        or "timeout" in low
        or "after 10 retries" in low
        or "failed to invoke" in low
    ):
        return (
            "Telegram se connection nahi ban paya (session dead hai ya galat DC ka). "
            "Naya session banao.\n"
            f"`{raw[:160]}`"
        )
    return f"Telegram error:\n`{raw[:200]}`"


async def cleanup_expired_flows():
    """Remove expired login flows safely."""
    now = time.time()
    to_remove = []
    for owner_id, flow in list(pending_auth.items()):
        if now - flow.get("created_at", 0) > OTP_TIMEOUT:
            to_remove.append(owner_id)

    for owner_id in to_remove:
        flow = pending_auth.pop(owner_id, None)
        if flow and "client" in flow:
            try:
                await flow["client"].stop()
            except Exception:
                pass


async def start_login_flow(owner_id: int, phone: str):
    """
    Start a new login flow by creating a temporary client and sending the OTP code.
    Returns (success: bool, message: str).
    """
    # Clean up existing flow for this owner if any
    if owner_id in pending_auth:
        old_flow = pending_auth.pop(owner_id, None)
        if old_flow and "client" in old_flow:
            try:
                await old_flow["client"].stop()
            except Exception:
                pass

    # Normalize phone: ensure only + and digits
    cleaned_phone = re.sub(r"[^\d+]", "", phone.strip())
    if not cleaned_phone.startswith("+"):
        cleaned_phone = "+" + cleaned_phone

    if len(cleaned_phone) < 8 or len(cleaned_phone) > 16:
        return False, "❌ Invalid phone number. Please include your country code (e.g. `+919876543210`)."

    # Create temporary client
    tmp_client = Client(
        name=f"auth_{owner_id}_{int(time.time())}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        in_memory=True,
    )

    try:
        await tmp_client.connect()
    except Exception as e:
        return False, f"❌ Failed to connect to Telegram: {e}"

    try:
        sent_code = await tmp_client.send_code(cleaned_phone)
        pending_auth[owner_id] = {
            "phone": cleaned_phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "step": "otp",
            "client": tmp_client,
            "created_at": time.time(),
        }
        return True, (
            "🔢 **Enter Telegram OTP**\n\n"
            "Please send the OTP in **ONE single message** with spaces between digits.\n\n"
            "Example: `1 2 3 4 5`"
        )
    except PhoneNumberInvalid:
        await tmp_client.disconnect()
        return False, "❌ The phone number is invalid. Please enter with country code (e.g. `+919876543210`)."
    except FloodWait as e:
        await tmp_client.disconnect()
        return False, f"⏳ FloodWait: Telegram requires you to wait {e.value} seconds before trying again."
    except RPCError as e:
        await tmp_client.disconnect()
        LOGGER.error(f"userbot login (auth.SendCode) failed: {e}")
        return False, explain_login_error(e)
    except Exception as e:
        await tmp_client.disconnect()
        LOGGER.error(f"userbot login (auth.SendCode) crashed: {type(e).__name__}: {e}")
        return False, explain_login_error(e)


async def process_otp_input(owner_id: int, otp_text: str):
    """
    Process OTP submitted in a single message (e.g. '1 2 3 4 5' or '1  2  3  4  5').
    Returns (success: bool, status: str, result: dict | str).
    status can be: 'SUCCESS', '2FA_REQUIRED', 'INVALID_FORMAT', 'ERROR'.
    """
    if owner_id not in pending_auth:
        return False, "ERROR", "❌ No active login session found. Use /useraccounts to start."

    flow = pending_auth[owner_id]
    if flow.get("step") != "otp":
        return False, "ERROR", "❌ Unexpected state. Please restart from /useraccounts."

    raw_text = otp_text.strip()

    # Master prompt rule: Accept only digits and whitespace
    if not re.fullmatch(r"^[\d\s]+$", raw_text):
        return (
            False,
            "INVALID_FORMAT",
            "❌ Invalid OTP format. Please enter the OTP like: `1 2 3 4 5`",
        )

    # Extract digits internally: "1 2 3 4 5" -> "12345"
    otp_code = re.sub(r"\s+", "", raw_text)
    if len(otp_code) < 3 or len(otp_code) > 10:
        return (
            False,
            "INVALID_FORMAT",
            "❌ Invalid OTP format. Please enter the OTP like: `1 2 3 4 5`",
        )

    client: Client = flow["client"]
    phone = flow["phone"]
    phone_code_hash = flow["phone_code_hash"]

    try:
        await client.sign_in(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
            phone_code=otp_code,
        )
        # Successfully signed in without 2FA!
        account_info = await complete_login(owner_id)
        return True, "SUCCESS", account_info

    except SessionPasswordNeeded:
        flow["step"] = "2fa"
        return (
            False,
            "2FA_REQUIRED",
            "🔐 **Two-Step Verification is enabled.**\n\nPlease enter your Telegram 2FA password.",
        )
    except PhoneCodeInvalid:
        return (
            False,
            "INVALID_OTP",
            "❌ Invalid OTP code. Please check and enter like: `1 2 3 4 5`",
        )
    except PhoneCodeExpired:
        pending_auth.pop(owner_id, None)
        try:
            await client.disconnect()
        except Exception:
            pass
        return (
            False,
            "ERROR",
            "❌ OTP has expired. Please use /useraccounts to try again.",
        )
    except FloodWait as e:
        pending_auth.pop(owner_id, None)
        try:
            await client.disconnect()
        except Exception:
            pass
        return (
            False,
            "ERROR",
            f"⏳ FloodWait: Please wait {e.value} seconds before trying again.",
        )
    except RPCError as e:
        LOGGER.error(f"userbot login (sign_in) failed: {e}")
        return False, "ERROR", explain_login_error(e)
    except Exception as e:
        LOGGER.error(f"userbot login (sign_in) crashed: {type(e).__name__}: {e}")
        return False, "ERROR", explain_login_error(e)


async def process_2fa_input(owner_id: int, password: str):
    """
    Process 2FA password submission.
    Returns (success: bool, status: str, result: dict | str).
    """
    if owner_id not in pending_auth:
        return False, "ERROR", "❌ No active login session found. Use /useraccounts to start."

    flow = pending_auth[owner_id]
    if flow.get("step") != "2fa":
        return False, "ERROR", "❌ Unexpected state. Please restart from /useraccounts."

    client: Client = flow["client"]

    try:
        await client.check_password(password=password.strip())
        account_info = await complete_login(owner_id)
        return True, "SUCCESS", account_info

    except PasswordHashInvalid:
        return False, "INVALID_PASSWORD", "❌ Incorrect 2FA password. Please try again:"
    except FloodWait as e:
        pending_auth.pop(owner_id, None)
        try:
            await client.disconnect()
        except Exception:
            pass
        return False, "ERROR", f"⏳ FloodWait: Please wait {e.value} seconds before trying again."
    except RPCError as e:
        LOGGER.error(f"userbot login (check_password) failed: {e}")
        return False, "ERROR", explain_login_error(e)
    except Exception as e:
        LOGGER.error(f"userbot login (check_password) crashed: {type(e).__name__}: {e}")
        return False, "ERROR", explain_login_error(e)


async def complete_login(owner_id: int) -> dict:
    """
    Finalize authentication: export session string, start persistent client,
    save to MongoDB, register handlers, and clean up temporary flow.
    """
    from Myra.userbot.handlers import register_userbot_handlers

    flow = pending_auth.pop(owner_id)
    tmp_client: Client = flow["client"]
    phone = flow["phone"]

    # Export session string
    session_string = await tmp_client.export_session_string()
    me = await tmp_client.get_me()

    # Disconnect temp client
    try:
        await tmp_client.disconnect()
    except Exception:
        pass

    account_id = f"ub_{me.id}"
    full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Telegram User"

    # Create persistent client
    persistent_client = Client(
        name=account_id,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    await persistent_client.start()

    # Prepare account data
    account_data = {
        "account_id": account_id,
        "telegram_id": me.id,
        "username": me.username or "",
        "name": full_name,
        "phone": phone,
        "added_by": int(owner_id),
        "enabled": True,
        "autochat": config.USERBOT_AUTOCHAT_DEFAULT,
        "reply_delay": None,
        "speed_mode": "fast",
        "group_mode": None,
        "reply_chance": None,
        "status": "CONNECTED",
        "created_at": time.time(),
    }

    # Save to MongoDB
    stored = await add_account(account_data)
    if stored is None:
        # The account already belongs to another bot (main bot or another clone):
        # never run the same Telegram account from two bots.
        try:
            await persistent_client.stop()
        except Exception:  # noqa: BLE001
            pass
        raise ValueError("ye account pehle se kisi aur bot me add hai")
    await save_session(account_id, session_string)

    # In-memory registry
    ub_accounts[account_id] = {
        "client": persistent_client,
        "telegram_id": me.id,
        "username": me.username or "",
        "name": full_name,
        "phone": phone,
        "added_by": int(owner_id),
        "scope": current_scope(),
        "autochat": config.USERBOT_AUTOCHAT_DEFAULT,
        "reply_delay": None,
        "speed_mode": "fast",
        "group_mode": None,
        "reply_chance": None,
        "status": "CONNECTED",
    }

    # Register handlers
    await register_userbot_handlers(persistent_client, account_id)

    return {
        "account_id": account_id,
        "telegram_id": me.id,
        "username": me.username or "",
        "name": full_name,
        "phone": phone,
        "added_by": int(owner_id),
        "autochat": config.USERBOT_AUTOCHAT_DEFAULT,
        "reply_delay": None,
        "speed_mode": "fast",
        "group_mode": None,
        "reply_chance": None,
        "status": "CONNECTED",
    }