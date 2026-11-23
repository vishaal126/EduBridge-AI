"""Streamlit screens for AI-generated courses and mind maps. Generation happens server-side via ai_client."""
import json
import uuid

import streamlit as st
import streamlit.components.v1 as components

import ai_client
import mindmap_view
import services as S
import theme as T

LEVELS = ["Complete beginner", "Beginner", "Intermediate", "Advanced"]


def _guard(me):
    if me["role"] != "student":
        st.info("AI courses and mind maps are available to student accounts.")
        return False
    if not ai_client.configured():
        _connect_card()
    else:
        st.markdown("<span class='pill ok'>● AI KEY SET</span>", unsafe_allow_html=True)
        _manage_key()
    return True


def _manage_key():
    """Test, replace or remove the active key (a rejected key must be replaceable)."""
    with st.expander("Test or change my API key"):
        src = {"session": "pasted in this session", "environment": "ANTHROPIC_API_KEY environment variable",
               "secrets": ".streamlit/secrets.toml"}.get(ai_client.key_source(), "unknown")
        st.caption(f"Currently using the key from: {src}. A key pasted here overrides the others.")
        if st.button("Test connection", key="ai_test_key"):
            with st.spinner("Contacting Anthropic..."):
                ok, msg = ai_client.test_key()
            (st.success if ok else st.error)(msg)
        with st.form("ai_key_replace", clear_on_submit=True):
            new = st.text_input("Paste a new API key", type="password", placeholder="sk-ant-...")
            go = st.form_submit_button("Use this key", use_container_width=True)
        if go:
            if ai_client.clean_key(new).startswith("sk-ant-"):
                ai_client.set_session_key(new)
                st.toast("Key updated", icon="✅")
                st.rerun()
            else:
                st.error("That doesn't look like an Anthropic key (it should start with sk-ant-).")
        if ai_client.key_source() == "session" and st.button("Remove pasted key", key="ai_clear_key"):
            ai_client.clear_session_key()
            st.rerun()


def _connect_card():
    """Friendly setup box: paste a key for this session, or see how to set it permanently."""
    st.markdown("<div class='setup'><b>🔑 Connect AI to unlock this page.</b><br>"
                "Paste your Anthropic API key below (get one at console.anthropic.com). It is kept only in this browser session's "
                "server memory and is never saved to disk or shown again.</div>", unsafe_allow_html=True)
    with st.form("ai_key_form", clear_on_submit=True):
        key = st.text_input("Anthropic API key", type="password", placeholder="sk-ant-...")
        ok = st.form_submit_button("Connect AI", use_container_width=True, type="primary")
    if ok:
        if ai_client.clean_key(key).startswith("sk-ant-") and len(ai_client.clean_key(key)) > 20:
            ai_client.set_session_key(key)
            st.toast("AI connected! You can generate now ✨", icon="✅")
            st.rerun()
        else:
            st.error("That doesn't look like an Anthropic key (it should start with sk-ant-).")
    with st.expander("Set it permanently instead"):
        st.markdown("**Windows PowerShell** (then restart the app):\n```powershell\n$env:ANTHROPIC_API_KEY=\"sk-ant-...\"\npython -m streamlit run app.py\n```\n"
                    "**Or** create `.streamlit/secrets.toml` next to `app.py`:\n```toml\nANTHROPIC_API_KEY = \"sk-ant-...\"\n```\n"
                    "`run_windows.bat` also asks for the key on start-up.")


def _quota_caption(me):
    q = S.ai_quota(me["id"])
    st.caption(f"Free AI generations left this month: {q['free_left']} of {S.FREE_AI_PER_MONTH}. "
               f"After that each costs {S.AI_COIN_COST} EduCoins (you have {q['balance']}). Failed generations are never charged.")


# ================================================================ Courses ========================
def courses_page(me):
    st.markdown("## AI-generated courses")
    st.caption("A personalised course built for your level and goals: lessons, examples, practice, quizzes, revision notes and a roadmap.")
    if not _guard(me):
        return
    t1, t2 = st.tabs(["Create a course", "My courses"])
    with t1:
        _quota_caption(me)
        with st.form("course_form"):
            subject = st.text_input("Subject", placeholder="Computer Science")
            topic = st.text_input("Topic", placeholder="Python for data analysis")
            level = st.selectbox("Your current knowledge level", LEVELS, index=1)
            objectives = st.text_area("Learning objectives", placeholder="Clean a CSV, make charts, and answer questions with pandas.")
            weeks = st.slider("Preferred course duration (weeks)", 1, 6, 2)
            go = st.form_submit_button("✨ Generate my course", use_container_width=True, type="primary", disabled=not ai_client.configured())
        if go:
            if not subject.strip() or not topic.strip() or len(objectives.strip()) < 10:
                st.error("Enter a subject, a topic and learning objectives (at least 10 characters).")
            elif not ai_client.configured():
                st.error(ai_client.setup_message())
            else:
                try:
                    S.ensure_can_generate(me["id"])
                    with st.spinner("Building your course. This can take up to a minute..."):
                        data = ai_client.generate_course(subject.strip(), topic.strip(), level, objectives.strip(), weeks)
                    cid = S.save_course(me["id"], subject.strip(), topic.strip(), level, objectives.strip(), f"{weeks} weeks", data)
                    st.session_state["open_course"] = cid
                    st.balloons()
                    st.success("Course created and saved. Open it under My courses.")
                except (S.ServiceError, ai_client.AIError) as e:
                    st.error(str(e))
    with t2:
        _my_courses(me)


def _my_courses(me):
    rows = S.list_courses(me["id"])
    if not rows:
        T.empty_state("🎓", "No courses yet", "Generate your first personalised course in the “Create a course” tab.")
        return
    ids = [r["id"] for r in rows]
    default = ids.index(st.session_state["open_course"]) if st.session_state.get("open_course") in ids else 0
    row = st.selectbox("Course", rows, index=default, format_func=lambda r: f"{r['topic']} · {r['subject']} ({S.course_percent(me['id'], r)}%)")
    st.session_state["open_course"] = row["id"]
    _, course = S.get_course(me["id"], row["id"])
    prog = S.course_progress(me["id"], row["id"])
    pct = S.course_percent(me["id"], row)
    st.markdown(f"<div class='panel'><span class='pill'>{T.esc(row['level'])}</span><span class='pill'>{T.esc(row['duration'])}</span><h3>{T.esc(course['title'])}</h3><p>{T.esc(course['summary'])}</p></div>", unsafe_allow_html=True)
    st.progress(pct / 100, text=f"{pct}% complete")
    tabs = st.tabs(["Lessons", "Roadmap", "Final assessment", "Manage"])
    with tabs[0]:
        n = len(course["lessons"])
        first_open = next((i for i in range(n) if not (prog.get(i) and prog[i]["completed"])), 0)
        key = f"lesson_{row['id']}"
        if st.button(f"▶ Resume at lesson {first_open + 1}", key=f"resume_{row['id']}"):
            st.session_state[key] = first_open
        if key not in st.session_state:
            st.session_state[key] = min(first_open, n - 1)
        idx = st.selectbox("Lesson", range(n), key=key,
                           format_func=lambda i: f"{'✅' if prog.get(i) and prog[i]['completed'] else '○'} {i + 1}. {course['lessons'][i]['title']}")
        _lesson(me, row["id"], idx, course["lessons"][idx], prog.get(idx))
    with tabs[1]:
        for r in course["roadmap"]:
            st.markdown(f"**{r['step']}** — {r['goal']}")
        if not course["roadmap"]:
            st.caption("No roadmap was generated for this course.")
    with tabs[2]:
        _quiz_block(me, row["id"], None, course.get("final_assessment", []), "final")
    with tabs[3]:
        if st.button("Delete this course", key=f"delc_{row['id']}"):
            S.delete_course(me["id"], row["id"])
            st.session_state.pop("open_course", None)
            st.rerun()


def _lesson(me, cid, idx, L, p):
    st.markdown(f"### {L['title']}")
    st.markdown(L["explanation"])
    if L["examples"]:
        st.markdown("#### Practical examples")
        for i, e in enumerate(L["examples"], 1):
            st.markdown(f"**Example {i}.** {e}")
    if L["practice"]:
        st.markdown("#### Practice questions")
        for i, q in enumerate(L["practice"], 1):
            with st.expander(f"Q{i}. {q['q']}"):
                st.markdown(q["a"] or "_No worked answer provided._")
    if L["quiz"]:
        st.markdown("#### Quick quiz")
        _quiz_block(me, cid, idx, L["quiz"], f"l{idx}", p)
    if L["revision"]:
        st.markdown("#### Revision notes")
        st.markdown("\n".join(f"- {x}" for x in L["revision"]))
    done = bool(p and p["completed"])
    if st.checkbox("Mark this lesson complete", value=done, key=f"done_{cid}_{idx}") != done:
        S.set_lesson(me["id"], cid, idx, completed=not done)
        st.rerun()


def _quiz_block(me, cid, lesson_idx, quiz, tag, p=None):
    if not quiz:
        st.caption("No questions available.")
        return
    if p and p["quiz_total"]:
        st.caption(f"Last score: {p['quiz_score']}/{p['quiz_total']}")
    with st.form(f"quiz_{cid}_{tag}"):
        picks = [st.radio(f"{i + 1}. {q['q']}", q["options"], index=None, key=f"qa_{cid}_{tag}_{i}") for i, q in enumerate(quiz)]
        sub = st.form_submit_button("Check answers")
    if sub:
        score = 0
        for i, (q, pick) in enumerate(zip(quiz, picks)):
            right = q["options"][q["answer"]]
            if pick == right:
                score += 1
                st.success(f"{i + 1}. Correct. {q['explanation']}")
            else:
                st.error(f"{i + 1}. {'Not answered' if pick is None else 'Not quite'} — answer: {right}. {q['explanation']}")
        st.info(f"Score: {score}/{len(quiz)}")
        if lesson_idx is not None:
            S.set_lesson(me["id"], cid, lesson_idx, quiz=(score, len(quiz)))


# ================================================================ Mind maps ======================
def _flatten(node, path=()):
    out = [(node, path + (node["label"],))]
    for c in node["children"]:
        out += _flatten(c, path + (node["label"],))
    return out


def _find(node, nid):
    if node["id"] == nid:
        return node
    for c in node["children"]:
        r = _find(c, nid)
        if r:
            return r
    return None


def _remove(node, nid):
    node["children"] = [c for c in node["children"] if c["id"] != nid]
    for c in node["children"]:
        _remove(c, nid)


def mindmap_page(me):
    st.markdown("## AI mind maps")
    st.caption("Type any subject or topic and get an interactive map of its sub-topics, concepts, definitions, examples, formulas and revision points.")
    if not _guard(me):
        return
    t1, t2 = st.tabs(["Create a mind map", "My mind maps"])
    with t1:
        _quota_caption(me)
        with st.form("mm_form"):
            topic = st.text_input("Subject or topic", placeholder="Newton's laws of motion")
            focus = st.text_input("Focus on (optional)", placeholder="applications and numerical problems")
            a, b, c = st.columns(3)
            depth = a.slider("Depth", 2, 4, 3)
            formulas = b.checkbox("Include formulas", True)
            audience = c.selectbox("Audience", ["School (Class 6-10)", "High-school / early college", "University", "Professional"], index=1)
            go = st.form_submit_button("✨ Generate mind map", use_container_width=True, type="primary", disabled=not ai_client.configured())
        if go:
            _generate_map(me, topic, depth, formulas, focus, audience)
    with t2:
        _my_maps(me)


def _generate_map(me, topic, depth, formulas, focus, audience, replace_id=None):
    if len(topic.strip()) < 2:
        st.error("Enter a subject or topic.")
        return
    if not ai_client.configured():
        st.error(ai_client.setup_message())
        return
    try:
        S.ensure_can_generate(me["id"])
        with st.spinner("Drawing your mind map..."):
            data = ai_client.generate_mindmap(topic.strip(), depth, formulas, focus.strip(), audience)
        opts = {"depth": depth, "formulas": formulas, "focus": focus, "audience": audience}
        mid = S.save_mindmap(me["id"], topic.strip(), opts, data)
        st.session_state["open_map"] = mid
        st.success("Mind map saved. Open it under My mind maps.")
    except (S.ServiceError, ai_client.AIError) as e:
        st.error(str(e))


def _my_maps(me):
    rows = S.list_mindmaps(me["id"])
    if not rows:
        T.empty_state("🧠", "No mind maps yet", "Generate one in the “Create a mind map” tab and it will appear here.")
        return
    ids = [r["id"] for r in rows]
    default = ids.index(st.session_state["open_map"]) if st.session_state.get("open_map") in ids else 0
    row = st.selectbox("Mind map", rows, index=default, format_func=lambda r: f"{r['topic']} · {r['created'][:10]}")
    st.session_state["open_map"] = row["id"]
    rec, data = S.get_mindmap(me["id"], row["id"])
    components.html(mindmap_view.component_html(data, 640), height=660, scrolling=False)
    st.caption("Click nodes for details, +/− to expand or collapse, scroll to zoom, drag to pan. "
               "“Save as PDF” opens your browser's print dialog (choose Save as PDF). If PNG download is blocked, use the SVG button below.")
    a, b, c, d = st.columns(4)
    a.download_button("Download SVG", mindmap_view.to_svg(data), f"mindmap_{rec['id']}.svg", "image/svg+xml", use_container_width=True)
    b.download_button("Download outline (.md)", mindmap_view.to_markdown(data), f"mindmap_{rec['id']}.md", use_container_width=True)
    opts = json.loads(rec["options"] or "{}")
    if c.button("Regenerate", use_container_width=True, key=f"regen_{rec['id']}", help="Creates a new map for the same topic and options (counts as a new AI generation)."):
        _generate_map(me, rec["topic"], opts.get("depth", 3), opts.get("formulas", True), opts.get("focus", ""), opts.get("audience", "High-school / early college"))
        st.rerun()
    if d.button("Delete", use_container_width=True, key=f"delm_{rec['id']}"):
        S.delete_mindmap(me["id"], rec["id"])
        st.session_state.pop("open_map", None)
        st.rerun()
    flat = _flatten(data["root"])
    st.markdown("### Explain or customise a branch")
    pick = st.selectbox("Branch", range(len(flat)), format_func=lambda i: " › ".join(flat[i][1]), key=f"pick_{rec['id']}")
    node, path = flat[pick]
    ex1, ex2 = st.columns(2)
    if ex1.button("Explain this branch", key=f"expl_{rec['id']}_{node['id']}"):
        if not ai_client.configured():
            st.error(ai_client.setup_message())
        else:
            try:
                with st.spinner("Explaining..."):
                    st.session_state[f"expl_txt_{rec['id']}_{node['id']}"] = ai_client.explain_branch(rec["topic"], list(path), node)
            except ai_client.AIError as e:
                st.error(str(e))
    txt = st.session_state.get(f"expl_txt_{rec['id']}_{node['id']}")
    if txt:
        st.markdown(txt)
    with st.expander("Edit this branch"):
        with st.form(f"edit_{rec['id']}_{node['id']}"):
            label = st.text_input("Label", node["label"])
            definition = st.text_area("Definition", node["definition"])
            example = st.text_input("Example", node["example"])
            formula = st.text_input("Formula", node["formula"])
            revision = st.text_input("Revision point", node["revision"])
            save = st.form_submit_button("Save changes")
        if save and label.strip():
            tgt = _find(data["root"], node["id"])
            tgt.update(label=label.strip()[:80], definition=definition.strip()[:500], example=example.strip()[:400],
                       formula=formula.strip()[:200], revision=revision.strip()[:300])
            S.update_mindmap(me["id"], rec["id"], data)
            st.rerun()
        with st.form(f"add_{rec['id']}_{node['id']}", clear_on_submit=True):
            new = st.text_input("Add a sub-branch under this node")
            add = st.form_submit_button("Add sub-branch")
        if add and new.strip():
            _find(data["root"], node["id"])["children"].append(
                {"id": "u" + uuid.uuid4().hex[:8], "label": new.strip()[:80], "definition": "", "example": "", "formula": "", "revision": "", "children": []})
            S.update_mindmap(me["id"], rec["id"], data)
            st.rerun()
        if node["id"] != data["root"]["id"] and st.button("Delete this branch", key=f"delb_{rec['id']}_{node['id']}"):
            _remove(data["root"], node["id"])
            data["links"] = [l for l in data["links"] if _find(data["root"], l["from"]) and _find(data["root"], l["to"])]
            S.update_mindmap(me["id"], rec["id"], data)
            st.rerun()
