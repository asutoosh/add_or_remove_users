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
    ReplyKeyboardRemove,
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
    track_start_click,
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

# Trial duration constants (can be overridden via env for testing)
# Set these to small values (e.g., 5 minutes = 0.083 hours) for testing
TRIAL_HOURS_3_DAY = _safe_float_env("TRIAL_HOURS_3_DAY", 72.0)
TRIAL_HOURS_5_DAY = _safe_float_env("TRIAL_HOURS_5_DAY", 120.0)
TAMPERING_TOLERANCE_SECONDS = 3600  # 1 hour tolerance for trial data validation
TRIAL_COOLDOWN_DAYS = 30  # Days before user can request another trial
INVITE_LINK_EXPIRY_HOURS = 5  # Hours before invite link expires

# Reminder timing (in MINUTES for easier testing - set to 3, 5, 7 for quick tests)
# For production: 1440 (24h), 2880 (48h), 4320 (72h), 5760 (96h), 7200 (120h)
REMINDER_1_MINUTES = _safe_float_env("REMINDER_1_MINUTES", 1440.0)  # 24 hours default
REMINDER_2_MINUTES = _safe_float_env("REMINDER_2_MINUTES", 2880.0)  # 48 hours default
TRIAL_END_3DAY_MINUTES = _safe_float_env("TRIAL_END_3DAY_MINUTES", 4320.0)  # 72 hours default
REMINDER_3_MINUTES = _safe_float_env("REMINDER_3_MINUTES", 4320.0)  # 72 hours (5-day trial)
REMINDER_4_MINUTES = _safe_float_env("REMINDER_4_MINUTES", 5760.0)  # 96 hours (5-day trial)
TRIAL_END_5DAY_MINUTES = _safe_float_env("TRIAL_END_5DAY_MINUTES", 7200.0)  # 120 hours default

# Configurable support/giveaway links (fallback to defaults if not set)
GIVEAWAY_CHANNEL_URL = os.environ.get("GIVEAWAY_CHANNEL_URL", "https://t.me/Freya_Trades")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@cogitosk")
FEEDBACK_FORM_URL = os.environ.get("FEEDBACK_FORM_URL", "https://forms.gle/K7ubyn2tvzuYeHXn9")
SUPPORT_FORM_URL = os.environ.get("SUPPORT_FORM_URL", "https://forms.gle/CJbNczZ6BcKjk6Bz9")

logger.info("Bot starting...")
logger.info(f"BASE_URL: {BASE_URL}")
logger.info(f"TRIAL_CHANNEL_ID: {TRIAL_CHANNEL_ID}")
logger.info(f"BOT_TOKEN: {'*' * 10 if BOT_TOKEN else 'NOT SET'}")
logger.info(f"=== TIMING CONFIGURATION ===")
logger.info(f"TRIAL_HOURS_3_DAY: {TRIAL_HOURS_3_DAY} hours")
logger.info(f"TRIAL_HOURS_5_DAY: {TRIAL_HOURS_5_DAY} hours")
logger.info(f"REMINDER_1_MINUTES: {REMINDER_1_MINUTES} min ({REMINDER_1_MINUTES/60:.1f} hours)")
logger.info(f"REMINDER_2_MINUTES: {REMINDER_2_MINUTES} min ({REMINDER_2_MINUTES/60:.1f} hours)")
logger.info(f"TRIAL_END_3DAY_MINUTES: {TRIAL_END_3DAY_MINUTES} min ({TRIAL_END_3DAY_MINUTES/60:.1f} hours)")
logger.info(f"REMINDER_3_MINUTES: {REMINDER_3_MINUTES} min ({REMINDER_3_MINUTES/60:.1f} hours)")
logger.info(f"REMINDER_4_MINUTES: {REMINDER_4_MINUTES} min ({REMINDER_4_MINUTES/60:.1f} hours)")
logger.info(f"TRIAL_END_5DAY_MINUTES: {TRIAL_END_5DAY_MINUTES} min ({TRIAL_END_5DAY_MINUTES/60:.1f} hours)")

# Validate TRIAL_CHANNEL_ID format
if TRIAL_CHANNEL_ID == 0:
    logger.error("⚠️ TRIAL_CHANNEL_ID is 0! Set it in your .env file. Join/leave detection will NOT work!")
elif TRIAL_CHANNEL_ID > 0:
    logger.warning(f"⚠️ TRIAL_CHANNEL_ID ({TRIAL_CHANNEL_ID}) is positive. Channels/supergroups usually have NEGATIVE IDs like -1001234567890")


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

    # Track /start command click - store user info or increment click count
    track_start_click({
        "tg_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "language_code": user.language_code,
        "is_premium": getattr(user, 'is_premium', False),
        "is_bot": user.is_bot,
    })

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
        "security and internal analytics. We do not sell or share this data. "
        "You can request deletion at any time.",
        reply_markup=keyboard,
    )


async def retry_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Simple /retry command to re-show the contact request keyboard if user cancelled.
    """
    contact_button = KeyboardButton(text="📱 Share phone number", request_contact=True)
    deny_button = KeyboardButton(text="❌ No thanks")
    keyboard = ReplyKeyboardMarkup(
        [[contact_button], [deny_button]], 
        resize_keyboard=True, 
        one_time_keyboard=True
    )

    await update.message.reply_text(
        "Let's try again. Please share your phone number using the button below.",
        reply_markup=keyboard,
    )


async def phone_deny_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle when user clicks 'No thanks' / deny button for phone verification.
    """
    if not update.message or not update.effective_user:
        return
    
    user = update.effective_user
    logger.info(f"User {user.id} denied phone verification")
    
    await update.message.reply_text(
        "No problem! 🙏\n\n"
        "Unfortunately, we need phone verification to prevent abuse and ensure "
        "fair access to the trial.\n\n"
        "If you change your mind, you can:\n"
        "• Type /retry to try again\n"
        "• Type /start to restart the process\n\n"
        f"🎁 You can also join our public giveaway channel for free content:\n"
        f"{GIVEAWAY_CHANNEL_URL}\n\n"
        f"💬 Or DM {SUPPORT_CONTACT} if you have any questions!",
        reply_markup=ReplyKeyboardRemove(),
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


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command to check bot status and debug info.
    Shows active trials, used trials count, scheduled jobs, etc.
    """
    user = update.effective_user
    if not user or not update.message:
        return
    
    # Only allow bot owner/admin to use this (you can customize this check)
    # For now, show to everyone but you may want to restrict it
    
    try:
        from storage import get_all_active_trials, USED_TRIALS_FILE, ACTIVE_TRIALS_FILE
        import os
        
        active_trials = get_all_active_trials()
        
        # Count used trials
        used_trials_count = 0
        try:
            import json
            if os.path.exists(USED_TRIALS_FILE):
                with open(USED_TRIALS_FILE, 'r') as f:
                    used_data = json.load(f)
                    used_trials_count = len(used_data)
        except Exception:
            used_trials_count = -1  # Error reading
        
        # Get job queue info
        jobs = context.job_queue.jobs() if context.job_queue else []
        job_count = len(jobs)
        
        # List pending jobs with their scheduled times
        job_list = []
        for job in jobs[:10]:  # Show max 10 jobs
            if job.data and "user_id" in job.data:
                next_run = job.next_t.strftime("%Y-%m-%d %H:%M:%S UTC") if job.next_t else "unknown"
                job_name = job.name or job.callback.__name__ if job.callback else "unknown"
                job_list.append(f"  • User {job.data['user_id']}: {job_name} at {next_run}")
        
        # Check if files exist and are writable
        files_status = []
        for fname, fpath in [("active_trials.json", ACTIVE_TRIALS_FILE), ("used_trials.json", USED_TRIALS_FILE)]:
            exists = os.path.exists(fpath)
            writable = os.access(os.path.dirname(fpath) or '.', os.W_OK)
            files_status.append(f"  • {fname}: exists={exists}, dir_writable={writable}")
        
        status_text = (
            f"📊 *Bot Status*\n\n"
            f"*Active Trials:* {len(active_trials)}\n"
            f"*Used Trials:* {used_trials_count}\n"
            f"*Scheduled Jobs:* {job_count}\n"
            f"*Trial Channel ID:* `{TRIAL_CHANNEL_ID}`\n\n"
            f"*File Status:*\n" + "\n".join(files_status) + "\n\n"
        )
        
        if active_trials:
            status_text += "*Active Trial Users:*\n"
            for tg_id, info in list(active_trials.items())[:5]:  # Show max 5
                end_at = info.get("trial_end_at", "unknown")
                hours = info.get("total_hours", "?")
                status_text += f"  • User {tg_id}: {hours}h trial, ends {end_at[:19] if len(end_at) > 19 else end_at}\n"
            if len(active_trials) > 5:
                status_text += f"  ... and {len(active_trials) - 5} more\n"
        
        if job_list:
            status_text += "\n*Scheduled Jobs:*\n" + "\n".join(job_list)
            if job_count > 10:
                status_text += f"\n  ... and {job_count - 10} more"
        
        await update.message.reply_text(status_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in status_command: {e}", exc_info=True)
        await update.message.reply_text(f"Error getting status: {e}")


async def test_leave_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command to simulate a user leaving (for testing).
    Usage: /test_leave <user_id>
    """
    user = update.effective_user
    if not user or not update.message:
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /test_leave <user_id>")
        return
    
    try:
        test_user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Must be a number.")
        return
    
    # Get active trial info
    active = get_active_trial(test_user_id)
    if not active:
        await update.message.reply_text(f"No active trial found for user {test_user_id}")
        return
    
    # Simulate the leave process
    try:
        leave_info = {
            "left_early_at": _now_utc().isoformat(),
            "reason": "test_leave_command",
            "join_time": active.get("join_time"),
            "total_hours": active.get("total_hours")
        }
        
        mark_trial_used(test_user_id, leave_info)
        clear_active_trial(test_user_id)
        
        await update.message.reply_text(
            f"✅ Simulated leave for user {test_user_id}\n"
            f"• Trial marked as used\n"
            f"• Active trial cleared"
        )
        
        # Try to send message to user
        try:
            await context.bot.send_message(
                chat_id=test_user_id,
                text="🧪 Test: Your trial has been marked as ended (admin test command)."
            )
            await update.message.reply_text("✅ Message sent to user")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Could not message user: {e}")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


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
    
    # If not in phone verification stage, ignore (let other handlers process)
    # This allows normal /commands to work


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    contact = update.message.contact
    user = update.effective_user

    if not contact:
        return

    logger.info(f"Contact handler triggered for user {user.id} ({user.username})")

    # CRITICAL: Check if user has already used their trial FIRST
    # This prevents the exploit where users click "Share phone number" button repeatedly
    if has_used_trial(user.id):
        logger.warning(f"User {user.id} tried to share phone but already used trial")
        await update.message.reply_text(
            "You have already used your free 3-day trial once.\n\n"
            "🎁 For more chances, you can join our giveaway channel:\n"
            f"{GIVEAWAY_CHANNEL_URL}\n\n"
            f"💬 Or DM {SUPPORT_CONTACT} to upgrade to the premium signals.",
            reply_markup=ReplyKeyboardRemove(),  # Remove the keyboard
        )
        return

    # Check if user has an ACTIVE trial (already in channel)
    active_trial = get_active_trial(user.id)
    if active_trial and "join_time" in active_trial and "total_hours" in active_trial:
        try:
            join_time = _parse_iso_to_utc(active_trial["join_time"])
            total_hours = float(active_trial["total_hours"])
            trial_end_at = join_time + timedelta(hours=total_hours)
            now = _now_utc()
            
            if now < trial_end_at:
                elapsed_hours = (now - join_time).total_seconds() / 3600.0
                remaining_hours = total_hours - elapsed_hours
                elapsed_rounded = round(elapsed_hours, 1)
                remaining_rounded = round(remaining_hours, 1)
                total_days = int(total_hours / 24)
                
                logger.warning(f"User {user.id} tried to share phone but already has active trial")
                await update.message.reply_text(
                    f"✅ You are already in your {total_days}-day free trial!\n\n"
                    f"⏱ Time elapsed: {elapsed_rounded} hours\n"
                    f"⏳ Time remaining: {remaining_rounded} hours\n\n"
                    "No need to verify again - you're already in the trial channel!",
                    reply_markup=ReplyKeyboardRemove(),  # Remove the keyboard
                )
                return
        except Exception as e:
            logger.warning(f"Error checking active trial in contact_handler for user {user.id}: {e}")

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
            "You can request deletion at any time.",
            reply_markup=ReplyKeyboardRemove(),  # Remove the keyboard
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
        logger.info(f"User {user.id} already has valid invite link, returning existing")
        await update.message.reply_text(
            "You already generated a trial invite link recently.\n\n"
            "Please use this link to join the trial channel:\n"
            f"{existing_link}\n\n"
            "If you have any issues accessing it, use /support to contact us.",
            reply_markup=ReplyKeyboardRemove(),  # Remove the keyboard
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
        logger.info(f"Created invite link for user {user.id}: {invite_link.invite_link}")
    except Exception as e:  # pragma: no cover - defensive
        logger.error(f"Failed to create invite link for user {user.id}: {e}")
        await update.message.reply_text(
            "Failed to create an invite link. Please try again later.",
            reply_markup=ReplyKeyboardRemove(),  # Remove the keyboard
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
        f"{invite_link.invite_link}",
        reply_markup=ReplyKeyboardRemove(),  # Remove the keyboard to prevent re-use
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
    logger.info("=== trial_chat_member_update TRIGGERED ===")
    
    if not update.chat_member:
        logger.warning("trial_chat_member_update called but update.chat_member is None")
        return
        
    chat_member = update.chat_member
    chat = chat_member.chat

    logger.info(f"Chat member update: chat_id={chat.id}, chat_title={chat.title}, TRIAL_CHANNEL_ID={TRIAL_CHANNEL_ID}")
    
    if chat.id != TRIAL_CHANNEL_ID:
        logger.info(f"Ignoring chat member update for chat_id={chat.id} (not trial channel {TRIAL_CHANNEL_ID})")
        return

    old = chat_member.old_chat_member
    new = chat_member.new_chat_member
    
    logger.info(f"Member status change: user={new.user.id if new.user else 'None'}, old_status={old.status}, new_status={new.status}")

    # Detect join: previously left/kicked, now member/admin
    if old.status in ("left", "kicked") and new.status in ("member", "administrator"):
        user = new.user
        if not user:
            logger.warning("new.user is None in trial_chat_member_update (join)")
            return
        
        logger.info(f"=== USER JOIN DETECTED ===")
        logger.info(f"User {user.id} ({user.username}) joined trial channel")
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
        logger.info(f"Scheduling reminder jobs for user {user.id} ({trial_days}-day trial)")

        if trial_days == 3:
            # Use configurable reminder times (in minutes)
            jq.run_once(
                trial_reminder_3day_1,
                when=timedelta(minutes=REMINDER_1_MINUTES),
                data={"user_id": user.id},
                name=f"reminder_1_{user.id}",
            )
            jq.run_once(
                trial_reminder_3day_2,
                when=timedelta(minutes=REMINDER_2_MINUTES),
                data={"user_id": user.id},
                name=f"reminder_2_{user.id}",
            )
            jq.run_once(
                trial_end,
                when=timedelta(minutes=TRIAL_END_3DAY_MINUTES),
                data={"user_id": user.id},
                name=f"trial_end_{user.id}",
            )
            logger.info(f"Scheduled 3-day trial jobs for user {user.id}: reminder_1 at {REMINDER_1_MINUTES}min, reminder_2 at {REMINDER_2_MINUTES}min, end at {TRIAL_END_3DAY_MINUTES}min")
        else:
            # Use configurable reminder times (in minutes)
            jq.run_once(
                trial_reminder_5day_1,
                when=timedelta(minutes=REMINDER_1_MINUTES),
                data={"user_id": user.id},
                name=f"reminder_1_{user.id}",
            )
            jq.run_once(
                trial_reminder_5day_3,
                when=timedelta(minutes=REMINDER_3_MINUTES),
                data={"user_id": user.id},
                name=f"reminder_3_{user.id}",
            )
            jq.run_once(
                trial_reminder_5day_4,
                when=timedelta(minutes=REMINDER_4_MINUTES),
                data={"user_id": user.id},
                name=f"reminder_4_{user.id}",
            )
            jq.run_once(
                trial_end,
                when=timedelta(minutes=TRIAL_END_5DAY_MINUTES),
                data={"user_id": user.id},
                name=f"trial_end_{user.id}",
            )
            logger.info(f"Scheduled 5-day trial jobs for user {user.id}: reminder_1 at {REMINDER_1_MINUTES}min, reminder_3 at {REMINDER_3_MINUTES}min, reminder_4 at {REMINDER_4_MINUTES}min, end at {TRIAL_END_5DAY_MINUTES}min")

    # Detect user leaving during trial phase and send feedback form
    if old.status in ("member", "administrator") and new.status in ("left", "kicked"):
        logger.info(f"=== USER LEAVE DETECTED ===")
        logger.info(f"User left/kicked: old_status={old.status}, new_status={new.status}")
        
        user = old.user
        if not user:
            logger.warning("old.user is None in trial_chat_member_update (leave)")
            return
        
        logger.info(f"Leave event for user_id={user.id}, username={user.username}")
        logger.info(f"chat_member.from_user: {chat_member.from_user.id if chat_member.from_user else 'None'}")
        
        # Ignore leaves caused by the bot itself (e.g. scheduled trial_end ban/unban)
        try:
            bot_user = await context.bot.get_me()
            logger.debug(f"Bot user id: {bot_user.id if bot_user else 'None'}")
        except Exception as e:
            logger.warning(f"Failed to get bot user info: {e}")
            bot_user = None

        # If the actor is the bot, don't send feedback (this is likely trial_end cleanup)
        if bot_user and chat_member.from_user and chat_member.from_user.id == bot_user.id:
            logger.info("Leave was caused by bot itself (trial_end cleanup), skipping feedback message")
            return
        
        logger.info(f"Processing voluntary leave for user_id={user.id}")

        # Get trial data BEFORE clearing it
        active = get_active_trial(user.id)
        logger.info(f"Active trial data for user {user.id}: {active}")
        
        # Try to compute how many trial hours they used and how many were remaining
        usage_info = ""
        total_days = 3  # Default
        total_hours_used = 0
        try:
            if active and "join_time" in active and "total_hours" in active:
                join_time = _parse_iso_to_utc(active["join_time"])
                total_hours = float(active["total_hours"])
                total_days = int(total_hours / 24)
                now = _now_utc()
                elapsed_hours = (now - join_time).total_seconds() / 3600.0
                remaining_hours = max(0.0, total_hours - elapsed_hours)
                total_hours_used = total_hours

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
                usage_info = "\n\nYour trial data was not found - it may have already expired."
        except Exception as e:
            logger.error(f"Failed to compute remaining trial hours for user_id={user.id}: {e}", exc_info=True)
            usage_info = ""

        # FIRST: Mark trial as used BEFORE clearing active trial (important order!)
        try:
            leave_info = {
                "left_early_at": _now_utc().isoformat(),
                "reason": "user_left_channel"
            }
            if active:
                leave_info["join_time"] = active.get("join_time")
                leave_info["total_hours"] = active.get("total_hours")
            
            mark_trial_used(user.id, leave_info)
            logger.info(f"✅ Successfully marked trial as used for user {user.id} (left early)")
        except Exception as e:
            logger.error(f"❌ FAILED to mark trial used on early leave for user_id={user.id}: {e}", exc_info=True)

        # SECOND: Clear active trial tracking since they left
        try:
            clear_active_trial(user.id)
            logger.info(f"Cleared active trial for user {user.id}")
        except Exception as e:
            logger.warning(f"Failed to clear active trial for user_id={user.id}: {e}", exc_info=True)

        # THIRD: Send message to user about leaving
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
            logger.info(f"✅ Successfully sent leave message to user {user.id}")
        except Exception as e:
            logger.error(f"❌ Failed to send leave message to user_id={user.id}: {e}", exc_info=True)
        
        logger.info(f"=== LEAVE PROCESSING COMPLETE for user {user.id} ===")


async def _send_trial_reminder(context: ContextTypes.DEFAULT_TYPE, user_id: int, message: str, reminder_name: str = "reminder") -> bool:
    """
    Helper function to send trial reminders with proper error handling.
    Returns True if message was sent successfully, False otherwise.
    """
    logger.info(f"=== {reminder_name} triggered for user {user_id} ===")
    
    # Check if user still has an active trial before sending reminder
    active_trial = get_active_trial(user_id)
    if not active_trial:
        logger.info(f"Skipping {reminder_name} for user {user_id} - no active trial (user may have left early)")
        return False
    
    logger.debug(f"Active trial data for {reminder_name}: {active_trial}")
    
    # Verify trial hasn't expired yet
    try:
        trial_end_str = active_trial.get("trial_end_at")
        if trial_end_str:
            trial_end_at = _parse_iso_to_utc(trial_end_str)
            now = _now_utc()
            if now >= trial_end_at:
                logger.info(f"Skipping {reminder_name} for user {user_id} - trial already expired at {trial_end_at}")
                return False
            else:
                remaining = (trial_end_at - now).total_seconds() / 3600
                logger.debug(f"Trial still active, {round(remaining, 1)} hours remaining")
    except Exception as e:
        logger.warning(f"Error checking trial expiry for user {user_id}: {e}")
    
    try:
        await context.bot.send_message(chat_id=user_id, text=message)
        logger.info(f"✅ Successfully sent {reminder_name} to user {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send {reminder_name} to user {user_id}: {e}", exc_info=True)
        return False


async def trial_reminder_3day_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    logger.info(f"trial_reminder_3day_1 job executing for user {user_id}")
    await _send_trial_reminder(
        context, user_id,
        "Hey, it's Freya 💋\n\n"
        "You've been inside my 3-Day Trial for about a day now – I hope you've already seen how I structure my trades and risk.\n\n"
        "In this group you'll usually see:\n\n"
        "• 🔔 2–6 signals per day\n"
        "• 🎯 Clear entry, take-profit levels & stop-loss\n"
        "• 📊 Screenshots + short explanation so you can learn, not just copy\n\n"
        "If you missed anything, scroll up in the trial chat and check today's setups – everything is transparent, including wins and SL.\n\n"
        f"If you have any questions, you can always DM me here: {SUPPORT_CONTACT}\n\n"
        "Stay tuned, more setups are coming. 💸",
        reminder_name="24h_reminder_3day"
    )


async def trial_reminder_3day_2(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    logger.info(f"trial_reminder_3day_2 job executing for user {user_id}")
    await _send_trial_reminder(
        context, user_id,
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
        reminder_name="48h_reminder_3day"
    )


async def trial_reminder_5day_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    logger.info(f"trial_reminder_5day_1 job executing for user {user_id}")
    await _send_trial_reminder(
        context, user_id,
        "⏱ 1 day (24 hours) has passed, 4 days remaining in your 5-day trial.\n\n"
        f"💬 Enjoying the signals? Upgrade anytime by contacting {SUPPORT_CONTACT}",
        reminder_name="24h_reminder_5day"
    )


async def trial_reminder_5day_3(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    logger.info(f"trial_reminder_5day_3 job executing for user {user_id}")
    await _send_trial_reminder(
        context, user_id,
        "⏱ 3 days (72 hours) have passed, 2 days remaining in your 5-day trial.\n\n"
        f"💬 Questions about upgrading? Contact {SUPPORT_CONTACT}",
        reminder_name="72h_reminder_5day"
    )


async def trial_reminder_5day_4(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    logger.info(f"trial_reminder_5day_4 job executing for user {user_id}")
    await _send_trial_reminder(
        context, user_id,
        "⏱ 4 days (96 hours) have passed. Only the last 24 hours left in your trial!\n\n"
        f"⚡ Don't miss out! Contact {SUPPORT_CONTACT} to upgrade and keep receiving signals.",
        reminder_name="96h_reminder_5day"
    )


async def trial_end(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    logger.info(f"=== trial_end job executing for user {user_id} ===")
    
    # Check if user still has active trial (they might have left early)
    active_trial = get_active_trial(user_id)
    if not active_trial:
        logger.info(f"No active trial for user {user_id} - they may have left early, skipping trial_end")
        return
    
    # Check if already marked as used (avoid duplicate marking)
    if has_used_trial(user_id):
        logger.info(f"User {user_id} already marked as used trial, clearing active trial only")
        clear_active_trial(user_id)
        return
    
    # Mark this user as having used their free trial first (JSON is the source of truth)
    try:
        mark_trial_used(
            user_id,
            {
                "trial_ended_at": _now_utc().isoformat(),
                "ended_by": "scheduled_job"
            },
        )
        logger.info(f"✅ Marked trial as used for user {user_id}")
    except Exception as e:
        logger.error(f"❌ Failed to mark trial as used for user_id={user_id}: {e}", exc_info=True)

    # Clear active trial tracking on natural trial end
    try:
        clear_active_trial(user_id)
        logger.info(f"Cleared active trial for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to clear active trial for user {user_id}: {e}")

    # Notify user and remove them from trial channel
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
        logger.info(f"✅ Sent trial end message to user {user_id}")
    except Exception as e:
        logger.warning(f"Could not send trial end message to user {user_id}: {e}")

    # Remove from trial channel
    try:
        await context.bot.ban_chat_member(TRIAL_CHANNEL_ID, user_id)
        await context.bot.unban_chat_member(TRIAL_CHANNEL_ID, user_id)
        logger.info(f"Removed user {user_id} from trial channel")
    except Exception as e:
        logger.warning(f"Could not remove user {user_id} from trial channel: {e}")
    
    logger.info(f"=== trial_end complete for user {user_id} ===")


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
        
        logger.info(f"=== RESTORING JOBS ON STARTUP ===")
        logger.info(f"Found {len(active_trials)} active trials to restore")

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
                
                # Restore reminder jobs based on trial type (use configurable minutes)
                if is_5day:
                    # 5-day trial: reminders at configured times
                    reminder_times_minutes = [
                        (REMINDER_1_MINUTES, trial_reminder_5day_1),
                        (REMINDER_3_MINUTES, trial_reminder_5day_3),
                        (REMINDER_4_MINUTES, trial_reminder_5day_4),
                        (TRIAL_END_5DAY_MINUTES, trial_end),
                    ]
                else:
                    # 3-day trial: reminders at configured times
                    reminder_times_minutes = [
                        (REMINDER_1_MINUTES, trial_reminder_3day_1),
                        (REMINDER_2_MINUTES, trial_reminder_3day_2),
                        (TRIAL_END_3DAY_MINUTES, trial_end),
                    ]
                
                # Schedule each reminder job if it hasn't passed yet
                restored_jobs = 0
                for minutes_offset, job_func in reminder_times_minutes:
                    reminder_time = join_dt + timedelta(minutes=minutes_offset)
                    delay = reminder_time - now
                    
                    # Only schedule if the reminder time hasn't passed yet
                    if delay.total_seconds() > 0:
                        jq.run_once(
                            job_func,
                            when=delay,
                            data={"user_id": user_id},
                            name=f"{job_func.__name__}_{user_id}",
                        )
                        logger.info(f"Restored {job_func.__name__} for user {user_id}, scheduled in {delay}")
                        restored_jobs += 1
                    else:
                        logger.debug(f"Skipped {job_func.__name__} for user {user_id} (already passed)")
                
                if restored_jobs > 0:
                    logger.info(f"Restored {restored_jobs} jobs for user {user_id}")
                
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
                
        logger.info(f"=== JOB RESTORATION COMPLETE ===")
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
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("test_leave", test_leave_command))

    application.add_handler(
        CallbackQueryHandler(start_trial_callback, pattern="^start_trial$")
    )
    application.add_handler(
        CallbackQueryHandler(
            continue_verification_callback, pattern="^continue_verification$"
        )
    )

    # Handle "No thanks" / deny button for phone verification
    application.add_handler(
        MessageHandler(filters.Regex(r"^❌ No thanks$"), phone_deny_handler)
    )

    # Handle text messages during phone verification (tell user to click button instead)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_during_phone_verification_handler)
    )

    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))

    application.add_handler(
        ChatMemberHandler(trial_chat_member_update, ChatMemberHandler.CHAT_MEMBER)
    )

    # CRITICAL: Must explicitly request chat_member updates!
    # By default, Telegram doesn't send chat_member updates unless requested.
    # See: https://core.telegram.org/bots/api#getupdates
    allowed_updates = [
        "message",
        "edited_message",
        "callback_query",
        "chat_member",  # Required for join/leave detection
    ]
    logger.info(f"Starting polling with allowed_updates: {allowed_updates}")
    
    # Handles event loop setup/teardown internally.
    application.run_polling(allowed_updates=allowed_updates)


if __name__ == "__main__":
    try:
        logger.info("Starting Telegram bot...")
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


