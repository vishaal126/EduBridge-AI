"""Streamlit screens for the tutor marketplace, bookings, EduCoins wallet and dashboards.
All state changes go through services.py, which re-checks roles, ownership and status."""
import base64
from datetime import date, datetime, timedelta

import streamlit as st
import streamlit.components.v1 as components

import pricing
import services as S
import theme as T

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
PLAN_LABEL = {"hourly": "Hourly", "weekly": "Weekly", "monthly": "Monthly"}
STATUS_ICON = {"Pending": "🟡", "Accepted": "🔵", "Confirmed": "🟢", "Completed": "✅", "Rejected": "🔴", "Cancelled": "⚪"}


def _try(fn, *a, ok=None, **k):
    """Run a service call; show its error nicely. Returns (success, result)."""
    try:
        r = fn(*a, **k)
        if ok:
            st.success(ok)
        return True, r
    except S.ServiceError as e:
        st.error(str(e))
        return False, None


def _avatar(p, size=64):
    if p["photo"]:
        mime = "image/png" if bytes(p["photo"][:4]) == b"\x89PNG" else "image/jpeg"
        return f"<img src='data:{mime};base64,{base64.b64encode(bytes(p['photo'])).decode()}' style='width:{size}px;height:{size}px;border-radius:50%;object-fit:cover;border:2px solid #6c5ce7'>"
    return f"<div style='width:{size}px;height:{size}px;border-radius:50%;background:#29264a;display:flex;align-items:center;justify-content:center;font-size:{size // 2}px'>🎓</div>"


def _esc(s):
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inr(n):
    return f"₹{int(n):,}"


def _stars(r, n):
    return f"⭐ {r} ({n})" if r else "No reviews yet"


# ================================================================ Tutor dashboard ===============
def tutor_dashboard(me):
    st.markdown("## Tutor dashboard")
    prof = S.get_profile(me["id"])
    earn = S.tutor_earnings(me["id"])
    avg, n_rev = S.tutor_rating(me["id"])
    pending = S.qone("SELECT COUNT(*) n FROM bookings WHERE tutor_id=? AND status='Pending'", (me["id"],))["n"]
    upcoming = S.qone("SELECT COUNT(*) n FROM bookings WHERE tutor_id=? AND status='Confirmed'", (me["id"],))["n"]
    c = st.columns(4)
    c[0].metric("Pending requests", pending)
    c[1].metric("Confirmed sessions", upcoming)
    c[2].metric("Earned (paid, completed)", _inr(earn["earned_inr"]))
    c[3].metric("Rating", f"{avg} ⭐" if avg else "—", f"{n_rev} reviews")
    if not prof or not prof["hourly_price"]:
        st.warning("Finish your profile and set at least an hourly price. Students can find and book you only after that.")
    tabs = st.tabs(["Profile & subjects", "Pricing", "Availability", "Offline requests", "Online requests",
                    "Students", "Earnings", "Reviews", "Booking history"])
    with tabs[0]:
        _profile_form(me, prof)
    with tabs[1]:
        _pricing_form(me, prof)
    with tabs[2]:
        _availability(me)
    with tabs[3]:
        _requests(me, "offline")
    with tabs[4]:
        _requests(me, "online")
    with tabs[5]:
        _students(me)
    with tabs[6]:
        _earnings(me, earn)
    with tabs[7]:
        _reviews(me)
    with tabs[8]:
        _history(me, "tutor")


def _profile_form(me, p):
    st.caption("This is what students see. Your price is never changed by the platform.")
    cats = list(pricing.CATEGORIES)
    with st.form("tutor_profile"):
        headline = st.text_input("Headline", p["headline"] if p else "", placeholder="Python & data science tutor, 6 years' experience")
        subjects = st.text_input("Subjects you teach (comma separated)", p["subjects"] if p else "", placeholder="Python, SQL, Statistics")
        categories = st.multiselect("Teaching categories", cats, default=[x for x in (p["categories"].split(", ") if p and p["categories"] else []) if x in cats])
        quals = st.text_area("Qualifications & skills", p["qualifications"] if p else "", placeholder="M.Tech (CSE), Google Data Analytics certified")
        a, b = st.columns(2)
        years = a.number_input("Teaching experience (years)", 0, 60, int(p["experience_years"]) if p else 0)
        modes = b.multiselect("Teaching modes", ["online", "offline"], default=(p["modes"].split(",") if p else ["online"]))
        a, b, c = st.columns(3)
        city = a.text_input("City", (p["city"] if p else "") or me["city"] or "")
        state = b.text_input("State", (p["state"] if p else "") or me["state"] or "")
        country = c.text_input("Country", (p["country"] if p else "") or me["country"] or "India")
        radius = st.number_input("Travel radius for offline sessions (km)", 1, 100, int(p["travel_radius_km"]) if p else 10)
        prefs = st.text_area("Teaching preferences", p["preferences"] if p else "", placeholder="Small batches, project-based, Telugu/English")
        photo = st.file_uploader("Profile picture (JPG/PNG, under 1 MB)", type=["jpg", "jpeg", "png"])
        active = st.checkbox("Visible to students", value=bool(p["active"]) if p else True)
        save = st.form_submit_button("Save profile", use_container_width=True)
    if save:
        f = dict(headline=headline, subjects=subjects, categories=categories, qualifications=quals, experience_years=years, modes=modes,
                 city=city, state=state, country=country, travel_radius_km=radius, preferences=prefs, active=active)
        if p:  # keep existing prices/coin settings when only the profile changes
            for k in ("hourly_price", "weekly_price", "weekly_sessions", "weekly_hours", "monthly_price", "monthly_sessions",
                      "monthly_hours", "accepts_coins", "coin_max_pct"):
                f[k] = p[k]
        ok, _ = _try(S.save_tutor_profile, me["id"], f, photo.getvalue() if photo else None, ok="Profile saved.")
        if ok:
            st.rerun()


def _pricing_form(me, p):
    if not p:
        st.info("Save your profile first, then set your prices here.")
        return
    st.markdown("### Your prices")
    st.caption("You decide every price. Estimates below are guidance only and are never applied automatically.")
    with st.form("tutor_pricing"):
        a, b, c = st.columns(3)
        hourly = a.number_input("Hourly price (₹)", 0, 200000, int(p["hourly_price"] or 0), step=50)
        weekly = b.number_input("Weekly price (₹)", 0, 200000, int(p["weekly_price"] or 0), step=100, help="0 = not offered")
        monthly = c.number_input("Monthly price (₹)", 0, 200000, int(p["monthly_price"] or 0), step=500, help="0 = not offered")
        a, b, c, d = st.columns(4)
        ws = a.number_input("Weekly: sessions", 1, 14, int(p["weekly_sessions"]))
        wh = b.number_input("Weekly: hours/session", 0.5, 6.0, float(p["weekly_hours"]), step=0.5)
        ms = c.number_input("Monthly: sessions", 1, 60, int(p["monthly_sessions"]))
        mh = d.number_input("Monthly: hours/session", 0.5, 6.0, float(p["monthly_hours"]), step=0.5)
        st.markdown("**EduCoins**")
        coins = st.checkbox("I accept EduCoins as payment", value=bool(p["accepts_coins"]))
        pct = st.slider("Maximum share of a booking payable in EduCoins (%)", 0, 100, int(p["coin_max_pct"]),
                        help=f"100% = a student can book entirely with EduCoins. 1 EduCoin = ₹{S.COIN_INR} of fee value.")
        save = st.form_submit_button("Save prices", use_container_width=True)
    if save:
        f = {k: p[k] for k in ("headline", "categories", "subjects", "qualifications", "experience_years", "city", "state", "country",
                               "preferences", "travel_radius_km", "active")}
        f["categories"] = [x for x in (p["categories"] or "").split(", ") if x]
        f["modes"] = (p["modes"] or "online").split(",")
        f.update(hourly_price=hourly, weekly_price=weekly, monthly_price=monthly, weekly_sessions=ws, weekly_hours=wh,
                 monthly_sessions=ms, monthly_hours=mh, accepts_coins=coins, coin_max_pct=pct)
        ok, _ = _try(S.save_tutor_profile, me["id"], f, ok="Prices saved.")
        if ok:
            st.rerun()
    st.markdown("### Estimated local price range")
    g = S.price_guidance(p, None)
    if not g:
        return
    for mode, e in g.items():
        mine = p["hourly_price"]
        verdict = pricing.compare_to_market(mine, e["low"], e["high"])
        st.markdown(f"<div class='panel'><span class='pill'>ESTIMATE · NOT YOUR PRICE</span> <b>{mode.title()} hourly</b>: "
                    f"{_inr(e['low'])} – {_inr(e['high'])} (typical {_inr(e['mid'])})<br>"
                    f"<small>Your actual hourly price: {_inr(mine) if mine else 'not set'} → {verdict}.</small><br>"
                    f"<small>Suggested weekly ≈ {_inr(pricing.package_estimate(e['mid'], p['weekly_sessions'], p['weekly_hours'], 'weekly'))}, "
                    f"monthly ≈ {_inr(pricing.package_estimate(e['mid'], p['monthly_sessions'], p['monthly_hours'], 'monthly'))} "
                    f"(small package discounts).</small><br><small class='muted'>{_esc(e['note'])}</small></div>", unsafe_allow_html=True)
    with st.expander("How this estimate is calculated"):
        st.json(g[next(iter(g))]["factors"])


def _availability(me):
    st.markdown("### Weekly availability")
    st.caption("Times are in your local time. Students book one-hour slots inside these windows.")
    with st.form("avail_add", clear_on_submit=True):
        a, b, c, d = st.columns(4)
        day = a.selectbox("Day", range(7), format_func=lambda i: WEEKDAYS[i])
        start = b.time_input("From", datetime.strptime("17:00", "%H:%M").time())
        end = c.time_input("To", datetime.strptime("20:00", "%H:%M").time())
        mode = d.selectbox("Mode", ["both", "online", "offline"])
        add = st.form_submit_button("Add window")
    if add:
        ok, _ = _try(S.add_availability, me["id"], day, start.strftime("%H:%M"), end.strftime("%H:%M"), mode, ok="Availability added.")
        if ok:
            st.rerun()
    rows = S.availability(me["id"])
    if not rows:
        st.info("No availability yet — students cannot book you until you add a window.")
    for r in rows:
        a, b = st.columns([5, 1])
        a.write(f"**{WEEKDAYS[r['weekday']]}** {r['start_time']}–{r['end_time']} · {r['mode']}")
        if b.button("Remove", key=f"av_del_{r['id']}"):
            S.delete_availability(me["id"], r["id"])
            st.rerun()


def _requests(me, mode):
    rows = [r for r in S.my_bookings(me["id"], "tutor") if r["mode"] == mode]
    open_rows = [r for r in rows if r["status"] in ("Pending", "Accepted", "Confirmed")]
    if not open_rows:
        st.info(f"No open {mode} requests.")
    for b in open_rows:
        with st.expander(f"{STATUS_ICON[b['status']]} #{b['id']} · {b['other_name']} · {PLAN_LABEL[b['plan']]} · {b['slot_start'].replace('T', ' ')} · {b['status']}", expanded=b["status"] == "Pending"):
            st.write(f"Price {_inr(b['list_price'])} · EduCoins {b['coins_applied']} · Student pays {_inr(b['amount_due'])} · "
                     f"{b['sessions']} session(s), {b['total_hours']:g} h")
            if b["student_note"]:
                st.caption(f"Student note: {b['student_note']}")
            if b["status"] == "Pending":
                with st.form(f"resp_{b['id']}"):
                    loc = st.text_input("Meeting place / address (required for offline)", key=f"loc_{b['id']}") if mode == "offline" else ""
                    note = st.text_input("Note to student (optional)", key=f"note_{b['id']}")
                    a, c = st.columns(2)
                    acc = a.form_submit_button("Accept")
                    rej = c.form_submit_button("Reject")
                if acc or rej:
                    ok, res = _try(S.tutor_respond, me["id"], b["id"], bool(acc), note, loc)
                    if ok:
                        st.rerun()
            elif b["status"] == "Accepted":
                if b["amount_due"]:
                    st.info("Waiting for the student's payment.")
            if b["status"] in ("Accepted", "Confirmed", "Pending") and st.button("Cancel booking", key=f"tc_{b['id']}"):
                ok, _ = _try(S.cancel_booking, me["id"], b["id"])
                if ok:
                    st.rerun()
            if b["status"] == "Confirmed":
                if mode == "online" and b["meeting_url"]:
                    st.markdown(f"[Open video room]({b['meeting_url']})")
                if st.button("Mark completed", key=f"done_{b['id']}"):
                    ok, _ = _try(S.complete_booking, me["id"], b["id"])
                    if ok:
                        st.rerun()


def _students(me):
    rows = S.qall("""SELECT u.id,u.name,u.city,u.languages,COUNT(b.id) n,SUM(b.status='Completed') done FROM bookings b JOIN users u ON u.id=b.student_id
                     WHERE b.tutor_id=? AND b.status IN ('Accepted','Confirmed','Completed') GROUP BY u.id ORDER BY n DESC""", (me["id"],))
    if not rows:
        st.info("Students appear here once you accept their bookings.")
    for r in rows:
        st.markdown(f"<div class='panel'><b>{_esc(r['name'])}</b> <span class='pill'>{r['n']} bookings · {r['done'] or 0} completed</span>"
                    f"<p class='muted'>{_esc(r['city'] or 'Location not shared')} · Languages: {_esc(r['languages'])}</p></div>", unsafe_allow_html=True)


def _earnings(me, e):
    c = st.columns(3)
    c[0].metric("Earned in ₹ (completed & paid)", _inr(e["earned_inr"]))
    c[1].metric("Confirmed, not yet delivered", _inr(e["upcoming_inr"]))
    c[2].metric("EduCoins received (completed)", e["earned_coins"], f"≈ {_inr(e['earned_coins'] * S.COIN_INR)} fee value")
    if e["refunds_due"]:
        st.warning(f"{_inr(e['refunds_due'])} of cancelled, already-paid bookings is marked refund due.")
    st.caption("These figures are bookings recorded in EduBridge. Paying money out to tutors (bank transfer) is not built in; see README.")


def _reviews(me):
    rows = S.qall("SELECT r.*,u.name FROM reviews r JOIN users u ON u.id=r.student_id WHERE r.tutor_id=? ORDER BY r.id DESC", (me["id"],))
    avg, n = S.tutor_rating(me["id"])
    st.metric("Average rating", f"{avg} ⭐" if avg else "—", f"{n} reviews")
    for r in rows:
        st.markdown(f"<div class='panel'>{'⭐' * r['rating']} <b>{_esc(r['name'])}</b><p>{_esc(r['comment'])}</p><small>{r['created'][:10]}</small></div>", unsafe_allow_html=True)


def _history(me, role):
    rows = S.my_bookings(me["id"], role)
    if not rows:
        st.info("No bookings yet.")
        return
    st.dataframe([{"#": r["id"], "With": r["other_name"], "Mode": r["mode"], "Plan": r["plan"], "Start": r["slot_start"].replace("T", " "),
                   "Total ₹": r["list_price"], "EduCoins": r["coins_applied"], "Paid ₹": r["amount_due"] if r["payment_status"] == "paid" else 0,
                   "Payment": r["payment_status"], "Status": r["status"]} for r in rows], use_container_width=True, hide_index=True)


# ================================================================ Marketplace ===================
def marketplace(me, mode):
    title = {"offline": "Offline meetings", "online": "Online classes"}[mode]
    st.markdown(f"## {title}")
    st.caption("Find a tutor, compare hourly / weekly / monthly prices and request a booking. Nothing is confirmed until the tutor accepts and payment is verified by the server."
               if mode == "online" else
               "Find a tutor near you for face-to-face sessions. Distances are straight-line estimates between city centres.")
    if me["role"] != "student":
        st.info("Tutor accounts manage their services in the Tutor dashboard. Sign in as a student to book.")
        return
    if mode == "offline" and not me["city"]:
        st.warning("Add your city in My Profile to see distances and local price estimates.")
    with st.expander("Search filters", expanded=True):
        a, b, c = st.columns(3)
        subj = a.text_input("Subject or skill", key=f"q_{mode}")
        cat = b.selectbox("Category", ["Any"] + list(pricing.CATEGORIES), key=f"c_{mode}")
        city = c.text_input("Tutor city" if mode == "offline" else "Tutor city (optional)", value=(me["city"] or "") if mode == "offline" else "", key=f"city_{mode}")
        a, b, c = st.columns(3)
        maxp = a.number_input("Max hourly price (₹, 0 = any)", 0, 100000, 0, step=100, key=f"mp_{mode}")
        sort = b.selectbox("Sort by", ["Best match", "Lowest price", "Top rated"] + (["Nearest"] if mode == "offline" else []), key=f"s_{mode}")
        problem = c.text_input("Describe what you're stuck on (optional)", key=f"pr_{mode}", help="Used to rank tutors whose subjects match your problem.")
    results = S.search_tutors(mode, subj, "" if cat == "Any" else cat, city, maxp or None, me, problem, sort)
    st.caption(f"{len(results)} tutor(s) found")
    if not results:
        st.info("No tutors match yet. Try removing a filter. Tutors appear here after they set a price and availability.")
    for r in results:
        _tutor_card(me, r, mode)


def _tutor_card(me, r, mode):
    p = r["p"]
    est = S.price_guidance(p, me["city"]).get(mode)
    dist = f"{r['distance_km']:g} km away" if r["distance_km"] is not None else ("Distance unknown" if mode == "offline" else "")
    st.markdown(
        f"<div class='panel'><div style='display:flex;gap:16px;align-items:center'>{_avatar(p)}<div>"
        f"<h3 style='margin:0'>{_esc(p['name'])}</h3><div class='muted'>{_esc(p['headline'])}</div>"
        f"<span class='pill'>{_stars(r['rating'], r['reviews'])}</span><span class='pill'>{p['experience_years']} yrs</span>"
        f"<span class='pill'>📍 {_esc(p['city'] or 'Location not set')}{', ' + _esc(p['state']) if p['state'] else ''}</span>"
        + (f"<span class='pill'>{dist}</span>" if dist else "") + (f"<span class='pill'>Accepts EduCoins ≤{p['coin_max_pct']}%</span>" if p["accepts_coins"] else "") +
        f"</div></div><p><b>Subjects:</b> {_esc(p['subjects'])}<br><b>Qualifications:</b> {_esc(p['qualifications'] or '—')}"
        f"<br><b>Preferences:</b> {_esc(p['preferences'] or '—')}</p></div>", unsafe_allow_html=True)
    c = st.columns(3)
    for col, plan in zip(c, ("hourly", "weekly", "monthly")):
        d = S.plan_details(p, plan)
        col.metric(PLAN_LABEL[plan] + " (tutor's price)", _inr(d[0]) if d else "Not offered",
                   (f"{d[1]} session(s) · {d[2]:g} h" if d else None), delta_color="off")
    if est:
        verdict = pricing.compare_to_market(p["hourly_price"], est["low"], est["high"])
        extra = f" · est. travel fee ≈ {_inr(est['travel_fee'])}/session" if est["travel_fee"] else ""
        st.caption(f"Estimated local hourly range ({mode}): {_inr(est['low'])}–{_inr(est['high'])} — this tutor's price is {verdict}{extra}. "
                   "Estimates are guidance, not the tutor's price.")
    with st.expander(f"Book {p['name']}"):
        _booking_form(me, p, mode)


def _booking_form(me, p, mode):
    key = f"{mode}_{p['user_id']}"
    plans = [pl for pl in ("hourly", "weekly", "monthly") if S.plan_details(p, pl)]
    plan = st.radio("Plan", plans, format_func=lambda x: PLAN_LABEL[x], horizontal=True, key=f"plan_{key}")
    price, sessions, hours = S.plan_details(p, plan)
    st.info(f"{PLAN_LABEL[plan]}: {_inr(price)} · {sessions} session(s) · {hours:g} learning hour(s)"
            + (" · first session starts at the slot you pick; the tutor agrees the remaining dates with you in Messages." if plan != "hourly" else ""))
    day = st.date_input("Date", min_value=date.today(), max_value=date.today() + timedelta(days=60), key=f"d_{key}")
    slots = S.free_slots(p["user_id"], day, mode)
    if not slots:
        st.warning("No free slots on this date. Try another day.")
        return
    slot = st.selectbox("Time slot", slots, format_func=lambda s: s[11:16], key=f"slot_{key}")
    coins = 0
    if p["accepts_coins"] and p["coin_max_pct"]:
        bal = S.coin_balance(me["id"])
        cap = min(bal, (price * p["coin_max_pct"] // 100) // S.COIN_INR)
        st.caption(f"Tutor accepts up to {p['coin_max_pct']}% in EduCoins. You have {bal} EduCoins (usable here: up to {cap}).")
        if cap > 0:
            coins = st.number_input("EduCoins to use", 0, int(cap), 0, key=f"coins_{key}")
    due = price - coins * S.COIN_INR
    st.markdown(f"**Total {_inr(price)}** − EduCoins {_inr(coins * S.COIN_INR)} = **{_inr(due)} to pay**" + (" (free with EduCoins)" if due == 0 else ""))
    note = st.text_area("Message to tutor (what you want to learn)", key=f"note_{key}")
    if st.button("Request booking", key=f"book_{key}", type="primary"):
        ok, bid = _try(S.create_booking, me["id"], p["user_id"], mode, plan, slot, coins, note)
        if ok:
            st.success(f"Request #{bid} sent. Status: Pending. The tutor must accept it; you will pay only after acceptance. Track it in My Bookings.")


# ================================================================ Student bookings ===============
def my_bookings(me):
    st.markdown("## My bookings")
    rows = S.my_bookings(me["id"], "student")
    tabs = st.tabs(["Active", "Completed", "All & payment history"])
    with tabs[0]:
        active = [r for r in rows if r["status"] in ("Pending", "Accepted", "Confirmed")]
        if not active:
            st.info("No active bookings. Find a tutor under Offline Meetings or Online Classes.")
        for b in active:
            _student_booking(me, b)
    with tabs[1]:
        done = [r for r in rows if r["status"] == "Completed"]
        if not done:
            st.info("Completed classes will appear here, where you can leave a review.")
        for b in done:
            _student_booking(me, b)
    with tabs[2]:
        _history(me, "student")


def _student_booking(me, b):
    st.markdown(f"<div class='panel'><span class='pill'>{STATUS_ICON[b['status']]} {b['status'].upper()}</span>"
                f"<span class='pill'>{b['mode'].upper()}</span><span class='pill'>{PLAN_LABEL[b['plan']].upper()}</span>"
                f"<h3 style='margin:6px 0'>#{b['id']} · {_esc(b['other_name'])}</h3>"
                f"<p>{b['slot_start'].replace('T', ' ')} · {b['sessions']} session(s), {b['total_hours']:g} h<br>"
                f"Price {_inr(b['list_price'])} − EduCoins {b['coins_applied']} = <b>{_inr(b['amount_due'])}</b> · Payment: {b['payment_status'].replace('_', ' ')}</p></div>",
                unsafe_allow_html=True)
    if b["tutor_note"]:
        st.caption(f"Tutor note: {b['tutor_note']}")
    if b["status"] == "Pending":
        st.caption("Waiting for the tutor to accept or reject. Any EduCoins you used are held and returned if it is rejected or cancelled.")
    if b["status"] == "Accepted" and b["amount_due"] > 0:
        if b["location_note"]:
            st.caption(f"Meeting place: {b['location_note']}")
        if not S.payments_configured():
            st.warning("Online payment is not set up on this server yet, so this booking cannot be paid or confirmed. The site owner must configure Razorpay (see README).")
        else:
            a, c = st.columns(2)
            if a.button(f"Pay {_inr(b['amount_due'])}", key=f"pay_{b['id']}", type="primary"):
                ok, url = _try(S.start_payment, me["id"], b["id"])
                if ok:
                    st.session_state[f"payurl_{b['id']}"] = url
            url = st.session_state.get(f"payurl_{b['id']}") or b["payment_link_url"]
            if url:
                st.markdown(f"1. [Open the secure payment page]({url}) and pay.\n2. Come back and press **Verify payment**. The booking becomes Confirmed only after the server verifies the payment with Razorpay.")
            if b["payment_status"] == "pending" and c.button("Verify payment", key=f"ver_{b['id']}"):
                ok, res = _try(S.verify_payment, me["id"], b["id"])
                if ok:
                    if res == "paid":
                        st.success("Payment verified. Your booking is confirmed.")
                        st.rerun()
                    elif res == "failed":
                        st.error("That payment link expired or was cancelled. Press Pay to create a new one.")
                    else:
                        st.info("Payment not received yet. If you just paid, wait a few seconds and verify again.")
    if b["status"] == "Confirmed":
        if b["mode"] == "online" and b["meeting_url"]:
            st.markdown(f"**Video class:** [Join the secure video room]({b['meeting_url']})")
            if st.toggle("Open video room here", key=f"vid_{b['id']}"):
                components.iframe(b["meeting_url"], height=520)
        elif b["location_note"]:
            st.markdown(f"**Meeting place:** {b['location_note']}")
    if b["status"] in ("Pending", "Accepted", "Confirmed") and st.button("Cancel booking", key=f"sc_{b['id']}"):
        ok, refund_due = _try(S.cancel_booking, me["id"], b["id"])
        if ok:
            if refund_due:
                st.warning("You had paid for this booking. A refund is due; refunds are processed by the site owner through Razorpay.")
            st.rerun()
    if b["status"] == "Completed" and not b["my_rating"]:
        with st.form(f"rev_{b['id']}"):
            rating = st.slider("Rating", 1, 5, 5)
            comment = st.text_input("Comment")
            go = st.form_submit_button("Submit review")
        if go:
            ok, _ = _try(S.add_review, me["id"], b["id"], rating, comment, ok="Thanks for your review!")
            if ok:
                st.rerun()


# ================================================================ Wallet =========================
def wallet(me):
    st.markdown("## EduCoins wallet")
    w = S.wallet_summary(me["id"])
    c = st.columns(5)
    c[0].metric("Available", w["balance"])
    c[1].metric("Earned", w["earned"])
    c[2].metric("Spent", w["spent"])
    c[3].metric("Returned (refunds)", w["refunded"])
    c[4].metric("Pending rewards", w["pending"], help="Rewards waiting for the other person to verify, or for peer review.")
    st.caption(f"1 EduCoin = ₹{S.COIN_INR} of tutor-fee value. Coins are credited only by the server after verified activity; there is no manual way to add or edit coins.")
    t1, t2, t3 = st.tabs(["How to earn", "How to use", "Transaction history"])
    with t1:
        st.markdown(f"""
- **Skill swap session** verified by *both* partners: teacher **+{S.SWAP_TEACH_COINS}**, learner **+{S.SWAP_LEARN_COINS}** (sessions of at least {S.SWAP_MIN_MINUTES} min; max {S.SWAP_DAILY_CAP}/day each and {S.SWAP_PAIR_WEEKLY_CAP}/week per pair).
- **Helpful doubt answer** marked helpful by the student who asked: **+{S.DOUBT_HELPFUL_COINS}** (max {S.DOUBT_DAILY_CAP}/day).
- **Weekly challenge** approved in peer review: one fifth of the challenge XP (minimum 5).
""")
    with t2:
        q = S.ai_quota(me["id"])
        st.markdown(f"""
- **Tutor sessions:** pay part or all of a booking with EduCoins when the tutor accepts coins (each tutor sets their own maximum %). Free sessions are possible when a tutor accepts 100%.
- **Premium AI:** you get {S.FREE_AI_PER_MONTH} free AI courses/mind maps per month. Extra ones cost **{S.AI_COIN_COST} EduCoins** each. This month: {q['free_left']} free left.
""")
        st.info("Learning resources: not available yet (there is no resource catalogue in EduBridge to redeem against).")
        accepting = S.qall("SELECT p.user_id,u.name,p.coin_max_pct,p.hourly_price FROM tutor_profiles p JOIN users u ON u.id=p.user_id WHERE p.accepts_coins=1 AND p.coin_max_pct>0 AND p.hourly_price IS NOT NULL AND p.active=1")
        st.markdown("**Tutors accepting EduCoins**")
        for a in accepting:
            need = (a["hourly_price"] * a["coin_max_pct"] // 100) // S.COIN_INR
            st.write(f"{a['name']}: up to {need} coins on a {_inr(a['hourly_price'])} hourly session ({a['coin_max_pct']}%)")
        if not accepting:
            st.caption("No tutors accept EduCoins yet.")
    with t3:
        rows = S.wallet_history(me["id"])
        if not rows:
            st.info("No transactions yet.")
        else:
            st.dataframe([{"When": r["created"], "Type": r["kind"], "Coins": f"{r['amount']:+d}", "Reason": r["reason"]} for r in rows],
                         use_container_width=True, hide_index=True)


# ================================================================ Student dashboard ==============
def student_dashboard(me):
    d = S.student_dashboard(me["id"])
    T.hero(f"{T.greeting()}, {me['name'].split()[0]}", "Your tutors, classes, courses, swaps and coins in one place.", "STUDENT DASHBOARD", "🚀")
    c = st.columns(4)
    c[0].metric("EduCoins", d["wallet"]["balance"], f"+{d['wallet']['pending']} pending" if d["wallet"]["pending"] else None)
    c[1].metric("Skill XP", me["xp"])
    c[2].metric("Active bookings", d["offline"] + d["online"])
    c[3].metric("Unread notifications", d["unread"])
    a, b, c3 = st.columns(3)
    a.markdown(f"<div class='panel'><h3>🧑‍🏫 My tutors</h3><p>{', '.join(_esc(t['name']) for t in d['tutors']) or 'No tutors yet'}</p></div>", unsafe_allow_html=True)
    b.markdown(f"<div class='panel'><h3>🤝 Offline meetings</h3><p>{d['offline']} open request(s)/session(s)</p></div>", unsafe_allow_html=True)
    c3.markdown(f"<div class='panel'><h3>💻 Online classes</h3><p>{d['online']} open request(s)/session(s)</p></div>", unsafe_allow_html=True)
    a, b, c3 = st.columns(3)
    a.markdown(f"<div class='panel'><h3>🔄 Skill swapping</h3><p>{d['swaps_active']} active swap(s)</p></div>", unsafe_allow_html=True)
    b.markdown(f"<div class='panel'><h3>🧠 Mind maps</h3><p>{d['mindmaps']} saved</p></div>", unsafe_allow_html=True)
    c3.markdown(f"<div class='panel'><h3>📒 Bookings</h3><p>{d['bookings']} total · {d['completed']} completed</p></div>", unsafe_allow_html=True)
    st.markdown("### Jump back in")
    q1, q2, q3, q4 = st.columns(4)
    with q1: T.nav_card("🎓", "AI Courses", "Build a personal course", "AI Courses", "qa_courses")
    with q2: T.nav_card("🧠", "Mind Maps", "Map any topic visually", "AI Mind Maps", "qa_maps")
    with q3: T.nav_card("🤝", "Find a tutor", "Book offline meetings", "Offline Meetings", "qa_tutor")
    with q4: T.nav_card("👥", "Study rooms", "Learn together", "Learning Rooms", "qa_rooms")
    st.markdown("### Learning progress · AI courses")
    if not d["courses"]:
        T.empty_state("🎓", "No AI courses yet", "Create a personalised course and track your progress here.", "Create a course", "AI Courses", key="empty_courses")
    for row in d["courses"][:6]:
        pct = S.course_percent(me["id"], row)
        st.write(f"**{row['topic']}** · {row['subject']} · {row['level']}")
        st.progress(pct / 100, text=f"{pct}% complete")
    l, r = st.columns(2)
    with l:
        st.markdown("### Upcoming sessions")
        if not d["upcoming"] and not d["swap_upcoming"]:
            st.caption("Nothing scheduled.")
        for b in d["upcoming"]:
            st.markdown(f"<div class='panel'>{'💻' if b['mode'] == 'online' else '🤝'} <b>{b['slot_start'].replace('T', ' ')}</b> with {_esc(b['other_name'])} · {b['plan']}</div>", unsafe_allow_html=True)
        for s in d["swap_upcoming"]:
            st.markdown(f"<div class='panel'>🔄 <b>{s['scheduled'].replace('T', ' ')}</b> skill swap: {_esc(s['topic'])}</div>", unsafe_allow_html=True)
    with r:
        st.markdown("### Completed sessions")
        done = S.qall("""SELECT b.*,u.name other_name FROM bookings b JOIN users u ON u.id=b.tutor_id WHERE b.student_id=? AND b.status='Completed' ORDER BY b.slot_start DESC LIMIT 5""", (me["id"],))
        sw = S.qone("SELECT COUNT(*) n FROM swap_sessions WHERE (teacher_id=? OR learner_id=?) AND status='Completed'", (me["id"], me["id"]))["n"]
        if not done and not sw:
            st.caption("No completed sessions yet.")
        for b in done:
            st.markdown(f"<div class='panel'>✅ {b['slot_start'][:10]} with {_esc(b['other_name'])} · {b['plan']}</div>", unsafe_allow_html=True)
        if sw:
            st.caption(f"{sw} completed skill-swap session(s)")
