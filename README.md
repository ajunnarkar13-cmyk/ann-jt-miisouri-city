# Ann — Johnny Tamales Missouri City loyalty + referrals

Ann tracks **guest loyalty** (visits / points) and **who referred each guest**. Multiple marketers each get a unique code and shareable signup link. Built to sit beside **Toast** (CSV sync now; API hooks stubbed).

**Venue:** Johnny Tamales Missouri City · (281) 208-7464 · johnnytamalesmc.com  
JT-only branding (no other brands in this app).

## Run

```bash
cd /workspace/jeffe-jt/08-ann-loyalty
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
| `/staff` | FOH | Phone check-in (+points), quick enroll |
| `/lookup` | Anyone | Find guest by phone |
| `/admin` | Manager | Marketers, settings, Toast CSV import |

## Seed marketers (demo)

- **ALEX** — Alex Rivera  
- **JORDAN** — Jordan Lee  
- **SAM** — Sam Ortiz  

Share links like `/join/ALEX`.

## Points (defaults)

- 10 points per visit  
- +25 guest bonus on first visit if they came from a marketer  
- Change both under Admin → Settings  

## Toast

See [TOAST.md](TOAST.md). Short version: export Toast customers → Admin → Import Toast CSV (needs a Phone column). Staff can paste Toast check # on check-in.

## Data

SQLite file: `data/ann.db` (created on first run).
