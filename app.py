import os
import json
import re
import sqlite3
import uuid
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (Flask, render_template, request, jsonify, redirect,
                   url_for, session, send_from_directory, flash)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pypdf
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "studynest.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "studynest-dev-secret-change-in-prod")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
    -- Auth tables
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    -- Per-user study data
    CREATE TABLE IF NOT EXISTS pdfs (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        title TEXT NOT NULL,
        text_content TEXT NOT NULL,
        summary TEXT,
        topics TEXT,
        definitions TEXT,
        formulas TEXT,
        created_at TEXT NOT NULL,
        page_count INTEGER DEFAULT 0,
        word_count INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS quiz_sets (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        pdf_id TEXT NOT NULL,
        title TEXT NOT NULL,
        difficulty TEXT NOT NULL,
        q_types TEXT NOT NULL,
        questions_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (pdf_id) REFERENCES pdfs(id)
    );

    CREATE TABLE IF NOT EXISTS attempts (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        quiz_set_id TEXT NOT NULL,
        pdf_id TEXT NOT NULL,
        score INTEGER NOT NULL,
        total INTEGER NOT NULL,
        time_taken_sec INTEGER DEFAULT 0,
        answers_json TEXT NOT NULL,
        wrong_questions_json TEXT,
        topic_breakdown_json TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS flashcards (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        pdf_id TEXT NOT NULL,
        front TEXT NOT NULL,
        back TEXT NOT NULL,
        topic TEXT,
        learned INTEGER DEFAULT 0,
        bookmarked INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY (pdf_id) REFERENCES pdfs(id)
    );

    CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        pdf_id TEXT NOT NULL,
        note_type TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (pdf_id) REFERENCES pdfs(id)
    );

    CREATE TABLE IF NOT EXISTS chat_history (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        pdf_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (pdf_id) REFERENCES pdfs(id)
    );

    CREATE TABLE IF NOT EXISTS user_state (
        id TEXT PRIMARY KEY,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        current_streak INTEGER DEFAULT 0,
        longest_streak INTEGER DEFAULT 0,
        last_study_date TEXT,
        badges_json TEXT DEFAULT '[]',
        api_provider TEXT DEFAULT '',
        api_key TEXT DEFAULT '',
        FOREIGN KEY (id) REFERENCES users(id)
    );
    """)
    conn.commit()
    conn.close()


init_db()

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def current_user_id():
    return session.get("user_id")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user_id():
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    uid = current_user_id()
    if not uid:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(user) if user else None

# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user_id():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name  = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            return render_template("signup.html", error="All fields are required.", name=name, email=email)
        if len(password) < 8:
            return render_template("signup.html", error="Password must be at least 8 characters.", name=name, email=email)

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if existing:
            return render_template("signup.html", error="An account with that email already exists.", email=email)

        user_id = str(uuid.uuid4())
        pw_hash = generate_password_hash(password)
        conn = get_db()
        conn.execute(
            "INSERT INTO users (id,name,email,password_hash,created_at) VALUES (?,?,?,?,?)",
            (user_id, name, email, pw_hash, datetime.now().isoformat())
        )
        conn.execute(
            "INSERT OR IGNORE INTO user_state (id,xp,level,badges_json) VALUES (?,0,1,'[]')",
            (user_id,)
        )
        conn.commit()
        conn.close()

        # Log the user straight in — no email verification required
        session["user_id"] = user_id
        session["user_name"] = name
        return redirect(url_for("dashboard"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user_id():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if not user or not check_password_hash(user["password_hash"], password):
            return render_template("login.html", error="Incorrect email or password.", email=email)
        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        next_url = request.args.get("next") or url_for("dashboard")
        return redirect(next_url)
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------------------------------------------------------------------
# Helpers: gamification  (now per-user)
# ---------------------------------------------------------------------------

def get_user_state():
    uid = current_user_id()
    conn = get_db()
    row = conn.execute("SELECT * FROM user_state WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else {"xp":0,"level":1,"current_streak":0,"longest_streak":0,
                                   "last_study_date":None,"badges_json":"[]","api_provider":"","api_key":""}


def xp_for_level(level):
    return 100 * level * (level + 1) // 2


def add_xp(amount):
    uid = current_user_id()
    conn = get_db()
    state = conn.execute("SELECT * FROM user_state WHERE id=?", (uid,)).fetchone()
    new_xp = state["xp"] + amount
    level  = state["level"]
    while new_xp >= xp_for_level(level):
        level += 1
    conn.execute("UPDATE user_state SET xp=?, level=? WHERE id=?", (new_xp, level, uid))
    conn.commit()
    conn.close()
    return new_xp, level


def touch_streak():
    uid = current_user_id()
    conn = get_db()
    state = conn.execute("SELECT * FROM user_state WHERE id=?", (uid,)).fetchone()
    today = date.today().isoformat()
    last  = state["last_study_date"]
    current = state["current_streak"]
    longest = state["longest_streak"]

    if last == today:
        pass
    elif last == (date.today() - timedelta(days=1)).isoformat():
        current += 1
    else:
        current = 1

    longest = max(longest, current)
    badges  = json.loads(state["badges_json"] or "[]")
    if current >= 7  and "streak_7"  not in badges: badges.append("streak_7")
    if current >= 30 and "streak_30" not in badges: badges.append("streak_30")

    conn.execute("UPDATE user_state SET current_streak=?,longest_streak=?,last_study_date=?,badges_json=? WHERE id=?",
                 (current, longest, today, json.dumps(badges), uid))
    conn.commit()
    conn.close()


def check_badges():
    uid = current_user_id()
    conn = get_db()
    state = conn.execute("SELECT * FROM user_state WHERE id=?", (uid,)).fetchone()
    badges = json.loads(state["badges_json"] or "[]")
    total_attempts = conn.execute("SELECT COUNT(*) c FROM attempts WHERE user_id=?", (uid,)).fetchone()["c"]
    total_q  = conn.execute("SELECT SUM(total) t FROM attempts WHERE user_id=?", (uid,)).fetchone()["t"] or 0
    total_pdfs = conn.execute("SELECT COUNT(*) c FROM pdfs WHERE user_id=?", (uid,)).fetchone()["c"]

    if total_attempts >= 1  and "quiz_first"  not in badges: badges.append("quiz_first")
    if total_attempts >= 10 and "quiz_master" not in badges: badges.append("quiz_master")
    if total_q >= 100       and "century"     not in badges: badges.append("century")
    if total_pdfs >= 5      and "librarian"   not in badges: badges.append("librarian")

    conn.execute("UPDATE user_state SET badges_json=? WHERE id=?", (json.dumps(badges), uid))
    conn.commit()
    conn.close()


BADGE_INFO = {
    "streak_7":   {"name":"7-Day Streak",  "desc":"Studied 7 days in a row",     "icon":"flame"},
    "streak_30":  {"name":"30-Day Streak", "desc":"Studied 30 days in a row",    "icon":"flame"},
    "quiz_first": {"name":"First Steps",   "desc":"Completed your first quiz",   "icon":"footprints"},
    "quiz_master":{"name":"Quiz Master",   "desc":"Completed 10 quizzes",        "icon":"crown"},
    "century":    {"name":"Century Club",  "desc":"Answered 100+ questions",     "icon":"target"},
    "librarian":  {"name":"Librarian",     "desc":"Uploaded 5 PDFs",             "icon":"library"},
}

# ---------------------------------------------------------------------------
# Helpers: PDF text extraction
# ---------------------------------------------------------------------------

def extract_pdf_text(filepath):
    reader = pypdf.PdfReader(filepath)
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n\n".join(pages), len(reader.pages)


def naive_topics(text, limit=12):
    lines = text.split("\n")
    candidates = []
    for line in lines:
        s = line.strip()
        if 3 < len(s) < 70 and not s.endswith(".") and (s.isupper() or s.istitle()):
            candidates.append(s)
    seen = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
        if len(seen) >= limit:
            break
    return seen

# ---------------------------------------------------------------------------
# AI provider abstraction
# ---------------------------------------------------------------------------

class AIError(Exception):
    pass


def call_ai(prompt, system="You are a helpful study assistant.", max_tokens=4000):
    state = get_user_state()
    provider = (state.get("api_provider") or "").lower()
    api_key  = state.get("api_key") or ""
    if not api_key or not provider:
        raise AIError("No API key configured. Add your Anthropic or OpenAI key in Settings.")

    if provider == "anthropic":
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-sonnet-4-6","max_tokens":max_tokens,"system":system,
                  "messages":[{"role":"user","content":prompt}]},
            timeout=120,
        )
        if resp.status_code != 200:
            raise AIError(f"Anthropic API error: {resp.status_code} {resp.text[:300]}")
        data  = resp.json()
        parts = [b["text"] for b in data.get("content",[]) if b.get("type")=="text"]
        return "\n".join(parts)

    elif provider == "openai":
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
            json={"model":"gpt-4o-mini","max_tokens":max_tokens,
                  "messages":[{"role":"system","content":system},{"role":"user","content":prompt}]},
            timeout=120,
        )
        if resp.status_code != 200:
            raise AIError(f"OpenAI API error: {resp.status_code} {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]

    elif provider == "gemini":
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            headers={"x-goog-api-key":api_key,"Content-Type":"application/json"},
            json={"system_instruction":{"parts":[{"text":system}]},
                  "contents":[{"role":"user","parts":[{"text":prompt}]}],
                  "generationConfig":{"maxOutputTokens":max_tokens}},
            timeout=120,
        )
        if resp.status_code != 200:
            raise AIError(f"Gemini API error: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        try:
            candidate = data["candidates"][0]
            parts = candidate.get("content",{}).get("parts",[])
            text  = "".join(p.get("text","") for p in parts)
            if not text:
                raise AIError(f"Gemini returned no text (finish: {candidate.get('finishReason','?')})")
            return text
        except (KeyError, IndexError):
            raise AIError(f"Unexpected Gemini response: {str(data)[:300]}")
    else:
        raise AIError("Unknown AI provider configured.")


def extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(json)?","",text).strip()
    text = re.sub(r"```$","",text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for open_ch, close_ch in [("[","]"),("{","}")]:
        start = text.find(open_ch)
        end   = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end+1])
            except Exception:
                continue
    raise AIError("Could not parse AI response as JSON.")

# ---------------------------------------------------------------------------
# Context processor – inject user info into every template
# ---------------------------------------------------------------------------

@app.context_processor
def inject_user():
    uid = current_user_id()
    if uid:
        state = get_user_state()
        next_level_xp = xp_for_level(state["level"])
        prev_level_xp = xp_for_level(state["level"]-1) if state["level"] > 1 else 0
        progress_pct  = 0
        if next_level_xp > prev_level_xp:
            progress_pct = round(100*(state["xp"]-prev_level_xp)/(next_level_xp-prev_level_xp))
        return dict(current_user={"id":uid,"name":session.get("user_name","")},
                    state=state, progress_pct=progress_pct, next_level_xp=next_level_xp)
    return dict(current_user=None, state=None, progress_pct=0, next_level_xp=0)

# ---------------------------------------------------------------------------
# Routes: pages  (all protected with @login_required)
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    uid = current_user_id()
    conn = get_db()
    pdfs     = conn.execute("SELECT * FROM pdfs WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
    attempts = conn.execute("SELECT * FROM attempts WHERE user_id=? ORDER BY created_at DESC LIMIT 8", (uid,)).fetchall()
    total_attempts   = conn.execute("SELECT COUNT(*) c FROM attempts WHERE user_id=?", (uid,)).fetchone()["c"]
    avg_score_row    = conn.execute("SELECT AVG(score*100.0/total) a FROM attempts WHERE user_id=? AND total>0", (uid,)).fetchone()
    avg_score        = round(avg_score_row["a"] or 0)
    total_cards      = conn.execute("SELECT COUNT(*) c FROM flashcards WHERE user_id=?", (uid,)).fetchone()["c"]
    learned_cards    = conn.execute("SELECT COUNT(*) c FROM flashcards WHERE user_id=? AND learned=1", (uid,)).fetchone()["c"]
    conn.close()

    return render_template("dashboard.html", pdfs=pdfs, attempts=attempts,
                           total_attempts=total_attempts, avg_score=avg_score,
                           total_cards=total_cards, learned_cards=learned_cards,
                           active="dashboard")


@app.route("/library")
@login_required
def library():
    uid = current_user_id()
    conn = get_db()
    pdfs = conn.execute("SELECT * FROM pdfs WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
    conn.close()
    return render_template("library.html", pdfs=pdfs, active="library")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    uid  = current_user_id()
    file = request.files.get("pdf_file")
    if not file or file.filename == "":
        return jsonify({"error":"No file selected."}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error":"Please upload a PDF file."}), 400

    pdf_id   = str(uuid.uuid4())
    filename = secure_filename(file.filename)
    save_path = os.path.join(UPLOAD_DIR, f"{pdf_id}_{filename}")
    file.save(save_path)

    try:
        text, page_count = extract_pdf_text(save_path)
    except Exception as e:
        return jsonify({"error":f"Could not read PDF: {e}"}), 400

    if not text.strip():
        return jsonify({"error":"No extractable text found (this PDF may be scanned/image-only)."}), 400

    title      = filename.rsplit(".",1)[0].replace("_"," ").replace("-"," ").title()
    word_count = len(text.split())

    conn = get_db()
    conn.execute(
        """INSERT INTO pdfs (id,user_id,filename,title,text_content,summary,topics,definitions,formulas,created_at,page_count,word_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pdf_id, uid, filename, title, text, "", json.dumps([]), json.dumps([]), json.dumps([]),
         datetime.now().isoformat(), page_count, word_count),
    )
    conn.commit()
    conn.close()
    return jsonify({"pdf_id":pdf_id,"redirect":url_for("pdf_detail", pdf_id=pdf_id)})


@app.route("/pdf/<pdf_id>")
@login_required
def pdf_detail(pdf_id):
    uid = current_user_id()
    conn = get_db()
    pdf = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    if not pdf:
        conn.close()
        return redirect(url_for("library"))
    quiz_sets     = conn.execute("SELECT * FROM quiz_sets WHERE pdf_id=? ORDER BY created_at DESC", (pdf_id,)).fetchall()
    flashcard_count = conn.execute("SELECT COUNT(*) c FROM flashcards WHERE pdf_id=? AND user_id=?", (pdf_id, uid)).fetchone()["c"]
    notes         = conn.execute("SELECT * FROM notes WHERE pdf_id=? AND user_id=? ORDER BY created_at DESC", (pdf_id, uid)).fetchall()
    conn.close()

    topics      = json.loads(pdf["topics"] or "[]")
    definitions = json.loads(pdf["definitions"] or "[]")
    formulas    = json.loads(pdf["formulas"] or "[]")

    return render_template("pdf_detail.html", pdf=pdf, topics=topics, definitions=definitions,
                           formulas=formulas, quiz_sets=quiz_sets, flashcard_count=flashcard_count,
                           notes=notes, active="library")


@app.route("/pdf/<pdf_id>/analyze", methods=["POST"])
@login_required
def analyze_pdf(pdf_id):
    uid = current_user_id()
    conn = get_db()
    pdf = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    if not pdf:
        conn.close()
        return jsonify({"error":"PDF not found."}), 404

    text   = pdf["text_content"][:18000]
    prompt = f"""Analyze the following study material and return ONLY valid JSON with this exact shape:
{{
  "summary": "a 3-4 sentence topic-wise summary",
  "topics": ["topic 1", ...up to 10...],
  "definitions": [{{"term": "...", "definition": "..."}},...up to 8...],
  "formulas": [{{"name": "...", "expression": "...", "context": "..."}},...or empty array...]
}}

DOCUMENT:
{text}"""

    try:
        raw  = call_ai(prompt, system="You extract structured study data. Respond with ONLY valid JSON.")
        data = extract_json(raw)
    except AIError as e:
        conn.close()
        return jsonify({"error":str(e)}), 400

    conn.execute("UPDATE pdfs SET summary=?,topics=?,definitions=?,formulas=? WHERE id=?",
                 (data.get("summary",""), json.dumps(data.get("topics",[])),
                  json.dumps(data.get("definitions",[])), json.dumps(data.get("formulas",[])), pdf_id))
    conn.commit()
    conn.close()
    return jsonify({"ok":True, **data})


@app.route("/pdf/<pdf_id>/delete", methods=["POST"])
@login_required
def delete_pdf(pdf_id):
    uid = current_user_id()
    conn = get_db()
    conn.execute("DELETE FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid))
    conn.execute("DELETE FROM quiz_sets WHERE pdf_id=? AND user_id=?", (pdf_id, uid))
    conn.execute("DELETE FROM flashcards WHERE pdf_id=? AND user_id=?", (pdf_id, uid))
    conn.execute("DELETE FROM notes WHERE pdf_id=? AND user_id=?", (pdf_id, uid))
    conn.execute("DELETE FROM chat_history WHERE pdf_id=? AND user_id=?", (pdf_id, uid))
    conn.commit()
    conn.close()
    return redirect(url_for("library"))


# ---- Quiz ----

@app.route("/pdf/<pdf_id>/quiz/new", methods=["GET","POST"])
@login_required
def new_quiz(pdf_id):
    uid = current_user_id()
    conn = get_db()
    pdf = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    conn.close()
    if not pdf:
        return redirect(url_for("library"))

    if request.method == "GET":
        return render_template("quiz_setup.html", pdf=pdf, active="library")

    difficulty = request.form.get("difficulty","medium")
    q_types    = request.form.getlist("q_types") or ["mcq"]
    count      = int(request.form.get("count", 8))

    type_labels = {
        "mcq":"Multiple Choice Questions (4 options, one correct, include 'correct_index')",
        "true_false":"True/False statements (include 'correct_answer' as true or false)",
        "fill_blank":"Fill in the Blank (use ___ for the blank, include 'correct_answer')",
        "match":"Match the Following (include 'pairs' as a list of {left, right}, 4-5 pairs)",
        "short_answer":"Short Answer Questions (include 'model_answer', 1-3 sentences)",
    }
    requested_types = [type_labels[t] for t in q_types if t in type_labels]

    prompt = f"""Based on the study material below, generate {count} questions at {difficulty.upper()} difficulty.
Use ONLY these question types, distributing roughly evenly: {', '.join(requested_types)}.

Return ONLY valid JSON (no markdown fences) as a list of question objects. Each object MUST include:
- "type": one of "mcq","true_false","fill_blank","match","short_answer"
- "question": the question text
- "topic": a short topic tag
- type-specific fields as described above
- "explanation": a 1-sentence explanation of the correct answer

MATERIAL:
{pdf['text_content'][:16000]}"""

    try:
        raw       = call_ai(prompt, system="You are an expert quiz writer. Respond with ONLY a valid JSON array.", max_tokens=4000)
        questions = extract_json(raw)
        if isinstance(questions, dict):
            questions = questions.get("questions",[])
    except AIError as e:
        return render_template("quiz_setup.html", pdf=pdf, active="library", error=str(e))

    quiz_id = str(uuid.uuid4())
    conn = get_db()
    conn.execute(
        "INSERT INTO quiz_sets (id,user_id,pdf_id,title,difficulty,q_types,questions_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (quiz_id, uid, pdf_id, f"{pdf['title']} — {difficulty.title()} Quiz",
         difficulty, json.dumps(q_types), json.dumps(questions), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("take_quiz", quiz_id=quiz_id))


@app.route("/quiz/<quiz_id>")
@login_required
def take_quiz(quiz_id):
    uid = current_user_id()
    conn = get_db()
    quiz = conn.execute("SELECT * FROM quiz_sets WHERE id=? AND user_id=?", (quiz_id, uid)).fetchone()
    conn.close()
    if not quiz:
        return redirect(url_for("library"))
    questions  = json.loads(quiz["questions_json"])
    timed      = request.args.get("timed","0")
    time_limit = request.args.get("minutes","10")
    return render_template("quiz_take.html", quiz=quiz, questions=questions,
                           q_json=quiz["questions_json"], timed=timed, time_limit=time_limit, active="library")


@app.route("/quiz/<quiz_id>/submit", methods=["POST"])
@login_required
def submit_quiz(quiz_id):
    uid = current_user_id()
    conn = get_db()
    quiz = conn.execute("SELECT * FROM quiz_sets WHERE id=? AND user_id=?", (quiz_id, uid)).fetchone()
    if not quiz:
        conn.close()
        return jsonify({"error":"Quiz not found"}), 404

    questions    = json.loads(quiz["questions_json"])
    payload      = request.get_json()
    user_answers = payload.get("answers",{})
    time_taken   = payload.get("time_taken_sec",0)

    score = 0
    wrong = []
    topic_stats = {}

    for i, q in enumerate(questions):
        topic = q.get("topic","General")
        topic_stats.setdefault(topic, {"correct":0,"total":0})
        topic_stats[topic]["total"] += 1

        user_ans = user_answers.get(str(i))
        correct  = False
        qtype    = q.get("type")

        if qtype == "mcq":
            correct = user_ans is not None and int(user_ans) == int(q.get("correct_index",-1))
        elif qtype == "true_false":
            correct = str(user_ans).lower() == str(q.get("correct_answer")).lower()
        elif qtype == "fill_blank":
            correct = str(user_ans or "").strip().lower() == str(q.get("correct_answer","")).strip().lower()
        elif qtype == "match":
            correct_pairs = {p["left"]:p["right"] for p in q.get("pairs",[])}
            user_pairs = user_ans or {}
            correct = all(user_pairs.get(l)==r for l,r in correct_pairs.items()) and len(user_pairs)==len(correct_pairs)
        elif qtype == "short_answer":
            correct = str(user_ans or "").strip().lower() != ""

        if correct:
            score += 1
            topic_stats[topic]["correct"] += 1
        else:
            wrong.append({"index":i,"question":q.get("question"),"topic":topic,
                          "your_answer":user_ans,"explanation":q.get("explanation","")})

    attempt_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO attempts (id,user_id,quiz_set_id,pdf_id,score,total,time_taken_sec,answers_json,wrong_questions_json,topic_breakdown_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (attempt_id, uid, quiz_id, quiz["pdf_id"], score, len(questions), time_taken,
         json.dumps(user_answers), json.dumps(wrong), json.dumps(topic_stats), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    xp_gained = score*10 + (5 if score==len(questions) else 0)
    new_xp, new_level = add_xp(xp_gained)
    touch_streak()
    check_badges()

    return jsonify({"attempt_id":attempt_id,"score":score,"total":len(questions),
                    "xp_gained":xp_gained,"new_xp":new_xp,"new_level":new_level,
                    "redirect":url_for("quiz_result", attempt_id=attempt_id)})


@app.route("/result/<attempt_id>")
@login_required
def quiz_result(attempt_id):
    uid = current_user_id()
    conn = get_db()
    attempt = conn.execute("SELECT * FROM attempts WHERE id=? AND user_id=?", (attempt_id, uid)).fetchone()
    if not attempt:
        conn.close()
        return redirect(url_for("library"))
    quiz = conn.execute("SELECT * FROM quiz_sets WHERE id=?", (attempt["quiz_set_id"],)).fetchone()
    conn.close()

    questions      = json.loads(quiz["questions_json"])
    wrong          = json.loads(attempt["wrong_questions_json"] or "[]")
    topic_breakdown = json.loads(attempt["topic_breakdown_json"] or "{}")
    return render_template("quiz_result.html", attempt=attempt, quiz=quiz, questions=questions,
                           wrong=wrong, topic_breakdown=topic_breakdown, active="library")


@app.route("/revision/<pdf_id>")
@login_required
def revision_mode(pdf_id):
    uid = current_user_id()
    conn = get_db()
    pdf      = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    attempts = conn.execute("SELECT * FROM attempts WHERE pdf_id=? AND user_id=? ORDER BY created_at DESC", (pdf_id, uid)).fetchall()
    conn.close()

    weak_topics     = {}
    wrong_questions = []
    for a in attempts:
        breakdown = json.loads(a["topic_breakdown_json"] or "{}")
        for topic, stats in breakdown.items():
            weak_topics.setdefault(topic, {"correct":0,"total":0})
            weak_topics[topic]["correct"] += stats["correct"]
            weak_topics[topic]["total"]   += stats["total"]
        wrong_questions.extend(json.loads(a["wrong_questions_json"] or "[]"))

    weak_sorted = sorted(
        [{"topic":t,**s,"accuracy":round(100*s["correct"]/s["total"]) if s["total"] else 0} for t,s in weak_topics.items()],
        key=lambda x: x["accuracy"],
    )
    return render_template("revision.html", pdf=pdf, weak_topics=weak_sorted,
                           wrong_questions=wrong_questions[:30], active="library")


@app.route("/revision/<pdf_id>/generate", methods=["POST"])
@login_required
def generate_revision_quiz(pdf_id):
    uid = current_user_id()
    conn = get_db()
    pdf      = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    attempts = conn.execute("SELECT * FROM attempts WHERE pdf_id=? AND user_id=?", (pdf_id, uid)).fetchall()
    conn.close()
    if not pdf:
        return redirect(url_for("library"))

    wrong_qs    = []
    weak_topics = set()
    for a in attempts:
        wrong_qs.extend(json.loads(a["wrong_questions_json"] or "[]"))
        breakdown = json.loads(a["topic_breakdown_json"] or "{}")
        for t,s in breakdown.items():
            if s["total"] and s["correct"]/s["total"] < 0.6:
                weak_topics.add(t)

    focus      = ", ".join(list(weak_topics)[:8]) or "the most important topics"
    wrong_text = "\n".join(f"- {q.get('question','')}" for q in wrong_qs[:15])

    prompt = f"""Based on this study material, generate 8 revision questions focused on: {focus}.
{"Previous wrong questions (cover similar concepts):\\n"+wrong_text if wrong_text else ""}

Return ONLY a valid JSON array of question objects, mixing types "mcq" and "short_answer".

MATERIAL:
{pdf['text_content'][:14000]}"""

    try:
        raw       = call_ai(prompt, system="You are an expert tutor creating targeted revision quizzes. Respond with ONLY a valid JSON array.")
        questions = extract_json(raw)
        if isinstance(questions, dict):
            questions = questions.get("questions",[])
    except AIError as e:
        return redirect(url_for("revision_mode", pdf_id=pdf_id, error=str(e)))

    quiz_id = str(uuid.uuid4())
    conn = get_db()
    conn.execute(
        "INSERT INTO quiz_sets (id,user_id,pdf_id,title,difficulty,q_types,questions_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (quiz_id, uid, pdf_id, f"{pdf['title']} — Revision Quiz", "revision",
         json.dumps(["mcq","short_answer"]), json.dumps(questions), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("take_quiz", quiz_id=quiz_id))


# ---- Flashcards ----

@app.route("/flashcards")
@app.route("/flashcards/<pdf_id>")
@login_required
def flashcards_page(pdf_id=None):
    uid  = current_user_id()
    conn = get_db()
    pdfs = conn.execute("SELECT * FROM pdfs WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
    if pdf_id:
        cards = conn.execute("SELECT * FROM flashcards WHERE pdf_id=? AND user_id=? ORDER BY created_at", (pdf_id, uid)).fetchall()
        pdf   = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    else:
        cards = conn.execute("SELECT * FROM flashcards WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
        pdf   = None
    conn.close()
    return render_template("flashcards.html", pdfs=pdfs, cards=[dict(c) for c in cards], pdf=pdf, active="flashcards")


@app.route("/flashcards/<pdf_id>/generate", methods=["POST"])
@login_required
def generate_flashcards(pdf_id):
    uid  = current_user_id()
    conn = get_db()
    pdf  = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    conn.close()
    if not pdf:
        return jsonify({"error":"PDF not found"}), 404

    count  = int(request.json.get("count",12)) if request.is_json else 12
    prompt = f"""Create {count} flashcards from this study material. Return ONLY a valid JSON array of objects:
{{"front": "question or term", "back": "concise answer/definition (1-2 sentences)", "topic": "short topic tag"}}.

MATERIAL:
{pdf['text_content'][:16000]}"""

    try:
        raw   = call_ai(prompt, system="You create concise, high-quality flashcards. Respond with ONLY a valid JSON array.")
        cards = extract_json(raw)
        if isinstance(cards, dict):
            cards = cards.get("flashcards",[])
    except AIError as e:
        return jsonify({"error":str(e)}), 400

    conn = get_db()
    for card in cards:
        conn.execute(
            "INSERT INTO flashcards (id,user_id,pdf_id,front,back,topic,created_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), uid, pdf_id, card.get("front",""), card.get("back",""),
             card.get("topic","General"), datetime.now().isoformat()),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok":True,"count":len(cards)})


@app.route("/flashcards/<card_id>/toggle", methods=["POST"])
@login_required
def toggle_flashcard(card_id):
    uid   = current_user_id()
    field = request.json.get("field")
    if field not in ("learned","bookmarked"):
        return jsonify({"error":"invalid field"}), 400
    conn    = get_db()
    current = conn.execute(f"SELECT {field} FROM flashcards WHERE id=? AND user_id=?", (card_id, uid)).fetchone()
    if not current:
        conn.close()
        return jsonify({"error":"not found"}), 404
    new_val = 0 if current[field] else 1
    conn.execute(f"UPDATE flashcards SET {field}=? WHERE id=? AND user_id=?", (new_val, card_id, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok":True,"value":new_val})


# ---- AI Tutor ----

@app.route("/tutor")
@app.route("/tutor/<pdf_id>")
@login_required
def tutor_page(pdf_id=None):
    uid  = current_user_id()
    conn = get_db()
    pdfs    = conn.execute("SELECT * FROM pdfs WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
    history = []
    pdf     = None
    if pdf_id:
        pdf     = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
        history = conn.execute("SELECT * FROM chat_history WHERE pdf_id=? AND user_id=? ORDER BY created_at", (pdf_id, uid)).fetchall()
    conn.close()
    return render_template("tutor.html", pdfs=pdfs, pdf=pdf, history=history, active="tutor")


def chunk_text(text, chunk_size=1200, overlap=150):
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i:i+chunk_size])
        i += chunk_size - overlap
    return chunks


def retrieve_relevant_chunks(text, query, top_k=4):
    chunks      = chunk_text(text)
    query_words = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 2}
    scored      = []
    for chunk in chunks:
        chunk_words = re.findall(r"\w+", chunk.lower())
        score       = sum(1 for w in chunk_words if w in query_words)
        scored.append((score, chunk))
    scored.sort(key=lambda x: -x[0])
    top = [c for s,c in scored[:top_k] if s > 0]
    if not top:
        top = [c for _,c in scored[:top_k]]
    return "\n\n---\n\n".join(top)


@app.route("/tutor/<pdf_id>/ask", methods=["POST"])
@login_required
def tutor_ask(pdf_id):
    uid  = current_user_id()
    conn = get_db()
    pdf  = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    if not pdf:
        conn.close()
        return jsonify({"error":"PDF not found"}), 404

    question = request.json.get("question","").strip()
    if not question:
        conn.close()
        return jsonify({"error":"Empty question"}), 400

    context = retrieve_relevant_chunks(pdf["text_content"], question, top_k=5)
    prompt  = f"""You are an AI tutor. Answer the student's question using ONLY the context excerpts below from their document "{pdf['title']}". If the answer isn't in the context, say so clearly.

CONTEXT EXCERPTS:
{context}

STUDENT QUESTION: {question}

Give a clear, well-structured answer (use short paragraphs or bullet points where helpful)."""

    try:
        answer = call_ai(prompt, system="You are a patient, precise AI tutor who only answers from provided context.", max_tokens=1500)
    except AIError as e:
        conn.close()
        return jsonify({"error":str(e)}), 400

    now = datetime.now().isoformat()
    conn.execute("INSERT INTO chat_history (id,user_id,pdf_id,role,content,created_at) VALUES (?,?,?,?,?,?)",
                 (str(uuid.uuid4()), uid, pdf_id, "user", question, now))
    conn.execute("INSERT INTO chat_history (id,user_id,pdf_id,role,content,created_at) VALUES (?,?,?,?,?,?)",
                 (str(uuid.uuid4()), uid, pdf_id, "assistant", answer, now))
    conn.commit()
    conn.close()
    return jsonify({"answer":answer})


# ---- Smart Notes ----

@app.route("/notes/<pdf_id>")
@login_required
def notes_page(pdf_id):
    uid  = current_user_id()
    conn = get_db()
    pdf   = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    notes = conn.execute("SELECT * FROM notes WHERE pdf_id=? AND user_id=? ORDER BY created_at DESC", (pdf_id, uid)).fetchall()
    conn.close()
    if not pdf:
        return redirect(url_for("library"))
    return render_template("notes.html", pdf=pdf, notes=notes, active="notes")


@app.route("/notes/<pdf_id>/generate", methods=["POST"])
@login_required
def generate_notes(pdf_id):
    uid  = current_user_id()
    conn = get_db()
    pdf  = conn.execute("SELECT * FROM pdfs WHERE id=? AND user_id=?", (pdf_id, uid)).fetchone()
    conn.close()
    if not pdf:
        return jsonify({"error":"PDF not found"}), 404

    note_type    = request.json.get("note_type","summary")
    instructions = {
        "summary":    "Write a clear one-page summary covering the main ideas in flowing paragraphs.",
        "bullets":    "Write organized bullet-point notes grouped under topic headings (use markdown ## headings and - bullets).",
        "exam":       "Write exam revision notes: key facts, definitions, and likely exam points, organized by topic with markdown headings.",
        "cheatsheet": "Write an ultra-condensed last-minute cheat sheet: only the most critical facts, formulas, and terms, as terse markdown bullets grouped by topic.",
    }
    instruction = instructions.get(note_type, instructions["summary"])
    prompt = f"""{instruction}

Base this entirely on the material below. Use markdown formatting where helpful.

MATERIAL:
{pdf['text_content'][:18000]}"""

    try:
        content = call_ai(prompt, system="You are an expert note-taker who creates clear, well-organized study notes.", max_tokens=3000)
    except AIError as e:
        return jsonify({"error":str(e)}), 400

    note_id = str(uuid.uuid4())
    conn = get_db()
    conn.execute("INSERT INTO notes (id,user_id,pdf_id,note_type,content,created_at) VALUES (?,?,?,?,?,?)",
                 (note_id, uid, pdf_id, note_type, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return jsonify({"ok":True,"note_id":note_id,"content":content})


# ---- Progress ----

@app.route("/progress")
@login_required
def progress_page():
    uid  = current_user_id()
    conn = get_db()
    attempts = conn.execute("SELECT * FROM attempts WHERE user_id=? ORDER BY created_at", (uid,)).fetchall()
    pdfs     = conn.execute("SELECT * FROM pdfs WHERE user_id=?", (uid,)).fetchall()
    cards    = conn.execute("SELECT * FROM flashcards WHERE user_id=?", (uid,)).fetchall()
    conn.close()

    state  = get_user_state()
    badges = json.loads(state["badges_json"] or "[]")
    badge_details = [{"id":b, **BADGE_INFO.get(b,{"name":b,"desc":"","icon":"award"})} for b in badges]

    topic_agg = {}
    for a in attempts:
        breakdown = json.loads(a["topic_breakdown_json"] or "{}")
        for t,s in breakdown.items():
            topic_agg.setdefault(t, {"correct":0,"total":0})
            topic_agg[t]["correct"] += s["correct"]
            topic_agg[t]["total"]   += s["total"]

    topic_list    = sorted(
        [{"topic":t,**s,"accuracy":round(100*s["correct"]/s["total"]) if s["total"] else 0} for t,s in topic_agg.items()],
        key=lambda x: -x["accuracy"],
    )
    strong_topics = [t for t in topic_list if t["accuracy"] >= 70][:5]
    weak_topics   = [t for t in topic_list if t["accuracy"] < 70][:5]
    score_trend   = [{"date":a["created_at"][:10],"pct":round(100*a["score"]/a["total"]) if a["total"] else 0} for a in attempts[-15:]]

    next_level_xp = xp_for_level(state["level"])
    prev_level_xp = xp_for_level(state["level"]-1) if state["level"] > 1 else 0

    return render_template("progress.html", state=state, badges=badge_details, all_badge_info=BADGE_INFO,
                           total_pdfs=len(pdfs), total_attempts=len(attempts), total_cards=len(cards),
                           learned_cards=sum(1 for c in cards if c["learned"]),
                           strong_topics=strong_topics, weak_topics=weak_topics, score_trend=score_trend,
                           next_level_xp=next_level_xp, prev_level_xp=prev_level_xp, active="progress")


# ---- Settings ----

@app.route("/settings", methods=["GET","POST"])
@login_required
def settings_page():
    uid = current_user_id()
    if request.method == "POST":
        provider = request.form.get("api_provider","")
        api_key  = request.form.get("api_key","")
        conn = get_db()
        conn.execute("UPDATE user_state SET api_provider=?,api_key=? WHERE id=?", (provider, api_key, uid))
        conn.commit()
        conn.close()
        return redirect(url_for("settings_page", saved="1"))

    state = get_user_state()
    return render_template("settings.html", state=state, active="settings", saved=request.args.get("saved"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=True)
