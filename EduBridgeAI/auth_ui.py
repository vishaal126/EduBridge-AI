"""Streamlit side of the EduBridge sign-in screen.

The futuristic login UI lives in `auth_component/` (HTML + CSS + JS) and runs as a Streamlit custom component.
Every button press is sent here as an action; this module calls `auth_service` (real SQLite accounts, real hashes,
real OAuth) and hands the outcome back to the component. Nothing the browser says is trusted: the component only
asks, the server decides, and `st.session_state.uid` is set here, server-side, after a genuine authentication.
"""
import logging
import uuid
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import auth_service as A

log = logging.getLogger("edubridge.auth")
ss = st.session_state

_component = components.declare_component("edubridge_auth", path=str(Path(__file__).with_name("auth_component")))

# The component iframe is stretched over the whole page so the original full-screen design is preserved.
_CSS = """<style>
iframe[title$="edubridge_auth"]{position:fixed;inset:0;width:100vw;height:100vh;height:100dvh;border:0;z-index:999990;background:#04050d;color-scheme:dark}
[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stSidebar"],[data-testid="collapsedControl"],footer{display:none!important}
.stApp{overflow:hidden}
</style>"""


def _secret(key):
    return st.secrets.get(key)


def _cookies():
    try:
        return dict(st.context.cookies)          # Streamlit >= 1.37; read-only view of the request cookies
    except Exception:
        return {}


def _oneshot(*names):
    ss["_clear"] = {"id": uuid.uuid4().hex, "names": list(names)}


# ---- session helpers (used by app.py) ------------------------------------------------------------
def require_session():
    """True only while st.session_state.uid has a live, server-side session behind it."""
    uid = ss.get("uid")
    if uid is None:
        return False
    if not A.session_valid(ss.get("eb_token"), uid):          # revoked (password reset, sign-out elsewhere) or expired
        _drop_login()
        return False
    return True


def _drop_login():
    for k in ("uid", "nav", "eb_token", "_pending", "_auth_result", "_auth_flash", "_pending_link", "_reset_token"):
        ss.pop(k, None)


def sign_out():
    A.revoke_session(ss.get("eb_token"))
    _drop_login()
    _oneshot("eb_rt")                                          # the component deletes the browser cookie on next render


def _restore_from_cookie():
    tok = _cookies().get("eb_rt")
    u = A.session_user(tok) if tok else None
    if not u:
        return False
    ss.uid, ss.eb_token = u["id"], tok
    return True


# ---- granting a session ----------------------------------------------------------------------------
def _grant(u, remember, created=False):
    token = A.create_session(u["id"], remember)
    ss["_pending"] = {"uid": u["id"], "token": token}          # promoted to uid only when the component says "continue"
    if ss.get("_clear") and "eb_rt" in ss["_clear"]["names"]:
        ss.pop("_clear")
    return {"ok": True, "created": created, "cookie": {"token": token, "persistent": bool(remember)},
            "user": {"name": u["name"], "email": u["email"] or u["phone"], "role": u["role"]}}


# ---- redirect handlers (OAuth callback, emailed reset link) ---------------------------------------
def _flash(**kw):
    ss["_auth_flash"] = dict(id=uuid.uuid4().hex, **kw)


def _handle_oauth_callback():
    qp = st.query_params
    if not (("code" in qp and "state" in qp) or ("error" in qp and "state" in qp)):
        return
    params = {k: qp.get(k) for k in ("code", "state", "error")}
    st.query_params.clear()                                    # never leave the code in the URL / history
    try:
        res = A.complete_oauth(params, _cookies().get("eb_oauth"))
        if res["status"] == "link":
            ss["_pending_link"] = res["pending"]
        else:
            ss["_auth_flash"] = dict(id=uuid.uuid4().hex, **_grant(res["user"], False, res["created"]))
    except A.AuthError as e:
        _flash(ok=False, error=str(e))
    except Exception:
        log.exception("OAuth callback failed")
        _flash(ok=False, error="Sign-in failed unexpectedly. Please try again.")
    _oneshot("eb_oauth")


def _handle_reset_link():
    tok = st.query_params.get("reset")
    if not tok:
        return
    st.query_params.clear()
    if A.validate_reset_token(tok):
        ss["_reset_token"] = tok                               # kept server-side; the browser never sees it again
    else:
        _flash(ok=False, error="This reset link is invalid or has expired. Request a new one.")


# ---- component actions -------------------------------------------------------------------------------
def _dispatch(act, v):
    s = lambda k, n=300: str(v.get(k) or "")[:n]
    if act == "login":
        return _grant(A.login(s("ident", 254), s("password", 256), s("role", 20)), bool(v.get("remember")))
    if act == "register":
        prof = v.get("profile") if isinstance(v.get("profile"), dict) else {}
        u = A.register(s("name", 100), s("email", 254), s("password", 256), s("confirm", 256), s("role", 20),
                       v.get("terms") is True, {k: str(prof.get(k) or "")[:300] for k in ("skills", "wants", "city", "state", "country")})
        return _grant(u, False, created=True)
    if act == "social_start":
        return {"ok": True, "redirect": A.begin_oauth(s("provider", 20), s("role", 20), s("bid", 140))}
    if act == "forgot":
        return {"ok": True, "message": A.request_password_reset(s("email", 254))}
    if act == "reset":
        tok = ss.get("_reset_token")
        if not tok:
            raise A.AuthError("This reset link is invalid or has expired. Request a new one.", fatal=True)
        u = A.reset_password(tok, s("password", 256), s("confirm", 256))
        ss.pop("_reset_token", None)
        return {"ok": True, "email": u["email"]}
    if act == "reset_cancel":
        ss.pop("_reset_token", None)
        return None
    if act == "link_confirm":
        pending = ss.get("_pending_link")
        if not pending:
            raise A.AuthError("This link request expired. Please sign in with Google or GitHub again.", fatal=True)
        u = A.confirm_link(pending, s("password", 256))
        ss.pop("_pending_link", None)
        return _grant(u, False)
    if act == "link_cancel":
        ss.pop("_pending_link", None)
        return None
    if act == "continue":
        p = ss.pop("_pending", None)
        if p:
            ss.uid, ss.eb_token = p["uid"], p["token"]
            ss.pop("_auth_result", None)
            ss.pop("_auth_flash", None)
        return None
    raise A.AuthError("Unknown request.")


def _handle_action(v):
    aid = str(v.get("id") or "")[:64]
    ss["_auth_last"] = aid
    ss.pop("_auth_flash", None)
    try:
        res = _dispatch(v.get("action"), v)
    except A.AuthError as e:
        res = {"ok": False, "error": str(e), "field": e.field, "fatal": e.fatal}
    except Exception:
        log.exception("auth action failed")
        res = {"ok": False, "error": "Something went wrong. Please try again."}
    if res is not None:
        res["id"] = aid
        ss["_auth_result"] = res
    st.rerun()


# ---- entry point -------------------------------------------------------------------------------------
def render():
    """Show the sign-in screen. Returns normally while the visitor is signed out; reruns once they are in."""
    A.set_secret_provider(_secret)
    if _restore_from_cookie():                                 # valid "remember me" / session cookie
        st.rerun()
    st.markdown(_CSS, unsafe_allow_html=True)
    _handle_oauth_callback()
    _handle_reset_link()
    pend = ss.get("_pending_link")
    link = {"provider": A.LABEL[pend["provider"]], "email": pend["email"]} if pend else None
    val = _component(key="eb_auth", default=None, providers=A.provider_status(), result=ss.get("_auth_result"),
                     flash=ss.get("_auth_flash"), clear=ss.get("_clear"), link=link,
                     mode="reset" if ss.get("_reset_token") else "login")
    if isinstance(val, dict) and val.get("id") and val.get("id") != ss.get("_auth_last"):
        _handle_action(val)
