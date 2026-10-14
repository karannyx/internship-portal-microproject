import sqlite3
import requests
import time
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.secret_key = 'skill2career_production_secret_key_2026'
DB_NAME = 'database.db'

# T&P Cell / Recruiter Passkey
ADMIN_PASSKEY = "admin2026"

# Auto-sync throttling (Har 5 minute mein background check)
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
            job_id INTEGER NOT NULL
        )
    ''')

    # Seed core Indian Govt & Tech roles if empty
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

# --- AUTOMATIC ONLINE JOB SYNC ENGINE ---

def sync_live_jobs_automatically():
    """Online publish hone wali nayi tech openings ko automatic portal mein laata hai."""
    global LAST_SYNC_TIME
    current_time = time.time()
    
    # Agar pichle 5 minute mein sync ho chuka hai, toh dobara API call na karein (Speed badhane ke liye)
    if current_time - LAST_SYNC_TIME < SYNC_INTERVAL:
        return

    LAST_SYNC_TIME = current_time
    try:
        # Real-time free tech openings feed
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
                
                # Format required skills
                skills = ", ".join(tags[:4]) if tags else "Python, HTML, SQL"

                if title and company:
                    # Check karein agar yeh job pehle se database mein toh nahi hai
                    cursor.execute('SELECT id FROM internships WHERE title = ? AND company = ?', (title, company))
                    if not cursor.fetchone():
                        cursor.execute('INSERT INTO internships (title, company, required_skills) VALUES (?, ?, ?)', 
                                       (title, company, skills))

            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Auto-sync background check note: {e}")

# --- MAIN & NAVIGATION ROUTES ---

@app.route('/')
def home():
    return redirect(url_for('jobs'))

@app.route('/jobs')
def jobs():
    # 1. Nayi online publish hui jobs ko auto-fetch aur insert karein
    sync_live_jobs_automatically()

    # 2. Database se latest saari openings nikaalein
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, title, company, required_skills FROM internships ORDER BY id DESC')
    raw_internships = cursor.fetchall()

    student_skills = []
    if 'student_email' in session:
        cursor.execute('SELECT skills FROM students WHERE email = ?', (session['student_email'],))
        row = cursor.fetchone()
        if row and row['skills']:
            student_skills = [s.strip().lower() for s in row['skills'].split(',') if s.strip()]

    internships = []
    top_score = 0
    for job in raw_internships:
        job_dict = dict(job)
        job_req = [s.strip() for s in job['required_skills'].split(',') if s.strip()]

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

    conn.close()
    return render_template('jobs.html', internships=internships, top_score=top_score, total_jobs=len(internships))

@app.route('/live_jobs')
def live_jobs():
    external_jobs = []
    try:
        response = requests.get('https://remotive.com/api/remote-jobs?category=software-dev&limit=20', timeout=6)
        if response.status_code == 200:
            data = response.json()
            external_jobs = data.get('jobs', [])
    except Exception as e:
        print(f"Error fetching live jobs: {e}")
    return render_template('live_jobs.html', jobs=external_jobs)

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

# --- STUDENT DASHBOARD ---

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
        SELECT internships.id, internships.title, internships.company, internships.required_skills
        FROM internships
        JOIN applications ON internships.id = applications.job_id
        WHERE applications.student_email = ?
        ORDER BY applications.id DESC
    ''', (session['student_email'],))
    applied_jobs = cursor.fetchall()
    conn.close()

    return render_template('dashboard.html', student=student, applied_jobs=applied_jobs)

# --- AUTHENTICATION & FIREBASE SYNC ---

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

# --- PROTECTED ADMIN JOB POSTING & APPLICATIONS ---

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

@app.route('/apply/<int:job_id>', methods=['GET', 'POST'])
def apply(job_id):
    if request.method == 'POST':
        email = request.form.get('email')
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO applications (student_email, job_id) VALUES (?, ?)', (email, job_id))
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard') if 'student_email' in session else url_for('jobs'))
    return render_template('apply.html', job_id=job_id)

@app.route('/view_applicants/<int:job_id>')
def view_applicants(job_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT title FROM internships WHERE id = ?', (job_id,))
    job = cursor.fetchone()
    job_title = job['title'] if job else "Unknown Role"

    cursor.execute('''
        SELECT students.name, students.email, students.skills 
        FROM students
        JOIN applications ON students.email = applications.student_email
        WHERE applications.job_id = ?
    ''', (job_id,))
    applicants = cursor.fetchall()
    conn.close()
    return render_template('view_applicants.html', applicants=applicants, job_title=job_title)

if __name__ == '__main__':
    app.run(debug=True)