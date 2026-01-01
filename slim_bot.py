"""
Freya Quinn - Slim Bot
Handles ONLY:
- ChatMemberHandler (join/leave detection)
- Trial reminders (24h, 48h, 72h, etc.)
- Trial end cleanup

All user-facing UI is handled by the Mini App.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from storage import (
    get_active_trial,
    set_active_trial,
    clear_active_trial,
    mark_trial_used,
    has_used_trial,
    get_used_trial_info,
    get_all_active_trials,
    append_trial_log,
    track_start_click,
    get_pending_verification,
    set_pending_verification,
    clear_pending_verification,
    get_valid_invite_link,
    set_invite_info,
    _verify_trial_signature,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv()


def _safe_float_env(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TRIAL_CHANNEL_ID = int(os.environ.get("TRIAL_CHANNEL_ID", "0"))
TIMEZONE_OFFSET_HOURS = _safe_float_env("TIMEZONE_OFFSET_HOURS", 0.0)

# Trial durations
TRIAL_HOURS_3_DAY = _safe_float_env("TRIAL_HOURS_3_DAY", 72.0)
TRIAL_HOURS_5_DAY = _safe_float_env("TRIAL_HOURS_5_DAY", 120.0)

# Reminder timing (in MINUTES)
REMINDER_1_MINUTES = _safe_float_env("REMINDER_1_MINUTES", 1440.0)  # 24h
REMINDER_2_MINUTES = _safe_float_env("REMINDER_2_MINUTES", 2880.0)  # 48h
TRIAL_END_3DAY_MINUTES = _safe_float_env("TRIAL_END_3DAY_MINUTES", 4320.0)  # 72h
REMINDER_3_MINUTES = _safe_float_env("REMINDER_3_MINUTES", 4320.0)  # 72h (5-day)
REMINDER_4_MINUTES = _safe_float_env("REMINDER_4_MINUTES", 5760.0)  # 96h (5-day)
TRIAL_END_5DAY_MINUTES = _safe_float_env("TRIAL_END_5DAY_MINUTES", 7200.0)  # 120h

TAMPERING_TOLERANCE_SECONDS = 3600
BLOCKED_PHONE_COUNTRY_CODE = os.environ.get("BLOCKED_PHONE_COUNTRY_CODE", "+91")
INVITE_LINK_EXPIRY_HOURS = int(os.environ.get("INVITE_LINK_EXPIRY_HOURS", "5"))

# Phone verification - can be disabled via env
REQUIRE_PHONE_VERIFICATION = os.environ.get("REQUIRE_PHONE_VERIFICATION", "true").lower() == "true"

# Trial cooldown in days before user can retry
TRIAL_COOLDOWN_DAYS = int(os.environ.get("TRIAL_COOLDOWN_DAYS", "30"))

# Links
GIVEAWAY_CHANNEL_URL = os.environ.get("GIVEAWAY_CHANNEL_URL", "https://t.me/Freya_Trades")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@cogitosk")
FEEDBACK_FORM_URL = os.environ.get("FEEDBACK_FORM_URL", "https://forms.gle/K7ubyn2tvzuYeHXn9")
SUPPORT_FORM_URL = os.environ.get("SUPPORT_FORM_URL", "https://forms.gle/CJbNczZ6BcKjk6Bz9")
BASE_URL = os.environ.get("BASE_URL", "https://freyatrades.live")

# Validate required vars
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
# CRITICAL: Validate TRIAL_CHANNEL_ID - bot cannot function without valid channel
if TRIAL_CHANNEL_ID == 0:
    error_msg = (
        "❌ CRITICAL ERROR: TRIAL_CHANNEL_ID not set!\n\n"
        "Set TRIAL_CHANNEL_ID in your environment (e.g. .env file).\n"
        "Get the channel ID by adding @iDbot to your channel.\n\n"
        "Example: TRIAL_CHANNEL_ID=-1001234567890"
    )
    logger.error(error_msg)
    raise ValueError(error_msg)

if TRIAL_CHANNEL_ID >= 0:
    error_msg = (
        f"❌ CRITICAL ERROR: Invalid TRIAL_CHANNEL_ID: {TRIAL_CHANNEL_ID}\n\n"
        "Channel IDs must be NEGATIVE numbers like -1001234567890.\n"
        "Positive numbers are for private chats, not channels!\n\n"
        "Get the correct ID by adding @iDbot to your channel."
    )
    logger.error(error_msg)
    raise ValueError(error_msg)

logger.info(f"✅ TRIAL_CHANNEL_ID validated: {TRIAL_CHANNEL_ID}")
logger.info(f"Slim Bot starting - TRIAL_CHANNEL_ID: {TRIAL_CHANNEL_ID}")



def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_to_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_weekend(dt: datetime) -> bool:
    local_dt = dt + timedelta(hours=TIMEZONE_OFFSET_HOURS)
    return local_dt.weekday() >= 5


def validate_trial_data(trial_data: dict, user_id: int) -> bool:
    """Validate trial data hasn't been tampered with. Returns True if valid, False if tampered."""
    if "join_time" not in trial_data or "total_hours" not in trial_data:
        logger.warning(f"validate_trial_data: Missing required fields for user {user_id}")
        return False
    
    # SECURITY: Verify HMAC signature
    if not _verify_trial_signature(trial_data, user_id):
        logger.error(f"validate_trial_data: SIGNATURE VERIFICATION FAILED for user {user_id}")
        return False
    
    try:
        total_hours = float(trial_data["total_hours"])
        if total_hours not in [TRIAL_HOURS_3_DAY, TRIAL_HOURS_5_DAY]:
            logger.warning(f"validate_trial_data: Invalid total_hours {total_hours} for user {user_id}")
            return False
        
        join_time = _parse_iso_to_utc(trial_data["join_time"])
        now = _now_utc()
        
        if join_time > now:
            logger.warning(f"validate_trial_data: Join time in future for user {user_id}")
            return False
        
        days_ago = (now - join_time).total_seconds() / 86400
        if days_ago > 30:
            logger.warning(f"validate_trial_data: Join time too old for user {user_id}")
            return False
            
        return True
    except Exception as e:
        logger.warning(f"Error validating trial data for user {user_id}: {e}")
        return False


# =============================================================================
# Command Handlers (Fallback for non-Mini App clients)
# =============================================================================

from telegram import (
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
)

async def start_trial_session(user, context: ContextTypes.DEFAULT_TYPE, send_welcome: bool = True) -> None:
    """Starts a new trial session for the user (sets DB, schedules jobs, sends msg)."""
    now = _now_utc()
    
    # Determine trial duration
    if _is_weekend(now):
        trial_days = 5
        total_hours = TRIAL_HOURS_5_DAY
    else:
        trial_days = 3
        total_hours = TRIAL_HOURS_3_DAY
    
    trial_end_at = now + timedelta(hours=total_hours)
    
    # Store active trial
    set_active_trial(user.id, {
        "join_time": now.isoformat(),
        "total_hours": total_hours,
        "trial_end_at": trial_end_at.isoformat(),
    })
    
    append_trial_log({
        "tg_id": user.id,
        "username": user.username,
        "join_time": now.isoformat(),
        "trial_days": trial_days,
    })
    
    if send_welcome:
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                f"✅ Your {trial_days}-day ({int(total_hours)} hours) trial phase has started now!\n\n"
                "You will receive reminders as your trial approaches the end."
            ),
        )
        
    # Schedule jobs
    jq = context.job_queue
    logger.info(f"Scheduling reminder jobs for user {user.id} ({trial_days}-day trial)")

    if trial_days == 3:
        jq.run_once(trial_reminder_3day_1, when=timedelta(minutes=REMINDER_1_MINUTES), data={"user_id": user.id}, name=f"reminder_1_{user.id}")
        jq.run_once(trial_reminder_3day_2, when=timedelta(minutes=REMINDER_2_MINUTES), data={"user_id": user.id}, name=f"reminder_2_{user.id}")
        jq.run_once(trial_end, when=timedelta(minutes=TRIAL_END_3DAY_MINUTES), data={"user_id": user.id}, name=f"trial_end_{user.id}")
    else:
        jq.run_once(trial_reminder_5day_1, when=timedelta(minutes=REMINDER_1_MINUTES), data={"user_id": user.id}, name=f"reminder_1_{user.id}")
        jq.run_once(trial_reminder_5day_3, when=timedelta(minutes=REMINDER_3_MINUTES), data={"user_id": user.id}, name=f"reminder_3_{user.id}")
        jq.run_once(trial_reminder_5day_4, when=timedelta(minutes=REMINDER_4_MINUTES), data={"user_id": user.id}, name=f"reminder_4_{user.id}")
        jq.run_once(trial_end, when=timedelta(minutes=TRIAL_END_5DAY_MINUTES), data={"user_id": user.id}, name=f"trial_end_{user.id}")

    logger.info(f"Started {trial_days}-day trial for user {user.id}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command - show Mini App button with fallback for other clients."""
    user = update.effective_user
    if not user:
        return
    
    # Track every /start click for analytics (before any blocking checks)
    track_start_click({
        "tg_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "language_code": user.language_code,
        "is_premium": getattr(user, 'is_premium', False),
        "is_bot": user.is_bot,
    })
    
    # Check if user already used trial
    if has_used_trial(user.id):
        await update.message.reply_text(
            "You have already used your free trial.\n\n"
            f"🎁 Join giveaways: {GIVEAWAY_CHANNEL_URL}\n"
            f"💬 Upgrade: {SUPPORT_CONTACT}",
        )
        return
    
    # Check if user has active trial
    active = get_active_trial(user.id)
    
    # SELF-HEALING: If no active trial record, check if they are actually in the channel
    # This handles cases where the bot missed the 'join' event
    if not active and not has_used_trial(user.id):
        try:
            member = await context.bot.get_chat_member(TRIAL_CHANNEL_ID, user.id)
            if member.status in ("member", "administrator", "creator"):
                 logger.info(f"Self-healing: User {user.id} is in channel but missing trial record. Starting trial now.")
                 # Start the trial session (restoring missed state)
                 await start_trial_session(user, context, send_welcome=True)
                 active = get_active_trial(user.id) # Refresh active state
        except Exception as e:
            logger.warning(f"Failed to check membership for healing: {e}")
            
    if active and "trial_end_at" in active:
        try:
            end_at = _parse_iso_to_utc(active["trial_end_at"])
            if _now_utc() < end_at:
                remaining = (end_at - _now_utc()).total_seconds() / 3600
                await update.message.reply_text(
                    f"✅ You have an active trial!\n\n"
                    f"⏳ Time remaining: {remaining:.1f} hours\n\n"
                    f"💬 Questions? DM {SUPPORT_CONTACT}",
                )
                return

        except Exception:
            pass
    
    # Check if user has a valid invite link that hasn't expired yet
    # This prevents the loop where users restart flow and get a new link
    now = _now_utc()
    existing_link = get_valid_invite_link(user.id, now)
    if existing_link:
        await update.message.reply_text(
            "✅ You already have a valid trial invite link!\n\n"
            f"🔗 {existing_link}\n\n"
            "Please use this link to join the trial channel.\n"
            "If the link doesn't work (or says expired), it might have been used or revoked.\n"
            "You can try waiting for it to expire (5 hours) or contact support.",
        )
        return

    # Dual-mode keyboard:
    # Row 1: Mini App button (for official Telegram clients)
    # Row 2: Fallback callback button (for Telegram X and other clients)
    keyboard = [
        [InlineKeyboardButton(
            text="🚀 Access Now",
            web_app=WebAppInfo(url=f"{BASE_URL.rstrip('/')}/app")
        )],
        [InlineKeyboardButton(
            text="📱 Not working? Tap here",
            callback_data="start_trial_fallback"
        )]
    ]
    
    # Check if user has completed step1 (form) but not step2 (phone)
    pending_data = get_pending_verification(user.id)
    if pending_data and pending_data.get("step1_ok") and pending_data.get("status") != "phone_verified":
        # User passed step1 but hasn't done phone verification yet
        keyboard = [
            [InlineKeyboardButton("✅ Continue Verification", callback_data="continue_verification")],
        ]
        await update.message.reply_text(
            "✅ *Step 1 Already Complete!*\n\n"
            "Great news! You've already passed the initial verification.\n\n"
            "📱 *Just one more step:* Share your phone number to get your trial invite.\n\n"
            "👇 Tap below to complete:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Hey {user.first_name}! 👋\n\n"
        "Welcome to Freya Quinn's Flirty Profits! 💋\n\n"
        "Get instant access to my VIP signals.\n\n"
        "Tap the button below to start:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(
        "📚 **Commands & Help**\n\n"
        "/start - Start your free trial\n"
        "/help - This help message\n"
        "/faq - Frequently asked questions\n"
        "/about - About Freya Quinn\n"
        "/support - Contact support\n\n"
        f"💬 Direct support: {SUPPORT_CONTACT}",
        parse_mode="Markdown"
    )


async def cmd_faq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /faq command."""
    await update.message.reply_text(
        "❓ **Frequently Asked Questions**\n\n"
        "**Q: How long is the trial?**\n"
        "A: 3 days (weekdays) or 5 days (weekends).\n\n"
        "**Q: What do I get?**\n"
        "A: 2-6 trading signals daily with clear entry, TP & SL.\n\n"
        "**Q: How do I start?**\n"
        "A: Tap /start and click the button.\n\n"
        "**Q: What happens after trial ends?**\n"
        "A: You can upgrade to Premium for continued access.\n\n"
        f"More questions? DM {SUPPORT_CONTACT}",
        parse_mode="Markdown"
    )


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /about command."""
    await update.message.reply_text(
        "💋 **About Freya Quinn**\n\n"
        "I'm Freya - your flirty forex friend!\n\n"
        "I provide premium trading signals with:\n"
        "• 🎯 High win rate\n"
        "• 📊 Clear entry, TP & SL\n"
        "• 💰 Consistent profits\n\n"
        f"🎁 Free giveaways: {GIVEAWAY_CHANNEL_URL}\n"
        f"💬 Questions: {SUPPORT_CONTACT}",
        parse_mode="Markdown"
    )


async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /support command."""
    await update.message.reply_text(
        "🆘 **Need Help?**\n\n"
        f"📝 Support Form: {SUPPORT_FORM_URL}\n\n"
        f"💬 Direct message: {SUPPORT_CONTACT}\n\n"
        f"📣 Feedback: {FEEDBACK_FORM_URL}\n\n"
        "We typically respond within 24 hours!",
        parse_mode="Markdown"
    )


# =============================================================================
# Callback Handlers (Fallback workflow for non-Mini App clients)
# =============================================================================

async def start_trial_fallback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback for clients that don't support Mini Apps (Telegram X, etc.)."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    
    user = query.from_user
    if not user:
        return
    
    # Check if already used trial
    if has_used_trial(user.id):
        await query.edit_message_text(
            "You have already used your free trial.\n\n"
            f"🎁 Join giveaways: {GIVEAWAY_CHANNEL_URL}\n"
            f"💬 Upgrade: {SUPPORT_CONTACT}",
        )
        return
    
    # Build verification URL (regular URL, not WebApp)
    trial_url = f"{BASE_URL.rstrip('/')}/trial?tg_id={user.id}"
    
    keyboard = [
        [InlineKeyboardButton("🌐 Open Verification Page", url=trial_url)],
        [InlineKeyboardButton("✅ Done - Continue", callback_data="continue_verification")]
    ]
    
    await query.edit_message_text(
        "📱 **Alternative Verification**\n\n"
        "Your app doesn't support Mini Apps. No problem!\n\n"
        "**Step 1:** Tap the button below to open the verification page in your browser.\n\n"
        "**Step 2:** Complete the verification on the page.\n\n"
        "**Step 3:** Come back and tap 'Done - Continue'.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def continue_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle continue verification after web step."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    
    user = query.from_user
    if not user:
        return
    
    # Try to fetch verification data from API
    import aiohttp
    data = None
    
    try:
        api_url = f"{BASE_URL.rstrip('/')}/api/get-verification?tg_id={user.id}"
        headers = {}
        api_secret = os.environ.get("API_SECRET", "")
        if api_secret:
            headers["X-API-Secret"] = api_secret
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("success") and result.get("data"):
                        data = result["data"]
    except Exception as e:
        logger.warning(f"Could not fetch verification data: {e}")
    
    if not data or not data.get("step1_ok"):
        await query.edit_message_text(
            "❌ **Verification not found**\n\n"
            "Please complete the web verification first:\n\n"
            "1. Open the verification page\n"
            "2. Turn off VPN/Proxy\n"
            "3. Fill in your details\n"
            "4. Submit the form\n"
            "5. Come back and try again\n\n"
            "Use /start to try again.",
            parse_mode="Markdown"
        )
        return
        
    # Step 1 passed - Request Phone Number
    contact_button = KeyboardButton(text="📱 Share phone number", request_contact=True)
    deny_button = KeyboardButton(text="❌ No thanks")
    keyboard = ReplyKeyboardMarkup(
        [[contact_button], [deny_button]], 
        resize_keyboard=True, 
        one_time_keyboard=True
    )

    await query.message.reply_text(
        "Step 1 passed ✅.\n\n"
        "Step 2: Please share your phone number using the button below.\n\n"
        "We use your name, country, and phone number only for verification, "
        "security and internal analytics. We do not sell or share this data.",
        reply_markup=keyboard,
    )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    contact = update.message.contact
    user = update.effective_user

    if not contact:
        return

    # Check if user has already used trial
    if has_used_trial(user.id):
        await update.message.reply_text(
            "You have already used your free trial.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Ensure the shared contact belongs to the same user
    if contact.user_id and contact.user_id != user.id:
        await update.message.reply_text("Please share your own phone number using the button.")
        return

    phone = contact.phone_number or ""
    if not phone.startswith("+"):
        phone = "+" + phone

    data = get_pending_verification(user.id) or {}

    # Block phone numbers by country code
    if BLOCKED_PHONE_COUNTRY_CODE and phone.startswith(BLOCKED_PHONE_COUNTRY_CODE):
        data["status"] = "blocked_phone_india"
        data["phone"] = phone
        set_pending_verification(user.id, data)
        await update.message.reply_text(
            "You are not eligible for this trial with this phone number.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Passed phone check
    data["status"] = "verified"
    data["phone"] = phone
    set_pending_verification(user.id, data)

    # ATOMIC CHECK: Use new atomic function to prevent race condition
    from storage import atomic_create_or_get_invite, finalize_invite_creation, cleanup_failed_invite_creation
    
    now = _now_utc()
    expires_at_dt = now + timedelta(hours=INVITE_LINK_EXPIRY_HOURS)
    
    invite_data = {
        "invite_created_at": now.isoformat(),
        "invite_expires_at": expires_at_dt.isoformat(),
    }
    
    result = atomic_create_or_get_invite(user.id, invite_data)
    
    if result["action"] == "existing":
        await update.message.reply_text(
            f"✅ You already have an invite link!\n\n{result['link']}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return


    await update.message.reply_text("Verification 2 passed ✅. Generating your ONE-TIME invite link...")

    try:
        invite_link = await context.bot.create_chat_invite_link(
            chat_id=TRIAL_CHANNEL_ID,
            member_limit=1,
            expire_date=int(expires_at_dt.timestamp()),
        )
        
        # Finalize the creation
        finalize_invite_creation(user.id, invite_link.invite_link)
        
        await update.message.reply_text(
            f"Here is your one-time trial invite:\n\n{invite_link.invite_link}\n\n⚠️ Link expires in {INVITE_LINK_EXPIRY_HOURS} hours.",
            reply_markup=ReplyKeyboardRemove(),
        )
        
        # Log completion
        append_trial_log({
            "tg_id": user.id,
            "username": user.username,
            "name": data.get("name"),
            "country": data.get("country"),
            "phone": phone,
            "verification_completed_at": now.isoformat(),
        })
        
        clear_pending_verification(user.id)
        
    except Exception as e:
        logger.error(f"Failed to generate invite for {user.id}: {e}")
        cleanup_failed_invite_creation(user.id)  # Clean up placeholder
        await update.message.reply_text(
            "Error generating invite link. Please contact support.",
            reply_markup=ReplyKeyboardRemove(),
        )


async def retry_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    # 1. Check if trial already used
    if has_used_trial(user.id):
        await update.message.reply_text(
            "You have already used your free trial.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # 2. Check if trial already active
    if get_active_trial(user.id):
        await update.message.reply_text(
            "You already have an active trial!",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # 3. Check if they are actually in the verification process
    data = get_pending_verification(user.id)
    if not data or not data.get("step1_ok"):
        await update.message.reply_text(
            "⚠️ You haven't started the verification yet.\n\n"
            "Please use /start to begin.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    
    if data.get("status") == "verified":
        await update.message.reply_text(
            "✅ You are already verified!\n\n"
            "Use /start to get your invite link.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # 4. Allow retry if they are stuck at step 2
    contact_button = KeyboardButton(text="📱 Share phone number", request_contact=True)
    deny_button = KeyboardButton(text="❌ No thanks")
    keyboard = ReplyKeyboardMarkup(
        [[contact_button], [deny_button]], 
        resize_keyboard=True, 
        one_time_keyboard=True
    )
    await update.message.reply_text(
        "Please share your phone number using the button below.",
        reply_markup=keyboard,
    )


async def text_during_phone_verification_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text messages when user is supposed to share phone number via button.
    Users sometimes type their phone number instead of clicking the share button.
    """
    if not update.message or not update.effective_user:
        return
    
    user = update.effective_user
    message_text = update.message.text or ""
    
    # Check if user is in the phone verification stage
    # (has completed step1 but hasn't verified phone yet)
    data = get_pending_verification(user.id)
    
    if data and data.get("step1_ok") and data.get("status") != "verified":
        # User is in phone verification stage but sent text instead of clicking button
        logger.info(f"User {user.id} sent text '{message_text[:50]}...' during phone verification stage")
        
        # Check if it looks like they typed a phone number
        import re
        looks_like_phone = bool(re.search(r'[\d\+\-\(\)\s]{7,}', message_text))
        
        if looks_like_phone:
            await update.message.reply_text(
                "⚠️ Please don't type your phone number!\n\n"
                "For security, we need you to use Telegram's official phone sharing button.\n\n"
                "👇 Click the **'📱 Share phone number'** button below to continue.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "⚠️ Please use the button to share your phone number.\n\n"
                "👇 Click the **'📱 Share phone number'** button below to continue verification.\n\n"
                "If you don't see the button, type /retry to show it again.",
                parse_mode="Markdown"
            )
        return


async def phone_deny_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "No problem! Verification cancelled. Use /start to try again.",
        reply_markup=ReplyKeyboardRemove(),
    )


# =============================================================================
# Admin Commands
# =============================================================================

# Admin TG IDs - comma-separated list of Telegram user IDs who can use admin commands
_admin_ids_raw = os.environ.get("ADMIN_TG_IDS", "")
ADMIN_TG_IDS: set[int] = set()
if _admin_ids_raw:
    for id_str in _admin_ids_raw.split(","):
        id_str = id_str.strip()
        if id_str.isdigit():
            ADMIN_TG_IDS.add(int(id_str))


def is_admin(user_id: int) -> bool:
    """Check if a user ID is in the admin list."""
    return user_id in ADMIN_TG_IDS


def parse_inline_buttons(text: str):
    """
    Parse inline buttons from text.
    Format: [button:Label:URL] or [button:Label:callback_data]
    Returns (cleaned_text, InlineKeyboardMarkup or None)
    """
    import re
    pattern = r'\[button:([^:]+):([^\]]+)\]'
    matches = re.findall(pattern, text)
    
    if not matches:
        return text, None
    
    cleaned_text = re.sub(pattern, '', text).strip()
    
    buttons = []
    for label, target in matches:
        label = label.strip()
        target = target.strip()
        if target.startswith('http://') or target.startswith('https://'):
            buttons.append([InlineKeyboardButton(label, url=target)])
        else:
            buttons.append([InlineKeyboardButton(label, callback_data=target)])
    
    return cleaned_text, InlineKeyboardMarkup(buttons) if buttons else None


async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to send messages to specific chat IDs."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized. Admins only.")
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📝 Usage: /send chat_id1,chat_id2,... Your message\n"
            "Button syntax: [button:Label:URL]"
        )
        return
    
    chat_ids_raw = context.args[0]
    message_text = ' '.join(context.args[1:])
    cleaned_text, keyboard = parse_inline_buttons(message_text)
    
    chat_ids = []
    for cid_str in chat_ids_raw.split(','):
        try:
            cid = int(cid_str.strip())
            if cid > 0:
                chat_ids.append(cid)
        except ValueError:
            continue
    
    if not chat_ids or not cleaned_text:
        await update.message.reply_text("❌ Invalid chat IDs or empty message.")
        return
    
    from telegram.error import BadRequest, Forbidden, TelegramError
    import asyncio
    
    successful, failed = [], []
    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=cleaned_text, reply_markup=keyboard)
            successful.append(chat_id)
            await asyncio.sleep(0.5)
        except (Forbidden, BadRequest, TelegramError):
            failed.append(chat_id)
    
    await update.message.reply_text(
        f"📊 Sent: {len(successful)}/{len(chat_ids)} | Failed: {len(failed)}"
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to broadcast to all /start users."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized. Admins only.")
        return
    
    from storage import get_all_start_users, is_banned
    from telegram.error import BadRequest, Forbidden, TelegramError
    import asyncio
    
    all_users = get_all_start_users()
    if not all_users:
        await update.message.reply_text("❌ No users found.")
        return
    
    reply_msg = update.message.reply_to_message
    has_media = False
    media_type = None
    
    if reply_msg:
        if reply_msg.photo:
            has_media, media_type = True, "photo"
        elif reply_msg.video:
            has_media, media_type = True, "video"
        elif reply_msg.document:
            has_media, media_type = True, "document"
    
    if context.args:
        message_text = ' '.join(context.args)
    elif has_media and reply_msg.caption:
        message_text = reply_msg.caption
    else:
        await update.message.reply_text(
            "📝 Usage: /broadcast Your message\n"
            "Or reply to media with /broadcast"
        )
        return
    
    cleaned_text, keyboard = parse_inline_buttons(message_text)
    chat_ids = [int(tg_id) for tg_id in all_users.keys() if not is_banned(int(tg_id))]
    
    status_msg = await update.message.reply_text(f"📤 Broadcasting to {len(chat_ids)} users...")
    
    successful, failed = 0, 0
    for i, chat_id in enumerate(chat_ids):
        try:
            if has_media:
                if media_type == "photo":
                    await context.bot.send_photo(chat_id=chat_id, photo=reply_msg.photo[-1].file_id, caption=cleaned_text, reply_markup=keyboard)
                elif media_type == "video":
                    await context.bot.send_video(chat_id=chat_id, video=reply_msg.video.file_id, caption=cleaned_text, reply_markup=keyboard)
                elif media_type == "document":
                    await context.bot.send_document(chat_id=chat_id, document=reply_msg.document.file_id, caption=cleaned_text, reply_markup=keyboard)
            else:
                await context.bot.send_message(chat_id=chat_id, text=cleaned_text, reply_markup=keyboard)
            successful += 1
            if (i + 1) % 50 == 0:
                try:
                    await status_msg.edit_text(f"📤 {i+1}/{len(chat_ids)} | ✅ {successful} | ❌ {failed}")
                except Exception:
                    pass
            await asyncio.sleep(0.05)
        except (Forbidden, BadRequest, TelegramError):
            failed += 1
    
    await status_msg.edit_text(f"📊 Complete: ✅ {successful}/{len(chat_ids)} | ❌ {failed}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to view statistics."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized. Admins only.")
        return
    
    from storage import get_storage_stats
    stats = get_storage_stats()
    
    await update.message.reply_text(
        f"📊 *Statistics*\n\n"
        f"👥 /start clicks: `{stats['total_start_clicks']}`\n"
        f"✅ Verified: `{stats['verified_users']}`\n"
        f"🎯 Active trials: `{stats['active_trials']}`\n"
        f"📦 Used trials: `{stats['used_trials']}`\n"
        f"🚫 Banned: `{stats['banned_users']}`",
        parse_mode="Markdown"
    )


async def user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to lookup user info."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("📝 Usage: /user <tg_id>")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    
    from storage import get_start_user_info, get_active_trial, get_used_trial_info, is_banned
    
    start_info = get_start_user_info(target_id)
    active = get_active_trial(target_id)
    used = get_used_trial_info(target_id)
    banned = is_banned(target_id)
    
    lines = [f"👤 *User {target_id}*"]
    if banned:
        lines.append("🚫 *BANNED*")
    if start_info:
        lines.append(f"📌 @{start_info.get('username', 'N/A')}")
        lines.append(f"📌 Clicks: {start_info.get('click_count', 0)}")
    if active:
        lines.append(f"🎯 Active trial: {active.get('total_hours', '?')}h")
    elif used:
        lines.append(f"📦 Trial used")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to ban a user."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("📝 Usage: /ban <tg_id> [reason]")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    
    reason = ' '.join(context.args[1:]) if len(context.args) > 1 else "Admin ban"
    
    from storage import add_banned_user, is_banned
    if is_banned(target_id):
        await update.message.reply_text(f"⚠️ Already banned.")
        return
    
    add_banned_user(target_id, reason, user.id)
    await update.message.reply_text(f"🚫 User {target_id} banned.")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to unban a user."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("📝 Usage: /unban <tg_id>")
        return
    
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    
    from storage import remove_banned_user
    if remove_banned_user(target_id):
        await update.message.reply_text(f"✅ User {target_id} unbanned.")
    else:
        await update.message.reply_text(f"⚠️ User was not banned.")


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to export data as JSON."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    export_type = context.args[0].lower() if context.args else "all"
    
    from storage import get_all_start_users, get_all_active_trials, USED_TRIALS_FILE, PENDING_FILE, _load_json
    import json, io
    
    data = {}
    if export_type in ("clicks", "all"):
        data["start_clicks"] = get_all_start_users()
    if export_type in ("trials", "all"):
        data["active_trials"] = get_all_active_trials()
        data["used_trials"] = _load_json(USED_TRIALS_FILE, {})
    if export_type in ("verified", "all"):
        data["verifications"] = _load_json(PENDING_FILE, {})
    
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    file_bytes = io.BytesIO(json_str.encode('utf-8'))
    file_bytes.name = f"export_{export_type}.json"
    
    await update.message.reply_document(document=file_bytes, caption=f"📦 Exported: {export_type}")


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to schedule a broadcast."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args or len(context.args) < 3:
        await update.message.reply_text("📝 Usage: /schedule YYYY-MM-DD HH:MM Message")
        return
    
    date_str, time_str = context.args[0], context.args[1]
    message_text = ' '.join(context.args[2:])
    
    try:
        scheduled_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        scheduled_dt = scheduled_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use YYYY-MM-DD HH:MM")
        return
    
    now = _now_utc()
    if scheduled_dt <= now:
        await update.message.reply_text("❌ Time must be in the future.")
        return
    
    from storage import add_scheduled_broadcast
    broadcast_id = add_scheduled_broadcast({
        "scheduled_at": scheduled_dt.isoformat(),
        "message": message_text,
        "created_by": user.id,
    })
    
    delay = scheduled_dt - now
    context.job_queue.run_once(
        execute_scheduled_broadcast,
        when=delay,
        data={"broadcast_id": broadcast_id}
    )
    
    await update.message.reply_text(f"✅ Scheduled! ID: `{broadcast_id}`", parse_mode="Markdown")


async def execute_scheduled_broadcast(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute scheduled broadcast."""
    broadcast_id = context.job.data["broadcast_id"]
    
    from storage import get_all_start_users, is_banned, mark_broadcast_sent, get_scheduled_broadcasts
    import asyncio
    
    broadcasts = get_scheduled_broadcasts()
    broadcast = next((b for b in broadcasts if b.get("id") == broadcast_id), None)
    
    if not broadcast or broadcast.get("sent"):
        return
    
    message_text = broadcast.get("message", "")
    cleaned_text, keyboard = parse_inline_buttons(message_text)
    
    all_users = get_all_start_users()
    chat_ids = [int(tg_id) for tg_id in all_users.keys() if not is_banned(int(tg_id))]
    
    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=cleaned_text, reply_markup=keyboard)
            await asyncio.sleep(0.05)
        except Exception:
            pass
    
    mark_broadcast_sent(broadcast_id)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to delete a message."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("📝 Usage: /delete <chat_id> <message_id>")
        return
    
    try:
        chat_id, message_id = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid IDs.")
        return
    
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        await update.message.reply_text(f"✅ Deleted.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def list_scheduled_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to list scheduled broadcasts."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    from storage import get_scheduled_broadcasts
    
    broadcasts = get_scheduled_broadcasts()
    pending = [b for b in broadcasts if not b.get("sent")]
    
    if not pending:
        await update.message.reply_text("📭 No scheduled broadcasts.")
        return
    
    lines = ["📋 *Scheduled Broadcasts*\n"]
    for b in pending[:10]:
        bid = b.get("id", "?")
        scheduled_at = b.get("scheduled_at", "?")[:16] if b.get("scheduled_at") else "?"
        message = b.get("message", "")[:40]
        lines.append(f"🆔 `{bid}` - {scheduled_at}")
        lines.append(f"   {message}{'...' if len(b.get('message', '')) > 40 else ''}")
    
    if len(pending) > 10:
        lines.append(f"\n... and {len(pending) - 10} more")
    
    lines.append("\n💡 `/cancel <id>` to cancel")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cancel_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to cancel a scheduled broadcast."""
    user = update.effective_user
    if not user or not update.message:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("📝 Usage: /cancel <broadcast_id>")
        return
    
    broadcast_id = context.args[0]
    
    from storage import remove_scheduled_broadcast
    
    if remove_scheduled_broadcast(broadcast_id):
        jobs = context.job_queue.get_jobs_by_name(f"scheduled_broadcast_{broadcast_id}")
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text(f"✅ Broadcast `{broadcast_id}` cancelled.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Not found: `{broadcast_id}`", parse_mode="Markdown")




# =============================================================================
# Chat Member Handler (Join/Leave Detection)
# =============================================================================


async def trial_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user join/leave in trial channel."""
    if not update.chat_member:
        return
    
    chat_member = update.chat_member
    chat = chat_member.chat
    
    if chat.id != TRIAL_CHANNEL_ID:
        return
    
    old = chat_member.old_chat_member
    new = chat_member.new_chat_member
    
    logger.info(f"Chat member update: user={new.user.id if new.user else 'None'}, {old.status} -> {new.status}")
    
    # USER JOINED
    if old.status in ("left", "kicked") and new.status in ("member", "administrator"):
        user = new.user
        if not user:
            return
        
        logger.info(f"User {user.id} joined trial channel")
        now = _now_utc()
        
        # Check if already used trial
        if has_used_trial(user.id):
            user_trial_info = get_used_trial_info(user.id)
            trial_ended_at_str = user_trial_info.get("trial_ended_at") if user_trial_info else None
            
            if trial_ended_at_str:
                try:
                    ended_at = _parse_iso_to_utc(trial_ended_at_str)
                    days_since = (now - ended_at).total_seconds() / 86400
                    if days_since < TRIAL_COOLDOWN_DAYS:
                        await context.bot.send_message(
                            chat_id=user.id,
                            text=f"You recently used a trial. Please wait {TRIAL_COOLDOWN_DAYS} days.\n\n"
                                 f"🎁 Giveaways: {GIVEAWAY_CHANNEL_URL}\n"
                                 f"💬 Upgrade: {SUPPORT_CONTACT}",
                        )
                        try:
                            await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user.id)
                            await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user.id)
                        except Exception:
                            pass
                        return
                except Exception:
                    pass
            
            # Block user who already used trial
            await context.bot.send_message(
                chat_id=user.id,
                text=f"You have already used your trial.\n\n"
                     f"🎁 Giveaways: {GIVEAWAY_CHANNEL_URL}\n"
                     f"💬 Upgrade: {SUPPORT_CONTACT}",
            )
            try:
                await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user.id)
                await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user.id)
            except Exception:
                pass
            return
        
        # Check if trial already active (prevent double scheduling)
        existing = get_active_trial(user.id)
        if existing and "trial_end_at" in existing:
            try:
                end_at = _parse_iso_to_utc(existing["trial_end_at"])
                if now < end_at:
                    return  # Already running
            except Exception:
                pass
        
        # Determine trial duration
        if _is_weekend(now):
            trial_days = 5
            total_hours = TRIAL_HOURS_5_DAY
        else:
            trial_days = 3
            total_hours = TRIAL_HOURS_3_DAY
        
        trial_end_at = now + timedelta(hours=total_hours)
        
        # Store active trial
        set_active_trial(user.id, {
            "join_time": now.isoformat(),
            "total_hours": total_hours,
            "trial_end_at": trial_end_at.isoformat(),
        })
        
        append_trial_log({
            "tg_id": user.id,
            "username": user.username,
            "join_time": now.isoformat(),
            "trial_days": trial_days,
        })
        
        # Send welcome message
        await context.bot.send_message(
            chat_id=user.id,
            text=f"✅ Your {trial_days}-day ({int(total_hours)} hours) trial has started!\n\n"
                 "You will receive reminders as your trial approaches the end.",
        )
        
        # Schedule reminders
        jq = context.job_queue
        
        if trial_days == 3:
            jq.run_once(trial_reminder_3day_1, when=timedelta(minutes=REMINDER_1_MINUTES),
                        data={"user_id": user.id}, name=f"reminder_1_{user.id}")
            jq.run_once(trial_reminder_3day_2, when=timedelta(minutes=REMINDER_2_MINUTES),
                        data={"user_id": user.id}, name=f"reminder_2_{user.id}")
            jq.run_once(trial_end, when=timedelta(minutes=TRIAL_END_3DAY_MINUTES),
                        data={"user_id": user.id}, name=f"trial_end_{user.id}")
        else:
            jq.run_once(trial_reminder_5day_1, when=timedelta(minutes=REMINDER_1_MINUTES),
                        data={"user_id": user.id}, name=f"reminder_1_{user.id}")
            jq.run_once(trial_reminder_5day_3, when=timedelta(minutes=REMINDER_3_MINUTES),
                        data={"user_id": user.id}, name=f"reminder_3_{user.id}")
            jq.run_once(trial_reminder_5day_4, when=timedelta(minutes=REMINDER_4_MINUTES),
                        data={"user_id": user.id}, name=f"reminder_4_{user.id}")
            jq.run_once(trial_end, when=timedelta(minutes=TRIAL_END_5DAY_MINUTES),
                        data={"user_id": user.id}, name=f"trial_end_{user.id}")
        
        logger.info(f"Scheduled {trial_days}-day trial for user {user.id}")
    
    # USER LEFT
    elif old.status in ("member", "administrator") and new.status in ("left", "kicked"):
        user = old.user
        if not user:
            return
        
        # Check if bot caused the leave (trial_end cleanup)
        try:
            bot_user = await context.bot.get_me()
            if bot_user and chat_member.from_user and chat_member.from_user.id == bot_user.id:
                logger.info("Leave caused by bot, skipping feedback")
                return
        except Exception:
            pass
        
        logger.info(f"User {user.id} left trial channel voluntarily")
        
        # Get trial data
        active = get_active_trial(user.id)
        
        # Mark trial as used
        leave_info = {
            "left_early_at": _now_utc().isoformat(),
            "reason": "user_left_channel",
        }
        if active:
            leave_info["join_time"] = active.get("join_time")
            leave_info["total_hours"] = active.get("total_hours")
        
        mark_trial_used(user.id, leave_info)
        clear_active_trial(user.id)
        
        # Send feedback message
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=f"👋 You have left the trial channel.\n\n"
                     f"Your free trial has been marked as consumed.\n\n"
                     f"📝 Feedback: {FEEDBACK_FORM_URL}\n"
                     f"🎁 Giveaways: {GIVEAWAY_CHANNEL_URL}\n"
                     f"💬 Upgrade: {SUPPORT_CONTACT}",
            )
        except Exception as e:
            logger.warning(f"Failed to send leave message to {user.id}: {e}")


# =============================================================================
# Reminder Functions
# =============================================================================

async def _send_reminder(context: ContextTypes.DEFAULT_TYPE, user_id: int, message: str, name: str) -> bool:
    """Send reminder if user still has active trial."""
    active = get_active_trial(user_id)
    if not active:
        logger.info(f"Skipping {name} for {user_id} - no active trial")
        return False
    
    try:
        trial_end_str = active.get("trial_end_at")
        if trial_end_str:
            trial_end = _parse_iso_to_utc(trial_end_str)
            if _now_utc() >= trial_end:
                logger.info(f"Skipping {name} for {user_id} - trial expired")
                return False
    except Exception:
        pass
    
    try:
        await context.bot.send_message(chat_id=user_id, text=message)
        logger.info(f"Sent {name} to {user_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to send {name} to {user_id}: {e}")
        return False


async def trial_reminder_3day_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_reminder(context, user_id,
        "Hey, it's Freya 💋\n\n"
        "You've been inside my 3-Day Trial for about a day now – I hope you've already seen how I structure my trades and risk.\n\n"
        "In this group you'll usually see:\n\n"
        "• 🔔 2–6 signals per day\n"
        "• 🎯 Clear entry, take-profit levels & stop-loss\n"
        "• 📊 Screenshots + short explanation so you can learn, not just copy\n\n"
        "If you missed anything, scroll up in the trial chat and check today's setups – everything is transparent, including wins and SL.\n\n"
        f"If you have any questions, you can always DM me here: {SUPPORT_CONTACT}\n\n"
        "Stay tuned, more setups are coming. 💸",
        "24h_reminder_3day"
    )


async def trial_reminder_3day_2(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_reminder(context, user_id,
        "Day 2 check-in 🧡\n\n"
        "You're almost two days into the trial now. You've probably noticed:\n\n"
        "• How I wait for clean setups, not random entries\n"
        "• How every trade comes with a fixed SL (no \"no-SL gambling\")\n"
        "• How I manage multiple take-profits to lock in profit\n\n"
        "If this style fits you and you want daily guidance, my members stay with me on a 30-Day Premium plan where they get:\n\n"
        "• Full-access signals (all pairs / gold / indices I trade)\n"
        "• Priority support in DM\n"
        "• Occasional market breakdowns & extra tips\n\n"
        "I'll send you a small reminder again when your trial is about to end, so you don't miss the chance to continue.\n\n"
        f"For now – just keep watching the signals and see if it matches your personality and schedule. ❤️\n\n"
        f"If you already know you want to stay, message me 'PREMIUM' here: {SUPPORT_CONTACT}",
        "48h_reminder_3day"
    )


async def trial_reminder_5day_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_reminder(context, user_id,
        "⏱ 1 day (24 hours) has passed, 4 days remaining in your 5-day trial.\n\n"
        f"💬 Enjoying the signals? Upgrade anytime by contacting {SUPPORT_CONTACT}",
        "24h_reminder_5day"
    )


async def trial_reminder_5day_3(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_reminder(context, user_id,
        "⏱ 3 days (72 hours) have passed, 2 days remaining in your 5-day trial.\n\n"
        f"💬 Questions about upgrading? Contact {SUPPORT_CONTACT}",
        "72h_reminder_5day"
    )


async def trial_reminder_5day_4(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_reminder(context, user_id,
        "⏱ 4 days (96 hours) have passed. Only the last 24 hours left in your trial!\n\n"
        f"⚡ Don't miss out! Contact {SUPPORT_CONTACT} to upgrade and keep receiving signals.",
        "96h_reminder_5day"
    )


async def trial_end(context: ContextTypes.DEFAULT_TYPE) -> None:
    """End user's trial."""
    user_id = context.job.data["user_id"]
    logger.info(f"trial_end for user {user_id}")
    
    active = get_active_trial(user_id)
    if not active:
        logger.info(f"No active trial for {user_id}")
        return
    
    if has_used_trial(user_id):
        clear_active_trial(user_id)
        return
    
    # Mark as used
    mark_trial_used(user_id, {
        "trial_ended_at": _now_utc().isoformat(),
        "ended_by": "scheduled_job",
    })
    clear_active_trial(user_id)
    
    # Send end message
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "Your trial just ended 🕊\n\n"
                "Thank you for testing Freya's Flirty Profits for 3 days.\n\n"
                "If you liked the structure of the signals and want to keep going, here are your options:\n\n"
                "✅ 30-Day Premium Membership\n"
                "– Full access to all signals\n"
                "– Same entries I personally take\n"
                "– Ongoing DM support for questions\n\n"
                f"Message me directly: {SUPPORT_CONTACT}\n\n"
                "If you're not ready yet, no pressure – you can also stay connected through my public channel for updates and occasional previews:\n\n"
                f"🌐 Public channel: {GIVEAWAY_CHANNEL_URL}\n\n"
                "Trade safe, manage your risk, and remember: no one wins every trade – the edge comes from discipline. 💚"
            ),
        )
    except Exception as e:
        logger.warning(f"Failed to send trial end message: {e}")
    
    # Remove from channel
    try:
        await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user_id)
        await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user_id)
        logger.info(f"Removed user {user_id} from channel")
    except Exception as e:
        logger.warning(f"Failed to remove user {user_id}: {e}")


# =============================================================================
# Periodic Cleanup
# =============================================================================

async def periodic_cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hourly cleanup of expired trials."""
    now = _now_utc()
    active_trials = get_all_active_trials()
    
    for tg_id_str, info in active_trials.items():
        try:
            user_id = int(tg_id_str)
            trial_end_at_str = info.get("trial_end_at")
            if not trial_end_at_str:
                continue
            
            end_at = _parse_iso_to_utc(trial_end_at_str)
            if now >= end_at:
                if not has_used_trial(user_id):
                    mark_trial_used(user_id, {
                        "trial_ended_at": now.isoformat(),
                        "ended_by": "periodic_cleanup",
                    })
                
                clear_active_trial(user_id)
                
                try:
                    await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user_id)
                    await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user_id)
                except Exception:
                    pass
                
                logger.info(f"Cleaned up expired trial for {user_id}")
        except Exception as e:
            logger.warning(f"Cleanup error for {tg_id_str}: {e}")


# =============================================================================
# Main
# =============================================================================

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Restore jobs for active trials
    now = _now_utc()
    active_trials = get_all_active_trials()
    jq = application.job_queue
    
    for tg_id_str, info in active_trials.items():
        try:
            user_id = int(tg_id_str)
            join_time_str = info.get("join_time")
            total_hours = info.get("total_hours")
            
            if not join_time_str or total_hours is None:
                continue
            
            join_dt = _parse_iso_to_utc(join_time_str)
            total_hours_float = float(total_hours)
            end_dt = join_dt + timedelta(hours=total_hours_float)
            
            is_5day = (total_hours_float == TRIAL_HOURS_5_DAY)
            
            if is_5day:
                reminders = [
                    (REMINDER_1_MINUTES, trial_reminder_5day_1),
                    (REMINDER_3_MINUTES, trial_reminder_5day_3),
                    (REMINDER_4_MINUTES, trial_reminder_5day_4),
                    (TRIAL_END_5DAY_MINUTES, trial_end),
                ]
            else:
                reminders = [
                    (REMINDER_1_MINUTES, trial_reminder_3day_1),
                    (REMINDER_2_MINUTES, trial_reminder_3day_2),
                    (TRIAL_END_3DAY_MINUTES, trial_end),
                ]
            
            for minutes, func in reminders:
                reminder_time = join_dt + timedelta(minutes=minutes)
                delay = reminder_time - now
                if delay.total_seconds() > 0:
                    jq.run_once(func, when=delay, data={"user_id": user_id})
                    logger.info(f"Restored {func.__name__} for user {user_id}")
            
            if end_dt <= now:
                jq.run_once(trial_end, when=timedelta(seconds=0), data={"user_id": user_id})
                
        except Exception as e:
            logger.warning(f"Error restoring jobs for {tg_id_str}: {e}")
    
    # Periodic cleanup
    jq.run_repeating(periodic_cleanup, interval=timedelta(hours=1), first=timedelta(minutes=5))
    
    # Add command handlers (fallback for non-Mini App clients)
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("faq", cmd_faq))
    application.add_handler(CommandHandler("about", cmd_about))
    application.add_handler(CommandHandler("support", cmd_support))
    application.add_handler(CommandHandler("retry", retry_command))
    
    # Admin commands
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("user", user_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("export", export_command))
    application.add_handler(CommandHandler("schedule", schedule_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("list_scheduled", list_scheduled_command))
    application.add_handler(CommandHandler("cancel", cancel_broadcast_command))
    
    # Contact Handler
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(MessageHandler(filters.Regex("^❌ No thanks$"), phone_deny_handler))
    
    # Text fallback handler (must be last message handler to not block commands)
    # Filters.text & ~Filters.command means "any text that is NOT a command"
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_during_phone_verification_handler))
    
    # Add callback handlers (for fallback workflow buttons)
    application.add_handler(CallbackQueryHandler(start_trial_fallback_callback, pattern="^start_trial_fallback$"))
    application.add_handler(CallbackQueryHandler(continue_verification_callback, pattern="^continue_verification$"))
    
    # Add chat member handler
    application.add_handler(
        ChatMemberHandler(trial_chat_member_update, ChatMemberHandler.CHAT_MEMBER)
    )
    
    # Start polling - include 'message' for commands, 'callback_query' for buttons, 'chat_member' for join/leave
    allowed_updates = ["message", "callback_query", "chat_member"]
    logger.info("Starting slim bot with command and callback handlers...")
    application.run_polling(allowed_updates=allowed_updates)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
