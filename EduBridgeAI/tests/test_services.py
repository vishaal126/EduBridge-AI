import os, sys, tempfile, unittest, json
from datetime import datetime, timedelta, date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_tmp = tempfile.mkdtemp()
os.environ["EDUBRIDGE_DB"] = os.path.join(_tmp, "t.db")
import sqlite3
import services as S, pricing

BASE = """CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,email TEXT,phone TEXT,password TEXT,role TEXT,skills TEXT DEFAULT '',wants TEXT DEFAULT '',bio TEXT DEFAULT '',languages TEXT DEFAULT 'English',xp INTEGER DEFAULT 0,created TEXT);
CREATE TABLE notifications(id INTEGER PRIMARY KEY,user_id INTEGER,body TEXT,read INTEGER DEFAULT 0,created TEXT);
CREATE TABLE swaps(id INTEGER PRIMARY KEY,title TEXT,creator INTEGER,members TEXT,plan TEXT,status TEXT DEFAULT 'Open',created TEXT);
CREATE TABLE swap_members(swap_id INTEGER,user_id INTEGER,status TEXT DEFAULT 'Pending',joined TEXT,PRIMARY KEY(swap_id,user_id));
CREATE TABLE rooms(id INTEGER PRIMARY KEY,title TEXT,owner INTEGER,topic TEXT,scheduled TEXT,created TEXT,room_code TEXT);
CREATE TABLE room_members(room_id INTEGER,user_id INTEGER,UNIQUE(room_id,user_id));
CREATE TABLE doubts(id INTEGER PRIMARY KEY,student_id INTEGER,question TEXT,subject TEXT,language TEXT,urgent INTEGER DEFAULT 0,status TEXT DEFAULT 'Open',created TEXT);
CREATE TABLE doubt_replies(id INTEGER PRIMARY KEY,doubt_id INTEGER,tutor_id INTEGER,reply TEXT,created TEXT);
CREATE TABLE tutor_sessions(id INTEGER PRIMARY KEY,student_id INTEGER,tutor_id INTEGER,status TEXT);
CREATE TABLE requests(id INTEGER PRIMARY KEY,student INTEGER,tutor INTEGER,status TEXT);
CREATE TABLE challenges(id INTEGER PRIMARY KEY,reward INTEGER);
CREATE TABLE challenge_submissions(id INTEGER PRIMARY KEY,user_id INTEGER,challenge_id INTEGER,status TEXT);
"""
def user(c, name, role, **kw):
    return c.execute("INSERT INTO users(name,role,skills,wants) VALUES(?,?,?,?)", (name, role, kw.get("skills",""), kw.get("wants",""))).lastrowid

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c = sqlite3.connect(os.environ["EDUBRIDGE_DB"]); c.executescript(BASE)
        cls.stu = user(c, "Stu", "student", skills="Excel", wants="Python")
        cls.stu2 = user(c, "Stu2", "student", skills="Python", wants="Excel")
        cls.tut = user(c, "Tia", "tutor"); c.commit(); c.close()
        S.init_schema()
        S.save_tutor_profile(cls.tut, dict(subjects="Python", categories=["Programming & technology"], modes=["online","offline"],
            city="Hyderabad", hourly_price=500, weekly_price=1800, weekly_sessions=3, weekly_hours=1,
            monthly_price=6000, monthly_sessions=12, accepts_coins=True, coin_max_pct=50))
        for wd in range(7): S.add_availability(cls.tut, wd, "00:00", "23:00", "both")

    def slot(self, days=2, hour=10):
        return datetime.combine(date.today()+timedelta(days=days), datetime.min.time()).replace(hour=hour).isoformat(timespec="minutes")

    def fund(self, uid, n, ref):
        S.award_coins(uid, n, "test", "test", ref)

    def test_01_price_never_touched_and_guidance(self):
        p = S.get_profile(self.tut); self.assertEqual(p["hourly_price"], 500)
        g = S.price_guidance(p, "Hyderabad"); self.assertIn("online", g); self.assertTrue(g["offline"]["low"] < g["offline"]["high"])
        self.assertEqual(S.get_profile(self.tut)["hourly_price"], 500)

    def test_02_booking_coin_flow(self):
        self.fund(self.stu, 100, 1001)
        with self.assertRaises(S.ServiceError): S.create_booking(self.stu, self.tut, "online", "hourly", self.slot(2), coins=300)  # over 50% cap
        bid = S.create_booking(self.stu, self.tut, "online", "hourly", self.slot(2), coins=100)
        b = S.qone("SELECT * FROM bookings WHERE id=?", (bid,)); self.assertEqual((b["status"], b["amount_due"]), ("Pending", 400))
        self.assertEqual(S.coin_balance(self.stu), 0)
        with self.assertRaises(S.ServiceError): S.create_booking(self.stu, self.tut, "online", "hourly", self.slot(2))  # slot taken
        self.assertEqual(S.tutor_respond(self.tut, bid, False), "Rejected")
        self.assertEqual(S.coin_balance(self.stu), 100)           # refunded once
        with self.assertRaises(S.ServiceError): S.tutor_respond(self.tut, bid, False)

    def test_03_no_negative_balance(self):
        with self.assertRaises(S.ServiceError):
            S.create_booking(self.stu2, self.tut, "online", "hourly", self.slot(3), coins=200)  # not enough coins / cap
        self.fund(self.stu2, 10, 2001)
        with self.assertRaises(S.ServiceError): S.create_booking(self.stu2, self.tut, "online", "hourly", self.slot(3, 11), coins=250)
        self.assertGreaterEqual(S.coin_balance(self.stu2), 0)

    def test_04_payment_requires_backend_verification(self):
        bid = S.create_booking(self.stu, self.tut, "offline", "hourly", self.slot(4))
        with self.assertRaises(S.ServiceError): S.tutor_respond(self.tut, bid, True)           # needs location
        self.assertEqual(S.tutor_respond(self.tut, bid, True, location="Cafe X"), "Accepted")
        os.environ["RAZORPAY_KEY_ID"]="k"; os.environ["RAZORPAY_KEY_SECRET"]="s"
        calls = []
        def fake(method, path, payload=None):
            calls.append((method, path))
            if method == "POST": return {"id": "plink_1", "short_url": "https://rzp.io/x"}
            return fake.resp
        S._rzp = fake
        self.assertEqual(S.start_payment(self.stu, bid), "https://rzp.io/x")
        fake.resp = {"status": "created"}; self.assertEqual(S.verify_payment(self.stu, bid), "pending")
        self.assertEqual(S.qone("SELECT status FROM bookings WHERE id=?", (bid,))["status"], "Accepted")
        fake.resp = {"status": "paid", "amount_paid": 100, "currency": "INR"}
        with self.assertRaises(S.ServiceError): S.verify_payment(self.stu, bid)               # wrong amount
        self.assertEqual(S.qone("SELECT status FROM bookings WHERE id=?", (bid,))["status"], "Accepted")
        with self.assertRaises(S.ServiceError): S.verify_payment(self.stu2, bid)              # someone else
        fake.resp = {"status": "paid", "amount_paid": 50000, "currency": "INR", "payments": [{"payment_id": "pay_1"}]}
        self.assertEqual(S.verify_payment(self.stu, bid), "paid")
        b = S.qone("SELECT * FROM bookings WHERE id=?", (bid,)); self.assertEqual((b["status"], b["payment_status"]), ("Confirmed", "paid"))
        self.assertEqual(S.cancel_booking(self.stu, bid), True)  # refund_due flagged
        self.assertEqual(S.qone("SELECT payment_status FROM bookings WHERE id=?", (bid,))["payment_status"], "refund_due")

    def test_05_free_booking_and_complete_review(self):
        S.save_tutor_profile(self.tut, dict(subjects="Python", categories=[], modes=["online"], city="Hyderabad", hourly_price=100,
            accepts_coins=True, coin_max_pct=100))
        self.fund(self.stu, 100, 3001)
        bid = S.create_booking(self.stu, self.tut, "online", "hourly", self.slot(5), coins=100)
        self.assertEqual(S.tutor_respond(self.tut, bid, True), "Confirmed")
        b = S.qone("SELECT * FROM bookings WHERE id=?", (bid,)); self.assertTrue(b["meeting_url"].startswith("https://meet.jit.si/"))
        with self.assertRaises(S.ServiceError): S.complete_booking(self.tut, bid)   # before start time
        S.qall  # force start time into the past
        c = sqlite3.connect(os.environ["EDUBRIDGE_DB"]); c.execute("UPDATE bookings SET slot_start=? WHERE id=?", ((datetime.now()-timedelta(hours=2)).isoformat(timespec="minutes"), bid)); c.commit(); c.close()
        with self.assertRaises(S.ServiceError): S.complete_booking(self.stu, bid)
        S.complete_booking(self.tut, bid); S.add_review(self.stu, bid, 5, "great")
        with self.assertRaises(S.ServiceError): S.add_review(self.stu, bid, 4, "again")
        self.assertEqual(S.tutor_rating(self.tut), (5.0, 1))
        self.assertEqual(S.tutor_earnings(self.tut)["earned_coins"], 100)

    def test_06_duplicate_rewards_blocked(self):
        self.assertTrue(S.award_coins(self.stu, 5, "x", "challenge", 77)); self.assertFalse(S.award_coins(self.stu, 5, "x", "challenge", 77))

    def test_07_swap_flow_and_rewards(self):
        sid = S.swap_invite(self.stu, self.stu2, "Py+Excel", "Excel", "Python", "plan")
        with self.assertRaises(S.ServiceError): S.swap_invite(self.stu, self.stu2, "again", "a", "b", "")
        with self.assertRaises(S.ServiceError): S.swap_respond(self.tut, sid, True)
        S.swap_respond(self.stu2, sid, True)
        room = S.swap_room(self.stu, sid)
        self.assertEqual(len(S.qall("SELECT * FROM room_members WHERE room_id=?", (room["id"],))), 2)
        with self.assertRaises(S.ServiceError): S.swap_room(self.tut, sid)
        when = (datetime.now()+timedelta(hours=1)).isoformat(timespec="minutes")
        ss = S.schedule_swap_session(self.stu, sid, self.stu2, "Pandas basics", when, 30)
        with self.assertRaises(S.ServiceError): S.confirm_swap_session(self.stu, ss)   # not started yet
        c = sqlite3.connect(os.environ["EDUBRIDGE_DB"]); c.execute("UPDATE swap_sessions SET scheduled=? WHERE id=?", ((datetime.now()-timedelta(hours=1)).isoformat(timespec="minutes"), ss)); c.commit(); c.close()
        b0 = S.coin_balance(self.stu2)
        self.assertIn("Waiting", S.confirm_swap_session(self.stu, ss))
        self.assertEqual(S.coin_balance(self.stu2), b0)
        self.assertEqual(S.wallet_summary(self.stu)["pending"], S.SWAP_LEARN_COINS)
        S.confirm_swap_session(self.stu2, ss)
        self.assertEqual(S.coin_balance(self.stu2), b0 + S.SWAP_TEACH_COINS)
        with self.assertRaises(S.ServiceError): S.confirm_swap_session(self.stu2, ss)  # no double reward
        self.assertEqual(S.coin_balance(self.stu2), b0 + S.SWAP_TEACH_COINS)
        ids = {r["id"] for r in S.message_contacts(self.stu)}; self.assertIn(self.stu2, ids)

    def test_08_matches_and_ai_quota(self):
        m = S.swap_matches(S.get_user(self.stu)); self.assertTrue(m and m[0]["mutual"])
        uid = self.stu2
        for i in range(S.FREE_AI_PER_MONTH): S.save_mindmap(uid, f"t{i}", {}, {"label": "x"})
        q = S.ai_quota(uid); self.assertEqual((q["free_left"], q["cost"]), (0, S.AI_COIN_COST))
        bal = S.coin_balance(uid)
        if bal < S.AI_COIN_COST:
            with self.assertRaises(S.ServiceError): S.ensure_can_generate(uid)
            with self.assertRaises(S.ServiceError): S.save_mindmap(uid, "paid", {}, {"label": "x"})
        self.assertEqual(S.coin_balance(uid), bal)  # failed paid generation costs nothing

    def test_09_course_ownership(self):
        cid = S.save_course(self.stu, "CS", "Py", "Beginner", "", "4 weeks", {"lessons": [{"title": "a"}, {"title": "b"}]})
        S.set_lesson(self.stu, cid, 0, completed=True)
        self.assertEqual(S.course_percent(self.stu, S.get_course(self.stu, cid)[0]), 50)
        with self.assertRaises(S.ServiceError): S.get_course(self.stu2, cid)
        with self.assertRaises(S.ServiceError): S.set_lesson(self.stu2, cid, 0, completed=True)

    def test_10_search_and_pricing(self):
        r = S.search_tutors("online", subject_q="python", student=S.get_user(self.stu)); self.assertEqual(len(r), 1)
        self.assertEqual(len(S.search_tutors("offline", subject_q="python")), 0)  # profile now online-only
        self.assertAlmostEqual(pricing.distance_km("Hyderabad", "Warangal"), 125, delta=15)
        self.assertEqual(pricing.compare_to_market(100, 200, 300), "below estimated range")

if __name__ == "__main__": unittest.main(verbosity=2)


class RowCopyTests(unittest.TestCase):
    """Regression: Streamlit deep-copies selectbox options; sqlite3.Row broke this on Python 3.14."""

    def test_rows_are_copyable_and_still_indexable(self):
        import copy, pickle
        rows = S.qall("SELECT 1 AS a, 'x' AS b")
        r = rows[0]
        self.assertEqual((r["a"], r[0], r["b"], r[1]), (1, 1, "x", "x"))
        self.assertEqual(copy.deepcopy(rows), rows)
        self.assertEqual(pickle.loads(pickle.dumps(r)), r)
