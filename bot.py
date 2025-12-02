import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from storage import get_pending_verification, set_pending_verification, clear_pending_verification, append_trial_log


# Load .env file (if present) into environment variables
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TRIAL_CHANNEL_ID = int(os.environ.get("TRIAL_CHANNEL_ID", "0"))
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")

# Validate required environment variables
if not BOT_TOKEN:
    error_msg = (
        "❌ ERROR: BOT_TOKEN is missing!\n"
        "   For Render: Set BOT_TOKEN in Render Dashboard → Your service → Environment\n"
        "   For local: Create .env file with BOT_TOKEN=your_token"
    )
    print(error_msg)
    raise RuntimeError("BOT_TOKEN is required but not set")

if TRIAL_CHANNEL_ID == 0:
    print("⚠️  WARNING: TRIAL_CHANNEL_ID is not set (using 0)")
    print("   Set TRIAL_CHANNEL_ID in Render Dashboard → Environment (or .env for local)")

print(f"✅ Bot starting...")
print(f"   BASE_URL: {BASE_URL}")
print(f"   TRIAL_CHANNEL_ID: {TRIAL_CHANNEL_ID}")
print(f"   BOT_TOKEN: {'*' * 10 if BOT_TOKEN else 'NOT SET'}")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return

    keyboard = [
        [InlineKeyboardButton("🎁 Get Free Trial", callback_data="start_trial")],
    ]
    await update.message.reply_text(
        "Welcome! Tap the button below to start your free trial verification.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def start_trial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = query.from_user
    tg_id = user.id

    # Build URL - use Web App if HTTPS, fallback to regular URL if HTTP
    # Telegram Web Apps require HTTPS, so we check BASE_URL scheme
    trial_url = f"{BASE_URL.rstrip('/')}/trial?tg_id={tg_id}"
    
    # Check if BASE_URL uses HTTPS
    if BASE_URL.startswith("https://"):
        # Use Web App (opens as popup inside Telegram)
        # Include tg_id in URL as fallback in case JavaScript extraction fails
        button = InlineKeyboardButton("🌐 Open verification page", web_app=WebAppInfo(url=f"{BASE_URL.rstrip('/')}/trial?tg_id={tg_id}"))
    else:
        # Fallback to regular URL button (opens in external browser)
        # This is needed because Telegram Web Apps require HTTPS
        button = InlineKeyboardButton("🌐 Open verification page", url=trial_url)

    keyboard = [
        [button],
        [InlineKeyboardButton("✅ Continue verification", callback_data="continue_verification")],
    ]

    await query.edit_message_text(
        "Step 1: Open the verification page to pass IP and basic checks.\n"
        "After you finish there, come back here and tap 'Continue verification'.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def continue_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = query.from_user
    tg_id = user.id

    # Try to get data from local storage first
    data = get_pending_verification(tg_id)
    
    # If not found locally, try to fetch from web app API (for Render deployment)
    # This works even if web app and bot are in separate containers
    if not data or not data.get("step1_ok"):
        try:
            import aiohttp
            api_url = f"{BASE_URL.rstrip('/')}/api/get-verification?tg_id={tg_id}"
            print(f"🔍 Trying to fetch from web app API: {api_url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get("success") and result.get("data"):
                            data = result["data"]
                            print(f"✅ Got data from web app API for tg_id={tg_id}")
                            # Also save locally for future use
                            from storage import set_pending_verification
                            set_pending_verification(tg_id, data)
        except Exception as e:
            print(f"⚠️ Could not fetch from API: {e}")
            # Continue with local check
    
    if not data or not data.get("step1_ok"):
        await query.edit_message_text(
            "We could not find your web verification.\n"
            "Please tap 'Get Free Trial' again and complete the web step first."
        )
        return

    contact_button = KeyboardButton(text="📱 Share phone number", request_contact=True)
    keyboard = ReplyKeyboardMarkup([[contact_button]], resize_keyboard=True, one_time_keyboard=True)

    await query.message.reply_text(
        "Step 1 passed ✅.\n\n"
        "Step 2: Please share your phone number using the button below.\n\n"
        "We use your name, country, and phone number only for verification, "
        "security and internal analytics. We do not sell or share this data. "
        "You can request deletion at any time.",
        reply_markup=keyboard,
    )


async def retry_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Simple /retry command to re-show the contact request keyboard if user cancelled.
    """
    contact_button = KeyboardButton(text="📱 Share phone number", request_contact=True)
    keyboard = ReplyKeyboardMarkup([[contact_button]], resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        "Let's try again. Please share your phone number using the button below.",
        reply_markup=keyboard,
    )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    contact = update.message.contact
    user = update.effective_user

    if not contact:
        return

    # Ensure the shared contact belongs to the same user
    if contact.user_id and contact.user_id != user.id:
        await update.message.reply_text("Please share your own phone number using the button.")
        return

    phone = contact.phone_number or ""
    if not phone.startswith("+"):
        phone = "+" + phone

    data = get_pending_verification(user.id) or {}

    # Block Indian phone numbers
    if phone.startswith("+91"):
        data["status"] = "blocked_phone_india"
        data["phone"] = phone
        set_pending_verification(user.id, data)

        await update.message.reply_text(
            "You are not eligible for this trial with this phone number.\n"
            "We store minimal information only for security and abuse-prevention. "
            "You can request deletion at any time."
        )
        return

    # Passed phone check
    data["status"] = "verified"
    data["phone"] = phone
    set_pending_verification(user.id, data)

    await update.message.reply_text("Verification 2 passed ✅. Generating your one-time invite link...")

    bot = context.bot
    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=TRIAL_CHANNEL_ID,
            member_limit=1,
        )
    except Exception as e:  # pragma: no cover - defensive
        await update.message.reply_text(
            "Failed to create an invite link. Please try again later."
        )
        return

    await update.message.reply_text(
        "Here is your one-time invite link to the private trial channel.\n"
        "Please do not share it with others:\n"
        f"{invite_link.invite_link}"
    )

    # Log minimal info for your records
    append_trial_log(
        {
            "tg_id": user.id,
            "username": user.username,
            "name": data.get("name"),
            "country": data.get("country"),
            "phone": phone,
            "marketing_opt_in": data.get("marketing_opt_in", False),
            "verification_completed_at": _now_utc().isoformat(),
        }
    )

    # If you want to minimise stored sensitive data, you can clear the pending record here:
    # clear_pending_verification(user.id)


def _is_weekend(dt: datetime) -> bool:
    # 5 = Saturday, 6 = Sunday
    return dt.weekday() >= 5


async def trial_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_member = update.chat_member
    chat = chat_member.chat

    if chat.id != TRIAL_CHANNEL_ID:
        return

    old = chat_member.old_chat_member
    new = chat_member.new_chat_member

    # Detect join: previously left/kicked, now member/admin
    if old.status in ("left", "kicked") and new.status in ("member", "administrator"):
        user = new.user
        now = _now_utc()

        if _is_weekend(now):
            trial_days = 5
            total_hours = 120
        else:
            trial_days = 3
            total_hours = 72

        append_trial_log(
            {
                "tg_id": user.id,
                "username": user.username,
                "join_time": now.isoformat(),
                "trial_days": trial_days,
            }
        )

        await context.bot.send_message(
            chat_id=user.id,
            text=(
                f"✅ Your {trial_days}-day ({total_hours} hours) trial phase has started now!\n\n"
                "You will receive reminders as your trial approaches the end."
            ),
        )

        jq = context.job_queue

        if trial_days == 3:
            jq.run_once(
                trial_reminder_3day_1,
                when=timedelta(hours=24),
                data={"user_id": user.id},
            )
            jq.run_once(
                trial_reminder_3day_2,
                when=timedelta(hours=48),
                data={"user_id": user.id},
            )
            jq.run_once(
                trial_end,
                when=timedelta(hours=72),
                data={"user_id": user.id},
            )
        else:
            jq.run_once(
                trial_reminder_5day_1,
                when=timedelta(hours=24),
                data={"user_id": user.id},
            )
            jq.run_once(
                trial_reminder_5day_3,
                when=timedelta(hours=72),
                data={"user_id": user.id},
            )
            jq.run_once(
                trial_reminder_5day_4,
                when=timedelta(hours=96),
                data={"user_id": user.id},
            )
            jq.run_once(
                trial_end,
                when=timedelta(hours=120),
                data={"user_id": user.id},
            )


async def trial_reminder_3day_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await context.bot.send_message(
        chat_id=user_id,
        text="⏱ 1 day has passed, 2 days remaining in your trial.",
    )


async def trial_reminder_3day_2(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await context.bot.send_message(
        chat_id=user_id,
        text="⏱ 2 days have passed. Only the last 24 hours left in your trial!",
    )


async def trial_reminder_5day_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await context.bot.send_message(
        chat_id=user_id,
        text="⏱ 1 day has passed, 4 days remaining in your 5-day trial.",
    )


async def trial_reminder_5day_3(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await context.bot.send_message(
        chat_id=user_id,
        text="⏱ 3 days have passed, 2 days remaining in your 5-day trial.",
    )


async def trial_reminder_5day_4(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await context.bot.send_message(
        chat_id=user_id,
        text="⏱ 4 days have passed. Only the last 24 hours left in your trial!",
    )


async def trial_end(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await context.bot.send_message(
        chat_id=user_id,
        text="⛔ Your trial has finished. If you enjoyed the signals, you can upgrade to a paid plan to continue.",
    )
    # Optionally remove from trial channel
    try:
        await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user_id)
        await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user_id)
    except Exception:
        # Ignore errors (e.g. if bot is not admin)
        pass


def main() -> None:
    """
    Synchronous entrypoint for running the bot.
    Handlers remain async, but python-telegram-bot v21+ can manage the event
    loop internally via `run_polling()`.
    """
    # BOT_TOKEN is already validated at module level
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("retry", retry_command))

    application.add_handler(
        CallbackQueryHandler(start_trial_callback, pattern="^start_trial$")
    )
    application.add_handler(
        CallbackQueryHandler(
            continue_verification_callback, pattern="^continue_verification$"
        )
    )

    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))

    application.add_handler(
        ChatMemberHandler(trial_chat_member_update, ChatMemberHandler.CHAT_MEMBER)
    )

    # Handles event loop setup/teardown internally.
    application.run_polling()


if __name__ == "__main__":
    try:
        print("🚀 Starting Telegram bot...")
        main()
    except KeyboardInterrupt:
        print("\n⚠️ Bot stopped by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        raise


