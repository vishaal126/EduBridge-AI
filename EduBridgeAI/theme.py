"""EduBridge look & feel: one stylesheet plus a few small UI helpers (hero, empty states, nav icons).

Everything visual lives here so app.py / ui_*.py stay readable. Pure CSS animation, no extra dependencies.
"""
import html
from datetime import datetime

import streamlit as st

NAV_ICONS = {
    "Dashboard": "🏠", "Tutor Dashboard": "🏠", "Overview": "✨", "Offline Meetings": "🤝", "Online Classes": "💻",
    "AI Courses": "🎓", "AI Mind Maps": "🧠", "My Bookings": "📒", "EduCoins Wallet": "🪙", "Tutor + Skill Swap": "🔄",
    "Tutor Workspace": "🧑‍🏫", "AI Doubt Solver": "💡", "Learning Rooms": "👥", "My Sessions & Notes": "📝",
    "Skill Portfolio": "🏅", "XP & Leaderboard": "🏆", "Student Requests": "📥", "Messages": "💬",
    "Notifications": "🔔", "My Profile": "👤",
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@600;700;800&display=swap');

:root{
  --bg1:#0b1020; --bg2:#141e34; --line:#2c3b5d; --text:#edf3ff; --muted:#b7c5df;
  --violet:#7c6cff; --blue:#3b82f6; --teal:#2dd4bf; --pink:#ff5fa2; --amber:#fbbf24;
  --grad:linear-gradient(100deg,#7c6cff 0%,#3b82f6 55%,#2dd4bf 100%);
}

/* ---------- page + animated backdrop ---------- */
.stApp{
  font-family:'DM Sans',sans-serif;color:var(--text);
  background:
    radial-gradient(600px 400px at 12% 8%,rgba(124,108,255,.30),transparent 60%),
    radial-gradient(520px 380px at 88% 18%,rgba(45,212,191,.18),transparent 60%),
    radial-gradient(640px 460px at 70% 95%,rgba(255,95,162,.14),transparent 60%),
    linear-gradient(160deg,#0b1020 0%,#0d1427 60%,#090e1b 100%);
  background-size:140% 140%,140% 140%,140% 140%,100% 100%;
  animation:bgdrift 26s ease-in-out infinite alternate;
}
@keyframes bgdrift{0%{background-position:0% 0%,100% 0%,60% 100%,0 0}100%{background-position:30% 20%,70% 30%,40% 80%,0 0}}
@keyframes fadeUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes shimmer{0%{background-position:-200% 0}100%{background-position:200% 0}}
@keyframes floaty{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(124,108,255,.45)}70%{box-shadow:0 0 0 10px rgba(124,108,255,0)}}
@keyframes gradmove{0%{background-position:0% 50%}100%{background-position:200% 50%}}

[data-testid="stHeader"]{background:transparent}
.block-container{padding-top:2.2rem;max-width:1180px;animation:fadeUp .45s ease both}
h1,h2,h3,h4{font-family:Manrope,sans-serif!important;color:#f4f7ff!important;letter-spacing:-.025em}
h2{position:relative;padding-bottom:.35rem}
h2::after{content:"";display:block;width:54px;height:4px;border-radius:4px;margin-top:6px;background:var(--grad)}
.stCaption,small,[data-testid="stCaptionContainer"]{color:var(--muted)!important}
hr{border-color:var(--line)!important;opacity:.6}
a{color:#8fb4ff!important}

/* ---------- sidebar ---------- */
[data-testid="stSidebar"]{background:linear-gradient(180deg,#121b33 0%,#0e1528 100%);border-right:1px solid var(--line)}
[data-testid="stSidebar"] h1{font-size:1.45rem}
.gradtxt{background:var(--grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.sb-card{display:flex;gap:12px;align-items:center;padding:12px;border-radius:16px;margin:6px 0 14px;
  background:linear-gradient(120deg,rgba(124,108,255,.22),rgba(45,212,191,.10));border:1px solid var(--line)}
.sb-avatar{width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;font:800 18px Manrope,sans-serif;
  color:#fff;background:var(--grad);flex:none;animation:pulse 3s infinite}
.sb-name{font:700 15px Manrope,sans-serif;color:#fff;line-height:1.2}
.sb-meta{font-size:12px;color:var(--muted);word-break:break-all}

/* nav radio -> vertical pill menu */
[data-testid="stSidebar"] div[role="radiogroup"]{gap:3px}
[data-testid="stSidebar"] div[role="radiogroup"]>label{
  width:100%;padding:9px 12px;border-radius:12px;border:1px solid transparent;cursor:pointer;
  transition:transform .15s ease,background .2s ease,border-color .2s ease}
[data-testid="stSidebar"] div[role="radiogroup"]>label>div:first-child{display:none}
[data-testid="stSidebar"] div[role="radiogroup"]>label p{font-weight:600;font-size:14.5px;margin:0}
[data-testid="stSidebar"] div[role="radiogroup"]>label:hover{background:rgba(124,108,255,.14);transform:translateX(4px)}
[data-testid="stSidebar"] div[role="radiogroup"]>label:has(input:checked){
  background:linear-gradient(100deg,rgba(124,108,255,.38),rgba(59,130,246,.26));border-color:#5b6bd6;
  box-shadow:0 6px 18px rgba(80,90,220,.28)}
[data-testid="stSidebar"] div[role="radiogroup"]>label:has(input:checked) p{color:#fff}

/* ---------- hero + cards ---------- */
.hero{position:relative;overflow:hidden;border-radius:22px;padding:26px 28px;margin:6px 0 18px;border:1px solid var(--line);
  background:linear-gradient(120deg,rgba(124,108,255,.24),rgba(45,212,191,.08)),#141e34;animation:fadeUp .5s ease both}
.hero::before{content:"";position:absolute;inset:-40% -10% auto auto;width:340px;height:340px;border-radius:50%;
  background:radial-gradient(circle,rgba(124,108,255,.35),transparent 65%);animation:floaty 7s ease-in-out infinite}
.hero h1{position:relative;font-size:36px;margin:8px 0 4px;background:linear-gradient(100deg,#fff,#b9c6ff 40%,#7fe9dc);
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent!important}
.hero p{position:relative;color:var(--muted);margin:0;max-width:720px}
.hero-emoji{position:absolute;right:26px;top:18px;font-size:54px;animation:floaty 4s ease-in-out infinite;filter:drop-shadow(0 8px 16px rgba(0,0,0,.35))}

.panel{position:relative;background:linear-gradient(145deg,rgba(124,108,255,.10),rgba(45,212,191,.04)),rgba(20,30,52,.82);
  backdrop-filter:blur(6px);border:1px solid var(--line);border-radius:18px;padding:18px 20px;margin:8px 0 14px;
  transition:transform .2s ease,border-color .2s ease,box-shadow .2s ease;animation:fadeUp .45s ease both}
.panel:hover{transform:translateY(-3px);border-color:#6b72e8;box-shadow:0 14px 34px rgba(60,70,200,.25)}
.panel h3{margin:0 0 6px;font-size:1.1rem}
.panel p{color:var(--muted);margin:4px 0}
.feature{text-align:left}
.feature .ico{font-size:30px;display:inline-block;margin-bottom:6px;animation:floaty 5s ease-in-out infinite}
.pill{display:inline-block;padding:4px 11px;border-radius:99px;font-size:11px;font-weight:700;letter-spacing:.04em;color:#d9d4ff;
  background:rgba(124,108,255,.18);border:1px solid #5a52a8;margin:2px 6px 2px 0}
.pill.ok{color:#b8ffe9;background:rgba(45,212,191,.15);border-color:#25917f}
.pill.warn{color:#ffe7a8;background:rgba(251,191,36,.14);border-color:#9c7a1b}

.empty{text-align:center;padding:34px 20px;border:1.5px dashed #3a4c78;border-radius:20px;margin:10px 0 16px;
  background:rgba(20,30,52,.55);animation:fadeUp .5s ease both}
.empty .big{font-size:46px;animation:floaty 4s ease-in-out infinite}
.empty h3{margin:6px 0 4px}.empty p{color:var(--muted);margin:0 auto;max-width:460px}

.setup{border-radius:18px;padding:16px 18px;margin:6px 0 14px;border:1px solid #7a5a1f;
  background:linear-gradient(120deg,rgba(251,191,36,.14),rgba(255,95,162,.07))}
.setup b{color:#ffe7a8}

.rank{display:flex;align-items:center;gap:14px}
.rank .medal{font:800 22px Manrope,sans-serif;width:44px;text-align:center}
.rank .grow{flex:1}.rank .xp{font:800 22px Manrope,sans-serif;background:var(--grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.bar{height:7px;border-radius:8px;background:#1b2848;overflow:hidden;margin-top:8px}
.bar>span{display:block;height:100%;border-radius:8px;background:var(--grad);background-size:200% 100%;animation:gradmove 3s linear infinite}

/* ---------- inputs + buttons ---------- */
div.stButton>button,div.stFormSubmitButton>button,div.stDownloadButton>button{
  border-radius:12px;border:1px solid #5866a0;color:#fff;font-weight:700;min-height:41px;
  background:linear-gradient(100deg,#6c5ce7,#3b82f6);background-size:200% 100%;
  transition:transform .15s ease,box-shadow .2s ease,background-position .4s ease,filter .2s ease}
div.stButton>button:hover,div.stFormSubmitButton>button:hover,div.stDownloadButton>button:hover{
  transform:translateY(-2px);box-shadow:0 10px 24px rgba(79,100,240,.45);background-position:100% 0;filter:brightness(1.08);color:#fff;border-color:#8aa0ff}
div.stButton>button:active,div.stFormSubmitButton>button:active{transform:translateY(0) scale(.98)}
div.stButton>button:disabled,div.stFormSubmitButton>button:disabled{opacity:.45;filter:grayscale(.5);transform:none;box-shadow:none}
button[kind="primary"],button[data-testid="stBaseButton-primary"],button[data-testid="stBaseButton-primaryFormSubmit"]{
  background:linear-gradient(100deg,#7c6cff,#3b82f6,#2dd4bf)!important;background-size:200% 100%!important}

.stTextInput input,.stTextArea textarea,.stNumberInput input,.stDateInput input,.stTimeInput input,
.stSelectbox div[data-baseweb="select"]>div,.stMultiSelect div[data-baseweb="select"]>div{
  background:#0f192c!important;color:var(--text)!important;border-color:#33466a!important;border-radius:12px!important;
  transition:border-color .2s ease,box-shadow .2s ease}
.stTextInput input:focus,.stTextArea textarea:focus,.stNumberInput input:focus{
  border-color:#7c6cff!important;box-shadow:0 0 0 3px rgba(124,108,255,.28)!important}
[data-testid="stForm"]{background:rgba(18,28,49,.7);border:1px solid var(--line);border-radius:18px;padding:18px}

[data-testid="stMetric"]{position:relative;overflow:hidden;background:linear-gradient(145deg,#1a2744,#141e34);padding:14px 16px;
  border:1px solid var(--line);border-radius:16px;transition:transform .2s ease,box-shadow .2s ease}
[data-testid="stMetric"]::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--grad)}
[data-testid="stMetric"]:hover{transform:translateY(-3px);box-shadow:0 12px 28px rgba(60,70,200,.25)}
[data-testid="stMetricValue"]{font-family:Manrope,sans-serif;font-weight:800}

.stTabs [data-baseweb="tab-list"]{gap:6px;border-bottom:1px solid var(--line)}
.stTabs [data-baseweb="tab"]{background:rgba(18,28,49,.8);border-radius:12px 12px 0 0;padding:8px 16px;transition:background .2s ease,transform .15s ease}
.stTabs [data-baseweb="tab"]:hover{background:rgba(124,108,255,.22);transform:translateY(-2px)}
.stTabs [aria-selected="true"]{background:linear-gradient(100deg,rgba(124,108,255,.45),rgba(59,130,246,.3))!important;color:#fff!important}
.stTabs [data-baseweb="tab-highlight"]{background:var(--grad)!important;height:3px}

[data-testid="stExpander"]{border:1px solid var(--line)!important;border-radius:16px!important;background:rgba(18,28,49,.6);transition:border-color .2s ease}
[data-testid="stExpander"]:hover{border-color:#5560b8!important}
.stProgress>div>div>div>div{background:var(--grad)!important;background-size:200% 100%;animation:gradmove 3s linear infinite}
[data-testid="stAlert"]{border-radius:14px;border:1px solid var(--line);animation:fadeUp .35s ease both}
[data-testid="stDataFrame"]{border-radius:14px;overflow:hidden}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-thumb{background:#34466f;border-radius:9px}::-webkit-scrollbar-track{background:transparent}

@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
@media (max-width:720px){.hero h1{font-size:27px}.hero-emoji{display:none}.block-container{padding-left:1rem;padding-right:1rem}}
</style>
"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


def esc(s):
    return html.escape(str(s if s is not None else ""))


def greeting():
    h = datetime.now().hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening"


def hero(title, sub, pill="EDUBRIDGE WORKSPACE", emoji="🎓"):
    st.markdown(f"<div class='hero'><div class='hero-emoji'>{emoji}</div><span class='pill'>{esc(pill)}</span>"
                f"<h1>{esc(title)}</h1><p>{esc(sub)}</p></div>", unsafe_allow_html=True)


def goto(page):
    """Button callback: jump to another page of the sidebar menu (use as on_click=T.goto, args=(page,))."""
    st.session_state["nav"] = page


def empty_state(icon, title, text, cta=None, target=None, key=None):
    st.markdown(f"<div class='empty'><div class='big'>{icon}</div><h3>{esc(title)}</h3><p>{esc(text)}</p></div>", unsafe_allow_html=True)
    if cta and target:
        _, mid, _ = st.columns([1, 1, 1])
        mid.button(cta, key=key or f"empty_{target}", on_click=goto, args=(target,), use_container_width=True)


def nav_card(icon, title, text, target, key):
    """A clickable feature card: panel + a button that opens `target`."""
    st.markdown(f"<div class='panel feature'><div class='ico'>{icon}</div><h3>{esc(title)}</h3><p>{esc(text)}</p></div>", unsafe_allow_html=True)
    st.button(f"Open {title} →", key=key, on_click=goto, args=(target,), use_container_width=True)


def sidebar_profile(me):
    initial = esc((me["name"] or "?").strip()[:1].upper())
    st.markdown(f"<div class='sb-card'><div class='sb-avatar'>{initial}</div><div><div class='sb-name'>{esc(me['name'])}</div>"
                f"<div class='sb-meta'>{esc(me['role'].title())} · ⚡ {me['xp'] or 0} XP</div>"
                f"<div class='sb-meta'>{esc(me['email'] or me['phone'])}</div></div></div>", unsafe_allow_html=True)
