"""EduBridge backend services (no Streamlit imports, so everything here is unit-testable).

Every public function that changes data takes the *acting user id* and re-checks role/ownership/state
inside the function, so the UI cannot bypass authorisation. Money-like state (EduCoins, bookings,
payments) is changed only inside BEGIN IMMEDIATE transactions with status preconditions.
"""
import json, os, re, secrets, sqlite3, hmac, base64, urllib.request, urllib.error
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path

import pricing

DB = Path(os.environ.get("EDUBRIDGE_DB", Path(__file__).with_name("edubridge.db")))

# ---- Tunable business rules -------------------------------------------------------------------
COIN_INR = 1                 # 1 EduCoin = Rs.1 of tutor-fee value
SWAP_TEACH_COINS = 50        # coins to the teacher of a verified swap session
SWAP_LEARN_COINS = 20        # coins to the learner of a verified swap session
SWAP_MIN_MINUTES = 20        # sessions shorter than this earn nothing
SWAP_DAILY_CAP = 2           # rewarded swap sessions per user per day
SWAP_PAIR_WEEKLY_CAP = 3     # rewarded swap sessions per pair of users per 7 days
DOUBT_HELPFUL_COINS = 10
DOUBT_DAILY_CAP = 5
FREE_AI_PER_MONTH = 3        # free AI courses + mind maps per user per calendar month
AI_COIN_COST = 15            # coins per AI generation beyond the free quota
ACTIVE_STATUSES = ("Pending", "Accepted", "Confirmed")


class ServiceError(Exception):
    """A user-presentable error."""


def now_iso():
    return datetime.now().isoformat(timespec="minutes")


class Row(dict):
    """Database row that behaves like sqlite3.Row (row["col"] and row[0]) but is a plain dict underneath.

    sqlite3.Row cannot be copied or pickled on newer Python versions (3.14 raises
    "TypeError: cannot pickle 'sqlite3.Row' object"), and Streamlit deep-copies the options given to
    st.selectbox / st.radio / st.multiselect. A dict subclass copies fine, so every dropdown works.
    """
    __slots__ = ()

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


def row_factory(cursor, row):
    return Row(zip([d[0] for d in cursor.description], row))


def connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=15, isolation_level=None)
    c.row_factory = row_factory
    return c


@contextmanager
def tx():
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.execute("COMMIT")
    except BaseException:
        try:
            c.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        c.close()


def qall(sql, p=()):
    c = connect()
    try:
        return c.execute(sql, p).fetchall()
    finally:
        c.close()


def qone(sql, p=()):
    rows = qall(sql, p)
    return rows[0] if rows else None


def _notify(c, uid, msg):
    c.execute("INSERT INTO notifications(user_id,body,created) VALUES(?,?,?)", (uid, msg, now_iso()))


# ---- Schema -----------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS tutor_profiles(
  user_id INTEGER PRIMARY KEY, headline TEXT DEFAULT '', categories TEXT DEFAULT '', subjects TEXT DEFAULT '',
  qualifications TEXT DEFAULT '', experience_years INTEGER DEFAULT 0, city TEXT DEFAULT '', state TEXT DEFAULT '',
  country TEXT DEFAULT 'India', modes TEXT DEFAULT 'online', preferences TEXT DEFAULT '', photo BLOB,
  hourly_price INTEGER, weekly_price INTEGER, weekly_sessions INTEGER DEFAULT 3, weekly_hours REAL DEFAULT 1,
  monthly_price INTEGER, monthly_sessions INTEGER DEFAULT 12, monthly_hours REAL DEFAULT 1,
  accepts_coins INTEGER DEFAULT 0, coin_max_pct INTEGER DEFAULT 0, travel_radius_km INTEGER DEFAULT 10,
  active INTEGER DEFAULT 1, updated TEXT);
CREATE TABLE IF NOT EXISTS tutor_availability(
  id INTEGER PRIMARY KEY, tutor_id INTEGER NOT NULL, weekday INTEGER NOT NULL, start_time TEXT NOT NULL,
  end_time TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'both');
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, tutor_id INTEGER NOT NULL, mode TEXT NOT NULL,
  plan TEXT NOT NULL, slot_start TEXT NOT NULL, sessions INTEGER NOT NULL, total_hours REAL NOT NULL,
  list_price INTEGER NOT NULL, coins_applied INTEGER NOT NULL DEFAULT 0, amount_due INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'Pending', payment_status TEXT NOT NULL DEFAULT 'none',
  payment_link_id TEXT, payment_link_url TEXT, payment_id TEXT, paid_at TEXT, meeting_url TEXT,
  location_note TEXT DEFAULT '', student_note TEXT DEFAULT '', tutor_note TEXT DEFAULT '',
  created TEXT, updated TEXT, completed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_bookings_tutor_slot ON bookings(tutor_id,slot_start);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY, booking_id INTEGER UNIQUE NOT NULL, student_id INTEGER NOT NULL,
  tutor_id INTEGER NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), comment TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS coin_txns(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('earn','spend','refund')),
  amount INTEGER NOT NULL, reason TEXT, ref_type TEXT NOT NULL, ref_id INTEGER NOT NULL, created TEXT,
  UNIQUE(user_id,kind,ref_type,ref_id));
CREATE TABLE IF NOT EXISTS swap_sessions(
  id INTEGER PRIMARY KEY, swap_id INTEGER NOT NULL, teacher_id INTEGER NOT NULL, learner_id INTEGER NOT NULL,
  topic TEXT, scheduled TEXT, duration_min INTEGER DEFAULT 30, status TEXT DEFAULT 'Scheduled',
  teacher_confirmed INTEGER DEFAULT 0, learner_confirmed INTEGER DEFAULT 0, rewarded INTEGER DEFAULT 0,
  created TEXT, completed_at TEXT);
CREATE TABLE IF NOT EXISTS ai_courses(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT, topic TEXT, level TEXT, objectives TEXT,
  duration TEXT, data TEXT NOT NULL, created TEXT);
CREATE TABLE IF NOT EXISTS course_progress(
  user_id INTEGER NOT NULL, course_id INTEGER NOT NULL, lesson_idx INTEGER NOT NULL, completed INTEGER DEFAULT 0,
  quiz_score INTEGER, quiz_total INTEGER, updated TEXT, PRIMARY KEY(user_id,course_id,lesson_idx));
CREATE TABLE IF NOT EXISTS mind_maps(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, topic TEXT, options TEXT, data TEXT NOT NULL, created TEXT);
CREATE TABLE IF NOT EXISTS ai_usage(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT, ref_id INTEGER, coins INTEGER DEFAULT 0, created TEXT);
"""


def init_schema():
    """Create/upgrade the new tables. Safe to call on every start; existing data is preserved."""
    c = connect()
    try:
        c.executescript(SCHEMA)
        ucols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        for col, ddl in [("city", "TEXT DEFAULT ''"), ("state", "TEXT DEFAULT ''"), ("country", "TEXT DEFAULT 'India'")]:
            if col not in ucols:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
        # Older databases may lack uniqueness on room membership; dedupe then enforce it (prevents double-joins).
        c.execute("DELETE FROM room_members WHERE rowid NOT IN (SELECT MIN(rowid) FROM room_members GROUP BY room_id,user_id)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_room_members_unique ON room_members(room_id,user_id)")
        scols = {r["name"] for r in c.execute("PRAGMA table_info(swaps)")}
        if "room_id" not in scols:
            c.execute("ALTER TABLE swaps ADD COLUMN room_id INTEGER")
    finally:
        c.close()


# ---- Small helpers ----------------------------------------------------------------------------
def get_user(uid):
    return qone("SELECT * FROM users WHERE id=?", (uid,))


def _require_role(uid, role):
    u = get_user(uid)
    if not u or u["role"] != role:
        raise ServiceError(f"Only {role}s can do this.")
    return u


def split_list(s):
    return [x.strip() for x in re.split(r"[,;\n]", s or "") if x.strip()]


# ---- EduCoins ledger --------------------------------------------------------------------------
def coin_balance(uid, c=None):
    sql = "SELECT COALESCE(SUM(amount),0) n FROM coin_txns WHERE user_id=?"
    if c is not None:
        return c.execute(sql, (uid,)).fetchone()["n"]
    return qone(sql, (uid,))["n"]


def _credit(c, uid, amount, kind, reason, ref_type, ref_id):
    """Insert a positive ledger row. Returns False if this (user,kind,ref) was already recorded."""
    if amount <= 0:
        raise ServiceError("Invalid coin amount.")
    try:
        c.execute("INSERT INTO coin_txns(user_id,kind,amount,reason,ref_type,ref_id,created) VALUES(?,?,?,?,?,?,?)",
                  (uid, kind, int(amount), reason, ref_type, ref_id, now_iso()))
        return True
    except sqlite3.IntegrityError:
        return False


def _debit(c, uid, amount, reason, ref_type, ref_id):
    """Atomic spend. Must run inside tx(); BEGIN IMMEDIATE serialises concurrent spenders."""
    amount = int(amount)
    if amount <= 0:
        raise ServiceError("Invalid coin amount.")
    if coin_balance(uid, c) < amount:
        raise ServiceError("Not enough EduCoins.")
    try:
        c.execute("INSERT INTO coin_txns(user_id,kind,amount,reason,ref_type,ref_id,created) VALUES(?,?,?,?,?,?,?)",
                  (uid, "spend", -amount, reason, ref_type, ref_id, now_iso()))
    except sqlite3.IntegrityError:
        raise ServiceError("This charge was already recorded.")


def wallet_summary(uid):
    r = qone("""SELECT COALESCE(SUM(CASE WHEN kind='earn' THEN amount END),0) earned,
                       COALESCE(-SUM(CASE WHEN kind='spend' THEN amount END),0) spent,
                       COALESCE(SUM(CASE WHEN kind='refund' THEN amount END),0) refunded,
                       COALESCE(SUM(amount),0) balance FROM coin_txns WHERE user_id=?""", (uid,))
    pend_swap = qall("""SELECT teacher_id,learner_id FROM swap_sessions WHERE status='Scheduled'
                        AND (teacher_confirmed=1 OR learner_confirmed=1) AND (teacher_id=? OR learner_id=?)""", (uid, uid))
    pending = sum(SWAP_TEACH_COINS if s["teacher_id"] == uid else SWAP_LEARN_COINS for s in pend_swap)
    pend_ch = qall("""SELECT c.reward FROM challenge_submissions s JOIN challenges c ON c.id=s.challenge_id
                      WHERE s.user_id=? AND s.status='Pending'""", (uid,))
    pending += sum(max(5, x["reward"] // 5) for x in pend_ch)
    return {"balance": r["balance"], "earned": r["earned"], "spent": r["spent"], "refunded": r["refunded"],
            "pending": pending}


def wallet_history(uid, limit=100):
    return qall("SELECT * FROM coin_txns WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))


def award_coins(uid, amount, reason, ref_type, ref_id):
    """Backend-only reward entry point. Duplicate (user, ref) awards are ignored. Returns True if credited."""
    with tx() as c:
        ok = _credit(c, uid, amount, "earn", reason, ref_type, ref_id)
        if ok:
            _notify(c, uid, f"+{amount} EduCoins: {reason}")
        return ok


# ---- Tutor profile, pricing, availability -------------------------------------------------------
def get_profile(tutor_id):
    return qone("SELECT p.*,u.name,u.bio FROM tutor_profiles p JOIN users u ON u.id=p.user_id WHERE p.user_id=?",
                (tutor_id,))


def _price(v, label):
    if v in (None, "", 0):
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ServiceError(f"{label} must be a whole number of rupees.")
    if v < 0 or v > 200000:
        raise ServiceError(f"{label} must be between Rs.0 and Rs.200000.")
    return v or None


def save_tutor_profile(actor, f, photo_bytes=None):
    """Create/update the actor's own tutor profile. Prices are only ever set by the tutor."""
    _require_role(actor, "tutor")
    modes = [m for m in f.get("modes", []) if m in ("online", "offline")]
    if not modes:
        raise ServiceError("Choose at least one teaching mode (online / offline).")
    if not f.get("subjects", "").strip():
        raise ServiceError("List at least one subject you teach.")
    hourly = _price(f.get("hourly_price"), "Hourly price")
    weekly = _price(f.get("weekly_price"), "Weekly price")
    monthly = _price(f.get("monthly_price"), "Monthly price")
    ws, wh = int(f.get("weekly_sessions") or 3), float(f.get("weekly_hours") or 1)
    ms, mh = int(f.get("monthly_sessions") or 12), float(f.get("monthly_hours") or 1)
    if not (1 <= ws <= 14 and 1 <= ms <= 60 and 0.5 <= wh <= 6 and 0.5 <= mh <= 6):
        raise ServiceError("Check the number of sessions and hours per session in your weekly/monthly plans.")
    pct = max(0, min(100, int(f.get("coin_max_pct") or 0)))
    if photo_bytes:
        if len(photo_bytes) > 1_000_000:
            raise ServiceError("Profile picture must be under 1 MB.")
        if not (photo_bytes[:3] == b"\xff\xd8\xff" or photo_bytes[:8] == b"\x89PNG\r\n\x1a\n"):
            raise ServiceError("Profile picture must be a JPG or PNG image.")
    vals = (f.get("headline", "").strip()[:140], ", ".join(f.get("categories", [])), f["subjects"].strip(),
            f.get("qualifications", "").strip(), max(0, min(60, int(f.get("experience_years") or 0))),
            f.get("city", "").strip(), f.get("state", "").strip(), (f.get("country") or "India").strip(),
            ",".join(modes), f.get("preferences", "").strip(), hourly, weekly, ws, wh, monthly, ms, mh,
            int(bool(f.get("accepts_coins"))), pct if f.get("accepts_coins") else 0,
            max(1, min(100, int(f.get("travel_radius_km") or 10))), int(bool(f.get("active", True))), now_iso())
    with tx() as c:
        exists = c.execute("SELECT 1 FROM tutor_profiles WHERE user_id=?", (actor,)).fetchone()
        if exists:
            c.execute("""UPDATE tutor_profiles SET headline=?,categories=?,subjects=?,qualifications=?,experience_years=?,
                city=?,state=?,country=?,modes=?,preferences=?,hourly_price=?,weekly_price=?,weekly_sessions=?,weekly_hours=?,
                monthly_price=?,monthly_sessions=?,monthly_hours=?,accepts_coins=?,coin_max_pct=?,travel_radius_km=?,active=?,updated=?
                WHERE user_id=?""", vals + (actor,))
        else:
            c.execute("""INSERT INTO tutor_profiles(headline,categories,subjects,qualifications,experience_years,city,state,country,
                modes,preferences,hourly_price,weekly_price,weekly_sessions,weekly_hours,monthly_price,monthly_sessions,monthly_hours,
                accepts_coins,coin_max_pct,travel_radius_km,active,updated,user_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      vals + (actor,))
        if photo_bytes:
            c.execute("UPDATE tutor_profiles SET photo=? WHERE user_id=?", (photo_bytes, actor))
        c.execute("UPDATE users SET city=?,state=?,country=? WHERE id=?",
                  (f.get("city", "").strip(), f.get("state", "").strip(), (f.get("country") or "India").strip(), actor))


def add_availability(actor, weekday, start, end, mode):
    _require_role(actor, "tutor")
    if mode not in ("online", "offline", "both") or not 0 <= int(weekday) <= 6:
        raise ServiceError("Invalid availability.")
    try:
        s = datetime.strptime(start, "%H:%M")
        e = datetime.strptime(end, "%H:%M")
    except ValueError:
        raise ServiceError("Use HH:MM times.")
    if e <= s + timedelta(minutes=59):
        raise ServiceError("An availability window must be at least one hour long.")
    with tx() as c:
        overlap = c.execute("""SELECT 1 FROM tutor_availability WHERE tutor_id=? AND weekday=? AND start_time<? AND end_time>?""",
                            (actor, weekday, end, start)).fetchone()
        if overlap:
            raise ServiceError("That window overlaps one you already added.")
        c.execute("INSERT INTO tutor_availability(tutor_id,weekday,start_time,end_time,mode) VALUES(?,?,?,?,?)",
                  (actor, weekday, start, end, mode))


def delete_availability(actor, avail_id):
    with tx() as c:
        c.execute("DELETE FROM tutor_availability WHERE id=? AND tutor_id=?", (avail_id, actor))


def availability(tutor_id):
    return qall("SELECT * FROM tutor_availability WHERE tutor_id=? ORDER BY weekday,start_time", (tutor_id,))


def free_slots(tutor_id, day, mode):
    """Hourly start times (ISO strings) that are in the tutor's availability and not already held."""
    rows = qall("SELECT * FROM tutor_availability WHERE tutor_id=? AND weekday=? AND mode IN ('both',?) ORDER BY start_time",
                (tutor_id, day.weekday(), mode))
    taken = {r["slot_start"] for r in qall(
        "SELECT slot_start FROM bookings WHERE tutor_id=? AND slot_start LIKE ? AND status IN ('Pending','Accepted','Confirmed')",
        (tutor_id, day.isoformat() + "%"))}
    out, now = [], datetime.now()
    for r in rows:
        t = datetime.combine(day, datetime.strptime(r["start_time"], "%H:%M").time())
        end = datetime.combine(day, datetime.strptime(r["end_time"], "%H:%M").time())
        while t + timedelta(hours=1) <= end:
            iso = t.isoformat(timespec="minutes")
            if t > now and iso not in taken:
                out.append(iso)
            t += timedelta(hours=1)
    return out


def tutor_rating(tutor_id):
    r = qone("SELECT AVG(rating) a, COUNT(*) n FROM reviews WHERE tutor_id=?", (tutor_id,))
    return (round(r["a"], 1) if r["a"] else None), r["n"]


def plan_details(p, plan):
    """(price, sessions, total_hours) for a plan on a profile row, or None if the tutor hasn't offered it."""
    if plan == "hourly" and p["hourly_price"]:
        return p["hourly_price"], 1, 1.0
    if plan == "weekly" and p["weekly_price"]:
        return p["weekly_price"], p["weekly_sessions"], p["weekly_sessions"] * p["weekly_hours"]
    if plan == "monthly" and p["monthly_price"]:
        return p["monthly_price"], p["monthly_sessions"], p["monthly_sessions"] * p["monthly_hours"]
    return None


def price_guidance(p, student_city=None):
    """Estimated market range for a tutor profile row (never modifies the tutor's price)."""
    cat = (p["categories"] or "Other").split(",")[0].strip() or "Other"
    out = {}
    for mode in [m for m in (p["modes"] or "").split(",") if m]:
        out[mode] = pricing.estimate_hourly(cat, p["subjects"], p["city"], p["country"], p["experience_years"],
                                            p["qualifications"], mode, student_city)
    return out


def search_tutors(mode, subject_q="", category="", city="", max_hourly=None, student=None, problem="", sort="Best match"):
    sc = (student["city"] if student else "") or ""
    rows = qall("""SELECT p.*,u.name,u.bio,u.languages FROM tutor_profiles p JOIN users u ON u.id=p.user_id
                   WHERE p.active=1 AND p.hourly_price IS NOT NULL AND (',' || p.modes || ',') LIKE ?""", (f"%,{mode},%",))
    terms = set(re.findall(r"[a-z0-9+#.]{3,}", f"{subject_q} {problem}".lower()))
    res = []
    for p in rows:
        hay = f"{p['subjects']} {p['categories']} {p['headline']} {p['bio'] or ''}".lower()
        if subject_q and not any(t in hay for t in re.findall(r"[a-z0-9+#.]{2,}", subject_q.lower())):
            continue
        if category and category not in (p["categories"] or ""):
            continue
        if city and city.strip().lower() not in (p["city"] or "").lower():
            continue
        if max_hourly and p["hourly_price"] > max_hourly:
            continue
        score = len(terms & set(re.findall(r"[a-z0-9+#.]{3,}", hay)))
        avg, n = tutor_rating(p["user_id"])
        dist = pricing.distance_km(p["city"], sc) if sc else None
        if mode == "offline" and dist is not None and dist > (p["travel_radius_km"] or 10) + 25:
            pass  # still listed; distance is shown so the student can decide
        res.append({"p": p, "rating": avg, "reviews": n, "distance_km": dist, "match": score})
    key = {"Lowest price": lambda r: r["p"]["hourly_price"], "Top rated": lambda r: -(r["rating"] or 0),
           "Nearest": lambda r: (r["distance_km"] is None, r["distance_km"] or 0),
           "Best match": lambda r: (-r["match"], -(r["rating"] or 0))}[sort]
    return sorted(res, key=key)


# ---- Bookings ---------------------------------------------------------------------------------
def create_booking(student_id, tutor_id, mode, plan, slot_iso, coins=0, note=""):
    _require_role(student_id, "student")
    p = get_profile(tutor_id)
    if not p or not p["active"]:
        raise ServiceError("This tutor is not accepting bookings.")
    if mode not in (p["modes"] or "").split(","):
        raise ServiceError(f"This tutor does not offer {mode} teaching.")
    det = plan_details(p, plan)
    if not det:
        raise ServiceError("The tutor has not set a price for that plan.")
    price, sessions, hours = det
    try:
        slot = datetime.fromisoformat(slot_iso)
    except ValueError:
        raise ServiceError("Choose a valid time slot.")
    coins = int(coins or 0)
    if coins < 0:
        raise ServiceError("Invalid EduCoins amount.")
    if coins:
        if not p["accepts_coins"]:
            raise ServiceError("This tutor does not accept EduCoins.")
        cap = (price * p["coin_max_pct"] // 100) // COIN_INR
        if coins > cap:
            raise ServiceError(f"This tutor accepts at most {cap} EduCoins for this plan.")
    due = price - coins * COIN_INR
    with tx() as c:
        # slot must be currently free (re-checked inside the transaction to stop double booking)
        if slot_iso not in free_slots_in_tx(c, tutor_id, slot.date(), mode):
            raise ServiceError("That slot is no longer available. Please pick another.")
        dup = c.execute("SELECT 1 FROM bookings WHERE student_id=? AND tutor_id=? AND plan=? AND status IN ('Pending','Accepted','Confirmed') AND slot_start=?",
                        (student_id, tutor_id, plan, slot_iso)).fetchone()
        if dup:
            raise ServiceError("You already have this booking.")
        cur = c.execute("""INSERT INTO bookings(student_id,tutor_id,mode,plan,slot_start,sessions,total_hours,list_price,coins_applied,
                           amount_due,student_note,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (student_id, tutor_id, mode, plan, slot_iso, sessions, hours, price, coins, due, note.strip(), now_iso(), now_iso()))
        bid = cur.lastrowid
        if coins:
            _debit(c, student_id, coins, f"Held for booking #{bid}", "booking", bid)
        stu = c.execute("SELECT name FROM users WHERE id=?", (student_id,)).fetchone()["name"]
        _notify(c, tutor_id, f"New {mode} {plan} booking request #{bid} from {stu} for {slot_iso.replace('T', ' ')}.")
        return bid


def free_slots_in_tx(c, tutor_id, day, mode):
    rows = c.execute("SELECT * FROM tutor_availability WHERE tutor_id=? AND weekday=? AND mode IN ('both',?)",
                     (tutor_id, day.weekday(), mode)).fetchall()
    taken = {r["slot_start"] for r in c.execute(
        "SELECT slot_start FROM bookings WHERE tutor_id=? AND slot_start LIKE ? AND status IN ('Pending','Accepted','Confirmed')",
        (tutor_id, day.isoformat() + "%"))}
    out, now = [], datetime.now()
    for r in rows:
        t = datetime.combine(day, datetime.strptime(r["start_time"], "%H:%M").time())
        end = datetime.combine(day, datetime.strptime(r["end_time"], "%H:%M").time())
        while t + timedelta(hours=1) <= end:
            iso = t.isoformat(timespec="minutes")
            if t > now and iso not in taken:
                out.append(iso)
            t += timedelta(hours=1)
    return out


def _booking_for_update(c, bid):
    b = c.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    if not b:
        raise ServiceError("Booking not found.")
    return b


def _refund_coins(c, b, why):
    if b["coins_applied"]:
        if _credit(c, b["student_id"], b["coins_applied"], "refund", why, "booking", b["id"]):
            _notify(c, b["student_id"], f"{b['coins_applied']} EduCoins returned ({why}).")


def _confirm(c, b):
    meet = b["meeting_url"]
    if b["mode"] == "online" and not meet:
        meet = "https://meet.jit.si/EduBridge-" + secrets.token_urlsafe(12).replace("_", "x").replace("-", "y")
    c.execute("UPDATE bookings SET status='Confirmed',meeting_url=?,updated=? WHERE id=?", (meet, now_iso(), b["id"]))
    _notify(c, b["student_id"], f"Booking #{b['id']} is confirmed.")
    _notify(c, b["tutor_id"], f"Booking #{b['id']} is confirmed.")


def tutor_respond(actor, bid, accept, note="", location=""):
    with tx() as c:
        b = _booking_for_update(c, bid)
        if b["tutor_id"] != actor:
            raise ServiceError("This is not your booking.")
        if b["status"] != "Pending":
            raise ServiceError(f"Booking is already {b['status']}.")
        if not accept:
            c.execute("UPDATE bookings SET status='Rejected',tutor_note=?,updated=? WHERE id=?", (note.strip(), now_iso(), bid))
            _refund_coins(c, b, "booking rejected")
            _notify(c, b["student_id"], f"Booking #{bid} was declined by the tutor.")
            return "Rejected"
        if b["mode"] == "offline" and not location.strip():
            raise ServiceError("Add the meeting place/address for an offline booking.")
        c.execute("UPDATE bookings SET status='Accepted',tutor_note=?,location_note=?,updated=? WHERE id=?",
                  (note.strip(), location.strip(), now_iso(), bid))
        b = _booking_for_update(c, bid)
        if b["amount_due"] == 0:
            c.execute("UPDATE bookings SET payment_status='not_required' WHERE id=?", (bid,))
            _confirm(c, b)
            return "Confirmed"
        _notify(c, b["student_id"], f"Booking #{bid} accepted. Pay Rs.{b['amount_due']} to confirm it.")
        return "Accepted"


def cancel_booking(actor, bid):
    with tx() as c:
        b = _booking_for_update(c, bid)
        if actor not in (b["student_id"], b["tutor_id"]):
            raise ServiceError("This is not your booking.")
        if b["status"] not in ACTIVE_STATUSES:
            raise ServiceError(f"A {b['status'].lower()} booking cannot be cancelled.")
        pay = "refund_due" if b["payment_status"] == "paid" else b["payment_status"]
        c.execute("UPDATE bookings SET status='Cancelled',payment_status=?,updated=? WHERE id=?", (pay, now_iso(), bid))
        _refund_coins(c, b, "booking cancelled")
        other = b["tutor_id"] if actor == b["student_id"] else b["student_id"]
        _notify(c, other, f"Booking #{bid} was cancelled.")
        return pay == "refund_due"


def complete_booking(actor, bid):
    with tx() as c:
        b = _booking_for_update(c, bid)
        if b["tutor_id"] != actor:
            raise ServiceError("Only the tutor can mark a booking completed.")
        if b["status"] != "Confirmed":
            raise ServiceError("Only confirmed bookings can be completed.")
        if datetime.fromisoformat(b["slot_start"]) > datetime.now():
            raise ServiceError("You can complete a booking only after its start time.")
        c.execute("UPDATE bookings SET status='Completed',completed_at=?,updated=? WHERE id=?", (now_iso(), now_iso(), bid))
        _notify(c, b["student_id"], f"Booking #{bid} is completed. You can now leave a review.")


def add_review(actor, bid, rating, comment):
    with tx() as c:
        b = _booking_for_update(c, bid)
        if b["student_id"] != actor:
            raise ServiceError("Only the student can review this booking.")
        if b["status"] != "Completed":
            raise ServiceError("You can review a booking after it is completed.")
        if not 1 <= int(rating) <= 5:
            raise ServiceError("Choose a rating from 1 to 5.")
        try:
            c.execute("INSERT INTO reviews(booking_id,student_id,tutor_id,rating,comment,created) VALUES(?,?,?,?,?,?)",
                      (bid, actor, b["tutor_id"], int(rating), comment.strip()[:1000], now_iso()))
        except sqlite3.IntegrityError:
            raise ServiceError("You already reviewed this booking.")
        _notify(c, b["tutor_id"], f"New {int(rating)}-star review for booking #{bid}.")


def my_bookings(uid, role):
    col, other = ("student_id", "tutor_id") if role == "student" else ("tutor_id", "student_id")
    return qall(f"""SELECT b.*,u.name AS other_name,u.city AS other_city,
                    (SELECT rating FROM reviews r WHERE r.booking_id=b.id) AS my_rating
                    FROM bookings b JOIN users u ON u.id=b.{other} WHERE b.{col}=? ORDER BY b.slot_start DESC""", (uid,))


def tutor_earnings(tutor_id):
    r = qone("""SELECT
        COALESCE(SUM(CASE WHEN status='Completed' AND payment_status='paid' THEN amount_due END),0) earned_inr,
        COALESCE(SUM(CASE WHEN status='Completed' THEN coins_applied END),0) earned_coins,
        COALESCE(SUM(CASE WHEN status='Confirmed' AND payment_status='paid' THEN amount_due END),0) upcoming_inr,
        COALESCE(SUM(CASE WHEN status='Confirmed' THEN coins_applied END),0) upcoming_coins,
        COALESCE(SUM(CASE WHEN payment_status='refund_due' THEN amount_due END),0) refunds_due
        FROM bookings WHERE tutor_id=?""", (tutor_id,))
    return dict(r)


# ---- Payments (Razorpay Payment Links) ----------------------------------------------------------
def razorpay_keys():
    return os.environ.get("RAZORPAY_KEY_ID", ""), os.environ.get("RAZORPAY_KEY_SECRET", "")


def payments_configured():
    k, s = razorpay_keys()
    return bool(k and s)


def _rzp(method, path, payload=None):
    """HTTP call to Razorpay. Tests replace this function."""
    key, secret = razorpay_keys()
    req = urllib.request.Request("https://api.razorpay.com/v1" + path, method=method,
                                 data=json.dumps(payload).encode() if payload is not None else None)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode())
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ServiceError(f"Payment provider error ({e.code}). Check your Razorpay keys.")
    except (urllib.error.URLError, TimeoutError):
        raise ServiceError("Could not reach the payment provider. Try again shortly.")


def start_payment(actor, bid):
    """Create (or reuse) a Razorpay payment link for an accepted booking. Does NOT mark anything paid."""
    if not payments_configured():
        raise ServiceError("Online payments are not configured on this server (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET).")
    b = qone("SELECT * FROM bookings WHERE id=?", (bid,))
    if not b or b["student_id"] != actor:
        raise ServiceError("This is not your booking.")
    if b["status"] != "Accepted" or b["amount_due"] <= 0:
        raise ServiceError("This booking is not waiting for payment.")
    if b["payment_status"] == "pending" and b["payment_link_url"]:
        return b["payment_link_url"]
    stu = get_user(actor)
    resp = _rzp("POST", "/payment_links", {
        "amount": b["amount_due"] * 100, "currency": "INR", "accept_partial": False,
        "reference_id": f"EB{bid}-{secrets.token_hex(4)}", "description": f"EduBridge booking #{bid}",
        "customer": {"name": stu["name"]}, "notify": {"sms": False, "email": False}, "reminder_enable": False})
    if not resp.get("id") or not resp.get("short_url"):
        raise ServiceError("The payment provider did not return a payment link.")
    with tx() as c:
        c.execute("UPDATE bookings SET payment_status='pending',payment_link_id=?,payment_link_url=?,updated=? WHERE id=? AND status='Accepted'",
                  (resp["id"], resp["short_url"], now_iso(), bid))
    return resp["short_url"]


def verify_payment(actor, bid):
    """Ask Razorpay (server to server) whether the link was paid for the full amount. Returns a status string."""
    b = qone("SELECT * FROM bookings WHERE id=?", (bid,))
    if not b or b["student_id"] != actor:
        raise ServiceError("This is not your booking.")
    if b["payment_status"] == "paid":
        return "paid"
    if not b["payment_link_id"]:
        raise ServiceError("No payment has been started for this booking.")
    resp = _rzp("GET", f"/payment_links/{b['payment_link_id']}")
    st = resp.get("status")
    if st == "paid":
        if resp.get("amount_paid") != b["amount_due"] * 100 or resp.get("currency", "INR") != "INR":
            raise ServiceError("The amount paid does not match the booking. Contact support with booking #%d." % bid)
        pid = (resp.get("payments") or [{}])[0].get("payment_id")
        with tx() as c:
            b2 = _booking_for_update(c, bid)
            if b2["status"] != "Accepted" or b2["payment_status"] == "paid":
                return b2["payment_status"]
            c.execute("UPDATE bookings SET payment_status='paid',payment_id=?,paid_at=? WHERE id=?", (pid, now_iso(), bid))
            _confirm(c, _booking_for_update(c, bid))
        return "paid"
    if st in ("cancelled", "expired"):
        with tx() as c:
            c.execute("UPDATE bookings SET payment_status='failed',payment_link_id=NULL,payment_link_url=NULL WHERE id=? AND payment_status='pending'", (bid,))
        return "failed"
    return "pending"


# ---- Skill swap -------------------------------------------------------------------------------
def _norm_skills(s):
    return {x.lower() for x in split_list(s)}


def swap_matches(me):
    """Peers who can teach something I want AND/OR want something I can teach, best (mutual) first."""
    my_teach, my_want = _norm_skills(me["skills"]), _norm_skills(me["wants"])
    out = []
    for u in qall("SELECT * FROM users WHERE id<>?", (me["id"],)):
        they_teach, they_want = _norm_skills(u["skills"]), _norm_skills(u["wants"])
        learn = sorted(x for x in they_teach if any(x == w or x in w or w in x for w in my_want))
        teach = sorted(x for x in they_want if any(x == s or x in s or s in x for s in my_teach))
        if learn or teach:
            out.append({"user": u, "learn": learn, "teach": teach, "mutual": bool(learn and teach)})
    return sorted(out, key=lambda m: (not m["mutual"], -(len(m["learn"]) + len(m["teach"]))))


def swap_invite(actor, partner_id, title, offer, want, plan):
    me, partner = get_user(actor), get_user(partner_id)
    if not partner or partner_id == actor:
        raise ServiceError("Choose another learner to invite.")
    if not (title.strip() and offer.strip() and want.strip()):
        raise ServiceError("Enter a name, the skill you teach and the skill you want to learn.")
    with tx() as c:
        dup = c.execute("""SELECT 1 FROM swaps s JOIN swap_members a ON a.swap_id=s.id AND a.user_id=?
                           JOIN swap_members b ON b.swap_id=s.id AND b.user_id=? WHERE s.status='Open'
                           AND (b.status='Pending' OR b.status='Accepted') AND s.creator=?""", (actor, partner_id, actor)).fetchone()
        if dup:
            raise ServiceError("You already have an open swap or pending invitation with this person.")
        cur = c.execute("INSERT INTO swaps(title,creator,members,plan,created) VALUES(?,?,?,?,?)",
                        (title.strip(), actor, str(actor), f"{me['name']} teaches: {offer.strip()} | Wants to learn: {want.strip()} | {plan.strip()}", now_iso()))
        sid = cur.lastrowid
        c.execute("INSERT INTO swap_members(swap_id,user_id,status,joined) VALUES(?,?,?,?)", (sid, actor, "Accepted", now_iso()))
        c.execute("INSERT INTO swap_members(swap_id,user_id,status,joined) VALUES(?,?,?,NULL)", (sid, partner_id, "Pending"))
        _notify(c, partner_id, f"{me['name']} invited you to the '{title.strip()}' skill swap.")
        return sid


def _ensure_room(c, swap_id):
    s = c.execute("SELECT * FROM swaps WHERE id=?", (swap_id,)).fetchone()
    if s["room_id"] and c.execute("SELECT 1 FROM rooms WHERE id=?", (s["room_id"],)).fetchone():
        rid = s["room_id"]
    else:
        code = secrets.token_hex(4).upper()
        while c.execute("SELECT 1 FROM rooms WHERE room_code=?", (code,)).fetchone():
            code = secrets.token_hex(4).upper()
        rid = c.execute("INSERT INTO rooms(title,owner,topic,scheduled,created,room_code) VALUES(?,?,?,?,?,?)",
                        (f"Swap: {s['title']}", s["creator"], "Skill swap", "", now_iso(), code)).lastrowid
        c.execute("UPDATE swaps SET room_id=? WHERE id=?", (rid, swap_id))
    for m in c.execute("SELECT user_id FROM swap_members WHERE swap_id=? AND status='Accepted'", (swap_id,)).fetchall():
        c.execute("INSERT OR IGNORE INTO room_members(room_id,user_id) VALUES(?,?)", (rid, m["user_id"]))
    return rid


def swap_respond(actor, swap_id, accept):
    with tx() as c:
        m = c.execute("SELECT * FROM swap_members WHERE swap_id=? AND user_id=?", (swap_id, actor)).fetchone()
        s = c.execute("SELECT * FROM swaps WHERE id=?", (swap_id,)).fetchone()
        if not m or not s or m["status"] != "Pending":
            raise ServiceError("There is no pending invitation to answer.")
        me = c.execute("SELECT name FROM users WHERE id=?", (actor,)).fetchone()["name"]
        if accept:
            c.execute("UPDATE swap_members SET status='Accepted',joined=? WHERE swap_id=? AND user_id=?", (now_iso(), swap_id, actor))
            c.execute("UPDATE swaps SET members=CASE WHEN members='' THEN ? ELSE members||','||? END WHERE id=?", (str(actor), str(actor), swap_id))
            _ensure_room(c, swap_id)
            _notify(c, s["creator"], f"{me} accepted '{s['title']}'. Your swap room is ready.")
        else:
            c.execute("DELETE FROM swap_members WHERE swap_id=? AND user_id=?", (swap_id, actor))
            c.execute("UPDATE swaps SET status='Declined' WHERE id=? AND NOT EXISTS(SELECT 1 FROM swap_members WHERE swap_id=? AND status='Accepted' AND user_id<>?)",
                      (swap_id, swap_id, s["creator"]))
            _notify(c, s["creator"], f"{me} declined '{s['title']}'.")


def swap_room(actor, swap_id):
    """Return the swap's room row (creating/repairing it for older swaps). Only accepted members may call."""
    with tx() as c:
        if not c.execute("SELECT 1 FROM swap_members WHERE swap_id=? AND user_id=? AND status='Accepted'", (swap_id, actor)).fetchone():
            raise ServiceError("You are not part of this swap.")
        rid = _ensure_room(c, swap_id)
        return c.execute("SELECT * FROM rooms WHERE id=?", (rid,)).fetchone()


def _swap_partners(c, swap_id):
    return [r["user_id"] for r in c.execute("SELECT user_id FROM swap_members WHERE swap_id=? AND status='Accepted'", (swap_id,))]


def schedule_swap_session(actor, swap_id, teacher_id, topic, scheduled_iso, minutes):
    with tx() as c:
        members = _swap_partners(c, swap_id)
        if actor not in members or len(members) != 2 or teacher_id not in members:
            raise ServiceError("Both partners must have accepted the swap before scheduling.")
        if not topic.strip():
            raise ServiceError("Add a topic for the session.")
        try:
            when = datetime.fromisoformat(scheduled_iso)
        except ValueError:
            raise ServiceError("Choose a valid date and time.")
        if when < datetime.now() - timedelta(minutes=5):
            raise ServiceError("Choose a time in the future.")
        learner = [m for m in members if m != teacher_id][0]
        cur = c.execute("INSERT INTO swap_sessions(swap_id,teacher_id,learner_id,topic,scheduled,duration_min,created) VALUES(?,?,?,?,?,?,?)",
                        (swap_id, teacher_id, learner, topic.strip(), scheduled_iso, int(minutes), now_iso()))
        other = learner if actor == teacher_id else teacher_id
        _notify(c, other, f"A swap session '{topic.strip()}' was scheduled for {scheduled_iso.replace('T', ' ')}.")
        return cur.lastrowid


def swap_sessions(swap_id):
    return qall("SELECT * FROM swap_sessions WHERE swap_id=? ORDER BY scheduled DESC", (swap_id,))


def confirm_swap_session(actor, sid):
    """Each partner confirms the session happened. Coins are paid once BOTH confirm, subject to anti-abuse caps."""
    with tx() as c:
        s = c.execute("SELECT * FROM swap_sessions WHERE id=?", (sid,)).fetchone()
        if not s or actor not in (s["teacher_id"], s["learner_id"]):
            raise ServiceError("This is not your session.")
        if s["status"] != "Scheduled":
            raise ServiceError(f"This session is already {s['status'].lower()}.")
        if datetime.fromisoformat(s["scheduled"]) > datetime.now():
            raise ServiceError("You can confirm a session only after it has started.")
        col = "teacher_confirmed" if actor == s["teacher_id"] else "learner_confirmed"
        c.execute(f"UPDATE swap_sessions SET {col}=1 WHERE id=?", (sid,))
        s = c.execute("SELECT * FROM swap_sessions WHERE id=?", (sid,)).fetchone()
        if not (s["teacher_confirmed"] and s["learner_confirmed"]):
            return "Waiting for your partner to confirm."
        c.execute("UPDATE swap_sessions SET status='Completed',completed_at=? WHERE id=?", (now_iso(), sid))
        reason = _swap_reward(c, s)
        if reason:
            return f"Session completed. No EduCoins this time: {reason}"
        return f"Session verified by both partners. +{SWAP_TEACH_COINS} EduCoins to the teacher, +{SWAP_LEARN_COINS} to the learner."


def _swap_reward(c, s):
    """Pay coins for a mutually confirmed session. Returns a reason string if nothing was paid, else None."""
    if s["duration_min"] < SWAP_MIN_MINUTES:
        return f"sessions under {SWAP_MIN_MINUTES} minutes are not rewarded."
    day = (s["completed_at"] or now_iso())[:10]
    week_ago = (datetime.now() - timedelta(days=7)).isoformat(timespec="minutes")
    for uid in (s["teacher_id"], s["learner_id"]):
        n = c.execute("SELECT COUNT(*) n FROM coin_txns WHERE user_id=? AND ref_type='swap_session' AND kind='earn' AND created LIKE ?",
                      (uid, day + "%")).fetchone()["n"]
        if n >= SWAP_DAILY_CAP:
            return f"daily reward limit of {SWAP_DAILY_CAP} swap sessions reached."
    pair = c.execute("""SELECT COUNT(*) n FROM swap_sessions WHERE rewarded=1 AND completed_at>=? AND
                        ((teacher_id=? AND learner_id=?) OR (teacher_id=? AND learner_id=?))""",
                     (week_ago, s["teacher_id"], s["learner_id"], s["learner_id"], s["teacher_id"])).fetchone()["n"]
    if pair >= SWAP_PAIR_WEEKLY_CAP:
        return f"weekly reward limit for this pair of learners ({SWAP_PAIR_WEEKLY_CAP}) reached."
    _credit(c, s["teacher_id"], SWAP_TEACH_COINS, "earn", "Taught a verified skill-swap session", "swap_session", s["id"])
    _credit(c, s["learner_id"], SWAP_LEARN_COINS, "earn", "Learned in a verified skill-swap session", "swap_session", s["id"])
    c.execute("UPDATE swap_sessions SET rewarded=1 WHERE id=?", (s["id"],))
    _notify(c, s["teacher_id"], f"+{SWAP_TEACH_COINS} EduCoins for the verified swap session '{s['topic']}'.")
    _notify(c, s["learner_id"], f"+{SWAP_LEARN_COINS} EduCoins for the verified swap session '{s['topic']}'.")
    return None


def mark_reply_helpful(actor, reply_id):
    """The student who asked marks a tutor/peer reply helpful -> the replier earns coins (once per reply)."""
    with tx() as c:
        r = c.execute("""SELECT r.id,r.tutor_id,d.student_id,d.subject FROM doubt_replies r JOIN doubts d ON d.id=r.doubt_id WHERE r.id=?""",
                      (reply_id,)).fetchone()
        if not r or r["student_id"] != actor:
            raise ServiceError("Only the person who asked can rate a reply.")
        if r["tutor_id"] == actor:
            raise ServiceError("You cannot reward yourself.")
        today = now_iso()[:10]
        n = c.execute("SELECT COUNT(*) n FROM coin_txns WHERE user_id=? AND ref_type='doubt_reply' AND created LIKE ?",
                      (r["tutor_id"], today + "%")).fetchone()["n"]
        if n >= DOUBT_DAILY_CAP:
            raise ServiceError("This helper has reached today's reward limit. Thank you for rating!")
        if not _credit(c, r["tutor_id"], DOUBT_HELPFUL_COINS, "earn", f"Helpful answer: {r['subject']}", "doubt_reply", reply_id):
            raise ServiceError("You already marked this reply helpful.")
        _notify(c, r["tutor_id"], f"+{DOUBT_HELPFUL_COINS} EduCoins: your answer on {r['subject']} was marked helpful.")


def message_contacts(uid):
    """Users this person is allowed to message: accepted/confirmed tutoring, bookings, swap partners (and legacy requests)."""
    return qall("""SELECT DISTINCT u.* FROM users u WHERE u.id<>? AND u.id IN (
        SELECT tutor_id FROM tutor_sessions WHERE student_id=? AND status IN ('Accepted','Completed')
        UNION SELECT student_id FROM tutor_sessions WHERE tutor_id=? AND status IN ('Accepted','Completed')
        UNION SELECT tutor_id FROM bookings WHERE student_id=? AND status IN ('Accepted','Confirmed','Completed')
        UNION SELECT student_id FROM bookings WHERE tutor_id=? AND status IN ('Accepted','Confirmed','Completed')
        UNION SELECT tutor FROM requests WHERE student=? AND status='Accepted'
        UNION SELECT student FROM requests WHERE tutor=? AND status='Accepted'
        UNION SELECT b.user_id FROM swap_members a JOIN swap_members b ON a.swap_id=b.swap_id AND b.user_id<>a.user_id
              WHERE a.user_id=? AND a.status='Accepted' AND b.status='Accepted') ORDER BY u.name""",
                (uid,) * 8)


# ---- AI usage, courses, mind maps ---------------------------------------------------------------
def ai_quota(uid):
    month = date.today().strftime("%Y-%m")
    used = qone("SELECT COUNT(*) n FROM ai_usage WHERE user_id=? AND created LIKE ?", (uid, month + "%"))["n"]
    free_left = max(0, FREE_AI_PER_MONTH - used)
    return {"free_left": free_left, "cost": 0 if free_left else AI_COIN_COST, "balance": coin_balance(uid)}


def ensure_can_generate(uid):
    q = ai_quota(uid)
    if q["cost"] and q["balance"] < q["cost"]:
        raise ServiceError(f"You have used your {FREE_AI_PER_MONTH} free AI generations this month. Another costs "
                           f"{q['cost']} EduCoins and you have {q['balance']}.")
    return q


def _record_ai_use(c, uid, kind, ref_id):
    month = date.today().strftime("%Y-%m")
    used = c.execute("SELECT COUNT(*) n FROM ai_usage WHERE user_id=? AND created LIKE ?", (uid, month + "%")).fetchone()["n"]
    cost = 0 if used < FREE_AI_PER_MONTH else AI_COIN_COST
    cur = c.execute("INSERT INTO ai_usage(user_id,kind,ref_id,coins,created) VALUES(?,?,?,?,?)", (uid, kind, ref_id, cost, now_iso()))
    if cost:
        _debit(c, uid, cost, f"Premium AI {kind}", "ai_use", cur.lastrowid)


def save_course(uid, subject, topic, level, objectives, duration, data):
    with tx() as c:
        cid = c.execute("INSERT INTO ai_courses(user_id,subject,topic,level,objectives,duration,data,created) VALUES(?,?,?,?,?,?,?,?)",
                        (uid, subject, topic, level, objectives, duration, json.dumps(data), now_iso())).lastrowid
        _record_ai_use(c, uid, "course", cid)
        return cid


def list_courses(uid):
    return qall("SELECT id,subject,topic,level,duration,created,data FROM ai_courses WHERE user_id=? ORDER BY id DESC", (uid,))


def get_course(uid, cid):
    r = qone("SELECT * FROM ai_courses WHERE id=? AND user_id=?", (cid, uid))
    if not r:
        raise ServiceError("Course not found.")
    return r, json.loads(r["data"])


def delete_course(uid, cid):
    with tx() as c:
        c.execute("DELETE FROM course_progress WHERE course_id=? AND user_id=?", (cid, uid))
        c.execute("DELETE FROM ai_courses WHERE id=? AND user_id=?", (cid, uid))


def course_progress(uid, cid):
    return {r["lesson_idx"]: r for r in qall("SELECT * FROM course_progress WHERE user_id=? AND course_id=?", (uid, cid))}


def set_lesson(uid, cid, idx, completed=None, quiz=None):
    get_course(uid, cid)  # ownership check
    with tx() as c:
        c.execute("INSERT OR IGNORE INTO course_progress(user_id,course_id,lesson_idx,updated) VALUES(?,?,?,?)", (uid, cid, idx, now_iso()))
        if completed is not None:
            c.execute("UPDATE course_progress SET completed=?,updated=? WHERE user_id=? AND course_id=? AND lesson_idx=?",
                      (int(completed), now_iso(), uid, cid, idx))
        if quiz is not None:
            c.execute("UPDATE course_progress SET quiz_score=?,quiz_total=?,updated=? WHERE user_id=? AND course_id=? AND lesson_idx=?",
                      (quiz[0], quiz[1], now_iso(), uid, cid, idx))


def course_percent(uid, row):
    n = len(json.loads(row["data"]).get("lessons", [])) or 1
    done = qone("SELECT COUNT(*) n FROM course_progress WHERE user_id=? AND course_id=? AND completed=1", (uid, row["id"]))["n"]
    return round(100 * done / n)


def save_mindmap(uid, topic, options, data):
    with tx() as c:
        mid = c.execute("INSERT INTO mind_maps(user_id,topic,options,data,created) VALUES(?,?,?,?,?)",
                        (uid, topic, json.dumps(options), json.dumps(data), now_iso())).lastrowid
        _record_ai_use(c, uid, "mind map", mid)
        return mid


def list_mindmaps(uid):
    return qall("SELECT id,topic,options,created FROM mind_maps WHERE user_id=? ORDER BY id DESC", (uid,))


def get_mindmap(uid, mid):
    r = qone("SELECT * FROM mind_maps WHERE id=? AND user_id=?", (mid, uid))
    if not r:
        raise ServiceError("Mind map not found.")
    return r, json.loads(r["data"])


def update_mindmap(uid, mid, data):
    """Save edits to a map without charging again (customisation of an existing map)."""
    with tx() as c:
        c.execute("UPDATE mind_maps SET data=? WHERE id=? AND user_id=?", (json.dumps(data), mid, uid))


def delete_mindmap(uid, mid):
    with tx() as c:
        c.execute("DELETE FROM mind_maps WHERE id=? AND user_id=?", (mid, uid))


# ---- Dashboards ---------------------------------------------------------------------------------
def student_dashboard(uid):
    q1 = lambda sql, p=(uid,): qone(sql, p)["n"]
    d = {}
    d["tutors"] = qall("""SELECT DISTINCT u.id,u.name FROM bookings b JOIN users u ON u.id=b.tutor_id WHERE b.student_id=?
                          AND b.status IN ('Accepted','Confirmed','Completed')""", (uid,))
    d["offline"] = q1("SELECT COUNT(*) n FROM bookings WHERE student_id=? AND mode='offline' AND status IN ('Pending','Accepted','Confirmed')")
    d["online"] = q1("SELECT COUNT(*) n FROM bookings WHERE student_id=? AND mode='online' AND status IN ('Pending','Accepted','Confirmed')")
    d["bookings"] = q1("SELECT COUNT(*) n FROM bookings WHERE student_id=?")
    d["completed"] = q1("SELECT COUNT(*) n FROM bookings WHERE student_id=? AND status='Completed'")
    d["upcoming"] = qall("""SELECT b.*,u.name AS other_name FROM bookings b JOIN users u ON u.id=b.tutor_id WHERE b.student_id=?
                            AND b.status='Confirmed' AND b.slot_start>=? ORDER BY b.slot_start LIMIT 10""", (uid, now_iso()))
    d["swap_upcoming"] = qall("""SELECT * FROM swap_sessions WHERE (teacher_id=? OR learner_id=?) AND status='Scheduled' ORDER BY scheduled LIMIT 10""", (uid, uid))
    d["swaps_active"] = q1("SELECT COUNT(*) n FROM swap_members WHERE user_id=? AND status='Accepted'")
    d["courses"] = list_courses(uid)
    d["mindmaps"] = q1("SELECT COUNT(*) n FROM mind_maps WHERE user_id=?")
    d["unread"] = q1("SELECT COUNT(*) n FROM notifications WHERE user_id=? AND read=0")
    d["wallet"] = wallet_summary(uid)
    return d
