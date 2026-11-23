import streamlit as st
import sqlite3, hashlib, secrets, re, os
from datetime import timedelta
from pathlib import Path
from datetime import datetime, date

import services as S
import ui_market as M
import ui_ai as AI
import pricing
import theme as T
import auth_service as A      # real authentication (sessions, OAuth, password reset)
import auth_ui as AUI         # the futuristic sign-in screen (Streamlit component)

DB = Path(os.environ.get("EDUBRIDGE_DB", Path(__file__).with_name("edubridge.db")))
st.set_page_config(page_title="EduBridge AI", page_icon="🎓", layout="wide")

T.inject()

def db():
    c=sqlite3.connect(DB,check_same_thread=False); c.row_factory=S.row_factory; return c
# Password hashing now lives in auth_service (identical PBKDF2 format, so existing accounts keep working).
pw_hash=A.pw_hash
pw_ok=A.pw_ok
def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,email TEXT UNIQUE,phone TEXT UNIQUE,password TEXT,role TEXT,skills TEXT DEFAULT '',wants TEXT DEFAULT '',bio TEXT DEFAULT '',languages TEXT DEFAULT 'English',xp INTEGER DEFAULT 0,created TEXT);
        CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,student INTEGER,tutor INTEGER,topic TEXT,note TEXT,status TEXT DEFAULT 'Pending',created TEXT);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,sender INTEGER,receiver INTEGER,body TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS swaps(id INTEGER PRIMARY KEY,title TEXT,creator INTEGER,members TEXT,plan TEXT,status TEXT DEFAULT 'Open',created TEXT);
        CREATE TABLE IF NOT EXISTS progress(id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT,detail TEXT,done INTEGER DEFAULT 0,created TEXT);
        CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,user_id INTEGER,body TEXT,read INTEGER DEFAULT 0,created TEXT);
        CREATE TABLE IF NOT EXISTS portfolio(id INTEGER PRIMARY KEY,user_id INTEGER,kind TEXT,title TEXT,detail TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS endorsements(id INTEGER PRIMARY KEY,from_user INTEGER,to_user INTEGER,skill TEXT,note TEXT,created TEXT,UNIQUE(from_user,to_user,skill));
        CREATE TABLE IF NOT EXISTS rooms(id INTEGER PRIMARY KEY,title TEXT,owner INTEGER,topic TEXT,scheduled TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS room_members(room_id INTEGER,user_id INTEGER,UNIQUE(room_id,user_id));
        CREATE TABLE IF NOT EXISTS room_posts(id INTEGER PRIMARY KEY,room_id INTEGER,user_id INTEGER,kind TEXT,content TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS swap_members(swap_id INTEGER,user_id INTEGER,status TEXT DEFAULT 'Pending',joined TEXT,PRIMARY KEY(swap_id,user_id));
        CREATE TABLE IF NOT EXISTS doubts(id INTEGER PRIMARY KEY,student_id INTEGER,question TEXT,subject TEXT,language TEXT,urgent INTEGER DEFAULT 0,status TEXT DEFAULT 'Open',created TEXT);
        CREATE TABLE IF NOT EXISTS doubt_replies(id INTEGER PRIMARY KEY,doubt_id INTEGER,tutor_id INTEGER,reply TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS tutor_sessions(id INTEGER PRIMARY KEY,student_id INTEGER,tutor_id INTEGER,topic TEXT,problem TEXT,language TEXT,session_type TEXT,duration INTEGER,status TEXT DEFAULT 'Pending',scheduled TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS session_notes(id INTEGER PRIMARY KEY,session_id INTEGER,concepts TEXT,explanations TEXT,practice TEXT,homework TEXT,next_goals TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS challenges(id INTEGER PRIMARY KEY,title TEXT,description TEXT,reward INTEGER,deadline TEXT);
        CREATE TABLE IF NOT EXISTS challenge_done(user_id INTEGER,challenge_id INTEGER,UNIQUE(user_id,challenge_id));
        CREATE TABLE IF NOT EXISTS challenge_submissions(
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            challenge_id INTEGER NOT NULL,
            evidence TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            reviewer_id INTEGER,
            created TEXT NOT NULL,
            reviewed TEXT,
            UNIQUE(user_id,challenge_id)
        );
        """)
        # Add columns to older prototype databases, if needed.
        cols={r["name"] for r in c.execute("PRAGMA table_info(users)")}
        for col,definition in [("phone","TEXT"),("xp","INTEGER DEFAULT 0"),("languages","TEXT DEFAULT 'English'")]:
            if col not in cols: c.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
        room_cols={r["name"] for r in c.execute("PRAGMA table_info(rooms)")}
        if "room_code" not in room_cols:
            c.execute("ALTER TABLE rooms ADD COLUMN room_code TEXT")
        # Give existing rooms a unique join code when upgrading an older database.
        existing=c.execute("SELECT id FROM rooms WHERE room_code IS NULL OR room_code='' ").fetchall()
        for row in existing:
            code=secrets.token_hex(4).upper()
            while c.execute("SELECT 1 FROM rooms WHERE room_code=?",(code,)).fetchone():
                code=secrets.token_hex(4).upper()
            c.execute("UPDATE rooms SET room_code=? WHERE id=?",(code,row["id"]))
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rooms_room_code ON rooms(room_code)")
        # Migrate the prototype's comma-separated skill-swap membership into a reliable table.
        for swap in c.execute("SELECT id,creator,members FROM swaps").fetchall():
            member_ids={int(x) for x in (swap["members"] or "").split(",") if x.strip().isdigit()}
            member_ids.add(swap["creator"])
            for member_id in member_ids:
                c.execute("INSERT OR IGNORE INTO swap_members(swap_id,user_id,status,joined) VALUES(?,?,?,?)",
                          (swap["id"],member_id,"Accepted",datetime.now().isoformat(timespec="minutes")))
        # Weekly challenge seeds are intentionally generic sample challenges.
        if c.execute("SELECT COUNT(*) FROM challenges").fetchone()[0]==0:
            c.executemany("INSERT INTO challenges(title,description,reward,deadline) VALUES(?,?,?,?)",[
                ("Teach one concept","Help a peer understand one concept and record what you taught.",30,"This week"),
                ("Build a mini project","Complete a small project and add it to your portfolio.",50,"This week"),
                ("Learn something new","Record a skill you learned from another student.",25,"This week")])
init()
S.init_schema()   # EduBridge v2 tables (tutor marketplace, bookings, EduCoins, AI courses, mind maps)
A.init_auth_schema()   # new auth tables only (identities, sessions, reset tokens, OAuth state); users table untouched

def q(sql,p=(),one=False):
    with db() as c:
        rows=c.execute(sql,p).fetchall()
        return rows[0] if one and rows else (None if one else rows)
def run(sql,p=()):
    with db() as c: return c.execute(sql,p).lastrowid
def user(uid): return q("SELECT * FROM users WHERE id=?",(uid,),True)
def notify(uid,msg): run("INSERT INTO notifications(user_id,body,created) VALUES(?,?,?)",(uid,msg,datetime.now().isoformat(timespec="minutes")))
def award(uid,points,reason):
    run("UPDATE users SET xp=xp+? WHERE id=?",(points,uid)); notify(uid,f"+{points} XP: {reason}")
def skills(s): return [x.strip() for x in (s or "").split(",") if x.strip()]

def auth():
    # Signed-out screen: the animated EduBridge login/register/forgot-password UI (auth_component/),
    # backed by auth_service (real SQLite accounts, PBKDF2, server-side sessions, Google/GitHub OAuth).
    AUI.render()

def page_header(title,sub):
    T.hero(title,sub)
def portfolio_page(me):
    st.markdown("## Digital skill portfolio")
    st.caption("Your public learning record: what you teach, learn, build and contribute.")
    tabs=st.tabs(["Skills taught","Skills learned","Projects","Peer endorsements","Add entry"])
    for tab,kind in zip(tabs,["Taught","Learned","Project","Endorsement"]):
        with tab:
            rows=q("SELECT * FROM portfolio WHERE user_id=? AND kind=? ORDER BY id DESC",(me["id"],kind))
            if kind=="Endorsement":
                rows=q("SELECT e.*,u.name FROM endorsements e JOIN users u ON u.id=e.from_user WHERE e.to_user=? ORDER BY e.id DESC",(me["id"],))
                for r in rows: st.markdown(f"<div class='panel'><b>{r['skill']}</b> · endorsed by {r['name']}<p>{r['note']}</p></div>",unsafe_allow_html=True)
            else:
                if not rows: st.info("No entries yet. Add your first item in the Add entry tab.")
                for r in rows: st.markdown(f"<div class='panel'><b>{r['title']}</b><p>{r['detail']}</p><small>{r['created'][:10]}</small></div>",unsafe_allow_html=True)
    with tabs[4]:
        with st.form("portfolio_add"):
            kind=st.selectbox("Entry type",["Taught","Learned","Project"])
            title=st.text_input("Skill or project name")
            detail=st.text_area("Description / what you achieved")
            save=st.form_submit_button("Add to portfolio")
        if save and title.strip():
            run("INSERT INTO portfolio(user_id,kind,title,detail,created) VALUES(?,?,?,?,?)",(me["id"],kind,title.strip(),detail.strip(),datetime.now().isoformat(timespec="minutes")))
            award(me["id"],10,f"portfolio entry added: {title}"); st.success("Added to your portfolio."); st.rerun()
    st.markdown("### Endorse a peer")
    peers=q("SELECT id,name FROM users WHERE id<>? AND role='student' ORDER BY name",(me["id"],))
    if peers:
        with st.form("endorse"):
            target=st.selectbox("Student",peers,format_func=lambda x:x["name"])
            skill=st.text_input("Skill to endorse")
            note=st.text_input("Why do you recommend this skill?")
            send=st.form_submit_button("Send endorsement")
        if send and skill.strip():
            try:
                run("INSERT INTO endorsements(from_user,to_user,skill,note,created) VALUES(?,?,?,?,?)",(me["id"],target["id"],skill.strip(),note.strip(),datetime.now().isoformat(timespec="minutes")))
                notify(target["id"],f"{me['name']} endorsed your {skill} skill."); award(me["id"],5,"peer endorsement given"); award(target["id"],5,"peer endorsement received"); st.success("Endorsement saved.")
            except sqlite3.IntegrityError: st.warning("You have already endorsed this skill for that peer.")

def _language_options():
    return ["English", "Telugu", "Hindi", "Tamil", "Kannada", "Malayalam", "Bengali", "Marathi", "Other"]

def _tokens(text):
    stop={"the","and","for","with","from","that","this","have","want","need","help","how","what","why","when","where","can","could","should","would","about","into","your","you","are","but","not","only","understand","understanding"}
    return {x for x in re.findall(r"[a-zA-Z0-9+#.]+",(text or "").lower()) if len(x)>2 and x not in stop}

def tutor_matching(me):
    st.markdown("## Find a tutor for the problem you're facing")
    st.caption("Describe what you can do already, exactly where you get stuck, and what kind of help would move you forward.")
    with st.form("problem_tutor_match"):
        problem=st.text_area("Describe your actual problem",placeholder="I understand Python theory and syntax, but I freeze when I have to turn a problem into a working program. I need practice breaking problems into steps.",height=130)
        subject=st.text_input("Subject or skill",placeholder="Python programming")
        language=st.selectbox("Preferred teaching language",_language_options())
        session_type=st.selectbox("What kind of help do you want?",["Regular tutoring", "10-minute free trial", "Urgent 10-minute help"])
        schedule=st.text_input("Preferred time (optional)",placeholder="Today after 5 PM")
        find=st.form_submit_button("Find matching tutors",use_container_width=True)
    if find:
        if len(problem.strip())<20 or not subject.strip():
            st.error("Please describe the difficulty in at least 20 characters and enter a subject.")
        else:
            st.session_state["tutor_match_context"]={"problem":problem.strip(),"subject":subject.strip(),"language":language,"session_type":session_type,"schedule":schedule.strip(),"owner":me["id"]}
    ctx=st.session_state.get("tutor_match_context")
    if ctx and ctx.get("owner")==me["id"]:
        tutors=q("SELECT * FROM users WHERE role='tutor' AND id<>? ORDER BY name",(me["id"],))
        terms=_tokens(ctx["subject"]+" "+ctx["problem"])
        ranked=[]
        for t in tutors:
            offered=_tokens((t["skills"] or "")+" "+(t["bio"] or ""))
            overlap=terms & offered
            score=len(overlap)
            if ctx["subject"].lower() in (t["skills"] or "").lower(): score+=5
            language_match=ctx["language"].lower() in (t["languages"] or "english").lower()
            if language_match: score+=4
            ranked.append((score,t,sorted(overlap),language_match))
        ranked.sort(key=lambda x:(x[0],x[1]["xp"]),reverse=True)
        if not ranked:
            st.info("No tutor profiles are registered yet. Ask classmates or invite a tutor to create a profile.")
        else:
            st.markdown("### Best available matches")
            for score,t,matched,language_match in ranked[:8]:
                st.markdown(f"<div class='panel'><h3>{t['name']}</h3><p><b>Teaches:</b> {t['skills'] or 'Skills not listed'}</p><p><b>Languages:</b> {t['languages'] or 'English'}</p><p><b>Skill match:</b> {', '.join(matched) if matched else 'Profile needs more skill details'}</p><p><b>Language match:</b> {'Yes' if language_match else 'Not listed — confirm before booking'}</p><p>{t['bio'] or ''}</p><span class='pill'>{t['xp']} XP</span></div>",unsafe_allow_html=True)
                existing=q("SELECT id FROM tutor_sessions WHERE student_id=? AND tutor_id=? AND topic=? AND status IN ('Pending','Accepted')",(me["id"],t["id"],ctx["subject"]),True)
                if existing:
                    st.caption("You already have an active request with this tutor for this topic.")
                elif st.button(f"Request {ctx['session_type'].lower()} with {t['name']}",key=f"tutor_request_{t['id']}"):
                    duration=10 if ctx["session_type"]!="Regular tutoring" else 45
                    run("INSERT INTO tutor_sessions(student_id,tutor_id,topic,problem,language,session_type,duration,scheduled,created) VALUES(?,?,?,?,?,?,?,?,?)",
                        (me["id"],t["id"],ctx["subject"],ctx["problem"],ctx["language"],ctx["session_type"],duration,ctx["schedule"],datetime.now().isoformat(timespec="minutes")))
                    notify(t["id"],f"{me['name']} requested {ctx['session_type'].lower()} for {ctx['subject']} in {ctx['language']}.")
                    st.success("Request sent. The tutor can accept it in Tutor Workspace.")
                    st.rerun()
        st.caption("Matching uses profile skill keywords and declared teaching languages. It is a relevance aid, not a guarantee of tutor quality or availability.")

def doubt_solver(me):
    st.markdown("## AI Doubt Solver & tutor help desk")
    st.caption("Get a guided breakdown now, post your doubt for a tutor reply, or request urgent 10-minute help.")
    tabs=st.tabs(["Solve a doubt now", "Ask a tutor", "Tutor replies"])
    with tabs[0]:
        question=st.text_area("What are you stuck on?",placeholder="I understand Python loops, but I don't know how to use them to solve a real problem.",key="solver_question")
        level=st.selectbox("Explanation level",["Beginner", "School level", "Detailed"],key="solver_level")
        if st.button("Break down my doubt",disabled=not question.strip(),key="solver_run"):
            words=_tokens(question)
            st.markdown("### Let's break the problem down")
            st.markdown("**1. Identify the goal**")
            st.write("Rewrite the question as one clear outcome. What should the program, calculation, or explanation produce?")
            st.markdown("**2. Identify what you already know**")
            st.write("Separate the parts you understand from the exact step where you get stuck.")
            st.markdown("**3. Work through a tiny example**")
            if any(x in words for x in {"python","loop","programming","code","function","list","array"}):
                st.code("# Example: build the solution in small steps\nnumbers = [2, 4, 6]\ntotal = 0\nfor number in numbers:\n    total += number\nprint(total)",language="python")
                st.write("Trace one iteration at a time. Check how the variable changes before moving to the next item.")
            elif any(x in words for x in {"sql","database","query","table"}):
                st.code("SELECT student_name, mark\nFROM results\nWHERE mark >= 50;",language="sql")
                st.write("Read the query in three pieces: which columns, which table, and which rows should be returned?")
            elif any(x in words for x in {"html","css","webpage","website"}):
                st.code("<h1>My page</h1>\n<style>h1 { color: teal; }</style>",language="html")
                st.write("HTML provides the page structure; CSS changes how that structure is presented.")
            else:
                st.write("Make a small example with two or three inputs. Predict the result, work it out one step at a time, then compare your result.")
            st.markdown("**4. Check your understanding**")
            st.write("Explain the solution in your own words. If you cannot explain one step, that is the best place to ask a tutor for help.")
            st.info("This built-in tutor uses guided problem-solving templates and examples. It does not call a hosted generative-AI model, so verify answers against course material.")
    with tabs[1]:
        with st.form("post_doubt",clear_on_submit=True):
            subject=st.text_input("Subject / topic",placeholder="Python functions")
            question=st.text_area("Your question and what you tried",placeholder="Include the error, expected result, and what you have tried.")
            language=st.selectbox("Preferred reply language",_language_options(),key="doubt_language")
            urgent=st.checkbox("I need urgent 10-minute help")
            post=st.form_submit_button("Post doubt for tutors",use_container_width=True)
        if post:
            if not subject.strip() or len(question.strip())<15: st.error("Add a topic and explain your question in at least 15 characters.")
            else:
                did=run("INSERT INTO doubts(student_id,question,subject,language,urgent,created) VALUES(?,?,?,?,?,?)",(me["id"],question.strip(),subject.strip(),language,int(urgent),datetime.now().isoformat(timespec="minutes")))
                tutors=q("SELECT id,skills FROM users WHERE role='tutor'")
                terms=_tokens(subject+" "+question)
                for t in tutors:
                    if terms & _tokens(t["skills"]): notify(t["id"],f"New {'URGENT ' if urgent else ''}doubt posted in {subject}; preferred language: {language}.")
                st.success("Your doubt is posted. Tutors can reply in the Tutor replies tab. Urgent requests are marked for priority; availability is not guaranteed.")
                st.rerun()
        st.markdown("### Your posted doubts")
        mine=q("SELECT * FROM doubts WHERE student_id=? ORDER BY id DESC",(me["id"],))
        for d in mine:
            st.markdown(f"<div class='panel'><span class='pill'>{'URGENT · 10 MIN' if d['urgent'] else 'STANDARD'}</span><b>{d['subject']}</b><p>{d['question']}</p><small>{d['language']} · {d['status']} · {d['created']}</small></div>",unsafe_allow_html=True)
            replies=q("SELECT r.*,u.name FROM doubt_replies r JOIN users u ON u.id=r.tutor_id WHERE r.doubt_id=? ORDER BY r.id",(d["id"],))
            for r in replies:
                st.info(f"{r['name']}: {r['reply']}")
                if st.button("👍 This helped (rewards the helper with EduCoins)",key=f"helpful_{r['id']}"):
                    try: S.mark_reply_helpful(me["id"],r["id"]); st.success("Thanks! The helper earned EduCoins.")
                    except S.ServiceError as e: st.warning(str(e))
    with tabs[2]:
        if me["role"]=="tutor":
            incoming=q("SELECT d.*,u.name FROM doubts d JOIN users u ON u.id=d.student_id WHERE d.status='Open' ORDER BY d.urgent DESC,d.id DESC")
            if not incoming: st.info("No open doubts right now.")
            for d in incoming:
                with st.expander(f"{'🚨 URGENT · ' if d['urgent'] else ''}{d['subject']} · {d['name']} · {d['language']}"):
                    st.write(d["question"])
                    with st.form(f"doubt_reply_{d['id']}"):
                        reply=st.text_area("Your explanation / answer")
                        send=st.form_submit_button("Send tutor reply")
                    if send and reply.strip():
                        run("INSERT INTO doubt_replies(doubt_id,tutor_id,reply,created) VALUES(?,?,?,?)",(d["id"],me["id"],reply.strip(),datetime.now().isoformat(timespec="minutes")))
                        run("UPDATE doubts SET status='Answered' WHERE id=?",(d["id"],))
                        notify(d["student_id"],f"{me['name']} replied to your doubt about {d['subject']}.")
                        st.success("Reply sent."); st.rerun()
        else: st.info("Tutor replies to your posted doubts appear under Ask a tutor.")

def skill_swap(me):
    st.markdown("## Tutor + Skill Swap")
    st.caption("One connected learning community: find a tutor for your current challenge, then exchange a skill you can teach in return.")
    with st.expander("Part 1 — Find a tutor for your learning gap", expanded=True):
        tutor_matching(me)
    st.markdown("---")
    st.markdown("### Skill swapping")
    with st.expander("My skill profile (what I can teach / want to learn)"):
        with st.form("swap_skill_profile"):
            teach=st.text_input("Skills I can teach",me["skills"] or "",placeholder="Excel, UI Design")
            learn=st.text_input("Skills I want to learn",me["wants"] or "",placeholder="Python, Statistics")
            saved=st.form_submit_button("Save skill profile")
        if saved:
            run("UPDATE users SET skills=?,wants=? WHERE id=?",(teach.strip(),learn.strip(),me["id"])); st.success("Skill profile saved."); st.rerun()
    if not S.split_list(me["skills"]) or not S.split_list(me["wants"]):
        st.info("Add at least one skill you can teach and one you want to learn to get matches.")
    st.markdown("#### Learners who match your skills")
    matches=S.swap_matches(me)
    if not matches: st.caption("No matches yet. Other learners appear here when their skills overlap with what you want to learn or can teach.")
    for m in matches[:8]:
        u=m["user"]
        badge="<span class='pill'>MUTUAL MATCH</span>" if m["mutual"] else ""
        st.markdown(f"<div class='panel'><b>{u['name']}</b> {badge}"
                    f"<p>{'Can teach you: '+', '.join(m['learn']) if m['learn'] else ''}{' · ' if m['learn'] and m['teach'] else ''}{'Wants to learn from you: '+', '.join(m['teach']) if m['teach'] else ''}</p></div>",unsafe_allow_html=True)
        if st.button(f"Invite {u['name']} to swap",key=f"swap_pick_{u['id']}"):
            st.session_state["swap_partner"]=u["id"]; st.rerun()
    st.markdown("#### Send a skill-swap request")
    peers=q("SELECT * FROM users WHERE id<>? ORDER BY name",(me["id"],))
    pre=st.session_state.get("swap_partner")
    pidx=next((i for i,p in enumerate(peers) if p["id"]==pre),0)
    with st.form("swap_create"):
        title=st.text_input("Learning circle name",placeholder="Python + Excel exchange")
        offer=st.text_input("A skill I can teach",placeholder="Excel dashboards")
        want=st.text_input("A skill I want to learn",placeholder="Python problem solving")
        partner=st.selectbox("Choose a peer",peers,index=pidx,format_func=lambda x:f"{x['name']} · teaches {x['skills'] or 'skills not listed'}") if peers else None
        plan=st.text_area("Exchange plan",placeholder="I'll teach Excel formulas; my partner will coach me through Python exercises.")
        create=st.form_submit_button("Send skill-swap invitation",use_container_width=True)
    if create:
        if not partner: st.error("There is nobody to invite yet.")
        else:
            try:
                S.swap_invite(me["id"],partner["id"],title,offer,want,plan)
                st.session_state.pop("swap_partner",None)
                st.success("Invitation sent. Your partner must accept before the exchange (and its room) becomes active.")
            except S.ServiceError as e: st.error(str(e))
    st.markdown("### Invitations for you")
    invites=q("SELECT sm.*,s.title,s.plan,s.creator,u.name AS inviter FROM swap_members sm JOIN swaps s ON s.id=sm.swap_id JOIN users u ON u.id=s.creator WHERE sm.user_id=? AND sm.status='Pending' ORDER BY sm.swap_id DESC",(me["id"],))
    if not invites: st.caption("No pending invitations.")
    for inv in invites:
        st.markdown(f"<div class='panel'><b>{inv['title']}</b><p>From {inv['inviter']}</p><p>{inv['plan']}</p></div>",unsafe_allow_html=True)
        a,b=st.columns(2)
        with a:
            if st.button("Accept invitation",key=f"swap_accept_{inv['swap_id']}"):
                try: S.swap_respond(me["id"],inv["swap_id"],True); st.rerun()
                except S.ServiceError as e: st.error(str(e))
        with b:
            if st.button("Decline",key=f"swap_decline_{inv['swap_id']}"):
                try: S.swap_respond(me["id"],inv["swap_id"],False); st.rerun()
                except S.ServiceError as e: st.error(str(e))
    st.markdown("### Your active exchanges")
    groups=q("SELECT s.* FROM swaps s JOIN swap_members m ON m.swap_id=s.id WHERE m.user_id=? AND m.status='Accepted' ORDER BY s.id DESC",(me["id"],))
    if not groups: st.info("No active skill exchanges yet. Send a request above or accept an invitation.")
    for g in groups:
        members=q("SELECT u.id,u.name FROM swap_members m JOIN users u ON u.id=m.user_id WHERE m.swap_id=? AND m.status='Accepted' ORDER BY u.name",(g["id"],))
        with st.expander(f"{g['title']} · {', '.join(x['name'] for x in members)}",expanded=len(groups)==1):
            st.write(g["plan"])
            try:
                room=S.swap_room(me["id"],g["id"])
                st.markdown(f"<span class='pill'>ROOM CODE {room['room_code']}</span> Your swap room is under **Learning Rooms** → *{room['title']}* (chat, tasks, resources). You can also use **Messages**.",unsafe_allow_html=True)
            except S.ServiceError as e: st.warning(str(e))
            st.markdown("**Sessions**")
            for ss in S.swap_sessions(g["id"]):
                who={x["id"]:x["name"] for x in members}
                st.write(f"{'✅' if ss['status']=='Completed' else '🗓️'} {ss['scheduled'].replace('T',' ')} · {ss['topic']} · {who.get(ss['teacher_id'],'?')} teaches · {ss['duration_min']} min · {ss['status']}")
                if ss["status"]=="Scheduled":
                    mine=ss["teacher_confirmed"] if ss["teacher_id"]==me["id"] else ss["learner_confirmed"]
                    if mine: st.caption("You confirmed. Waiting for your partner.")
                    elif st.button("Mark completed (both partners must confirm)",key=f"swap_done_{ss['id']}"):
                        try: st.success(S.confirm_swap_session(me["id"],ss["id"]))
                        except S.ServiceError as e: st.error(str(e))
            if len(members)==2:
                with st.form(f"swap_sched_{g['id']}",clear_on_submit=True):
                    topic=st.text_input("Session topic")
                    teacher=st.selectbox("Who teaches this session?",members,format_func=lambda x:x["name"])
                    d1,d2,d3=st.columns(3)
                    day=d1.date_input("Date",min_value=date.today()); tm=d2.time_input("Time"); mins=d3.number_input("Minutes",10,180,30,step=5)
                    sched=st.form_submit_button("Schedule session")
                if sched:
                    try:
                        S.schedule_swap_session(me["id"],g["id"],teacher["id"],topic,datetime.combine(day,tm).isoformat(timespec="minutes"),int(mins))
                        st.success("Session scheduled."); st.rerun()
                    except S.ServiceError as e: st.error(str(e))
            else: st.caption("Sessions can be scheduled once the swap has two active partners.")

def rooms_page(me):
    st.markdown("## Collaborative learning rooms")
    st.caption("Create a room for your study group, share its code, and collaborate through posts and resources.")

    with st.expander("How to create or join a learning room", expanded=True):
        st.markdown("""
        **Create a room**
        1. Open the **Create a room** section below.
        2. Enter a room name and the topic you want to study.
        3. Optionally add the session date or time.
        4. Select **Create room**. You will automatically become a member.
        5. Copy the room code and share it with the students you want to invite.

        **Join a room**
        1. Ask the room creator for the room code.
        2. Enter the code in **Join with a room code**.
        3. Select **Join room**. The room will then appear under **Your rooms**.

        **Collaborate**
        - Select one of your rooms to view its discussion.
        - Use the post form to share a discussion, task, or resource link.
        """)

    with st.expander("＋ Create a room", expanded=True):
        with st.form("room_new"):
            title=st.text_input("Room name",placeholder="e.g. Python beginners study group")
            topic=st.text_input("Learning topic",placeholder="e.g. Python basics")
            when=st.text_input("Session schedule (optional)",placeholder="e.g. Saturday 4:00 PM")
            make=st.form_submit_button("Create room",use_container_width=True)
        if make:
            if not title.strip() or not topic.strip():
                st.error("Enter both a room name and a learning topic.")
            else:
                code=secrets.token_hex(4).upper()
                while q("SELECT id FROM rooms WHERE room_code=?",(code,),True):
                    code=secrets.token_hex(4).upper()
                rid=run("INSERT INTO rooms(title,owner,topic,scheduled,created,room_code) VALUES(?,?,?,?,?,?)",
                        (title.strip(),me["id"],topic.strip(),when.strip(),datetime.now().isoformat(timespec="minutes"),code))
                run("INSERT OR IGNORE INTO room_members(room_id,user_id) VALUES(?,?)",(rid,me["id"]))
                st.session_state["new_room_code"]=code
                st.toast("Learning room created! 🎉",icon="✅")
                st.rerun()
    if st.session_state.get("new_room_code"):
        st.success(f"Your new room code is: {st.session_state['new_room_code']} — share this code with classmates.")
        if st.button("Hide room code",key="hide_room_code"):
            st.session_state.pop("new_room_code",None); st.rerun()

    with st.expander("＋ Join with a room code"):
        with st.form("room_join"):
            join_code=st.text_input("Room code",placeholder="Enter the 8-character code").strip().upper()
            join=st.form_submit_button("Join room",use_container_width=True)
        if join:
            room=q("SELECT * FROM rooms WHERE room_code=?",(join_code,),True)
            if not join_code:
                st.error("Enter a room code.")
            elif not room:
                st.error("No room was found with that code. Check the code with the room creator.")
            else:
                run("INSERT OR IGNORE INTO room_members(room_id,user_id) VALUES(?,?)",(room["id"],me["id"]))
                st.toast(f"You joined '{room['title']}' 👋",icon="✅")
                st.rerun()

    rooms=q("SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.user_id=? ORDER BY r.id DESC",(me["id"],))
    st.markdown("### Your rooms")
    if not rooms:
        T.empty_state("👥","No rooms yet","Create a study room above, or join one with a code from a classmate.")
        return
    room=st.selectbox("Choose a room",rooms,format_func=lambda x:f"{x['title']} · {x['topic']}")
    members=q("SELECT u.name FROM room_members m JOIN users u ON u.id=m.user_id WHERE m.room_id=? ORDER BY u.name",(room["id"],))
    st.markdown(f"<div class='panel'><h3>{room['title']}</h3><p><b>Topic:</b> {room['topic']}<br><b>Schedule:</b> {room['scheduled'] or 'To be decided'}<br><b>Room code:</b> {room['room_code']}<br><b>Members ({len(members)}):</b> {', '.join(x['name'] for x in members)}</p></div>",unsafe_allow_html=True)
    st.caption("Share the room code only with people you want to invite.")
    posts=q("SELECT p.*,u.name FROM room_posts p JOIN users u ON u.id=p.user_id WHERE room_id=? ORDER BY p.id DESC",(room["id"],))
    st.markdown("### Room discussion and resources")
    if not posts: st.info("No posts yet. Start the discussion by sharing a message, task, or resource.")
    for post in posts:
        st.markdown(f"<div class='panel'><b>{post['kind']} · {post['name']}</b><p>{post['content']}</p><small>{post['created']}</small></div>",unsafe_allow_html=True)
    with st.form("room_post",clear_on_submit=True):
        kind=st.selectbox("Post type",["Discussion","Task","Resource"])
        content=st.text_area("Message, task or resource link")
        post=st.form_submit_button("Share with room")
    if post:
        if not content.strip(): st.error("Write a message or add a resource before sharing.")
        else:
            run("INSERT INTO room_posts(room_id,user_id,kind,content,created) VALUES(?,?,?,?,?)",
                (room["id"],me["id"],kind,content.strip(),datetime.now().isoformat(timespec="minutes")))
            st.rerun()


def messages(me):
    st.markdown("## Messages")
    # Contacts now include accepted tutor sessions, bookings and skill-swap partners (the old query only read a legacy table, so Messages was always empty).
    contacts=S.message_contacts(me["id"])
    if not contacts: st.info("Messaging becomes available when a tutor accepts your session or booking request, or a skill-swap partner accepts your invitation.")
    else:
        other=st.selectbox("Conversation",contacts,format_func=lambda x:f"{x['name']} · {x['email'] or x['phone']}")
        hist=q("SELECT * FROM messages WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?) ORDER BY id",(me["id"],other["id"],other["id"],me["id"]))
        for m in hist:
            who=user(m["sender"])
            st.markdown(f"<div class='panel'><b>{who['name']}</b><p>{m['body']}</p><small>{m['created']}</small></div>",unsafe_allow_html=True)
        with st.form("sendmsg",clear_on_submit=True):
            body=st.text_input("Message")
            send=st.form_submit_button("Send")
        if send and body.strip():
            run("INSERT INTO messages(sender,receiver,body,created) VALUES(?,?,?,?)",(me["id"],other["id"],body.strip(),datetime.now().isoformat(timespec="minutes")))
            notify(other["id"],f"New message from {me['name']}."); st.rerun()

def tutor_workspace(me):
    st.markdown("## Tutor Workspace")
    st.caption("Manage tutoring requests, trial sessions, urgent help and follow-up learning notes.")
    incoming=q("SELECT s.*,u.name AS student_name FROM tutor_sessions s JOIN users u ON u.id=s.student_id WHERE s.tutor_id=? ORDER BY CASE WHEN s.session_type='Urgent 10-minute help' THEN 0 ELSE 1 END,s.id DESC",(me["id"],))
    if not incoming: st.info("No tutoring session requests yet. Students can find you through Tutor + Skill Swap.")
    for sess in incoming:
        with st.expander(f"{sess['session_type']} · {sess['topic']} · {sess['student_name']} · {sess['status']}"):
            st.write(sess["problem"])
            st.caption(f"Preferred language: {sess['language']} · Duration: {sess['duration']} minutes · Preferred time: {sess['scheduled'] or 'To be arranged'}")
            if sess["status"]=="Pending":
                a,b=st.columns(2)
                with a:
                    if st.button("Accept session",key=f"session_accept_{sess['id']}"):
                        run("UPDATE tutor_sessions SET status='Accepted' WHERE id=? AND tutor_id=? AND status='Pending'",(sess["id"],me["id"]))
                        notify(sess["student_id"],f"{me['name']} accepted your {sess['duration']}-minute tutoring request.")
                        st.rerun()
                with b:
                    if st.button("Decline",key=f"session_decline_{sess['id']}"):
                        run("UPDATE tutor_sessions SET status='Declined' WHERE id=? AND tutor_id=? AND status='Pending'",(sess["id"],me["id"]))
                        notify(sess["student_id"],f"{me['name']} could not accept your tutoring request."); st.rerun()
            if sess["status"]=="Accepted":
                st.info("Contact the student through Messages to agree on a meeting link or location. The app does not host live video calls.")
                with st.form(f"session_notes_{sess['id']}"):
                    concepts=st.text_area("Important concepts covered")
                    explanations=st.text_area("Simple explanations / examples")
                    practice=st.text_area("Practice questions")
                    homework=st.text_area("Homework")
                    goals=st.text_area("Goals for next session")
                    done=st.form_submit_button("Save session notes and complete session")
                if done:
                    if not concepts.strip() or not explanations.strip(): st.error("Add at least the concepts and explanation before saving notes.")
                    else:
                        run("INSERT INTO session_notes(session_id,concepts,explanations,practice,homework,next_goals,created) VALUES(?,?,?,?,?,?,?)",(sess["id"],concepts.strip(),explanations.strip(),practice.strip(),homework.strip(),goals.strip(),datetime.now().isoformat(timespec="minutes")))
                        run("UPDATE tutor_sessions SET status='Completed' WHERE id=? AND tutor_id=?",(sess["id"],me["id"]))
                        notify(sess["student_id"],f"{me['name']} added smart notes for your {sess['topic']} session.")
                        st.success("Notes saved and shared with the student."); st.rerun()
    st.markdown("### Your teaching profile")
    st.caption("Keep your skills, experience and preferred languages in My Profile so problem-based matching can find you.")

def student_sessions(me):
    st.markdown("## My tutoring sessions & smart notes")
    sessions=q("SELECT s.*,u.name AS tutor_name FROM tutor_sessions s JOIN users u ON u.id=s.tutor_id WHERE s.student_id=? ORDER BY s.id DESC",(me["id"],))
    if not sessions: T.empty_state("🧑‍🏫","No sessions yet","Describe the problem you're stuck on and we'll match you with tutors who can help.","Find a tutor","Tutor + Skill Swap",key="empty_sessions")
    for sess in sessions:
        st.markdown(f"<div class='panel'><b>{sess['topic']} · {sess['tutor_name']}</b><p>{sess['session_type']} · {sess['duration']} minutes · {sess['language']} · <span class='pill'>{sess['status']}</span></p><p>{sess['problem']}</p></div>",unsafe_allow_html=True)
        notes=q("SELECT * FROM session_notes WHERE session_id=? ORDER BY id DESC",(sess["id"],))
        for n in notes:
            st.markdown("### Smart notes after session")
            for label,key in [("Important concepts","concepts"),("Simple explanations","explanations"),("Practice questions","practice"),("Homework","homework"),("Next session goals","next_goals")]:
                if n[key]: st.markdown(f"**{label}**\n\n{n[key]}")

def leaderboard():
    st.markdown("## Skill XP & leaderboard")
    st.caption("XP is awarded for approved challenge submissions and other recorded learning contributions.")
    rows=q("SELECT name,role,xp FROM users ORDER BY xp DESC,name LIMIT 20")
    top=max([r["xp"] or 0 for r in rows] or [1]) or 1
    for i,r in enumerate(rows,1):
        medal={1:"🥇",2:"🥈",3:"🥉"}.get(i,f"#{i}")
        pct=int(100*(r["xp"] or 0)/top)
        st.markdown(f"<div class='panel'><div class='rank'><div class='medal'>{medal}</div><div class='grow'><b>{T.esc(r['name'])}</b> <span class='pill'>{T.esc(r['role']).upper()}</span><div class='bar'><span style='width:{pct}%'></span></div></div><div class='xp'>{r['xp'] or 0} XP</div></div></div>",unsafe_allow_html=True)

    st.markdown("### Weekly challenges")
    st.info("To prevent instant self-awards, submit a short explanation of what you completed. Another user must review it before XP is added. Do not approve your own submission.")
    for ch in q("SELECT * FROM challenges ORDER BY id"):
        submission=q("SELECT * FROM challenge_submissions WHERE user_id=? AND challenge_id=?",(st.session_state.uid,ch["id"]),True)
        st.markdown(f"<div class='panel'><b>{ch['title']}</b><p>{ch['description']}</p><small>Reward: {ch['reward']} XP · {ch['deadline']}</small></div>",unsafe_allow_html=True)
        if not submission:
            with st.form(f"challenge_submit_{ch['id']}"):
                evidence=st.text_area("Describe what you completed and include specific results or a project/resource link",key=f"evidence_{ch['id']}")
                submitted=st.form_submit_button("Submit for peer review")
            if submitted:
                if len(evidence.strip())<40:
                    st.error("Please provide at least 40 characters describing your actual work. A short claim such as 'done' is not enough.")
                else:
                    run("INSERT INTO challenge_submissions(user_id,challenge_id,evidence,created) VALUES(?,?,?,?)",
                        (st.session_state.uid,ch["id"],evidence.strip(),datetime.now().isoformat(timespec="minutes")))
                    st.success("Submission sent for peer review. XP will be added only after another user approves it.")
                    st.rerun()
        elif submission["status"]=="Approved":
            st.success(f"Approved — {ch['reward']} XP awarded.")
        elif submission["status"]=="Rejected":
            st.warning("This submission was not approved. You can submit again with clearer evidence.")
            with st.form(f"challenge_resubmit_{ch['id']}"):
                evidence=st.text_area("Add improved evidence",key=f"resubmit_evidence_{ch['id']}")
                resubmit=st.form_submit_button("Resubmit for review")
            if resubmit:
                if len(evidence.strip())<40: st.error("Please provide at least 40 characters of evidence.")
                else:
                    run("UPDATE challenge_submissions SET evidence=?,status='Pending',reviewer_id=NULL,created=?,reviewed=NULL WHERE id=?",
                        (evidence.strip(),datetime.now().isoformat(timespec="minutes"),submission["id"]))
                    st.rerun()
        else:
            st.info("Submission pending peer review. No XP has been awarded yet.")

    pending=q("SELECT s.*,u.name AS learner,c.title AS challenge_title,c.reward FROM challenge_submissions s JOIN users u ON u.id=s.user_id JOIN challenges c ON c.id=s.challenge_id WHERE s.status='Pending' AND s.user_id<>? ORDER BY s.id",(st.session_state.uid,))
    st.markdown("### Review another learner's submission")
    if not pending:
        st.caption("There are no submissions from other learners waiting for your review.")
    for sub in pending:
        with st.expander(f"{sub['challenge_title']} · submitted by {sub['learner']}"):
            st.write(sub["evidence"])
            st.caption(f"Potential reward: {sub['reward']} XP")
            a,b=st.columns(2)
            with a:
                if st.button("Approve evidence",key=f"approve_submission_{sub['id']}"):
                    with db() as c:
                        changed=c.execute("UPDATE challenge_submissions SET status='Approved',reviewer_id=?,reviewed=? WHERE id=? AND status='Pending' AND user_id<>?",
                            (st.session_state.uid,datetime.now().isoformat(timespec="minutes"),sub["id"],st.session_state.uid)).rowcount
                        if changed:
                            c.execute("UPDATE users SET xp=xp+? WHERE id=?",(sub["reward"],sub["user_id"]))
                            c.execute("INSERT INTO notifications(user_id,body,created) VALUES(?,?,?)",
                                (sub["user_id"],f"Your challenge '{sub['challenge_title']}' was approved. +{sub['reward']} XP.",datetime.now().isoformat(timespec="minutes")))
                    if changed:
                        S.award_coins(sub["user_id"],max(5,sub["reward"]//5),f"Challenge approved: {sub['challenge_title']}","challenge",sub["id"])
                        st.success("Approved. XP and EduCoins have been awarded once.")
                    st.rerun()
            with b:
                if st.button("Request resubmission",key=f"reject_submission_{sub['id']}"):
                    run("UPDATE challenge_submissions SET status='Rejected',reviewer_id=?,reviewed=? WHERE id=? AND status='Pending' AND user_id<>?",
                        (st.session_state.uid,datetime.now().isoformat(timespec="minutes"),sub["id"],st.session_state.uid))
                    run("INSERT INTO notifications(user_id,body,created) VALUES(?,?,?)",
                        (sub["user_id"],f"Your challenge '{sub['challenge_title']}' needs clearer evidence. Please revise and resubmit.",datetime.now().isoformat(timespec="minutes")))
                    st.rerun()

def main():
    if not AUI.require_session(): auth(); return
    me=user(st.session_state.uid)
    if not me: AUI.sign_out(); st.rerun()
    with st.sidebar:
        st.markdown("<h1>🎓 <span class='gradtxt'>EduBridge AI</span></h1>",unsafe_allow_html=True)
        st.caption("YOUR LEARNING COMMUNITY")
        T.sidebar_profile(me)
        pages=["Dashboard","Offline Meetings","Online Classes","AI Courses","AI Mind Maps","My Bookings","EduCoins Wallet","Tutor + Skill Swap","AI Doubt Solver","Learning Rooms","My Sessions & Notes","Skill Portfolio","XP & Leaderboard","Messages","Notifications","My Profile"]
        if me["role"]=="tutor": pages=["Tutor Dashboard","Overview","Tutor Workspace","Tutor + Skill Swap","EduCoins Wallet","AI Doubt Solver","Learning Rooms","Skill Portfolio","Student Requests","Messages","Notifications","My Profile"]
        if st.session_state.get("nav") not in pages: st.session_state.pop("nav",None)
        page=st.radio("NAVIGATION",pages,key="nav",format_func=lambda p:f"{T.NAV_ICONS.get(p,'•')}  {p}")
        st.markdown("---")
        if st.button("Sign out",use_container_width=True):
            AUI.sign_out(); st.rerun()
    if page=="Dashboard": M.student_dashboard(me)
    elif page=="Tutor Dashboard": M.tutor_dashboard(me)
    elif page=="Offline Meetings": M.marketplace(me,"offline")
    elif page=="Online Classes": M.marketplace(me,"online")
    elif page=="AI Courses": AI.courses_page(me)
    elif page=="AI Mind Maps": AI.mindmap_page(me)
    elif page=="My Bookings": M.my_bookings(me)
    elif page=="EduCoins Wallet": M.wallet(me)
    elif page=="Overview":
        page_header(f"Welcome, {me['name'].split()[0]}","Your skills are valuable. Learn, teach, collaborate and build proof of what you can do.")
        c1,c2,c3=st.columns(3)
        with c1: st.metric("Skill XP",me["xp"])
        with c2: st.metric("Portfolio entries",q("SELECT COUNT(*) n FROM portfolio WHERE user_id=?",(me["id"],),True)["n"])
        with c3: st.metric("Unread notifications",q("SELECT COUNT(*) n FROM notifications WHERE user_id=? AND read=0",(me["id"],),True)["n"])
        if me["role"]=="student":
            st.markdown("### Your learning toolkit")
            a,b,c=st.columns(3)
            with a: T.nav_card("🎯","Find a Tutor","Match tutors to your real learning problem and preferred language.","Tutor + Skill Swap","ov_tutor")
            with b: T.nav_card("🧠","Doubt Solver","Get a guided explanation or post a doubt for tutor support.","AI Doubt Solver","ov_doubt")
            with c: T.nav_card("🔄","Skill Swap","Learn from others while sharing a skill you know.","Tutor + Skill Swap","ov_swap")
        else: st.info("Use Tutor Workspace to respond to tutoring requests, urgent doubts and session follow-ups.")
    elif page=="Tutor + Skill Swap": skill_swap(me)
    elif page=="AI Doubt Solver": doubt_solver(me)
    elif page=="Tutor Workspace": tutor_workspace(me)
    elif page=="My Sessions & Notes": student_sessions(me)
    elif page=="Skill Portfolio": portfolio_page(me)
    elif page=="XP & Leaderboard": leaderboard()
    elif page=="Learning Rooms": rooms_page(me)
    elif page=="Messages": messages(me)
    elif page=="Student Requests":
        st.markdown("## Student requests")
        reqs=q("SELECT r.*,u.name,u.email,u.phone FROM requests r JOIN users u ON u.id=r.student WHERE r.tutor=? ORDER BY r.id DESC",(me["id"],))
        for r in reqs:
            st.markdown(f"<div class='panel'><span class='pill'>{r['status']}</span><h3>{r['topic']}</h3><p>{r['name']} · {r['email'] or r['phone']}</p><p>{r['note']}</p></div>",unsafe_allow_html=True)
            if r["status"]=="Pending":
                if st.button("Accept",key=f"accept{r['id']}"):
                    run("UPDATE requests SET status='Accepted' WHERE id=?",(r["id"],)); notify(r["student"],f"{me['name']} accepted your tutoring request."); st.rerun()
                if st.button("Decline",key=f"decline{r['id']}"):
                    run("UPDATE requests SET status='Declined' WHERE id=?",(r["id"],)); notify(r["student"],f"{me['name']} declined your tutoring request."); st.rerun()
    elif page=="Notifications":
        notes=q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC",(me["id"],))
        for n in notes:
            st.markdown(f"<div class='panel'>{'🔵' if not n['read'] else '⚪'} {n['body']}<p><small>{n['created']}</small></p></div>",unsafe_allow_html=True)
            if not n["read"] and st.button("Mark read",key=f"read{n['id']}"):
                run("UPDATE notifications SET read=1 WHERE id=?",(n["id"],)); st.rerun()
    elif page=="My Profile":
        with st.form("profile"):
            name=st.text_input("Name",me["name"]); offered=st.text_input("Skills you can teach",me["skills"]); wants=st.text_input("Skills you want to learn",me["wants"]); bio=st.text_area("About you",me["bio"]); languages=st.multiselect("Languages you can teach / learn in",_language_options(),default=[x.strip() for x in (me["languages"] or "English").split(",") if x.strip() in _language_options()])
            pc1,pc2,pc3=st.columns(3); p_city=pc1.text_input("City",me["city"] or ""); p_state=pc2.text_input("State",me["state"] or ""); p_country=pc3.text_input("Country",me["country"] or "India")
            save=st.form_submit_button("Save profile")
        if save:
            run("UPDATE users SET name=?,skills=?,wants=?,bio=?,languages=?,city=?,state=?,country=? WHERE id=?",(name,offered,wants,bio,", ".join(languages),p_city.strip(),p_state.strip(),p_country.strip() or "India",me["id"])); st.success("Profile saved."); st.rerun()
    st.markdown("---")
    st.caption("EduBridge AI · Learning together, growing together · Prototype")

if __name__=="__main__": main()
