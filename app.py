import sqlite3
import requests
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    skills TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS internships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    required_skills TEXT NOT NULL)''')
    # NEW: Table to track who applied to what
    c.execute('''CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_email TEXT NOT NULL,
                    job_id INTEGER NOT NULL)''')
    conn.commit()
    conn.close()

init_db()

# --- Skill Matching Logic ---
def get_match_score(student_skills, job_skills):
    s_skills = [s.strip().lower() for s in student_skills.split(',') if s.strip()]
    j_skills = [j.strip().lower() for j in job_skills.split(',') if j.strip()]
    if not j_skills: return 0
    match_count = sum(1 for skill in j_skills if skill in s_skills)
    return int((match_count * 100) / len(j_skills))

# --- Routes ---
@app.route('/', methods=['GET', 'POST'])
def home():
    score = None
    if request.method == 'POST':
        student_skills = request.form['student_skills']
        job_skills = request.form['job_skills']
        score = get_match_score(student_skills, job_skills)
    return render_template('index.html', score=score)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        skills = request.form['skills']
        try:
            conn = sqlite3.connect('database.db')
            c = conn.cursor()
            c.execute('INSERT INTO students (name, email, skills) VALUES (?, ?, ?)', (name, email, skills))
            conn.commit()
            conn.close()
            return f"<h3>Registration Successful!</h3><a href='/jobs'>View Available Jobs</a>"
        except sqlite3.IntegrityError:
            return "<h3>Error: Email already registered.</h3><a href='/register'>Try again</a>"
    return render_template('register.html')

@app.route('/add_job', methods=['GET', 'POST'])
def add_job():
    if request.method == 'POST':
        title = request.form['title']
        company = request.form['company']
        required_skills = request.form['required_skills']
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute('INSERT INTO internships (title, company, required_skills) VALUES (?, ?, ?)', (title, company, required_skills))
        conn.commit()
        conn.close()
        return redirect(url_for('jobs'))
    return render_template('add_job.html')

@app.route('/jobs')
def jobs():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('SELECT * FROM internships')
    internships = c.fetchall()
    conn.close()
    return render_template('jobs.html', internships=internships)

# NEW: Apply for a job
@app.route('/apply/<int:job_id>', methods=['GET', 'POST'])
def apply(job_id):
    if request.method == 'POST':
        email = request.form['email']
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        
        # Check if student exists
        c.execute('SELECT * FROM students WHERE email = ?', (email,))
        if not c.fetchone():
            return "<h3>Error: Email not found. <a href='/register'>Register first</a></h3>"
            
        # Record the application
        c.execute('INSERT INTO applications (student_email, job_id) VALUES (?, ?)', (email, job_id))
        conn.commit()
        conn.close()
        return "<h3>Successfully applied!</h3><a href='/jobs'>Back to Jobs</a>"
        
    return render_template('apply.html', job_id=job_id)

@app.route('/live_jobs')
def live_jobs():
    external_jobs = []
    try:
        # Fetch up to 20 open software/tech jobs from Remotive API
        response = requests.get('https://remotive.com/api/remote-jobs?category=software-dev&limit=20', timeout=6)
        if response.status_code == 200:
            data = response.json()
            external_jobs = data.get('jobs', [])
    except Exception as e:
        print(f"Error fetching live jobs: {e}")

    return render_template('live_jobs.html', jobs=external_jobs)

# NEW: View applicants for a specific job
@app.route('/view_applicants/<int:job_id>')
def view_applicants(job_id):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    
    # Get job title
    c.execute('SELECT title FROM internships WHERE id = ?', (job_id,))
    job_title = c.fetchone()[0]
    
    # Get all students who applied to this job_id
    c.execute('''
        SELECT students.name, students.email, students.skills 
        FROM applications 
        JOIN students ON applications.student_email = students.email 
        WHERE applications.job_id = ?
    ''', (job_id,))
    applicants = c.fetchall()
    conn.close()
    
    return render_template('view_applicants.html', applicants=applicants, job_title=job_title)

if __name__ == '__main__':
    app.run(debug=True)