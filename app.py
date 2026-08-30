from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, g
import qrcode
import os
import sqlite3
from datetime import datetime, date
import sms_service
from zoneinfo import ZoneInfo

app = Flask(__name__)
app.secret_key = 'superdupersecretkey123'
DATABASE = 'qr_attendance.db'
LATE_CUTOFF = "08:00:00"
SCHOOL_NAME = "San Miguel Elementary School"
GRADE_LEVEL = "Grade 6"

PH_TZ = ZoneInfo('Asia/Manila')

def get_ph_date():
    return datetime.now(PH_TZ).date()

def get_ph_datetime():
    return datetime.now(PH_TZ)

def format_time_12hr(time_24):
    if not time_24:
        return ""
    try:
        return datetime.strptime(time_24, "%H:%M:%S").strftime("%I:%M:%S %p")
    except:
        return time_24

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE, timeout=10.0)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT "Teacher", status TEXT DEFAULT "pending")')
        db.execute('CREATE TABLE IF NOT EXISTS students (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, grade_section TEXT NOT NULL, parent_name TEXT, parent_contact TEXT, qr_code_path TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, date TEXT NOT NULL, time_in TEXT, time_out TEXT, status TEXT DEFAULT "Present", scanned_by TEXT, FOREIGN KEY (student_id) REFERENCES students(student_id))')
        db.execute('CREATE TABLE IF NOT EXISTS teachers (id INTEGER PRIMARY KEY AUTOINCREMENT, teacher_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, subject TEXT, contact TEXT)')
        cur = db.execute("SELECT * FROM users WHERE username='admin'")
        if not cur.fetchone():
            db.execute("INSERT INTO users (username, password, role, status) VALUES ('admin', 'admin123', 'Admin', 'approved')")
        db.commit()

init_db()

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']; password = request.form['password']
        db = get_db(); cur = db.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password)); user = cur.fetchone()
        if user:
            if user['status']!= 'approved':
                flash('Your account is pending approval by Admin', 'warning'); return redirect(url_for('login'))
            session['logged_in'] = True; session['username'] = user['username']; session['role'] = user['role']; flash(f'Welcome back, {user["username"]}!', 'success'); return redirect(url_for('dashboard'))
        else:
            flash('Invalid Username or Password', 'danger')
    return render_template('login.html', school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/register_user', methods=['GET', 'POST'])
def register_user():
    if request.method == 'POST':
        username = request.form['username']; password = request.form['password']; role = request.form['role']; db = get_db()
        try:
            db.execute("INSERT INTO users(username, password, role, status) VALUES(?,?,?, 'pending')", (username, password, role)); db.commit(); flash('Registration Sent! Wait for Admin approval.', 'success'); return redirect(url_for('login'))
        except:
            flash('Username already exists', 'danger')
    return render_template('register_user.html', school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/approvals')
def approvals():
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Admin access only', 'danger'); return redirect(url_for('login'))
    db = get_db(); pending_users = db.execute("SELECT * FROM users WHERE status='pending'").fetchall()
    return render_template('approvals.html', users=pending_users, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/approve_user/<int:id>')
def approve_user(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Admin access only', 'danger'); return redirect(url_for('login'))
    db = get_db(); db.execute("UPDATE users SET status='approved' WHERE id=?", [id]); db.commit(); flash('User Approved Successfully', 'success'); return redirect(url_for('approvals'))

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    today_str = get_ph_date().isoformat(); db = get_db()
    total = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    present_today = db.execute("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date=? AND time_in IS NOT NULL", [today_str]).fetchone()[0]
    late_today = db.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Late'", [today_str]).fetchone()[0]
    absent_today = 0 if present_today == 0 else total - present_today
    recent = db.execute("SELECT s.name, a.time_in, a.status, a.scanned_by FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=? ORDER BY a.id DESC LIMIT 5", [today_str]).fetchall()
    return render_template('dashboard.html', total=total, present=present_today, late=late_today, absent=absent_today, recent=recent, school=SCHOOL_NAME, grade=GRADE_LEVEL, today=get_ph_date(), format_time=format_time_12hr)

@app.route('/students')
def students():
    if not session.get('logged_in') or session['role']!= 'Admin':
        return redirect(url_for('login'))
    db = get_db(); students = db.execute("SELECT * FROM students").fetchall()
    return render_template('students.html', students=students, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/teachers')
def teachers():
    if not session.get('logged_in') or session['role']!= 'Admin':
        return redirect(url_for('login'))
    db = get_db(); teachers = db.execute("SELECT * FROM teachers ORDER BY name").fetchall()
    return render_template('teachers.html', teachers=teachers, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/register_teacher', methods=['GET', 'POST'])
def register_teacher():
    if not session.get('logged_in') or session['role']!= 'Admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        teacher_id = request.form['teacher_id']; name = request.form['name']; subject = request.form['subject']; contact = request.form['contact']
        db = get_db()
        try:
            db.execute("INSERT INTO teachers(teacher_id, name, subject, contact) VALUES(?,?,?,?)", (teacher_id, name, subject, contact)); db.commit(); flash('Teacher Registered Successfully', 'success'); return redirect(url_for('teachers'))
        except sqlite3.IntegrityError:
            flash('Error: Teacher ID already exists', 'danger')
    return render_template('register_teacher.html', school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/edit_teacher/<int:id>', methods=['GET', 'POST'])
def edit_teacher(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only.', 'danger'); return redirect(url_for('dashboard'))
    db = get_db()
    teacher = db.execute("SELECT * FROM teachers WHERE id =?", (id,)).fetchone()
    if request.method == 'POST':
        teacher_id = request.form['teacher_id']; name = request.form['name']; subject = request.form['subject']; contact = request.form['contact']
        try:
            db.execute("UPDATE teachers SET teacher_id=?, name=?, subject=?, contact=? WHERE id=?", (teacher_id, name, subject, contact, id)); db.commit(); flash(f'Teacher {name} updated successfully.', 'success'); return redirect(url_for('teachers'))
        except sqlite3.IntegrityError:
            flash('Error: Teacher ID already exists', 'danger')
    return render_template('edit_teacher.html', teacher=teacher, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/delete_teacher/<int:id>', methods=['POST'])
def delete_teacher(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only.', 'danger'); return redirect(url_for('dashboard'))
    db = get_db()
    try:
        db.execute("DELETE FROM teachers WHERE id =?", (id,)); db.commit(); flash('Teacher deleted successfully.', 'success')
    except Exception as e:
        db.rollback(); flash(f'Error deleting teacher: {e}', 'danger')
    return redirect(url_for('teachers'))

@app.route('/register_student', methods=['GET', 'POST'])
def register_student():
    if not session.get('logged_in') or session['role']!= 'Admin':
        return redirect(url_for('login'))
    qr_path = None; student_name = None; student_id = None
    if request.method == 'POST':
        student_id = request.form['student_id']; name = request.form['name']; section = request.form['grade_section']; pname = request.form['parent_name']; pcontact = request.form['parent_contact']
        os.makedirs('static/qr_codes', exist_ok=True); qr = qrcode.make(student_id); qr_filename = f"qr_codes/{student_id}.png"; qr_full_path = f"static/{qr_filename}"; qr.save(qr_full_path)
        db = get_db()
        try:
            db.execute("INSERT INTO students(student_id, name, grade_section, parent_name, parent_contact, qr_code_path) VALUES(?,?,?,?,?,?)", (student_id, name, section, pname, pcontact, qr_filename)); db.commit(); flash('Student Registered Successfully', 'success'); qr_path = qr_filename; student_name = name
        except sqlite3.IntegrityError:
            flash('Error: Student ID already exists', 'danger')
    return render_template('register_student.html', school=SCHOOL_NAME, grade=GRADE_LEVEL, qr_path=qr_path, student_name=student_name, student_id=student_id)

@app.route('/scanner')
def scanner():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('scanner.html', school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/scan', methods=['POST'])
def scan():
    data = request.get_json(); student_id = data['student_id']; scan_type = data.get('scan_type', 'in')
    now = get_ph_datetime(); today_str = get_ph_date().isoformat(); cur_time_24 = now.strftime("%H:%M:%S"); cur_time_12 = now.strftime("%I:%M:%S %p"); db = get_db()
    student = db.execute("SELECT * FROM students WHERE student_id=?", [student_id]).fetchone()
    if not student:
        return jsonify({'status': 'error', 'message': 'Student not found'})
    record = db.execute("SELECT * FROM attendance WHERE student_id=? AND date=?", (student_id, today_str)).fetchone()
    status = 'Late' if cur_time_24 > LATE_CUTOFF else 'Present'; message = ""
    if scan_type == 'in':
        if record and record['time_in']:
            return jsonify({'status': 'error', 'message': f'{student["name"]} already TIMED IN at {format_time_12hr(record["time_in"])}'})
        if not record:
            db.execute("INSERT INTO attendance(student_id, date, time_in, status, scanned_by) VALUES(?,?,?,?,?)", (student_id, today_str, cur_time_24, status, session['username']))
        else:
            db.execute("UPDATE attendance SET time_in=?, status=? WHERE id=?", (cur_time_24, status, record['id']))
        message = f"{student['name']} TIME IN: {cur_time_12} - {status}"
    elif scan_type == 'out':
        if not record or not record['time_in']:
            return jsonify({'status': 'error', 'message': f'{student["name"]} has no TIME IN record yet'})
        if record['time_out']:
            return jsonify({'status': 'error', 'message': f'{student["name"]} already TIMED OUT at {format_time_12hr(record["time_out"])}'})
        db.execute("UPDATE attendance SET time_out=? WHERE id=?", (cur_time_24, record['id'])); message = f"{student['name']} TIME OUT: {cur_time_12}"
    db.commit()
    sms_service.send_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
    return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})

@app.route('/attendance')
def attendance():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    db = get_db()
    filter_date = request.args.get('date', get_ph_date().isoformat())
    all_dates = db.execute("SELECT DISTINCT date FROM attendance ORDER BY date DESC").fetchall()
    records = db.execute("SELECT a.*, s.name FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=? ORDER BY a.time_in DESC", [filter_date]).fetchall()
    return render_template('attendance.html', records=records, all_dates=all_dates, filter_date=filter_date, school=SCHOOL_NAME, grade=GRADE_LEVEL, format_time=format_time_12hr)

@app.route('/reports')
def reports():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    today_str = get_ph_date().isoformat(); db = get_db()
    total = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    present_today = db.execute("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date=? AND time_in IS NOT NULL", [today_str]).fetchone()[0]
    late_today = db.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Late'", [today_str]).fetchone()[0]
    absent_today = 0 if present_today == 0 else total - present_today
    return render_template('reports.html', total=total, present=present_today, late=late_today, absent=absent_today, school=SCHOOL_NAME, grade=GRADE_LEVEL, today=get_ph_date())

@app.route('/reset_attendance', methods=['POST'])
def reset_attendance():
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized Access', 'danger'); return redirect(url_for('login'))
    db = get_db(); db.execute("DELETE FROM attendance"); db.commit(); flash('All Attendance Records Have Been Reset Successfully', 'success')
    return redirect(url_for('attendance'))

@app.route('/delete_student/<student_id>', methods=['POST'])
def delete_student(student_id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only.', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    try:
        db.execute("DELETE FROM attendance WHERE student_id =?", (student_id,))
        db.execute("DELETE FROM students WHERE student_id =?", (student_id,))
        db.commit()
        qr_file = f"static/qr_codes/{student_id}.png"
        if os.path.exists(qr_file):
            os.remove(qr_file)
        flash(f'Student {student_id} deleted successfully.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error deleting student: {e}', 'danger')
    return redirect(url_for('students'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/edit_student/<student_id>', methods=['GET', 'POST'])
def edit_student(student_id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only.', 'danger'); return redirect(url_for('dashboard'))
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE student_id =?", (student_id,)).fetchone()
    if request.method == 'POST':
        name = request.form['name']; section = request.form['grade_section']; pname = request.form['parent_name']; pcontact = request.form['parent_contact']
        db.execute("UPDATE students SET name=?, grade_section=?, parent_name=?, parent_contact=? WHERE student_id=?", (name, section, pname, pcontact, student_id)); db.commit(); flash(f'Student {name} updated successfully.', 'success'); return redirect(url_for('students'))
    return render_template('edit_student.html', student=student, school=SCHOOL_NAME, grade=GRADE_LEVEL)

if __name__ == '__main__':
    app.run(debug=True)
