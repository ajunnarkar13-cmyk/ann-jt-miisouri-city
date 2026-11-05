"""Ann — Johnny Tamales Missouri City loyalty + referral tracker."""
from __future__ import annotations

import csv
import io
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "data" / "ann.db"
DEFAULT_ADMIN_PIN = "7464"  # JT phone last-4 — change in Settings
POINTS_PER_VISIT = 10
REFERRAL_BONUS = 25

app = Flask(__name__)
app.secret_key = os.environ.get("ANN_SECRET", "jt-ann-dev-change-me")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def format_phone(digits: str) -> str:
    d = normalize_phone(digits)
    if len(d) == 10:
        return f"({d[:3]}) {d[3:6]}-{d[6:]}"
    return digits or ""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def setting(key: str, default: str = "") -> str:
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS marketers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            phone TEXT,
            email TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS guests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE,
            email TEXT,
            toast_guid TEXT,
            referred_by_marketer_id INTEGER,
            points INTEGER NOT NULL DEFAULT 0,
            visit_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (referred_by_marketer_id) REFERENCES marketers(id)
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guest_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            points_delta INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            toast_order_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (guest_id) REFERENCES guests(id)
        );
        """
    )
    cur = db.execute("SELECT COUNT(*) FROM settings")
    if cur.fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?)",
            [
                ("admin_pin", DEFAULT_ADMIN_PIN),
                ("points_per_visit", str(POINTS_PER_VISIT)),
                ("referral_bonus", str(REFERRAL_BONUS)),
                ("venue_name", "Johnny Tamales Missouri City"),
                ("venue_phone", "(281) 208-7464"),
                ("venue_web", "johnnytamalesmc.com"),
            ],
        )
    cur = db.execute("SELECT COUNT(*) FROM marketers")
    if cur.fetchone()[0] == 0:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        seeds = [
            ("Alex Rivera", "ALEX", "2815550101", "alex@example.com"),
            ("Jordan Lee", "JORDAN", "2815550102", "jordan@example.com"),
            ("Sam Ortiz", "SAM", "2815550103", "sam@example.com"),
        ]
        for name, code, phone, email in seeds:
            db.execute(
                "INSERT INTO marketers(name, code, phone, email, created_at) VALUES(?,?,?,?,?)",
                (name, code, phone, email, now),
            )
        # sample guests
        db.execute(
            "INSERT INTO guests(name, phone, email, referred_by_marketer_id, points, visit_count, created_at) VALUES(?,?,?,?,?,?,?)",
            ("Maria Gomez", "8325551001", "maria@example.com", 1, 20, 2, now),
        )
        db.execute(
            "INSERT INTO guests(name, phone, email, referred_by_marketer_id, points, visit_count, created_at) VALUES(?,?,?,?,?,?,?)",
            ("Chris Patel", "8325551002", None, 2, 10, 1, now),
        )
        db.execute(
            "INSERT INTO guests(name, phone, email, referred_by_marketer_id, points, visit_count, created_at) VALUES(?,?,?,?,?,?,?)",
            ("Taylor Nguyen", "8325551003", "taylor@example.com", None, 0, 0, now),
        )
        db.execute(
            "INSERT INTO events(guest_id, kind, points_delta, note, created_at) VALUES(?,?,?,?,?)",
            (1, "visit", 10, "Seed visit", now),
        )
        db.execute(
            "INSERT INTO events(guest_id, kind, points_delta, note, created_at) VALUES(?,?,?,?,?)",
            (1, "visit", 10, "Seed visit", now),
        )
        db.execute(
            "INSERT INTO events(guest_id, kind, points_delta, note, created_at) VALUES(?,?,?,?,?)",
            (1, "referral_bonus", 25, "Bonus credited to marketer ALEX on Maria first visit (tracked on guest)", now),
        )
        db.execute(
            "INSERT INTO events(guest_id, kind, points_delta, note, created_at) VALUES(?,?,?,?,?)",
            (2, "visit", 10, "Seed visit", now),
        )
    db.commit()
    db.close()


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def marketer_stats(db: sqlite3.Connection):
    return db.execute(
        """
        SELECT m.*,
               (SELECT COUNT(*) FROM guests g WHERE g.referred_by_marketer_id = m.id) AS referrals,
               (SELECT COALESCE(SUM(g.visit_count), 0) FROM guests g WHERE g.referred_by_marketer_id = m.id) AS attributed_visits
        FROM marketers m
        WHERE m.active = 1
        ORDER BY referrals DESC, attributed_visits DESC, m.name
        """
    ).fetchall()


@app.context_processor
def inject_brand():
    return {
        "venue_name": setting("venue_name", "Johnny Tamales Missouri City"),
        "venue_phone": setting("venue_phone", "(281) 208-7464"),
        "venue_web": setting("venue_web", "johnnytamalesmc.com"),
        "format_phone": format_phone,
        "is_admin": bool(session.get("admin")),
    }


@app.route("/")
def home():
    db = get_db()
    stats = {
        "guests": db.execute("SELECT COUNT(*) c FROM guests").fetchone()["c"],
        "marketers": db.execute("SELECT COUNT(*) c FROM marketers WHERE active=1").fetchone()["c"],
        "visits": db.execute("SELECT COALESCE(SUM(visit_count),0) c FROM guests").fetchone()["c"],
        "points": db.execute("SELECT COALESCE(SUM(points),0) c FROM guests").fetchone()["c"],
    }
    leaderboard = marketer_stats(db)[:5]
    return render_template("home.html", stats=stats, leaderboard=leaderboard)


@app.route("/join")
@app.route("/join/<code>")
def join(code: str | None = None):
    code = (code or request.args.get("ref") or "").strip().upper()
    marketer = None
    if code:
        marketer = (
            get_db()
            .execute("SELECT * FROM marketers WHERE upper(code)=? AND active=1", (code,))
            .fetchone()
        )
    return render_template("join.html", code=code, marketer=marketer)


@app.route("/join", methods=["POST"])
@app.route("/join/<code>", methods=["POST"])
def join_post(code: str | None = None):
    name = (request.form.get("name") or "").strip()
    phone = normalize_phone(request.form.get("phone") or "")
    email = (request.form.get("email") or "").strip() or None
    ref = (request.form.get("ref_code") or code or "").strip().upper()
    if not name or len(phone) != 10:
        flash("Name and a valid 10-digit US phone are required.", "error")
        return redirect(url_for("join", code=ref or None))
    db = get_db()
    existing = db.execute("SELECT id FROM guests WHERE phone=?", (phone,)).fetchone()
    if existing:
        flash("You’re already on the list — ask staff to check you in by phone.", "info")
        return redirect(url_for("guest_lookup", phone=phone))
    marketer_id = None
    if ref:
        m = db.execute("SELECT id FROM marketers WHERE upper(code)=? AND active=1", (ref,)).fetchone()
        if m:
            marketer_id = m["id"]
    now = utcnow()
    cur = db.execute(
        "INSERT INTO guests(name, phone, email, referred_by_marketer_id, created_at) VALUES(?,?,?,?,?)",
        (name, phone, email, marketer_id, now),
    )
    guest_id = cur.lastrowid
    db.execute(
        "INSERT INTO events(guest_id, kind, points_delta, note, created_at) VALUES(?,?,?,?,?)",
        (guest_id, "enroll", 0, f"Joined via {ref or 'direct'}", now),
    )
    db.commit()
    flash(f"Welcome to Ann, {name.split()[0]}! Show this phone at Johnny Tamales to earn points.", "ok")
    return redirect(url_for("guest_card", guest_id=guest_id))


@app.route("/guest/<int:guest_id>")
def guest_card(guest_id: int):
    db = get_db()
    guest = db.execute(
        """
        SELECT g.*, m.name AS marketer_name, m.code AS marketer_code
        FROM guests g
        LEFT JOIN marketers m ON m.id = g.referred_by_marketer_id
        WHERE g.id = ?
        """,
        (guest_id,),
    ).fetchone()
    if not guest:
        flash("Guest not found.", "error")
        return redirect(url_for("home"))
    events = db.execute(
        "SELECT * FROM events WHERE guest_id=? ORDER BY id DESC LIMIT 20", (guest_id,)
    ).fetchall()
    return render_template("guest.html", guest=guest, events=events)


@app.route("/lookup")
def guest_lookup():
    phone = normalize_phone(request.args.get("phone") or "")
    guest = None
    if phone:
        guest = get_db().execute("SELECT * FROM guests WHERE phone=?", (phone,)).fetchone()
        if guest:
            return redirect(url_for("guest_card", guest_id=guest["id"]))
        flash("No guest with that phone yet — they can join at /join.", "info")
    return render_template("lookup.html", phone=phone)


@app.route("/staff")
def staff():
    return render_template("staff.html")


@app.route("/staff/checkin", methods=["POST"])
def staff_checkin():
    phone = normalize_phone(request.form.get("phone") or "")
    note = (request.form.get("note") or "").strip() or "Visit"
    toast_order = (request.form.get("toast_order_id") or "").strip() or None
    if len(phone) != 10:
        flash("Enter a 10-digit phone.", "error")
        return redirect(url_for("staff"))
    db = get_db()
    guest = db.execute("SELECT * FROM guests WHERE phone=?", (phone,)).fetchone()
    if not guest:
        flash("Guest not enrolled. Send them to /join or enroll below.", "error")
        return redirect(url_for("staff"))
    ppv = int(setting("points_per_visit", str(POINTS_PER_VISIT)))
    bonus = int(setting("referral_bonus", str(REFERRAL_BONUS)))
    now = utcnow()
    first_visit = guest["visit_count"] == 0
    db.execute(
        "UPDATE guests SET points = points + ?, visit_count = visit_count + 1 WHERE id=?",
        (ppv, guest["id"]),
    )
    db.execute(
        "INSERT INTO events(guest_id, kind, points_delta, note, toast_order_id, created_at) VALUES(?,?,?,?,?,?)",
        (guest["id"], "visit", ppv, note, toast_order, now),
    )
    if first_visit and guest["referred_by_marketer_id"]:
        # credit referral bonus onto the guest ledger (marketer gets leaderboard credit via attributed visits)
        db.execute(
            "UPDATE guests SET points = points + ? WHERE id=?",
            (bonus, guest["id"]),
        )
        db.execute(
            "INSERT INTO events(guest_id, kind, points_delta, note, created_at) VALUES(?,?,?,?,?)",
            (
                guest["id"],
                "referral_bonus",
                bonus,
                "First-visit referral thank-you",
                now,
            ),
        )
    db.commit()
    flash(f"Checked in {guest['name']} (+{ppv} pts).", "ok")
    return redirect(url_for("guest_card", guest_id=guest["id"]))


@app.route("/staff/enroll", methods=["POST"])
def staff_enroll():
    name = (request.form.get("name") or "").strip()
    phone = normalize_phone(request.form.get("phone") or "")
    ref = (request.form.get("ref_code") or "").strip().upper()
    if not name or len(phone) != 10:
        flash("Name + valid phone required.", "error")
        return redirect(url_for("staff"))
    db = get_db()
    if db.execute("SELECT id FROM guests WHERE phone=?", (phone,)).fetchone():
        flash("Already enrolled — use check-in.", "info")
        return redirect(url_for("staff"))
    marketer_id = None
    if ref:
        m = db.execute("SELECT id FROM marketers WHERE upper(code)=? AND active=1", (ref,)).fetchone()
        if m:
            marketer_id = m["id"]
    now = utcnow()
    cur = db.execute(
        "INSERT INTO guests(name, phone, referred_by_marketer_id, created_at) VALUES(?,?,?,?)",
        (name, phone, marketer_id, now),
    )
    db.execute(
        "INSERT INTO events(guest_id, kind, points_delta, note, created_at) VALUES(?,?,?,?,?)",
        (cur.lastrowid, "enroll", 0, f"Staff enroll / {ref or 'direct'}", now),
    )
    db.commit()
    flash(f"Enrolled {name}.", "ok")
    return redirect(url_for("guest_card", guest_id=cur.lastrowid))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        pin = (request.form.get("pin") or "").strip()
        if pin == setting("admin_pin", DEFAULT_ADMIN_PIN):
            session["admin"] = True
            flash("Admin unlocked.", "ok")
            nxt = request.args.get("next") or url_for("admin")
            return redirect(nxt)
        flash("Wrong PIN.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    flash("Logged out.", "info")
    return redirect(url_for("home"))


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    return render_template(
        "admin.html",
        marketers=marketer_stats(db),
        guests=db.execute(
            """
            SELECT g.*, m.code AS marketer_code
            FROM guests g LEFT JOIN marketers m ON m.id = g.referred_by_marketer_id
            ORDER BY g.id DESC LIMIT 50
            """
        ).fetchall(),
        pin=setting("admin_pin", DEFAULT_ADMIN_PIN),
        points_per_visit=setting("points_per_visit", str(POINTS_PER_VISIT)),
        referral_bonus=setting("referral_bonus", str(REFERRAL_BONUS)),
    )


@app.route("/admin/marketers", methods=["POST"])
@admin_required
def admin_add_marketer():
    name = (request.form.get("name") or "").strip()
    code = (request.form.get("code") or "").strip().upper()
    phone = normalize_phone(request.form.get("phone") or "") or None
    email = (request.form.get("email") or "").strip() or None
    if not name or not code:
        flash("Name and code required.", "error")
        return redirect(url_for("admin"))
    db = get_db()
    try:
        db.execute(
            "INSERT INTO marketers(name, code, phone, email, created_at) VALUES(?,?,?,?,?)",
            (name, code, phone, email, utcnow()),
        )
        db.commit()
        flash(f"Marketer {name} ({code}) added.", "ok")
    except sqlite3.IntegrityError:
        flash("That code is already taken.", "error")
    return redirect(url_for("admin"))


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    pin = (request.form.get("admin_pin") or "").strip()
    ppv = (request.form.get("points_per_visit") or "").strip()
    bonus = (request.form.get("referral_bonus") or "").strip()
    if pin:
        set_setting("admin_pin", pin)
    if ppv.isdigit():
        set_setting("points_per_visit", ppv)
    if bonus.isdigit():
        set_setting("referral_bonus", bonus)
    flash("Settings saved.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/import-toast", methods=["POST"])
@admin_required
def admin_import_toast():
    f = request.files.get("csv")
    if not f or not f.filename:
        flash("Choose a CSV file.", "error")
        return redirect(url_for("admin"))
    text = f.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        flash("Empty CSV.", "error")
        return redirect(url_for("admin"))
    # flexible column map
    fields = {h.lower().strip(): h for h in reader.fieldnames}

    def col(*names):
        for n in names:
            if n in fields:
                return fields[n]
        return None

    c_name = col("name", "customer name", "guest name", "full name")
    c_phone = col("phone", "mobile phone", "phone number", "customer phone", "mobile")
    c_email = col("email", "email address", "customer email")
    c_guid = col("guid", "customer guid", "toast guid", "customer id")
    if not c_phone:
        flash("CSV needs a Phone column (Toast customer export).", "error")
        return redirect(url_for("admin"))
    db = get_db()
    added = updated = 0
    now = utcnow()
    for row in reader:
        phone = normalize_phone(row.get(c_phone) or "")
        if len(phone) != 10:
            continue
        name = (row.get(c_name) or "").strip() or format_phone(phone)
        email = (row.get(c_email) or "").strip() or None if c_email else None
        guid = (row.get(c_guid) or "").strip() or None if c_guid else None
        existing = db.execute("SELECT id FROM guests WHERE phone=?", (phone,)).fetchone()
        if existing:
            db.execute(
                "UPDATE guests SET name=COALESCE(NULLIF(?, ''), name), email=COALESCE(?, email), toast_guid=COALESCE(?, toast_guid) WHERE id=?",
                (name, email, guid, existing["id"]),
            )
            updated += 1
        else:
            cur = db.execute(
                "INSERT INTO guests(name, phone, email, toast_guid, created_at) VALUES(?,?,?,?,?)",
                (name, phone, email, guid, now),
            )
            db.execute(
                "INSERT INTO events(guest_id, kind, points_delta, note, created_at) VALUES(?,?,?,?,?)",
                (cur.lastrowid, "toast_import", 0, "Imported from Toast CSV", now),
            )
            added += 1
    db.commit()
    flash(f"Toast import: {added} new, {updated} updated.", "ok")
    return redirect(url_for("admin"))


@app.route("/m/<code>")
def marketer_page(code: str):
    db = get_db()
    m = db.execute("SELECT * FROM marketers WHERE upper(code)=? AND active=1", (code.upper(),)).fetchone()
    if not m:
        flash("Unknown marketer code.", "error")
        return redirect(url_for("home"))
    referrals = db.execute(
        "SELECT * FROM guests WHERE referred_by_marketer_id=? ORDER BY id DESC",
        (m["id"],),
    ).fetchall()
    link = url_for("join", code=m["code"], _external=True)
    return render_template("marketer.html", marketer=m, referrals=referrals, link=link)


@app.route("/api/health")
def health():
    return {"ok": True, "app": "Ann", "venue": "Johnny Tamales Missouri City"}


# Stub hooks for future Toast Partner API
@app.route("/api/toast/webhook", methods=["POST"])
def toast_webhook_stub():
    """Placeholder: future Toast order webhook → auto check-in by phone."""
    return {"accepted": False, "message": "Toast webhook not configured yet — use staff check-in or CSV import."}, 501


init_db()  # gunicorn / import-time bootstrap

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "5055"))
    print(f"Ann running → http://127.0.0.1:{port}  (admin PIN default: {DEFAULT_ADMIN_PIN})")
    app.run(host="0.0.0.0", port=port, debug=True)
