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
import random
import re
import urllib.parse

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, redirect
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
IPAPI_API_KEY = os.environ.get("IPAPI_API_KEY", "")
IPAPI_API_KEY_2 = os.environ.get("IPAPI_API_KEY_2", "")  # Optional backup key
BLOCKED_COUNTRY_CODE = os.environ.get("BLOCKED_COUNTRY_CODE", "PK").upper()
BLOCKED_PHONE_COUNTRY_CODE = os.environ.get("BLOCKED_PHONE_COUNTRY_CODE", "+91")
GIVEAWAY_CHANNEL_URL = os.environ.get("GIVEAWAY_CHANNEL_URL", "https://t.me/Freya_Trades")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@cogitosk")
INVITE_LINK_EXPIRY_HOURS = int(os.environ.get("INVITE_LINK_EXPIRY_HOURS", "5"))
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Letttttmeeeeeeiiiiiiinbot")

# SECURITY: Fail fast if API_SECRET not set or too short
if not API_SECRET or len(API_SECRET) < 32:
    logger.critical(
        "❌ FATAL: API_SECRET not set or too short (min 32 chars)! "
        "Set API_SECRET in .env file."
    )
    raise SystemExit("API_SECRET must be set and at least 32 characters long")

# Flask app
app = Flask(__name__, static_folder='mini_app', static_url_path='')

# SECURITY: Limit request body size to prevent large POST attacks
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024  # 16 KB max

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


def _get_daily_verification_count() -> int:
    """
    Get a random verification count that stays consistent for the day.
    Uses current date as seed so the number is the same all day but changes daily.
    Returns a number between 200-300.
    """
    today = datetime.now(timezone.utc).date()
    # Use date as seed for consistent daily number
    seed = int(today.strftime('%Y%m%d'))
    random.seed(seed)
    return random.randint(200, 300)


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
        
        # Check auth_date is not too old (CRITICAL: Use 5 minutes max per Telegram recommendations)
        auth_date_list = parsed.get("auth_date", [])
        if auth_date_list and auth_date_list[0]:
            try:
                auth_timestamp = int(auth_date_list[0])
                now_timestamp = int(_now_utc().timestamp())
                time_diff = now_timestamp - auth_timestamp
                
                # SECURITY: 5 minutes (300 seconds) max to prevent replay attacks
                # Telegram recommends this to prevent intercepted initData from being reused
                if time_diff > 300:
                    logger.warning(f"initData auth_date too old: {time_diff} seconds (max 300)")
                    return None
                
                # Also reject future timestamps (clock skew attacks)
                if time_diff < -60:  # Allow 1 minute clock skew
                    logger.warning(f"initData auth_date in future: {time_diff} seconds")
                    return None
                    
            except ValueError as e:
                logger.warning(f"Invalid auth_date format: {e}")
                return None
        
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
        validated_via_init_data = False
        if init_data:
            user_data = validate_init_data(init_data)
            if user_data:
                tg_id = user_data.get("id")
                validated_via_init_data = True
        
        # Must have tg_id
        if not tg_id:
            return jsonify({"error": "Authentication required"}), 401
        
        # SECURITY: If tg_id provided without valid initData, add extra IP rate limiting
        # This prevents attackers from spamming endpoints with arbitrary tg_ids
        if not validated_via_init_data:
            ip = get_client_ip()
            # Much stricter rate limit for non-validated requests: 3 per 10 minutes per IP
            if not check_ip_rate_limit(ip, max_requests=3, window_minutes=10):
                logger.warning(f"Rate limited IP {ip} for unauthenticated tg_id access")
                return jsonify({"error": "Too many requests"}), 429
        
        # SECURITY FIX #1: If initData was validated, tg_id MUST match authenticated user
        if validated_via_init_data and user_data:
            if tg_id != user_data.get("id"):
                logger.warning(f"tg_id mismatch: requested={tg_id}, authenticated={user_data.get('id')}")
                return jsonify({"error": "tg_id does not match authenticated user"}), 403
        
        # Rate limit by tg_id
        if not check_tg_rate_limit(tg_id):
            return jsonify({"error": "Too many requests"}), 429
        
        # Add to request context
        request.tg_id = tg_id
        request.tg_user = user_data
        request.validated_via_init_data = validated_via_init_data
        
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# ipapi.is API
# =============================================================================

def _ipapi_lookup(ip: str) -> Optional[dict]:
    """
    Call ipapi.is API to get IP geolocation and threat data.
    Docs: https://ipapi.is/
    """
    base_url = "https://api.ipapi.is"
    
    api_keys = []
    if IPAPI_API_KEY:
        api_keys.append(IPAPI_API_KEY)
    if IPAPI_API_KEY_2:
        api_keys.append(IPAPI_API_KEY_2)
    
    if not api_keys:
        logger.warning("No IPAPI_API_KEY configured - IP checks disabled")
        return None
    
    # Load balance by IP hash
    key_index = hash(ip) % len(api_keys)
    
    for i in range(len(api_keys)):
        current_key = api_keys[(key_index + i) % len(api_keys)]
        params = {"q": ip, "key": current_key}
        try:
            resp = requests.get(base_url, params=params, timeout=5)
            if resp.ok:
                data = resp.json()
                # Check for error response
                if not (isinstance(data, dict) and "error" in data):
                    return data
        except Exception as e:
            logger.warning(f"ipapi.is API call failed: {e}")
            continue
    
    return None


def check_ip_status(ip: str) -> Tuple[bool, bool, bool, str]:
    """
    Check IP for VPN/TOR and blocked country using ipapi.is.
    Returns: (is_vpn, is_blocked_country, api_failed, country_code)
    
    Blocks:
    - VPN users (is_vpn)
    - TOR exit nodes (is_tor)
    - Blocked country (location.country_code matches BLOCKED_COUNTRY_CODE)
    """
    data = _ipapi_lookup(ip)
    
    if not data:
        # API failed - fail-open
        logger.warning(f"ipapi.is API failed for {ip}")
        return False, False, True, ""
    
    # Check country from location object
    location = data.get("location", {})
    country_code = location.get("country_code", "").upper()
    is_blocked = country_code == BLOCKED_COUNTRY_CODE
    
    # Check VPN and TOR
    is_vpn = False
    
    # Block VPN users
    if data.get("is_vpn") is True:
        is_vpn = True
        logger.info(f"IP {ip} detected as VPN")
    
    # Block TOR exit nodes
    if data.get("is_tor") is True:
        is_vpn = True
        logger.info(f"IP {ip} detected as TOR exit node")
    
    return is_vpn, is_blocked, False, country_code


# =============================================================================
# API Endpoints
# =============================================================================

@app.route("/")
def serve_home():
    """
    Serve different content based on request source:
    - Telegram Mini App: serve mini_app/index.html
    - Regular browser: serve templates/index.html (landing page)
    """
    # Check if request is from Telegram
    # 1. Check for tgWebAppData in URL (Mini App passes this)
    # 2. Check User-Agent for Telegram
    # 3. Check for tg_id parameter (fallback flow)
    
    is_telegram = False
    
    # Check query params for Telegram Mini App data
    if request.args.get('tgWebAppData') or request.args.get('tgWebAppStartParam'):
        is_telegram = True
    
    # Check User-Agent for Telegram client
    user_agent = request.headers.get('User-Agent', '').lower()
    if 'telegram' in user_agent or 'tg' in user_agent:
        is_telegram = True
    
    # Check Referer for Telegram
    referer = request.headers.get('Referer', '').lower()
    if 't.me' in referer or 'telegram' in referer:
        is_telegram = True
    
    if is_telegram:
        # Serve Mini App for Telegram users
        return send_from_directory(app.static_folder, 'index.html')
    else:
        # Serve landing page for regular browsers
        return send_from_directory('templates', 'index.html')


@app.route("/app")
def serve_miniapp():
    """
    Dedicated route for the Mini App.
    Always serves mini_app/index.html regardless of User-Agent.
    Use this URL in BotFather and for the Menu Button.
    """
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
        "has_invite_link": False,
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
                return jsonify(result)
        except Exception as e:
            logger.warning(f"Error checking active trial: {e}")
    
    # Check if user has a valid invite link (not expired)
    # CRITICAL: This prevents users who generated a link but didn't join yet
    # from being forced to restart the flow and generate a new link
    now = _now_utc()
    existing_link = get_valid_invite_link(tg_id, now)
    if existing_link:
        result["has_invite_link"] = True
        result["invite_link"] = existing_link
        result["can_start_trial"] = False
        logger.info(f"User {tg_id} has valid invite link, showing it instead of welcome screen")
    
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
    
    # Send immediate follow-up message to user via Telegram
    # This prompts them to complete identity confirmation
    try:
        daily_count = _get_daily_verification_count()
        message_text = (
            "✅ *Step 1 Complete!*\n\n"
            "Great job! You've passed the initial verification.\n\n"
            "✅ *One more step:* Confirm your identity to unlock trial access\n\n"
            f"🛡️ *Secure Verification* ({daily_count}+ traders verified today)\n\n"
            "_Your privacy is fully protected._\n\n"
            "👇 Tap the button below to continue:"
        )
        
        inline_keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Confirm & Continue", "callback_data": "continue_verification"}
            ]]
        }
        
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        response = requests.post(api_url, json={
            "chat_id": tg_id,
            "text": message_text,
            "parse_mode": "Markdown",
            "reply_markup": inline_keyboard
        }, timeout=5)
        
        if response.ok:
            logger.info(f"Sent step1 follow-up message to tg_id={tg_id}")
        else:
            logger.warning(f"Failed to send step1 message to tg_id={tg_id}: {response.text}")
    except Exception as e:
        logger.warning(f"Error sending step1 follow-up to tg_id={tg_id}: {e}")
        # Don't fail the verification if message fails
    
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
    
    # SECURITY: This sensitive endpoint should only be accessible via valid initData
    # or the bot's fallback flow (which requires phone verification anyway)
    if not getattr(request, 'validated_via_init_data', False):
        logger.warning(f"Trial invite attempt without initData for tg_id={tg_id}")
        return jsonify({
            "success": False,
            "error": "Please use the Telegram app to access this feature",
        }), 403
    
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
# Legacy / Fallback Endpoints (for non-Mini App clients)
# =============================================================================

@app.route("/trial")
def trial_page():
    """
    Fallback trial page for non-Mini App clients.
    Redirects to Mini App with tg_id query param preserved.
    """
    tg_id = request.args.get("tg_id")
    if tg_id:
        # For browsers that don't support WebApp, show a simple verification page
        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Free Trial Verification</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {{
            --bg-primary: #000000;
            --bg-card: #1a1a1a;
            --text-primary: #ffffff;
            --text-secondary: #9ca3af;
            --accent-primary: #8b5cf6;
            --accent-gradient: linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%);
            --border-color: #2a2a2a;
        }}
        body {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 16px;
        }}
        .card {{
            background: var(--bg-card);
            border-radius: 12px;
            padding: 24px;
            max-width: 420px;
            width: 100%;
            border: 1px solid var(--border-color);
            position: relative;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }}
        h2 {{ margin-top: 0; font-size: 1.5rem; margin-bottom: 0.5rem; }}
        p {{ color: var(--text-secondary); margin-top: 0; margin-bottom: 1.5rem; line-height: 1.5; }}
        form {{ display: flex; flex-direction: column; gap: 16px; }}
        input, select {{
            width: 100%;
            padding: 14px 16px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--bg-card);
            color: var(--text-primary);
            font-size: 15px;
            box-sizing: border-box;
        }}
        input:focus, select:focus {{
            outline: none;
            border-color: var(--accent-primary);
        }}
        button {{
            padding: 16px;
            border-radius: 999px;
            border: none;
            background: var(--accent-gradient);
            color: white;
            font-weight: 600;
            font-size: 16px;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        button:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(139, 92, 246, 0.4);
        }}
        button:disabled {{
            opacity: 0.7;
            transform: none;
            cursor: wait;
        }}
        .hidden {{ display: none !important; }}
        .success-modal {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: var(--bg-card);
            border-radius: 12px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            z-index: 10;
        }}
        .success-icon {{ font-size: 3rem; margin-bottom: 1rem; }}
        .spinner {{
            width: 32px;
            height: 32px;
            border: 3px solid var(--border-color);
            border-top-color: var(--accent-primary);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin: 1.5rem auto;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        .error {{ color: #ef4444; font-size: 0.9rem; margin-top: 0.5rem; }}
    </style>
</head>
<body>
    <div class="card">
        <div id="form-content">
            <h2>Free Trial Verification</h2>
            <p>Complete this form to verify your eligibility.</p>
            <form id="vform">
                <input type="hidden" id="tg_id" value="{tg_id}">
                <input type="text" id="name" placeholder="Your Name" required>
                <select id="country" required>
                    <option value="">Select Country</option>
                    <option value="Afghanistan">Afghanistan</option>
                    <option value="Albania">Albania</option>
                    <option value="Algeria">Algeria</option>
                    <option value="Andorra">Andorra</option>
                    <option value="Angola">Angola</option>
                    <option value="Antigua and Barbuda">Antigua and Barbuda</option>
                    <option value="Argentina">Argentina</option>
                    <option value="Armenia">Armenia</option>
                    <option value="Australia">Australia</option>
                    <option value="Austria">Austria</option>
                    <option value="Azerbaijan">Azerbaijan</option>
                    <option value="Bahamas">Bahamas</option>
                    <option value="Bahrain">Bahrain</option>
                    <option value="Bangladesh">Bangladesh</option>
                    <option value="Barbados">Barbados</option>
                    <option value="Belarus">Belarus</option>
                    <option value="Belgium">Belgium</option>
                    <option value="Belize">Belize</option>
                    <option value="Benin">Benin</option>
                    <option value="Bhutan">Bhutan</option>
                    <option value="Bolivia">Bolivia</option>
                    <option value="Bosnia and Herzegovina">Bosnia and Herzegovina</option>
                    <option value="Botswana">Botswana</option>
                    <option value="Brazil">Brazil</option>
                    <option value="Brunei">Brunei</option>
                    <option value="Bulgaria">Bulgaria</option>
                    <option value="Burkina Faso">Burkina Faso</option>
                    <option value="Burundi">Burundi</option>
                    <option value="Cambodia">Cambodia</option>
                    <option value="Cameroon">Cameroon</option>
                    <option value="Canada">Canada</option>
                    <option value="Cape Verde">Cape Verde</option>
                    <option value="Central African Republic">Central African Republic</option>
                    <option value="Chad">Chad</option>
                    <option value="Chile">Chile</option>
                    <option value="China">China</option>
                    <option value="Colombia">Colombia</option>
                    <option value="Comoros">Comoros</option>
                    <option value="Congo">Congo</option>
                    <option value="Costa Rica">Costa Rica</option>
                    <option value="Croatia">Croatia</option>
                    <option value="Cuba">Cuba</option>
                    <option value="Cyprus">Cyprus</option>
                    <option value="Czech Republic">Czech Republic</option>
                    <option value="Denmark">Denmark</option>
                    <option value="Djibouti">Djibouti</option>
                    <option value="Dominica">Dominica</option>
                    <option value="Dominican Republic">Dominican Republic</option>
                    <option value="East Timor">East Timor</option>
                    <option value="Ecuador">Ecuador</option>
                    <option value="Egypt">Egypt</option>
                    <option value="El Salvador">El Salvador</option>
                    <option value="Equatorial Guinea">Equatorial Guinea</option>
                    <option value="Eritrea">Eritrea</option>
                    <option value="Estonia">Estonia</option>
                    <option value="Eswatini">Eswatini</option>
                    <option value="Ethiopia">Ethiopia</option>
                    <option value="Fiji">Fiji</option>
                    <option value="Finland">Finland</option>
                    <option value="France">France</option>
                    <option value="Gabon">Gabon</option>
                    <option value="Gambia">Gambia</option>
                    <option value="Georgia">Georgia</option>
                    <option value="Germany">Germany</option>
                    <option value="Ghana">Ghana</option>
                    <option value="Greece">Greece</option>
                    <option value="Grenada">Grenada</option>
                    <option value="Guatemala">Guatemala</option>
                    <option value="Guinea">Guinea</option>
                    <option value="Guinea-Bissau">Guinea-Bissau</option>
                    <option value="Guyana">Guyana</option>
                    <option value="Haiti">Haiti</option>
                    <option value="Honduras">Honduras</option>
                    <option value="Hungary">Hungary</option>
                    <option value="Iceland">Iceland</option>
                    <option value="India">India</option>
                    <option value="Indonesia">Indonesia</option>
                    <option value="Iran">Iran</option>
                    <option value="Iraq">Iraq</option>
                    <option value="Ireland">Ireland</option>
                    <option value="Israel">Israel</option>
                    <option value="Italy">Italy</option>
                    <option value="Ivory Coast">Ivory Coast</option>
                    <option value="Jamaica">Jamaica</option>
                    <option value="Japan">Japan</option>
                    <option value="Jordan">Jordan</option>
                    <option value="Kazakhstan">Kazakhstan</option>
                    <option value="Kenya">Kenya</option>
                    <option value="Kiribati">Kiribati</option>
                    <option value="Kuwait">Kuwait</option>
                    <option value="Kyrgyzstan">Kyrgyzstan</option>
                    <option value="Laos">Laos</option>
                    <option value="Latvia">Latvia</option>
                    <option value="Lebanon">Lebanon</option>
                    <option value="Lesotho">Lesotho</option>
                    <option value="Liberia">Liberia</option>
                    <option value="Libya">Libya</option>
                    <option value="Liechtenstein">Liechtenstein</option>
                    <option value="Lithuania">Lithuania</option>
                    <option value="Luxembourg">Luxembourg</option>
                    <option value="Madagascar">Madagascar</option>
                    <option value="Malawi">Malawi</option>
                    <option value="Malaysia">Malaysia</option>
                    <option value="Maldives">Maldives</option>
                    <option value="Mali">Mali</option>
                    <option value="Malta">Malta</option>
                    <option value="Marshall Islands">Marshall Islands</option>
                    <option value="Mauritania">Mauritania</option>
                    <option value="Mauritius">Mauritius</option>
                    <option value="Mexico">Mexico</option>
                    <option value="Micronesia">Micronesia</option>
                    <option value="Moldova">Moldova</option>
                    <option value="Monaco">Monaco</option>
                    <option value="Mongolia">Mongolia</option>
                    <option value="Montenegro">Montenegro</option>
                    <option value="Morocco">Morocco</option>
                    <option value="Mozambique">Mozambique</option>
                    <option value="Myanmar">Myanmar</option>
                    <option value="Namibia">Namibia</option>
                    <option value="Nauru">Nauru</option>
                    <option value="Nepal">Nepal</option>
                    <option value="Netherlands">Netherlands</option>
                    <option value="New Zealand">New Zealand</option>
                    <option value="Nicaragua">Nicaragua</option>
                    <option value="Niger">Niger</option>
                    <option value="Nigeria">Nigeria</option>
                    <option value="North Korea">North Korea</option>
                    <option value="North Macedonia">North Macedonia</option>
                    <option value="Norway">Norway</option>
                    <option value="Oman">Oman</option>
                    <option value="Pakistan">Pakistan</option>
                    <option value="Palau">Palau</option>
                    <option value="Palestine">Palestine</option>
                    <option value="Panama">Panama</option>
                    <option value="Papua New Guinea">Papua New Guinea</option>
                    <option value="Paraguay">Paraguay</option>
                    <option value="Peru">Peru</option>
                    <option value="Philippines">Philippines</option>
                    <option value="Poland">Poland</option>
                    <option value="Portugal">Portugal</option>
                    <option value="Qatar">Qatar</option>
                    <option value="Romania">Romania</option>
                    <option value="Russia">Russia</option>
                    <option value="Rwanda">Rwanda</option>
                    <option value="Saint Kitts and Nevis">Saint Kitts and Nevis</option>
                    <option value="Saint Lucia">Saint Lucia</option>
                    <option value="Saint Vincent and the Grenadines">Saint Vincent and the Grenadines</option>
                    <option value="Samoa">Samoa</option>
                    <option value="San Marino">San Marino</option>
                    <option value="Sao Tome and Principe">Sao Tome and Principe</option>
                    <option value="Saudi Arabia">Saudi Arabia</option>
                    <option value="Senegal">Senegal</option>
                    <option value="Serbia">Serbia</option>
                    <option value="Seychelles">Seychelles</option>
                    <option value="Sierra Leone">Sierra Leone</option>
                    <option value="Singapore">Singapore</option>
                    <option value="Slovakia">Slovakia</option>
                    <option value="Slovenia">Slovenia</option>
                    <option value="Solomon Islands">Solomon Islands</option>
                    <option value="Somalia">Somalia</option>
                    <option value="South Africa">South Africa</option>
                    <option value="South Korea">South Korea</option>
                    <option value="South Sudan">South Sudan</option>
                    <option value="Spain">Spain</option>
                    <option value="Sri Lanka">Sri Lanka</option>
                    <option value="Sudan">Sudan</option>
                    <option value="Suriname">Suriname</option>
                    <option value="Sweden">Sweden</option>
                    <option value="Switzerland">Switzerland</option>
                    <option value="Syria">Syria</option>
                    <option value="Taiwan">Taiwan</option>
                    <option value="Tajikistan">Tajikistan</option>
                    <option value="Tanzania">Tanzania</option>
                    <option value="Thailand">Thailand</option>
                    <option value="Togo">Togo</option>
                    <option value="Trinidad and Tobago">Trinidad and Tobago</option>
                    <option value="Tunisia">Tunisia</option>
                    <option value="Turkey">Turkey</option>
                    <option value="Turkmenistan">Turkmenistan</option>
                    <option value="Tuvalu">Tuvalu</option>
                    <option value="Uganda">Uganda</option>
                    <option value="Ukraine">Ukraine</option>
                    <option value="United Arab Emirates">United Arab Emirates</option>
                    <option value="United Kingdom">United Kingdom</option>
                    <option value="United States">United States</option>
                    <option value="Uruguay">Uruguay</option>
                    <option value="Uzbekistan">Uzbekistan</option>
                    <option value="Vanuatu">Vanuatu</option>
                    <option value="Vatican City">Vatican City</option>
                    <option value="Venezuela">Venezuela</option>
                    <option value="Vietnam">Vietnam</option>
                    <option value="Yemen">Yemen</option>
                    <option value="Zambia">Zambia</option>
                    <option value="Zimbabwe">Zimbabwe</option>
                    <option value="Other">Other</option>
                </select>
                <input type="email" id="email" placeholder="Email (optional)">
                <button type="submit">Complete Verification</button>
                <div id="msg"></div>
            </form>
        </div>

        <!-- Success Modal (Initially Hidden) -->
        <div id="success-modal" class="success-modal hidden">
            <div class="success-icon">✨</div>
            <h2>Info Collected!</h2>
            <p>Your details have been saved.</p>
            <div class="spinner"></div>
            <p style="color: #9ca3af; font-size: 0.9rem;">Redirecting to Telegram...</p>
        </div>
    </div>
    <script>
        document.getElementById('vform').onsubmit = async function(e) {{
            e.preventDefault();
            const msg = document.getElementById('msg');
            const submitBtn = this.querySelector('button[type="submit"]');
            const formContent = document.getElementById('form-content');
            const successModal = document.getElementById('success-modal');
            
            const tg_id = document.getElementById('tg_id').value;
            const name = document.getElementById('name').value;
            const country = document.getElementById('country').value;
            const email = document.getElementById('email').value;
            
            msg.textContent = 'Verifying...';
            msg.className = '';
            submitBtn.disabled = true;
            submitBtn.style.opacity = '0.7';

            try {{
                const resp = await fetch('/api/fallback/verify', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{tg_id, name, country, email}})
                }});
                const data = await resp.json();
                
                if (data.success) {{
                    // Show success modal
                    formContent.classList.add('hidden');
                    successModal.classList.remove('hidden');
                    
                    // Redirect to bot after 2 seconds
                    setTimeout(() => {{
                        window.location.href = "https://t.me/{BOT_USERNAME}";
                    }}, 2000);
                }} else {{
                    msg.className = 'error';
                    msg.textContent = data.error || 'Error. Please try again.';
                    submitBtn.disabled = false;
                    submitBtn.style.opacity = '1';
                }}
            }} catch(err) {{
                msg.className = 'error';
                msg.textContent = 'Network error. Please try again.';
                submitBtn.disabled = false;
                submitBtn.style.opacity = '1';
            }}
        }};
    </script>
</body>
</html>
"""
    # No tg_id, redirect to main page
    return redirect("/")


@app.route("/api/fallback/verify", methods=["POST"])
def api_fallback_verify():
    """
    Simple fallback verification for non-Mini App clients.
    Stores step1_ok without requiring Telegram auth (uses tg_id from form).
    
    SECURITY: 
    - Rate limited by IP to prevent abuse
    - Origin validated to prevent CSRF
    - Input sanitized to prevent XSS
    """
    # SECURITY: Validate request origin to prevent CSRF attacks
    origin = request.headers.get("Origin", "").lower()
    referer = request.headers.get("Referer", "").lower()
    
    # Get base URL from environment
    base_url = os.environ.get("BASE_URL", "").lower().rstrip("/")
    
    # Allowed origins: our domain + Telegram web
    allowed_origins = [
        base_url,
        "https://web.telegram.org",
        "https://t.me",
    ]
    
    # Check if request comes from allowed origin
    is_valid_origin = False
    for allowed in allowed_origins:
        if allowed and (origin.startswith(allowed) or referer.startswith(allowed)):
            is_valid_origin = True
            break
    
    # Also allow if no origin (direct API calls for testing)
    # But only if coming from localhost
    if not is_valid_origin and not origin and not referer:
        client_ip = get_client_ip()
        if client_ip in ["127.0.0.1", "::1", "localhost"]:
            is_valid_origin = True
            logger.info("Allowing fallback verify from localhost with no origin")
    
    if not is_valid_origin:
        logger.warning(
            f"CSRF attempt blocked: origin={origin or 'none'}, referer={referer or 'none'}, "
            f"ip={get_client_ip()}"
        )
        return jsonify({
            "success": False, 
            "error": "Invalid request origin. Please use the official verification page."
        }), 403
    
    # SECURITY: Strict IP-based rate limiting to prevent abuse
    ip = get_client_ip()
    if not check_ip_rate_limit(ip, max_requests=3, window_minutes=30):
        logger.warning(f"Rate limited IP {ip} for fallback verify abuse")
        return jsonify({"success": False, "error": "Too many requests. Please wait and try again."}), 429
    
    data = request.get_json() or {}
    tg_id_str = data.get("tg_id")
    
    if not tg_id_str:
        return jsonify({"success": False, "error": "Missing tg_id"})
    
    try:
        tg_id = int(tg_id_str)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid tg_id"})
    
    # SECURITY: Validate tg_id is a reasonable Telegram user ID (positive, not too large)
    if tg_id <= 0 or tg_id > 9999999999999:
        return jsonify({"success": False, "error": "Invalid tg_id"})
    
    name = sanitize_input(data.get("name", ""), max_length=100)
    country = sanitize_input(data.get("country", ""), max_length=100)
    email = sanitize_input(data.get("email", ""), max_length=255)
    
    if not name or not country:
        return jsonify({"success": False, "error": "Name and country required"})
    
    # Check IP
    ip = get_client_ip()
    is_vpn, is_blocked, api_failed, country_code = check_ip_status(ip)
    
    if is_vpn and not api_failed:
        return jsonify({"success": False, "error": "VPN/Proxy detected. Please disable and try again."})
    
    if is_blocked and not api_failed:
        return jsonify({"success": False, "error": "Your country is not eligible for the trial."})
    
    # Store verification
    existing = get_pending_verification(tg_id) or {}
    existing.update({
        "tg_id": tg_id,
        "name": name,
        "country": country,
        "email": email,
        "ip_address": ip,
        "ip_country_code": country_code,
        "step1_ok": True,
        "status": "step1_passed",
        "verified_at": _now_utc().isoformat(),
        "fallback_flow": True,
    })
    
    if api_failed:
        existing["ip_check_bypassed"] = True
        existing["requires_manual_review"] = True
    
    set_pending_verification(tg_id, existing)
    logger.info(f"Fallback verification step1 for tg_id={tg_id}")
    
    # Send follow-up message to user via Telegram
    try:
        message_text = (
            "✅ *Step 1 Complete!*\n\n"
            "Great job! You've passed the initial verification.\n\n"
            "📱 *One more step:* Please share your phone number to complete verification.\n\n"
            "_We only use this to prevent bots and ensure fair access. "
            "Your privacy is protected - we never share your data._\n\n"
            "👇 Tap the button below to continue:"
        )
        
        inline_keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Continue Verification", "callback_data": "continue_verification"}
            ]]
        }
        
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        response = requests.post(api_url, json={
            "chat_id": tg_id,
            "text": message_text,
            "parse_mode": "Markdown",
            "reply_markup": inline_keyboard
        }, timeout=5)
        
        if response.ok:
            logger.info(f"Sent fallback step1 follow-up to tg_id={tg_id}")
        else:
            logger.warning(f"Failed to send fallback step1 message to tg_id={tg_id}: {response.text}")
    except Exception as e:
        logger.warning(f"Error sending fallback step1 follow-up to tg_id={tg_id}: {e}")
    
    return jsonify({"success": True})


@app.route("/api/get-verification", methods=["GET"])
def api_get_verification():
    """
    API endpoint for bot to fetch verification data.
    Allows bot to check if user passed web verification.
    
    SECURITY: Always requires API_SECRET with constant-time comparison.
    """
    # SECURITY: Always require API secret - no empty bypass
    api_secret = os.environ.get("API_SECRET", "")
    if not api_secret or len(api_secret) < 32:
        logger.error("API_SECRET not configured or too short - /api/get-verification blocked")
        return jsonify({"error": "Server configuration error"}), 500
    
    # SECURITY: ONLY accept header (never query param to avoid logging secrets)
    provided = request.headers.get("X-API-Secret")
    
    # Rate limit failed auth attempts
    ip = get_client_ip()
    
    if not provided:
        if not check_ip_rate_limit(ip, max_requests=5, window_minutes=15):
            logger.warning(f"Rate limited IP {ip} for missing API secret")
            return jsonify({"error": "Too many requests"}), 429
        logger.warning(f"Missing API secret from IP {ip}")
        return jsonify({"error": "Unauthorized"}), 401
    
    # SECURITY: Use constant-time comparison to prevent timing attacks
    import hmac
    if not hmac.compare_digest(provided.encode(), api_secret.encode()):
        if not check_ip_rate_limit(ip, max_requests=3, window_minutes=15):
            logger.warning(f"Rate limited IP {ip} for invalid API secret")
            return jsonify({"error": "Too many requests"}), 429
        logger.warning(f"Invalid API secret from IP {ip}")
        return jsonify({"error": "Unauthorized"}), 401
    
    # Validate tg_id parameter
    tg_id_str = request.args.get("tg_id")
    if not tg_id_str or not tg_id_str.isdigit():
        return jsonify({"error": "Invalid tg_id"}), 400
    
    # SECURITY: Validate tg_id range to prevent overflow
    tg_id = int(tg_id_str)
    if tg_id <= 0 or tg_id > 9999999999999:
        return jsonify({"error": "Invalid tg_id"}), 400
    
    # Fetch and return data
    data = get_pending_verification(tg_id)
    if data:
        return jsonify({"success": True, "data": data})
    return jsonify({"success": False, "data": None})


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
