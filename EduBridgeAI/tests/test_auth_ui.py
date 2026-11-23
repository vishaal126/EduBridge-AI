"""Exercises auth_ui's server-side flow against a stub `streamlit` (no browser needed)."""
import os, sys, tempfile, types, unittest, sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EDUBRIDGE_DB"] = os.path.join(tempfile.mkdtemp(), "ui.db")


class Rerun(Exception):
    pass


class QP(dict):
    def clear(self): super().clear()


st = types.ModuleType("streamlit")
class SS(dict):                       # like st.session_state: item AND attribute access
    __getattr__ = dict.get
    def __setattr__(self, k, v): self[k] = v
st.session_state = SS()
st.query_params = QP()
st.context = types.SimpleNamespace(cookies={})
st.secrets = {}
st.markdown = lambda *a, **k: None
def _rerun(): raise Rerun()
st.rerun = _rerun
comp = types.ModuleType("streamlit.components.v1")
comp.declare_component = lambda name, path: (lambda **kw: st._component_value)
st._component_value = None
components = types.ModuleType("streamlit.components"); components.v1 = comp
sys.modules.update({"streamlit": st, "streamlit.components": components, "streamlit.components.v1": comp})
st.components = components

import services as S, auth_service as A, auth_ui as U
U.ss = st.session_state
from tests.test_auth import USERS

GOOD = "Sup3r-Secret!"


def act(**v):
    v.setdefault("id", os.urandom(4).hex())
    st._component_value = v
    try: U.render()
    except Rerun: pass
    return st.session_state.get("_auth_result")


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c = sqlite3.connect(S.DB); c.executescript(USERS); c.close(); A.init_auth_schema()

    def setUp(self):
        st.session_state.clear(); st.query_params.clear(); st.context.cookies.clear(); st._component_value = None

    def test_register_then_continue_creates_real_session(self):
        r = act(action="register", name="Ui User", email="ui@example.com", password=GOOD, confirm=GOOD, role="Student", terms=True, profile={"city": "Pune"})
        self.assertTrue(r["ok"] and r["created"]); self.assertNotIn("uid", st.session_state)     # not signed in until "continue"
        act(action="continue")
        self.assertIn("uid", st.session_state); self.assertTrue(U.require_session())
        row = S.qone("SELECT * FROM users WHERE email='ui@example.com'"); self.assertEqual((row["city"], row["id"]), ("Pune", st.session_state["uid"]))
        tok = st.session_state["eb_token"]; U.sign_out()
        self.assertNotIn("uid", st.session_state); self.assertIsNone(A.session_user(tok)); self.assertEqual(st.session_state["_clear"]["names"], ["eb_rt"])

    def test_bad_login_never_sets_uid_and_client_cannot_forge_continue(self):
        r = act(action="login", ident="ghost@example.com", password="x", role="Student", remember=False)
        self.assertFalse(r["ok"]); act(action="continue"); self.assertNotIn("uid", st.session_state)
        st.session_state["uid"] = 1                                   # tampered state without a server session is rejected
        self.assertFalse(U.require_session()); self.assertNotIn("uid", st.session_state)

    def test_cookie_restore_and_revocation(self):
        act(action="register", name="Cookie Mon", email="cm@example.com", password=GOOD, confirm=GOOD, role="Tutor", terms=True)
        tok = st.session_state["_pending"]["token"]; st.session_state.clear()
        st.context.cookies["eb_rt"] = tok
        try: U.render()
        except Rerun: pass
        self.assertIn("uid", st.session_state)
        A.revoke_session(tok); st.session_state.clear(); st._component_value = None
        U.render(); self.assertNotIn("uid", st.session_state)

    def test_reset_link_param_and_flow(self):
        uid = A.register("Rs", "rs@example.com", GOOD, GOOD, "student", True)["id"]
        from unittest import mock
        sent = {}
        os.environ.update(SMTP_HOST="h", SMTP_FROM="f@x.co")
        with mock.patch.object(A, "send_email", lambda to, s_, text, html=None: sent.update(text=text)):
            r = act(action="forgot", email="rs@example.com"); self.assertTrue(r["ok"])
        tok = sent["text"].split("?reset=")[1].split()[0]
        st.session_state.clear(); st.query_params["reset"] = tok; st._component_value = None
        U.render(); self.assertEqual(st.session_state["_reset_token"], tok); self.assertNotIn("reset", st.query_params)
        r = act(action="reset", password="N3w-Password!", confirm="N3w-Password!"); self.assertTrue(r["ok"])
        self.assertNotIn("_reset_token", st.session_state); self.assertEqual(A.login("rs@example.com", "N3w-Password!", "student")["id"], uid)
        st.query_params["reset"] = tok; U.render()                       # token is single use
        self.assertNotIn("_reset_token", st.session_state); self.assertFalse(st.session_state["_auth_flash"]["ok"])

    def test_social_start_unconfigured_is_an_error_not_a_fake_success(self):
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"): os.environ.pop(k, None)
        r = act(action="social_start", provider="google", role="Student", bid="b" * 30)
        self.assertFalse(r["ok"]); self.assertIn("isn’t configured", r["error"])

    def test_oauth_callback_binds_to_browser_and_clears_params(self):
        os.environ.update(GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="sec")
        url = A.begin_oauth("google", "student", "c" * 30)
        import urllib.parse as up
        state = dict(up.parse_qsl(url.split("?", 1)[1]))["state"]
        st.query_params.update(code="abc", state=state)
        st.context.cookies["eb_oauth"] = "wrong" * 8                    # a different browser
        U.render()
        self.assertEqual(len(st.query_params), 0); self.assertFalse(st.session_state["_auth_flash"]["ok"])
        self.assertIn("different browser", st.session_state["_auth_flash"]["error"]); self.assertNotIn("uid", st.session_state)


if __name__ == "__main__":
    unittest.main()
