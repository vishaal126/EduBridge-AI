import os, sys, tempfile, unittest, time, json, base64, hashlib
from datetime import timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_tmp = tempfile.mkdtemp()
os.environ["EDUBRIDGE_DB"] = os.path.join(_tmp, "auth.db")
os.environ.update(GOOGLE_CLIENT_ID="cid.apps.googleusercontent.com", GOOGLE_CLIENT_SECRET="gsecret",
                  GITHUB_CLIENT_ID="ghid", GITHUB_CLIENT_SECRET="ghsecret", APP_BASE_URL="http://localhost:8501",
                  SMTP_HOST="smtp.example.test", SMTP_FROM="EduBridge <no-reply@example.test>")
import sqlite3
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
import services as S
import auth_service as A

# The users table exactly as the ORIGINAL app.py creates it (+ the columns services.init_schema adds).
USERS = """CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,email TEXT UNIQUE,phone TEXT UNIQUE,password TEXT,role TEXT,
skills TEXT DEFAULT '',wants TEXT DEFAULT '',bio TEXT DEFAULT '',languages TEXT DEFAULT 'English',xp INTEGER DEFAULT 0,created TEXT,
city TEXT DEFAULT '',state TEXT DEFAULT '',country TEXT DEFAULT 'India');"""

# A hash produced by the ORIGINAL app.py code, to prove old accounts still verify.
def legacy_pw_hash(p, s="a" * 32):
    return s + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), s.encode(), 100000).hex()

GOOD = "Sup3r-Secret!"


def add_user(name, email=None, phone=None, pw="x", role="student"):
    c = sqlite3.connect(os.environ["EDUBRIDGE_DB"])
    uid = c.execute("INSERT INTO users(name,email,phone,password,role) VALUES(?,?,?,?,?)",
                    (name, email, phone, legacy_pw_hash(pw) if pw else A.UNUSABLE_PASSWORD, role)).lastrowid
    c.commit(); c.close(); return uid


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c = sqlite3.connect(os.environ["EDUBRIDGE_DB"]); c.executescript(USERS); c.close()
        A.init_auth_schema(); A.init_auth_schema()          # idempotent
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.legacy_email = add_user("Old Timer", "old@example.com", "+91 98765 43210", "oldpass1")
        cls.tutor = add_user("Tia", "tia@example.com", None, "TutorPass1!", "tutor")

    def setUp(self):
        c = sqlite3.connect(os.environ["EDUBRIDGE_DB"]); c.execute("DELETE FROM auth_attempts"); c.commit(); c.close()
        A._jwks_client = None

    # ---- passwords / login --------------------------------------------------------------------
    def test_legacy_hash_format_verifies(self):
        self.assertTrue(A.pw_ok("oldpass1", legacy_pw_hash("oldpass1")))
        self.assertFalse(A.pw_ok("nope", legacy_pw_hash("oldpass1")))
        self.assertFalse(A.pw_ok("anything", A.UNUSABLE_PASSWORD))
        self.assertEqual(A.pw_hash("abc", "s" * 8), legacy_pw_hash("abc", "s" * 8))

    def test_existing_user_logs_in_by_email_and_phone(self):
        for ident in ("OLD@example.com", " old@example.com ", "+91 98765 43210", "919876543210", "+919876543210"):
            self.assertEqual(A.login(ident, "oldpass1", "Student")["id"], self.legacy_email, ident)

    def test_wrong_password_unknown_user_and_role(self):
        with self.assertRaises(A.AuthError) as e: A.login("old@example.com", "bad", "student")
        self.assertEqual(str(e.exception), A.INVALID_LOGIN)
        with self.assertRaises(A.AuthError) as e2: A.login("ghost@example.com", "whatever1", "student")
        self.assertEqual(str(e2.exception), A.INVALID_LOGIN)
        with self.assertRaises(A.AuthError) as e3: A.login("tia@example.com", "TutorPass1!", "student")
        self.assertIn("tutor account", str(e3.exception))
        self.assertEqual(A.login("tia@example.com", "TutorPass1!", "tutor")["id"], self.tutor)

    def test_lockout_after_repeated_failures(self):
        for _ in range(5):
            with self.assertRaises(A.AuthError): A.login("old@example.com", "bad", "student")
        with self.assertRaises(A.AuthError) as e: A.login("old@example.com", "oldpass1", "student")
        self.assertIn("Too many attempts", str(e.exception))

    # ---- registration -------------------------------------------------------------------------
    def test_register_creates_real_row_and_hashes(self):
        u = A.register("New Person", "New@Example.com", GOOD, GOOD, "tutor", True, {"city": "Hyderabad"})
        row = S.get_user(u["id"])
        self.assertEqual((row["email"], row["role"], row["xp"], row["languages"], row["city"]),
                         ("new@example.com", "tutor", 0, "English", "Hyderabad"))
        self.assertNotIn(GOOD, row["password"]); self.assertTrue(A.pw_ok(GOOD, row["password"]))
        self.assertEqual(A.login("new@example.com", GOOD, "tutor")["id"], u["id"])

    def test_register_validation_and_duplicates(self):
        bad = [("A", "a@b.co", GOOD, GOOD, True, "name"), ("Alice", "nope", GOOD, GOOD, True, "email"),
               ("Alice", "al@b.co", "short", "short", True, "password"), ("Alice", "al@b.co", "alllowercase1", "alllowercase1", True, "password"),
               ("Alice", "al@b.co", "password123", "password123", True, "password"),
               ("Alice", "al@b.co", GOOD, GOOD + "x", True, "confirm"), ("Alice", "al@b.co", GOOD, GOOD, False, "terms")]
        for name, em, pw, cf, terms, field in bad:
            with self.assertRaises(A.AuthError) as e: A.register(name, em, pw, cf, "student", terms)
            self.assertEqual(e.exception.field, field)
        A.register("Dup", "dup@example.com", GOOD, GOOD, "student", True)
        with self.assertRaises(A.AuthError) as e: A.register("Dup2", "DUP@example.com", GOOD, GOOD, "student", True)
        self.assertEqual(e.exception.field, "email")
        with self.assertRaises(A.AuthError): A.register("X Y", "xy@example.com", GOOD, GOOD, "admin", True)

    # ---- sessions -----------------------------------------------------------------------------
    def test_sessions_hashed_expire_and_revoke(self):
        t = A.create_session(self.tutor, remember=True)
        raw = sqlite3.connect(os.environ["EDUBRIDGE_DB"]).execute("SELECT token_hash,persistent FROM auth_sessions").fetchall()
        self.assertTrue(all(t != r[0] for r in raw))
        self.assertEqual(A.session_user(t)["id"], self.tutor); self.assertTrue(A.session_valid(t, self.tutor))
        self.assertFalse(A.session_valid(t, self.legacy_email)); self.assertIsNone(A.session_user("garbage"))
        A.revoke_session(t); self.assertIsNone(A.session_user(t))
        t2 = A.create_session(self.tutor)
        c = sqlite3.connect(os.environ["EDUBRIDGE_DB"]); c.execute("UPDATE auth_sessions SET expires='2000-01-01T00:00:00+00:00'"); c.commit(); c.close()
        self.assertIsNone(A.session_user(t2))

    # ---- password reset -----------------------------------------------------------------------
    def test_reset_flow(self):
        uid = A.register("Resetter", "reset@example.com", GOOD, GOOD, "student", True)["id"]
        sess = A.create_session(uid)
        sent = {}
        with mock.patch.object(A, "send_email", lambda to, subj, text, html=None: sent.update(to=to, text=text)):
            self.assertEqual(A.request_password_reset("reset@example.com"), A.GENERIC_RESET_MSG)
            self.assertEqual(A.request_password_reset("nobody@example.com"), A.GENERIC_RESET_MSG)   # no enumeration
        self.assertEqual(sent["to"], "reset@example.com")
        token = sent["text"].split("?reset=")[1].split()[0]
        stored = [r[0] for r in sqlite3.connect(os.environ["EDUBRIDGE_DB"]).execute("SELECT token_hash FROM password_resets")]
        self.assertNotIn(token, stored); self.assertEqual(A.validate_reset_token(token), uid)
        with self.assertRaises(A.AuthError): A.reset_password(token, "weak", "weak")
        with self.assertRaises(A.AuthError): A.reset_password(token, "N3w-Password!", "different")
        A.reset_password(token, "N3w-Password!", "N3w-Password!")
        self.assertEqual(A.login("reset@example.com", "N3w-Password!", "student")["id"], uid)
        with self.assertRaises(A.AuthError): A.login("reset@example.com", GOOD, "student")
        self.assertIsNone(A.session_user(sess))                      # old sessions revoked
        with self.assertRaises(A.AuthError): A.reset_password(token, "An0ther-Pass!", "An0ther-Pass!")  # single use
        self.assertIsNone(A.validate_reset_token(token))

    def test_reset_expiry_and_not_configured(self):
        uid = add_user("Exp", "exp@example.com", None, "pw123456")
        tok = "tok-" + "x" * 30
        c = sqlite3.connect(os.environ["EDUBRIDGE_DB"])
        c.execute("INSERT INTO password_resets VALUES(?,?,?,?,0)", (A._h(tok), uid, "2000-01-01T00:00:00+00:00", "2000-01-01T00:30:00+00:00")); c.commit(); c.close()
        with self.assertRaises(A.AuthError): A.reset_password(tok, GOOD, GOOD)
        with mock.patch.dict(os.environ, {"SMTP_HOST": ""}):
            with self.assertRaises(A.AuthError) as e: A.request_password_reset("exp@example.com")
            self.assertIn("isn’t set up", str(e.exception))

    def test_reset_email_failure_is_reported(self):
        add_user("Mailfail", "mf@example.com", None, "pw123456")
        with mock.patch.object(A, "send_email", side_effect=OSError("smtp down")):
            with self.assertRaises(A.AuthError) as e: A.request_password_reset("mf@example.com")
        self.assertIn("couldn’t send", str(e.exception))

    # ---- OAuth --------------------------------------------------------------------------------
    BIND = "b" * 32

    def _start(self, provider="google", role="student"):
        url = A.begin_oauth(provider, role, self.BIND)
        q = dict(__import__("urllib.parse").parse.parse_qsl(url.split("?", 1)[1]))
        return url, q

    def _id_token(self, q, **over):
        now = int(time.time())
        claims = dict(iss="https://accounts.google.com", aud=os.environ["GOOGLE_CLIENT_ID"], sub="g-123", email="gnew@example.com",
                      email_verified=True, name="Gina G", iat=now, exp=now + 600, nonce=q["nonce"])
        claims.update(over)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})

    def _complete(self, q, id_token, binding=None, **extra):
        resp = mock.Mock(status_code=200); resp.json.return_value = {"id_token": id_token}
        with mock.patch.object(A.requests, "post", return_value=resp), \
             mock.patch.object(A, "_google_signing_key", return_value=self.key.public_key()):
            return A.complete_oauth(dict(code="authcode", state=q["state"], **extra), binding or self.BIND)

    def test_begin_oauth_url_and_config(self):
        url, q = self._start()
        self.assertTrue(url.startswith(A.GOOGLE_AUTH)); self.assertEqual(q["code_challenge_method"], "S256")
        self.assertEqual((q["response_type"], q["scope"], q["redirect_uri"]), ("code", "openid email profile", "http://localhost:8501"))
        self.assertTrue(q["state"] and q["nonce"]); self.assertNotIn("gsecret", url)
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": ""}):
            with self.assertRaises(A.AuthError) as e: A.begin_oauth("google", "student", self.BIND)
            self.assertIn("isn’t configured", str(e.exception))

    def test_google_new_user_created_and_relogin(self):
        _, q = self._start(role="tutor")
        r = self._complete(q, self._id_token(q))
        self.assertEqual((r["status"], r["created"], r["user"]["role"], r["user"]["email"]), ("ok", True, "tutor", "gnew@example.com"))
        self.assertEqual(r["user"]["password"], A.UNUSABLE_PASSWORD)
        with self.assertRaises(A.AuthError): A.login("gnew@example.com", A.UNUSABLE_PASSWORD, "tutor")
        _, q2 = self._start(role="tutor")
        r2 = self._complete(q2, self._id_token(q2))
        self.assertEqual((r2["created"], r2["user"]["id"]), (False, r["user"]["id"]))
        n = sqlite3.connect(os.environ["EDUBRIDGE_DB"]).execute("SELECT COUNT(*) FROM auth_identities WHERE subject='g-123'").fetchone()[0]
        self.assertEqual(n, 1)                                          # no duplicate links
        _, q3 = self._start(role="student")
        with self.assertRaises(A.AuthError) as e: self._complete(q3, self._id_token(q3))
        self.assertIn("tutor account", str(e.exception))

    def test_state_single_use_expiry_binding_and_errors(self):
        _, q = self._start()
        tok = self._id_token(q)
        with self.assertRaises(A.AuthError) as e: self._complete(q, tok, binding="z" * 32)
        self.assertIn("different browser", str(e.exception))
        with self.assertRaises(A.AuthError): self._complete(q, tok)     # already consumed even by the failed attempt
        with self.assertRaises(A.AuthError): A.complete_oauth(dict(code="c", state="forged"), self.BIND)
        _, q2 = self._start()
        c = sqlite3.connect(os.environ["EDUBRIDGE_DB"]); c.execute("UPDATE oauth_states SET expires='2000-01-01T00:00:00+00:00'"); c.commit(); c.close()
        with self.assertRaises(A.AuthError): self._complete(q2, self._id_token(q2))
        _, q3 = self._start()
        with self.assertRaises(A.AuthError) as e: A.complete_oauth(dict(state=q3["state"], error="access_denied"), self.BIND)
        self.assertIn("cancelled", str(e.exception))

    def test_id_token_claims_are_enforced(self):
        cases = {"nonce": dict(nonce="wrong"), "audience": dict(aud="other-client"), "issuer": dict(iss="https://evil.test"),
                 "expired": dict(exp=int(time.time()) - 3600, iat=int(time.time()) - 7200), "unverified email": dict(email_verified=False)}
        for label, over in cases.items():
            _, q = self._start()
            with self.assertRaises(A.AuthError, msg=label): self._complete(q, self._id_token(q, **over))
        _, q = self._start()
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)       # wrong signing key
        forged = jwt.encode(dict(iss="https://accounts.google.com", aud=os.environ["GOOGLE_CLIENT_ID"], sub="x", email="f@e.co",
                                 email_verified=True, iat=int(time.time()), exp=int(time.time()) + 60, nonce=q["nonce"]), other, algorithm="RS256")
        with self.assertRaises(A.AuthError): self._complete(q, forged)
        _, q = self._start()
        none_tok = jwt.encode(dict(iss="https://accounts.google.com", aud=os.environ["GOOGLE_CLIENT_ID"], sub="x", email="f@e.co", email_verified=True, nonce=q["nonce"]), None, algorithm="none")
        with self.assertRaises(A.AuthError): self._complete(q, none_tok)

    def test_email_match_with_password_account_requires_password_to_link(self):
        uid = add_user("Pwuser", "pwuser@example.com", None, "pwuser-pass1")
        _, q = self._start()
        r = self._complete(q, self._id_token(q, sub="g-link", email="pwuser@example.com"))
        self.assertEqual(r["status"], "link")                           # NOT auto-linked
        n = sqlite3.connect(os.environ["EDUBRIDGE_DB"]).execute("SELECT COUNT(*) FROM auth_identities WHERE user_id=?", (uid,)).fetchone()[0]
        self.assertEqual(n, 0)
        with self.assertRaises(A.AuthError): A.confirm_link(r["pending"], "wrong")
        self.assertEqual(A.confirm_link(r["pending"], "pwuser-pass1")["id"], uid)
        _, q2 = self._start()
        r2 = self._complete(q2, self._id_token(q2, sub="g-link", email="pwuser@example.com"))
        self.assertEqual((r2["status"], r2["user"]["id"]), ("ok", uid))
        _, q3 = self._start()                                           # different Google account, same email → refused
        with self.assertRaises(A.AuthError): self._complete(q3, self._id_token(q3, sub="g-other", email="pwuser@example.com"))

    def test_github_flow_uses_verified_primary_email(self):
        _, q = self._start("github")
        def post(url, **kw):
            r = mock.Mock(status_code=200); r.json.return_value = {"access_token": "gho_x"}; return r
        def get(url, **kw):
            r = mock.Mock(status_code=200)
            r.json.return_value = ({"id": 4242, "login": "octo", "name": None, "email": "public-unverified@x.co"} if url.endswith("/user") else
                                   [{"email": "octo-unverified@x.co", "primary": False, "verified": False},
                                    {"email": "Octo@Example.org", "primary": True, "verified": True}]); return r
        with mock.patch.object(A.requests, "post", post), mock.patch.object(A.requests, "get", get):
            r = A.complete_oauth(dict(code="c", state=q["state"]), self.BIND)
        self.assertEqual((r["user"]["email"], r["user"]["name"], r["created"]), ("octo@example.org", "octo", True))
        _, q2 = self._start("github")
        def get2(url, **kw):
            r = mock.Mock(status_code=200); r.json.return_value = ({"id": 7, "login": "n"} if url.endswith("/user") else [{"email": "n@x.co", "primary": True, "verified": False}]); return r
        with mock.patch.object(A.requests, "post", post), mock.patch.object(A.requests, "get", get2):
            with self.assertRaises(A.AuthError) as e: A.complete_oauth(dict(code="c", state=q2["state"]), self.BIND)
        self.assertIn("no verified email", str(e.exception))


if __name__ == "__main__":
    unittest.main()
