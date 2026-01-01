import json
import logging
import os
import stat
import threading
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

# Configure logging
logger = logging.getLogger(__name__)


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


def _get_signing_key() -> bytes:
    """
    Get secret key for HMAC signing.
    Uses BOT_TOKEN from environment (kept secret).
    """
    bot_token = os.environ.get("BOT_TOKEN", "")
    if not bot_token:
        logger.warning("BOT_TOKEN not set - trial data signing disabled")
        return b"insecure_fallback_key"  # Fallback (should never happen in production)
    return bot_token.encode()


def _sign_trial_data(data: Dict[str, Any], user_id: int) -> str:
    """
    Generate HMAC-SHA256 signature for trial data to prevent tampering.
    
    Signs: user_id + join_time + total_hours + trial_end_at
    Returns: hex-encoded signature
    """
    # Create canonical message
    join_time = data.get("join_time", "")
    total_hours = str(data.get("total_hours", ""))
    trial_end_at = data.get("trial_end_at", "")
    
    message = f"{user_id}|{join_time}|{total_hours}|{trial_end_at}".encode()
    
    # Generate HMAC
    secret = _get_signing_key()
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    
    logger.debug(f"Generated signature for user {user_id}")
    return signature


def _verify_trial_signature(data: Dict[str, Any], user_id: int) -> bool:
    """
    Verify HMAC signature of trial data.
    Returns True if valid, False if tampered.
    """
    if "signature" not in data:
        logger.warning(f"No signature in trial data for user {user_id}")
        return False
    
    stored_sig = data["signature"]
    expected_sig = _sign_trial_data(data, user_id)
    
    # Constant-time comparison
    is_valid = hmac.compare_digest(stored_sig, expected_sig)
    
    if not is_valid:
        logger.error(f"❌ SECURITY ALERT: Invalid signature for user {user_id} - data may be tampered!")
    
    return is_valid


def _sign_invite_data(data: Dict[str, Any], user_id: int) -> str:
    """
    Generate HMAC-SHA256 signature for invite data to prevent tampering.
    
    Signs: user_id + invite_link + created_at + expires_at
    Returns: hex-encoded signature
    """
    invite_link = data.get("invite_link", "")
    created_at = data.get("invite_created_at", "")
    expires_at = data.get("invite_expires_at", "")
    
    message = f"{user_id}|{invite_link}|{created_at}|{expires_at}".encode()
    
    # Generate HMAC
    secret = _get_signing_key()
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    
    logger.debug(f"Generated invite signature for user {user_id}")
    return signature


def _verify_invite_signature(data: Dict[str, Any], user_id: int) -> bool:
    """
    Verify HMAC signature of invite data.
    Returns True if valid, False if tampered.
    """
    if "signature" not in data:
        logger.warning(f"No signature in invite data for user {user_id}")
        return False
    
    stored_sig = data["signature"]
    expected_sig = _sign_invite_data(data, user_id)
    
    # Constant-time comparison
    is_valid = hmac.compare_digest(stored_sig, expected_sig)
    
    if not is_valid:
        logger.error(f"❌ SECURITY ALERT: Invalid invite signature for user {user_id} - data may be tampered!")
    
    return is_valid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PENDING_FILE = os.path.join(BASE_DIR, "pending_verifications.json")
TRIAL_LOG_FILE = os.path.join(BASE_DIR, "trial_users.json")
USED_TRIALS_FILE = os.path.join(BASE_DIR, "used_trials.json")
ACTIVE_TRIALS_FILE = os.path.join(BASE_DIR, "active_trials.json")
INVITES_FILE = os.path.join(BASE_DIR, "invites.json")
START_USERS_CLICKS_FILE = os.path.join(BASE_DIR, "startusersclicks.json")

_lock = threading.Lock()


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # On any error, fall back to default to avoid crashing the app
        return default


def _save_json(path: str, data: Any) -> None:
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # Set restrictive permissions (owner read/write only) for security
        # Only on Unix-like systems - skip on Windows where chmod behaves differently
        if os.name != 'nt':  # 'nt' is Windows
            try:
                os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
            except Exception as e:
                logger.warning(f"Could not set permissions on {tmp_path}: {e}")
        
        os.replace(tmp_path, path)
        
        if os.name != 'nt':
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except Exception as e:
                logger.warning(f"Could not set permissions on {path}: {e}")
        
        logger.debug(f"Successfully saved JSON to {path}")
    except Exception as e:
        logger.error(f"Failed to save JSON to {path}: {e}")
        # Clean up temp file if it exists
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise  # Re-raise to let caller know save failed


def get_pending_verification(tg_id: int) -> Optional[Dict[str, Any]]:
    with _lock:
        logger.debug(f"get_pending_verification: Looking for tg_id={tg_id}")
        logger.debug(f"File path: {PENDING_FILE}, exists: {os.path.exists(PENDING_FILE)}")
        data = _load_json(PENDING_FILE, {})
        logger.debug(f"All keys in data: {list(data.keys())}")
        result = data.get(str(tg_id))
        if result:
            logger.debug(f"Found data for tg_id={tg_id}")
        else:
            logger.debug(f"No data found for tg_id={tg_id}")
        return result


def set_pending_verification(tg_id: int, info: Dict[str, Any]) -> None:
    with _lock:
        data = _load_json(PENDING_FILE, {})
        data[str(tg_id)] = info
        _save_json(PENDING_FILE, data)
        # Debug logging
        logger.debug(f"Saved verification data for tg_id={tg_id} to {PENDING_FILE}")
        logger.debug(f"File exists after save: {os.path.exists(PENDING_FILE)}")
        # Verify it was saved
        verify_data = _load_json(PENDING_FILE, {})
        if str(tg_id) in verify_data:
            logger.debug("Verified: Data is in file")
        else:
            logger.error(f"Data NOT found in file after save for tg_id={tg_id}!")


def clear_pending_verification(tg_id: int) -> None:
    with _lock:
        data = _load_json(PENDING_FILE, {})
        data.pop(str(tg_id), None)
        _save_json(PENDING_FILE, data)


def append_trial_log(record: Dict[str, Any]) -> None:
    with _lock:
        records: List[Dict[str, Any]] = _load_json(TRIAL_LOG_FILE, [])
        records.append(record)
        _save_json(TRIAL_LOG_FILE, records)


def has_used_trial(tg_id: int) -> bool:
    """
    Check if a user has already consumed their free trial.
    Data is stored in USED_TRIALS_FILE as a mapping of tg_id -> record.
    """
    with _lock:
        data = _load_json(USED_TRIALS_FILE, {})
        result = str(tg_id) in data
        logger.debug(f"has_used_trial({tg_id}): {result}, file has {len(data)} entries")
        return result


def mark_trial_used(tg_id: int, info: Dict[str, Any]) -> None:
    """
    Mark a user as having used their free trial.
    This can be called when the trial period ends or user leaves early.
    """
    with _lock:
        logger.info(f"mark_trial_used: Marking user {tg_id} as used, info={info}")
        data = _load_json(USED_TRIALS_FILE, {})
        data[str(tg_id)] = info
        _save_json(USED_TRIALS_FILE, data)
        
        # Verify the save worked
        verify_data = _load_json(USED_TRIALS_FILE, {})
        if str(tg_id) in verify_data:
            logger.info(f"mark_trial_used: Successfully saved trial for user {tg_id}")
        else:
            logger.error(f"mark_trial_used: FAILED to save trial for user {tg_id} - data not found after save!")


def get_used_trial_info(tg_id: int) -> Optional[Dict[str, Any]]:
    """
    Get information about a user's used trial, if any.
    Returns the trial info dict if user has used a trial, None otherwise.
    """
    with _lock:
        data = _load_json(USED_TRIALS_FILE, {})
        return data.get(str(tg_id))


def atomic_check_and_reserve_trial(tg_id: int) -> bool:
    """
    SECURITY FIX #4: Atomically check if user can start trial AND mark as reserved.
    Returns True if allowed (and now reserved), False if already used or active.
    
    This prevents race condition where two simultaneous requests both pass
    has_used_trial() check and both create invites.
    """
    with _lock:
        # Check if already used
        used_data = _load_json(USED_TRIALS_FILE, {})
        if str(tg_id) in used_data:
            logger.info(f"atomic_check_and_reserve_trial: User {tg_id} already used trial")
            return False
        
        # Check if already has active trial
        active_data = _load_json(ACTIVE_TRIALS_FILE, {})
        if str(tg_id) in active_data:
            logger.info(f"atomic_check_and_reserve_trial: User {tg_id} already has active trial")
            return False
        
        # Reserve by marking as "pending trial start"
        # This prevents concurrent requests from passing
        used_data[str(tg_id)] = {
            "reserved_at": datetime.now(timezone.utc).isoformat(),
            "status": "reserved"
        }
        _save_json(USED_TRIALS_FILE, used_data)
        logger.info(f"atomic_check_and_reserve_trial: Reserved trial for user {tg_id}")
        return True


def get_all_active_trials() -> Dict[str, Any]:
    """
    Return all active trial records as a mapping of tg_id -> info.
    """
    with _lock:
        data = _load_json(ACTIVE_TRIALS_FILE, {})
        return data


def get_active_trial(tg_id: int) -> Optional[Dict[str, Any]]:
    """
    Return active trial data for a user if present.
    Stored as a mapping of tg_id -> {join_time, total_hours, ...}.
    """
    with _lock:
        data = _load_json(ACTIVE_TRIALS_FILE, {})
        return data.get(str(tg_id))


def set_active_trial(tg_id: int, info: Dict[str, Any]) -> None:
    """
    Store or update active trial info for a user.
    Called when the user joins the trial channel.
    Automatically adds HMAC signature to prevent tampering.
    """
    with _lock:
        # Add signature before saving
        info["signature"] = _sign_trial_data(info, tg_id)
        
        data = _load_json(ACTIVE_TRIALS_FILE, {})
        data[str(tg_id)] = info
        _save_json(ACTIVE_TRIALS_FILE, data)
        logger.info(f"set_active_trial: Set active trial for user {tg_id}, total_hours={info.get('total_hours')}, signed=True")


def clear_active_trial(tg_id: int) -> None:
    """
    Clear active trial info for a user.
    Called when the trial ends or the user leaves.
    """
    with _lock:
        data = _load_json(ACTIVE_TRIALS_FILE, {})
        if str(tg_id) in data:
            data.pop(str(tg_id), None)
            _save_json(ACTIVE_TRIALS_FILE, data)
            logger.info(f"clear_active_trial: Cleared active trial for user {tg_id}")
        else:
            logger.debug(f"clear_active_trial: No active trial found for user {tg_id} to clear")


def get_invite_info(tg_id: int) -> Optional[Dict[str, Any]]:
    """
    Get stored invite info for a user, if any.
    """
    with _lock:
        data = _load_json(INVITES_FILE, {})
        return data.get(str(tg_id))


def set_invite_info(tg_id: int, info: Dict[str, Any]) -> None:
    """
    Store or update invite info for a user.
    Automatically adds HMAC signature to prevent tampering.
    """
    with _lock:
        # Add signature before saving
        info["signature"] = _sign_invite_data(info, tg_id)
        
        data = _load_json(INVITES_FILE, {})
        data[str(tg_id)] = info
        _save_json(INVITES_FILE, data)
        logger.info(f"set_invite_info: Saved invite for user {tg_id}, signed=True")


def get_valid_invite_link(tg_id: int, now: datetime) -> Optional[str]:
    """
    Atomically check if user has a valid invite link that hasn't expired.
    Returns the invite link if valid, None otherwise.
    This function holds the lock to prevent race conditions.
    """
    with _lock:
        data = _load_json(INVITES_FILE, {})
        invite_info = data.get(str(tg_id))
        if not invite_info or "invite_expires_at" not in invite_info:
            return None
        
        # SECURITY FIX #2: Verify signature first
        if not _verify_invite_signature(invite_info, tg_id):
            logger.error(f"Invalid invite signature for user {tg_id}, rejecting")
            return None
        
        try:
            expires_at = _parse_iso_to_utc(invite_info["invite_expires_at"])
            # Ensure now is also timezone-aware UTC for comparison
            now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
            if now_utc < expires_at and invite_info.get("invite_link"):
                return invite_info["invite_link"]
        except Exception:
            # If parsing fails, treat as invalid
            return None
        
        return None


def atomic_create_or_get_invite(tg_id: int, link_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Atomically check for existing valid invite OR create new one.
    Returns {"action": "existing", "link": "..."} or {"action": "created", "link": None}
    This prevents race condition when multiple requests arrive simultaneously.
    """
    with _lock:
        now = datetime.now(timezone.utc)
        data = _load_json(INVITES_FILE, {})
        invite_info = data.get(str(tg_id))
        
        # Check if valid invite exists
        if invite_info and "invite_expires_at" in invite_info:
            try:
                expires_at = _parse_iso_to_utc(invite_info["invite_expires_at"])
                if now < expires_at and invite_info.get("invite_link"):
                    logger.info(f"atomic_create_or_get_invite: Found existing valid invite for user {tg_id}")
                    return {"action": "existing", "link": invite_info["invite_link"]}
            except Exception as e:
                logger.warning(f"atomic_create_or_get_invite: Error parsing existing invite: {e}")
        
        # No valid invite - mark as creating (store temporary placeholder)
        data[str(tg_id)] = {
            **link_data,
            "creating": True,  # Marker to prevent concurrent creation
            "created_at": now.isoformat()
        }
        _save_json(INVITES_FILE, data)
        logger.info(f"atomic_create_or_get_invite: Marked user {tg_id} as creating new invite")
        return {"action": "created", "link": None}


def finalize_invite_creation(tg_id: int, invite_link: str) -> None:
    """
    Finalize invite creation by removing 'creating' marker and adding actual link.
    Re-signs data with new invite_link.
    """
    with _lock:
        data = _load_json(INVITES_FILE, {})
        if str(tg_id) in data:
            data[str(tg_id)]["invite_link"] = invite_link
            data[str(tg_id)].pop("creating", None)  # Remove marker
            # Re-sign with the new invite_link
            data[str(tg_id)]["signature"] = _sign_invite_data(data[str(tg_id)], tg_id)
            _save_json(INVITES_FILE, data)
            logger.info(f"finalize_invite_creation: Finalized invite for user {tg_id}")


def cleanup_failed_invite_creation(tg_id: int) -> None:
    """
    Remove temporary placeholder if invite creation failed.
    """
    with _lock:
        data = _load_json(INVITES_FILE, {})
        if str(tg_id) in data and data[str(tg_id)].get("creating"):
            del data[str(tg_id)]
            _save_json(INVITES_FILE, data)
            logger.info(f"cleanup_failed_invite_creation: Cleaned up failed creation for user {tg_id}")


def check_rate_limit(tg_id: int, action: str, max_attempts: int = 5, window_minutes: int = 15) -> bool:
    """
    Check if user exceeded rate limit for a specific action.
    Returns True if allowed, False if rate limited.
    """
    with _lock:
        data = _load_json(PENDING_FILE, {})
        user_data = data.get(str(tg_id), {})
        
        rate_key = f"{action}_attempts"
        attempts = user_data.get(rate_key, [])
        now = datetime.now(timezone.utc)
        
        # Remove attempts older than window
        valid_attempts = []
        for ts_str in attempts:
            try:
                ts = _parse_iso_to_utc(ts_str)
                if (now - ts).total_seconds() < window_minutes * 60:
                    valid_attempts.append(ts_str)
            except Exception:
                pass
        
        if len(valid_attempts) >= max_attempts:
            return False  # Rate limited
        
        # Add current attempt (store as timezone-aware UTC)
        valid_attempts.append(now.isoformat())
        user_data[rate_key] = valid_attempts
        data[str(tg_id)] = user_data
        _save_json(PENDING_FILE, data)
        
        return True  # Allowed


def track_start_click(user_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Track when a user clicks the /start command.
    If user is new, stores all their info.
    If user exists, increments their click count and updates last_click_at.
    
    user_info should contain: tg_id, username, first_name, last_name, language_code, is_premium, is_bot
    
    Returns the user record (new or updated).
    """
    tg_id = str(user_info.get("tg_id", ""))
    if not tg_id:
        logger.warning("track_start_click: No tg_id provided")
        return {}
    
    with _lock:
        data = _load_json(START_USERS_CLICKS_FILE, {})
        now = datetime.now(timezone.utc).isoformat()
        
        if tg_id in data:
            # User exists - increment click count and update last_click_at
            data[tg_id]["click_count"] = data[tg_id].get("click_count", 1) + 1
            data[tg_id]["last_click_at"] = now
            # Update any changed user info (username, name, etc. can change)
            if user_info.get("username"):
                data[tg_id]["username"] = user_info.get("username")
            if user_info.get("first_name"):
                data[tg_id]["first_name"] = user_info.get("first_name")
            if user_info.get("last_name"):
                data[tg_id]["last_name"] = user_info.get("last_name")
            if user_info.get("language_code"):
                data[tg_id]["language_code"] = user_info.get("language_code")
            if user_info.get("is_premium") is not None:
                data[tg_id]["is_premium"] = user_info.get("is_premium")
            logger.info(f"track_start_click: User {tg_id} clicked /start again, count={data[tg_id]['click_count']}")
        else:
            # New user - store all info
            data[tg_id] = {
                "tg_id": int(tg_id),
                "username": user_info.get("username"),
                "first_name": user_info.get("first_name"),
                "last_name": user_info.get("last_name"),
                "language_code": user_info.get("language_code"),
                "is_premium": user_info.get("is_premium", False),
                "is_bot": user_info.get("is_bot", False),
                "first_click_at": now,
                "last_click_at": now,
                "click_count": 1,
            }
            logger.info(f"track_start_click: New user {tg_id} (@{user_info.get('username')}) clicked /start")
        
        _save_json(START_USERS_CLICKS_FILE, data)
        return data[tg_id]


def get_start_user_info(tg_id: int) -> Optional[Dict[str, Any]]:
    """
    Get stored info for a user who clicked /start.
    Returns None if user not found.
    """
    with _lock:
        data = _load_json(START_USERS_CLICKS_FILE, {})
        return data.get(str(tg_id))


def get_all_start_users() -> Dict[str, Any]:
    """
    Get all users who have clicked /start.
    Returns a dict mapping tg_id -> user info.
    """
    with _lock:
        return _load_json(START_USERS_CLICKS_FILE, {})


# =============================================================================
# SECURITY FIX #5: Cleanup expired data
# =============================================================================

PENDING_VERIFICATION_EXPIRY_HOURS = 24  # Expire after 24 hours

def cleanup_expired_pending_verifications() -> int:
    """
    Remove pending verifications older than EXPIRY hours.
    Returns count of entries removed.
    """
    with _lock:
        data = _load_json(PENDING_FILE, {})
        now = datetime.now(timezone.utc)
        
        expired_ids = []
        for tg_id_str, info in data.items():
            # Check various timestamp fields
            verified_at_str = info.get("verified_at") or info.get("ip_check_at")
            if not verified_at_str:
                continue
            
            try:
                verified_at = _parse_iso_to_utc(verified_at_str)
                hours_old = (now - verified_at).total_seconds() / 3600
                if hours_old > PENDING_VERIFICATION_EXPIRY_HOURS:
                    expired_ids.append(tg_id_str)
            except Exception:
                pass
        
        for tg_id_str in expired_ids:
            del data[tg_id_str]
        
        if expired_ids:
            _save_json(PENDING_FILE, data)
            logger.info(f"Cleaned up {len(expired_ids)} expired pending verifications")
        
        return len(expired_ids)


# =============================================================================
# Banned Users Management
# =============================================================================

BANNED_USERS_FILE = os.path.join(BASE_DIR, "banned_users.json")


def get_banned_users() -> Dict[str, Any]:
    """Get all banned users."""
    with _lock:
        return _load_json(BANNED_USERS_FILE, {})


def is_banned(tg_id: int) -> bool:
    """Check if a user is banned."""
    with _lock:
        data = _load_json(BANNED_USERS_FILE, {})
        return str(tg_id) in data


def add_banned_user(tg_id: int, reason: str, banned_by: int) -> None:
    """Add a user to the banned list."""
    with _lock:
        data = _load_json(BANNED_USERS_FILE, {})
        data[str(tg_id)] = {
            "banned_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "banned_by": banned_by,
        }
        _save_json(BANNED_USERS_FILE, data)
        logger.info(f"Banned user {tg_id}, reason: {reason}")


def remove_banned_user(tg_id: int) -> bool:
    """Remove a user from the banned list. Returns True if user was banned."""
    with _lock:
        data = _load_json(BANNED_USERS_FILE, {})
        if str(tg_id) in data:
            del data[str(tg_id)]
            _save_json(BANNED_USERS_FILE, data)
            logger.info(f"Unbanned user {tg_id}")
            return True
        return False


# =============================================================================
# Scheduled Broadcasts
# =============================================================================

SCHEDULED_BROADCASTS_FILE = os.path.join(BASE_DIR, "scheduled_broadcasts.json")


def get_scheduled_broadcasts() -> List[Dict[str, Any]]:
    """Get all scheduled broadcasts."""
    with _lock:
        return _load_json(SCHEDULED_BROADCASTS_FILE, [])


def add_scheduled_broadcast(broadcast_data: Dict[str, Any]) -> str:
    """
    Add a scheduled broadcast.
    Returns the broadcast ID.
    """
    import uuid
    with _lock:
        data = _load_json(SCHEDULED_BROADCASTS_FILE, [])
        broadcast_id = str(uuid.uuid4())[:8]
        broadcast_data["id"] = broadcast_id
        broadcast_data["created_at"] = datetime.now(timezone.utc).isoformat()
        data.append(broadcast_data)
        _save_json(SCHEDULED_BROADCASTS_FILE, data)
        logger.info(f"Added scheduled broadcast {broadcast_id}")
        return broadcast_id


def remove_scheduled_broadcast(broadcast_id: str) -> bool:
    """Remove a scheduled broadcast by ID."""
    with _lock:
        data = _load_json(SCHEDULED_BROADCASTS_FILE, [])
        new_data = [b for b in data if b.get("id") != broadcast_id]
        if len(new_data) < len(data):
            _save_json(SCHEDULED_BROADCASTS_FILE, new_data)
            logger.info(f"Removed scheduled broadcast {broadcast_id}")
            return True
        return False


def get_pending_broadcasts(now: datetime) -> List[Dict[str, Any]]:
    """Get broadcasts that are due to be sent."""
    with _lock:
        data = _load_json(SCHEDULED_BROADCASTS_FILE, [])
        pending = []
        for broadcast in data:
            scheduled_at_str = broadcast.get("scheduled_at")
            if not scheduled_at_str:
                continue
            try:
                scheduled_at = _parse_iso_to_utc(scheduled_at_str)
                if scheduled_at <= now and not broadcast.get("sent"):
                    pending.append(broadcast)
            except Exception:
                pass
        return pending


def mark_broadcast_sent(broadcast_id: str) -> None:
    """Mark a broadcast as sent."""
    with _lock:
        data = _load_json(SCHEDULED_BROADCASTS_FILE, [])
        for broadcast in data:
            if broadcast.get("id") == broadcast_id:
                broadcast["sent"] = True
                broadcast["sent_at"] = datetime.now(timezone.utc).isoformat()
                break
        _save_json(SCHEDULED_BROADCASTS_FILE, data)


# =============================================================================
# Statistics Helpers
# =============================================================================

def get_storage_stats() -> Dict[str, Any]:
    """Get statistics about stored data."""
    with _lock:
        start_users = _load_json(START_USERS_CLICKS_FILE, {})
        active_trials = _load_json(ACTIVE_TRIALS_FILE, {})
        used_trials = _load_json(USED_TRIALS_FILE, {})
        pending = _load_json(PENDING_FILE, {})
        banned = _load_json(BANNED_USERS_FILE, {})
        
        # Count verified users
        verified_count = sum(1 for v in pending.values() if v.get("status") == "verified" or v.get("step1_ok"))
        
        return {
            "total_start_clicks": len(start_users),
            "active_trials": len(active_trials),
            "used_trials": len(used_trials),
            "pending_verifications": len(pending),
            "verified_users": verified_count,
            "banned_users": len(banned),
        }


