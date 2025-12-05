import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# Safe environment parsing helpers
# ============================================================================
def _safe_int_env(name: str, default: int) -> int:
    """Safely parse integer environment variable with fallback."""
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        logger.warning(f"Invalid integer for {name}: {val!r}; using default {default}")
        return default


def _safe_float_env(name: str, default: float) -> float:
    """Safely parse float environment variable with fallback."""
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        logger.warning(f"Invalid float for {name}: {val!r}; using default {default}")
        return default


# ============================================================================
# Timezone-aware datetime helpers
# ============================================================================
def _parse_iso_to_utc(value: str) -> datetime:
    """
    Parse ISO8601 string to timezone-aware UTC datetime.
    If the string has no tzinfo, we assume it was stored as UTC.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        # Assume naive timestamps were stored as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
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

from storage import (
    get_pending_verification,
    set_pending_verification,
    clear_pending_verification,
    append_trial_log,
    has_used_trial,
    mark_trial_used,
    get_used_trial_info,
    get_active_trial,
    set_active_trial,
    clear_active_trial,
    get_all_active_trials,
    get_invite_info,
    set_invite_info,
    get_valid_invite_link,
)


# Load .env file (if present) into environment variables
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TRIAL_CHANNEL_ID = _safe_int_env("TRIAL_CHANNEL_ID", 0)
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
BLOCKED_PHONE_COUNTRY_CODE = os.environ.get("BLOCKED_PHONE_COUNTRY_CODE", "+91")
TIMEZONE_OFFSET_HOURS = _safe_float_env("TIMEZONE_OFFSET_HOURS", 0.0)
API_SECRET = os.environ.get("API_SECRET", "")  # Optional: for web app API authentication

# Validate required environment variables for production deployment
if not BOT_TOKEN:
    error_msg = (
        "BOT_TOKEN is missing! "
        "Set BOT_TOKEN in your environment (e.g. .env on the server or systemd Environment=). "
        "For local development: create a .env file with BOT_TOKEN=your_token"
    )
    logger.error(error_msg)
    raise RuntimeError("BOT_TOKEN is required but not set")

if TRIAL_CHANNEL_ID == 0:
    logger.warning("TRIAL_CHANNEL_ID is not set (using 0). Set it in your environment.")

# Trial duration constants
TRIAL_HOURS_3_DAY = 72
TRIAL_HOURS_5_DAY = 120
TAMPERING_TOLERANCE_SECONDS = 3600  # 1 hour tolerance for trial data validation
TRIAL_COOLDOWN_DAYS = 30  # Days before user can request another trial
INVITE_LINK_EXPIRY_HOURS = 5  # Hours before invite link expires

# Configurable support/giveaway links (fallback to defaults if not set)
GIVEAWAY_CHANNEL_URL = os.environ.get("GIVEAWAY_CHANNEL_URL", "https://t.me/Freya_Trades")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@cogitosk")
FEEDBACK_FORM_URL = os.environ.get("FEEDBACK_FORM_URL", "https://forms.gle/K7ubyn2tvzuYeHXn9")
SUPPORT_FORM_URL = os.environ.get("SUPPORT_FORM_URL", "https://forms.gle/CJbNczZ6BcKjk6Bz9")

logger.info("Bot starting...")
logger.info(f"BASE_URL: {BASE_URL}")
logger.info(f"TRIAL_CHANNEL_ID: {TRIAL_CHANNEL_ID}")
logger.info(f"BOT_TOKEN: {'*' * 10 if BOT_TOKEN else 'NOT SET'}")


# Track last time check to detect clock manipulation
_last_time_check: Optional[datetime] = None

def _now_utc() -> datetime:
    """Get current UTC time with clock manipulation detection."""
    global _last_time_check
    now = datetime.now(timezone.utc)
    
    if _last_time_check:
        # Check if time went backwards (clock manipulation)
        if now < _last_time_check:
            logger.critical(f"System clock went backwards! Previous: {_last_time_check}, Now: {now}")
            # Use previous time + small increment to prevent issues
            now = _last_time_check + timedelta(seconds=1)
    
    _last_time_check = now
    return now


def _is_weekend(dt: datetime) -> bool:
    """
    Weekend check in local time (controlled via TIMEZONE_OFFSET_HOURS).
    """
    local_dt = dt + timedelta(hours=TIMEZONE_OFFSET_HOURS)
    # 5 = Saturday, 6 = Sunday
    return local_dt.weekday() >= 5


def validate_trial_data(trial_data: dict, user_id: int) -> bool:
    """
    Validate trial data hasn't been tampered with.
    Returns True if valid, False if tampered.
    """
    if "join_time" not in trial_data or "total_hours" not in trial_data:
        return False
    
    try:
        join_time = _parse_iso_to_utc(trial_data["join_time"])
        # Normalize total_hours to int for consistent comparisons
        total_hours = int(float(trial_data["total_hours"]))
        
        # Calculate expected end time
        expected_end = join_time + timedelta(hours=total_hours)
        
        # If trial_end_at exists, it should match calculation (within 1 hour tolerance)
        if "trial_end_at" in trial_data:
            claimed_end = _parse_iso_to_utc(trial_data["trial_end_at"])
            time_diff = abs((claimed_end - expected_end).total_seconds())
            if time_diff > TAMPERING_TOLERANCE_SECONDS:  # More than tolerance = tampering
                logger.warning(f"Trial data tampering detected for user {user_id}")
                return False
        
        # Check total_hours is valid (3 or 5 days only)
        if total_hours not in [TRIAL_HOURS_3_DAY, TRIAL_HOURS_5_DAY]:
            logger.warning(f"Invalid total_hours ({total_hours}) for user {user_id}")
            return False
        
        # Check join_time is not in future
        if join_time > _now_utc():
            logger.warning(f"Join time in future for user {user_id}")
            return False
        
        return True
    except Exception as e:
        logger.warning(f"Error validating trial data for user {user_id}: {e}")
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return

    # If user already consumed their free trial, don't allow another one
    if has_used_trial(user.id):
        await update.message.reply_text(
            "You have already used your free 3-day trial once.\n\n"
            "🎁 For more chances, you can join our giveaway channel:\n"
            f"{GIVEAWAY_CHANNEL_URL}\n\n"
            f"💬 Or DM {SUPPORT_CONTACT} to upgrade to the premium signals.",
        )
        return

    # Check if user has an ACTIVE trial (currently in trial period)
    active_trial = get_active_trial(user.id)
    if active_trial and "join_time" in active_trial and "total_hours" in active_trial:
        try:
            join_time = _parse_iso_to_utc(active_trial["join_time"])
            total_hours = float(active_trial["total_hours"])
            trial_end_at = join_time + timedelta(hours=total_hours)
            now = _now_utc()
            
            # If trial hasn't ended yet, user is still in active trial
            if now < trial_end_at:
                elapsed_hours = (now - join_time).total_seconds() / 3600.0
                remaining_hours = total_hours - elapsed_hours
                elapsed_rounded = round(elapsed_hours, 1)
                remaining_rounded = round(remaining_hours, 1)
                total_days = int(total_hours / 24)
                
                await update.message.reply_text(
                    f"✅ You are currently in your {total_days}-day free trial!\n\n"
                    f"⏱ Time elapsed: {elapsed_rounded} hours\n"
                    f"⏳ Time remaining: {remaining_rounded} hours\n\n"
                    "You will receive reminders as your trial approaches the end.\n\n"
                    f"💬 Questions? DM {SUPPORT_CONTACT}",
                )
                return
        except Exception as e:
            logger.warning(f"Error checking active trial for user {user.id}: {e}")
            # Continue to show normal start message if check fails

    keyboard = [
        [InlineKeyboardButton("🎁 Get Free Trial", callback_data="start_trial")],
    ]
    await update.message.reply_text(
        "Welcome! Tap the button below to start your free trial verification.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def start_trial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = query.from_user
    if not user:
        return
    tg_id = user.id

    # Check if user already consumed their free trial BEFORE showing verification page
    if has_used_trial(tg_id):
        await query.edit_message_text(
            "You have already used your free 3-day trial once.\n\n"
            "🎁 For more chances, you can join our giveaway channel:\n"
            f"{GIVEAWAY_CHANNEL_URL}\n\n"
            f"💬 Or DM {SUPPORT_CONTACT} to upgrade to the premium signals.",
        )
        return

    # Check if user has an ACTIVE trial (currently in trial period)
    active_trial = get_active_trial(tg_id)
    if active_trial and "join_time" in active_trial and "total_hours" in active_trial:
        try:
            join_time = _parse_iso_to_utc(active_trial["join_time"])
            total_hours = float(active_trial["total_hours"])
            trial_end_at = join_time + timedelta(hours=total_hours)
            now = _now_utc()
            
            # If trial hasn't ended yet, user is still in active trial
            if now < trial_end_at:
                elapsed_hours = (now - join_time).total_seconds() / 3600.0
                remaining_hours = total_hours - elapsed_hours
                elapsed_rounded = round(elapsed_hours, 1)
                remaining_rounded = round(remaining_hours, 1)
                total_days = int(total_hours / 24)
                
                await query.edit_message_text(
                    f"✅ You are currently in your {total_days}-day free trial!\n\n"
                    f"⏱ Time elapsed: {elapsed_rounded} hours\n"
                    f"⏳ Time remaining: {remaining_rounded} hours\n\n"
                    "You will receive reminders as your trial approaches the end.\n\n"
                    f"💬 Questions? DM {SUPPORT_CONTACT}",
                )
                return
        except Exception as e:
            logger.warning(f"Error checking active trial for user {tg_id}: {e}")
            # Continue to show verification page if check fails

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
    if not query:
        return
    await query.answer()

    user = query.from_user
    if not user:
        return
    tg_id = user.id

    # Try to get data from local storage first (same machine)
    data = get_pending_verification(tg_id)
    logger.debug(f"Continue verification check for tg_id={tg_id}: local data found = {data is not None}")
    
    # If not found locally, try to fetch from web app API.
    # This works even if web app and bot are on separate processes / machines,
    # as long as BASE_URL points to your HTTPS domain on the droplet.
    if not data or not data.get("step1_ok"):
        logger.debug("Local data missing or step1_ok=False, trying web app API...")
        try:
            import aiohttp
            api_url = f"{BASE_URL.rstrip('/')}/api/get-verification?tg_id={tg_id}"
            logger.debug(f"Trying to fetch from web app API: {api_url}")
            headers = {}
            if API_SECRET:
                # Use header-only authentication (more secure than URL query string)
                headers["X-API-Secret"] = API_SECRET
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get("success") and result.get("data"):
                            data = result["data"]
                            logger.debug(f"Got data from web app API for tg_id={tg_id}")
                            logger.debug(f"Data keys: {list(data.keys())}, step1_ok: {data.get('step1_ok')}")
                            # Also save locally for future use
                            set_pending_verification(tg_id, data)
                        else:
                            logger.debug("API returned success=False or no data")
                    elif resp.status == 401:
                        logger.warning("API authentication failed - check API_SECRET")
                    elif resp.status == 429:
                        logger.warning("API rate limited")
                    else:
                        logger.debug(f"API returned status {resp.status}")
        except Exception as e:
            logger.warning(f"Could not fetch from API: {e}", exc_info=True)
            # Continue with local check
    else:
        logger.debug(f"Found valid local data: step1_ok={data.get('step1_ok')}")
    
    if not data or not data.get("step1_ok"):
        logger.debug(f"No valid verification data found for tg_id={tg_id}")
        await query.edit_message_text(
            "We could not find your web verification.\n"
            "Please tap 'Get Free Trial' again and complete the web step first.\n\n"
            "⚠️ Make sure you:\n"
            "1. Open the verification page\n"
            "2. Turn off VPN/Proxy\n"
            "3. Fill in your details (name, country, email optional)\n"
            "4. Submit the form\n"
            "5. Close the mini-app\n"
            "6. Then click 'Continue verification'"
        )
        return
    
    logger.info(f"Verification Step 1 confirmed passed for tg_id={tg_id}")

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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Help command explaining the bot and verification process."""
    help_text = (
        "🤖 *About This Bot*\n\n"
        "This bot is used to manage users accessing premium content and services.\n\n"
        "📋 *Available Commands:*\n"
        "• /start - Start the bot and begin free trial\n"
        "• /help - Help and commands list\n"
        "• /faq - Frequently asked questions\n"
        "• /about - About this bot\n"
        "• /support - Contact support\n\n"
        "🔐 *Verification Process:*\n\n"
        "*Step 1: Initial Verification*\n"
        "1. Click on /start command\n"
        "2. A 'Get Free Trial' button will appear\n"
        "3. Click on the button to open the verification page\n"
        "4. Turn off VPN/Proxy before proceeding\n"
        "5. IP test will happen automatically\n"
        "6. Fill in your details:\n"
        "   • Name (required)\n"
        "   • Country (required)\n"
        "   • Email (optional - you can delete later)\n"
        "7. Close the Telegram mini-app\n\n"
        "*Step 2: Phone Verification*\n"
        "1. If Step 1 passed, click on 'Continue verification'\n"
        "2. Click on 'Allow phone number access' button\n"
        "   (We need this to confirm you're not a bot)\n"
        "3. Share your phone number when prompted\n"
        "4. You will receive a one-time premium group invite link\n"
        "5. Join the group to access premium content\n\n"
        "✅ Once both verifications are complete, you'll gain access to premium features!"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FAQ command with frequently asked questions."""
    now = _now_utc()
    is_weekend = _is_weekend(now)
    
    if is_weekend:
        trial_days = "5 days"
        trial_reason = "Since today is a weekend and the market is closed, you get 5 days of access."
    else:
        trial_days = "3 days"
        trial_reason = "Since today is not a weekend, you get 3 days of access."
    
    faq_text = (
        "❓ *Frequently Asked Questions*\n\n"
        "1️⃣ *How many days can I use the free trial?*\n"
        f"   You can use the free trial for {trial_days}. {trial_reason}\n\n"
        "2️⃣ *Can I delete my information later?*\n"
        "   Yes, absolutely! You can request deletion of your information at any time.\n\n"
        "3️⃣ *Why do you need my phone number?*\n"
        "   We need your phone number to verify that you're a real person and not a bot. "
        "Your privacy is important to us, and we don't share your data with third parties.\n\n"
        "4️⃣ *What if I can't access the premium group?*\n"
        "   If you're having trouble accessing the group, please use /support command "
        "to contact our team. We'll help you resolve the issue.\n\n"
        "5️⃣ *Can I share the invite link with others?*\n"
        "   No, the invite link is one-time use and unique to your account. "
        "Please do not share it with others.\n\n"
        "6️⃣ *What happens after my trial ends?*\n"
        "   After your trial period ends, you'll need to upgrade to a paid plan "
        "to continue accessing premium content and services."
    )
    await update.message.reply_text(faq_text, parse_mode="Markdown")


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """About command with brief description of the bot."""
    about_text = (
        "ℹ️ *About This Bot*\n\n"
        "This bot helps manage access to premium content and services through a secure "
        "verification process. We provide a free trial period so you can experience our "
        "premium features before committing to a paid plan.\n\n"
        "Our verification system ensures that only legitimate users can access premium "
        "content, helping us maintain quality and prevent abuse.\n\n"
        "For support or questions, use /support to contact our team."
    )
    await update.message.reply_text(about_text, parse_mode="Markdown")


async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Support command with contact form link."""
    support_text = (
        "🆘 *Support*\n\n"
        "If you can't access the premium group or need assistance, please submit this form:\n\n"
        f"👉 {SUPPORT_FORM_URL}\n\n"
        "Our team will contact you shortly to help resolve your issue."
    )
    await update.message.reply_text(support_text, parse_mode="Markdown")


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    contact = update.message.contact
    user = update.effective_user

    if not contact:
        return

    # Ensure the shared contact belongs to the same user
    # Improved validation: require user_id to match (prevents sharing other contacts)
    if not contact.user_id:
        await update.message.reply_text(
            "Please share your phone number directly from Telegram. "
            "The contact must be linked to your Telegram account."
        )
        return
    
    if contact.user_id != user.id:
        await update.message.reply_text("Please share your own phone number using the button.")
        return

    phone = contact.phone_number or ""
    if not phone.startswith("+"):
        phone = "+" + phone

    data = get_pending_verification(user.id) or {}

    # Block phone numbers by country code (configurable via env BLOCKED_PHONE_COUNTRY_CODE, default +91)
    if BLOCKED_PHONE_COUNTRY_CODE and phone.startswith(BLOCKED_PHONE_COUNTRY_CODE):
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

    # Before generating a new invite link, check if user recently generated one
    # Use atomic function to prevent race condition (multiple rapid clicks)
    now = _now_utc()
    existing_link = get_valid_invite_link(user.id, now)
    
    # Send message if existing link is valid
    if existing_link:
        await update.message.reply_text(
            "You already generated a trial invite link recently.\n\n"
            "Please use this link to join the trial channel:\n"
            f"{existing_link}\n\n"
            "If you have any issues accessing it, use /support to contact us."
        )
        return

    await update.message.reply_text("Verification 2 passed ✅. Generating your one-time invite link...")

    bot = context.bot
    try:
        # Expire invite link after configured hours
        expires_at_dt = now + timedelta(hours=INVITE_LINK_EXPIRY_HOURS)
        invite_link = await bot.create_chat_invite_link(
            chat_id=TRIAL_CHANNEL_ID,
            member_limit=1,
            expire_date=int(expires_at_dt.timestamp()),
        )
    except Exception as e:  # pragma: no cover - defensive
        await update.message.reply_text(
            "Failed to create an invite link. Please try again later."
        )
        return

    # Store invite metadata so we don't generate unlimited fresh links
    set_invite_info(
        user.id,
        {
            "invite_link": invite_link.invite_link,
            "invite_created_at": now.isoformat(),
            "invite_expires_at": expires_at_dt.isoformat(),
        },
    )

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

    # Clear pending verification record now that verification is complete
    clear_pending_verification(user.id)


async def periodic_trial_cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Periodic cleanup job that runs every hour to check all active trials
    and end expired ones. This is a fallback in case scheduled jobs fail.
    """
    now = _now_utc()
    active_trials = get_all_active_trials()
    
    cleaned_count = 0
    for tg_id_str, info in active_trials.items():
        try:
            user_id = int(tg_id_str)
            
            # Validate trial data first
            if not validate_trial_data(info, user_id):
                logger.warning(f"Invalid trial data for user {user_id}, cleaning up")
                clear_active_trial(user_id)
                cleaned_count += 1
                continue
            
            trial_end_at_str = info.get("trial_end_at")
            if not trial_end_at_str:
                continue
            
            end_at = _parse_iso_to_utc(trial_end_at_str)
            
            # If trial expired, end it now
            if now >= end_at:
                # Mark as used
                mark_trial_used(user_id, {
                    "trial_ended_at": now.isoformat(),
                    "ended_by": "periodic_cleanup"
                })
                
                # Remove from channel
                try:
                    await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user_id)
                    await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user_id)
                except Exception:
                    pass
                
                # Clear active trial
                clear_active_trial(user_id)
                cleaned_count += 1
                
                # Notify user
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "⛔ Your trial has finished. If you enjoyed the signals, you can upgrade "
                            "to a paid plan to continue."
                        ),
                    )
                except Exception:
                    pass
                
                logger.info(f"Cleaned up expired trial for user {user_id}")
        except Exception as e:
            logger.warning(f"Error in periodic cleanup for {tg_id_str}: {e}")
    
    if cleaned_count > 0:
        logger.info(f"Periodic cleanup: Ended {cleaned_count} expired trial(s)")


async def trial_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle chat member updates (join/leave) in the trial channel."""
    if not update.chat_member:
        logger.warning("trial_chat_member_update called but update.chat_member is None")
        return
        
    chat_member = update.chat_member
    chat = chat_member.chat

    logger.debug(f"Chat member update received for chat_id={chat.id}, TRIAL_CHANNEL_ID={TRIAL_CHANNEL_ID}")
    
    if chat.id != TRIAL_CHANNEL_ID:
        logger.debug(f"Ignoring chat member update for chat_id={chat.id} (not trial channel)")
        return

    old = chat_member.old_chat_member
    new = chat_member.new_chat_member

    # Detect join: previously left/kicked, now member/admin
    if old.status in ("left", "kicked") and new.status in ("member", "administrator"):
        user = new.user
        if not user:
            # Safety check: user should always be present, but handle edge case
            logger.warning("new.user is None in trial_chat_member_update")
            return
        now = _now_utc()

        # Check if user has already used a trial (prevent rejoin extension exploit)
        if has_used_trial(user.id):
            # User already used trial - check if enough time has passed (30 day cooldown)
            user_trial_info = get_used_trial_info(user.id)
            if not user_trial_info:
                # Should not happen if has_used_trial returned True, but block to be safe
                await context.bot.send_message(
                    chat_id=user.id,
                    text=(
                        "You have already used a trial. Please wait before requesting another.\n\n"
                        "🎁 For more chances, you can join our giveaway channel:\n"
                        f"{GIVEAWAY_CHANNEL_URL}\n\n"
                        f"💬 Or DM {SUPPORT_CONTACT} to upgrade to the premium signals."
                    ),
                )
                try:
                    await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user.id)
                    await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user.id)
                except Exception:
                    pass
                return
            
            # Check when trial ended
            trial_ended_at_str = user_trial_info.get("trial_ended_at") or user_trial_info.get("left_early_at")
            if trial_ended_at_str:
                try:
                    ended_at = _parse_iso_to_utc(trial_ended_at_str)
                    days_since_end = (now - ended_at).total_seconds() / 86400
                    
                    if days_since_end < TRIAL_COOLDOWN_DAYS:  # Cooldown period
                        await context.bot.send_message(
                            chat_id=user.id,
                            text=(
                                f"You recently used a trial. Please wait {TRIAL_COOLDOWN_DAYS} days before requesting another.\n\n"
                                "🎁 For more chances, you can join our giveaway channel:\n"
                                f"{GIVEAWAY_CHANNEL_URL}\n\n"
                                f"💬 Or DM {SUPPORT_CONTACT} to upgrade to the premium signals."
                            ),
                        )
                        # Remove from channel
                        try:
                            await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user.id)
                            await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user.id)
                        except Exception:
                            pass
                        return
                except Exception:
                    # If we can't parse the date, block to be safe
                    await context.bot.send_message(
                        chat_id=user.id,
                        text=(
                            "You have already used a trial. Please wait before requesting another.\n\n"
                            "🎁 For more chances, you can join our giveaway channel:\n"
                            f"{GIVEAWAY_CHANNEL_URL}\n\n"
                            f"💬 Or DM {SUPPORT_CONTACT} to upgrade to the premium signals."
                        ),
                    )
                    try:
                        await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user.id)
                        await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user.id)
                    except Exception:
                        pass
                    return
            else:
                # No end date recorded but they used a trial - block to be safe
                await context.bot.send_message(
                    chat_id=user.id,
                    text=(
                        "You have already used a trial. Please wait before requesting another.\n\n"
                        "🎁 For more chances, you can join our giveaway channel:\n"
                        f"{GIVEAWAY_CHANNEL_URL}\n\n"
                        f"💬 Or DM {SUPPORT_CONTACT} to upgrade to the premium signals."
                    ),
                )
                try:
                    await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user.id)
                    await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user.id)
                except Exception:
                    pass
                return
        
        # Determine trial duration based on weekend
        if _is_weekend(now):
            trial_days = 5
            total_hours = TRIAL_HOURS_5_DAY
        else:
            trial_days = 3
            total_hours = TRIAL_HOURS_3_DAY

        # If an active trial already exists and has not yet expired, avoid double-scheduling
        existing = get_active_trial(user.id)
        if existing:
            # Validate trial data hasn't been tampered with
            if not validate_trial_data(existing, user.id):
                logger.warning(f"Invalid trial data for user {user.id}, clearing and restarting")
                clear_active_trial(user.id)
            elif "trial_end_at" in existing:
                try:
                    end_at = _parse_iso_to_utc(existing["trial_end_at"])
                    if now < end_at:
                        # Trial is already running; do not re-start it
                        return
                except Exception:
                    pass

        trial_end_at = now + timedelta(hours=total_hours)

        # Track active trial so we can compute remaining hours if user leaves early and restore after restart
        set_active_trial(
            user.id,
            {
                "join_time": now.isoformat(),
                "total_hours": total_hours,
                "trial_end_at": trial_end_at.isoformat(),
            },
        )

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

    # Detect user leaving during trial phase and send feedback form
    if old.status in ("member", "administrator") and new.status in ("left", "kicked"):
        logger.info(f"User left/kicked detected: old_status={old.status}, new_status={new.status}")
        
        # Ignore leaves caused by the bot itself (e.g. scheduled trial_end ban/unban)
        try:
            bot_user = await context.bot.get_me()
        except Exception as e:
            logger.warning(f"Failed to get bot user info: {e}")
            bot_user = None

        # If the actor is the bot, don't send feedback (this is likely trial_end cleanup)
        if bot_user and chat_member.from_user and chat_member.from_user.id == bot_user.id:
            logger.info("Leave was caused by bot itself, skipping feedback message")
            return

        user = old.user
        if not user:
            # Safety check: user should always be present, but handle edge case
            logger.warning("old.user is None in trial_chat_member_update")
            return

        logger.info(f"Processing leave for user_id={user.id}, username={user.username}")

        # Try to compute how many trial hours they used and how many were remaining
        usage_info = ""
        total_days = 3  # Default
        try:
            active = get_active_trial(user.id)
            logger.debug(f"Active trial data for user {user.id}: {active}")
            
            if active and "join_time" in active and "total_hours" in active:
                join_time = _parse_iso_to_utc(active["join_time"])
                total_hours = float(active["total_hours"])
                total_days = int(total_hours / 24)
                now = _now_utc()
                elapsed_hours = (now - join_time).total_seconds() / 3600.0
                remaining_hours = max(0.0, total_hours - elapsed_hours)

                # Round for nicer display
                elapsed_hours_rounded = round(elapsed_hours, 1)
                remaining_hours_rounded = round(remaining_hours, 1)

                usage_info = (
                    f"\n\n📊 Trial Usage Summary:\n"
                    f"• You consumed: {elapsed_hours_rounded} hours out of {int(total_hours)} hours ({total_days} days)\n"
                    f"• Remaining unused: {remaining_hours_rounded} hours"
                )
                logger.info(f"User {user.id} consumed {elapsed_hours_rounded}/{total_hours} hours, {remaining_hours_rounded} remaining")
            else:
                logger.warning(f"No active trial found for user {user.id} (may have already been cleared or never started)")
                # Still send a generic message
                usage_info = "\n\nYour trial data was not found - it may have already expired."
        except Exception as e:
            logger.error(f"Failed to compute remaining trial hours for user_id={user.id}: {e}", exc_info=True)
            usage_info = ""  # Continue without usage info

        # Send message to user about leaving
        try:
            leave_message = (
                f"👋 You have left the trial channel.\n"
                f"{usage_info}\n\n"
                f"Your free {total_days}-day trial has been marked as consumed.\n\n"
                "We hope you enjoyed testing our signals! 🙌\n\n"
                f"📝 We'd love to hear your feedback:\n{FEEDBACK_FORM_URL}\n\n"
                f"🎁 For more chances, join our giveaway: {GIVEAWAY_CHANNEL_URL}\n"
                f"💬 Ready to upgrade? DM {SUPPORT_CONTACT}"
            )
            
            await context.bot.send_message(
                chat_id=user.id,
                text=leave_message,
            )
            logger.info(f"Successfully sent leave message to user {user.id}")
        except Exception as e:
            logger.error(f"Failed to send leave message to user_id={user.id}: {e}", exc_info=True)
            # Continue with marking trial as used even if message failed

        # Treat this as a consumed trial as well
        try:
            # Store more detailed info about the early leave
            active = get_active_trial(user.id)
            leave_info = {
                "left_early_at": _now_utc().isoformat(),
                "reason": "user_left_channel"
            }
            if active:
                leave_info["join_time"] = active.get("join_time")
                leave_info["total_hours"] = active.get("total_hours")
            
            mark_trial_used(user.id, leave_info)
            logger.info(f"Marked trial as used for user {user.id} (left early)")
        except Exception as e:
            logger.warning(f"Failed to mark trial used on early leave for user_id={user.id}: {e}", exc_info=True)

        # Clear active trial tracking since they left
        try:
            clear_active_trial(user.id)
            logger.info(f"Cleared active trial for user {user.id}")
        except Exception as e:
            logger.warning(f"Failed to clear active trial for user_id={user.id}: {e}", exc_info=True)


async def _send_trial_reminder(context: ContextTypes.DEFAULT_TYPE, user_id: int, message: str) -> bool:
    """
    Helper function to send trial reminders with proper error handling.
    Returns True if message was sent successfully, False otherwise.
    """
    # Check if user still has an active trial before sending reminder
    active_trial = get_active_trial(user_id)
    if not active_trial:
        logger.info(f"Skipping reminder for user {user_id} - no active trial (user may have left)")
        return False
    
    # Verify trial hasn't expired yet
    try:
        trial_end_str = active_trial.get("trial_end_at")
        if trial_end_str:
            trial_end_at = _parse_iso_to_utc(trial_end_str)
            if _now_utc() >= trial_end_at:
                logger.info(f"Skipping reminder for user {user_id} - trial already expired")
                return False
    except Exception as e:
        logger.warning(f"Error checking trial expiry for user {user_id}: {e}")
    
    try:
        await context.bot.send_message(chat_id=user_id, text=message)
        logger.info(f"Successfully sent trial reminder to user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to send trial reminder to user {user_id}: {e}")
        return False


async def trial_reminder_3day_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_trial_reminder(
        context, user_id,
        "⏱ 1 day (24 hours) has passed, 2 days remaining in your trial.\n\n"
        f"💬 Enjoying the signals? Upgrade anytime by contacting {SUPPORT_CONTACT}"
    )


async def trial_reminder_3day_2(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_trial_reminder(
        context, user_id,
        "⏱ 2 days (48 hours) have passed. Only the last 24 hours left in your trial!\n\n"
        f"⚡ Don't miss out! Contact {SUPPORT_CONTACT} to upgrade and keep receiving signals."
    )


async def trial_reminder_5day_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_trial_reminder(
        context, user_id,
        "⏱ 1 day (24 hours) has passed, 4 days remaining in your 5-day trial.\n\n"
        f"💬 Enjoying the signals? Upgrade anytime by contacting {SUPPORT_CONTACT}"
    )


async def trial_reminder_5day_3(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_trial_reminder(
        context, user_id,
        "⏱ 3 days (72 hours) have passed, 2 days remaining in your 5-day trial.\n\n"
        f"💬 Questions about upgrading? Contact {SUPPORT_CONTACT}"
    )


async def trial_reminder_5day_4(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_trial_reminder(
        context, user_id,
        "⏱ 4 days (96 hours) have passed. Only the last 24 hours left in your trial!\n\n"
        f"⚡ Don't miss out! Contact {SUPPORT_CONTACT} to upgrade and keep receiving signals."
    )


async def trial_end(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    # Mark this user as having used their free trial first (JSON is the source of truth)
    try:
        mark_trial_used(
            user_id,
            {
                "trial_ended_at": _now_utc().isoformat(),
            },
        )
    except Exception as e:
        # Do not crash job on storage issues; just log if needed
        logger.warning(f"Failed to mark trial as used for user_id={user_id}: {e}")

    # Clear active trial tracking on natural trial end
    clear_active_trial(user_id)

    # Notify user and remove them from trial channel
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "⛔ Your trial has finished. If you enjoyed the signals, you can upgrade "
                "to a paid plan to continue."
            ),
        )
    except Exception:
        # User might have blocked the bot or never opened DM; ignore
        pass

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
    
    # IMPORTANT: For ChatMemberHandler to work, ensure:
    # 1. Bot is an admin in the trial channel/group
    # 2. Bot has necessary permissions (at minimum: view members)
    # 3. Bot is not in "Group Privacy" mode (BotFather -> Bot Settings -> Group Privacy -> OFF)
    #    OR the bot must be added as admin to receive member updates
    logger.info("Application built. ChatMemberHandler requires bot to be admin in trial channel.")

    # Restore trial end jobs and reminder jobs after a restart based on active_trials.json
    try:
        now = _now_utc()
        active_trials = get_all_active_trials()
        jq = application.job_queue

        for tg_id_str, info in active_trials.items():
            try:
                user_id = int(tg_id_str)
            except ValueError:
                continue

            # Validate trial data hasn't been tampered with
            if not validate_trial_data(info, user_id):
                logger.warning(f"Invalid trial data for user {user_id} on restore, clearing")
                clear_active_trial(user_id)
                continue

            trial_end_at_str = info.get("trial_end_at")
            join_time_str = info.get("join_time")
            total_hours = info.get("total_hours")

            if not join_time_str or total_hours is None:
                continue

            try:
                join_dt = _parse_iso_to_utc(join_time_str)
                total_hours_float = float(total_hours)
                
                # Calculate end time
                if trial_end_at_str:
                    end_dt = _parse_iso_to_utc(trial_end_at_str)
                else:
                    end_dt = join_dt + timedelta(hours=total_hours_float)
                
                # Determine trial type (3-day or 5-day) based on total_hours
                is_5day = (total_hours_float == TRIAL_HOURS_5_DAY)
                
                # Restore reminder jobs based on trial type
                if is_5day:
                    # 5-day trial: reminders at +24h, +72h, +96h, end at +120h
                    reminder_times = [
                        (24, trial_reminder_5day_1),
                        (72, trial_reminder_5day_3),
                        (96, trial_reminder_5day_4),
                        (120, trial_end),
                    ]
                else:
                    # 3-day trial: reminders at +24h, +48h, end at +72h
                    reminder_times = [
                        (24, trial_reminder_3day_1),
                        (48, trial_reminder_3day_2),
                        (72, trial_end),
                    ]
                
                # Schedule each reminder job if it hasn't passed yet
                for hours_offset, job_func in reminder_times:
                    reminder_time = join_dt + timedelta(hours=hours_offset)
                    delay = reminder_time - now
                    
                    # Only schedule if the reminder time hasn't passed yet
                    if delay.total_seconds() > 0:
                        jq.run_once(
                            job_func,
                            when=delay,
                            data={"user_id": user_id},
                        )
                        logger.info(f"Restored {job_func.__name__} job for user {user_id}, scheduled in {delay}")
                    else:
                        logger.debug(f"Skipped {job_func.__name__} job for user {user_id} (already passed)")
                
                # If trial end has passed, schedule immediate cleanup
                if end_dt <= now:
                    jq.run_once(
                        trial_end,
                        when=timedelta(seconds=0),
                        data={"user_id": user_id},
                    )
                    logger.info(f"Scheduled immediate trial_end cleanup for user {user_id} (trial expired)")
                    
            except Exception as e:
                logger.warning(f"Error restoring jobs for user {user_id}: {e}", exc_info=True)
                continue
                
    except Exception as e:
        logger.warning(f"Failed to restore active trial jobs: {e}", exc_info=True)
    
    # Add periodic cleanup job as fallback (runs every hour)
    # This ensures trials end even if scheduled jobs fail
    try:
        application.job_queue.run_repeating(
            periodic_trial_cleanup,
            interval=timedelta(hours=1),  # Check every hour
            first=timedelta(minutes=5)  # Start 5 minutes after bot starts
        )
        logger.info("Periodic trial cleanup job scheduled")
    except Exception as e:
        logger.warning(f"Failed to schedule periodic cleanup job: {e}")
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("faq", faq_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("support", support_command))
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
        logger.info("Starting Telegram bot...")
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


