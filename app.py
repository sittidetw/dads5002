import io
import json
import os
import shutil
import secrets
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

try:
    import xlsxwriter  # noqa: F401

    HAS_XLSXWRITER = True
except ModuleNotFoundError:
    HAS_XLSXWRITER = False

DB_PATH = "answers.db"
TH_TZ = ZoneInfo("Asia/Bangkok")
MC_PLACEHOLDER = "— Select an option —"
QUESTION_RESPONSE_TYPES = {
    "long_text": "Long text answer",
    "multiple_choice": "Multiple choice",
}
DEFAULT_QUESTIONS = [
    {
        "question": "Explain one key concept you learned today.",
        "response_type": "long_text",
        "options": [],
    },
    {
        "question": "Give an example related to the concept.",
        "response_type": "long_text",
        "options": [],
    },
    {
        "question": "What is one question you still have?",
        "response_type": "long_text",
        "options": [],
    },
]
ASSIGNMENT_TYPES = ["Individual", "Group"]
PARTICIPATION_STEP = 1.0
FREE_RESPONSE_ACTIVITY_NAME = "Free response (student-defined)"
FREE_RESPONSE_DESCRIPTION = (
    "This system-managed activity lets students craft their own prompts and submit long-form answers."
)
FREE_RESPONSE_FIXED_ID = 0
FREE_RESPONSE_ACTIVITY_ID: int | None = None

DATA_DELETE_ACTIONS: dict[str, list[str]] = {
    "Student responses & grades": [
        "DELETE FROM answers",
        "DELETE FROM student_activity",
    ],
    "Activities & questions": [
        "DELETE FROM questions",
        "DELETE FROM activities",
    ],
    "Check-ins & daily participation": [
        "DELETE FROM participation_daily",
        "DELETE FROM check_ins",
    ],
    "Student roster": [
        "DELETE FROM student_roster",
    ],
}


def now_th():
    return datetime.now(TH_TZ)


def today_th():
    return now_th().date()


def default_question_bundle():
    return [
        {
            "id": None,
            "question_no": idx + 1,
            "question": q["question"],
            "response_type": q.get("response_type", "long_text"),
            "options": list(q.get("options", [])),
        }
        for idx, q in enumerate(DEFAULT_QUESTIONS)
    ]


def normalize_response_type(value: str | None) -> str:
    if value not in QUESTION_RESPONSE_TYPES:
        return "long_text"
    return str(value)


def serialize_options(options: list[str]) -> str:
    cleaned = [opt.strip() for opt in options if str(opt).strip()]
    return json.dumps(cleaned)


def deserialize_options(raw) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(opt) for opt in raw if str(opt).strip()]
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(opt) for opt in parsed if str(opt).strip()]
        except json.JSONDecodeError:
            pass
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return []


def _load_teacher_accounts():
    accounts: dict[str, str] = {}

    def _teacher_secret_block():
        try:
            secrets_container = st.secrets  # may raise if secrets file missing
        except Exception:
            return {}
        try:
            cfg = secrets_container.get("teacher_accounts", {})
        except AttributeError:
            try:
                cfg = secrets_container["teacher_accounts"]
            except Exception:
                cfg = {}
        except Exception:
            cfg = {}
        return cfg if isinstance(cfg, dict) else {}

    for key, value in _teacher_secret_block().items():
        accounts[str(key)] = str(value)
    username = os.environ.get("TEACHER_USERNAME")
    password = os.environ.get("TEACHER_PASSWORD")
    if username and password:
        accounts[username] = password
    if not accounts:
        accounts["teacher"] = os.environ.get("TEACHER_FALLBACK_PASSWORD", "admin")
    return accounts


TEACHER_ACCOUNTS = _load_teacher_accounts()

# Local safety; on Streamlit Cloud this exists
if not st.runtime.exists():  # type: ignore[attr-defined]
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
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            activity_date TEXT,
            assignment_type TEXT DEFAULT 'Individual',
            description TEXT,
            active INTEGER DEFAULT 1
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_week TEXT,
            activity_id INTEGER,
            question_no INTEGER NOT NULL,
            question TEXT NOT NULL,
            response_type TEXT DEFAULT 'long_text',
            options TEXT,
            UNIQUE(activity_id, question_no) ON CONFLICT REPLACE
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            group_name TEXT,
            activity_id INTEGER,
            date_week TEXT,
            question_id INTEGER,
            question_no INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            checked INTEGER DEFAULT 0,
            score REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS student_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            activity_id INTEGER NOT NULL,
            participation_points REAL DEFAULT 0,
            overall_grade REAL DEFAULT 0,
            notes TEXT,
            UNIQUE(student_id, activity_id) ON CONFLICT REPLACE
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS check_ins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            check_in_date TEXT NOT NULL,
            note TEXT,
            recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, check_in_date)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS participation_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            check_in_date TEXT NOT NULL,
            participation_points REAL DEFAULT 0,
            teacher_note TEXT,
            UNIQUE(student_id, check_in_date) ON CONFLICT REPLACE
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS student_roster (
            student_id TEXT PRIMARY KEY,
            student_name TEXT NOT NULL
        );
        """
    )
    for stmt in (
        "ALTER TABLE questions ADD COLUMN activity_id INTEGER",
        "ALTER TABLE questions ADD COLUMN response_type TEXT DEFAULT 'long_text'",
        "ALTER TABLE questions ADD COLUMN options TEXT",
        "ALTER TABLE answers ADD COLUMN activity_id INTEGER",
        "ALTER TABLE answers ADD COLUMN question_id INTEGER",
        "ALTER TABLE answers ADD COLUMN score REAL",
        "ALTER TABLE answers ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE answers ADD COLUMN group_name TEXT",
        "ALTER TABLE participation_daily ADD COLUMN teacher_note TEXT",
    ):
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            pass
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_activity_question ON questions(activity_id, question_no)"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_answers_student_activity_question ON answers(student_id, activity_id, question_no)"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_student_activity_unique ON student_activity(student_id, activity_id)"
    )
    cur.execute(
        """
        DELETE FROM check_ins
        WHERE rowid NOT IN (
            SELECT MAX(rowid) FROM check_ins GROUP BY student_id, check_in_date
        )
        """
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_checkins_unique ON check_ins(student_id, check_in_date)"
    )
    cur.execute(
        """
        DELETE FROM participation_daily
        WHERE rowid NOT IN (
            SELECT MAX(rowid) FROM participation_daily GROUP BY student_id, check_in_date
        )
        """
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_participation_daily_unique ON participation_daily(student_id, check_in_date)"
    )
    con.commit()
    con.close()


def get_activity(activity_id: int | None):
    if not activity_id:
        return None
    con = get_con()
    df = pd.read_sql_query(
        """
        SELECT id, name, activity_date, assignment_type, description, active
        FROM activities WHERE id=?
        """,
        con,
        params=[activity_id],
    )
    con.close()
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def get_activities(active_only=True) -> pd.DataFrame:
    con = get_con()
    query = """
        SELECT id, name, activity_date, assignment_type, description, active
        FROM activities
    """
    if active_only:
        query += " WHERE active=1"
    query += " ORDER BY COALESCE(activity_date, '') DESC, name ASC"
    df = pd.read_sql_query(query, con)
    con.close()
    return df


def ensure_free_response_activity():
    con = get_con()
    cur = con.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO activities (id, name, activity_date, assignment_type, description, active)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            FREE_RESPONSE_FIXED_ID,
            FREE_RESPONSE_ACTIVITY_NAME,
            None,
            "Individual",
            FREE_RESPONSE_DESCRIPTION,
        ),
    )
    cur.execute(
        """
        UPDATE activities
        SET name=?, assignment_type=?, description=?, active=1
        WHERE id=?
        """,
        (
            FREE_RESPONSE_ACTIVITY_NAME,
            "Individual",
            FREE_RESPONSE_DESCRIPTION,
            FREE_RESPONSE_FIXED_ID,
        ),
    )
    cur.execute("DELETE FROM activities WHERE name=? AND id<>?", (FREE_RESPONSE_ACTIVITY_NAME, FREE_RESPONSE_FIXED_ID))
    con.commit()
    con.close()
    return FREE_RESPONSE_FIXED_ID


def is_free_response_activity(activity_id):
    if activity_id is None:
        return False
    try:
        return int(activity_id) == FREE_RESPONSE_FIXED_ID
    except (TypeError, ValueError):
        return False


def save_activity(activity_id, name, activity_date, assignment_type, description, active):
    date_value = None
    if isinstance(activity_date, date):
        date_value = activity_date.isoformat()
    elif isinstance(activity_date, str) and activity_date.strip():
        date_value = activity_date.strip()
    con = get_con()
    cur = con.cursor()
    if activity_id:
        cur.execute(
            """
            UPDATE activities
            SET name=?, activity_date=?, assignment_type=?, description=?, active=?
            WHERE id=?
            """,
            (name.strip(), date_value, assignment_type, description.strip(), int(bool(active)), activity_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO activities (name, activity_date, assignment_type, description, active)
            VALUES (?,?,?,?,?)
            """,
            (name.strip(), date_value, assignment_type, description.strip(), int(bool(active))),
        )
    con.commit()
    con.close()


def delete_activity(activity_id: int | None):
    if not activity_id:
        return
    if is_free_response_activity(activity_id):
        return
    con = get_con()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT student_id FROM student_activity WHERE activity_id=?", (activity_id,))
    students = [row[0] for row in cur.fetchall()]
    cur.execute("DELETE FROM questions WHERE activity_id=?", (activity_id,))
    cur.execute("DELETE FROM answers WHERE activity_id=?", (activity_id,))
    cur.execute("DELETE FROM student_activity WHERE activity_id=?", (activity_id,))
    if students:
        placeholders = ",".join(["?"] * len(students))
        cur.execute(
            f"DELETE FROM participation_daily WHERE student_id IN ({placeholders})",
            students,
        )
    cur.execute("DELETE FROM activities WHERE id=?", (activity_id,))
    con.commit()
    con.close()


def load_question_bundle(activity_id: int | None):
    if not activity_id:
        return default_question_bundle()
    con = get_con()
    df = pd.read_sql_query(
        """
        SELECT id, question_no, question, response_type, options
        FROM questions
        WHERE activity_id=?
        ORDER BY question_no
        """,
        con,
        params=[activity_id],
    )
    con.close()
    if df.empty:
        return default_question_bundle()
    records = []
    for _, row in df.iterrows():
        records.append(
            {
                "id": row["id"],
                "question_no": int(row["question_no"]),
                "question": row["question"],
                "response_type": normalize_response_type(row.get("response_type")),
                "options": deserialize_options(row.get("options")),
            }
        )
    return records


def save_question_set(activity_id, questions: list[dict]):
    if not activity_id:
        return
    activity = get_activity(activity_id)
    con = get_con()
    cur = con.cursor()
    cur.execute("DELETE FROM questions WHERE activity_id=?", (activity_id,))
    for idx, q in enumerate(questions, start=1):
        qtext = str(q.get("question", "")).strip()
        if not qtext:
            continue
        response_type = normalize_response_type(q.get("response_type"))
        options_raw = q.get("options", [])
        if response_type != "multiple_choice":
            options_raw = []
        options_json = serialize_options(options_raw)
        cur.execute(
            """
            INSERT INTO questions (activity_id, question_no, question, date_week, response_type, options)
            VALUES (?,?,?,?,?,?)
            """,
            (
                activity_id,
                idx,
                qtext,
                activity.get("activity_date") if activity else None,
                response_type,
                options_json,
            ),
        )
    con.commit()
    con.close()


def blank_question_template(position: int):
    return {
        "id": None,
        "question_no": position,
        "question": "",
        "response_type": "long_text",
        "options": [],
    }


def clear_question_editor_state(activity_id: int):
    prefix = f"question_editor_{activity_id}_"
    count_key = f"question_count_{activity_id}"
    cache_key = f"{count_key}_cache"
    keys_to_remove = []
    for key in list(st.session_state.keys()):
        if key.startswith(prefix) or key in (count_key, cache_key):
            keys_to_remove.append(key)
    for key in keys_to_remove:
        st.session_state.pop(key, None)


def get_question_builder(activity_id: int):
    builders = st.session_state.setdefault("question_builders", {})
    cache_key = f"question_count_{activity_id}_cache"
    if activity_id not in builders:
        builders[activity_id] = load_question_bundle(activity_id)
        st.session_state[cache_key] = len(builders[activity_id]) or 1
    return builders[activity_id]


def set_question_builder(activity_id: int, records: list[dict], reset_inputs: bool = False):
    st.session_state.setdefault("question_builders", {})
    if reset_inputs:
        clear_question_editor_state(activity_id)
    st.session_state["question_builders"][activity_id] = records
    st.session_state[f"question_count_{activity_id}_cache"] = len(records) or 1


def save_answers(student_id, activity_id, qa_list, group_name=""):
    activity = get_activity(activity_id)
    activity_label = safe_date_label(
        activity.get("activity_date") if activity else None, today_th().isoformat()
    )
    con = get_con()
    cur = con.cursor()
    free_mode = is_free_response_activity(activity_id)
    student_id_clean = student_id.strip()
    if free_mode:
        cur.execute(
            "SELECT COALESCE(MAX(question_no), 0) FROM answers WHERE student_id=? AND activity_id=?",
            (student_id_clean, activity_id),
        )
        start_offset = int(cur.fetchone()[0] or 0)
    else:
        cur.execute("DELETE FROM answers WHERE student_id=? AND activity_id=?", (student_id_clean, activity_id))
        start_offset = 0
    for idx, item in enumerate(qa_list, start=1):
        q_no = item.get("question_no")
        if not isinstance(q_no, int) or q_no <= 0:
            q_no = idx
        if free_mode:
            q_no = start_offset + q_no
        cur.execute(
            """
            INSERT INTO answers (student_id, group_name, activity_id, date_week, question_id, question_no, question, answer, checked, score)
            VALUES (?,?,?,?,?,?,?,?,0,NULL)
            """,
            (
                student_id_clean,
                group_name.strip() or None,
                activity_id,
                activity_label,
                item.get("question_id"),
                q_no,
                item.get("question"),
                item.get("answer"),
            ),
        )
    con.commit()
    con.close()


def load_answers(activity_id=None, student_search=""):
    con = get_con()
    where = []
    params = []
    if activity_id:
        where.append("ans.activity_id = ?")
        params.append(activity_id)
    if student_search.strip():
        where.append("ans.student_id LIKE ?")
        params.append(f"%{student_search.strip()}%")
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    query = f"""
        SELECT
            ans.id,
            ans.student_id,
            ans.group_name,
            ans.activity_id,
            act.name AS activity_name,
            ans.question_no,
            ans.question,
            ans.answer,
            ans.checked,
            ans.score
        FROM answers ans
        LEFT JOIN activities act ON ans.activity_id = act.id
        {where_clause}
        ORDER BY ans.student_id ASC, ans.question_no ASC
    """
    df = pd.read_sql_query(query, con, params=params)
    con.close()
    df = attach_names(df)
    cols = df.columns.tolist()
    if "student_name" in cols and "student_id" in cols:
        cols.remove("student_name")
        insert_at = cols.index("student_id") + 1
        cols = cols[:insert_at] + ["student_name"] + cols[insert_at:]
        df = df[cols]
    return df


def update_checked(ids, checked=True):
    if not ids:
        return
    con = get_con()
    cur = con.cursor()
    placeholders = ",".join(["?"] * len(ids))
    cur.execute(
        f"UPDATE answers SET checked=? WHERE id IN ({placeholders})",
        [1 if checked else 0, *ids],
    )
    con.commit()
    con.close()


def delete_data_sections(selections: list[str]):
    if not selections:
        return False, "No sections selected."
    con = get_con()
    cur = con.cursor()
    try:
        for key in selections:
            statements = DATA_DELETE_ACTIONS.get(key, [])
            for stmt in statements:
                cur.execute(stmt)
        con.commit()
    except Exception as exc:
        con.rollback()
        con.close()
        return False, f"Unable to delete data: {exc}"
    con.close()
    return True, "Selected data has been deleted."


def update_scores(changes: list[dict]):
    if not changes:
        return
    con = get_con()
    cur = con.cursor()
    for item in changes:
        cur.execute(
            "UPDATE answers SET score=?, checked=? WHERE id=?",
            (
                item.get("score"),
                1 if item.get("checked") else 0,
                item.get("id"),
            ),
        )
    con.commit()
    con.close()


def get_participation(activity_id):
    con = get_con()
    totals = pd.read_sql_query(
        """
        SELECT student_id, SUM(COALESCE(score,0)) AS total_score
        FROM answers
        WHERE activity_id=?
        GROUP BY student_id
        """,
        con,
        params=[activity_id],
    )
    participation = pd.read_sql_query(
        """
        SELECT student_id, participation_points, overall_grade, check_in_time, check_in_note
        FROM student_activity
        WHERE activity_id=?
        """,
        con,
        params=[activity_id],
    )
    con.close()
    df = pd.merge(totals, participation, on="student_id", how="outer")
    numeric_defaults = {
        "total_score": 0.0,
        "participation_points": 0.0,
        "overall_grade": 0.0,
    }
    for col, default in numeric_defaults.items():
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna(default)
    df["calculated_grade"] = df["total_score"] + df["participation_points"]
    df.loc[df["overall_grade"] == 0, "overall_grade"] = df["calculated_grade"]
    df = attach_names(df)
    return df.sort_values("student_id")


def save_participation(activity_id, records: pd.DataFrame):
    if records.empty:
        return
    con = get_con()
    cur = con.cursor()
    payload = []
    for _, row in records.iterrows():
        payload.append(
            (
                row["student_id"],
                activity_id,
                float(row.get("participation_points", 0) or 0),
                float(row.get("overall_grade", 0) or 0),
            )
        )
    cur.executemany(
        """
        INSERT INTO student_activity (student_id, activity_id, participation_points, overall_grade)
        VALUES (?,?,?,?)
        ON CONFLICT(student_id, activity_id) DO UPDATE SET
            participation_points=excluded.participation_points,
            overall_grade=excluded.overall_grade
        """,
        payload,
    )
    con.commit()
    con.close()


def format_timestamp(ts: datetime | None) -> str:
    if ts is None:
        return ""
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def record_student_check_in(student_id: str, check_in_date: str, note: str = ""):
    if not student_id or not check_in_date:
        return
    timestamp = format_timestamp(now_th())
    con = get_con()
    cur = con.cursor()
    try:
        cur.execute(
            """
            INSERT INTO check_ins (student_id, check_in_date, note, recorded_at)
            VALUES (?,?,?,?)
            ON CONFLICT(student_id, check_in_date) DO UPDATE SET
                note=excluded.note,
                recorded_at=excluded.recorded_at
            """,
            (student_id, check_in_date.strip(), note.strip(), timestamp),
        )
    except sqlite3.IntegrityError:
        cur.execute(
            """
            UPDATE check_ins
            SET note=?, recorded_at=?
            WHERE student_id=? AND check_in_date=?
            """,
            (note.strip(), timestamp, student_id.strip(), check_in_date.strip()),
        )
    con.commit()
    con.close()


def load_check_ins(check_in_date: str | None = None):
    con = get_con()
    params = []
    where = ""
    if check_in_date:
        where = "WHERE check_in_date = ?"
        params.append(check_in_date)
    query = f"""
        SELECT student_id, check_in_date, note, recorded_at
        FROM check_ins
        {where}
        ORDER BY recorded_at DESC
    """
    df = pd.read_sql_query(query, con, params=params)
    con.close()
    if "recorded_at" in df.columns:
        df["recorded_at"] = df["recorded_at"].apply(
            lambda val: format_timestamp(datetime.fromisoformat(val)) if isinstance(val, str) and val else val
        )
    df = attach_names(df)
    return df


def get_daily_participation(check_in_date: str):
    checkins = load_check_ins(check_in_date)
    if checkins.empty:
        checkins = pd.DataFrame(columns=["student_id", "check_in_date", "note", "recorded_at"])
    con = get_con()
    participation = pd.read_sql_query(
        """
        SELECT student_id, participation_points, teacher_note
        FROM participation_daily
        WHERE check_in_date = ?
        """,
        con,
        params=[check_in_date],
    )
    con.close()
    df = pd.merge(
        checkins,
        participation,
        on="student_id",
        how="left",
    )
    df = attach_names(df)
    df["participation_points"] = df["participation_points"].fillna(0.0)
    df["teacher_note"] = df["teacher_note"].fillna("")
    return df


def load_all_checkin_students():
    con = get_con()
    df = pd.read_sql_query("SELECT DISTINCT student_id FROM check_ins", con)
    con.close()
    return df


def save_daily_participation(check_in_date: str, records: pd.DataFrame):
    if records.empty:
        return
    con = get_con()
    cur = con.cursor()
    payload = []
    for _, row in records.iterrows():
        payload.append(
            (
                row["student_id"],
                check_in_date,
                float(row.get("participation_points", 0) or 0),
                row.get("teacher_note", "") or "",
            )
        )
    cur.executemany(
        """
        INSERT INTO participation_daily (student_id, check_in_date, participation_points, teacher_note)
        VALUES (?,?,?,?)
        ON CONFLICT(student_id, check_in_date) DO UPDATE SET
            participation_points=excluded.participation_points,
            teacher_note=excluded.teacher_note
        """,
        payload,
    )
    con.commit()
    con.close()


def build_gradebook(activity_id):
    responses = load_answers(activity_id)
    summary = get_participation(activity_id)
    keep_cols = ["student_id", "student_name", "total_score", "participation_points", "overall_grade"]
    for col in keep_cols:
        if col not in summary.columns:
            summary[col] = ""
    summary = summary[keep_cols]
    summary = summary.rename(
        columns={
            "student_id": "Student ID",
            "student_name": "Student Name",
            "total_score": "Score (questions)",
            "participation_points": "Participation",
            "overall_grade": "Final grade",
        }
    )
    return responses, summary


def backup_database():
    if not os.path.exists(DB_PATH):
        return b""
    with open(DB_PATH, "rb") as fh:
        return fh.read()


def restore_database(content: bytes):
    if not content:
        return False, "No file content to restore."
    if content[:16] != b"SQLite format 3\x00":
        return False, "Uploaded file is not a valid SQLite database."
    # Create safety backup before overwriting
    if os.path.exists(DB_PATH):
        backup_path = f"{DB_PATH}.bak_{now_th().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(DB_PATH, backup_path)
    with open(DB_PATH, "wb") as fh:
        fh.write(content)
    return True, "Database restored. Restart the app to ensure all changes are loaded."


def get_student_name(student_id: str) -> str:
    if not student_id:
        return ""
    con = get_con()
    df = pd.read_sql_query(
        "SELECT student_name FROM student_roster WHERE student_id=? LIMIT 1",
        con,
        params=[student_id],
    )
    con.close()
    if df.empty:
        return ""
    return str(df.iloc[0]["student_name"])


def upsert_roster(records: list[tuple[str, str]]):
    if not records:
        return
    con = get_con()
    cur = con.cursor()
    cur.executemany(
        """
        INSERT INTO student_roster (student_id, student_name)
        VALUES (?, ?)
        ON CONFLICT(student_id) DO UPDATE SET student_name=excluded.student_name
        """,
        records,
    )
    con.commit()
    con.close()


def load_roster():
    con = get_con()
    df = pd.read_sql_query("SELECT student_id, student_name FROM student_roster ORDER BY student_id", con)
    con.close()
    return df


def attach_names(df: pd.DataFrame):
    roster = load_roster()
    if df is None or df.empty or roster.empty or "student_id" not in df.columns:
        return df
    # Drop any prior student_name columns to avoid *_x / *_y duplication
    df = df.drop(columns=["student_name"], errors="ignore")
    merged = df.merge(roster, on="student_id", how="left")
    # If merge created suffixes, clean them up
    if "student_name_x" in merged.columns or "student_name_y" in merged.columns:
        merged["student_name"] = merged.get("student_name_x").combine_first(merged.get("student_name_y"))
        merged = merged.drop(columns=["student_name_x", "student_name_y"], errors="ignore")
    return merged


def ensure_dataframe_columns(df: pd.DataFrame, defaults: dict[str, object]):
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
    return df


def ensure_student_names(df: pd.DataFrame, id_col: str, name_col: str):
    """Guarantee that the display table has a Student Name column even if no roster exists."""
    if df is None or df.empty or id_col not in df.columns:
        return df
    df = df.copy()
    id_values = df[id_col].astype(str)
    if name_col not in df.columns:
        df[name_col] = id_values
        return df
    df[name_col] = df[name_col].fillna("").astype(str)
    empty_mask = df[name_col].str.strip() == ""
    df.loc[empty_mask, name_col] = id_values[empty_mask]
    return df


def normalize_student_ids(values) -> set[str]:
    normalized: set[str] = set()
    for val in values:
        if val is None:
            continue
        text = str(val).strip()
        if text:
            normalized.add(text)
    return normalized


def load_all_daily_participation_totals():
    con = get_con()
    df = pd.read_sql_query(
        """
        SELECT student_id, SUM(COALESCE(participation_points,0)) AS daily_points
        FROM participation_daily
        GROUP BY student_id
        """,
        con,
    )
    con.close()
    return df


def load_participation_daily_entries():
    con = get_con()
    df = pd.read_sql_query(
        """
        SELECT student_id, check_in_date, participation_points, teacher_note
        FROM participation_daily
        ORDER BY check_in_date DESC
        """,
        con,
    )
    con.close()
    return df


def adjust_daily_score(check_in_date: str, student_id: str, delta: float):
    key = f"{check_in_date}|{student_id}"
    adjustments = st.session_state.get("daily_participation_adjust", {})
    current = adjustments.get(key, 0.0)
    adjustments[key] = current + delta
    st.session_state["daily_participation_adjust"] = adjustments


def apply_daily_participation_adjustments(df: pd.DataFrame, check_in_date: str):
    if df.empty:
        return df
    adjustments = st.session_state.get("daily_participation_adjust", {})
    if not adjustments:
        return df
    df = df.copy()
    for idx, row in df.iterrows():
        key = f"{check_in_date}|{row['student_id']}"
        delta = adjustments.get(key, 0.0)
        if delta:
            base = float(row.get("participation_points", 0) or 0)
            df.at[idx, "participation_points"] = base + delta
    return df


def build_activity_label_map(df: pd.DataFrame):
    return {
        int(row["id"]): f"{row['name']} ({safe_date_label(row['activity_date'])})"
        for _, row in df.iterrows()
    }


def activity_select(label, df: pd.DataFrame, key=None, default_id=None):
    if df.empty:
        return None
    options = [int(item) for item in df["id"].tolist()]
    labels = build_activity_label_map(df)
    index = 0
    if default_id is not None and default_id in options:
        index = options.index(default_id)
    return st.selectbox(
        label,
        options=options,
        format_func=lambda x: labels.get(int(x), f"Activity {x}"),
        index=index,
        key=key,
    )


def safe_date_label(value, fallback="No date"):
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except TypeError:
        pass
    value_str = str(value).strip()
    return value_str if value_str else fallback


def authenticate_teacher(password):
    # Accept if password matches any configured teacher account
    for _, stored in TEACHER_ACCOUNTS.items():
        if secrets.compare_digest(str(password), str(stored)):
            return True
    return False


def set_teacher_password(new_password: str):
    if not new_password:
        return False
    # Persist only in memory; relies on env or secrets in deployment for durability
    TEACHER_ACCOUNTS.clear()
    TEACHER_ACCOUNTS["teacher"] = new_password
    return True


def clear_student_inputs():
    for key in st.session_state.get("student_answer_keys", []):
        st.session_state.pop(key, None)
    st.session_state["student_answer_keys"] = []
    reset_free_response_state()
    set_nav_warning("")


def reset_free_response_state():
    st.session_state["free_response_entries"] = []
    st.session_state["free_response_mode"] = False


def new_free_response_entry():
    return {"uid": secrets.token_hex(4), "question": "", "answer": ""}


def start_free_response_mode(activity_id: int):
    st.session_state["free_response_mode"] = True
    st.session_state["free_response_entries"] = [new_free_response_entry()]
    st.session_state.question_set = []
    st.session_state.answers = []
    st.session_state.q_index = 0
    st.session_state.started = True
    st.session_state.show_preview = False
    st.session_state.selected_activity = int(activity_id) if activity_id is not None else None
    set_nav_warning("")


def ensure_free_response_entries():
    entries = st.session_state.get("free_response_entries", [])
    if not entries:
        entries = [new_free_response_entry()]
    st.session_state["free_response_entries"] = entries
    return entries


def reset_student_state(question_set, activity_id):
    clear_student_inputs()
    st.session_state.question_set = question_set
    st.session_state.answers = [""] * len(question_set)
    st.session_state.q_index = 0
    st.session_state.started = True
    st.session_state.show_preview = False
    st.session_state.selected_activity = activity_id
    set_nav_warning("")


def register_student_answer_key(key: str):
    keys = st.session_state.get("student_answer_keys", [])
    if key not in keys:
        keys.append(key)
        st.session_state["student_answer_keys"] = keys


def render_free_response_form(student_id: str, group_name: str):
    activity = get_activity(st.session_state.selected_activity)
    st.write(
        f"**Activity:** {activity.get('name') if activity else 'Free response'} · "
        f"{activity.get('assignment_type') if activity else 'Assignment'}"
    )
    st.caption("Add your own prompts/questions and respond with long-form answers. Leave prompts blank to remove them.")
    entries = ensure_free_response_entries()
    remove_uid = None
    for idx, entry in enumerate(entries):
        uid = entry.get("uid") or secrets.token_hex(4)
        entry["uid"] = uid
        question_key = f"free_question_{uid}"
        answer_key = f"free_answer_{uid}"
        register_student_answer_key(question_key)
        register_student_answer_key(answer_key)
        current_question = st.text_input(
            f"Question / prompt {idx + 1}",
            value=entry.get("question", ""),
            key=question_key,
            placeholder="e.g., Summarize what you learned today",
        )
        current_answer = st.text_area(
            f"Answer {idx + 1}",
            value=entry.get("answer", ""),
            key=answer_key,
            height=160,
        )
        entry["question"] = current_question
        entry["answer"] = current_answer
        if idx > 0:
            if st.button("Remove", key=f"free_remove_{uid}", type="secondary"):
                remove_uid = uid
    if remove_uid:
        entries = [entry for entry in entries if entry.get("uid") != remove_uid]
        st.session_state["free_response_entries"] = entries or [new_free_response_entry()]
        st.session_state.pop(f"free_question_{remove_uid}", None)
        st.session_state.pop(f"free_answer_{remove_uid}", None)
        st.rerun()
    if st.button("➕ Add question/prompt", key="free_add_prompt", use_container_width=True):
        entries.append(new_free_response_entry())
        st.session_state["free_response_entries"] = entries
        st.rerun()
    valid_entries = [
        {
            "question": entry.get("question", "").strip(),
            "answer": entry.get("answer", "").strip(),
        }
        for entry in entries
        if entry.get("question", "").strip() and entry.get("answer", "").strip()
    ]
    st.caption("At least one prompt and answer are required to submit.")
    if st.button("🟦 Submit free response", use_container_width=True, disabled=not valid_entries, key="free_submit"):
        qa_payload = []
        for idx, entry in enumerate(valid_entries, start=1):
            qa_payload.append(
                {
                    "question_id": None,
                    "question_no": idx,
                    "question": entry["question"],
                    "answer": entry["answer"],
                }
            )
        save_answers(
            student_id.strip(),
            st.session_state.selected_activity,
            qa_payload,
            group_name.strip(),
        )
        st.success("Your answers have been submitted successfully!")
        clear_student_inputs()
        st.session_state.started = False
        st.session_state.q_index = 0
        st.session_state.answers = []
        st.session_state.show_preview = False
        st.session_state.question_set = []
        st.session_state.selected_activity = None


def handle_mc_change(question_idx: int, question: dict, total_questions: int, key: str, placeholder: str):
    choice = st.session_state.get(key, placeholder)
    answer = "" if choice == placeholder else choice
    if question_idx < len(st.session_state.answers):
        st.session_state.answers[question_idx] = answer
    if answer_is_filled(question, answer):
        set_nav_warning("")
        if question_idx < total_questions - 1:
            st.session_state.q_index = question_idx + 1
            st.rerun()


def answer_is_filled(question: dict, answer) -> bool:
    if answer is None:
        return False
    ans_str = str(answer).strip()
    if question.get("response_type") == "multiple_choice":
        return ans_str != ""
    return ans_str != ""


def set_nav_warning(message: str = ""):
    st.session_state["nav_warning"] = message


# ---------- App ----------
init_db()
FREE_RESPONSE_ACTIVITY_ID = ensure_free_response_activity()
st.set_page_config(page_title="DADS5002 Design Thinking", page_icon="✅", layout="wide")

st.session_state.setdefault("started", False)
st.session_state.setdefault("q_index", 0)
st.session_state.setdefault("answers", [])
st.session_state.setdefault("question_set", [])
st.session_state.setdefault("show_preview", False)
st.session_state.setdefault("selected_activity", None)
st.session_state.setdefault("teacher_loaded", False)
st.session_state.setdefault("teacher_authenticated", False)
st.session_state.setdefault("teacher_user", "")
st.session_state.setdefault("grading_filter", {})
st.session_state.setdefault("student_answer_keys", [])
st.session_state.setdefault("question_builders", {})
st.session_state.setdefault("nav_warning", "")
st.session_state.setdefault("daily_participation_adjust", {})
st.session_state.setdefault("free_response_mode", False)
st.session_state.setdefault("free_response_entries", [])
st.session_state.setdefault("all_responses_table", None)

st.title("📚 Student Activity Response Collector")
tab_student, tab_teacher = st.tabs(["👩‍🎓 Student", "👨‍🏫 Teacher"])


# ---------------- Student ----------------
with tab_student:
    st.subheader("Submit your responses")
    col1, col2 = st.columns(2)
    with col1:
        student_id = st.text_input("Student ID", placeholder="e.g., S001")
    student_name = get_student_name(student_id.strip())
    if student_name:
        st.caption(f"Name on roster: **{student_name}**")
    activities_df = get_activities(active_only=True)
    activity_id = None
    activity_options = ["(select activity)"]
    activity_labels = {}
    if not activities_df.empty:
        for _, row in activities_df.iterrows():
            label = f"{row['name']} ({safe_date_label(row['activity_date'])})"
            activity_options.append(label)
            activity_labels[label] = row["id"]
    with col2:
        chosen_label = st.selectbox("Activity", options=activity_options, index=0)
        activity_id = activity_labels.get(chosen_label)
    group_name = st.text_input("Group Name (optional)", placeholder="e.g., Team Alpha", key="student_group_name_input")
    check_in_note = ""
    check_in_disabled = not student_id.strip()
    if st.button("📍 Check In", use_container_width=True, disabled=check_in_disabled):
        if not student_id.strip():
            st.warning("Please enter Student ID before checking in.")
        else:
            record_student_check_in(student_id.strip(), today_th().isoformat(), check_in_note)
            st.success("Check-in recorded. Teacher can now award participation points.")
    start = st.button("✅ START", use_container_width=True, disabled=activities_df.empty)
    if start:
        if not student_id.strip():
            st.warning("Please enter Student ID.")
        elif not activity_id:
            st.warning("Please select an activity.")
        else:
            if is_free_response_activity(activity_id):
                clear_student_inputs()
                start_free_response_mode(activity_id)
            else:
                question_set = load_question_bundle(activity_id)
                reset_student_state(question_set, activity_id)

    if st.session_state.started:
        st.divider()
        if st.session_state.get("free_response_mode") and is_free_response_activity(st.session_state.selected_activity):
            render_free_response_form(student_id, group_name)
        else:
            activity = get_activity(st.session_state.selected_activity)
            question_set = st.session_state.get("question_set", [])
            if not question_set:
                question_set = load_question_bundle(st.session_state.selected_activity)
                st.session_state.question_set = question_set
                st.session_state.answers = [""] * len(question_set)
            total = len(question_set)
            st.write(
                f"**Activity:** {activity.get('name') if activity else 'N/A'} · "
                f"{activity.get('assignment_type') if activity else 'Assignment'} · "
                f"{safe_date_label(activity.get('activity_date') if activity else None, 'Date not set')}"
            )

            q_idx = max(0, min(st.session_state.q_index, total - 1))
            st.session_state.q_index = q_idx
            st.progress((q_idx + 1) / total, text=f"Question {q_idx + 1} of {total}")
            current_q = question_set[q_idx]
            st.markdown(f"**Question {current_q['question_no']}**")
            st.info(current_q["question"])

            if len(st.session_state.answers) != total:
                st.session_state.answers = (st.session_state.answers + [""] * total)[:total]

            response_type = current_q.get("response_type", "long_text")
            answer_widget_key = f"answer_{st.session_state.selected_activity}_{current_q['question_no']}_{response_type}"
            register_student_answer_key(answer_widget_key)
            existing_answer = st.session_state.answers[q_idx]
            answer_value = existing_answer
            if response_type == "multiple_choice":
                options = current_q.get("options") or []
                if not options:
                    st.warning("This question has no options configured; defaulting to text response.")
                    response_type = "long_text"
                else:
                    placeholder = MC_PLACEHOLDER
                    choices = [placeholder, *options]
                    default_index = 0
                    if existing_answer in options:
                        default_index = options.index(existing_answer) + 1
                    selected_option = st.radio(
                        "Choose an option",
                        options=choices,
                        index=default_index,
                        key=answer_widget_key,
                        on_change=handle_mc_change,
                        kwargs={
                            "question_idx": q_idx,
                            "question": current_q,
                            "total_questions": total,
                            "key": answer_widget_key,
                            "placeholder": placeholder,
                        },
                    )
                    answer_value = "" if selected_option == placeholder else selected_option

            if response_type != "multiple_choice":
                answer_value = st.text_area(
                    "Your Answer",
                    value=existing_answer,
                    height=160,
                    key=answer_widget_key,
                )

            st.session_state.answers[q_idx] = answer_value
            current_a_filled = answer_is_filled(current_q, st.session_state.answers[q_idx])
            if current_a_filled and st.session_state.get("nav_warning"):
                set_nav_warning("")

            c1, c2 = st.columns(2)
            with c1:
                back_clicked = st.button(
                    "⬅️ Back",
                    use_container_width=True,
                    disabled=(q_idx == 0),
                    key=f"back_btn_{q_idx}",
                )
                if back_clicked and q_idx > 0:
                    st.session_state.q_index = max(0, q_idx - 1)
                    st.session_state.show_preview = False
                    set_nav_warning("")
                    st.rerun()
            with c2:
                next_clicked = st.button(
                    "➡️ Next",
                    use_container_width=True,
                    disabled=(q_idx >= total - 1),
                    key=f"next_btn_{q_idx}",
                )
                if next_clicked:
                    latest_answer = st.session_state.get(answer_widget_key, st.session_state.answers[q_idx])
                    st.session_state.answers[q_idx] = latest_answer
                    if not answer_is_filled(current_q, latest_answer):
                        set_nav_warning("Please answer this question before continuing.")
                    else:
                        st.session_state.q_index = min(total - 1, q_idx + 1)
                        st.session_state.show_preview = False
                        set_nav_warning("")
                        st.rerun()

            if st.session_state.get("nav_warning"):
                st.warning(st.session_state["nav_warning"])

            all_filled = all(
                answer_is_filled(question_set[idx], st.session_state.answers[idx])
                for idx in range(total)
            )
            st.caption("Fill every answer to unlock preview & submit.")
            if st.button("👁️ Preview", use_container_width=True, disabled=not all_filled):
                st.session_state.show_preview = True

            if st.session_state.get("show_preview"):
                st.subheader("Preview & Submit")
                df_prev = pd.DataFrame(
                    {
                        "Question No.": [q["question_no"] for q in question_set],
                        "Question": [q["question"] for q in question_set],
                        "Answer": st.session_state.answers[:total],
                    }
                )
                st.dataframe(df_prev, use_container_width=True, hide_index=True)
                if st.button("🟦 SUBMIT", use_container_width=True, disabled=not all_filled):
                    qa_payload = []
                    for idx, q in enumerate(question_set):
                        qa_payload.append(
                            {
                                "question_id": q.get("id"),
                                "question_no": q.get("question_no"),
                                "question": q.get("question"),
                                "answer": st.session_state.answers[idx].strip(),
                            }
                        )
                    group_name_value = st.session_state.get("student_group_name_input", "").strip()
                    save_answers(
                        student_id.strip(),
                        st.session_state.selected_activity,
                        qa_payload,
                        group_name_value,
                    )
                    st.success("Your answers have been submitted successfully!")
                    clear_student_inputs()
                    st.session_state.started = False
                    st.session_state.q_index = 0
                    st.session_state.answers = []
                    st.session_state.show_preview = False
                    st.session_state.question_set = []
                    st.session_state.selected_activity = None


# ---------------- Teacher ----------------
with tab_teacher:
    st.subheader("Manage activities, grading, and exports")
    if not st.session_state.teacher_authenticated:
        st.info("Please log in to manage activities and grades.")
        with st.form("teacher_login"):
            password_input = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")
        if submitted:
            if authenticate_teacher(password_input):
                st.session_state.teacher_authenticated = True
                st.session_state.teacher_user = "teacher"
                st.success("Logged in successfully.")
                st.rerun()
            else:
                st.error("Invalid credentials.")
    else:
        st.success(f"Logged in as {st.session_state.teacher_user}")
        if st.button("Log out", type="secondary"):
            st.session_state.teacher_authenticated = False
            st.session_state.teacher_user = ""
            st.session_state.teacher_loaded = False
            st.session_state.grading_filter = {}
            st.rerun()
        activities_all = get_activities(active_only=False)

        tab_class_prep, tab_responses, tab_participation, tab_scores, tab_import, tab_backup = st.tabs(
            [
                "🗂️ Class preparation",
                "🧾 Student responses & grading",
                "⭐ Participation (by date)",
                "📊 Score overview",
                "🧑‍🎓 Import student names",
                "🗃️ Database backup & restore",
            ]
        )

        # ----- Tab: Class preparation -----
        with tab_class_prep:
            st.markdown("### 🗂️ Activities")
            activity_options = ["(new activity)"]
            activity_map = {}
            for _, row in activities_all.iterrows():
                label = f"{row['name']} ({safe_date_label(row['activity_date'])})"
                activity_options.append(label)
                activity_map[label] = row
            selected_activity_label = st.selectbox("Select activity to edit", options=activity_options)
            selected_activity_row = activity_map.get(selected_activity_label)
            default_date = today_th()
            if selected_activity_row is not None and selected_activity_row["activity_date"]:
                try:
                    default_date = datetime.strptime(selected_activity_row["activity_date"], "%Y-%m-%d").date()
                except ValueError:
                    default_date = today_th()
            submitted = False
            delete_requested = False
            can_delete_activity = selected_activity_row is not None and not is_free_response_activity(
                selected_activity_row["id"] if selected_activity_row is not None else None
            )
            if selected_activity_row is not None and is_free_response_activity(selected_activity_row["id"]):
                st.info("The free response activity is managed by the system and cannot be edited here.")
            else:
                with st.form("activity_form"):
                    name_val = st.text_input(
                        "Activity name",
                        value=selected_activity_row["name"] if selected_activity_row is not None else "",
                    )
                    date_val = st.date_input(
                        "Activity date",
                        value=default_date,
                    )
                    assignment_val = st.selectbox(
                        "Assignment type",
                        options=ASSIGNMENT_TYPES,
                        index=ASSIGNMENT_TYPES.index(
                            selected_activity_row["assignment_type"]
                        )
                        if selected_activity_row is not None
                        and selected_activity_row["assignment_type"] in ASSIGNMENT_TYPES
                        else 0,
                    )
                    description_default = ""
                    if selected_activity_row is not None and pd.notna(selected_activity_row["description"]):
                        description_default = str(selected_activity_row["description"])
                    description_val = st.text_area(
                        "Description",
                        value=description_default,
                    )
                    active_val = st.checkbox(
                        "Active (visible to students)",
                        value=bool(selected_activity_row["active"]) if selected_activity_row is not None else True,
                    )
                    col_save, col_delete = st.columns([1, 1])
                    submitted = col_save.form_submit_button("Save Activity")
                    if can_delete_activity:
                        delete_requested = col_delete.form_submit_button("🗑️ Delete this activity")
                    else:
                        col_delete.write("")
            if delete_requested:
                delete_activity(selected_activity_row["id"])
                st.success("Activity deleted.")
                activities_all = get_activities(active_only=False)
            elif submitted:
                if not name_val.strip():
                    st.error("Activity name is required.")
                else:
                    save_activity(
                        selected_activity_row["id"] if selected_activity_row is not None else None,
                        name_val,
                        date_val,
                        assignment_val,
                        description_val,
                        active_val,
                    )
                    st.success("Activity saved.")
                    activities_all = get_activities(active_only=False)

            st.dataframe(
                activities_all,
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("### 📝 Manage questions per activity")
            question_activities = activities_all
            if FREE_RESPONSE_ACTIVITY_ID is not None and not activities_all.empty:
                question_activities = activities_all[activities_all["id"] != FREE_RESPONSE_ACTIVITY_ID]
            if question_activities.empty:
                st.info("Create an activity first.")
            else:
                activity_id_for_questions = activity_select(
                    "Choose activity for questions",
                    question_activities,
                    key="question_activity_select",
                )
                if activity_id_for_questions is None:
                    st.info("No activity available.")
                else:
                    builder_records = list(get_question_builder(activity_id_for_questions))
                    count_key = f"question_count_{activity_id_for_questions}"
                    cache_key = f"{count_key}_cache"
                    current_count = st.session_state.get(
                        count_key,
                        st.session_state.get(cache_key, len(builder_records) or 1),
                    )
                    num_questions = st.number_input(
                        "Number of questions",
                        min_value=1,
                        max_value=50,
                        value=int(current_count) or 1,
                        step=1,
                        key=count_key,
                    )
                    num_questions = int(num_questions)
                    if num_questions > len(builder_records):
                        for _ in range(num_questions - len(builder_records)):
                            builder_records.append(blank_question_template(len(builder_records) + 1))
                    elif num_questions < len(builder_records):
                        for drop_idx in range(num_questions, len(builder_records)):
                            prefix = f"question_editor_{activity_id_for_questions}_{drop_idx}_"
                            st.session_state.pop(f"{prefix}text", None)
                            st.session_state.pop(f"{prefix}type", None)
                            st.session_state.pop(f"{prefix}options", None)
                        builder_records = builder_records[:num_questions]
                    for idx, record in enumerate(builder_records):
                        record["question_no"] = idx + 1
                    set_question_builder(activity_id_for_questions, builder_records)

                    updated_records = []
                    for idx, record in enumerate(builder_records):
                        st.markdown(f"**Question {idx + 1}**")
                        text_key = f"question_editor_{activity_id_for_questions}_{idx}_text"
                        type_key = f"question_editor_{activity_id_for_questions}_{idx}_type"
                        options_key = f"question_editor_{activity_id_for_questions}_{idx}_options"

                        text_val = st.text_input(
                            "Question text",
                            value=record.get("question", ""),
                            key=text_key,
                        )
                        type_options = list(QUESTION_RESPONSE_TYPES.keys())
                        type_default = normalize_response_type(record.get("response_type"))
                        type_index = type_options.index(type_default) if type_default in type_options else 0
                        type_val = st.selectbox(
                            "Response type",
                            options=type_options,
                            format_func=lambda opt: QUESTION_RESPONSE_TYPES.get(opt, opt),
                            index=type_index,
                            key=type_key,
                        )
                        opts_list = record.get("options", [])
                        if type_val == "multiple_choice":
                            options_text = st.text_area(
                                "Choices (one per line)",
                                value="\n".join(opts_list),
                                key=options_key,
                                help="Students will select exactly one of these choices.",
                            )
                            parsed_options = [opt.strip() for opt in options_text.splitlines() if opt.strip()]
                        else:
                            st.session_state.pop(options_key, None)
                            parsed_options = []
                        updated_records.append(
                            {
                                "id": record.get("id"),
                                "question_no": idx + 1,
                                "question": text_val,
                                "response_type": type_val,
                                "options": parsed_options,
                            }
                        )
                    set_question_builder(activity_id_for_questions, updated_records)

                    if st.button("💾 Save Question Set", use_container_width=True):
                        save_question_set(activity_id_for_questions, updated_records)
                        st.success("Questions saved.")

        # ----- Tab: Student responses & grading -----
        with tab_responses:
            st.markdown("### 🧾 Student responses & grading")
            if activities_all.empty:
                st.info("No activities to grade yet.")
            else:
                grading_df = activities_all.copy()
                all_label = "All activities"
                activity_options = grading_df["id"].tolist()
                activity_labels = build_activity_label_map(grading_df)
                activity_choice = st.selectbox(
                    "Activity to review",
                    options=[all_label, *activity_options],
                    format_func=lambda x: activity_labels.get(x, all_label) if x != all_label else all_label,
                    key="grading_activity_select",
                )
                student_filter = st.text_input("Search Student ID (optional)", key="student_filter")
                if st.button("Load responses", use_container_width=True):
                    st.session_state.grading_filter = {
                        "activity_id": None if activity_choice == all_label else activity_choice,
                        "student": student_filter,
                        "all": activity_choice == all_label,
                    }
                    st.session_state.teacher_loaded = True

                if st.session_state.get("grading_filter"):
                    filt = st.session_state["grading_filter"]
                    df_responses = load_answers(filt.get("activity_id"), filt.get("student", ""))
                    if df_responses.empty:
                        st.info("No responses found for this selection.")
                    else:
                        enable_editing = not filt.get("all")
                        if enable_editing:
                            base_by_id = df_responses.set_index("id")
                            editable_display = df_responses.drop(columns=["checked", "activity_id"], errors="ignore")
                            if "id" in editable_display.columns:
                                editable_display = editable_display.set_index("id")
                            editable_result = st.data_editor(
                                editable_display,
                                column_config={
                                    "score": st.column_config.NumberColumn(
                                        "Score", min_value=0.0, step=0.5, format="%.2f"
                                    ),
                                    "question_no": st.column_config.NumberColumn("Question #"),
                                    "group_name": st.column_config.TextColumn("Group"),
                                    "student_name": st.column_config.TextColumn("Student Name"),
                                },
                                disabled=[
                                    "student_id",
                                    "group_name",
                                    "activity_name",
                                    "question",
                                    "answer",
                                ],
                                hide_index=True,
                                use_container_width=True,
                                key="responses_editor",
                            )
                            editable = editable_result.reset_index().rename(columns={"index": "id"})
                            changes = []
                            for _, new in editable.iterrows():
                                row_id = int(new["id"])
                                base = base_by_id.loc[row_id]
                                base_score = base["score"]
                                new_score = new["score"]
                                score_changed = not (
                                    (pd.isna(base_score) and pd.isna(new_score))
                                    or base_score == new_score
                                )
                                if score_changed:
                                    changes.append(
                                        {
                                            "id": int(new["id"]),
                                            "score": None if pd.isna(new_score) else float(new_score),
                                            "checked": bool(base["checked"]),
                                        }
                                    )
                            if st.button("💾 Save grades", use_container_width=True, disabled=not changes):
                                update_scores(changes)
                                st.success("Grades updated.")
                        else:
                            display_df = df_responses.drop(columns=["checked", "activity_id", "id"], errors="ignore")
                            st.dataframe(display_df, hide_index=True, use_container_width=True)
                            st.info("Score edits are disabled when viewing all activities.")

        # ----- Tab: Participation -----
        with tab_participation:
            st.markdown("### ⭐ Participation (by date)")
            participation_date = st.date_input(
                "Participation date",
                value=today_th(),
                key="participation_date_input",
            )
            participation_date_str = participation_date.isoformat()
            checkins_for_date = load_check_ins(participation_date_str)
            if checkins_for_date.empty:
                st.info("No student check-ins for this date yet.")
            else:
                st.caption("Students who checked in on this date:")
                daily_df = get_daily_participation(participation_date_str)
                daily_df = apply_daily_participation_adjustments(daily_df, participation_date_str)
                display_daily = daily_df.rename(
                    columns={
                        "student_id": "Student ID",
                        "student_name": "Student Name",
                        "recorded_at": "Checked-in at",
                        "participation_points": "Participation points",
                        "teacher_note": "Teacher note",
                    }
                )
                display_daily = ensure_dataframe_columns(
                    display_daily,
                    {
                        "Student ID": "",
                        "Student Name": "",
                        "Checked-in at": "",
                        "Participation points": 0.0,
                        "Teacher note": "",
                    },
                )
                display_daily = display_daily[
                    [
                        "Student ID",
                        "Student Name",
                        "Checked-in at",
                        "Participation points",
                        "Teacher note",
                    ]
                ]
                editable_daily = st.data_editor(
                    display_daily,
                    column_config={
                        "Participation points": st.column_config.NumberColumn("Participation points", min_value=0.0, step=0.5),
                        "Teacher note": st.column_config.TextColumn("Teacher note"),
                    },
                    disabled=["Student ID", "Student Name", "Checked-in at"],
                    hide_index=True,
                    use_container_width=True,
                    key="daily_participation_editor",
                )
                st.caption("Use +/- buttons for quick adjustments, then press Save to persist.")
                for idx, row in editable_daily.iterrows():
                    student_id = row.get("Student ID", "")
                    student_name = row.get("Student Name", "") or "Unnamed"
                    current_points = row.get("Participation points", 0.0) or 0.0
                    col_label, col_minus, col_plus = st.columns([6, 1, 1])
                    with col_label:
                        st.write(f"{student_id} · {student_name} — {current_points:.2f} pts")
                    with col_minus:
                        if st.button("➖", key=f"participation_minus_{participation_date_str}_{student_id}"):
                            adjust_daily_score(participation_date_str, str(student_id), -PARTICIPATION_STEP)
                            st.rerun()
                    with col_plus:
                        if st.button("➕", key=f"participation_plus_{participation_date_str}_{student_id}"):
                            adjust_daily_score(participation_date_str, str(student_id), PARTICIPATION_STEP)
                            st.rerun()
                if st.button("💾 Save daily participation", use_container_width=True):
                    revert_daily = editable_daily.rename(
                        columns={
                            "Student ID": "student_id",
                            "Participation points": "participation_points",
                            "Teacher note": "teacher_note",
                        }
                    )
                    save_daily_participation(
                        participation_date_str, revert_daily[["student_id", "participation_points", "teacher_note"]]
                    )
                    adjustments = st.session_state.get("daily_participation_adjust", {})
                    st.session_state["daily_participation_adjust"] = {
                        key: val for key, val in adjustments.items() if not key.startswith(f"{participation_date_str}|")
                    }
                    st.success("Participation points saved for this date.")

        # ----- Tab: Score overview -----
        with tab_scores:
            st.markdown("### 📊 Gradebook & Exports (by student)")
            student_roster_df = load_roster()
            answers_all = load_answers()
            answer_ids = normalize_student_ids(answers_all.get("student_id", pd.Series(dtype=str)).tolist())
            activities = activities_all["id"].tolist()
            per_activity = []
            for act_id in activities:
                summary_df = get_participation(act_id)
                per_activity.append(summary_df)
            checkin_students = load_all_checkin_students()
            checkin_ids = normalize_student_ids(checkin_students.get("student_id", pd.Series(dtype=str)).tolist())
            daily_totals = load_all_daily_participation_totals()
            daily_ids = normalize_student_ids(daily_totals.get("student_id", pd.Series(dtype=str)).tolist())
            roster_ids = normalize_student_ids(student_roster_df.get("student_id", pd.Series(dtype=str)).tolist())
            computed_ids = roster_ids | answer_ids | checkin_ids | daily_ids
            student_options = ["All students"]
            if computed_ids:
                student_options.extend(sorted(computed_ids))
            selected_student = st.selectbox(
                "Student ID",
                options=student_options,
                key="gradebook_student_select",
            )
            st.caption("Showing per-student totals across all activities.")
            gradebook_df = pd.DataFrame()
            summary_display = pd.DataFrame()
            detail_display = pd.DataFrame()
            if per_activity:
                gradebook_df = pd.concat(per_activity, ignore_index=True)
            if gradebook_df.empty:
                gradebook_df = pd.DataFrame(
                    columns=["student_id", "student_name", "total_score", "participation_points", "overall_grade"]
                )
            roster_id_set = roster_ids
            response_ids = normalize_student_ids(gradebook_df.get("student_id", pd.Series(dtype=str)).tolist())
            all_student_ids = roster_id_set | checkin_ids | daily_ids | response_ids | answer_ids
            has_any_data = (
                bool(all_student_ids)
                or not gradebook_df.empty
                or not answers_all.empty
                or not daily_totals.empty
            )
            if not has_any_data:
                st.info("No grade data available for this selection yet.")
            else:
                gradebook_df = gradebook_df.reindex(columns=["student_id", "student_name", "total_score", "participation_points", "overall_grade"])
                gradebook_df = attach_names(gradebook_df)
                student_summary = (
                    gradebook_df.groupby(["student_id", "student_name"], dropna=False)
                    .agg(
                        total_score=("total_score", "sum"),
                        participation_points=("participation_points", "sum"),
                        overall_grade=("overall_grade", "sum"),
                    )
                    .reset_index()
                )
                if student_summary.empty and not answers_all.empty:
                    fallback_answers = answers_all.copy()
                    if "student_name" not in fallback_answers.columns:
                        fallback_answers["student_name"] = ""
                    fallback_answers["score"] = fallback_answers.get("score", pd.Series(dtype=float)).fillna(0.0)
                    summary_from_answers = (
                        fallback_answers.groupby(["student_id", "student_name"], dropna=False)
                        .agg(total_score=("score", "sum"))
                        .reset_index()
                    )
                    summary_from_answers["participation_points"] = 0.0
                    summary_from_answers["overall_grade"] = summary_from_answers["total_score"]
                    student_summary = summary_from_answers
                if "student_name" not in student_summary.columns:
                    student_summary["student_name"] = ""
                if not daily_totals.empty:
                    daily_totals = attach_names(daily_totals)
                    daily_merge = daily_totals.rename(columns={"student_name": "daily_student_name"})
                    student_summary = student_summary.merge(daily_merge, on="student_id", how="outer")
                    if "student_name" not in student_summary.columns:
                        student_summary["student_name"] = ""
                    if "daily_student_name" in student_summary.columns:
                        student_summary["student_name"] = student_summary["student_name"].fillna(
                            student_summary["daily_student_name"]
                        )
                        student_summary = student_summary.drop(columns=["daily_student_name"], errors="ignore")
                    for col in ("total_score", "participation_points", "overall_grade", "daily_points"):
                        if col not in student_summary.columns:
                            student_summary[col] = 0
                        else:
                            student_summary[col] = student_summary[col].fillna(0)
                    student_summary["participation_points"] = (
                        student_summary["participation_points"] + student_summary["daily_points"]
                    )
                    student_summary = student_summary.drop(columns=["daily_points"], errors="ignore")
                    existing_ids = student_summary["student_id"].tolist() if "student_id" in student_summary.columns else []
                    missing_ids = [sid for sid in all_student_ids if sid not in existing_ids]
                    if missing_ids:
                        roster_lookup = student_roster_df.set_index("student_id").to_dict().get("student_name", {})
                        for sid in missing_ids:
                            student_summary = pd.concat(
                                [
                                    student_summary,
                                    pd.DataFrame(
                                        {
                                            "student_id": [sid],
                                            "student_name": [roster_lookup.get(sid, sid)],
                                            "total_score": [0],
                                            "participation_points": [0],
                                            "overall_grade": [0],
                                        }
                                    ),
                                ],
                                ignore_index=True,
                            )
                    student_summary["overall_grade"] = student_summary["total_score"] + student_summary["participation_points"]
                    student_summary = student_summary.rename(
                        columns={
                            "student_id": "Student ID",
                            "student_name": "Student Name",
                            "total_score": "TTL Score from Activities",
                            "participation_points": "TTL Participation Points",
                            "overall_grade": "Final Score",
                        }
                    )
                    summary_display = student_summary.copy()
                    summary_display = ensure_student_names(summary_display, "Student ID", "Student Name")
                    summary_view = summary_display.copy()
                    detail_df = answers_all.copy()
                    if selected_student != "All students":
                        detail_df = detail_df[detail_df["student_id"] == selected_student]
                        summary_view = summary_view[summary_view["Student ID"] == selected_student]
                    if detail_df.empty:
                        st.info("No response-level scores available for this selection yet.")
                        detail_display = pd.DataFrame(
                            columns=[
                                "Student ID",
                                "Student Name",
                                "Group Name",
                                "Activity Date",
                                "Activity",
                                "Question #",
                                "Question",
                                "Submitted Response",
                                "Score",
                            ]
                        )
                    else:
                        activity_dates = {
                            row["id"]: safe_date_label(row["activity_date"])
                            for _, row in activities_all.iterrows()
                        }
                        detail_df["activity_date"] = detail_df["activity_id"].map(activity_dates)
                        detail_display = detail_df.rename(
                            columns={
                                "student_id": "Student ID",
                                "student_name": "Student Name",
                                "group_name": "Group Name",
                                "activity_name": "Activity",
                                "activity_date": "Activity Date",
                                "question_no": "Question #",
                                "question": "Question",
                                "answer": "Submitted Response",
                                "score": "Score",
                        }
                    )
                    detail_display = ensure_dataframe_columns(
                        detail_display,
                        {
                            "Student ID": "",
                            "Student Name": "",
                            "Group Name": "",
                            "Activity Date": "",
                            "Activity": "",
                            "Question #": "",
                            "Question": "",
                            "Submitted Response": "",
                            "Score": "",
                        },
                    )
                    detail_display = detail_display[
                        [
                            "Student ID",
                            "Student Name",
                            "Group Name",
                            "Activity Date",
                            "Activity",
                            "Question #",
                            "Question",
                            "Submitted Response",
                            "Score",
                        ]
                    ]
                    detail_display = ensure_student_names(detail_display, "Student ID", "Student Name")
                    participation_entries = load_participation_daily_entries()
                    participation_entries = attach_names(participation_entries)
                    participation_entries = participation_entries.rename(
                        columns={
                            "student_id": "Student ID",
                            "student_name": "Student Name",
                            "check_in_date": "Date",
                            "participation_points": "Participation Points",
                            "teacher_note": "Teacher Note",
                        }
                    )
                    participation_entries = ensure_dataframe_columns(
                        participation_entries,
                        {
                            "Student ID": "",
                            "Student Name": "",
                            "Date": "",
                            "Participation Points": 0.0,
                            "Teacher Note": "",
                        },
                    )
                    participation_entries = participation_entries[
                        ["Student ID", "Student Name", "Date", "Participation Points", "Teacher Note"]
                    ]
                    participation_entries = ensure_student_names(participation_entries, "Student ID", "Student Name")
                    if selected_student != "All students":
                        participation_entries = participation_entries[participation_entries["Student ID"] == selected_student]
                    if HAS_XLSXWRITER and (not summary_view.empty or not detail_display.empty or not participation_entries.empty):
                        export_buffer = io.BytesIO()
                        with pd.ExcelWriter(export_buffer, engine="xlsxwriter") as writer:  # type: ignore[arg-type]
                            summary_view.to_excel(writer, sheet_name="Summary", index=False)
                            detail_display.to_excel(writer, sheet_name="By Activity", index=False)
                            participation_entries.to_excel(writer, sheet_name="Participation Points", index=False)
                        st.download_button(
                            "⬇️ Export gradebook (.xlsx)",
                            data=export_buffer.getvalue(),
                            file_name="gradebook_by_student.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                        )
                    elif not HAS_XLSXWRITER:
                        st.warning("Install the optional dependency `xlsxwriter` (see requirements.txt) to enable Excel exports.")
                    st.markdown("**Summary by Student**")
                    if summary_view.empty:
                        st.info("No student summary available for this selection.")
                    else:
                        st.dataframe(summary_view, hide_index=True, use_container_width=True)
                    st.markdown("**Summary by Activities**")
                    st.dataframe(detail_display, hide_index=True, use_container_width=True)
                    st.markdown("**Summary of Participation Points**")
                    st.dataframe(participation_entries, hide_index=True, use_container_width=True)

        # ----- Tab: Import student names -----
        with tab_import:
            st.markdown("### 🧑‍🎓 Student roster (IDs → names)")
            roster_df = load_roster()
            st.caption("Upload a CSV with columns student_id, student_name. Existing IDs will be updated.")
            uploaded_roster = st.file_uploader("Upload roster CSV", type=["csv"], key="roster_upload")
            if uploaded_roster is not None:
                try:
                    roster_file = pd.read_csv(uploaded_roster)
                    if "student_id" not in roster_file.columns or ("student_name" not in roster_file.columns):
                        st.error("CSV must include 'student_id' and 'student_name' columns.")
                    else:
                        records = []
                        for _, row in roster_file.iterrows():
                            sid = str(row.get("student_id", "")).strip()
                            sname = str(row.get("student_name", "")).strip()
                            if sid and sname:
                                records.append((sid, sname))
                        if records:
                            upsert_roster(records)
                            st.success(f"Imported {len(records)} roster entries.")
                            roster_df = load_roster()
                        else:
                            st.warning("No valid rows found to import.")
                except Exception as exc:
                    st.error(f"Unable to read roster CSV: {exc}")
            if roster_df.empty:
                st.info("No roster loaded yet.")
            edit_display = (
                pd.DataFrame(columns=["Student ID", "Student Name"])
                if roster_df.empty
                else roster_df.rename(
                    columns={
                        "student_id": "Student ID",
                        "student_name": "Student Name",
                    }
                )
            )
            st.markdown("#### ✏️ Edit roster entries")
            editable_roster = st.data_editor(
                edit_display,
                column_config={
                    "Student ID": st.column_config.TextColumn("Student ID", help="IDs cannot be changed here."),
                    "Student Name": st.column_config.TextColumn("Student Name", help="Edit the student name."),
                },
                disabled=["Student ID"],
                hide_index=True,
                use_container_width=True,
                key="roster_editor",
            )
            if st.button("💾 Save roster edits", use_container_width=True):
                records = [
                    (str(row["Student ID"]).strip(), str(row["Student Name"]).strip())
                    for _, row in editable_roster.iterrows()
                    if str(row["Student ID"]).strip() and str(row["Student Name"]).strip()
                ]
                if not records:
                    st.warning("No valid rows to save.")
                else:
                    upsert_roster(records)
                    st.success("Roster updated.")
                    roster_df = load_roster()
            if not roster_df.empty:
                st.markdown("#### 🗑️ Delete roster entries")
                roster_delete = st.multiselect(
                    "Select Student IDs to delete",
                    options=roster_df["student_id"].tolist(),
                    format_func=lambda x: f"{x} · {get_student_name(x) or 'Unnamed'}",
                    key="roster_delete_select",
                )
                if st.button("Delete selected students", type="secondary", disabled=not roster_delete):
                    con = get_con()
                    cur = con.cursor()
                    placeholders = ",".join(["?"] * len(roster_delete))
                    try:
                        cur.execute(f"DELETE FROM student_roster WHERE student_id IN ({placeholders})", roster_delete)
                        con.commit()
                        st.success(f"Deleted {len(roster_delete)} students from roster.")
                        roster_df = load_roster()
                    except Exception as exc:
                        con.rollback()
                        st.error(f"Unable to delete roster entries: {exc}")
                    finally:
                        con.close()
            st.markdown("#### ➕ Add student manually")
            with st.form("add_student_form"):
                manual_id = st.text_input("Student ID", placeholder="e.g., S001")
                manual_name = st.text_input("Student Name", placeholder="e.g., Jane Doe")
                manual_submit = st.form_submit_button("Add student")
            if manual_submit:
                sid = manual_id.strip()
                sname = manual_name.strip()
                if not sid or not sname:
                    st.error("Both Student ID and Student Name are required.")
                else:
                    upsert_roster([(sid, sname)])
                    st.success(f"Added/updated roster entry for {sid}.")
                    roster_df = load_roster()


        # ----- Tab: Database backup -----
        with tab_backup:
            st.markdown("### 🗃️ Database backup & restore")
            backup_bytes = backup_database()
            st.download_button(
                "💾 Download DB backup",
                data=backup_bytes,
                file_name=f"answers_backup_{today_th().isoformat()}.db",
                mime="application/x-sqlite3",
                use_container_width=True,
            )
            with st.expander("⚠️ Restore database from backup"):
                st.warning(
                    "Restoring will overwrite the current database. A safety copy (.bak_YYYYMMDDHHMMSS) will be kept."
                )
                uploaded_db = st.file_uploader("Upload .db backup file", type=["db", "sqlite"], accept_multiple_files=False)
                if uploaded_db is not None:
                    success, msg = restore_database(uploaded_db.read())
                    if success:
                        st.success(msg)
                    else:
                        st.error(msg)
            with st.expander("🗑️ Delete data from database"):
                st.warning("This action permanently removes data from the current database. Download a backup first.")
                delete_choices = st.multiselect(
                    "Select the data you want to delete",
                    options=list(DATA_DELETE_ACTIONS.keys()),
                    key="delete_sections_select",
                )
                confirm_delete = st.text_input("Type DELETE to confirm", key="delete_sections_confirm")
                if st.button("Delete selected data", type="primary", disabled=not delete_choices):
                    if confirm_delete.strip().upper() != "DELETE":
                        st.error("Please type DELETE in the confirmation box.")
                    else:
                        ok, message = delete_data_sections(delete_choices)
                        if ok:
                            st.success(message)
                        else:
                            st.error(message)
