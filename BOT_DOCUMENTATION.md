# 📖 Complete Bot & Web App Documentation

> **System**: Telegram Trial Verification Bot  
> **Version**: 2.0 (Security Hardened)  
> **Last Updated**: December 2025

---

## 📋 Table of Contents

1. [System Overview](#-system-overview)
2. [Architecture Diagram](#-architecture-diagram)
3. [Complete User Journey](#-complete-user-journey)
4. [Bot Commands Reference](#-bot-commands-reference)
5. [Web App Flow](#-web-app-flow)
6. [All Possible Scenarios](#-all-possible-scenarios)
7. [Trial Channel Events](#-trial-channel-events)
8. [Scheduled Jobs & Reminders](#-scheduled-jobs--reminders)
9. [Security Features](#-security-features)
10. [Data Storage](#-data-storage)
11. [Error Handling](#-error-handling)
12. [Environment Variables](#-environment-variables)

---

## 🏗 System Overview

This system consists of **two separate services** working together:

| Component | File | Purpose | Port |
|-----------|------|---------|------|
| **Telegram Bot** | `bot.py` | Handles Telegram interactions, trial management | N/A (polling) |
| **Web App** | `web_app.py` | IP verification, VPN detection, form submission | 5000 |
| **Storage** | `storage.py` | JSON file-based data persistence | N/A |

### How They Communicate

```
┌─────────────────┐         ┌─────────────────┐
│   Telegram      │         │    Web App      │
│   Bot (bot.py)  │◄───────►│  (web_app.py)   │
└────────┬────────┘   API   └────────┬────────┘
         │                           │
         └───────────┬───────────────┘
                     ▼
            ┌─────────────────┐
            │   Storage       │
            │  (JSON files)   │
            └─────────────────┘
```

---

## 🔄 Architecture Diagram

```
                                    INTERNET
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
                    ▼                   ▼                   ▼
            ┌───────────┐       ┌───────────┐       ┌───────────┐
            │  Telegram │       │   Nginx   │       │ IP2Location│
            │  Servers  │       │  (HTTPS)  │       │    API    │
            └─────┬─────┘       └─────┬─────┘       └─────┬─────┘
                  │                   │                   │
                  │ Polling           │ Proxy             │ HTTP
                  ▼                   ▼                   ▼
            ┌─────────────────────────────────────────────────┐
            │              YOUR DROPLET                        │
            │  ┌─────────────┐         ┌─────────────┐        │
            │  │   bot.py    │◄───────►│ web_app.py  │        │
            │  │  (systemd)  │  HTTP   │  (systemd)  │        │
            │  └──────┬──────┘         └──────┬──────┘        │
            │         │                       │               │
            │         └───────────┬───────────┘               │
            │                     ▼                           │
            │         ┌─────────────────────┐                 │
            │         │    storage.py       │                 │
            │         │  ┌───────────────┐  │                 │
            │         │  │ JSON Files:   │  │                 │
            │         │  │ - pending     │  │                 │
            │         │  │ - active      │  │                 │
            │         │  │ - used        │  │                 │
            │         │  │ - invites     │  │                 │
            │         │  │ - trial_log   │  │                 │
            │         │  └───────────────┘  │                 │
            │         └─────────────────────┘                 │
            └─────────────────────────────────────────────────┘
```

---

## 👤 Complete User Journey

### Phase 1: Initial Contact with Bot

```
User opens Telegram → Searches for your bot → Clicks START
```

#### Step 1.1: User sends `/start`

**What happens internally:**
1. `start()` function in `bot.py` receives the update
2. Bot checks `has_used_trial(user.id)` in storage
3. Two possible outcomes:

**Outcome A: User has NOT used trial before**
```
Bot Response:
┌────────────────────────────────────────┐
│ Welcome! Tap the button below to       │
│ start your free trial verification.    │
│                                        │
│ [🎁 Get Free Trial]                    │
└────────────────────────────────────────┘
```

**Outcome B: User HAS used trial before**
```
Bot Response:
┌────────────────────────────────────────┐
│ You have already used your free        │
│ 3-day trial once.                      │
│                                        │
│ 🎁 For more chances, you can join our  │
│ giveaway channel:                      │
│ https://t.me/Freya_Trades              │
│                                        │
│ 💬 Or DM @cogitosk to upgrade to the   │
│ premium signals.                       │
└────────────────────────────────────────┘
```

---

### Phase 2: Starting Trial Verification

#### Step 2.1: User clicks "🎁 Get Free Trial" button

**What happens internally:**
1. `start_trial_callback()` function triggered
2. Bot generates verification URL: `{BASE_URL}/trial?tg_id={user_id}`
3. Checks if `BASE_URL` is HTTPS or HTTP

**If HTTPS (Production):**
```
Bot Response:
┌────────────────────────────────────────┐
│ Step 1: Open the verification page     │
│ to pass IP and basic checks.           │
│ After you finish there, come back      │
│ here and tap 'Continue verification'.  │
│                                        │
│ [🌐 Open verification page] ← WebApp   │
│ [✅ Continue verification]             │
└────────────────────────────────────────┘
```
The "Open verification page" opens as a **Telegram Mini App** (popup inside Telegram).

**If HTTP (Local Development):**
```
Same message but button opens in external browser
```

---

### Phase 3: Web Verification (web_app.py)

#### Step 3.1: User opens verification page

**URL:** `https://yourdomain.com/trial?tg_id=123456789`

**What happens internally:**
1. `trial()` route receives GET request
2. Extracts client IP via `get_client_ip()`
3. Performs IP rate limit check (5 requests/hour per IP)

**Possible Outcomes:**

##### Outcome A: IP Rate Limited
```html
┌────────────────────────────────────────┐
│ Too many requests from your IP         │
│ address. Please wait 60 minutes        │
│ before trying again.                   │
└────────────────────────────────────────┘
```

##### Outcome B: VPN/Proxy Detected
```
Web App calls: check_ip_status(ip)
  → Calls IP2Location API
  → Checks: is_proxy, is_vpn, is_tor, is_data_center, etc.
```

If VPN detected:
```html
┌────────────────────────────────────────┐
│ We detected VPN / proxy on your        │
│ connection. Please turn it off and     │
│ apply again. We store minimal          │
│ information only for security and      │
│ abuse prevention.                      │
└────────────────────────────────────────┘
```

##### Outcome C: Blocked Country (e.g., Pakistan)
```html
┌────────────────────────────────────────┐
│ Sorry, you are not eligible for this   │
│ trial from your region (Pakistan).     │
│ We store minimal information only      │
│ for security and abuse-prevention.     │
│ You can request deletion at any time.  │
└────────────────────────────────────────┘
```

##### Outcome D: User Already Passed Step 1
```html
┌────────────────────────────────────────┐
│ ✅ You have already passed Step 1      │
│ verification!                          │
│                                        │
│ Please close this window and tap       │
│ 'Continue verification' button in      │
│ Telegram to proceed with Step 2.       │
└────────────────────────────────────────┘
(Mini App auto-closes after 3 seconds)
```

##### Outcome E: IP Check Passed → Show Form
```html
┌────────────────────────────────────────┐
│ Free Trial Verification                │
│                                        │
│ IP check passed. Please fill in your   │
│ name and country to continue.          │
│                                        │
│ Name: [________________]               │
│ Country: [Select your country ▼]       │
│ Email (optional): [________________]   │
│                                        │
│ ☐ I agree to receive future updates    │
│   and offers about this channel.       │
│                                        │
│ [Submit & continue]                    │
└────────────────────────────────────────┘
```

---

#### Step 3.2: User submits the form

**What happens internally:**
1. `trial()` route receives POST request
2. Extracts `tg_id` from form (JavaScript extracts from Telegram WebApp API)
3. Validates `tg_id` is a valid number
4. Re-checks VPN/country (security: prevent bypass)
5. Rate limit check per user (3 attempts/hour)
6. Sanitizes inputs (prevents XSS)
7. Validates email format if provided

**Possible Outcomes:**

##### Outcome A: Missing tg_id
```html
┌────────────────────────────────────────┐
│ Error: Could not verify your Telegram  │
│ account. Please make sure you opened   │
│ this page from Telegram and try again. │
└────────────────────────────────────────┘
```

##### Outcome B: Rate Limited (too many attempts)
```html
┌────────────────────────────────────────┐
│ Too many verification attempts.        │
│ Please wait 60 minutes before trying   │
│ again.                                 │
└────────────────────────────────────────┘
```

##### Outcome C: VPN Detected on POST
```
Same VPN message as before
(User might have turned VPN back on)
```

##### Outcome D: Missing Name or Country
```html
┌────────────────────────────────────────┐
│ Name and country are required.         │
│ Please fill the form again.            │
│                                        │
│ [Form shown again]                     │
└────────────────────────────────────────┘
```

##### Outcome E: Invalid Email Format
```html
┌────────────────────────────────────────┐
│ Invalid email format. Please enter     │
│ a valid email address or leave it      │
│ empty.                                 │
│                                        │
│ [Form shown again]                     │
└────────────────────────────────────────┘
```

##### Outcome F: Success! ✅
```
Data saved to pending_verifications.json:
{
  "123456789": {
    "name": "John Doe",
    "country": "United States",
    "email": "john@example.com",
    "ip": "203.0.113.45",
    "marketing_opt_in": true,
    "step1_ok": true,
    "status": "step1_passed",
    "created_at": "2025-12-03T19:30:00+00:00"
  }
}
```

```html
┌────────────────────────────────────────┐
│ Step 1 verification passed ✅          │
│                                        │
│ Please go back to Telegram and tap     │
│ 'Continue verification'. We only use   │
│ your data for verification, security   │
│ and (if you agreed) updates.           │
└────────────────────────────────────────┘
```

---

### Phase 4: Back to Bot - Phone Verification

#### Step 4.1: User clicks "✅ Continue verification"

**What happens internally:**
1. `continue_verification_callback()` triggered
2. Bot checks local storage for `tg_id`
3. If not found locally, calls web app API:
   ```
   GET {BASE_URL}/api/get-verification?tg_id=123456789
   Headers: X-API-Secret: {API_SECRET}
   ```
4. Checks if `step1_ok` is True

**Possible Outcomes:**

##### Outcome A: Verification data NOT found
```
Bot Response:
┌────────────────────────────────────────┐
│ We could not find your web             │
│ verification.                          │
│ Please tap 'Get Free Trial' again      │
│ and complete the web step first.       │
│                                        │
│ ⚠️ Make sure you:                      │
│ 1. Open the verification page          │
│ 2. Turn off VPN/Proxy                  │
│ 3. Fill in your details                │
│ 4. Submit the form                     │
│ 5. Close the mini-app                  │
│ 6. Then click 'Continue verification'  │
└────────────────────────────────────────┘
```

##### Outcome B: Verification data FOUND ✅
```
Bot Response:
┌────────────────────────────────────────┐
│ Step 1 passed ✅.                       │
│                                        │
│ Step 2: Please share your phone        │
│ number using the button below.         │
│                                        │
│ We use your name, country, and phone   │
│ number only for verification, security │
│ and internal analytics. We do not      │
│ sell or share this data. You can       │
│ request deletion at any time.          │
│                                        │
│ [📱 Share phone number]                │
└────────────────────────────────────────┘
```

---

#### Step 4.2: User shares phone number

**What happens internally:**
1. `contact_handler()` triggered
2. Validates contact belongs to same user (`contact.user_id == user.id`)
3. Checks phone country code against blocked list

**Possible Outcomes:**

##### Outcome A: Contact doesn't belong to user
```
Bot Response:
┌────────────────────────────────────────┐
│ Please share your own phone number     │
│ using the button.                      │
└────────────────────────────────────────┘
```

##### Outcome B: Contact not linked to Telegram account
```
Bot Response:
┌────────────────────────────────────────┐
│ Please share your phone number         │
│ directly from Telegram. The contact    │
│ must be linked to your Telegram        │
│ account.                               │
└────────────────────────────────────────┘
```

##### Outcome C: Blocked phone country code (e.g., +91 India)
```
Bot Response:
┌────────────────────────────────────────┐
│ You are not eligible for this trial    │
│ with this phone number.                │
│ We store minimal information only      │
│ for security and abuse-prevention.     │
│ You can request deletion at any time.  │
└────────────────────────────────────────┘

Data saved:
{
  "status": "blocked_phone_india",
  "phone": "+919876543210"
}
```

##### Outcome D: User already has valid invite link
```
Bot Response:
┌────────────────────────────────────────┐
│ You already generated a trial invite   │
│ link recently.                         │
│                                        │
│ Please use this link to join the       │
│ trial channel:                         │
│ https://t.me/+AbCdEfGhIjKlMnOp         │
│                                        │
│ If you have any issues accessing it,   │
│ use /support to contact us.            │
└────────────────────────────────────────┘
```

##### Outcome E: Success! Generate invite link ✅
```
Bot Response (1):
┌────────────────────────────────────────┐
│ Verification 2 passed ✅. Generating    │
│ your one-time invite link...           │
└────────────────────────────────────────┘

Bot Response (2):
┌────────────────────────────────────────┐
│ Here is your one-time invite link      │
│ to the private trial channel.          │
│ Please do not share it with others:    │
│ https://t.me/+AbCdEfGhIjKlMnOp         │
└────────────────────────────────────────┘

Data saved to invites.json:
{
  "123456789": {
    "invite_link": "https://t.me/+AbCdEfGhIjKlMnOp",
    "invite_created_at": "2025-12-03T19:35:00+00:00",
    "invite_expires_at": "2025-12-04T00:35:00+00:00"
  }
}

Data saved to trial_users.json:
{
  "tg_id": 123456789,
  "username": "johndoe",
  "name": "John Doe",
  "country": "United States",
  "phone": "+14155551234",
  "marketing_opt_in": true,
  "verification_completed_at": "2025-12-03T19:35:00+00:00"
}
```

---

### Phase 5: User Joins Trial Channel

#### Step 5.1: User clicks invite link and joins channel

**What happens internally:**
1. `trial_chat_member_update()` triggered by `ChatMemberHandler`
2. Detects: `old.status = "left"` → `new.status = "member"`
3. Checks if user already used trial

**Possible Outcomes:**

##### Outcome A: User already used trial (within 30 days)
```
Bot DM:
┌────────────────────────────────────────┐
│ You recently used a trial. Please      │
│ wait 30 days before requesting         │
│ another.                               │
│                                        │
│ 🎁 For more chances, you can join our  │
│ giveaway channel:                      │
│ https://t.me/Freya_Trades              │
│                                        │
│ 💬 Or DM @cogitosk to upgrade to the   │
│ premium signals.                       │
└────────────────────────────────────────┘

Action: User is immediately removed from channel
(ban then unban to kick without permanent ban)
```

##### Outcome B: User already used trial (no end date recorded)
```
Same message as above - blocked for safety
```

##### Outcome C: Trial already active and not expired
```
No action - user already has running trial
```

##### Outcome D: Success! Start new trial ✅

**Weekend check:**
```python
if today is Saturday or Sunday:
    trial_days = 5
    total_hours = 120
else:
    trial_days = 3
    total_hours = 72
```

```
Bot DM:
┌────────────────────────────────────────┐
│ ✅ Your 3-day (72 hours) trial phase   │
│ has started now!                       │
│                                        │
│ You will receive reminders as your     │
│ trial approaches the end.              │
└────────────────────────────────────────┘
(or 5-day/120 hours if weekend)

Data saved to active_trials.json:
{
  "123456789": {
    "join_time": "2025-12-03T19:40:00+00:00",
    "total_hours": 72,
    "trial_end_at": "2025-12-06T19:40:00+00:00"
  }
}

Scheduled jobs created:
- trial_reminder_3day_1 → runs at +24 hours
- trial_reminder_3day_2 → runs at +48 hours
- trial_end → runs at +72 hours
```

---

## ⏰ Scheduled Jobs & Reminders

### 3-Day Trial Schedule

| Time | Job | Message |
|------|-----|---------|
| +24h | `trial_reminder_3day_1` | "⏱ 1 day has passed, 2 days remaining in your trial." |
| +48h | `trial_reminder_3day_2` | "⏱ 2 days have passed. Only the last 24 hours left in your trial!" |
| +72h | `trial_end` | Trial ends, user removed |

### 5-Day Trial Schedule (Weekends)

| Time | Job | Message |
|------|-----|---------|
| +24h | `trial_reminder_5day_1` | "⏱ 1 day has passed, 4 days remaining in your 5-day trial." |
| +72h | `trial_reminder_5day_3` | "⏱ 3 days have passed, 2 days remaining in your 5-day trial." |
| +96h | `trial_reminder_5day_4` | "⏱ 4 days have passed. Only the last 24 hours left in your trial!" |
| +120h | `trial_end` | Trial ends, user removed |

### Trial End Process

When `trial_end()` job runs:
```
1. mark_trial_used(user_id, {"trial_ended_at": "..."})
2. clear_active_trial(user_id)
3. Send DM to user:
   ┌────────────────────────────────────────┐
   │ ⛔ Your trial has finished. If you     │
   │ enjoyed the signals, you can upgrade   │
   │ to a paid plan to continue.            │
   └────────────────────────────────────────┘
4. Remove from channel (ban → unban)
```

### Periodic Cleanup Job

Runs **every hour** as backup:
- Checks all active trials
- Validates trial data hasn't been tampered
- Ends any expired trials that scheduled jobs missed
- Logs: "✅ Periodic cleanup: Ended X expired trial(s)"

---

## 🚪 User Leaves Trial Channel Early

If user leaves channel before trial ends:

**What happens internally:**
1. `trial_chat_member_update()` detects leave
2. Checks if leave was caused by bot (trial_end cleanup) - if so, ignores
3. Computes remaining hours
4. Marks trial as used
5. Sends feedback request

```
Bot DM:
┌────────────────────────────────────────┐
│ We noticed you left the trial channel  │
│ before your trial finished.            │
│                                        │
│ You used about 18.5 hours of your      │
│ free trial and had about 53.5 hours    │
│ remaining.                             │
│                                        │
│ We hope you had a great time testing   │
│ our signals 🙌                         │
│ It would mean a lot if you could       │
│ share quick feedback here:             │
│ https://forms.gle/K7ubyn2tvzuYeHXn9    │
└────────────────────────────────────────┘

Data saved to used_trials.json:
{
  "123456789": {
    "left_early_at": "2025-12-04T14:10:00+00:00"
  }
}
```

---

## 🤖 Bot Commands Reference

| Command | Function | Response |
|---------|----------|----------|
| `/start` | Begin trial flow | Shows "Get Free Trial" button or "already used" message |
| `/help` | Show help | Detailed verification process explanation |
| `/faq` | FAQ | Answers to common questions (trial duration, data deletion, etc.) |
| `/about` | About bot | Brief description of the bot |
| `/support` | Get support | Link to support form |
| `/retry` | Re-show phone button | Shows phone sharing keyboard again |

### /help Response
```
🤖 *About This Bot*

This bot is used to manage users accessing premium content and services.

📋 *Available Commands:*
• /start - Start the bot and begin free trial
• /help - Help and commands list
• /faq - Frequently asked questions
• /about - About this bot
• /support - Contact support

🔐 *Verification Process:*

*Step 1: Initial Verification*
1. Click on /start command
2. A 'Get Free Trial' button will appear
3. Click on the button to open the verification page
4. Turn off VPN/Proxy before proceeding
5. IP test will happen automatically
6. Fill in your details:
   • Name (required)
   • Country (required)
   • Email (optional - you can delete later)
7. Close the Telegram mini-app

*Step 2: Phone Verification*
1. If Step 1 passed, click on 'Continue verification'
2. Click on 'Allow phone number access' button
3. Share your phone number when prompted
4. You will receive a one-time premium group invite link
5. Join the group to access premium content

✅ Once both verifications are complete, you'll gain access!
```

### /faq Response
```
❓ *Frequently Asked Questions*

1️⃣ *How many days can I use the free trial?*
   You can use the free trial for 3 days.
   (or 5 days on weekends)

2️⃣ *Can I delete my information later?*
   Yes, absolutely! You can request deletion at any time.

3️⃣ *Why do you need my phone number?*
   We need your phone number to verify you're real.

4️⃣ *What if I can't access the premium group?*
   Use /support command to contact our team.

5️⃣ *Can I share the invite link with others?*
   No, the invite link is one-time use and unique.

6️⃣ *What happens after my trial ends?*
   You'll need to upgrade to continue accessing content.
```

---

## 🌐 Web App API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Landing page |
| `/health` | GET | Health check (returns "OK") |
| `/trial` | GET | Show verification form |
| `/trial` | POST | Submit verification form |
| `/check-step1` | GET | Check if user passed step 1 (for JS) |
| `/api/get-verification` | GET | Bot fetches user verification data |
| `/debug-ip` | GET | Debug IP info (disabled by default) |

### API Authentication

`/api/get-verification` requires authentication if `API_SECRET` is set:

```
Header: X-API-Secret: your_secret_here
```

Returns:
```json
{
  "success": true,
  "data": {
    "name": "John Doe",
    "country": "United States",
    "step1_ok": true,
    ...
  }
}
```

---

## 🔒 Security Features

### 1. IP Rate Limiting
- Web verification: 5 requests/hour per IP
- API endpoint: 20 requests/15 minutes per IP

### 2. User Rate Limiting
- 3 verification attempts per user per hour

### 3. VPN/Proxy Detection
Checks via IP2Location API:
- `is_proxy`
- `is_vpn`
- `is_tor`
- `is_public_proxy`
- `is_web_proxy`
- `is_residential_proxy`
- `is_data_center`

### 4. Country Blocking
Configurable via `BLOCKED_COUNTRY_CODE` (e.g., "PK", "IN")

### 5. Phone Country Blocking
Configurable via `BLOCKED_PHONE_COUNTRY_CODE` (e.g., "+91")

### 6. Trial Data Tampering Detection
- Validates `join_time` + `total_hours` matches `trial_end_at`
- Tolerance: 1 hour (3600 seconds)
- Checks `total_hours` is valid (72 or 120 only)
- Checks `join_time` is not in future

### 7. Clock Manipulation Detection
- Tracks last time check
- Detects if system clock goes backwards
- Logs critical warning

### 8. Telegram initData Validation
- Validates HMAC signature using SHA256(BOT_TOKEN)
- Prevents impersonation of users

### 9. Input Sanitization
- HTML escaping to prevent XSS
- Max length limits on all inputs
- Email format validation

### 10. 30-Day Cooldown
- Users must wait 30 days between trials

---

## 💾 Data Storage

### File: `pending_verifications.json`
```json
{
  "123456789": {
    "name": "John Doe",
    "country": "United States",
    "email": "john@example.com",
    "ip": "203.0.113.45",
    "marketing_opt_in": true,
    "step1_ok": true,
    "status": "step1_passed",
    "created_at": "2025-12-03T19:30:00+00:00",
    "phone": "+14155551234",
    "verification_attempts": ["2025-12-03T19:29:00+00:00"]
  }
}
```

### File: `active_trials.json`
```json
{
  "123456789": {
    "join_time": "2025-12-03T19:40:00+00:00",
    "total_hours": 72,
    "trial_end_at": "2025-12-06T19:40:00+00:00"
  }
}
```

### File: `used_trials.json`
```json
{
  "123456789": {
    "trial_ended_at": "2025-12-06T19:40:00+00:00"
  }
}
```
or if left early:
```json
{
  "123456789": {
    "left_early_at": "2025-12-04T14:10:00+00:00"
  }
}
```

### File: `invites.json`
```json
{
  "123456789": {
    "invite_link": "https://t.me/+AbCdEfGhIjKlMnOp",
    "invite_created_at": "2025-12-03T19:35:00+00:00",
    "invite_expires_at": "2025-12-04T00:35:00+00:00"
  }
}
```

### File: `trial_users.json` (Log)
```json
[
  {
    "tg_id": 123456789,
    "username": "johndoe",
    "name": "John Doe",
    "country": "United States",
    "phone": "+14155551234",
    "marketing_opt_in": true,
    "verification_completed_at": "2025-12-03T19:35:00+00:00"
  },
  {
    "tg_id": 123456789,
    "username": "johndoe",
    "join_time": "2025-12-03T19:40:00+00:00",
    "trial_days": 3
  }
]
```

---

## ⚠️ Error Handling

### Bot Startup Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `BOT_TOKEN is required but not set` | Missing BOT_TOKEN | Add to .env file |
| `WARNING: TRIAL_CHANNEL_ID is not set` | Missing channel ID | Add to .env file |

### Runtime Errors

| Scenario | Handling |
|----------|----------|
| Failed to create invite link | Shows error message, doesn't crash |
| Failed to mark trial used | Logs warning, continues |
| Failed to remove user from channel | Silently continues (bot may not be admin) |
| API call to web app fails | Falls back to local storage check |
| Clock manipulation detected | Uses previous time + 1 second |

### Web App Errors

| Scenario | Response |
|----------|----------|
| IP2Location API fails | Fails open (doesn't block user) |
| JSON storage error | Returns error message to user |
| Invalid tg_id | Shows helpful error message |

---

## 🔧 Environment Variables

### Required
| Variable | Description | Example |
|----------|-------------|---------|
| `BOT_TOKEN` | Telegram bot token | `123456:ABC-DEF...` |
| `TRIAL_CHANNEL_ID` | Channel ID for trials | `-1001234567890` |
| `BASE_URL` | Web app URL | `https://trial.yourdomain.com` |

### Recommended
| Variable | Description | Default |
|----------|-------------|---------|
| `API_SECRET` | Secret for bot↔web auth | (empty) |
| `IP2LOCATION_API_KEY` | Primary API key | (empty) |
| `IP2LOCATION_API_KEY_2` | Backup API key | (empty) |

### Optional
| Variable | Description | Default |
|----------|-------------|---------|
| `BLOCKED_COUNTRY_CODE` | Country to block | `PK` |
| `BLOCKED_PHONE_COUNTRY_CODE` | Phone prefix to block | `+91` |
| `TIMEZONE_OFFSET_HOURS` | Offset for weekend check | `0` |
| `ENABLE_DEBUG_IP` | Enable /debug-ip | `0` |
| `GIVEAWAY_CHANNEL_URL` | Giveaway channel | `https://t.me/Freya_Trades` |
| `SUPPORT_CONTACT` | Support username | `@cogitosk` |
| `FEEDBACK_FORM_URL` | Feedback form | `https://forms.gle/...` |
| `SUPPORT_FORM_URL` | Support form | `https://forms.gle/...` |

---

## 📊 Complete Flow Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           COMPLETE USER FLOW                                  │
└──────────────────────────────────────────────────────────────────────────────┘

User: /start
    │
    ▼
┌─────────────────┐
│ Has used trial? │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
   YES        NO
    │         │
    ▼         ▼
[BLOCKED]   [Show "Get Free Trial" button]
                    │
                    ▼
            User clicks button
                    │
                    ▼
            [Open Web App]
                    │
                    ▼
         ┌──────────────────┐
         │ IP Rate Limited? │
         └────────┬─────────┘
                  │
             ┌────┴────┐
             │         │
            YES        NO
             │         │
             ▼         ▼
         [BLOCKED]  ┌──────────────┐
                    │ VPN Detected?│
                    └──────┬───────┘
                           │
                      ┌────┴────┐
                      │         │
                     YES        NO
                      │         │
                      ▼         ▼
                  [BLOCKED]  ┌─────────────────┐
                             │ Blocked Country?│
                             └────────┬────────┘
                                      │
                                 ┌────┴────┐
                                 │         │
                                YES        NO
                                 │         │
                                 ▼         ▼
                             [BLOCKED]  [Show Form]
                                              │
                                              ▼
                                        User submits
                                              │
                                              ▼
                                        [Save Data]
                                        step1_ok=true
                                              │
                                              ▼
                                   Back to Telegram Bot
                                              │
                                              ▼
                                   Click "Continue verification"
                                              │
                                              ▼
                                   ┌───────────────────┐
                                   │ Step1 data found? │
                                   └─────────┬─────────┘
                                             │
                                        ┌────┴────┐
                                        │         │
                                       NO        YES
                                        │         │
                                        ▼         ▼
                                   [Error msg]  [Request phone]
                                                      │
                                                      ▼
                                               User shares phone
                                                      │
                                                      ▼
                                        ┌─────────────────────┐
                                        │ Phone country OK?   │
                                        └──────────┬──────────┘
                                                   │
                                              ┌────┴────┐
                                              │         │
                                             NO        YES
                                              │         │
                                              ▼         ▼
                                         [BLOCKED]  [Generate invite]
                                                          │
                                                          ▼
                                                   User joins channel
                                                          │
                                                          ▼
                                              ┌───────────────────────┐
                                              │ Already used trial?   │
                                              └───────────┬───────────┘
                                                          │
                                                     ┌────┴────┐
                                                     │         │
                                                    YES        NO
                                                     │         │
                                                     ▼         ▼
                                               [KICKED]   [START TRIAL]
                                                               │
                                                               ▼
                                                    ┌──────────────────┐
                                                    │ Schedule:        │
                                                    │ - Reminders      │
                                                    │ - Trial end      │
                                                    └──────────────────┘
                                                               │
                                        ┌──────────────────────┼─────────────────────┐
                                        │                      │                     │
                                        ▼                      ▼                     ▼
                                 [User leaves early]    [Reminders sent]      [Trial ends]
                                        │                                           │
                                        ▼                                           ▼
                                 [Send feedback form]                        [Remove from channel]
                                 [Mark trial used]                           [Mark trial used]
```

---

## 🎯 Quick Reference Card

```
┌────────────────────────────────────────────────────────────────┐
│                    QUICK REFERENCE                              │
├────────────────────────────────────────────────────────────────┤
│ Trial Duration:        3 days (weekday) / 5 days (weekend)     │
│ Invite Link Expiry:    5 hours                                 │
│ Cooldown Period:       30 days                                 │
│ Rate Limit (IP):       5 requests/hour                         │
│ Rate Limit (User):     3 attempts/hour                         │
│ Tampering Tolerance:   1 hour (3600 seconds)                   │
├────────────────────────────────────────────────────────────────┤
│ Services:              web-app.service, telegram-bot.service   │
│ User:                  trialbot (non-root)                     │
│ Port:                  5000 (Flask) → Nginx → HTTPS            │
├────────────────────────────────────────────────────────────────┤
│ Storage Files:         pending_verifications.json              │
│                        active_trials.json                      │
│                        used_trials.json                        │
│                        invites.json                            │
│                        trial_users.json                        │
└────────────────────────────────────────────────────────────────┘
```

---

**Document created**: December 2025  
**System version**: 2.0 (Security Hardened)

