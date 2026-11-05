"""Reward eligibility: threshold, in-app alerts, redeem, and optional SMS."""
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

_DB_DIR = tempfile.mkdtemp(prefix="ann-rewards-")
os.environ["ANN_DB"] = str(Path(_DB_DIR) / "ann.db")
for _key in (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM",
    "REWARD_WEBHOOK_URL",
    "REWARD_NOTIFY_EMAIL",
    "REWARD_THRESHOLD",
    "REWARD_NAME",
):
    os.environ.pop(_key, None)

import app  # noqa: E402

ELIGIBLE = "You're eligible — show staff for your free entrée."
ENV_KEYS = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM",
    "REWARD_WEBHOOK_URL",
    "REWARD_NOTIFY_EMAIL",
    "REWARD_THRESHOLD",
    "REWARD_NAME",
)


class _Resp:
    def __init__(self, status=201):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"


def connect():
    db = sqlite3.connect(app.DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def fetch_guest(phone):
    db = connect()
    row = db.execute("SELECT * FROM guests WHERE phone = ?", (phone,)).fetchone()
    db.close()
    return row


def events_for(phone, kind=None):
    db = connect()
    sql = """
        SELECT e.* FROM events e
        JOIN guests g ON g.id = e.guest_id
        WHERE g.phone = ?
    """
    args = [phone]
    if kind:
        sql += " AND e.kind = ?"
        args.append(kind)
    sql += " ORDER BY e.id"
    rows = db.execute(sql, args).fetchall()
    db.close()
    return rows


class RewardTests(unittest.TestCase):
    def setUp(self):
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        path = Path(app.DB_PATH)
        if path.exists():
            path.unlink()
        app.init_db()
        self.client = app.app.test_client()

    def settings(self, **overrides):
        with self.client.session_transaction() as sess:
            sess["admin"] = True
        data = {
            "admin_pin": "7464",
            "points_per_visit": "10",
            "referral_bonus": "25",
            "reward_threshold": "100",
            "reward_name": "free entrée",
        }
        data.update({k: v for k, v in overrides.items() if v is not None})
        return self.client.post("/admin/settings", data=data, follow_redirects=True)

    def test_defaults_and_schema(self):
        app.init_db()
        db = connect()
        cols = {row["name"] for row in db.execute("PRAGMA table_info(guests)")}
        self.assertIn("reward_eligible", cols)
        self.assertIn("reward_notified", cols)
        threshold = db.execute(
            "SELECT value FROM settings WHERE key = 'reward_threshold'"
        ).fetchone()["value"]
        name = db.execute(
            "SELECT value FROM settings WHERE key = 'reward_name'"
        ).fetchone()["value"]
        db.close()
        self.assertEqual(threshold, "100")
        self.assertEqual(name, "free entrée")
        self.assertEqual(fetch_guest("8325551001")["reward_eligible"], 0)

    def test_env_seeds_threshold_without_overwriting_admin(self):
        os.environ["REWARD_THRESHOLD"] = "80"
        os.environ["REWARD_NAME"] = "free tamal"
        try:
            Path(app.DB_PATH).unlink()
            app.init_db()
            db = connect()
            self.assertEqual(
                db.execute(
                    "SELECT value FROM settings WHERE key = 'reward_threshold'"
                ).fetchone()["value"],
                "80",
            )
            self.assertEqual(
                db.execute(
                    "SELECT value FROM settings WHERE key = 'reward_name'"
                ).fetchone()["value"],
                "free tamal",
            )
            db.close()
            os.environ["REWARD_THRESHOLD"] = "15"
            os.environ["REWARD_NAME"] = "something else"
            app.init_db()
            db = connect()
            self.assertEqual(
                db.execute(
                    "SELECT value FROM settings WHERE key = 'reward_threshold'"
                ).fetchone()["value"],
                "80",
            )
            db.close()
        finally:
            os.environ.pop("REWARD_THRESHOLD", None)
            os.environ.pop("REWARD_NAME", None)

    def test_legacy_migration_flags_without_sms(self):
        legacy = Path(tempfile.mkdtemp(prefix="ann-legacy-")) / "legacy.db"
        db = sqlite3.connect(legacy)
        db.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE marketers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE,
                phone TEXT,
                email TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE guests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                email TEXT,
                toast_guid TEXT,
                referred_by_marketer_id INTEGER,
                points INTEGER NOT NULL DEFAULT 0,
                visit_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guest_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                points_delta INTEGER NOT NULL DEFAULT 0,
                note TEXT,
                toast_order_id TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        db.execute("INSERT INTO settings(key, value) VALUES('admin_pin', '7464')")
        db.execute(
            "INSERT INTO marketers(name, code, created_at) VALUES('Legacy', 'LEGACY', '2020-01-01 00:00:00')"
        )
        db.execute(
            "INSERT INTO guests(name, phone, points, visit_count, created_at) VALUES(?,?,?,?,?)",
            ("Old Guest", "8325551999", 150, 15, "2020-01-01 00:00:00"),
        )
        db.execute(
            "INSERT INTO guests(name, phone, points, visit_count, created_at) VALUES(?,?,?,?,?)",
            ("Low Guest", "8325551888", 10, 1, "2020-01-01 00:00:00"),
        )
        db.commit()
        db.close()

        os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
        os.environ["TWILIO_AUTH_TOKEN"] = "secret-token"
        os.environ["TWILIO_FROM"] = "+15555550100"
        os.environ["REWARD_WEBHOOK_URL"] = "https://example.test/hook"
        original = app.DB_PATH
        app.DB_PATH = legacy
        try:
            with mock.patch("urllib.request.urlopen") as urlopen:
                app.init_db()
                app.init_db()
            self.assertEqual(urlopen.call_count, 0)
            db = sqlite3.connect(legacy)
            db.row_factory = sqlite3.Row
            cols = {row["name"] for row in db.execute("PRAGMA table_info(guests)")}
            self.assertIn("reward_eligible", cols)
            self.assertIn("reward_notified", cols)
            old = db.execute(
                "SELECT * FROM guests WHERE phone = '8325551999'"
            ).fetchone()
            low = db.execute(
                "SELECT * FROM guests WHERE phone = '8325551888'"
            ).fetchone()
            self.assertEqual(old["reward_eligible"], 1)
            self.assertEqual(old["reward_notified"], 1)
            self.assertEqual(low["reward_eligible"], 0)
            notes = db.execute(
                "SELECT note FROM events WHERE kind = 'reward_eligible'"
            ).fetchall()
            self.assertEqual(len(notes), 1)
            self.assertIn("via=startup", notes[0]["note"])
            self.assertIn("sms=skipped", notes[0]["note"])
            threshold = db.execute(
                "SELECT value FROM settings WHERE key = 'reward_threshold'"
            ).fetchone()["value"]
            self.assertEqual(threshold, "100")
            db.close()
        finally:
            app.DB_PATH = original
            for key in ENV_KEYS:
                os.environ.pop(key, None)

    def test_checkin_below_threshold_is_not_eligible(self):
        rv = self.client.post(
            "/staff/checkin",
            data={"phone": "8325551003"},
            follow_redirects=True,
        )
        text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 200)
        self.assertIn("Checked in Taylor Nguyen (+10 pts).", text)
        self.assertNotIn("Eligible for reward", text)
        self.assertIn("10 / 100 points toward a free entrée.", text)
        guest = fetch_guest("8325551003")
        self.assertEqual(guest["points"], 10)
        self.assertEqual(guest["reward_eligible"], 0)
        self.assertEqual(events_for("8325551003", "reward_eligible"), [])

    def test_lowering_threshold_flags_and_raising_clears(self):
        self.settings(reward_threshold="10")
        self.assertEqual(fetch_guest("8325551001")["reward_eligible"], 1)
        self.assertEqual(fetch_guest("8325551002")["reward_eligible"], 1)
        self.assertEqual(fetch_guest("8325551003")["reward_eligible"], 0)
        note = events_for("8325551001", "reward_eligible")[-1]["note"]
        self.assertIn("via=settings", note)
        self.assertIn("sms=skipped", note)
        self.settings(reward_threshold="100")
        self.assertEqual(fetch_guest("8325551001")["reward_eligible"], 0)
        self.assertEqual(fetch_guest("8325551001")["reward_notified"], 0)

    def test_guest_lookup_staff_and_admin_show_badge(self):
        self.settings(reward_threshold="10", reward_name="free entrée")
        lookup = self.client.get("/lookup", query_string={"phone": "(832) 555-1001"})
        lookup_text = lookup.get_data(as_text=True)
        self.assertIn("Eligible for reward", lookup_text)
        self.assertIn(ELIGIBLE, lookup_text)
        self.assertIn("Maria Gomez", lookup_text)

        staff = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("Eligible for reward", staff)
        self.assertIn("Maria Gomez", staff)
        self.assertIn("Chris Patel", staff)
        self.assertNotIn("Taylor Nguyen", staff)
        self.assertIn("Redeem reward", staff)

        with self.client.session_transaction() as sess:
            sess["admin"] = True
        admin = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Eligible for reward", admin)
        self.assertIn("Maria Gomez", admin)

        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("Eligible now", home)
        self.assertIn(">2</b><span>Eligible now", home)

        card = self.client.get("/guest/1").get_data(as_text=True)
        self.assertIn(ELIGIBLE, card)
        self.assertIn("Eligible for reward", card)
        self.assertNotIn("sms=", card)
        self.assertNotIn("webhook=", card)

    def test_empty_eligible_list(self):
        text = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("No guests are eligible right now.", text)

    def test_admin_rejects_bad_threshold_and_blank_name(self):
        self.settings(reward_threshold="40", reward_name="free tamal plate")
        self.settings(reward_threshold="0", reward_name="   ")
        self.settings(reward_threshold="nope", reward_name="free tamal plate")
        db = connect()
        threshold = db.execute(
            "SELECT value FROM settings WHERE key = 'reward_threshold'"
        ).fetchone()["value"]
        name = db.execute(
            "SELECT value FROM settings WHERE key = 'reward_name'"
        ).fetchone()["value"]
        db.close()
        self.assertEqual(threshold, "40")
        self.assertEqual(name, "free tamal plate")

    def test_custom_reward_name_on_guest_page(self):
        self.settings(reward_threshold="10", reward_name="free tamal plate")
        text = self.client.get("/guest/1").get_data(as_text=True)
        self.assertIn("You're eligible — show staff for your free tamal plate.", text)

    def test_checkin_sends_one_sms_and_does_not_repeat(self):
        self.settings(reward_threshold="10")
        self.client.post(
            "/staff/enroll",
            data={"name": "Casey Kim", "phone": "2815550177"},
        )
        os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
        os.environ["TWILIO_AUTH_TOKEN"] = "secret-token"
        os.environ["TWILIO_FROM"] = "+15555550100"
        try:
            with mock.patch("urllib.request.urlopen", return_value=_Resp()) as urlopen:
                first = self.client.post(
                    "/staff/checkin",
                    data={"phone": "2815550177"},
                    follow_redirects=True,
                )
                second = self.client.post(
                    "/staff/checkin",
                    data={"phone": "2815550177"},
                    follow_redirects=True,
                )
            self.assertEqual(urlopen.call_count, 1)
            req = urlopen.call_args[0][0]
            self.assertEqual(req.get_method(), "POST")
            self.assertIn("/Accounts/ACtest/Messages.json", req.full_url)
            parsed = urllib.parse.parse_qs(req.data.decode())
            self.assertEqual(parsed["To"], ["+12815550177"])
            self.assertEqual(parsed["From"], ["+15555550100"])
            self.assertIn("Johnny Tamales Missouri City", parsed["Body"][0])
            self.assertIn("free entrée", parsed["Body"][0])
            self.assertNotIn("Red Dot", parsed["Body"][0])
            self.assertNotIn("secret-token", req.full_url)
            self.assertNotIn("secret-token", req.data.decode())
            self.assertTrue(req.get_header("Authorization").startswith("Basic "))
            first_text = first.get_data(as_text=True)
            self.assertIn(ELIGIBLE, first_text)
            self.assertIn("Text sent.", first_text)
            self.assertIn("Eligible for reward", second.get_data(as_text=True))
            self.assertNotIn("Text sent.", second.get_data(as_text=True))
            guest = fetch_guest("2815550177")
            self.assertEqual(guest["points"], 20)
            self.assertEqual(guest["reward_eligible"], 1)
            self.assertEqual(len(events_for("2815550177", "reward_eligible")), 1)
        finally:
            for key in ENV_KEYS:
                os.environ.pop(key, None)

    def test_sms_http_failure_still_flags(self):
        self.settings(reward_threshold="10")
        self.client.post(
            "/staff/enroll",
            data={"name": "Rae Ortiz", "phone": "2815550166"},
        )
        os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
        os.environ["TWILIO_AUTH_TOKEN"] = "secret-token"
        os.environ["TWILIO_FROM"] = "+15555550100"
        try:
            with mock.patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("down"),
            ):
                rv = self.client.post(
                    "/staff/checkin",
                    data={"phone": "2815550166"},
                    follow_redirects=True,
                )
            self.assertEqual(rv.status_code, 200)
            text = rv.get_data(as_text=True)
            self.assertIn("Eligible for reward", text)
            self.assertIn("SMS could not be sent.", text)
            guest = fetch_guest("2815550166")
            self.assertEqual(guest["reward_eligible"], 1)
            self.assertEqual(guest["reward_notified"], 1)
            self.assertIn("sms=failed", events_for("2815550166", "reward_eligible")[-1]["note"])
        finally:
            for key in ENV_KEYS:
                os.environ.pop(key, None)

    def test_partial_twilio_config_skips_without_request(self):
        os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
        os.environ["TWILIO_AUTH_TOKEN"] = "secret-token"
        try:
            with mock.patch("urllib.request.urlopen") as urlopen:
                status = app.send_reward_sms("8325551003", "Taylor Nguyen", "free entrée", 10)
            self.assertEqual(status, "skipped")
            self.assertEqual(urlopen.call_count, 0)
            self.assertFalse(app.twilio_configured())
        finally:
            os.environ.pop("TWILIO_ACCOUNT_SID", None)
            os.environ.pop("TWILIO_AUTH_TOKEN", None)

    def test_webhook_and_email_stub(self):
        self.settings(reward_threshold="10")
        self.client.post(
            "/staff/enroll",
            data={"name": "Nina Shah", "phone": "2815550155"},
        )
        os.environ["REWARD_WEBHOOK_URL"] = "https://example.test/hook"
        os.environ["REWARD_NOTIFY_EMAIL"] = "manager@johnnytamalesmc.com"
        try:
            with mock.patch("urllib.request.urlopen", return_value=_Resp(200)) as urlopen:
                rv = self.client.post(
                    "/staff/checkin",
                    data={"phone": "2815550155"},
                    follow_redirects=True,
                )
            self.assertEqual(rv.status_code, 200)
            self.assertEqual(urlopen.call_count, 1)
            req = urlopen.call_args[0][0]
            payload = json.loads(req.data.decode())
            self.assertEqual(payload["event"], "reward_eligible")
            self.assertEqual(payload["phone"], "2815550155")
            self.assertEqual(payload["venue"], "Johnny Tamales Missouri City")
            self.assertNotIn("Red Dot", json.dumps(payload))
            note = events_for("2815550155", "reward_eligible")[-1]["note"]
            self.assertIn("webhook=sent", note)
            self.assertIn("email=stubbed", note)
            self.assertIn("sms=skipped", note)
        finally:
            os.environ.pop("REWARD_WEBHOOK_URL", None)
            os.environ.pop("REWARD_NOTIFY_EMAIL", None)

    def test_webhook_failure_does_not_crash(self):
        self.settings(reward_threshold="10")
        self.client.post(
            "/staff/enroll",
            data={"name": "Owen Blake", "phone": "2815550144"},
        )
        os.environ["REWARD_WEBHOOK_URL"] = "https://example.test/hook"
        try:
            with mock.patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("nope"),
            ):
                rv = self.client.post(
                    "/staff/checkin",
                    data={"phone": "2815550144"},
                    follow_redirects=True,
                )
            self.assertEqual(rv.status_code, 200)
            self.assertEqual(fetch_guest("2815550144")["reward_eligible"], 1)
            self.assertIn(
                "webhook=failed",
                events_for("2815550144", "reward_eligible")[-1]["note"],
            )
        finally:
            os.environ.pop("REWARD_WEBHOOK_URL", None)

    def test_non_http_webhook_is_ignored(self):
        os.environ["REWARD_WEBHOOK_URL"] = "file:///tmp/ann-reward"
        try:
            with mock.patch("urllib.request.urlopen") as urlopen:
                status = app.send_reward_webhook({"event": "reward_eligible"})
            self.assertEqual(status, "skipped")
            self.assertEqual(urlopen.call_count, 0)
        finally:
            os.environ.pop("REWARD_WEBHOOK_URL", None)

    def test_first_visit_referral_notifies_once(self):
        self.settings(reward_threshold="30")
        self.client.post(
            "/staff/enroll",
            data={"name": "Casey Kim", "phone": "2815550191", "ref_code": "ALEX"},
        )
        os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
        os.environ["TWILIO_AUTH_TOKEN"] = "secret-token"
        os.environ["TWILIO_FROM"] = "+15555550100"
        try:
            with mock.patch("urllib.request.urlopen", return_value=_Resp()) as urlopen:
                self.client.post(
                    "/staff/checkin",
                    data={"phone": "2815550191"},
                    follow_redirects=True,
                )
            self.assertEqual(urlopen.call_count, 1)
            guest = fetch_guest("2815550191")
            self.assertEqual(guest["points"], 35)
            self.assertEqual(guest["reward_eligible"], 1)
            self.assertEqual(len(events_for("2815550191", "reward_eligible")), 1)
        finally:
            for key in ENV_KEYS:
                os.environ.pop(key, None)

    def test_redeem_clears_cycle_and_can_earn_again(self):
        self.settings(reward_threshold="10")
        self.client.post(
            "/staff/enroll",
            data={"name": "Rae Ortiz", "phone": "2815550166"},
        )
        self.client.post("/staff/checkin", data={"phone": "2815550166"})
        guest = fetch_guest("2815550166")
        self.assertEqual(guest["points"], 10)
        self.assertEqual(guest["reward_eligible"], 1)

        not_yet = self.client.post(
            "/staff/redeem",
            data={"phone": "8325551003"},
            follow_redirects=True,
        )
        self.assertIn("not eligible", not_yet.get_data(as_text=True).lower())
        self.assertEqual(fetch_guest("8325551003")["points"], 0)

        missing = self.client.post(
            "/staff/redeem",
            data={"phone": "2815550000"},
            follow_redirects=True,
        )
        self.assertIn("Guest not found.", missing.get_data(as_text=True))

        os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
        os.environ["TWILIO_AUTH_TOKEN"] = "secret-token"
        os.environ["TWILIO_FROM"] = "+15555550100"
        try:
            with mock.patch("urllib.request.urlopen", return_value=_Resp()) as urlopen:
                redeemed = self.client.post(
                    "/staff/redeem",
                    data={"guest_id": guest["id"]},
                    follow_redirects=True,
                )
                self.assertEqual(urlopen.call_count, 0)
                again = self.client.post(
                    "/staff/checkin",
                    data={"phone": "2815550166"},
                    follow_redirects=True,
                )
            self.assertEqual(urlopen.call_count, 1)
        finally:
            for key in ENV_KEYS:
                os.environ.pop(key, None)

        self.assertIn("Redeemed free entrée for Rae Ortiz (-10 pts).", redeemed.get_data(as_text=True))
        self.assertNotIn(ELIGIBLE, redeemed.get_data(as_text=True))
        earned_again = fetch_guest("2815550166")
        self.assertEqual(earned_again["points"], 10)
        self.assertEqual(earned_again["reward_eligible"], 1)
        self.assertIn(ELIGIBLE, again.get_data(as_text=True))
        self.assertIn("Text sent.", again.get_data(as_text=True))
        kinds = [row["kind"] for row in events_for("2815550166")]
        self.assertIn("reward_redeem", kinds)
        self.assertEqual(kinds.count("reward_eligible"), 2)

    def test_redeem_keeps_eligibility_when_points_remain(self):
        self.settings(reward_threshold="10")
        maria = fetch_guest("8325551001")
        self.assertEqual(maria["points"], 20)
        os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
        os.environ["TWILIO_AUTH_TOKEN"] = "secret-token"
        os.environ["TWILIO_FROM"] = "+15555550100"
        try:
            with mock.patch("urllib.request.urlopen", return_value=_Resp()) as urlopen:
                rv = self.client.post(
                    "/staff/redeem",
                    data={"guest_id": maria["id"]},
                    follow_redirects=True,
                )
            self.assertEqual(urlopen.call_count, 1)
        finally:
            for key in ENV_KEYS:
                os.environ.pop(key, None)
        text = rv.get_data(as_text=True)
        self.assertIn("Redeemed free entrée for Maria Gomez (-10 pts).", text)
        self.assertIn("Still eligible for another free entrée.", text)
        self.assertIn("Text sent.", text)
        self.assertIn(ELIGIBLE, text)
        updated = fetch_guest("8325551001")
        self.assertEqual(updated["points"], 10)
        self.assertEqual(updated["reward_eligible"], 1)
        self.assertEqual(updated["reward_notified"], 1)
