# Ann — Johnny Tamales Missouri City loyalty + referrals

Ann tracks **guest loyalty** (visits / points) and **who referred each guest**. Multiple marketers each get a unique code and shareable signup link. Built to sit beside **Toast** (CSV sync now; API hooks stubbed).

**Venue:** Johnny Tamales Missouri City · (281) 208-7464 · johnnytamalesmc.com  
JT-only branding (no other brands in this app).

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open **http://127.0.0.1:5055**

Default **admin PIN:** `7464` (change in Admin → Settings).

## Screens

| Path | Who | What |
|------|-----|------|
| `/` | Everyone | Stats + marketer leaderboard |
| `/join` or `/join/ALEX` | Guests | Enroll; optional referral code |
| `/m/ALEX` | Marketers | Personal link + referral list |
| `/staff` | FOH | Eligible-guest list, redeem, phone check-in, quick enroll |
| `/lookup` | Anyone | Find guest by phone; reward badge when eligible |
| `/admin` | Manager | Eligible list, marketers, settings, Toast CSV import |

## Seed marketers (demo)

- **ALEX** — Alex Rivera  
- **JORDAN** — Jordan Lee  
- **SAM** — Sam Ortiz  

Share links like `/join/ALEX`.

## Points (defaults)

- 10 points per visit  
- +25 guest bonus on first visit if they came from a marketer  
- **100 points** earns a **free entrée**  
- Change these under Admin → Settings  

## Rewards

When a check-in (or any points change) brings a guest to the reward threshold and they are not already flagged for this redeem cycle, Ann marks them eligible:

- **Guest card:** “You're eligible — show staff for your free entrée.” (uses the configured reward name)
- **Lookup, Staff, and Admin:** an **Eligible for reward** badge, plus a list of everyone currently eligible
- **Staff → Redeem reward** deducts the threshold from their points and clears the flag so they can earn the next one. If they still have enough points, the next cycle starts immediately.

SMS uses Twilio when `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM` are all set. If any are missing, or Twilio returns an error, Ann keeps the in-app badge and does not crash. Optional: `REWARD_WEBHOOK_URL` (JSON POST) and `REWARD_NOTIFY_EMAIL` (log-only stub).

`REWARD_THRESHOLD` and `REWARD_NAME` seed those settings only when they are not in the database yet. After that, Admin → Settings is the source of truth. Guests who are already at the threshold on startup are flagged in the app without an SMS, so a restart does not text the list.

Schema columns `guests.reward_eligible` and `guests.reward_notified` are added automatically on startup.

## Toast

See [TOAST.md](TOAST.md). Short version: export Toast customers → Admin → Import Toast CSV (needs a Phone column). Staff can paste Toast check # on check-in.

## Data

SQLite file: `data/ann.db` (created on first run). Override the path with `ANN_DB`. Startup adds any missing reward columns and settings.

```bash
python3 -m unittest discover -s tests
```
