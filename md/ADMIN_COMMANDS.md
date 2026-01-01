# 🔐 Admin Commands Reference

## Setup

Add your Telegram ID to `.env`:
```env
ADMIN_TG_IDS=123456789
```
Multiple admins: `ADMIN_TG_IDS=123456789,987654321`

---

## 📢 Broadcasting

### `/broadcast <message>`
Send message to ALL users who clicked /start.

```
/broadcast 🎉 New signals available today!
```

**With button:**
```
/broadcast Check this out! [button:Join Now:https://t.me/+abc123]
```

**With media:** Reply to a photo/video and type:
```
/broadcast
```

---

### `/send <chat_ids> <message>`
Send to specific users (comma-separated IDs).

```
/send 123456789 Hello!
/send 123456789,987654321 Hey everyone!
```

---

## ⏰ Scheduled Broadcasts

### `/schedule <date> <time> <message>`
Schedule a future broadcast (UTC time).

```
/schedule 2026-01-05 10:00 Happy New Year! 🎉
```

### `/list_scheduled`
View all pending scheduled broadcasts.

### `/cancel <broadcast_id>`
Cancel a scheduled broadcast.

```
/cancel abc12345
```

---

## 📊 Statistics

### `/stats`
View bot statistics:
- Total /start clicks
- Verified users
- Active trials
- Used trials
- Banned users

### `/user <telegram_id>`
Lookup specific user info:
- Username
- Click count
- Trial status
- Ban status

```
/user 123456789
```

---

## 🚫 User Management

### `/ban <telegram_id> [reason]`
Ban a user from broadcasts and trials.

```
/ban 123456789 Spam
/ban 123456789
```

### `/unban <telegram_id>`
Remove ban from a user.

```
/unban 123456789
```

---

## 📦 Data Export

### `/export [type]`
Export data as JSON file.

| Type | Description |
|------|-------------|
| `all` | Everything (default) |
| `clicks` | /start users only |
| `trials` | Trial data |
| `verified` | Verification data |

```
/export all
/export clicks
```

---

## 🗑️ Message Management

### `/delete <chat_id> <message_id>`
Delete a sent message.

```
/delete 123456789 12345
```

---

## 💡 Button Syntax

Add inline buttons to any message:
```
[button:Label:https://url.com]
[button:Join:https://t.me/channel]
```

**Multiple buttons:**
```
/broadcast Join us! [button:Channel:https://t.me/channel] [button:Support:https://t.me/support]
```
