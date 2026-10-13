# 🎮 RK Gamers Bot (@rkgamersbot)

Telegram **Channel Management + Referral Rewards Bot** — files store karne ke liye server ki zaroorat nahi: bot sirf **message reference (file_id + msg_id)** save karta hai aur users ko **same file forward** karta hai. Fast, light, fully button-driven UI.

---

## ✨ Features

### 👑 Owner / Admin Panel (`/admin` — sab kuch buttons se)
| Feature | Detail |
|---|---|
| 🔘 **Reward Buttons** | Unlimited custom buttons (jaise "Get Free Key") — create/edit/delete/hide |
| 📦 **Content Manager** | Text, photo, video, document, audio, sticker — **multiple items ek sath**, Save/Cancel confirm ke sath |
| 🎯 **Milestones** | Set karo: 1 join pe kya milega, 2 join pe kya, 3 pe kya... — reward me **saved content / custom text / dono** de sakte ho |
| 📝 **Notebook** | Har button ka editable description — users ko button khulte hi dikhta hai |
| 🔗 **Direct Delivery** | Per-button toggle: referral zaroori hai ya content direct milega (owner control) |
| 📺 **Channels** | Add via forward/@username/ID — bot khud verify karta hai ki usko admin banaya gaya hai |
| 📢 **Broadcast** | Kisi bhi type ka content sab users ko — live progress ke sath |
| 👥 **Users** | List/search, profile, ban/unban |
| ⚙️ **Settings** | Force-Join channel gate, Strict anti-fraud toggle, New-user alerts |
| 📊 **Stats** | Users, joins (24h), rewards delivered, channels, buttons — sab live |

### 🙋 Users ke liye
- **100% button UI** — commands ki zaroorat nahi, sab inline keyboards se
- Dynamic reward buttons menu with **⬅️ ➡️ pagination** (arrows tabhi dikhte hain jab buttons zyada hon)
- **Get My Share Link** → unique deep-link → share karo (built-in Share button)
- Jaise hi koi link se aakar **channel join** karta hai → inviter ko notification + **automatic reward delivery**
- Profile page: invites, rewards claimed, join date

### 🛡️ Anti-Fraud System
- **Duplicate block**: wahi user dobara same inviter ke liye count nahi hoga — dono ko warning notification
- **Self-invite block**: apna link khud use karke reward nahi
- **Strict mode**: ek user ek reward button me sirf EK hi inviter ke liye kabhi count hoga
- **Leave detection**: counted user channel chhod de to count −1 ho jata hai (inviter ko alert)
- **Naye member only**: pehle se channel me member hai to count nahi hota
- Sab kuch DB me ledger (joins collection) me yaad rehta hai

---

## 🚀 Quick Start (Owner ke liye)

1. Bot ko **start** karo: [@rkgamersbot](https://t.me/rkgamersbot) → `/start`
2. `/admin` → **📺 Manage Channels** → ➕ Add Channel (channel se message forward karo)
3. Channel me **bot ko Admin** banao (Invite Users + Ban Users rights) → **🔄 Check Again** → ✅ Verified
4. `/admin` → **🔘 Manage Buttons** → ➕ New Button → naam bhejo (jaise `Get Free Key`)
5. Channel select karo → **content bhejo** (kuch bhi, multiple) → ✅ Save
6. 🎯 **Milestones** set karo (1 join = kya, 2 join = kya...) aur 📝 **Notebook** likho
7. 📢 Bot ka link users me share karo — done!

## ⏰ 24/7 Keep-Alive (UptimeRobot)

Render free service 15 min idle ke baad so jata hai. UptimeRobot se bachao:
1. [uptimerobot.com](https://uptimerobot.com) pe free account banao
2. **Add New Monitor** → Type: `HTTP(s)` → URL: `https://rk-gamers-bot.onrender.com/healthz` → Interval: 5 min
3. Bas — bot 24/7 jagta rahega 🚀

---

## 🛠️ Tech Stack

- **Python 3.11** + [python-telegram-bot 21](https://docs.python-telegram-bot.org/) (async, webhook mode)
- **MongoDB** (motor async driver) — users, channels, buttons, invites, joins, milestones
- **Render Web Service** — webhook + `/healthz` health endpoint
- Content **kabhi re-upload nahi hota** — sirf Telegram message references store hote hain (file_id + chat_id + msg_id)

## 📁 Project Structure

```
rk-gamers-bot/
├── bot.py                  # entry point (webhook/polling)
├── config.py               # env config
├── database/db.py          # motor MongoDB layer
├── handlers/
│   ├── start.py            # /start, deep-link referral, force-sub gate, main menu
│   ├── user.py             # reward buttons, share links, join verify, rewards
│   ├── admin.py            # panel, stats, broadcast, users, settings
│   ├── mng_buttons.py      # reward buttons CRUD + milestones + content
│   ├── mng_channels.py     # channel CRUD + verification
│   ├── members.py          # chat_member join/leave detection
│   └── fsm.py              # message state machine (inputs)
├── utils/                  # keyboards (pagination), helpers
└── tests/test_bot.py       # end-to-end tests
```

## ⚙️ Environment Variables

| Var | Required | Example |
|---|---|---|
| `BOT_TOKEN` | ✅ | `8804079285:AAE...` |
| `OWNER_ID` | ✅ | `8967004608` |
| `MONGO_URI` | ✅ | `mongodb+srv://user:pass@cluster0...` |
| `DB_NAME` | — | `rk_gamers_bot` |
| `BOT_USERNAME` | — | `rkgamersbot` |
| `WEBHOOK_SECRET` | — | random string (webhook path) |
| `ADMINS` | — | comma separated extra admin IDs |

> ⚠️ **MongoDB Atlas**: Render ke IPs dynamic hote hain — Atlas **Network Access** me `0.0.0.0/0` (Allow All) add karna zaroori hai.

## 🧪 Tests

```bash
pip install -r requirements.txt
export BOT_TOKEN=... OWNER_ID=... MONGO_URI=...
python tests/test_bot.py
```

Covers: config, Mongo CRUD, Telegram getMe, /start flow, referral join, duplicate block, leave detection, cleanup — **14 checks**.

## 🔒 Security Notes

- Saare secrets **environment variables** me hain — repo me koi token nahi
- Bot token kabhi share/commit na karo; leak ho to @BotFather se `/revoke` karke naya token Render env me update kar do
