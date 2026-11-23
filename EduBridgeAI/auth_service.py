"""EduBridge authentication backend.

No Streamlit imports, so everything here is unit-testable. It works on the EXISTING `users` table and keeps the
existing PBKDF2 password format (`salt$hex`), so every existing account keeps working. Only NEW tables are added
(see AUTH_SCHEMA); no existing table or column is changed or removed.

What lives here: password login, registration, server-side sessions (hashed tokens), password-reset tokens + email,
Google OpenID Connect and GitHub OAuth (state, nonce, PKCE, browser binding, ID-token verification), safe account
linking and rate limiting.
"""
import hashlib, hmac, logging, os, re, secrets, smtplib, ssl, sqlite3, urllib.parse
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import requests

import services as S

log = logging.getLogger("edubridge.auth")

SESSION_TTL = timedelta(hours=12)        # login without "Remember me"
REMEMBER_TTL = timedelta(days=30)        # login with "Remember me"
RESET_TTL = timedelta(minutes=30)
OAUTH_TTL = timedelta(minutes=10)
PBKDF2_ITERS = 100_000                   # must stay identical to the original app.py so old hashes verify
UNUSABLE_PASSWORD = "!oauth"             # stored for social-only accounts; never matches any password
INVALID_LOGIN = "Email / mobile number or password is incorrect."
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
COMMON_PASSWORDS = {"password", "password1", "password123", "12345678", "123456789", "qwertyuiop", "iloveyou",
                    "11111111", "abcd1234", "letmein123", "welcome123", "admin123", "passw0rd", "qwerty123"}


class AuthError(S.ServiceError):
    """A user-presentable authentication error. `field` names the form field to highlight (optional)."""

    def __init__(self, message, field=None, fatal=False):
        super().__init__(message)
        self.field = field
        self.fatal = fatal       # True: the reset/link step can't continue (token expired or used)


# ---- configuration (environment variables first, then Streamlit secrets) ------------------------
_secret_provider = None


def set_secret_provider(fn):
    global _secret_provider
    _secret_provider = fn


def cfg(key, default=""):
    v = os.environ.get(key)
    if v and v.strip():
        return v.strip()
    if _secret_provider:
        try:
            v = _secret_provider(key)
        except Exception:
            v = None
        if v and str(v).strip():
            return str(v).strip()
    return default


def base_url():
    return cfg("APP_BASE_URL", "http://localhost:8501").rstrip("/")


# ---- time / hashing helpers ---------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)


def _ts(dt):
    return dt.isoformat(timespec="seconds")


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def pw_hash(p, s=None):
    s = s or secrets.token_hex(16)
    return s + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), s.encode(), PBKDF2_ITERS).hex()


def pw_ok(p, h):
    try:
        s, d = h.split("$", 1)
        return hmac.compare_digest(pw_hash(p, s).split("$", 1)[1], d)
    except Exception:
        return False


# ---- schema (new tables only) -------------------------------------------------------------------
AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_identities(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, provider TEXT NOT NULL, subject TEXT NOT NULL,
  email TEXT, email_verified INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL,
  UNIQUE(provider, subject), UNIQUE(user_id, provider));
CREATE TABLE IF NOT EXISTS oauth_states(
  state_hash TEXT PRIMARY KEY, provider TEXT NOT NULL, nonce TEXT, verifier TEXT, binding_hash TEXT,
  role TEXT NOT NULL, created TEXT NOT NULL, expires TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS auth_sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created TEXT NOT NULL, expires TEXT NOT NULL,
  persistent INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS password_resets(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created TEXT NOT NULL, expires TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS auth_attempts(
  id INTEGER PRIMARY KEY, bucket TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_bucket ON auth_attempts(bucket, created);
"""


def init_auth_schema():
    """Create the auth tables. Safe on every start; existing data is never touched. Needs `users` to exist."""
    c = S.connect()
    try:
        c.executescript(AUTH_SCHEMA)
    finally:
        c.close()


# ---- validation ---------------------------------------------------------------------------------
def pw_strength(pw):
    """0-4, mirrors the strength meter in the login UI."""
    if not pw:
        return 0
    s = 0
    s += len(pw) >= 8
    s += bool(re.search(r"[a-z]", pw) and re.search(r"[A-Z]", pw))
    s += bool(re.search(r"\d", pw))
    s += bool(re.search(r"[^\w\s]", pw) or len(pw) >= 14)
    return max(1, s)


def check_password_policy(pw, field="password"):
    if len(pw) < 8:
        raise AuthError("Use at least 8 characters.", field)
    if len(pw) > 128:
        raise AuthError("Use at most 128 characters.", field)
    if pw.lower() in COMMON_PASSWORDS:
        raise AuthError("That password is too common. Choose a different one.", field)
    if pw_strength(pw) < 3:
        raise AuthError("Mix upper and lower case letters with a number or symbol.", field)


def norm_email(e):
    return (e or "").strip().lower()


def _role(role):
    r = (role or "").strip().lower()
    if r not in ("student", "tutor"):
        raise AuthError("Choose Student or Tutor.")
    return r


def public_user(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "phone": u["phone"], "role": u["role"]}


# ---- rate limiting ------------------------------------------------------------------------------
def _rate_check(bucket, limit, window_min):
    since = _ts(_now() - timedelta(minutes=window_min))
    n = S.qone("SELECT COUNT(*) n FROM auth_attempts WHERE bucket=? AND created>=?", (bucket, since))["n"]
    if n >= limit:
        raise AuthError("Too many attempts. Please wait a few minutes and try again.")


def _rate_hit(bucket):
    with S.tx() as c:
        c.execute("INSERT INTO auth_attempts(bucket,created) VALUES(?,?)", (bucket, _ts(_now())))
        c.execute("DELETE FROM auth_attempts WHERE created<?", (_ts(_now() - timedelta(days=1)),))


def _rate_clear(bucket):
    with S.tx() as c:
        c.execute("DELETE FROM auth_attempts WHERE bucket=?", (bucket,))


# ---- user lookup --------------------------------------------------------------------------------
def _digits(s):
    return re.sub(r"\D", "", s or "")


def find_user_by_ident(ident):
    """Same lookup as the original sign-in (email, case-insensitive, or exact phone) plus a tolerant phone match."""
    ident = (ident or "").strip()
    u = S.qone("SELECT * FROM users WHERE lower(email)=lower(?) OR phone=?", (ident, ident))
    if u or "@" in ident:
        return u
    d = _digits(ident)
    if 8 <= len(d) <= 15:
        return S.qone("SELECT * FROM users WHERE phone IS NOT NULL AND replace(replace(replace(replace("
                      "phone,' ',''),'-',''),'+',''),'(','')=?", (d,))
    return None


# ---- password login / registration --------------------------------------------------------------
def login(ident, password, role):
    role = _role(role)
    ident = (ident or "").strip()
    password = password or ""
    if not ident:
        raise AuthError("Enter your email or mobile number.", "identity")
    if not password:
        raise AuthError("Enter your password.", "password")
    if len(ident) > 254 or len(password) > 256:
        raise AuthError(INVALID_LOGIN, "password")
    bucket = "login:" + ident.lower()
    _rate_check(bucket, 5, 15)
    u = find_user_by_ident(ident)
    if u:
        ok = pw_ok(password, u["password"] or "")
    else:
        pw_hash(password)          # equalise timing so unknown accounts are not distinguishable
        ok = False
    if not ok:
        _rate_hit(bucket)
        raise AuthError(INVALID_LOGIN, "password")
    if u["role"] != role:
        raise AuthError(f"This is a {u['role']} account. Switch to “I’m a {u['role']}”.", "identity")
    _rate_clear(bucket)
    return u


def _clean(s, n):
    return re.sub(r"\s+", " ", (s or "").strip())[:n]


def register(name, email, password, confirm, role, terms, profile=None):
    role = _role(role)
    name = _clean(name, 80)
    email = norm_email(email)
    password = password or ""
    if len(name) < 2:
        raise AuthError("Enter your full name.", "name")
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.", "email")
    check_password_policy(password)
    if password != (confirm or ""):
        raise AuthError("Passwords do not match.", "confirm")
    if not terms:
        raise AuthError("Accept the terms to continue.", "terms")
    p = profile or {}
    vals = (name, email, pw_hash(password), role, _clean(p.get("skills"), 300), _clean(p.get("wants"), 300),
            "", "English", _ts_local(), _clean(p.get("city"), 80), _clean(p.get("state"), 80),
            _clean(p.get("country"), 80) or "India")
    try:
        with S.tx() as c:
            if c.execute("SELECT 1 FROM users WHERE lower(email)=?", (email,)).fetchone():
                raise AuthError("An account with this email already exists. Sign in instead.", "email")
            uid = c.execute("INSERT INTO users(name,email,phone,password,role,skills,wants,bio,languages,xp,created,city,state,country)"
                            " VALUES(?,?,NULL,?,?,?,?,?,?,0,?,?,?,?)", vals).lastrowid
    except sqlite3.IntegrityError:
        raise AuthError("An account with this email already exists. Sign in instead.", "email")
    return S.get_user(uid)


def _ts_local():
    return datetime.now().isoformat(timespec="minutes")   # same format the original app stores in users.created


# ---- sessions -----------------------------------------------------------------------------------
def create_session(uid, remember=False):
    token = secrets.token_urlsafe(32)
    now = _now()
    with S.tx() as c:
        c.execute("INSERT INTO auth_sessions(token_hash,user_id,created,expires,persistent) VALUES(?,?,?,?,?)",
                  (_h(token), uid, _ts(now), _ts(now + (REMEMBER_TTL if remember else SESSION_TTL)), int(bool(remember))))
        c.execute("DELETE FROM auth_sessions WHERE expires<?", (_ts(now),))
    return token


def session_user(token):
    if not token or len(token) > 200:
        return None
    r = S.qone("SELECT user_id FROM auth_sessions WHERE token_hash=? AND expires>?", (_h(token), _ts(_now())))
    return S.get_user(r["user_id"]) if r else None


def session_valid(token, uid):
    if not token or len(token) > 200:
        return False
    return bool(S.qone("SELECT 1 FROM auth_sessions WHERE token_hash=? AND user_id=? AND expires>?",
                       (_h(token), uid, _ts(_now()))))


def revoke_session(token):
    if token:
        with S.tx() as c:
            c.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_h(token),))


def revoke_all_sessions(uid):
    with S.tx() as c:
        c.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))


# ---- password reset -----------------------------------------------------------------------------
def smtp_configured():
    return bool(cfg("SMTP_HOST") and cfg("SMTP_FROM"))


def send_email(to, subject, text, html=None):
    host, port = cfg("SMTP_HOST"), int(cfg("SMTP_PORT", "587"))
    user, pwd = cfg("SMTP_USERNAME"), cfg("SMTP_PASSWORD")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg("SMTP_FROM"), to, subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15, context=ctx) as s:
            if user:
                s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.ehlo()
            if cfg("SMTP_STARTTLS", "true").lower() != "false":
                s.starttls(context=ctx)
                s.ehlo()
            if user:
                s.login(user, pwd)
            s.send_message(msg)


GENERIC_RESET_MSG = "If an account exists for that email, a reset link is on its way. It expires in 30 minutes."


def request_password_reset(email):
    email = norm_email(email)
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.", "email")
    if not smtp_configured():
        raise AuthError("Password reset email isn’t set up on this server yet. Ask the administrator to configure SMTP_HOST and SMTP_FROM (see README).")
    _rate_check("reset:" + email, 3, 60)
    _rate_check("reset:all", 40, 60)
    _rate_hit("reset:" + email)
    _rate_hit("reset:all")
    u = S.qone("SELECT id,name,email FROM users WHERE lower(email)=?", (email,))
    if not u:
        return GENERIC_RESET_MSG
    token = secrets.token_urlsafe(32)
    now = _now()
    with S.tx() as c:
        c.execute("UPDATE password_resets SET used=1 WHERE user_id=? AND used=0", (u["id"],))
        c.execute("INSERT INTO password_resets(token_hash,user_id,created,expires,used) VALUES(?,?,?,?,0)",
                  (_h(token), u["id"], _ts(now), _ts(now + RESET_TTL)))
    link = f"{base_url()}/?reset={urllib.parse.quote(token)}"
    first = (u["name"] or "there").split()[0]
    text = (f"Hi {first},\n\nWe received a request to reset your EduBridge AI password.\n\n{link}\n\n"
            f"This link works once and expires in 30 minutes. If you didn't ask for it, you can ignore this email; "
            f"your password stays unchanged.\n")
    html = (f"<p>Hi {first},</p><p>We received a request to reset your EduBridge AI password.</p>"
            f"<p><a href=\"{link}\">Choose a new password</a></p>"
            f"<p>This link works once and expires in 30 minutes. If you didn't ask for it, ignore this email.</p>")
    try:
        send_email(u["email"], "Reset your EduBridge AI password", text, html)
    except Exception:
        log.exception("password reset email failed")
        with S.tx() as c:
            c.execute("DELETE FROM password_resets WHERE token_hash=?", (_h(token),))
        raise AuthError("We couldn’t send the email right now. Please try again later.")
    return GENERIC_RESET_MSG


def validate_reset_token(token):
    """Return the user id for a live reset token, without consuming it."""
    if not token or len(token) > 200:
        return None
    r = S.qone("SELECT user_id FROM password_resets WHERE token_hash=? AND used=0 AND expires>?",
               (_h(token), _ts(_now())))
    return r["user_id"] if r else None


def reset_password(token, password, confirm):
    check_password_policy(password or "", "password")
    if (password or "") != (confirm or ""):
        raise AuthError("Passwords do not match.", "confirm")
    if not token or len(token) > 200:
        raise AuthError("This reset link is invalid or has expired. Request a new one.", fatal=True)
    with S.tx() as c:
        r = c.execute("SELECT user_id FROM password_resets WHERE token_hash=? AND used=0 AND expires>?",
                      (_h(token), _ts(_now()))).fetchone()
        if not r:
            raise AuthError("This reset link is invalid or has expired. Request a new one.", fatal=True)
        done = c.execute("UPDATE password_resets SET used=1 WHERE token_hash=? AND used=0", (_h(token),)).rowcount
        if done != 1:
            raise AuthError("This reset link was already used. Request a new one.", fatal=True)
        c.execute("UPDATE users SET password=? WHERE id=?", (pw_hash(password), r["user_id"]))
        c.execute("DELETE FROM auth_sessions WHERE user_id=?", (r["user_id"],))      # sign out everywhere
        u = c.execute("SELECT * FROM users WHERE id=?", (r["user_id"],)).fetchone()
    if u["email"]:
        _rate_clear("login:" + u["email"].lower())
    return u


# ---- OAuth: Google (OpenID Connect) and GitHub ----------------------------------------------------
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
GITHUB_AUTH = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_API = "https://api.github.com"
LABEL = {"google": "Google", "github": "GitHub"}


def provider_status():
    return {"google": bool(cfg("GOOGLE_CLIENT_ID") and cfg("GOOGLE_CLIENT_SECRET")),
            "github": bool(cfg("GITHUB_CLIENT_ID") and cfg("GITHUB_CLIENT_SECRET"))}


def redirect_uri():
    return base_url()


def begin_oauth(provider, role, binding):
    """Create a one-time state row and return the provider authorization URL."""
    if provider not in LABEL:
        raise AuthError("Unknown sign-in provider.")
    role = _role(role)
    if not provider_status()[provider]:
        raise AuthError(f"{LABEL[provider]} sign-in isn’t configured on this server yet. "
                        f"Set {provider.upper()}_CLIENT_ID and {provider.upper()}_CLIENT_SECRET (see GOOGLE_SETUP.md).")
    if not isinstance(binding, str) or not 20 <= len(binding) <= 128:
        raise AuthError("Your browser did not provide a secure sign-in token. Reload the page and try again.")
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    now = _now()
    with S.tx() as c:
        c.execute("DELETE FROM oauth_states WHERE expires<?", (_ts(now - timedelta(hours=1)),))
        c.execute("INSERT INTO oauth_states(state_hash,provider,nonce,verifier,binding_hash,role,created,expires,used)"
                  " VALUES(?,?,?,?,?,?,?,?,0)",
                  (_h(state), provider, nonce, verifier, _h(binding), role, _ts(now), _ts(now + OAUTH_TTL)))
    if provider == "google":
        challenge = hashlib.sha256(verifier.encode()).digest()
        import base64
        q = {"client_id": cfg("GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri(), "response_type": "code",
             "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account",
             "code_challenge": base64.urlsafe_b64encode(challenge).rstrip(b"=").decode(),
             "code_challenge_method": "S256"}
        return GOOGLE_AUTH + "?" + urllib.parse.urlencode(q)
    q = {"client_id": cfg("GITHUB_CLIENT_ID"), "redirect_uri": redirect_uri(), "scope": "read:user user:email",
         "state": state, "allow_signup": "true"}
    return GITHUB_AUTH + "?" + urllib.parse.urlencode(q)


def _consume_state(state):
    if not state or len(state) > 200:
        raise AuthError("This sign-in attempt is invalid or expired. Please try again.")
    with S.tx() as c:
        row = c.execute("SELECT * FROM oauth_states WHERE state_hash=?", (_h(state),)).fetchone()
        if not row or row["used"] or row["expires"] < _ts(_now()):
            raise AuthError("This sign-in attempt expired or was already used. Please try again.")
        c.execute("UPDATE oauth_states SET used=1 WHERE state_hash=?", (_h(state),))
    return row


_jwks_client = None


def _google_signing_key(id_token):
    global _jwks_client
    import jwt
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(GOOGLE_JWKS, cache_keys=True, lifespan=3600)
    return _jwks_client.get_signing_key_from_jwt(id_token).key


def verify_google_id_token(id_token, nonce, client_id):
    """Check signature (Google JWKS), issuer, audience, expiry and nonce; require a verified email."""
    import jwt
    try:
        claims = jwt.decode(id_token, _google_signing_key(id_token), algorithms=["RS256"], audience=client_id,
                            leeway=10, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        if claims.get("iss") not in GOOGLE_ISSUERS:      # checked by hand: older PyJWT only accepts one issuer string
            raise jwt.InvalidIssuerError("Invalid issuer")
    except Exception as e:
        log.warning("google id_token rejected: %s", e)
        raise AuthError("We couldn’t verify your Google identity. Please try again.")
    if not claims.get("nonce") or not hmac.compare_digest(str(claims["nonce"]), str(nonce or "")):
        raise AuthError("Google sign-in could not be verified (nonce mismatch). Please try again.")
    email = norm_email(claims.get("email"))
    if not email or str(claims.get("email_verified")).lower() != "true":
        raise AuthError("Your Google account’s email address is not verified, so it can’t be used to sign in.")
    return {"subject": str(claims["sub"]), "email": email, "email_verified": True,
            "name": _clean(claims.get("name"), 80) or email.split("@")[0]}


def _google_identity(code, row):
    try:
        r = requests.post(GOOGLE_TOKEN, timeout=12, data={
            "code": code, "client_id": cfg("GOOGLE_CLIENT_ID"), "client_secret": cfg("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": redirect_uri(), "grant_type": "authorization_code", "code_verifier": row["verifier"]})
    except requests.RequestException:
        raise AuthError("Couldn’t reach Google. Check your connection and try again.")
    if r.status_code != 200:
        log.warning("google token exchange failed: %s %s", r.status_code, r.text[:200])
        raise AuthError("Google rejected the sign-in request. Please try again.")
    id_token = (r.json() or {}).get("id_token")
    if not id_token:
        raise AuthError("Google did not return an identity token.")
    return verify_google_id_token(id_token, row["nonce"], cfg("GOOGLE_CLIENT_ID"))


def _github_identity(code, row):
    hdr = {"Accept": "application/json", "User-Agent": "EduBridgeAI"}
    try:
        r = requests.post(GITHUB_TOKEN, timeout=12, headers=hdr, data={
            "client_id": cfg("GITHUB_CLIENT_ID"), "client_secret": cfg("GITHUB_CLIENT_SECRET"),
            "code": code, "redirect_uri": redirect_uri()})
        tok = (r.json() or {}).get("access_token") if r.status_code == 200 else None
        if not tok:
            raise AuthError("GitHub rejected the sign-in request. Please try again.")
        auth = dict(hdr, Authorization="Bearer " + tok, **{"X-GitHub-Api-Version": "2022-11-28"})
        me = requests.get(GITHUB_API + "/user", headers=auth, timeout=12)
        em = requests.get(GITHUB_API + "/user/emails", headers=auth, timeout=12)
    except requests.RequestException:
        raise AuthError("Couldn’t reach GitHub. Check your connection and try again.")
    if me.status_code != 200 or em.status_code != 200:
        raise AuthError("GitHub did not return your account details. Please try again.")
    prof, emails = me.json(), em.json()
    verified = [e for e in emails if e.get("verified") and e.get("email")]
    primary = next((e for e in verified if e.get("primary")), verified[0] if verified else None)
    if not primary:
        raise AuthError("Your GitHub account has no verified email address. Verify one in GitHub’s email settings and try again.")
    return {"subject": str(prof["id"]), "email": norm_email(primary["email"]), "email_verified": True,
            "name": _clean(prof.get("name"), 80) or prof.get("login") or primary["email"].split("@")[0]}


def complete_oauth(params, binding):
    """Handle the provider redirect. Returns {"status":"ok","user":row,"created":bool}
    or {"status":"link","pending":{...}} when the email matches an unverified password account."""
    row = _consume_state(params.get("state"))
    bh = row["binding_hash"] or ""
    if not binding or not hmac.compare_digest(_h(binding), bh):
        raise AuthError("This sign-in was started in a different browser or tab, so it was rejected. Please start again.")
    label = LABEL[row["provider"]]
    if params.get("error"):
        if params["error"] == "access_denied":
            raise AuthError(f"{label} sign-in was cancelled.")
        raise AuthError(f"{label} sign-in failed ({str(params['error'])[:60]}). Please try again.")
    code = params.get("code")
    if not code or len(code) > 2048:
        raise AuthError(f"{label} did not return an authorization code. Please try again.")
    ident = _google_identity(code, row) if row["provider"] == "google" else _github_identity(code, row)
    try:
        return _resolve_identity(row["provider"], ident, row["role"])
    except sqlite3.IntegrityError:           # two simultaneous callbacks for the same new identity
        return _resolve_identity(row["provider"], ident, row["role"])


def _role_mismatch(u):
    return AuthError(f"This is a {u['role']} account. Switch to “I’m a {u['role']}” and try again.")


def _link(c, uid, provider, ident):
    c.execute("INSERT INTO auth_identities(user_id,provider,subject,email,email_verified,created) VALUES(?,?,?,?,1,?)",
              (uid, provider, ident["subject"], ident["email"], _ts(_now())))


def _resolve_identity(provider, ident, role):
    email = ident["email"]
    with S.tx() as c:
        link = c.execute("SELECT * FROM auth_identities WHERE provider=? AND subject=?", (provider, ident["subject"])).fetchone()
        if link:
            u = c.execute("SELECT * FROM users WHERE id=?", (link["user_id"],)).fetchone()
            if u:
                if u["role"] != role:
                    raise _role_mismatch(u)
                c.execute("UPDATE auth_identities SET email=? WHERE id=?", (email, link["id"]))
                return {"status": "ok", "user": u, "created": False}
            c.execute("DELETE FROM auth_identities WHERE id=?", (link["id"],))     # orphan from a deleted user
        u = c.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
        if u:
            if u["role"] != role:
                raise _role_mismatch(u)
            if c.execute("SELECT 1 FROM auth_identities WHERE user_id=? AND provider=?", (u["id"], provider)).fetchone():
                raise AuthError(f"That EduBridge account is already linked to a different {LABEL[provider]} account.")
            proven = c.execute("SELECT 1 FROM auth_identities WHERE user_id=? AND email_verified=1 AND lower(email)=?",
                               (u["id"], email)).fetchone()
            if proven:                       # the account's email was already verified by another provider
                _link(c, u["id"], provider, ident)
                return {"status": "ok", "user": u, "created": False}
            # Existing password account whose email was never verified: never link on an email match alone.
            return {"status": "link", "pending": {"provider": provider, "subject": ident["subject"], "email": email,
                                                  "user_id": u["id"], "role": role,
                                                  "exp": _ts(_now() + timedelta(minutes=10))}}
        uid = c.execute("INSERT INTO users(name,email,phone,password,role,skills,wants,bio,languages,xp,created,city,state,country)"
                        " VALUES(?,?,NULL,?,?,'','','','English',0,?,'','','India')",
                        (ident["name"], email, UNUSABLE_PASSWORD, role, _ts_local())).lastrowid
        _link(c, uid, provider, ident)
        u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return {"status": "ok", "user": u, "created": True}


def confirm_link(pending, password):
    """The visitor proved control of the provider email (provider-verified) and now proves control of the existing
    account by entering its password; only then is the provider identity attached."""
    if not pending or pending.get("exp", "") < _ts(_now()):
        raise AuthError("This link request expired. Please sign in with Google or GitHub again.", fatal=True)
    u = S.get_user(pending["user_id"])
    if not u:
        raise AuthError("That account no longer exists.")
    bucket = f"link:{u['id']}"
    _rate_check(bucket, 5, 15)
    if not password or not pw_ok(password, u["password"] or ""):
        _rate_hit(bucket)
        raise AuthError("That password is not correct.", "password")
    _rate_clear(bucket)
    ident = {"subject": pending["subject"], "email": pending["email"]}
    with S.tx() as c:
        if c.execute("SELECT 1 FROM auth_identities WHERE provider=? AND subject=?", (pending["provider"], ident["subject"])).fetchone() or \
           c.execute("SELECT 1 FROM auth_identities WHERE user_id=? AND provider=?", (u["id"], pending["provider"])).fetchone():
            raise AuthError("This account is already linked.")
        _link(c, u["id"], pending["provider"], ident)
    return u
