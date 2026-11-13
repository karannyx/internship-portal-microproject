import sqlite3
import requests
import time
import csv
import os
import smtplib
from io import StringIO
from collections import Counter
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response

app = Flask(__name__)
app.secret_key = 'skill2career_production_secret_key_2026'
DB_NAME = 'database.db'

ADMIN_PASSKEY = "admin2026"

# Email Configuration (Set via Render Environment Variables if available)
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "placement.cell.mits@gmail.com")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")  # App Password

LAST_SYNC_TIME = 0
SYNC_INTERVAL = 300  

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def get_company_logo(company_name, api_logo=None):
    """Returns official high-res company logo URL with domain matching."""
    if api_logo:
        return api_logo
    name = (company_name or "").lower()
    domain_map = {
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
            deadline TEXT DEFAULT 'Nov 25, 2026'
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_email TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            status TEXT DEFAULT 'Under Review',
            quiz_score TEXT DEFAULT 'N/A'
        )
    ''')

    # Schema Migrations
    cursor.execute("PRAGMA table_info(students)")
    s_cols = [col[1] for col in cursor.fetchall()]
    if 'avatar_url' not in s_cols:
        cursor.execute("ALTER TABLE students ADD COLUMN avatar_url TEXT DEFAULT ''")

    cursor.execute("PRAGMA table_info(internships)")
    i_cols = [col[1] for col in cursor.fetchall()]
    if 'deadline' not in i_cols:
        cursor.execute("ALTER TABLE internships ADD COLUMN deadline TEXT DEFAULT 'Nov 25, 2026'")

    cursor.execute("PRAGMA table_info(applications)")
    a_cols = [col[1] for col in cursor.fetchall()]
    if 'quiz_score' not in a_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN quiz_score TEXT DEFAULT 'N/A'")

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
            ("Junior Software Trainee", "India Post (CEPT)", "Python, SQL, HTML", "Nov 22, 2026"),
            ("Digital Banking Systems Trainee", "India Post Tech Cell", "Python, HTML, C", "Nov 26, 2026"),
            ("Software Engineer Intern", "TCS Digital", "Python, SQL, C", "Nov 20, 2026"),
            ("Data Analyst Intern", "Infosys Springboard", "Python, SQL", "Nov 30, 2026")
        ]
        cursor.executemany('INSERT INTO internships (title, company, required_skills, deadline) VALUES (?, ?, ?, ?)', openings)

    conn.commit()
    conn.close()

init_db()

# --- AUTOMATED EMAIL DISPATCHER ---

def dispatch_notification_email(student_email, student_name, job_title, company, status):
    subject = f"Placement Cell Notice: Application Status Updated for {job_title}"
    body = f"""
Dear {student_name},

Greetings from the Training & Placement Cell, MITS Gwalior.

Your application status for the following campus opening has been updated:
Role: {job_title}
Organization: {company}
Updated Status: {status}

{'Congratulations! Please check your portal dashboard for round instructions.' if status in ['Shortlisted', 'Interview Scheduled'] else 'Login to Skill2Career to review role analytics and recommendations.'}

Portal Access: https://skill2career.onrender.com/dashboard

Regards,
Training & Placement Cell
Madhav Institute of Technology & Science, Gwalior
"""
    if not SENDER_PASSWORD:
        print(f"\n[MOCK EMAIL DISPATCHED] To: {student_email} | Subject: {subject} | Status: {status}\n")
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
                        cursor.execute('INSERT INTO internships (title, company, required_skills, deadline) VALUES (?, ?, ?, ?)', 
                                       (title, company, skills, 'Rolling Basis'))

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
    cursor.execute('SELECT id, title, company, required_skills, deadline FROM internships ORDER BY id DESC')
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

# --- SCREENING QUIZ BEFORE APPLYING ---

QUIZ_QUESTION_BANK = {
    'python': [
        {"q": "What is the output of print(type([]) is list)?", "opts": ["True", "False", "TypeError", "None"], "ans": "True"},
        {"q": "Which data structure is immutable in Python?", "opts": ["Tuple", "List", "Dictionary", "Set"], "ans": "Tuple"}
    ],
    'sql': [
        {"q": "Which clause is used to filter aggregated groups?", "opts": ["HAVING", "WHERE", "GROUP BY", "ORDER BY"], "ans": "HAVING"},
        {"q": "Which constraint enforces uniqueness across rows?", "opts": ["UNIQUE / PRIMARY KEY", "FOREIGN KEY", "DEFAULT", "CHECK"], "ans": "UNIQUE / PRIMARY KEY"}
    ],
    'c': [
        {"q": "Which operator returns the memory address of a variable?", "opts": ["& (Address-of)", "* (Dereference)", "->", "sizeof"], "ans": "& (Address-of)"},
        {"q": "In C, strings terminate with which character?", "opts": ["\\0 (Null)", "\\n", "EOF", "\\t"], "ans": "\\0 (Null)"}
    ],
    'html': [
        {"q": "Which HTML5 tag is semantic for site navigation links?", "opts": ["<nav>", "<menu>", "<header>", "<section>"], "ans": "<nav>"},
        {"q": "Which tag embeds a responsive canvas for Chart.js?", "opts": ["<canvas>", "<svg>", "<paint>", "<graphic>"], "ans": "<canvas>"}
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

    cursor.execute('SELECT title, company, required_skills FROM internships WHERE id = ?', (job_id,))
    job = cursor.fetchone()
    conn.close()

    if not job:
        return redirect(url_for('jobs'))

    req_skills = [s.strip().lower() for s in job['required_skills'].split(',') if s.strip()]
    selected_questions = []

    for sk in req_skills:
        for bank_key in QUIZ_QUESTION_BANK:
            if bank_key in sk:
                for q in QUIZ_QUESTION_BANK[bank_key]:
                    if q not in selected_questions and len(selected_questions) < 3:
                        selected_questions.append(q)

    if not selected_questions:
        selected_questions = QUIZ_QUESTION_BANK['python'][:2] + [QUIZ_QUESTION_BANK['sql'][0]]

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

# --- RECRUITER 1-CLICK EXCEL / CSV EXPORT ---

@app.route('/export_applicants/<int:job_id>')
def export_applicants(job_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT title, company FROM internships WHERE id = ?', (job_id,))
    job = cursor.fetchone()
    job_title = job['title'] if job else "Drive"

    cursor.execute('''
        SELECT applications.id, students.name, students.email, students.skills, applications.quiz_score, applications.status
        FROM students
        JOIN applications ON students.email = applications.student_email
        WHERE applications.job_id = ?
        ORDER BY applications.id DESC
    ''', (job_id,))
    rows = cursor.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['App ID', 'Student Name', 'Registered Email', 'Candidate Skills', 'Screening Quiz Score', 'Application Status'])

    for r in rows:
        cw.writerow([r['id'], r['name'], r['email'], r['skills'], r['quiz_score'], r['status']])

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
        SELECT students.name, students.email, internships.title, internships.company
        FROM applications
        JOIN students ON applications.student_email = students.email
        JOIN internships ON applications.job_id = internships.id
        WHERE applications.id = ?
    ''', (app_id,))
    rec = cursor.fetchone()
    conn.close()

    if rec:
        dispatch_notification_email(rec['email'], rec['name'], rec['title'], rec['company'], new_status)

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

# --- LIVE ONLINE OPENINGS WITH COMPANY LOGOS ---

@app.route('/live_jobs')
def live_jobs():
    indian_internships = [
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
            "title": "Software Engineer Intern (Digital)",
            "company_name": "TCS (Tata Consultancy Services)",
            "candidate_required_location": "Pan India / Remote",
            "job_type": "Corporate Internship",
            "tags": ["Python", "SQL", "C", "DSA"],
            "url": "https://www.tcs.com/careers",
            "logo": get_company_logo("TCS"),
            "is_indian": True
        },
        {
            "title": "InStep Software Trainee",
            "company_name": "Infosys Technologies",
            "candidate_required_location": "Bengaluru / Pune, India",
            "job_type": "Flagship Internship",
            "tags": ["Python", "Cloud", "SQL", "Flask"],
            "url": "https://www.infosys.com/careers.html",
            "logo": get_company_logo("Infosys"),
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
            "title": "Backend Systems Intern",
            "company_name": "Razorpay India",
            "candidate_required_location": "Bengaluru / Remote India",
            "job_type": "Fintech Internship",
            "tags": ["Python", "SQL", "REST APIs", "Docker"],
            "url": "https://razorpay.com/jobs/",
            "logo": get_company_logo("Razorpay"),
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
        }
    ]

    external_jobs = []
    try:
        response = requests.get('https://remotive.com/api/remote-jobs?category=software-dev&limit=12', timeout=4)
        if response.status_code == 200:
            data = response.json()
            for rj in data.get('jobs', []):
                c_name = rj.get('company_name', '')
                api_logo = rj.get('company_logo') or rj.get('company_logo_url')
                external_jobs.append({
                    "title": rj.get('title'),
                    "company_name": c_name,
                    "candidate_required_location": rj.get('candidate_required_location') or 'Worldwide',
                    "job_type": rj.get('job_type') or 'Remote Tech',
                    "tags": rj.get('tags', [])[:5],
                    "url": rj.get('url'),
                    "logo": get_company_logo(c_name, api_logo),
                    "is_indian": False
                })
    except Exception as e:
        print(f"Error fetching live jobs: {e}")

    return render_template('live_jobs.html', jobs=indian_internships + external_jobs)

# --- MULTI-LANGUAGE YOUTUBE COURSES HUB ---

@app.route('/courses')
def courses():
    curated_courses = [
        # --- C LANGUAGE ---
        {
            "title": "Complete C Language & Pointers Masterclass (One Shot)",
            "provider": "CodeWithHarry",
            "platform": "YouTube",
            "tag": "C",
            "language": "Hindi",
            "level": "Beginner to Advanced",
            "duration": "15 Hours Full Video",
            "type": "YouTube Lecture",
            "url": "https://www.youtube.com/watch?v=ZSPZob_1TOk",
            "desc": "Master C syntax, memory allocation, pointers, arrays, strings, file I/O, and structures with hands-on problem sets."
        },
        {
            "title": "C Programming Tutorial for Beginners (Full Course)",
            "provider": "Apna College (Shradha Khapra)",
            "platform": "YouTube",
            "tag": "C",
            "language": "Hindi",
            "level": "Beginner",
            "duration": "10 Hours One-Shot",
            "type": "YouTube Lecture",
            "url": "https://www.youtube.com/watch?v=irqbmMNs2Bo",
            "desc": "College-exam and placement-focused C programming course covering loops, recursion, pointers, and dynamic memory."
        },
        {
            "title": "CS50's Introduction to Computer Science",
            "provider": "Harvard University / edX",
            "platform": "Harvard / YouTube",
            "tag": "C",
            "language": "English",
            "level": "Beginner to Intermediate",
            "duration": "10 Weeks Series",
            "type": "Free Certificate",
            "url": "https://cs50.harvard.edu/x/",
            "desc": "Harvard's flagship CS course covering low-level C memory management, algorithms, data structures, and web basics."
        },

        # --- C++ ---
        {
            "title": "Complete C++ Placement & DSA Course",
            "provider": "CodeHelp (Love Babbar)",
            "platform": "YouTube",
            "tag": "C++",
            "language": "Hindi",
            "level": "Placement Ready",
            "duration": "140+ Lectures Playlist",
            "type": "YouTube Playlist",
            "url": "https://www.youtube.com/playlist?list=PLDzeHZWIZsTryvtXdMr6rPh4IDexB5NIA",
            "desc": "Comprehensive C++ STL, Object-Oriented Programming (OOPs), recursion, and competitive programming preparation."
        },
        {
            "title": "C++ Full Course for Free (6 Hours)",
            "provider": "Bro Code",
            "platform": "YouTube",
            "tag": "C++",
            "language": "English",
            "level": "Beginner to Intermediate",
            "duration": "6 Hours Full Video",
            "type": "YouTube Lecture",
            "url": "https://www.youtube.com/watch?v=-TkoO8Z07hI",
            "desc": "Fast-paced, zero-fluff C++ tutorial covering pointers, dynamic memory, classes, inheritance, and polymorphism."
        },

        # --- PYTHON ---
        {
            "title": "Chai aur Python (Complete Backend & Core Python)",
            "provider": "Chai aur Code (Hitesh Choudhary)",
            "platform": "YouTube",
            "tag": "Python",
            "language": "Hindi",
            "level": "Beginner to Intermediate",
            "duration": "Complete Playlist",
            "type": "YouTube Playlist",
            "url": "https://www.youtube.com/playlist?list=PLu71SKxNbfoBsMugTFALhdOoZ5OqCGt7j",
            "desc": "Deep dive into Python internals, virtual environments, decorators, generators, SQLite/MongoDB, and API handling."
        },
        {
            "title": "100 Days of Code: Complete Python Pro Playlist",
            "provider": "CodeWithHarry",
            "platform": "YouTube",
            "tag": "Python",
            "language": "Hindi",
            "level": "Beginner to Pro",
            "duration": "100 Videos Playlist",
            "type": "YouTube Playlist",
            "url": "https://www.youtube.com/playlist?list=PLu0W_9lII9agwh1XjRt242xIpHhPT2llg",
            "desc": "Step-by-step daily Python lessons with real-world automation projects, OOPs, Flask basics, and exercises."
        },
        {
            "title": "Python for Beginners — Full Masterclass",
            "provider": "Programming with Mosh",
            "platform": "YouTube",
            "tag": "Python",
            "language": "English",
            "level": "Beginner",
            "duration": "6 Hours Full Video",
            "type": "YouTube Lecture",
            "url": "https://www.youtube.com/watch?v=_uQrJ0TkZlc",
            "desc": "Hands-on Python introduction covering data structures, modules, packages, and building automation & web scripts."
        },

        # --- JAVA ---
        {
            "title": "Java + DSA + Interview Preparation Bootcamp",
            "provider": "Kunal Kushwaha",
            "platform": "YouTube",
            "tag": "Java",
            "language": "English",
            "level": "Beginner to Advanced",
            "duration": "Complete Bootcamp",
            "type": "YouTube Playlist",
            "url": "https://www.youtube.com/playlist?list=PL9gnSGHSqcnr_DxHsP7AW9ftq0AtAyYqJ",
            "desc": "Industry-standard Java OOPs, Collections Framework, Git, and complete Data Structures & Algorithms bootcamp."
        },
        {
            "title": "Java Complete Placement Course (OOPs & Collections)",
            "provider": "Apna College",
            "platform": "YouTube",
            "tag": "Java",
            "language": "Hindi",
            "level": "Beginner to Placement",
            "duration": "Complete Playlist",
            "type": "YouTube Playlist",
            "url": "https://www.youtube.com/playlist?list=PLfqMhTWNBTe3LtFWcvwpqTkUSlB32kJop",
            "desc": "Structured Java course covering core syntax, object-oriented principles, exception handling, and interview coding problems."
        },

        # --- JAVASCRIPT & WEB DEV ---
        {
            "title": "Chai aur JavaScript (Complete Modern JS)",
            "provider": "Chai aur Code (Hitesh Choudhary)",
            "platform": "YouTube",
            "tag": "JavaScript",
            "language": "Hindi",
            "level": "Beginner to Advanced",
            "duration": "Complete Playlist",
            "type": "YouTube Playlist",
            "url": "https://www.youtube.com/playlist?list=PLu71SKxNbfoBuX3f4EOACle2y-tRC5Q37",
            "desc": "Master JS execution context, closures, Promises, Async/Await, Fetch API, DOM projects, and ES6+ internals."
        },
        {
            "title": "Complete Web Development Bootcamp (HTML, CSS, JS)",
            "provider": "Chai aur Code",
            "platform": "YouTube",
            "tag": "HTML/CSS",
            "language": "Hindi",
            "level": "Beginner",
            "duration": "Full Playlist",
            "type": "YouTube Playlist",
            "url": "https://www.youtube.com/playlist?list=PLu71SKxNbfoDBNF5s-WH6QBD3Ia43RR28",
            "desc": "Build responsive websites from scratch with semantic HTML5, modern CSS3 Flexbox & Grid, Tailwind, and DOM logic."
        },
        {
            "title": "JavaScript Full Course for Free (12 Hours)",
            "provider": "Bro Code",
            "platform": "YouTube",
            "tag": "JavaScript",
            "language": "English",
            "level": "Beginner to Intermediate",
            "duration": "12 Hours Full Video",
            "type": "YouTube Lecture",
            "url": "https://www.youtube.com/watch?v=lfmg-EJ8gm4",
            "desc": "Complete project-driven JavaScript course covering ES6 modules, asynchronous programming, and interactive web apps."
        },

        # --- REACT ---
        {
            "title": "Chai aur React — Complete Frontend Framework Series",
            "provider": "Chai aur Code (Hitesh Choudhary)",
            "platform": "YouTube",
            "tag": "React",
            "language": "Hindi",
            "level": "Intermediate",
            "duration": "Complete Playlist",
            "type": "YouTube Playlist",
            "url": "https://www.youtube.com/playlist?list=PLu71SKxNbfoDqgPchmvIsL4hTnJIrtige",
            "desc": "Learn React hooks, Virtual DOM, Context API, Redux Toolkit, React Router, and production app architecture."
        },

        # --- DSA ---
        {
            "title": "Striver's A2Z DSA Complete Placement Course",
            "provider": "take U forward (Striver)",
            "platform": "YouTube",
            "tag": "DSA",
            "language": "Hinglish",
            "level": "Placement Ready",
            "duration": "450+ Problems Playlist",
            "type": "YouTube Playlist",
            "url": "https://www.youtube.com/playlist?list=PLgUwDviBIf0oF6QL8m22w1hIDC1vJ_BHz",
            "desc": "Gold-standard DSA series for TCS Digital, Infosys, Amazon, and Google—covering Arrays, Trees, Graphs, and DP."
        },

        # --- SQL & DATABASES ---
        {
            "title": "Complete SQL & Relational Database Course (One Shot)",
            "provider": "Apna College",
            "platform": "YouTube",
            "tag": "SQL",
            "language": "Hindi",
            "level": "Beginner to Intermediate",
            "duration": "3.5 Hours One-Shot",
            "type": "YouTube Lecture",
            "url": "https://www.youtube.com/watch?v=hlGoQC332VM",
            "desc": "Learn SQL commands, constraints, primary/foreign keys, Joins, subqueries, and DBMS interview questions."
        },
        {
            "title": "SQL Tutorial — Full Database Course for Beginners",
            "provider": "freeCodeCamp.org",
            "platform": "YouTube",
            "tag": "SQL",
            "language": "English",
            "level": "Beginner",
            "duration": "4.5 Hours Full Video",
            "type": "YouTube Lecture",
            "url": "https://www.youtube.com/watch?v=HXV3zeQKqGY",
            "desc": "Step-by-step relational database design, CRUD queries, aggregate functions, and schema normalization."
        },

        # --- GIT & GITHUB ---
        {
            "title": "Complete Git & GitHub Masterclass in One Shot",
            "provider": "Chai aur Code (Hitesh Choudhary)",
            "platform": "YouTube",
            "tag": "Git",
            "language": "Hindi",
            "level": "Essential Tool",
            "duration": "Full Masterclass",
            "type": "YouTube Lecture",
            "url": "https://www.youtube.com/watch?v=q8EevlEpQ2A",
            "desc": "Master version control, branching, merge conflicts, pull requests, and open-source GitHub workflows."
        }
    ]
    return render_template('courses.html', courses=curated_courses)

@app.route('/developers')
def developers():
    return render_template('developers.html')

@app.route('/matcher', methods=['GET', 'POST'])
def matcher():
    score = None
    matched_skills = []
    missing_skills = []
    student_skills_raw = ""
    job_skills_raw = ""

    if 'student_email' in session and request.method == 'GET':
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT skills FROM students WHERE email = ?', (session['student_email'],))
        row = cursor.fetchone()
        conn.close()
        if row and row['skills']:
            student_skills_raw = row['skills']

    if request.method == 'POST':
        student_skills_raw = request.form.get('student_skills', '').strip()
        job_skills_raw = request.form.get('job_skills', '').strip()

        s_list = [s.strip() for s in student_skills_raw.split(',') if s.strip()]
        j_list = [s.strip() for s in job_skills_raw.split(',') if s.strip()]

        s_lower = {s.lower(): s for s in s_list}
        j_lower = {j.lower(): j for j in j_list}

        if j_list:
            matched_keys = set(s_lower.keys()).intersection(set(j_lower.keys()))
            missing_keys = set(j_lower.keys()) - set(s_lower.keys())

            matched_skills = [j_lower[k] for k in matched_keys]
            missing_skills = [j_lower[k] for k in missing_keys]
            score = round((len(matched_keys) / len(j_list)) * 100)
        else:
            score = 0

    return render_template('index.html', score=score,
                           matched_skills=matched_skills,
                           missing_skills=missing_skills,
                           student_skills_raw=student_skills_raw,
                           job_skills_raw=job_skills_raw)

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

    try:
        cursor.execute('''
            SELECT internships.id, internships.title, internships.company, internships.required_skills, applications.status, applications.quiz_score
            FROM internships
            JOIN applications ON internships.id = applications.job_id
            WHERE applications.student_email = ?
            ORDER BY applications.id DESC
        ''', (session['student_email'],))
        applied_jobs = cursor.fetchall()
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
    email = data.get('email')
    action = data.get('action')

    if not email:
        return jsonify({"status": "error", "message": "Email is required"}), 400

    conn = get_db()
    cursor = conn.cursor()

    if action == 'register':
        name = data.get('name', 'Student')
        skills = data.get('skills', '')
        try:
            cursor.execute('INSERT INTO students (name, email, skills) VALUES (?, ?, ?)', (name, email, skills))
            conn.commit()
        except sqlite3.IntegrityError:
            cursor.execute('UPDATE students SET name = ?, skills = ? WHERE email = ?', (name, skills, email))
            conn.commit()
        session['student_name'] = name
        session['student_email'] = email

    elif action == 'login':
        cursor.execute('SELECT name FROM students WHERE email = ?', (email,))
        student = cursor.fetchone()
        session['student_name'] = student['name'] if student else email.split('@')[0]
        session['student_email'] = email

    conn.close()
    return jsonify({"status": "success"})

@app.route('/add_job', methods=['GET', 'POST'])
def add_job():
    error = None
    if request.method == 'POST':
        pin = request.form.get('admin_pin', '').strip()
        title = request.form.get('title', '').strip()
        company = request.form.get('company', '').strip()
        required_skills = request.form.get('required_skills', '').strip()
        deadline = request.form.get('deadline', 'Nov 30, 2026').strip()

        if pin != ADMIN_PASSKEY:
            error = "Invalid Coordinator Passkey. Only authorized officers can post listings."
            return render_template('add_job.html', error=error)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO internships (title, company, required_skills, deadline) VALUES (?, ?, ?, ?)', 
                       (title, company, required_skills, deadline))
        conn.commit()
        conn.close()
        return redirect(url_for('jobs'))

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

if __name__ == '__main__':
    app.run(debug=True)