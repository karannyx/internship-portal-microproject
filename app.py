import sqlite3
import requests
import time
import csv
import os
import json
import hashlib
import smtplib
from datetime import datetime, timedelta
from urllib.parse import urlencode
from io import StringIO
from collections import Counter
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response

app = Flask(__name__)
app.secret_key = 'skill2career_production_secret_key_2026'
DB_NAME = 'database.db'

ADMIN_PASSKEY = "admin2026"
ADMIN_TIMEOUT_SECONDS = 300  # 5 minutes inactivity auto-lock

# Email Configuration (Set via Render Environment Variables if available)
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "placement.cell.mits@gmail.com")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")  # App Password

LAST_SYNC_TIME = 0
SYNC_INTERVAL = 300

LIVE_PORTAL_CACHE = {"timestamp": 0, "jobs": []}
LIVE_PORTAL_TTL = 300  # Cache external API jobs for 5 minutes for fast loading


@app.before_request
def auto_lock_admin_session():
    """Automatically locks T&P Admin session after 5 minutes of inactivity."""
    if session.get('is_admin'):
        now = time.time()
        last_active = session.get('admin_last_active', now)
        if now - last_active > ADMIN_TIMEOUT_SECONDS:
            session.pop('is_admin', None)
            session.pop('admin_last_active', None)
            if request.endpoint and (request.endpoint.startswith('admin_') or request.endpoint == 'add_job'):
                return redirect(url_for('admin_panel'))
        else:
            session['admin_last_active'] = now


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def format_interview_slot(date_str, time_str):
    """Formats YYYY-MM-DD and HH:MM into clean display strings."""
    formatted_date = date_str or ""
    formatted_time = time_str or ""
    if date_str:
        try:
            formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%b %d, %Y")
        except ValueError:
            pass
    if time_str:
        try:
            formatted_time = datetime.strptime(time_str, "%H:%M").strftime("%I:%M %p")
        except ValueError:
            pass
    return formatted_date, formatted_time


def build_gcal_link(job_title, company, date_str, time_str, venue):
    """Builds a 1-click Google Calendar event URL for scheduled interviews."""
    if not date_str:
        return ""
    try:
        t_str = time_str if time_str else "10:00"
        start_dt = datetime.strptime(f"{date_str} {t_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(hours=1)
        dates_param = f"{start_dt.strftime('%Y%m%dT%H%M%S')}/{end_dt.strftime('%Y%m%dT%H%M%S')}"
        params = {
            "action": "TEMPLATE",
            "text": f"Interview: {job_title} ({company})",
            "dates": dates_param,
            "details": f"Campus Recruitment Interview for {job_title} at {company} via Skill2Career Portal (MITS Gwalior).",
            "location": venue or "MITS Gwalior / Online"
        }
        return f"https://calendar.google.com/calendar/render?{urlencode(params)}"
    except Exception:
        return ""


def get_company_logo(company_name, api_logo=None):
    """Returns official high-res company/portal logo URL with domain matching."""
    if api_logo:
        return api_logo
    name = (company_name or "").lower()
    domain_map = {
        'naukri': 'naukri.com',
        'indeed': 'indeed.com',
        'linkedin': 'linkedin.com',
        'internshala': 'internshala.com',
        'aicte': 'aicte-india.org',
        'unstop': 'unstop.com',
        'wellfound': 'wellfound.com',
        'drdo': 'drdo.gov.in',
        'india post': 'indiapost.gov.in',
        'cept': 'indiapost.gov.in',
        'tcs': 'tcs.com',
        'tata': 'tcs.com',
        'infosys': 'infosys.com',
        'zoho': 'zoho.com',
        'razorpay': 'razorpay.com',
        'isro': 'isro.gov.in',
        'google': 'google.com',
        'microsoft': 'microsoft.com',
        'amazon': 'amazon.com',
        'wipro': 'wipro.com',
        'hcl': 'hcltech.com',
        'accenture': 'accenture.com',
        'flipkart': 'flipkart.com',
        'paytm': 'paytm.com'
    }
    for key, domain in domain_map.items():
        if key in name:
            return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    return ""


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            skills TEXT NOT NULL,
            avatar_url TEXT DEFAULT ''
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS internships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            required_skills TEXT NOT NULL,
            deadline TEXT DEFAULT 'Nov 25, 2026',
            custom_quiz TEXT DEFAULT '[]'
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_email TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            status TEXT DEFAULT 'Under Review',
            quiz_score TEXT DEFAULT 'N/A',
            interview_date TEXT DEFAULT '',
            interview_time TEXT DEFAULT '',
            interview_venue TEXT DEFAULT ''
        )
    ''')

    # Schema Migrations for Existing Cloud Databases
    cursor.execute("PRAGMA table_info(students)")
    s_cols = [col[1] for col in cursor.fetchall()]
    if 'avatar_url' not in s_cols:
        cursor.execute("ALTER TABLE students ADD COLUMN avatar_url TEXT DEFAULT ''")

    cursor.execute("PRAGMA table_info(internships)")
    i_cols = [col[1] for col in cursor.fetchall()]
    if 'deadline' not in i_cols:
        cursor.execute("ALTER TABLE internships ADD COLUMN deadline TEXT DEFAULT 'Nov 25, 2026'")
    if 'custom_quiz' not in i_cols:
        cursor.execute("ALTER TABLE internships ADD COLUMN custom_quiz TEXT DEFAULT '[]'")

    cursor.execute("PRAGMA table_info(applications)")
    a_cols = [col[1] for col in cursor.fetchall()]
    if 'status' not in a_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN status TEXT DEFAULT 'Under Review'")
    if 'quiz_score' not in a_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN quiz_score TEXT DEFAULT 'N/A'")
    if 'interview_date' not in a_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN interview_date TEXT DEFAULT ''")
    if 'interview_time' not in a_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN interview_time TEXT DEFAULT ''")
    if 'interview_venue' not in a_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN interview_venue TEXT DEFAULT ''")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS saved_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_email TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            UNIQUE(student_email, job_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS student_resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_email TEXT UNIQUE NOT NULL,
            phone TEXT,
            location TEXT,
            linkedin TEXT,
            github TEXT,
            college TEXT,
            degree TEXT,
            branch TEXT,
            cgpa TEXT,
            grad_year TEXT,
            project1_title TEXT,
            project1_tech TEXT,
            project1_desc TEXT,
            project2_title TEXT,
            project2_tech TEXT,
            project2_desc TEXT,
            certifications TEXT,
            summary TEXT
        )
    ''')

    cursor.execute('SELECT COUNT(*) FROM internships')
    if cursor.fetchone()[0] == 0:
        openings = [
            ("Junior Software Trainee", "India Post (CEPT)", "Python, SQL, HTML", "Nov 22, 2026", "[]"),
            ("Digital Banking Systems Trainee", "India Post Tech Cell", "Python, HTML, C", "Nov 26, 2026", "[]"),
            ("Software Engineer Intern", "TCS Digital", "Python, SQL, C", "Nov 20, 2026", "[]"),
            ("Data Analyst Intern", "Infosys Springboard", "Python, SQL", "Nov 30, 2026", "[]")
        ]
        cursor.executemany(
            'INSERT INTO internships (title, company, required_skills, deadline, custom_quiz) VALUES (?, ?, ?, ?, ?)',
            openings
        )

    conn.commit()
    conn.close()


init_db()


# --- AUTOMATED EMAIL DISPATCHER ---

def dispatch_notification_email(student_email, student_name, job_title, company, status, i_date="", i_time="", i_venue=""):
    subject = f"Placement Cell Notice: Application Status Updated for {job_title}"
    f_date, f_time = format_interview_slot(i_date, i_time)

    slot_block = ""
    if status == "Interview Scheduled" and (f_date or f_time or i_venue):
        slot_block = f"""
--- INTERVIEW SCHEDULE DETAILS ---
Date: {f_date or 'To be announced'}
Time: {f_time or 'To be announced'}
Venue / Link: {i_venue or 'MITS T&P Cell'}
----------------------------------
"""

    body = f"""
Dear {student_name},

Greetings from the Training & Placement Cell, MITS Gwalior.

Your application status for the following campus opening has been updated:
Role: {job_title}
Organization: {company}
Updated Status: {status}
{slot_block}
{'Congratulations! Your official letter is now available on your portal dashboard.' if status in ['Shortlisted', 'Interview Scheduled', 'Selected'] else 'Login to Skill2Career to review role analytics and recommendations.'}

Portal Access: https://skill2career-portal.onrender.com/dashboard

Regards,
Training & Placement Cell
Madhav Institute of Technology & Science, Gwalior
"""
    if not SENDER_PASSWORD:
        print(f"\n[MOCK EMAIL DISPATCHED] To: {student_email} | Subject: {subject} | Status: {status} {slot_block}\n")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = f"MITS Placement Cell <{SENDER_EMAIL}>"
        msg['To'] = student_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, student_email, msg.as_string())
        server.quit()
        print(f"[LIVE EMAIL SENT] Successfully notified {student_email}")
    except Exception as e:
        print(f"[SMTP WARNING] Could not send live email: {e}")


# --- AUTO SYNC ENGINE ---

def sync_live_jobs_automatically():
    global LAST_SYNC_TIME
    current_time = time.time()
    if current_time - LAST_SYNC_TIME < SYNC_INTERVAL:
        return

    LAST_SYNC_TIME = current_time
    try:
        response = requests.get('https://remotive.com/api/remote-jobs?category=software-dev&limit=10', timeout=3.5)
        if response.status_code == 200:
            data = response.json()
            jobs = data.get('jobs', [])
            conn = get_db()
            cursor = conn.cursor()

            for job in jobs:
                title = job.get('title', '').strip()
                company = job.get('company_name', '').strip()
                tags = job.get('tags', [])
                skills = ", ".join(tags[:4]) if tags else "Python, HTML, SQL"

                if title and company:
                    cursor.execute('SELECT id FROM internships WHERE title = ? AND company = ?', (title, company))
                    if not cursor.fetchone():
                        cursor.execute(
                            'INSERT INTO internships (title, company, required_skills, deadline, custom_quiz) VALUES (?, ?, ?, ?, ?)',
                            (title, company, skills, 'Rolling Basis', '[]')
                        )

            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Auto-sync note: {e}")


# --- ROUTES ---

@app.route('/')
def home():
    return redirect(url_for('live_jobs'))


@app.route('/jobs')
def jobs():
    sync_live_jobs_automatically()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, title, company, required_skills, deadline, custom_quiz FROM internships ORDER BY id DESC')
    raw_internships = cursor.fetchall()

    student_skills = []
    applied_job_ids = set()
    saved_job_ids = set()

    if 'student_email' in session:
        cursor.execute('SELECT skills FROM students WHERE email = ?', (session['student_email'],))
        row = cursor.fetchone()
        if row and row['skills']:
            student_skills = [s.strip().lower() for s in row['skills'].split(',') if s.strip()]

        cursor.execute('SELECT job_id FROM applications WHERE student_email = ?', (session['student_email'],))
        applied_job_ids = {r['job_id'] for r in cursor.fetchall()}

        cursor.execute('SELECT job_id FROM saved_jobs WHERE student_email = ?', (session['student_email'],))
        saved_job_ids = {r['job_id'] for r in cursor.fetchall()}

    internships = []
    top_score = 0
    for job in raw_internships:
        job_dict = dict(job)
        job_req = [s.strip() for s in job['required_skills'].split(',') if s.strip()]

        job_dict['is_applied'] = job['id'] in applied_job_ids
        job_dict['is_saved'] = job['id'] in saved_job_ids
        job_dict['logo'] = get_company_logo(job['company'])

        try:
            cq = json.loads(job['custom_quiz'] or '[]')
            job_dict['has_custom_quiz'] = len(cq) > 0
            job_dict['custom_quiz_count'] = len(cq)
        except Exception:
            job_dict['has_custom_quiz'] = False
            job_dict['custom_quiz_count'] = 0

        if student_skills and job_req:
            matched_items = [orig for orig in job_req if orig.lower() in student_skills]
            missing_items = [orig for orig in job_req if orig.lower() not in student_skills]
            score = round((len(matched_items) / len(job_req)) * 100)
            if score > top_score:
                top_score = score
            job_dict['match_score'] = score
            job_dict['matched_skills'] = matched_items
            job_dict['missing_skills'] = missing_items
        else:
            job_dict['match_score'] = None
            job_dict['matched_skills'] = []
            job_dict['missing_skills'] = job_req

        internships.append(job_dict)

    announcements = [
        "T&P Drive: TCS Digital applications closing this Friday at 5:00 PM.",
        "Notice: India Post (CEPT) technical screening assessments are now live.",
        "Pro-Tip: Complete Harvard CS50 in the Courses section to boost your Software Trainee match score to 100%."
    ]

    conn.close()
    return render_template('jobs.html', internships=internships, top_score=top_score, total_jobs=len(internships), announcements=announcements)


# --- EXPANDED SCREENING QUIZ BANK BEFORE APPLYING ---

QUIZ_QUESTION_BANK = {
    'python': [
        {"q": "What is the output of print(type([]) is list)?", "opts": ["True", "False", "TypeError", "None"], "ans": "True"},
        {"q": "Which data structure is immutable in Python?", "opts": ["Tuple", "List", "Dictionary", "Set"], "ans": "Tuple"},
        {"q": "What is the time complexity of looking up a key in a Python dictionary on average?", "opts": ["O(1)", "O(n)", "O(log n)", "O(n log n)"], "ans": "O(1)"},
        {"q": "Which keyword is used to create a generator function in Python?", "opts": ["yield", "return", "async", "lambda"], "ans": "yield"},
        {"q": "What does PEP 8 define in Python?", "opts": ["Code style and formatting guidelines", "Package installation protocol", "Memory garbage collection rules", "Database connector API"], "ans": "Code style and formatting guidelines"},
        {"q": "What will len({1, 2, 2, 3, 3, 3}) return in Python?", "opts": ["3", "6", "5", "TypeError"], "ans": "3"}
    ],
    'sql': [
        {"q": "Which clause is used to filter aggregated groups in SQL?", "opts": ["HAVING", "WHERE", "GROUP BY", "ORDER BY"], "ans": "HAVING"},
        {"q": "Which constraint enforces uniqueness and non-null values across rows?", "opts": ["PRIMARY KEY", "FOREIGN KEY", "DEFAULT", "CHECK"], "ans": "PRIMARY KEY"},
        {"q": "Which SQL JOIN returns all records from the left table and matched records from the right table?", "opts": ["LEFT JOIN", "INNER JOIN", "RIGHT JOIN", "CROSS JOIN"], "ans": "LEFT JOIN"},
        {"q": "Which SQL command is used to remove all rows from a table without logging individual row deletions?", "opts": ["TRUNCATE", "DELETE", "DROP", "REMOVE"], "ans": "TRUNCATE"},
        {"q": "What does ACID stand for in relational databases?", "opts": ["Atomicity, Consistency, Isolation, Durability", "Accuracy, Concurrency, Integrity, Data", "Access, Control, Indexing, Delivery", "Asynchronous, Cached, Indexed, Distributed"], "ans": "Atomicity, Consistency, Isolation, Durability"}
    ],
    'c': [
        {"q": "Which operator returns the memory address of a variable in C?", "opts": ["& (Address-of)", "* (Dereference)", "->", "sizeof"], "ans": "& (Address-of)"},
        {"q": "In C, strings terminate with which character?", "opts": ["\\0 (Null)", "\\n", "EOF", "\\t"], "ans": "\\0 (Null)"},
        {"q": "Which standard library function dynamically allocates memory and initializes all bytes to zero?", "opts": ["calloc()", "malloc()", "realloc()", "free()"], "ans": "calloc()"},
        {"q": "What is the default storage class for local variables declared inside a C function?", "opts": ["auto", "static", "extern", "register"], "ans": "auto"},
        {"q": "Which header file is required for printf() and scanf() in C?", "opts": ["<stdio.h>", "<stdlib.h>", "<conio.h>", "<string.h>"], "ans": "<stdio.h>"}
    ],
    'html': [
        {"q": "Which HTML5 tag is semantic for site navigation links?", "opts": ["<nav>", "<menu>", "<header>", "<section>"], "ans": "<nav>"},
        {"q": "Which tag embeds a responsive bitmap drawing surface for Chart.js?", "opts": ["<canvas>", "<svg>", "<paint>", "<graphic>"], "ans": "<canvas>"},
        {"q": "Which attribute specifies alternative text when an image fails to load?", "opts": ["alt", "title", "src", "aria-label"], "ans": "alt"},
        {"q": "Which CSS property controls the space inside an element's border?", "opts": ["padding", "margin", "gap", "outline"], "ans": "padding"},
        {"q": "Which HTTP status code indicates a resource was successfully created?", "opts": ["201 Created", "200 OK", "204 No Content", "302 Found"], "ans": "201 Created"}
    ],
    'javascript': [
        {"q": "Which keyword declares a block-scoped variable that cannot be reassigned?", "opts": ["const", "var", "let", "static"], "ans": "const"},
        {"q": "What does '===' check in JavaScript?", "opts": ["Both value and data type", "Only value with type coercion", "Reference memory address only", "Null safety"], "ans": "Both value and data type"},
        {"q": "Which method converts a JSON string into a JavaScript object?", "opts": ["JSON.parse()", "JSON.stringify()", "JSON.object()", "JSON.decode()"], "ans": "JSON.parse()"},
        {"q": "What does async/await work on top of in JavaScript?", "opts": ["Promises", "Callbacks only", "Web Workers", "Event Bubbling"], "ans": "Promises"}
    ],
    'react': [
        {"q": "Which React hook is used to manage local component state?", "opts": ["useState", "useEffect", "useRef", "useMemo"], "ans": "useState"},
        {"q": "What does JSX stand for in React?", "opts": ["JavaScript XML", "Java Syntax Extension", "JSON XML", "JavaScript XHR"], "ans": "JavaScript XML"},
        {"q": "Which hook runs side effects like API calls after rendering in React?", "opts": ["useEffect", "useState", "useContext", "useReducer"], "ans": "useEffect"},
        {"q": "Why should each item in a rendered React list have a unique 'key' prop?", "opts": ["To help React efficiently track and update changed DOM elements", "To encrypt component props", "To apply CSS grid ordering", "To prevent API rate limits"], "ans": "To help React efficiently track and update changed DOM elements"}
    ],
    'java': [
        {"q": "Which keyword prevents a class from being inherited in Java?", "opts": ["final", "static", "abstract", "private"], "ans": "final"},
        {"q": "Which Java collection does NOT allow duplicate elements?", "opts": ["HashSet", "ArrayList", "LinkedList", "Vector"], "ans": "HashSet"},
        {"q": "Which component executes Java bytecode on any platform?", "opts": ["JVM (Java Virtual Machine)", "JDK Compiler", "JAR Bundler", "JDBC Driver"], "ans": "JVM (Java Virtual Machine)"},
        {"q": "What is method overloading in Java?", "opts": ["Multiple methods in the same class with the same name but different parameters", "Overriding a parent method in a subclass", "Hiding private fields", "Running two threads simultaneously"], "ans": "Multiple methods in the same class with the same name but different parameters"}
    ],
    'dsa': [
        {"q": "What is the worst-case time complexity of Binary Search on a sorted array?", "opts": ["O(log n)", "O(n)", "O(1)", "O(n log n)"], "ans": "O(log n)"},
        {"q": "Which data structure follows the FIFO (First-In, First-Out) principle?", "opts": ["Queue", "Stack", "Binary Heap", "Hash Table"], "ans": "Queue"},
        {"q": "Which traversal of a Binary Search Tree (BST) yields elements in sorted ascending order?", "opts": ["Inorder", "Preorder", "Postorder", "Level-order"], "ans": "Inorder"},
        {"q": "Which algorithm is used to find the shortest path in a weighted graph with non-negative edges?", "opts": ["Dijkstra's Algorithm", "Kruskal's Algorithm", "DFS", "Topological Sort"], "ans": "Dijkstra's Algorithm"}
    ],
    'cloud': [
        {"q": "Which Git command creates and switches to a new branch in one step?", "opts": ["git checkout -b <branch>", "git branch -d <branch>", "git merge <branch>", "git stash"], "ans": "git checkout -b <branch>"},
        {"q": "What is a Docker container?", "opts": ["A lightweight standalone package containing app code and dependencies", "A physical bare-metal server", "A relational database engine", "A DNS load balancer"], "ans": "A lightweight standalone package containing app code and dependencies"},
        {"q": "Which HTTP method is idempotent and typically used to update an entire resource in REST APIs?", "opts": ["PUT", "POST", "PATCH", "CONNECT"], "ans": "PUT"}
    ],
    'general': [
        {"q": "Which data structure follows LIFO (Last-In, First-Out) order?", "opts": ["Stack", "Queue", "Linked List", "Graph"], "ans": "Stack"},
        {"q": "What is the primary purpose of HTTPS over HTTP?", "opts": ["Encrypts communication using TLS/SSL", "Increases image resolution", "Compresses SQL queries", "Bypasses DNS lookup"], "ans": "Encrypts communication using TLS/SSL"},
        {"q": "Which Git command uploads local committed changes to a remote repository?", "opts": ["git push", "git pull", "git clone", "git init"], "ans": "git push"},
        {"q": "What is the time complexity of Merge Sort in the worst case?", "opts": ["O(n log n)", "O(n^2)", "O(n)", "O(log n)"], "ans": "O(n log n)"},
        {"q": "In OOP, bundling data and methods operating on that data within a single unit is called:", "opts": ["Encapsulation", "Polymorphism", "Abstraction", "Inheritance"], "ans": "Encapsulation"}
    ]
}


@app.route('/apply/<int:job_id>')
def apply(job_id):
    if 'student_email' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM applications WHERE student_email = ? AND job_id = ?', (session['student_email'], job_id))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        return redirect(url_for('dashboard'))

    cursor.execute('SELECT title, company, required_skills, custom_quiz FROM internships WHERE id = ?', (job_id,))
    job = cursor.fetchone()
    conn.close()

    if not job:
        return redirect(url_for('jobs'))

    custom_questions = []
    try:
        parsed = json.loads(job['custom_quiz'] or '[]')
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and item.get('q') and item.get('opts') and item.get('ans'):
                    custom_questions.append(item)
    except Exception:
        custom_questions = []

    if custom_questions:
        selected_questions = custom_questions
    else:
        req_skills = [s.strip().lower() for s in job['required_skills'].split(',') if s.strip()]
        selected_questions = []

        skill_aliases = {
            'js': 'javascript',
            'node': 'javascript',
            'flask': 'python',
            'django': 'python',
            'mysql': 'sql',
            'postgres': 'sql',
            'sqlite': 'sql',
            'css': 'html',
            'web': 'html',
            'data structure': 'dsa',
            'algorithm': 'dsa',
            'docker': 'cloud',
            'aws': 'cloud',
            'git': 'cloud'
        }

        matched_keys = []
        for sk in req_skills:
            for bank_key in QUIZ_QUESTION_BANK:
                if bank_key != 'general' and bank_key in sk and bank_key not in matched_keys:
                    matched_keys.append(bank_key)
            for alias, target_key in skill_aliases.items():
                if alias in sk and target_key not in matched_keys:
                    matched_keys.append(target_key)

        idx = 0
        while len(selected_questions) < 5 and matched_keys:
            added_in_round = False
            for key in matched_keys:
                bank_list = QUIZ_QUESTION_BANK.get(key, [])
                if idx < len(bank_list):
                    q_item = bank_list[idx]
                    if q_item not in selected_questions and len(selected_questions) < 5:
                        selected_questions.append(q_item)
                        added_in_round = True
            if not added_in_round:
                break
            idx += 1

        for gq in QUIZ_QUESTION_BANK['general']:
            if len(selected_questions) >= 5:
                break
            if gq not in selected_questions:
                selected_questions.append(gq)

    return render_template('screening_quiz.html', job=job, job_id=job_id, questions=selected_questions)


@app.route('/submit_quiz/<int:job_id>', methods=['POST'])
def submit_quiz(job_id):
    if 'student_email' not in session:
        return redirect(url_for('login'))

    total_q = int(request.form.get('total_questions', 0))
    correct = 0

    for i in range(total_q):
        user_ans = request.form.get(f'q_{i}', '')
        correct_ans = request.form.get(f'ans_{i}', '')
        if user_ans and user_ans == correct_ans:
            correct += 1

    quiz_score = f"{correct}/{total_q}"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM applications WHERE student_email = ? AND job_id = ?', (session['student_email'], job_id))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO applications (student_email, job_id, status, quiz_score) VALUES (?, ?, ?, ?)',
                       (session['student_email'], job_id, 'Under Review', quiz_score))
        conn.commit()
    conn.close()

    return redirect(url_for('dashboard'))


# --- OFFICIAL SHORTLIST / INTERVIEW CALL / SELECTION LETTER ---

@app.route('/shortlist_letter/<int:app_id>')
def shortlist_letter(app_id):
    if 'student_email' not in session and not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT applications.id as app_id, applications.student_email, applications.status, applications.quiz_score,
               applications.interview_date, applications.interview_time, applications.interview_venue,
               students.id as student_id, students.name as student_name, students.skills,
               internships.title as job_title, internships.company, internships.required_skills, internships.deadline,
               student_resumes.branch, student_resumes.cgpa, student_resumes.degree, student_resumes.college, student_resumes.grad_year
        FROM applications
        JOIN students ON applications.student_email = students.email
        JOIN internships ON applications.job_id = internships.id
        LEFT JOIN student_resumes ON students.email = student_resumes.student_email
        WHERE applications.id = ?
    ''', (app_id,))
    record = cursor.fetchone()
    conn.close()

    if not record:
        return redirect(url_for('dashboard'))

    if not session.get('is_admin') and session.get('student_email') != record['student_email']:
        return redirect(url_for('dashboard'))

    if record['status'] not in ['Shortlisted', 'Interview Scheduled', 'Selected']:
        return redirect(url_for('dashboard'))

    raw_sig = f"{record['app_id']}:{record['student_email']}:{record['company']}:MITS2026"
    checksum = hashlib.sha256(raw_sig.encode()).hexdigest()[:6].upper()
    ref_no = f"MITS/TNP/2026/{record['app_id']:04d}-{checksum}"
    issue_date = datetime.now().strftime("%B %d, %Y")
    f_date, f_time = format_interview_slot(record['interview_date'], record['interview_time'])

    return render_template(
        'shortlist_letter.html',
        rec=record,
        ref_no=ref_no,
        issue_date=issue_date,
        formatted_date=f_date,
        formatted_time=f_time
    )


# --- RECRUITER 1-CLICK EXCEL / CSV EXPORT ---

@app.route('/export_applicants/<int:job_id>')
def export_applicants(job_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT title, company FROM internships WHERE id = ?', (job_id,))
    job = cursor.fetchone()
    job_title = job['title'] if job else "Drive"

    cursor.execute('''
        SELECT applications.id, students.name, students.email, students.skills, applications.quiz_score,
               applications.status, applications.interview_date, applications.interview_time, applications.interview_venue
        FROM students
        JOIN applications ON students.email = applications.student_email
        WHERE applications.job_id = ?
        ORDER BY applications.id DESC
    ''', (job_id,))
    rows = cursor.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['App ID', 'Student Name', 'Registered Email', 'Candidate Skills', 'Screening Quiz Score', 'Application Status', 'Interview Date', 'Interview Time', 'Venue / Link'])

    for r in rows:
        cw.writerow([r['id'], r['name'], r['email'], r['skills'], r['quiz_score'], r['status'], r['interview_date'] or '', r['interview_time'] or '', r['interview_venue'] or ''])

    output = Response(si.getvalue(), mimetype='text/csv')
    filename = f"Applicants_{job_title.replace(' ', '_')}.csv"
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return output


@app.route('/update_status/<int:app_id>', methods=['POST'])
def update_status(app_id):
    new_status = request.form.get('status', 'Under Review')
    job_id = request.form.get('job_id')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE applications SET status = ? WHERE id = ?', (new_status, app_id))
    conn.commit()

    cursor.execute('''
        SELECT students.name, students.email, internships.title, internships.company,
               applications.interview_date, applications.interview_time, applications.interview_venue
        FROM applications
        JOIN students ON applications.student_email = students.email
        JOIN internships ON applications.job_id = internships.id
        WHERE applications.id = ?
    ''', (app_id,))
    rec = cursor.fetchone()
    conn.close()

    if rec:
        dispatch_notification_email(
            rec['email'], rec['name'], rec['title'], rec['company'], new_status,
            rec['interview_date'], rec['interview_time'], rec['interview_venue']
        )

    return redirect(url_for('view_applicants', job_id=job_id))


# --- PUBLIC PORTFOLIO WITH DYNAMIC QR CODE ---

@app.route('/u/<int:student_id>')
def public_portfolio(student_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM students WHERE id = ?', (student_id,))
    student = cursor.fetchone()

    if not student:
        conn.close()
        return redirect(url_for('jobs'))

    cursor.execute('SELECT * FROM student_resumes WHERE student_email = ?', (student['email'],))
    resume = cursor.fetchone()
    conn.close()

    skills_list = [s.strip() for s in student['skills'].split(',') if s.strip()]
    return render_template('portfolio.html', student=student, resume=resume, skills_list=skills_list)


# --- LIVE ONLINE OPENINGS (YEAR-ROUND JOB BOARDS + MULTI-API AGGREGATOR) ---

@app.route('/live_jobs')
def live_jobs():
    # 1. Year-Round Indian Job Portals, Internship Platforms & Direct Career Hubs (20 Verified Portals)
    indian_internships = [
        {
            "title": "Software Developer & IT Internships (Pan India)",
            "company_name": "Naukri.com",
            "candidate_required_location": "Pan India / Remote",
            "job_type": "Job & Internship Portal",
            "tags": ["Python", "Java", "SQL", "React"],
            "url": "https://www.naukri.com/software-developer-internship-jobs",
            "logo": get_company_logo("Naukri"),
            "is_indian": True
        },
        {
            "title": "Junior Software Engineer & Web Dev Roles",
            "company_name": "Indeed India",
            "candidate_required_location": "India / Work From Home",
            "job_type": "Aggregator Portal",
            "tags": ["Python", "HTML", "JavaScript", "SQL"],
            "url": "https://in.indeed.com/jobs?q=software+developer+intern&l=India",
            "logo": get_company_logo("Indeed"),
            "is_indian": True
        },
        {
            "title": "Verified Virtual & In-Office Tech Internships",
            "company_name": "Internshala",
            "candidate_required_location": "Work From Home / India",
            "job_type": "Paid Internship Hub",
            "tags": ["Python", "Web Dev", "React", "DSA"],
            "url": "https://internshala.com/internships/computer-science-internship/",
            "logo": get_company_logo("Internshala"),
            "is_indian": True
        },
        {
            "title": "Entry-Level Software Engineering & Internships",
            "company_name": "LinkedIn Jobs India",
            "candidate_required_location": "Bengaluru / Hyderabad / Remote",
            "job_type": "Professional Network",
            "tags": ["Python", "DSA", "Cloud", "React"],
            "url": "https://www.linkedin.com/jobs/search/?keywords=Software%20Engineer%20Intern&location=India",
            "logo": get_company_logo("LinkedIn"),
            "is_indian": True
        },
        {
            "title": "National Engineering & PSU Internship Scheme",
            "company_name": "AICTE National Internship Portal",
            "candidate_required_location": "All India (Govt & Industry)",
            "job_type": "Govt Approved Portal",
            "tags": ["Python", "C", "Cybersecurity", "AI"],
            "url": "https://internship.aicte-india.org/",
            "logo": get_company_logo("AICTE"),
            "is_indian": True
        },
        {
            "title": "Hiring Challenges, Hackathons & Tech Internships",
            "company_name": "Unstop",
            "candidate_required_location": "Pan India / Online",
            "job_type": "Competition & Hiring",
            "tags": ["DSA", "C++", "Python", "Full Stack"],
            "url": "https://unstop.com/internships",
            "logo": get_company_logo("Unstop"),
            "is_indian": True
        },
        {
            "title": "Startup Software Engineer & Full-Stack Interns",
            "company_name": "Wellfound (AngelList)",
            "candidate_required_location": "Bengaluru / Remote India",
            "job_type": "Startup Talent Hub",
            "tags": ["React", "Node.js", "Python", "Docker"],
            "url": "https://wellfound.com/role/r/software-engineer",
            "logo": get_company_logo("Wellfound"),
            "is_indian": True
        },
        {
            "title": "Junior Software Trainee (CEPT Tech)",
            "company_name": "India Post (CEPT / Dak Vibhag)",
            "candidate_required_location": "India (Bengaluru / Hybrid)",
            "job_type": "Govt Tech Trainee",
            "tags": ["Python", "SQL", "HTML", "C"],
            "url": "https://www.indiapost.gov.in/",
            "logo": get_company_logo("India Post"),
            "is_indian": True
        },
        {
            "title": "Software Engineer Intern (Digital & NextStep)",
            "company_name": "TCS (Tata Consultancy Services)",
            "candidate_required_location": "Pan India / Remote",
            "job_type": "Corporate Internship",
            "tags": ["Python", "SQL", "C", "DSA"],
            "url": "https://www.tcs.com/careers",
            "logo": get_company_logo("TCS"),
            "is_indian": True
        },
        {
            "title": "InStep & Springboard Software Trainee",
            "company_name": "Infosys Technologies",
            "candidate_required_location": "Bengaluru / Pune, India",
            "job_type": "Flagship Internship",
            "tags": ["Python", "Cloud", "SQL", "Flask"],
            "url": "https://www.infosys.com/careers.html",
            "logo": get_company_logo("Infosys"),
            "is_indian": True
        },
        {
            "title": "WILP & Project Engineer Internships",
            "company_name": "Wipro Technologies",
            "candidate_required_location": "Bengaluru / Chennai / Noida",
            "job_type": "Enterprise Career Portal",
            "tags": ["Java", "Python", "SQL", "Cloud"],
            "url": "https://careers.wipro.com/",
            "logo": get_company_logo("Wipro"),
            "is_indian": True
        },
        {
            "title": "Graduate & Software Engineering Trainee",
            "company_name": "HCLTech India",
            "candidate_required_location": "Noida / Lucknow / Bengaluru",
            "job_type": "Enterprise Career Portal",
            "tags": ["C", "Python", "SQL", "Networking"],
            "url": "https://www.hcltech.com/careers",
            "logo": get_company_logo("HCL"),
            "is_indian": True
        },
        {
            "title": "Full Stack Web Developer Apprentice",
            "company_name": "Zoho Corporation",
            "candidate_required_location": "Chennai / Remote India",
            "job_type": "Product Internship",
            "tags": ["HTML", "React", "JavaScript", "SQL"],
            "url": "https://www.zoho.com/careers/",
            "logo": get_company_logo("Zoho"),
            "is_indian": True
        },
        {
            "title": "Backend & Fintech Systems Intern",
            "company_name": "Razorpay India",
            "candidate_required_location": "Bengaluru / Remote India",
            "job_type": "Fintech Internship",
            "tags": ["Python", "SQL", "REST APIs", "Docker"],
            "url": "https://razorpay.com/jobs/",
            "logo": get_company_logo("Razorpay"),
            "is_indian": True
        },
        {
            "title": "Software Development Engineering (SDE) Intern",
            "company_name": "Flipkart Careers",
            "candidate_required_location": "Bengaluru, India",
            "job_type": "E-Commerce Tech",
            "tags": ["DSA", "Java", "Python", "SQL"],
            "url": "https://www.flipkartcareers.com/",
            "logo": get_company_logo("Flipkart"),
            "is_indian": True
        },
        {
            "title": "Student Research & Engineering Trainee",
            "company_name": "ISRO (Indian Space Research Organisation)",
            "candidate_required_location": "India (IIRS / SAC Dehradun)",
            "job_type": "Govt Research Trainee",
            "tags": ["Python", "C", "Data Analysis", "GIS"],
            "url": "https://www.isro.gov.in/",
            "logo": get_company_logo("ISRO"),
            "is_indian": True
        },
        {
            "title": "Paid Engineering & Computer Science Apprenticeship",
            "company_name": "DRDO (Defence R&D Organisation)",
            "candidate_required_location": "Delhi / Bengaluru / Pune",
            "job_type": "Govt Defence R&D",
            "tags": ["C", "C++", "Python", "Algorithms"],
            "url": "https://www.drdo.gov.in/drdo/careers",
            "logo": get_company_logo("DRDO"),
            "is_indian": True
        },
        {
            "title": "Software Engineering & STEP Internships (India)",
            "company_name": "Google Careers India",
            "candidate_required_location": "Bengaluru / Hyderabad / Pune",
            "job_type": "Global Tech Hub",
            "tags": ["DSA", "C++", "Python", "Cloud"],
            "url": "https://www.google.com/about/careers/applications/jobs/results/?location=India",
            "logo": get_company_logo("Google"),
            "is_indian": True
        },
        {
            "title": "Software Engineering Internship (India IDC)",
            "company_name": "Microsoft Careers India",
            "candidate_required_location": "Hyderabad / Bengaluru / Noida",
            "job_type": "Global Tech Hub",
            "tags": ["DSA", "C++", "React", "Cloud"],
            "url": "https://careers.microsoft.com/v2/global/en/home.html",
            "logo": get_company_logo("Microsoft"),
            "is_indian": True
        },
        {
            "title": "SDE Intern & AWS Cloud Support Roles",
            "company_name": "Amazon Jobs India",
            "candidate_required_location": "Bengaluru / Chennai / Hyderabad",
            "job_type": "Global Tech Hub",
            "tags": ["DSA", "Java", "Python", "Docker"],
            "url": "https://www.amazon.jobs/en/locations/india",
            "logo": get_company_logo("Amazon"),
            "is_indian": True
        }
    ]

    # 2. Multi-API Live Global Remote Aggregator (Remotive + Arbeitnow + Jobicy with 5-min cache)
    now = time.time()
    if LIVE_PORTAL_CACHE["jobs"] and (now - LIVE_PORTAL_CACHE["timestamp"] < LIVE_PORTAL_TTL):
        external_jobs = LIVE_PORTAL_CACHE["jobs"]
    else:
        external_jobs = []
        seen_titles = set()

        # Source A: Remotive Live Remote Software Jobs
        try:
            r1 = requests.get('https://remotive.com/api/remote-jobs?category=software-dev&limit=14', timeout=3.5)
            if r1.status_code == 200:
                for rj in r1.json().get('jobs', []):
                    title = (rj.get('title') or '').strip()
                    c_name = (rj.get('company_name') or '').strip()
                    key = (title.lower(), c_name.lower())
                    if title and c_name and key not in seen_titles:
                        seen_titles.add(key)
                        api_logo = rj.get('company_logo') or rj.get('company_logo_url')
                        external_jobs.append({
                            "title": title,
                            "company_name": c_name,
                            "candidate_required_location": rj.get('candidate_required_location') or 'Worldwide Remote',
                            "job_type": rj.get('job_type') or 'Remote Tech',
                            "tags": (rj.get('tags') or ["Python", "Web Dev"])[:5],
                            "url": rj.get('url'),
                            "logo": get_company_logo(c_name, api_logo),
                            "is_indian": False
                        })
        except Exception as e:
            print(f"Remotive API note: {e}")

        # Source B: Arbeitnow Public Job Board API (No auth required)
        try:
            r2 = requests.get('https://www.arbeitnow.com/api/job-board-api', timeout=3.5)
            if r2.status_code == 200:
                for aj in r2.json().get('data', [])[:12]:
                    title = (aj.get('title') or '').strip()
                    c_name = (aj.get('company_name') or '').strip()
                    key = (title.lower(), c_name.lower())
                    if title and c_name and key not in seen_titles:
                        seen_titles.add(key)
                        tags = aj.get('tags') or ["Software", "Remote"]
                        loc = "Remote / Global" if aj.get('remote') else (aj.get('location') or "Europe / Remote")
                        external_jobs.append({
                            "title": title,
                            "company_name": c_name,
                            "candidate_required_location": loc,
                            "job_type": (aj.get('job_types') or ['Full-Time / Remote'])[0],
                            "tags": tags[:5],
                            "url": aj.get('url'),
                            "logo": get_company_logo(c_name),
                            "is_indian": False
                        })
        except Exception as e:
            print(f"Arbeitnow API note: {e}")

        # Source C: Jobicy Public Engineering Jobs API (No auth required)
        try:
            r3 = requests.get('https://jobicy.com/api/v2/remote-jobs?count=10&industry=engineering', timeout=3.5)
            if r3.status_code == 200:
                for jj in r3.json().get('jobs', []):
                    title = (jj.get('jobTitle') or '').strip()
                    c_name = (jj.get('companyName') or '').strip()
                    key = (title.lower(), c_name.lower())
                    if title and c_name and key not in seen_titles:
                        seen_titles.add(key)
                        j_type = jj.get('jobType')
                        if isinstance(j_type, list):
                            j_type = j_type[0] if j_type else "Remote Engineering"
                        external_jobs.append({
                            "title": title,
                            "company_name": c_name,
                            "candidate_required_location": jj.get('jobGeo') or 'Worldwide Remote',
                            "job_type": j_type or 'Remote Engineering',
                            "tags": ["Engineering", "Remote", "Full-Stack"],
                            "url": jj.get('url'),
                            "logo": get_company_logo(c_name, jj.get('companyLogo')),
                            "is_indian": False
                        })
        except Exception as e:
            print(f"Jobicy API note: {e}")

        if external_jobs:
            LIVE_PORTAL_CACHE["timestamp"] = now
            LIVE_PORTAL_CACHE["jobs"] = external_jobs

    return render_template('live_jobs.html', jobs=indian_internships + external_jobs)


# --- MULTI-LANGUAGE YOUTUBE COURSES HUB ---

@app.route('/courses')
def courses():
    return render_template('courses.html')


@app.route('/developers')
def developers():
    return render_template('developers.html')


@app.route('/matcher', methods=['GET', 'POST'])
def matcher():
    student = None
    if 'student_email' in session:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM students WHERE email = ?', (session['student_email'],))
        student = cursor.fetchone()
        conn.close()

    return render_template('matcher.html', student=student)


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'student_email' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        updated_skills = request.form.get('skills', '').strip()
        avatar_url = request.form.get('avatar_url', '').strip()
        cursor.execute('UPDATE students SET skills = ?, avatar_url = ? WHERE email = ?',
                       (updated_skills, avatar_url, session['student_email']))
        conn.commit()

    cursor.execute('SELECT id, name, email, skills, avatar_url FROM students WHERE email = ?', (session['student_email'],))
    student = cursor.fetchone()

    if not student:
        conn.close()
        session.clear()
        return redirect(url_for('login'))

    applied_jobs = []
    try:
        cursor.execute('''
            SELECT applications.id as app_id, internships.id, internships.title, internships.company,
                   internships.required_skills, applications.status, applications.quiz_score,
                   applications.interview_date, applications.interview_time, applications.interview_venue
            FROM internships
            JOIN applications ON internships.id = applications.job_id
            WHERE applications.student_email = ?
            ORDER BY applications.id DESC
        ''', (session['student_email'],))
        for row in cursor.fetchall():
            item = dict(row)
            f_date, f_time = format_interview_slot(item.get('interview_date'), item.get('interview_time'))
            item['formatted_date'] = f_date
            item['formatted_time'] = f_time
            item['gcal_url'] = build_gcal_link(
                item['title'], item['company'],
                item.get('interview_date'), item.get('interview_time'), item.get('interview_venue')
            )
            applied_jobs.append(item)
    except Exception:
        applied_jobs = []

    try:
        cursor.execute('''
            SELECT internships.id, internships.title, internships.company, internships.required_skills
            FROM internships
            JOIN saved_jobs ON internships.id = saved_jobs.job_id
            WHERE saved_jobs.student_email = ?
            ORDER BY saved_jobs.id DESC
        ''', (session['student_email'],))
        saved_jobs = cursor.fetchall()
    except Exception:
        saved_jobs = []

    conn.close()
    return render_template('dashboard.html', student=student, applied_jobs=applied_jobs, saved_jobs=saved_jobs)


@app.route('/toggle_save/<int:job_id>', methods=['POST'])
def toggle_save(job_id):
    if 'student_email' not in session:
        return jsonify({"status": "error", "message": "Sign in required"}), 401

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM saved_jobs WHERE student_email = ? AND job_id = ?', (session['student_email'], job_id))
    row = cursor.fetchone()

    if row:
        cursor.execute('DELETE FROM saved_jobs WHERE id = ?', (row['id'],))
        is_saved = False
    else:
        cursor.execute('INSERT INTO saved_jobs (student_email, job_id) VALUES (?, ?)', (session['student_email'], job_id))
        is_saved = True

    conn.commit()
    conn.close()
    return jsonify({"status": "success", "is_saved": is_saved})


@app.route('/interview_prep/<int:job_id>')
def interview_prep(job_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, title, company, required_skills FROM internships WHERE id = ?', (job_id,))
    job = cursor.fetchone()
    conn.close()

    if not job:
        return redirect(url_for('jobs'))

    req_skills = [s.strip().lower() for s in job['required_skills'].split(',') if s.strip()]

    interview_bank = {
        'python': [
            {"q": "What is the difference between list and tuple in Python?", "a": "Lists are mutable; tuples are immutable, faster, and preserve fixed data integrity."},
            {"q": "How does memory management work in Python?", "a": "Python uses a private heap managed by Reference Counting and a generational Garbage Collector to destroy cyclic references."},
            {"q": "Explain decorators in Python.", "a": "Decorators wrap another function to extend its behavior dynamically without modifying its source code directly."}
        ],
        'sql': [
            {"q": "Explain WHERE vs. HAVING clause.", "a": "WHERE filters rows before aggregation occurs; HAVING filters aggregated groups after GROUP BY executes."},
            {"q": "What are primary and foreign keys?", "a": "Primary key uniquely identifies records in a table; foreign key references another primary key to maintain referential integrity."}
        ],
        'html': [
            {"q": "What are Semantic HTML5 tags?", "a": "Tags like <header>, <nav>, <article>, and <footer> communicate structural meaning to browsers and accessibility tools."}
        ],
        'c': [
            {"q": "What is a pointer in C?", "a": "A pointer is a variable that stores the memory address of another variable. The dereference operator (*) accesses the value at that address."}
        ]
    }

    curated_qa = []
    for skill in req_skills:
        for key in interview_bank:
            if key in skill:
                for item in interview_bank[key]:
                    if item not in curated_qa:
                        curated_qa.append({"skill": key.upper(), **item})

    if not curated_qa:
        curated_qa = [
            {"skill": "CORE CS", "q": "Explain QuickSort vs. MergeSort.", "a": "MergeSort is always O(N log N) with O(N) space. QuickSort is average O(N log N) in-place."}
        ]

    return render_template('interview_prep.html', job=job, curated_qa=curated_qa)


@app.route('/analytics')
def analytics():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT required_skills FROM internships')
    rows = cursor.fetchall()
    all_skills = []
    for row in rows:
        for s in row['required_skills'].split(','):
            cleaned = s.strip().title()
            if cleaned:
                all_skills.append(cleaned)
    skill_counts = Counter(all_skills).most_common(6)
    skill_labels = [sc[0] for sc in skill_counts]
    skill_data = [sc[1] for sc in skill_counts]

    student_readiness = 0
    student_skills_count = 0
    if 'student_email' in session:
        cursor.execute('SELECT skills FROM students WHERE email = ?', (session['student_email'],))
        row = cursor.fetchone()
        if row and row['skills']:
            st_skills = [s.strip().lower() for s in row['skills'].split(',') if s.strip()]
            student_skills_count = len(st_skills)
            matches_in_market = sum(1 for s in st_skills if any(s in lbl.lower() for lbl in skill_labels))
            student_readiness = min(100, int((matches_in_market / max(1, len(skill_labels))) * 100) + 20)

    cursor.execute('SELECT status, COUNT(*) as count FROM applications GROUP BY status')
    status_rows = cursor.fetchall()
    status_labels = [sr['status'] for sr in status_rows] if status_rows else ["Under Review"]
    status_data = [sr['count'] for sr in status_rows] if status_rows else [1]

    cursor.execute('SELECT COUNT(*) FROM internships')
    total_openings = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM applications')
    total_apps = cursor.fetchone()[0]
    conn.close()

    return render_template('analytics.html',
                           skill_labels=skill_labels, skill_data=skill_data,
                           status_labels=status_labels, status_data=status_data,
                           student_readiness=student_readiness,
                           student_skills_count=student_skills_count,
                           total_openings=total_openings, total_apps=total_apps)


@app.route('/build_resume', methods=['GET', 'POST'])
def build_resume():
    if 'student_email' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        location = request.form.get('location', '').strip()
        linkedin = request.form.get('linkedin', '').strip()
        github = request.form.get('github', '').strip()
        college = request.form.get('college', '').strip()
        degree = request.form.get('degree', '').strip()
        branch = request.form.get('branch', '').strip()
        cgpa = request.form.get('cgpa', '').strip()
        grad_year = request.form.get('grad_year', '').strip()
        skills = request.form.get('skills', '').strip()
        project1_title = request.form.get('project1_title', '').strip()
        project1_tech = request.form.get('project1_tech', '').strip()
        project1_desc = request.form.get('project1_desc', '').strip()
        project2_title = request.form.get('project2_title', '').strip()
        project2_tech = request.form.get('project2_tech', '').strip()
        project2_desc = request.form.get('project2_desc', '').strip()
        certifications = request.form.get('certifications', '').strip()
        summary = request.form.get('summary', '').strip()

        if skills:
            cursor.execute('UPDATE students SET skills = ? WHERE email = ?', (skills, session['student_email']))

        cursor.execute('SELECT id FROM student_resumes WHERE student_email = ?', (session['student_email'],))
        row = cursor.fetchone()

        if row:
            cursor.execute('''
                UPDATE student_resumes SET
                    phone=?, location=?, linkedin=?, github=?,
                    college=?, degree=?, branch=?, cgpa=?, grad_year=?,
                    project1_title=?, project1_tech=?, project1_desc=?,
                    project2_title=?, project2_tech=?, project2_desc=?,
                    certifications=?, summary=?
                WHERE student_email=?
            ''', (phone, location, linkedin, github, college, degree, branch, cgpa, grad_year,
                  project1_title, project1_tech, project1_desc, project2_title, project2_tech, project2_desc,
                  certifications, summary, session['student_email']))
        else:
            cursor.execute('''
                INSERT INTO student_resumes (
                    student_email, phone, location, linkedin, github,
                    college, degree, branch, cgpa, grad_year,
                    project1_title, project1_tech, project1_desc,
                    project2_title, project2_tech, project2_desc,
                    certifications, summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (session['student_email'], phone, location, linkedin, github,
                  college, degree, branch, cgpa, grad_year,
                  project1_title, project1_tech, project1_desc,
                  project2_title, project2_tech, project2_desc,
                  certifications, summary))

        conn.commit()
        conn.close()
        return redirect(url_for('resume'))

    cursor.execute('SELECT name, email, skills FROM students WHERE email = ?', (session['student_email'],))
    student = cursor.fetchone()

    cursor.execute('SELECT * FROM student_resumes WHERE student_email = ?', (session['student_email'],))
    resume_data = cursor.fetchone()
    conn.close()

    return render_template('resume_builder.html', student=student, resume=resume_data)


@app.route('/resume')
def resume():
    if 'student_email' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, email, skills FROM students WHERE email = ?', (session['student_email'],))
    student = cursor.fetchone()

    cursor.execute('SELECT * FROM student_resumes WHERE student_email = ?', (session['student_email'],))
    resume_data = cursor.fetchone()
    conn.close()

    if not resume_data:
        return redirect(url_for('build_resume'))

    skills_list = [s.strip() for s in student['skills'].split(',') if s.strip()] if student and student['skills'] else []
    return render_template('resume.html', student=student, resume=resume_data, skills_list=skills_list)


@app.route('/login')
def login():
    return render_template('login.html')


@app.route('/register')
def register():
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('live_jobs'))


@app.route('/sync_firebase', methods=['POST'])
def sync_firebase():
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    action = data.get('action')

    if not email:
        return jsonify({"status": "error", "message": "Email is required"}), 400

    conn = get_db()
    cursor = conn.cursor()

    if action == 'register':
        name = (data.get('name') or 'Student').strip()
        skills = (data.get('skills') or '').strip()
        try:
            cursor.execute('INSERT INTO students (name, email, skills) VALUES (?, ?, ?)', (name, email, skills))
            conn.commit()
        except sqlite3.IntegrityError:
            cursor.execute('UPDATE students SET name = ?, skills = ? WHERE lower(email) = ?', (name, skills, email))
            conn.commit()
        session['student_name'] = name
        session['student_email'] = email

    elif action in ('login', 'google'):
        name = (data.get('name') or email.split('@')[0]).strip()
        avatar_url = (data.get('photo_url') or data.get('avatar_url') or '').strip()
        cursor.execute('SELECT name, avatar_url FROM students WHERE lower(email) = ?', (email,))
        student = cursor.fetchone()

        if student:
            final_name = student['name'] if student['name'] else name
            if avatar_url and not student['avatar_url']:
                cursor.execute('UPDATE students SET avatar_url = ? WHERE lower(email) = ?', (avatar_url, email))
                conn.commit()
        else:
            cursor.execute(
                'INSERT INTO students (name, email, skills, avatar_url) VALUES (?, ?, ?, ?)',
                (name, email, '', avatar_url)
            )
            conn.commit()
            final_name = name

        session['student_name'] = final_name
        session['student_email'] = email

    conn.close()
    return jsonify({"status": "success", "success": True, "redirect": url_for('dashboard')})


@app.route('/google_login', methods=['POST'])
def google_login():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    name = (data.get('name') or (email.split('@')[0] if email else 'Student')).strip()
    avatar_url = (data.get('photo_url') or data.get('avatar_url') or '').strip()

    if not email:
        return jsonify({"success": False, "error": "Google account email not received."}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT name, avatar_url FROM students WHERE lower(email) = ?', (email,))
    student = cursor.fetchone()

    if student:
        final_name = student['name'] if student['name'] else name
        if avatar_url and not student['avatar_url']:
            cursor.execute('UPDATE students SET avatar_url = ? WHERE lower(email) = ?', (avatar_url, email))
            conn.commit()
    else:
        cursor.execute(
            'INSERT INTO students (name, email, skills, avatar_url) VALUES (?, ?, ?, ?)',
            (name, email, '', avatar_url)
        )
        conn.commit()
        final_name = name

    conn.close()

    session['student_name'] = final_name
    session['student_email'] = email

    return jsonify({"success": True, "redirect": url_for('dashboard')})


# --- T&P ADMIN ONLY: POST NEW CAMPUS DRIVE & CUSTOM QUIZ ---

@app.route('/add_job', methods=['GET', 'POST'])
def add_job():
    # Require active T&P Admin login first
    if not session.get('is_admin'):
        return redirect(url_for('admin_panel'))

    error = None
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        company = request.form.get('company', '').strip()
        required_skills = request.form.get('required_skills', '').strip()
        deadline = request.form.get('deadline', 'Nov 30, 2026').strip()

        if not title or not company or not required_skills:
            error = "Please fill in Role Title, Company, and Required Skills."
            return render_template('add_job.html', error=error)

        # Parse custom screening quiz questions added by Admin (if any)
        q_texts = request.form.getlist('q_text[]')
        opt_as = request.form.getlist('opt_a[]')
        opt_bs = request.form.getlist('opt_b[]')
        opt_cs = request.form.getlist('opt_c[]')
        opt_ds = request.form.getlist('opt_d[]')
        correct_opts = request.form.getlist('correct_opt[]')

        custom_questions = []
        for i in range(len(q_texts)):
            q_str = (q_texts[i] or '').strip()
            oa = (opt_as[i] if i < len(opt_as) else '').strip()
            ob = (opt_bs[i] if i < len(opt_bs) else '').strip()
            oc = (opt_cs[i] if i < len(opt_cs) else '').strip()
            od = (opt_ds[i] if i < len(opt_ds) else '').strip()
            c_key = (correct_opts[i] if i < len(correct_opts) else 'A').strip().upper()

            if q_str and oa and ob and oc and od:
                opt_map = {'A': oa, 'B': ob, 'C': oc, 'D': od}
                ans_text = opt_map.get(c_key, oa)
                custom_questions.append({
                    "q": q_str,
                    "opts": [oa, ob, oc, od],
                    "ans": ans_text
                })

        custom_quiz_json = json.dumps(custom_questions)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO internships (title, company, required_skills, deadline, custom_quiz) VALUES (?, ?, ?, ?, ?)',
            (title, company, required_skills, deadline, custom_quiz_json)
        )
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))

    return render_template('add_job.html', error=error)


@app.route('/view_applicants/<int:job_id>')
def view_applicants(job_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, title, company, required_skills FROM internships WHERE id = ?', (job_id,))
    job = cursor.fetchone()
    job_title = job['title'] if job else "Unknown Role"

    cursor.execute('''
        SELECT applications.id as app_id, students.id as student_id, students.name, students.email, students.skills, applications.status, applications.quiz_score
        FROM students
        JOIN applications ON students.email = applications.student_email
        WHERE applications.job_id = ?
        ORDER BY applications.id DESC
    ''', (job_id,))
    applicants = cursor.fetchall()
    conn.close()
    return render_template('view_applicants.html', applicants=applicants, job=job, job_title=job_title, job_id=job_id)


# --- T&P MASTER ADMIN & LIVE DATABASE VIEWER ---

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    try:
        error = None
        if request.method == 'POST':
            pin = request.form.get('admin_pin', '').strip()
            if pin == ADMIN_PASSKEY:
                session['is_admin'] = True
                session['admin_last_active'] = time.time()
                return redirect(url_for('admin_panel'))
            else:
                error = "Invalid Coordinator Passkey. Access Denied."

        if not session.get('is_admin'):
            return render_template('admin.html', is_admin=False, error=error)

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT students.id, students.name, students.email, students.skills,
                   student_resumes.branch, student_resumes.cgpa, student_resumes.phone
            FROM students
            LEFT JOIN student_resumes ON students.email = student_resumes.student_email
            ORDER BY students.id DESC
        ''')
        students_list = cursor.fetchall()

        cursor.execute('''
            SELECT applications.id, applications.student_email, students.name as student_name,
                   internships.title as job_title, internships.company,
                   applications.quiz_score, applications.status, applications.job_id,
                   applications.interview_date, applications.interview_time, applications.interview_venue
            FROM applications
            LEFT JOIN students ON applications.student_email = students.email
            LEFT JOIN internships ON applications.job_id = internships.id
            ORDER BY applications.id DESC
        ''')
        applications_list = []
        for row in cursor.fetchall():
            item = dict(row)
            f_date, f_time = format_interview_slot(item.get('interview_date'), item.get('interview_time'))
            item['formatted_date'] = f_date
            item['formatted_time'] = f_time
            applications_list.append(item)

        cursor.execute('''
            SELECT internships.id, internships.title, internships.company,
                   internships.required_skills, internships.deadline, internships.custom_quiz,
                   COUNT(applications.id) as app_count
            FROM internships
            LEFT JOIN applications ON internships.id = applications.job_id
            GROUP BY internships.id
            ORDER BY internships.id DESC
        ''')
        drives_list = []
        for row in cursor.fetchall():
            d_item = dict(row)
            try:
                cq = json.loads(d_item.get('custom_quiz') or '[]')
                d_item['custom_quiz_count'] = len(cq)
            except Exception:
                d_item['custom_quiz_count'] = 0
            drives_list.append(d_item)

        cursor.execute('''
            SELECT student_resumes.*, students.id as student_id, students.name as student_name, students.skills
            FROM student_resumes
            LEFT JOIN students ON student_resumes.student_email = students.email
            ORDER BY student_resumes.id DESC
        ''')
        resumes_list = cursor.fetchall()

        conn.close()

        return render_template(
            'admin.html',
            is_admin=True,
            students=students_list,
            applications=applications_list,
            drives=drives_list,
            resumes=resumes_list
        )
    except Exception:
        import traceback
        return f"<pre style='padding:20px;background:#111;color:#f87171;font-family:monospace;'>ADMIN ERROR:\n{traceback.format_exc()}</pre>", 500


@app.route('/admin/update_status/<int:app_id>', methods=['POST'])
def admin_update_status(app_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_panel'))

    new_status = request.form.get('status', 'Under Review')
    interview_date = request.form.get('interview_date', '').strip()
    interview_time = request.form.get('interview_time', '').strip()
    interview_venue = request.form.get('interview_venue', '').strip()

    conn = get_db()
    cursor = conn.cursor()

    if new_status == 'Interview Scheduled':
        cursor.execute('''
            UPDATE applications
            SET status = ?, interview_date = ?, interview_time = ?, interview_venue = ?
            WHERE id = ?
        ''', (new_status, interview_date, interview_time, interview_venue, app_id))
    else:
        cursor.execute('UPDATE applications SET status = ? WHERE id = ?', (new_status, app_id))

    conn.commit()

    cursor.execute('''
        SELECT students.name, students.email, internships.title, internships.company,
               applications.interview_date, applications.interview_time, applications.interview_venue
        FROM applications
        JOIN students ON applications.student_email = students.email
        JOIN internships ON applications.job_id = internships.id
        WHERE applications.id = ?
    ''', (app_id,))
    rec = cursor.fetchone()
    conn.close()

    if rec:
        dispatch_notification_email(
            rec['email'], rec['name'], rec['title'], rec['company'], new_status,
            rec['interview_date'], rec['interview_time'], rec['interview_venue']
        )

    return redirect(url_for('admin_panel'))


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    session.pop('admin_last_active', None)
    return redirect(url_for('admin_panel'))


@app.route('/admin/delete_student/<int:student_id>', methods=['POST'])
def admin_delete_student(student_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_panel'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT email FROM students WHERE id = ?', (student_id,))
    row = cursor.fetchone()
    if row:
        email = row['email']
        cursor.execute('DELETE FROM applications WHERE student_email = ?', (email,))
        cursor.execute('DELETE FROM saved_jobs WHERE student_email = ?', (email,))
        cursor.execute('DELETE FROM student_resumes WHERE student_email = ?', (email,))
        cursor.execute('DELETE FROM students WHERE id = ?', (student_id,))
        conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))


@app.route('/admin/export/<string:export_type>')
def admin_export_csv(export_type):
    if not session.get('is_admin'):
        return redirect(url_for('admin_panel'))

    conn = get_db()
    cursor = conn.cursor()
    si = StringIO()
    cw = csv.writer(si)

    if export_type == 'students':
        cw.writerow(['Student ID', 'Full Name', 'Email', 'Verified Skills', 'Branch', 'CGPA', 'Phone'])
        cursor.execute('''
            SELECT students.id, students.name, students.email, students.skills,
                   student_resumes.branch, student_resumes.cgpa, student_resumes.phone
            FROM students
            LEFT JOIN student_resumes ON students.email = student_resumes.student_email
            ORDER BY students.id DESC
        ''')
        for r in cursor.fetchall():
            cw.writerow([r['id'], r['name'], r['email'], r['skills'], r['branch'] or 'N/A', r['cgpa'] or 'N/A', r['phone'] or 'N/A'])
        filename = "Skill2Career_All_Students.csv"

    else:
        cw.writerow(['App ID', 'Student Name', 'Email', 'Company', 'Role', 'Quiz Score', 'Status', 'Interview Date', 'Interview Time', 'Venue / Link'])
        cursor.execute('''
            SELECT applications.id, students.name, applications.student_email,
                   internships.company, internships.title, applications.quiz_score, applications.status,
                   applications.interview_date, applications.interview_time, applications.interview_venue
            FROM applications
            LEFT JOIN students ON applications.student_email = students.email
            LEFT JOIN internships ON applications.job_id = internships.id
            ORDER BY applications.id DESC
        ''')
        for r in cursor.fetchall():
            cw.writerow([r['id'], r['name'] or 'Student', r['student_email'], r['company'], r['title'], r['quiz_score'], r['status'], r['interview_date'] or '', r['interview_time'] or '', r['interview_venue'] or ''])
        filename = "Skill2Career_All_Applications.csv"

    conn.close()
    output = Response(si.getvalue(), mimetype='text/csv')
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return output


if __name__ == '__main__':
    app.run(debug=True)