# 🚀 Quick Reference Guide

## 🔒 Security Status: PRODUCTION READY ✅

All critical security vulnerabilities have been fixed. Your bot is secure and ready to ship!

---

## ⚡ Quick Setup

### 1. Add API Secret (Recommended)

```bash
# Generate a secure random secret
openssl rand -hex 32

# Add to .env file
echo "API_SECRET=$(openssl rand -hex 32)" >> .env
```

### 2. Restart Services

```bash
systemctl restart web-app.service
systemctl restart telegram-bot.service
```

---

## 📋 Environment Variables

### Required:
```env
BOT_TOKEN=your_telegram_bot_token
TRIAL_CHANNEL_ID=-1001234567890
BASE_URL=https://yourdomain.com
```

### Recommended:
```env
API_SECRET=your_random_secret_here
IP2LOCATION_API_KEY=your_key
BLOCKED_COUNTRY_CODE=PK
```

### Optional:
```env
IP2LOCATION_API_KEY_2=backup_key
BLOCKED_PHONE_COUNTRY_CODE=+91
TIMEZONE_OFFSET_HOURS=0
ENABLE_DEBUG_IP=0
```

---

## 🔍 Security Features

✅ **API Authentication** - Protects `/api/get-verification`  
✅ **Input Sanitization** - Prevents XSS attacks  
✅ **IP Rate Limiting** - Prevents distributed attacks  
✅ **Trial Validation** - Detects data tampering  
✅ **Periodic Cleanup** - Ensures trials end  
✅ **Clock Detection** - Prevents time manipulation  
✅ **30-Day Cooldown** - Prevents rapid re-trials  

---

## 🐛 Common Issues

### Bot can't connect to web app
- Check `BASE_URL` is correct
- Check `API_SECRET` matches in both services
- Check firewall allows connections

### Users getting rate limited
- Adjust limits in `web_app.py` if needed
- Check if legitimate traffic or attack

### Trials not ending
- Check periodic cleanup job is running
- Check logs for errors
- Verify `TRIAL_CHANNEL_ID` is correct

---

## 📊 Monitoring

### Check Service Status
```bash
systemctl status web-app.service
systemctl status telegram-bot.service
```

### View Logs
```bash
journalctl -u web-app.service -f
journalctl -u telegram-bot.service -f
```

### Check Files
```bash
ls -la /opt/trial_bot/add_or_remove_users/*.json
```

---

## 🎯 Key Files

- `bot.py` - Telegram bot logic
- `web_app.py` - Web verification app
- `storage.py` - Data storage functions
- `.env` - Environment variables (NOT in git!)

---

## 📝 Update Process

1. Pull latest code: `git pull`
2. Update dependencies: `pip install -r requirements.txt`
3. Restart services: `systemctl restart web-app telegram-bot`
4. Check logs: `journalctl -u web-app -f`

---

## ✅ Pre-Deployment Checklist

### Environment
- [ ] `BOT_TOKEN` set
- [ ] `TRIAL_CHANNEL_ID` set
- [ ] `BASE_URL` points to HTTPS domain
- [ ] `API_SECRET` set (recommended)

### Security
- [ ] Services running as `trialbot` user (NOT root!)
- [ ] `.env` file has restricted permissions (`chmod 600`)
- [ ] Nginx with HTTPS/TLS configured
- [ ] `/debug-ip` endpoint disabled (`ENABLE_DEBUG_IP=0`)

### Verification
- [ ] Services running: `systemctl status web-app telegram-bot`
- [ ] Logs look good: `journalctl -u web-app -f`
- [ ] Test trial flow works end-to-end

---

## 🆘 Support

If you encounter issues:
1. Check logs first
2. Verify environment variables
3. Test API endpoints manually
4. Review `SECURITY_AUDIT_REPORT.md` for details

---

**Your bot is secure and ready! 🎉**

