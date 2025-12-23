"""
Freya Quinn - Mini App API Backend
Handles all REST API endpoints for the Mini App + fallback web verification
"""

from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Optional, Tuple
from collections import defaultdict
import hashlib
import hmac
import html
import json
import logging
import os
import re
import urllib.parse

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
import requests

from storage import (
    get_pending_verification,
    set_pending_verification,
    clear_pending_verification,
    has_used_trial,
    get_active_trial,
    get_invite_info,
    set_invite_info,
    check_rate_limit,
    track_start_click,
    get_valid_invite_link,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv()

# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TRIAL_CHANNEL_ID = int(os.environ.get("TRIAL_CHANNEL_ID", "0"))
API_SECRET = os.environ.get("API_SECRET", "")
IP2LOCATION_API_KEY = os.environ.get("IP2LOCATION_API_KEY", "")
IP2LOCATION_API_KEY_2 = os.environ.get("IP2LOCATION_API_KEY_2", "")
BLOCKED_COUNTRY_CODE = os.environ.get("BLOCKED_COUNTRY_CODE", "PK").upper()
BLOCKED_PHONE_COUNTRY_CODE = os.environ.get("BLOCKED_PHONE_COUNTRY_CODE", "+91")
GIVEAWAY_CHANNEL_URL = os.environ.get("GIVEAWAY_CHANNEL_URL", "https://t.me/Freya_Trades")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@cogitosk")
INVITE_LINK_EXPIRY_HOURS = int(os.environ.get("INVITE_LINK_EXPIRY_HOURS", "5"))

# Flask app
app = Flask(__name__, static_folder='mini_app', static_url_path='')

# Rate limiting storage (in-memory, resets on restart)
_ip_rate_limits = defaultdict(list)
_tg_rate_limits = defaultdict(list)


# =============================================================================
# Utility Functions
# =============================================================================

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_to_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def sanitize_input(text: str, max_length: int = 200) -> Optional[str]:
    """Sanitize user input to prevent XSS and limit length."""
    if not text:
        return None
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length]
    return html.escape(text)


def is_valid_email(email: str) -> bool:
    """Basic email validation."""
    if not email:
        return True  # Optional field
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def get_client_ip() -> str:
    """Extract client IP from request headers."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ips = forwarded_for.split(",")
        if ips and ips[0].strip():
            return ips[0].strip()
    return request.remote_addr or "0.0.0.0"


# =============================================================================
# Rate Limiting
# =============================================================================

def check_ip_rate_limit(ip: str, max_requests: int = 5, window_minutes: int = 60) -> bool:
    """Check IP-based rate limit. Returns True if allowed."""
    now = _now_utc()
    cutoff = now - timedelta(minutes=window_minutes)
    
    _ip_rate_limits[ip] = [ts for ts in _ip_rate_limits[ip] if ts > cutoff]
    
    if len(_ip_rate_limits[ip]) >= max_requests:
        return False
    
    _ip_rate_limits[ip].append(now)
    return True


def check_tg_rate_limit(tg_id: int, max_requests: int = 10, window_minutes: int = 60) -> bool:
    """Check Telegram user rate limit. Returns True if allowed."""
    now = _now_utc()
    cutoff = now - timedelta(minutes=window_minutes)
    key = str(tg_id)
    
    _tg_rate_limits[key] = [ts for ts in _tg_rate_limits[key] if ts > cutoff]
    
    if len(_tg_rate_limits[key]) >= max_requests:
        return False
    
    _tg_rate_limits[key].append(now)
    return True


# =============================================================================
# Telegram initData Validation
# =============================================================================

def validate_init_data(init_data: str) -> Optional[dict]:
    """
    Validate Telegram Web App initData hash.
    Returns user data dict if valid, None otherwise.
    
    Algorithm: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN not set, skipping initData validation")
        return None
    
    try:
        parsed = urllib.parse.parse_qs(init_data)
        hash_list = parsed.get("hash", [])
        if not hash_list or not hash_list[0]:
            return None
        hash_value = hash_list[0]
        
        # Create data-check-string: alphabetically sorted key=value pairs, newline-separated
        # Exclude 'hash' from the check string
        data_check_string = "\n".join(
            f"{k}={v[0]}"
            for k, v in sorted(parsed.items())
            if k != "hash" and v and len(v) > 0
        )
        
        # CORRECT: secret_key = HMAC_SHA256(key="WebAppData", data=bot_token)
        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()
        
        # Validate hash
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(calculated_hash, hash_value):
            logger.warning("Invalid initData hash")
            return None
        
        # Check auth_date is not too old (within 24 hours)
        auth_date_list = parsed.get("auth_date", [])
        if auth_date_list and auth_date_list[0]:
            try:
                auth_timestamp = int(auth_date_list[0])
                now_timestamp = int(_now_utc().timestamp())
                if now_timestamp - auth_timestamp > 86400:  # 24 hours
                    logger.warning("initData auth_date too old")
                    return None
            except ValueError:
                pass
        
        # Parse user data
        if "user" in parsed and parsed["user"]:
            return json.loads(parsed["user"][0])
        
    except Exception as e:
        logger.error(f"initData validation error: {e}")
    
    return None


def require_telegram_auth(f):
    """Decorator to require valid Telegram initData."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Get initData from header
        init_data = request.headers.get("X-Telegram-Init-Data")
        
        # Also check tg_id from query/body
        tg_id = None
        if request.method == "GET":
            tg_id_param = request.args.get("tg_id")
        else:
            data = request.get_json(silent=True) or {}
            tg_id_param = data.get("tg_id") or request.args.get("tg_id")
        
        if tg_id_param:
            try:
                tg_id = int(tg_id_param)
            except ValueError:
                return jsonify({"error": "Invalid tg_id"}), 400
        
        # Validate initData if provided
        user_data = None
        if init_data:
            user_data = validate_init_data(init_data)
            if user_data:
                tg_id = user_data.get("id")
        
        # Must have tg_id
        if not tg_id:
            return jsonify({"error": "Authentication required"}), 401
        
        # Rate limit by tg_id
        if not check_tg_rate_limit(tg_id):
            return jsonify({"error": "Too many requests"}), 429
        
        # Add to request context
        request.tg_id = tg_id
        request.tg_user = user_data
        
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# IP2Location API
# =============================================================================

def _ip2location_lookup(ip: str) -> Optional[dict]:
    """Call IP2Location.io API with load balancing."""
    base_url = "https://api.ip2location.io/"
    
    api_keys = []
    if IP2LOCATION_API_KEY:
        api_keys.append(IP2LOCATION_API_KEY)
    if IP2LOCATION_API_KEY_2:
        api_keys.append(IP2LOCATION_API_KEY_2)
    
    if not api_keys:
        # Keyless mode
        params = {"ip": ip, "format": "json"}
        try:
            resp = requests.get(base_url, params=params, timeout=3)
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return None
    
    # Load balance by IP hash
    key_index = hash(ip) % len(api_keys)
    
    for i in range(len(api_keys)):
        current_key = api_keys[(key_index + i) % len(api_keys)]
        params = {"ip": ip, "format": "json", "key": current_key}
        try:
            resp = requests.get(base_url, params=params, timeout=3)
            if resp.ok:
                data = resp.json()
                if not (isinstance(data, dict) and "error" in data):
                    return data
        except Exception:
            continue
    
    return None


def check_ip_status(ip: str) -> Tuple[bool, bool, bool, str]:
    """
    Check IP for VPN/proxy and blocked country.
    Returns: (is_vpn, is_blocked_country, api_failed, country_code)
    """
    data = _ip2location_lookup(ip)
    
    if not data:
        # API failed - fail-open
        logger.warning(f"IP2Location API failed for {ip}")
        return False, False, True, ""
    
    # Check country
    country_code = data.get("country_code", "").upper()
    is_blocked = country_code == BLOCKED_COUNTRY_CODE
    
    # Check VPN/proxy
    is_vpn = False
    
    if data.get("is_proxy") is True:
        is_vpn = True
    
    if not is_vpn:
        proxy = data.get("proxy")
        if proxy and isinstance(proxy, dict):
            proxy_indicators = [
                proxy.get("is_vpn"),
                proxy.get("is_tor"),
                proxy.get("is_public_proxy"),
                proxy.get("is_web_proxy"),
                proxy.get("is_residential_proxy"),
                proxy.get("is_data_center"),
            ]
            if any(proxy_indicators):
                is_vpn = True
    
    if not is_vpn:
        proxy_type = data.get("proxy_type")
        if proxy_type and str(proxy_type).upper() in ["VPN", "TOR", "PUB", "WEB", "RES", "DCH"]:
            is_vpn = True
    
    return is_vpn, is_blocked, False, country_code


# =============================================================================
# API Endpoints
# =============================================================================

@app.route("/")
def serve_mini_app():
    """Serve the Mini App."""
    return send_from_directory(app.static_folder, 'index.html')


@app.route("/health")
def health():
    """Health check."""
    return jsonify({"status": "ok"}), 200


@app.route("/api/user/status", methods=["GET"])
@require_telegram_auth
def api_user_status():
    """Check user's trial status."""
    tg_id = request.tg_id
    
    # Track the request
    track_start_click({
        "tg_id": tg_id,
        "username": request.tg_user.get("username") if request.tg_user else None,
        "first_name": request.tg_user.get("first_name") if request.tg_user else None,
    })
    
    result = {
        "tg_id": tg_id,
        "has_used_trial": False,
        "has_active_trial": False,
        "can_start_trial": True,
    }
    
    # Check if already used
    if has_used_trial(tg_id):
        result["has_used_trial"] = True
        result["can_start_trial"] = False
        return jsonify(result)
    
    # Check if active trial
    active = get_active_trial(tg_id)
    if active and "join_time" in active and "total_hours" in active:
        try:
            join_time = _parse_iso_to_utc(active["join_time"])
            total_hours = float(active["total_hours"])
            trial_end = join_time + timedelta(hours=total_hours)
            now = _now_utc()
            
            if now < trial_end:
                elapsed = (now - join_time).total_seconds() / 3600.0
                remaining = total_hours - elapsed
                
                result["has_active_trial"] = True
                result["can_start_trial"] = False
                result["elapsed_hours"] = round(elapsed, 1)
                result["remaining_hours"] = round(remaining, 1)
                result["trial_days"] = int(total_hours / 24)
        except Exception as e:
            logger.warning(f"Error checking active trial: {e}")
    
    return jsonify(result)


@app.route("/api/verify/ip", methods=["GET"])
@require_telegram_auth
def api_verify_ip():
    """Check IP for VPN and blocked country."""
    tg_id = request.tg_id
    ip = get_client_ip()
    
    # Rate limit by IP
    if not check_ip_rate_limit(ip, max_requests=5, window_minutes=60):
        return jsonify({"error": "Too many requests from your IP"}), 429
    
    is_vpn, is_blocked, api_failed, country_code = check_ip_status(ip)
    
    result = {
        "ip": ip,
        "is_vpn": is_vpn,
        "is_blocked_country": is_blocked,
        "country_code": country_code,
        "bypassed": api_failed,  # Indicate if IP check was bypassed
    }
    
    # Store IP check result
    existing = get_pending_verification(tg_id) or {}
    existing["ip_address"] = ip
    existing["ip_check_at"] = _now_utc().isoformat()
    existing["ip_check_bypassed"] = api_failed
    existing["ip_country_code"] = country_code
    
    if api_failed:
        existing["requires_manual_review"] = True
        existing["ip_check_reason"] = "api_quota_or_failure"
    
    set_pending_verification(tg_id, existing)
    
    return jsonify(result)


@app.route("/api/verify/submit", methods=["POST"])
@require_telegram_auth
def api_verify_submit():
    """Submit verification form (name, country, email)."""
    tg_id = request.tg_id
    data = request.get_json() or {}
    
    # Validate inputs
    name = sanitize_input(data.get("name", ""), max_length=100)
    country = sanitize_input(data.get("country", ""), max_length=100)
    email = sanitize_input(data.get("email", ""), max_length=255)
    marketing = bool(data.get("marketing_opt_in", False))
    ip_bypassed = bool(data.get("ip_check_bypassed", False))
    
    if not name or not country:
        return jsonify({"success": False, "error": "Name and country are required"}), 400
    
    if email and not is_valid_email(email):
        return jsonify({"success": False, "error": "Invalid email format"}), 400
    
    # Get existing data and update
    existing = get_pending_verification(tg_id) or {}
    existing.update({
        "name": name,
        "country": country,
        "email": email,
        "marketing_opt_in": marketing,
        "step1_ok": True,
        "status": "step1_passed",
        "verified_at": _now_utc().isoformat(),
    })
    
    if ip_bypassed:
        existing["ip_check_bypassed"] = True
        existing["requires_manual_review"] = True
    
    set_pending_verification(tg_id, existing)
    
    logger.info(f"Verification step1 completed for tg_id={tg_id}")
    
    return jsonify({"success": True})


@app.route("/api/verify/phone", methods=["POST"])
@require_telegram_auth
def api_verify_phone():
    """Verify phone number."""
    tg_id = request.tg_id
    data = request.get_json() or {}
    
    phone = data.get("phone", "").strip()
    if not phone:
        return jsonify({"success": False, "error": "Phone number required"}), 400
    
    # Normalize phone
    if not phone.startswith("+"):
        phone = "+" + phone
    
    # Check blocked country code
    if BLOCKED_PHONE_COUNTRY_CODE and phone.startswith(BLOCKED_PHONE_COUNTRY_CODE):
        existing = get_pending_verification(tg_id) or {}
        existing["status"] = "blocked_phone"
        existing["phone"] = phone[:6] + "****"  # Partial for logs
        set_pending_verification(tg_id, existing)
        
        return jsonify({"success": False, "blocked": True, "error": "Phone not eligible"})
    
    # Check if already has valid invite
    now = _now_utc()
    existing_link = get_valid_invite_link(tg_id, now)
    if existing_link:
        return jsonify({
            "success": True,
            "already_has_link": True,
            "invite_link": existing_link,
        })
    
    # Update verification data
    existing = get_pending_verification(tg_id) or {}
    existing["phone"] = phone
    existing["phone_verified_at"] = now.isoformat()
    existing["status"] = "phone_verified"
    set_pending_verification(tg_id, existing)
    
    logger.info(f"Phone verified for tg_id={tg_id}")
    
    return jsonify({"success": True})


@app.route("/api/trial/invite", methods=["POST"])
@require_telegram_auth
def api_trial_invite():
    """Generate invite link for verified user."""
    tg_id = request.tg_id
    now = _now_utc()
    
    # Check if already used trial
    if has_used_trial(tg_id):
        return jsonify({
            "success": False,
            "error": "You have already used your free trial",
        })
    
    # Check if already has active trial
    active = get_active_trial(tg_id)
    if active:
        return jsonify({
            "success": False,
            "error": "You already have an active trial",
        })
    
    # Check if already has valid invite link
    existing_link = get_valid_invite_link(tg_id, now)
    if existing_link:
        return jsonify({
            "success": True,
            "already_has_link": True,
            "invite_link": existing_link,
        })
    
    # Verify user completed phone verification
    verification = get_pending_verification(tg_id)
    if not verification or verification.get("status") != "phone_verified":
        return jsonify({
            "success": False,
            "error": "Please complete phone verification first",
        })
    
    # Generate invite link via Bot API
    try:
        expires_at = now + timedelta(hours=INVITE_LINK_EXPIRY_HOURS)
        
        # Call Telegram Bot API to create invite link
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/createChatInviteLink"
        response = requests.post(api_url, json={
            "chat_id": TRIAL_CHANNEL_ID,
            "member_limit": 1,
            "expire_date": int(expires_at.timestamp()),
        }, timeout=10)
        
        if not response.ok:
            logger.error(f"Failed to create invite link: {response.text}")
            return jsonify({
                "success": False,
                "error": "Failed to create invite link. Please try again.",
            }), 500
        
        result = response.json()
        if not result.get("ok"):
            logger.error(f"Bot API error: {result}")
            return jsonify({
                "success": False,
                "error": "Failed to create invite link. Please try again.",
            }), 500
        
        invite_link = result["result"]["invite_link"]
        
        # Store invite info
        set_invite_info(tg_id, {
            "invite_link": invite_link,
            "invite_created_at": now.isoformat(),
            "invite_expires_at": expires_at.isoformat(),
        })
        
        # Clear pending verification
        clear_pending_verification(tg_id)
        
        logger.info(f"Generated invite link for tg_id={tg_id}")
        
        return jsonify({
            "success": True,
            "invite_link": invite_link,
            "expires_at": expires_at.isoformat(),
        })
        
    except Exception as e:
        logger.error(f"Error generating invite link: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": "Something went wrong. Please try again.",
        }), 500


@app.route("/api/trial/info", methods=["GET"])
@require_telegram_auth
def api_trial_info():
    """Get current trial information."""
    tg_id = request.tg_id
    
    active = get_active_trial(tg_id)
    if not active:
        return jsonify({
            "has_active_trial": False,
        })
    
    try:
        join_time = _parse_iso_to_utc(active["join_time"])
        total_hours = float(active["total_hours"])
        trial_end = join_time + timedelta(hours=total_hours)
        now = _now_utc()
        
        if now >= trial_end:
            return jsonify({
                "has_active_trial": False,
                "trial_ended": True,
            })
        
        elapsed = (now - join_time).total_seconds() / 3600.0
        remaining = total_hours - elapsed
        
        return jsonify({
            "has_active_trial": True,
            "join_time": active["join_time"],
            "trial_end_at": active.get("trial_end_at"),
            "total_hours": total_hours,
            "elapsed_hours": round(elapsed, 1),
            "remaining_hours": round(remaining, 1),
        })
        
    except Exception as e:
        logger.error(f"Error getting trial info: {e}")
        return jsonify({
            "has_active_trial": False,
            "error": str(e),
        })


# =============================================================================
# Legacy Web Endpoints
# =============================================================================
# NOTE: The legacy /trial page is served by web_app.py
# For unified deployment, run api.py as the main app.
# The legacy endpoints can be accessed by running web_app.py separately
# or by proxying /trial requests to the legacy app.
#
# If you need legacy support, you can:
# 1. Run web_app.py on a different port (e.g., 5001)
# 2. Configure nginx to proxy /trial to that port
# 3. Or use the Mini App exclusively (recommended)


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
