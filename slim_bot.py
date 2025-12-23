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
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
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

TRIAL_COOLDOWN_DAYS = 30

# Links
GIVEAWAY_CHANNEL_URL = os.environ.get("GIVEAWAY_CHANNEL_URL", "https://t.me/Freya_Trades")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@cogitosk")
FEEDBACK_FORM_URL = os.environ.get("FEEDBACK_FORM_URL", "https://forms.gle/K7ubyn2tvzuYeHXn9")
SUPPORT_FORM_URL = os.environ.get("SUPPORT_FORM_URL", "https://forms.gle/CJbNczZ6BcKjk6Bz9")
BASE_URL = os.environ.get("BASE_URL", "https://freyatrades.live")

# Validate required vars
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if TRIAL_CHANNEL_ID == 0:
    logger.warning("TRIAL_CHANNEL_ID not set!")

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


# =============================================================================
# Command Handlers (Fallback for non-Mini App clients)
# =============================================================================

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command - show Mini App button."""
    user = update.effective_user
    if not user:
        return
    
    keyboard = [[
        InlineKeyboardButton(
            text="🚀 Start Free Trial",
            web_app=WebAppInfo(url=BASE_URL)
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Hey {user.first_name}! 👋\n\n"
        "Welcome to **Freya Quinn's Flirty Profits**! 💋\n\n"
        "Get a FREE 3-Day Trial of my VIP signals.\n\n"
        "Tap the button below to start:\n",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    keyboard = [[
        InlineKeyboardButton(
            text="🚀 Open Mini App",
            web_app=WebAppInfo(url=BASE_URL)
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📚 **Commands & Help**\n\n"
        "/start - Start your free trial\n"
        "/help - This help message\n"
        "/faq - Frequently asked questions\n"
        "/about - About Freya Quinn\n"
        "/support - Contact support\n\n"
        f"💬 Direct support: {SUPPORT_CONTACT}",
        reply_markup=reply_markup,
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
        f"Hey, it's Freya 💋\n\n"
        f"You've been in your 3-Day Trial for about a day now.\n\n"
        f"In this group you'll see:\n"
        f"• 🔔 2–6 signals per day\n"
        f"• 🎯 Clear entry, TP \u0026 SL\n"
        f"• 📊 Screenshots + explanations\n\n"
        f"Questions? DM {SUPPORT_CONTACT}",
        "24h_reminder_3day"
    )


async def trial_reminder_3day_2(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_reminder(context, user_id,
        f"Day 2 check-in 🧡\n\n"
        f"Almost two days into the trial now.\n\n"
        f"If you like the signals, my members stay on a 30-Day Premium plan.\n\n"
        f"Ready to upgrade? DM {SUPPORT_CONTACT} 'PREMIUM'",
        "48h_reminder_3day"
    )


async def trial_reminder_5day_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_reminder(context, user_id,
        f"⏱ 1 day passed, 4 days remaining in your 5-day trial.\n\n"
        f"💬 Upgrade anytime: {SUPPORT_CONTACT}",
        "24h_reminder_5day"
    )


async def trial_reminder_5day_3(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_reminder(context, user_id,
        f"⏱ 3 days passed, 2 days remaining in your 5-day trial.\n\n"
        f"💬 Questions? {SUPPORT_CONTACT}",
        "72h_reminder_5day"
    )


async def trial_reminder_5day_4(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.data["user_id"]
    await _send_reminder(context, user_id,
        f"⏱ 4 days passed. Only 24 hours left!\n\n"
        f"⚡ Upgrade now: {SUPPORT_CONTACT}",
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
            text=f"Your trial just ended 🕊\n\n"
                 f"Thank you for testing Freya's Flirty Profits.\n\n"
                 f"✅ 30-Day Premium: Full access + DM support\n\n"
                 f"DM {SUPPORT_CONTACT} to upgrade\n\n"
                 f"Free content: {GIVEAWAY_CHANNEL_URL}",
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
    
    # Add chat member handler
    application.add_handler(
        ChatMemberHandler(trial_chat_member_update, ChatMemberHandler.CHAT_MEMBER)
    )
    
    # Start polling - include 'message' for commands and 'chat_member' for join/leave
    allowed_updates = ["message", "chat_member"]
    logger.info("Starting slim bot with command handlers...")
    application.run_polling(allowed_updates=allowed_updates)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
