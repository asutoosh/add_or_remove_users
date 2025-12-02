import json
import os
import threading
from typing import Any, Dict, Optional, List


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PENDING_FILE = os.path.join(BASE_DIR, "pending_verifications.json")
TRIAL_LOG_FILE = os.path.join(BASE_DIR, "trial_users.json")

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
    os.replace(tmp_path, path)


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


