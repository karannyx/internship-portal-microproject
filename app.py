import sqlite3
import requests
import time
from collections import Counter
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.secret_key = 'skill2career_production_secret_key_2026'
DB_NAME = 'database.db'

ADMIN_PASSKEY = "admin2026"

LAST_SYNC_TIME = 0
SYNC_INTERVAL = 300  

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            skills TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS internships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            required_skills TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_email TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            status TEXT DEFAULT 'Under Review'
        )
    ''')

    cursor.execute("PRAGMA table_info(applications)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'status' not in columns:
        cursor.execute("ALTER TABLE applications ADD COLUMN status TEXT DEFAULT 'Under Review'")

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
            ("Junior Software Trainee", "India Post (CEPT)", "Python, SQL, HTML"),
            ("Digital Banking Systems Trainee", "India Post Tech Cell", "Python, HTML, C"),
            ("Software Engineer Intern", "TCS Digital", "Python, SQL, C"),
            ("Data Analyst Intern", "Infosys Springboard", "Python, SQL")
        ]
        cursor.executemany('INSERT INTO internships (title, company, required_skills) VALUES (?, ?, ?)', openings)

    conn.commit()
    conn.close()

init_db()

# --- AUTOMATIC JOB SYNC ENGINE ---

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
                        cursor.execute('INSERT INTO internships (title, company, required_skills) VALUES (?, ?, ?)', 
                                       (title, company, skills))

            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Auto-sync note: {e}")

# --- MAIN & NAVIGATION ROUTES ---

@app.route('/')
def home():
    return redirect(url_for('jobs'))

@app.route('/jobs')
def jobs():
    sync_live_jobs_automatically()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, title, company, required_skills FROM internships ORDER BY id DESC')
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
        "Notice: India Post (CEPT) shortlisted candidates list updated for Technical Trainee roles.",
        "Pro-Tip: Complete Harvard CS50 in the Courses section to boost your Software Trainee match score to 100%."
    ]

    conn.close()
    return render_template('jobs.html', internships=internships, top_score=top_score, total_jobs=len(internships), announcements=announcements)

@app.route('/live_jobs')
def live_jobs():
    # 1. Verified Live Indian Tech & Govt Internships
    indian_internships = [
        {
            "title": "Junior Software Trainee (CEPT Tech)",
            "company_name": "India Post (CEPT / Dak Vibhag)",
            "candidate_required_location": "India (Bengaluru / Hybrid)",
            "job_type": "Govt Tech Trainee",
            "tags": ["Python", "SQL", "HTML", "C"],
            "url": "https://cept.gov.in/",
            "is_indian": True
        },
        {
            "title": "Software Engineer Intern (Digital)",
            "company_name": "TCS (Tata Consultancy Services)",
            "candidate_required_location": "Pan India / Remote",
            "job_type": "Corporate Internship",
            "tags": ["Python", "SQL", "C", "DSA"],
            "url": "https://www.tcs.com/careers/india",
            "is_indian": True
        },
        {
            "title": "InStep Software Trainee",
            "company_name": "Infosys Technologies",
            "candidate_required_location": "Bengaluru / Pune, India",
            "job_type": "Flagship Internship",
            "tags": ["Python", "Cloud", "SQL", "Flask"],
            "url": "https://www.infosys.com/instep/",
            "is_indian": True
        },
        {
            "title": "Full Stack Web Developer Apprentice",
            "company_name": "Zoho Corporation",
            "candidate_required_location": "Chennai / Remote India",
            "job_type": "Product Internship",
            "tags": ["HTML", "React", "JavaScript", "SQL"],
            "url": "https://www.zoho.com/careers/",
            "is_indian": True
        },
        {
            "title": "Backend Systems Intern",
            "company_name": "Razorpay India",
            "candidate_required_location": "Bengaluru / Remote India",
            "job_type": "Fintech Internship",
            "tags": ["Python", "SQL", "REST APIs", "Docker"],
            "url": "https://razorpay.com/jobs/",
            "is_indian": True
        },
        {
            "title": "Student Research & Engineering Trainee",
            "company_name": "ISRO (Indian Space Research Organisation)",
            "candidate_required_location": "India (IIRS / SAC Dehradun)",
            "job_type": "Govt Research Trainee",
            "tags": ["Python", "C", "Data Analysis", "GIS"],
            "url": "https://www.isro.gov.in/Internship.html",
            "is_indian": True
        }
    ]

    # 2. Live Global Openings from Remotive API
    external_jobs = []
    try:
        response = requests.get('https://remotive.com/api/remote-jobs?category=software-dev&limit=12', timeout=4)
        if response.status_code == 200:
            data = response.json()
            raw_jobs = data.get('jobs', [])
            for rj in raw_jobs:
                external_jobs.append({
                    "title": rj.get('title'),
                    "company_name": rj.get('company_name'),
                    "candidate_required_location": rj.get('candidate_required_location') or 'Worldwide',
                    "job_type": rj.get('job_type') or 'Remote Tech',
                    "tags": rj.get('tags', [])[:5],
                    "url": rj.get('url'),
                    "is_indian": False
                })
    except Exception as e:
        print(f"Error fetching live jobs: {e}")

    # Combine: Indian internships highlighted on top + worldwide remote tech
    all_openings = indian_internships + external_jobs
    return render_template('live_jobs.html', jobs=all_openings)

@app.route('/courses')
def courses():
    curated_courses = [
        {
            "title": "CS50's Introduction to Computer Science",
            "provider": "Harvard University / edX",
            "tag": "C",
            "level": "Beginner to Intermediate",
            "duration": "10 Weeks",
            "type": "Free / Self-Paced",
            "url": "https://cs50.harvard.edu/x/",
            "desc": "Foundational programming course covering C, memory, data structures, algorithms, and web basics."
        },
        {
            "title": "Scientific Computing with Python",
            "provider": "freeCodeCamp",
            "tag": "Python",
            "level": "Beginner",
            "duration": "300 Hours",
            "type": "Free Certificate",
            "url": "https://www.freecodecamp.org/learn/scientific-computing-with-python/",
            "desc": "Hands-on projects covering Python data structures, algorithms, OOP, and budget tracking."
        },
        {
            "title": "Responsive Web Design Certification",
            "provider": "freeCodeCamp",
            "tag": "HTML",
            "level": "Beginner",
            "duration": "300 Hours",
            "type": "Free Certificate",
            "url": "https://www.freecodecamp.org/learn/2022/responsive-web-design/",
            "desc": "Learn modern HTML5 semantic markup, CSS3 Flexbox, Grid, and build real responsive projects."
        },
        {
            "title": "Intro to SQL & Relational Databases",
            "provider": "Kaggle Learn",
            "tag": "SQL",
            "level": "Beginner",
            "duration": "4 Hours",
            "type": "Free Certificate",
            "url": "https://www.kaggle.com/learn/intro-to-sql",
            "desc": "Master SQL queries, JOINs, GROUP BY, BigQuery integration, and data querying best practices."
        },
        {
            "title": "Full Stack Open: Modern Web Development",
            "provider": "University of Helsinki",
            "tag": "React",
            "level": "Intermediate",
            "duration": "Self-Paced",
            "type": "Free Course",
            "url": "https://fullstackopen.com/en/",
            "desc": "Industry-grade introduction to React, Redux, Node.js, REST APIs, GraphQL, and TypeScript."
        },
        {
            "title": "Learn Git Branching & Version Control",
            "provider": "Open Source Community",
            "tag": "Git",
            "level": "Beginner",
            "duration": "2 Hours",
            "type": "Interactive Guide",
            "url": "https://learngitbranching.js.org/",
            "desc": "Visual interactive tutorial for mastering Git commands, branches, rebasing, and collaboration."
        }
    ]
    return render_template('courses.html', courses=curated_courses)

@app.route('/apply/<int:job_id>', methods=['GET', 'POST'])
def apply(job_id):
    if 'student_email' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM applications WHERE student_email = ? AND job_id = ?', (session['student_email'], job_id))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO applications (student_email, job_id, status) VALUES (?, ?, ?)', 
                       (session['student_email'], job_id, 'Under Review'))
        conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

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
            {"q": "What is the difference between list and tuple in Python?", "a": "Lists are mutable (elements can be modified, appended, removed), while tuples are immutable once created. Tuples are faster and used for fixed data integrity."},
            {"q": "How does memory management work in Python?", "a": "Python uses a private heap containing all objects and data structures. It relies on Reference Counting and a generational Garbage Collector to detect and destroy cyclic references."},
            {"q": "Explain decorators and write a typical use case.", "a": "Decorators are functions that take another function as an argument and extend its behavior without modifying it directly (e.g. @app.route in Flask or authentication checks)."}
        ],
        'sql': [
            {"q": "Explain the difference between WHERE and HAVING clauses.", "a": "WHERE filters rows before aggregation occurs. HAVING filters aggregated data after the GROUP BY clause has executed."},
            {"q": "What are primary keys, foreign keys, and unique indexes?", "a": "A Primary Key uniquely identifies each record and cannot be NULL. A Foreign Key references a primary key in another table to enforce referential integrity."},
            {"q": "What is database normalization (1NF, 2NF, 3NF)?", "a": "Normalization organizes database columns and tables to minimize redundancy and prevent dependency anomalies during insert, update, and delete actions."}
        ],
        'html': [
            {"q": "What are Semantic HTML5 tags and why are they critical?", "a": "Tags like <header>, <nav>, <article>, <section>, and <footer> clearly describe their meaning to both browser and developer, improving accessibility and SEO rankings."},
            {"q": "Explain the CSS Box Model.", "a": "The Box Model consists of four concentric layers: Content, Padding, Border, and Margin."}
        ],
        'react': [
            {"q": "What is the Virtual DOM in React and how does reconciliation work?", "a": "Virtual DOM is a lightweight memory copy of the real DOM. When state changes, React compares Virtual DOM with the previous snapshot (diffing algorithm) and only updates changed nodes in the real DOM."},
            {"q": "What is the purpose of useEffect dependency array?", "a": "It specifies when the effect should re-run. An empty array [] means it runs only once on mount; passing variables means it re-runs whenever those state values change."}
        ],
        'c': [
            {"q": "What is a Pointer and how does dereferencing work in C?", "a": "A pointer is a variable that stores the memory address of another variable. The dereference operator (*) accesses the value stored at that address."},
            {"q": "What is the difference between malloc() and calloc()?", "a": "malloc() allocates uninitialized memory containing garbage values; calloc() allocates memory and initializes all allocated bytes to zero."}
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
            {"skill": "CORE CS", "q": "Explain the Time and Space Complexity of QuickSort and MergeSort.", "a": "MergeSort is O(N log N) in all cases with O(N) auxiliary space. QuickSort average time is O(N log N) but worst case is O(N^2) if pivot selection is unoptimized."},
            {"skill": "CORE CS", "q": "What are ACID properties in transactional systems?", "a": "Atomicity, Consistency, Isolation, and Durability."}
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

@app.route('/update_status/<int:app_id>', methods=['POST'])
def update_status(app_id):
    new_status = request.form.get('status', 'Under Review')
    job_id = request.form.get('job_id')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE applications SET status = ? WHERE id = ?', (new_status, app_id))
    conn.commit()
    conn.close()
    return redirect(url_for('view_applicants', job_id=job_id))

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'student_email' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        updated_skills = request.form.get('skills', '').strip()
        cursor.execute('UPDATE students SET skills = ? WHERE email = ?', (updated_skills, session['student_email']))
        conn.commit()

    cursor.execute('SELECT name, email, skills FROM students WHERE email = ?', (session['student_email'],))
    student = cursor.fetchone()

    cursor.execute('''
        SELECT internships.id, internships.title, internships.company, internships.required_skills, applications.status
        FROM internships
        JOIN applications ON internships.id = applications.job_id
        WHERE applications.student_email = ?
        ORDER BY applications.id DESC
    ''', (session['student_email'],))
    applied_jobs = cursor.fetchall()

    cursor.execute('''
        SELECT internships.id, internships.title, internships.company, internships.required_skills
        FROM internships
        JOIN saved_jobs ON internships.id = saved_jobs.job_id
        WHERE saved_jobs.student_email = ?
        ORDER BY saved_jobs.id DESC
    ''', (session['student_email'],))
    saved_jobs = cursor.fetchall()

    conn.close()
    return render_template('dashboard.html', student=student, applied_jobs=applied_jobs, saved_jobs=saved_jobs)

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
    cursor.execute('SELECT name, email, skills FROM students WHERE email = ?', (session['student_email'],))
    student = cursor.fetchone()

    cursor.execute('SELECT * FROM student_resumes WHERE student_email = ?', (session['student_email'],))
    resume_data = cursor.fetchone()
    conn.close()

    if not resume_data:
        return redirect(url_for('build_resume'))

    skills_list = [s.strip() for s in student['skills'].split(',') if s.strip()] if student and student['skills'] else []
    return render_template('resume.html', student=student, resume=resume_data, skills_list=skills_list)

@app.route('/matcher', methods=['GET', 'POST'])
def matcher():
    score = None
    if request.method == 'POST':
        student_skills = [s.strip().lower() for s in request.form.get('student_skills', '').split(',') if s.strip()]
        job_skills = [s.strip().lower() for s in request.form.get('job_skills', '').split(',') if s.strip()]
        if job_skills:
            matches = set(student_skills).intersection(set(job_skills))
            score = round((len(matches) / len(job_skills)) * 100, 2)
        else:
            score = 0
    return render_template('index.html', score=score)

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/register')
def register():
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('jobs'))

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

        if pin != ADMIN_PASSKEY:
            error = "Invalid Coordinator Passkey. Only authorized officers can post listings."
            return render_template('add_job.html', error=error)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO internships (title, company, required_skills) VALUES (?, ?, ?)', 
                       (title, company, required_skills))
        conn.commit()
        conn.close()
        return redirect(url_for('jobs'))

    return render_template('add_job.html', error=error)

@app.route('/view_applicants/<int:job_id>')
def view_applicants(job_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT title FROM internships WHERE id = ?', (job_id,))
    job = cursor.fetchone()
    job_title = job['title'] if job else "Unknown Role"

    cursor.execute('''
        SELECT applications.id as app_id, students.name, students.email, students.skills, applications.status
        FROM students
        JOIN applications ON students.email = applications.student_email
        WHERE applications.job_id = ?
        ORDER BY applications.id DESC
    ''', (job_id,))
    applicants = cursor.fetchall()
    conn.close()
    return render_template('view_applicants.html', applicants=applicants, job_title=job_title, job_id=job_id)

if __name__ == '__main__':
    app.run(debug=True)