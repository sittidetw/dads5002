
# app.py — Streamlit Prototype: Student/Teacher Q&A Checker
# - Student can append unlimited questions before preview/submit
# - Safe Back/Next, progress clamped
# - Optional edit of question text per submission
import streamlit as st
import sqlite3
import pandas as pd
from datetime import date

DB_PATH = "answers.db"

# Local safety; on Streamlit Cloud this exists
if not st.runtime.exists():
    print("\n[!] Please run with:  streamlit run app.py\n")
    raise SystemExit

# ---------- DB Utilities ----------
def get_con():
    return sqlite3.connect(DB_PATH)

def init_db():
    con = get_con()
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            date_week TEXT NOT NULL,
            question_no INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            checked INTEGER DEFAULT 0
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_week TEXT NOT NULL,
            question_no INTEGER NOT NULL,
            question TEXT NOT NULL,
            UNIQUE(date_week, question_no) ON CONFLICT REPLACE
        );
        """
    )
    con.commit()
    con.close()

DEFAULT_QUESTIONS = [
    "Explain one key concept you learned today.",
    "Give an example related to the concept.",
    "What is one question you still have?"
]

def load_questions(date_week:str|None):
    if not date_week:
        return DEFAULT_QUESTIONS.copy()
    con = get_con()
    df = pd.read_sql_query(
        "SELECT question_no, question FROM questions WHERE date_week=? ORDER BY question_no",
        con, params=[date_week]
    )
    con.close()
    if df.empty:
        return DEFAULT_QUESTIONS.copy()
    q = df.sort_values("question_no")["question"].tolist()
    return q if len(q) > 0 else DEFAULT_QUESTIONS.copy()

def save_question_set(date_week:str, questions:list[str]):
    con = get_con()
    cur = con.cursor()
    cur.execute("DELETE FROM questions WHERE date_week=?", (date_week,))
    for idx, q in enumerate([q.strip() for q in questions], start=1):
        if q:
            cur.execute("INSERT INTO questions (date_week, question_no, question) VALUES (?,?,?)",
                        (date_week, idx, q))
    con.commit(); con.close()

def list_question_dates():
    con = get_con()
    df = pd.read_sql_query("SELECT DISTINCT date_week FROM questions ORDER BY date_week DESC", con)
    con.close(); return df["date_week"].tolist()

def save_answers(student_id, date_week, qa_list):
    con = get_con(); cur = con.cursor()
    cur.execute("DELETE FROM answers WHERE student_id=? AND date_week=?", (student_id, date_week))
    for qno, qtext, ans in qa_list:
        cur.execute(
            "INSERT INTO answers (student_id, date_week, question_no, question, answer, checked) VALUES (?,?,?,?,?,0)",
            (student_id, date_week, qno, qtext, ans)
        )
    con.commit(); con.close()

def load_answers(date_week=None, student_search=""):
    con = get_con()
    where, params = [], []
    if date_week:
        where.append("date_week = ?"); params.append(date_week)
    if student_search:
        where.append("student_id LIKE ?"); params.append(f"%{student_search}%")
    wh = (" WHERE " + " AND ".join(where)) if where else ""
    df = pd.read_sql_query(
        f"SELECT id, student_id, date_week, question_no, question, answer, checked FROM answers{wh} ORDER BY student_id, question_no",
        con, params=params
    )
    con.close(); return df

def update_checked(ids, checked=True):
    if not ids: return
    con = get_con(); cur = con.cursor()
    cur.execute(
        f"UPDATE answers SET checked = ? WHERE id IN ({','.join(['?']*len(ids))})",
        [1 if checked else 0, *ids]
    )
    con.commit(); con.close()

# ---------- App ----------
init_db()
st.set_page_config(page_title="Q&A Checker", page_icon="✅", layout="centered")

# session defaults
st.session_state.setdefault("started", False)
st.session_state.setdefault("q_index", 0)
st.session_state.setdefault("answers", DEFAULT_QUESTIONS.copy())
st.session_state.setdefault("show_preview", False)
st.session_state.setdefault("teacher_loaded", False)
st.session_state.setdefault("current_questions", DEFAULT_QUESTIONS.copy())
st.session_state.setdefault("allow_edit_question", True)  # default ON for convenience
st.session_state.setdefault("nav_request", None)

st.title("📚 Simple Student/Teacher Q&A Checker")

tab_student, tab_teacher = st.tabs(["👩‍🎓 Student", "👨‍🏫 Teacher"])


# ---------------- Student ----------------
with tab_student:
    st.subheader("Start")
    col1, col2 = st.columns(2)
    with col1:
        student_id = st.text_input("Student ID", placeholder="e.g., S001")
    with col2:
        date_week = st.text_input("Date / Week", value=str(date.today()), help="Use same label as teacher's question set.")

    start = st.button("✅ START", use_container_width=True)

    if start:
        if not student_id.strip():
            st.warning("Please enter Student ID.")
        else:
            st.session_state.current_questions = [""]  # start with one blank question
            st.session_state.answers = [""]
            st.session_state.q_index = 0
            st.session_state.started = True
            st.session_state.show_preview = False

    if st.session_state.started:
        st.divider()
        questions = st.session_state.get("current_questions", DEFAULT_QUESTIONS).copy()
        total = len(questions)

        # Ensure at least 1
        if total <= 0:
            questions = [""]; total = 1
            st.session_state.current_questions = questions
            st.session_state.answers = [""]

        q_idx = max(0, min(st.session_state.q_index, total-1))
        st.session_state.q_index = q_idx
        progress_value = max(0.0, min((q_idx + 1) / total, 1.0))
        st.progress(progress_value, text=f"ข้อ {q_idx+1}")

        # Editable question text (per submission)
        key_q = f"q_{q_idx}"
        edited_q = st.text_input("Question", value=questions[q_idx], key=key_q,
                                 placeholder="Type your question here")
        questions[q_idx] = edited_q
        st.session_state.current_questions = questions

        # Answer box
        if len(st.session_state.answers) != total:
            st.session_state.answers = (st.session_state.answers + [""]*total)[:total]
        key_a = f"a_{q_idx}"
        st.session_state.answers[q_idx] = st.text_area("Your Answer",
                                                       value=st.session_state.answers[q_idx],
                                                       height=140, key=key_a)

        # Validation for current step
        current_q_filled = questions[q_idx].strip() != ""
        current_a_filled = st.session_state.answers[q_idx].strip() != ""
        allow_next = current_q_filled and current_a_filled

        # Controls row
        c1, c2 = st.columns([1,1])
        with c1:
            if st.button("⬅️ Back", use_container_width=True, disabled=(q_idx==0)):
                st.session_state.q_index = max(0, q_idx-1)
                st.session_state.show_preview = False
        with c2:
            if st.button("➡️ Next", use_container_width=True, disabled=not allow_next, key=f"next_btn_{q_idx}"):
                st.session_state.nav_request = {"action": "next", "index": q_idx}

        nav_req = st.session_state.pop("nav_request", None)
        if nav_req and nav_req.get("action") == "next":
            idx = nav_req.get("index", st.session_state.q_index)
            if idx >= len(st.session_state.current_questions) - 1:
                st.session_state.current_questions.append("")
                st.session_state.answers.append("")
            st.session_state.q_index = min(len(st.session_state.current_questions)-1, idx+1)
            st.session_state.show_preview = False

        # Check if all filled for preview
        all_filled = all(q.strip() != "" for q in st.session_state.current_questions) and \
                     all(a.strip() != "" for a in st.session_state.answers[:len(st.session_state.current_questions)])

        # Preview & submit buttons
        if st.button("👁️ Preview", use_container_width=True, disabled=not all_filled):
            st.session_state.show_preview = True
        if not all_filled:
            st.info("กรุณากรอก 'คำถาม' และ 'คำตอบ' ให้ครบทุกข้อก่อนกด Preview/Submit")

        if st.session_state.get("show_preview"):
            st.subheader("Preview & Submit")
            questions = st.session_state.current_questions
            total = len(questions)
            df_prev = pd.DataFrame({
                "Question No.": list(range(1,total+1)),
                "Question": questions,
                "Answer": st.session_state.answers[:total]
            })
            st.dataframe(df_prev, use_container_width=True, hide_index=True)
            colp1, colp2 = st.columns([2,1])
            with colp2:
                # Safety: still verify before saving
                if st.button("🟦 SUBMIT", use_container_width=True, disabled=not all_filled):
                    qa = [(i+1, questions[i].strip(), st.session_state.answers[i].strip()) for i in range(total)]
                    save_answers(student_id.strip(), date_week.strip(), qa)
                    st.success("Your answers have been submitted successfully!")
                    # reset for new submission
                    st.session_state.started = False
                    st.session_state.q_index = 0
                    st.session_state.answers = [""] * len(DEFAULT_QUESTIONS)
                    st.session_state.show_preview = False
# ---------------- Teacher ----------------
with tab_teacher:
    st.subheader("Manage Questions & Check Answers")
    m1, m2 = st.columns([1,1])
    with m1:
        teacher_name = st.text_input("Teacher Name", placeholder="e.g., Ms. June")
    with m2:
        manage_date = st.text_input("Date / Week (for Question Set)", value=str(date.today()))

    with st.expander("📝 Edit Question Set for this Date/Week", expanded=True):
        existing_dates = list_question_dates()
        if existing_dates:
            st.caption("Load from saved sets:")
            load_select = st.selectbox("Saved dates", options=["(select)"] + existing_dates, index=0)
            if load_select != "(select)":
                manage_date = load_select
                st.session_state["tmp_questions"] = load_questions(manage_date)

        if "tmp_questions" not in st.session_state:
            st.session_state["tmp_questions"] = load_questions(manage_date)

        num = st.number_input("Number of questions", min_value=1, max_value=30, value=len(st.session_state["tmp_questions"]), step=1)
        qlist = st.session_state["tmp_questions"]
        if len(qlist) < num:
            qlist = qlist + [""]*(num-len(qlist))
        elif len(qlist) > num:
            qlist = qlist[:num]

        new_questions = []
        for i in range(int(num)):
            new_questions.append(st.text_input(f"Q{i+1}", value=qlist[i], placeholder=f"Enter question {i+1}"))
        st.session_state["tmp_questions"] = new_questions

        cqs1, cqs2, cqs3 = st.columns([1,1,1])
        with cqs1:
            if st.button("💾 Save Question Set", use_container_width=True):
                save_question_set(manage_date.strip(), new_questions)
                st.success(f"Saved {len(new_questions)} questions for {manage_date}.")
        with cqs2:
            if st.button("🔄 Reset to Default", use_container_width=True):
                st.session_state["tmp_questions"] = DEFAULT_QUESTIONS.copy()
        with cqs3:
            if st.button("📥 Load Current Saved", use_container_width=True):
                st.session_state["tmp_questions"] = load_questions(manage_date.strip())

    st.divider()

    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        filter_date = st.text_input("Filter Date / Week", value=manage_date, placeholder="YYYY-MM-DD")
    with c2:
        student_search = st.text_input("Search Student ID", placeholder="e.g., S001")
    with c3:
        start_check = st.button("✅ START (Load)", use_container_width=True)

    if start_check:
        st.session_state.teacher_loaded = True

    if st.session_state.get("teacher_loaded"):
        df = load_answers(filter_date.strip() or None, student_search.strip())
        if df.empty:
            st.info("No data found. Try adjusting filters or ask students to submit.")
        else:
            st.write("Toggle ✅ to mark answers as checked.")
            edited = df.copy()
            edited["checked"] = edited["checked"].astype(bool)
            edited = st.data_editor(
                edited,
                column_config={
                    "checked": st.column_config.CheckboxColumn("check"),
                    "question_no": st.column_config.NumberColumn("question"),
                },
                disabled=["id", "student_id", "date_week", "question_no", "question", "answer"],
                hide_index=True,
                use_container_width=True,
                key="teacher_table"
            )
            changed_to_true = edited[(edited["checked"] == True) & (df["checked"] == 0)]
            changed_to_false = edited[(edited["checked"] == False) & (df["checked"] == 1)]

            colu1, colu2, colu3 = st.columns([1,1,1])
            with colu1:
                if st.button("💾 Save Checks", use_container_width=True):
                    update_checked(changed_to_true["id"].tolist(), True)
                    update_checked(changed_to_false["id"].tolist(), False)
                    st.success("Saved check status.")
            with colu2:
                if st.button("☑️ Mark All as Checked", use_container_width=True):
                    update_checked(edited["id"].tolist(), True)
                    st.success("All rows marked as checked.")
            with colu3:
                csv = edited.to_csv(index=False).encode("utf-8")
                st.download_button("⬇️ Export CSV", csv, file_name=f"answers_{filter_date or 'all'}.csv", mime="text/csv", use_container_width=True)

    st.caption("Tip: Students can append extra questions before submitting. Default question set is provided by the teacher per Date/Week.")
