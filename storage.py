import json
import os
import stat
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PENDING_FILE = os.path.join(BASE_DIR, "pending_verifications.json")
TRIAL_LOG_FILE = os.path.join(BASE_DIR, "trial_users.json")
USED_TRIALS_FILE = os.path.join(BASE_DIR, "used_trials.json")
ACTIVE_TRIALS_FILE = os.path.join(BASE_DIR, "active_trials.json")
INVITES_FILE = os.path.join(BASE_DIR, "invites.json")

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
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    # Set restrictive permissions (owner read/write only) for security
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp_path, path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def get_pending_verification(tg_id: int) -> Optional[Dict[str, Any]]:
    with _lock:
        print(f"🔍 get_pending_verification: Looking for tg_id={tg_id}")
        print(f"   File path: {PENDING_FILE}")
        print(f"   File exists: {os.path.exists(PENDING_FILE)}")
        data = _load_json(PENDING_FILE, {})
        print(f"   All keys in data: {list(data.keys())}")
        result = data.get(str(tg_id))
        if result:
            print(f"   ✅ Found data for tg_id={tg_id}")
        else:
            print(f"   ❌ No data found for tg_id={tg_id}")
        return result


def set_pending_verification(tg_id: int, info: Dict[str, Any]) -> None:
    with _lock:
        data = _load_json(PENDING_FILE, {})
        data[str(tg_id)] = info
        _save_json(PENDING_FILE, data)
        # Debug logging
        print(f"💾 Saved verification data for tg_id={tg_id} to {PENDING_FILE}")
        print(f"   File exists after save: {os.path.exists(PENDING_FILE)}")
        # Verify it was saved
        verify_data = _load_json(PENDING_FILE, {})
        if str(tg_id) in verify_data:
            print(f"   ✅ Verified: Data is in file")
        else:
            print(f"   ❌ ERROR: Data NOT found in file after save!")


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
        return str(tg_id) in data


def mark_trial_used(tg_id: int, info: Dict[str, Any]) -> None:
    """
    Mark a user as having used their free trial.
    This can be called when the trial period ends.
    """
    with _lock:
        data = _load_json(USED_TRIALS_FILE, {})
        data[str(tg_id)] = info
        _save_json(USED_TRIALS_FILE, data)


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
    """
    with _lock:
        data = _load_json(ACTIVE_TRIALS_FILE, {})
        data[str(tg_id)] = info
        _save_json(ACTIVE_TRIALS_FILE, data)


def clear_active_trial(tg_id: int) -> None:
    """
    Clear active trial info for a user.
    Called when the trial ends or the user leaves.
    """
    with _lock:
        data = _load_json(ACTIVE_TRIALS_FILE, {})
        data.pop(str(tg_id), None)
        _save_json(ACTIVE_TRIALS_FILE, data)


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
    """
    with _lock:
        data = _load_json(INVITES_FILE, {})
        data[str(tg_id)] = info
        _save_json(INVITES_FILE, data)


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
                ts = datetime.fromisoformat(ts_str)
                if (now - ts).total_seconds() < window_minutes * 60:
                    valid_attempts.append(ts_str)
            except Exception:
                pass
        
        if len(valid_attempts) >= max_attempts:
            return False  # Rate limited
        
        # Add current attempt
        valid_attempts.append(now.isoformat())
        user_data[rate_key] = valid_attempts
        data[str(tg_id)] = user_data
        _save_json(PENDING_FILE, data)
        
        return True  # Allowed


