# 🚀 Quick Reference Guide

## 🔒 Security Status: PRODUCTION READY ✅

All critical security vulnerabilities have been fixed.

---

## ⚡ Quick Setup

### 1. Add Required Secrets

```bash
# Generate API secret (min 32 chars required!)
openssl rand -hex 32

# Add to .env
API_SECRET=your_32_char_secret_here
ADMIN_TG_IDS=your_telegram_id
```

### 2. Restart Services

```bash
sudo systemctl restart freya-api freya-slim-bot
```

---

## 📋 Environment Variables

### Required:
```env
BOT_TOKEN=your_telegram_bot_token
TRIAL_CHANNEL_ID=-1001234567890
BASE_URL=https://yourdomain.com
API_SECRET=your_32_char_secret  # Required! Min 32 chars
ADMIN_TG_IDS=123456789          # Your Telegram ID for admin commands
```

### Optional:
```env
IP2LOCATION_API_KEY=your_key
BLOCKED_COUNTRY_CODE=PK
BLOCKED_PHONE_COUNTRY_CODE=+91
TIMEZONE_OFFSET_HOURS=0
REQUIRE_PHONE_VERIFICATION=true
TRIAL_COOLDOWN_DAYS=30
INVITE_LINK_EXPIRY_HOURS=5
```

---

## 🤖 Admin Commands

| Command | Description |
|---------|-------------|
| `/send <ids> <msg>` | Send to specific users |
| `/broadcast <msg>` | Send to ALL users |
| `/stats` | View statistics |
| `/user <id>` | Lookup user info |
| `/ban <id>` | Ban a user |
| `/unban <id>` | Unban a user |
| `/export [type]` | Export data as JSON |
| `/schedule <date> <time> <msg>` | Schedule broadcast |
| `/list_scheduled` | View pending broadcasts |
| `/cancel <id>` | Cancel broadcast |
| `/delete <chat_id> <msg_id>` | Delete message |

**Button syntax:** `[button:Label:https://url.com]`

---

## 📊 Monitoring

### Check Service Status
```bash
sudo systemctl status freya-api --no-pager
sudo systemctl status freya-slim-bot --no-pager
```

### View Logs
```bash
journalctl -u freya-api -f
journalctl -u freya-slim-bot -f
```

---

## 🎯 Key Files

- `slim_bot.py` - Main Telegram bot
- `api.py` - Mini App API backend  
- `storage.py` - Data storage
- `.env` - Environment variables

---

## 📝 Update Process

```bash
cd /opt/trial_bot/add_or_remove_users
git pull origin mini-app
pip install -r requirements.txt
sudo systemctl restart freya-api freya-slim-bot
```

---

**Your bot is secure and ready! 🎉**
