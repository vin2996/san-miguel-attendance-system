from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, g
import qrcode
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date
import sms_service

# FIXED PART - Safe timezone
try:
    from zoneinfo import ZoneInfo
    try:
        PH_TZ = ZoneInfo('Asia/Manila')
    except Exception:
        PH_TZ = None
except ImportError:
    PH_TZ = None

app = Flask(__name__)
app.secret_key = 'superdupersecretkey123'

LATE_CUTOFF = "07:30:00"
LATE_CUTOFF_PM = "13:00:00"
SCHOOL_NAME = "San Miguel Elementary School"
GRADE_LEVEL = "Grade 6"

def get_ph_date():
    if PH_TZ:
        return datetime.now(PH_TZ).date()
    return datetime.now().date()

def get_ph_datetime():
    if PH_TZ:
        return datetime.now(PH_TZ)
    return datetime.now()

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
        url = os.environ.get('DATABASE_URL')
        if not url:
            raise Exception("DATABASE_URL not set")
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        db = g._database = psycopg2.connect(url, sslmode='require', cursor_factory=RealDictCursor)
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# ===== FIXED INIT_DB - WALANG ALTER - DIRECT COMPLETE TABLE =====
def init_db():
    with app.app_context():
        db = get_db()
        cur = db.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT \'Teacher\', status TEXT DEFAULT \'pending\')')
        cur.execute('CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, grade_section TEXT NOT NULL, parent_name TEXT, parent_contact TEXT, qr_code_path TEXT)')
        cur.execute('''CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY,
            student_id TEXT NOT NULL,
            date TEXT NOT NULL,
            time_in TEXT,
            time_out TEXT,
            status TEXT DEFAULT 'Present',
            scanned_by TEXT,
            time_in_am TEXT,
            time_out_am TEXT,
            time_in_pm TEXT,
            time_out_pm TEXT)''')
        cur.execute('CREATE TABLE IF NOT EXISTS teachers (id SERIAL PRIMARY KEY, teacher_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, subject TEXT, contact TEXT)')
        cur.execute("SELECT * FROM users WHERE username='admin'")
        if not cur.fetchone():
            cur.execute("INSERT INTO users (username, password, role, status) VALUES ('admin', 'admin123', 'Admin', 'approved')")
        db.commit()

try:
    init_db()
except Exception as e:
    print(f"DB Init: {e}")

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
        user = cur.fetchone()
        if user:
            if user['status']!= 'approved':
                flash('Your account is pending approval by Admin', 'warning')
                return redirect(url_for('login'))
            session['logged_in'] = True
            session['username'] = user['username']
            session['role'] = user['role']
            flash(f'Welcome back, {user["username"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid Username or Password', 'danger')
    return render_template('login.html', school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/register_user', methods=['GET', 'POST'])
def register_user():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        db = get_db()
        cur = db.cursor()
        try:
            cur.execute("INSERT INTO users(username, password, role, status) VALUES(%s,%s,%s, 'pending')", (username, password, role))
            db.commit()
            flash('Registration Sent! Wait for Admin approval.', 'success')
            return redirect(url_for('login'))
        except:
            db.rollback()
            flash('Username already exists', 'danger')
    return render_template('register_user.html', school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/approvals')
def approvals():
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Admin access only', 'danger')
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE status='pending'")
    pending_users = cur.fetchall()
    return render_template('approvals.html', users=pending_users, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/approve_user/<int:id>')
def approve_user(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Admin access only', 'danger')
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id=%s", [id])
    user = cur.fetchone()
    if user:
        cur.execute("UPDATE users SET status='approved' WHERE id=%s", [id])
        if user['role'] == 'Teacher':
            try:
                cur.execute("SELECT * FROM teachers WHERE teacher_id=%s", (user['username'],))
                exists = cur.fetchone()
                if not exists:
                    cur.execute("INSERT INTO teachers(teacher_id, name, subject, contact) VALUES(%s,%s,%s,%s)", (user['username'], user['username'], 'Not Set', 'Not Set'))
            except:
                db.rollback()
        db.commit()
        flash('User Approved Successfully & Added to Teachers List', 'success')
    return redirect(url_for('approvals'))

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    today_str = get_ph_date().isoformat()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) as c FROM students")
    total = cur.fetchone()['c']
    cur.execute("SELECT COUNT(DISTINCT student_id) as c FROM attendance WHERE date=%s AND time_in_am IS NOT NULL", [today_str])
    present_today = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) as c FROM attendance WHERE date=%s AND status='Late'", [today_str])
    late_today = cur.fetchone()['c']
    absent_today = 0 if present_today == 0 else total - present_today
    cur.execute("SELECT s.name, a.time_in_am, a.time_out_am, a.time_in_pm, a.time_out_pm, a.status, a.scanned_by FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=%s ORDER BY a.id DESC LIMIT 5", [today_str])
    recent = cur.fetchall()
    return render_template('dashboard.html', total=total, present=present_today, late=late_today, absent=absent_today, recent=recent, school=SCHOOL_NAME, grade=GRADE_LEVEL, today=get_ph_date(), format_time=format_time_12hr, late_am=LATE_CUTOFF, late_pm=LATE_CUTOFF_PM)

@app.route('/students')
def students():
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
        flash('Unauthorized! Admin or Teacher access only.', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM students")
    students = cur.fetchall()
    return render_template('students.html', students=students, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/teachers')
def teachers():
    if not session.get('logged_in') or session['role']!= 'Admin':
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM teachers ORDER BY name")
    teachers = cur.fetchall()
    return render_template('teachers.html', teachers=teachers, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/edit_teacher/<int:id>', methods=['GET', 'POST'])
def edit_teacher(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only.', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM teachers WHERE id =%s", (id,))
    teacher = cur.fetchone()
    if request.method == 'POST':
        teacher_id = request.form['teacher_id']
        name = request.form['name']
        subject = request.form['subject']
        contact = request.form['contact']
        try:
            cur.execute("UPDATE teachers SET teacher_id=%s, name=%s, subject=%s, contact=%s WHERE id=%s", (teacher_id, name, subject, contact, id))
            db.commit()
            flash(f'Teacher {name} updated successfully.', 'success')
            return redirect(url_for('teachers'))
        except:
            db.rollback()
            flash('Error: Teacher ID already exists', 'danger')
    return render_template('edit_teacher.html', teacher=teacher, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/delete_teacher/<int:id>', methods=['POST'])
def delete_teacher(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only.', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM teachers WHERE id =%s", (id,))
        db.commit()
        flash('Teacher deleted successfully.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error deleting teacher: {e}', 'danger')
    return redirect(url_for('teachers'))

@app.route('/register_student', methods=['GET', 'POST'])
def register_student():
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Admin access only - Teachers cannot add students', 'danger')
        return redirect(url_for('students'))
    qr_path = None
    student_name = None
    student_id = None
    if request.method == 'POST':
        student_id = request.form['student_id']
        name = request.form['name']
        section = request.form['grade_section']
        pname = request.form['parent_name']
        pcontact = request.form['parent_contact']
        os.makedirs('static/qr_codes', exist_ok=True)
        qr = qrcode.make(student_id)
        qr_filename = f"qr_codes/{student_id}.png"
        qr_full_path = f"static/{qr_filename}"
        qr.save(qr_full_path)
        db = get_db()
        cur = db.cursor()
        try:
            cur.execute("INSERT INTO students(student_id, name, grade_section, parent_name, parent_contact, qr_code_path) VALUES(%s,%s,%s,%s,%s,%s)", (student_id, name, section, pname, pcontact, qr_filename))
            db.commit()
            flash('Student Registered Successfully', 'success')
            qr_path = qr_filename
            student_name = name
        except:
            db.rollback()
            flash('Error: Student ID already exists', 'danger')
    return render_template('register_student.html', school=SCHOOL_NAME, grade=GRADE_LEVEL, qr_path=qr_path, student_name=student_name, student_id=student_id)

@app.route('/scanner')
def scanner():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('scanner.html', school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/scan', methods=['POST'])
def scan():
    data = request.get_json()
    student_id = data['student_id']
    now = get_ph_datetime()
    today_str = get_ph_date().isoformat()
    cur_time_24 = now.strftime("%H:%M:%S")
    cur_time_12 = now.strftime("%I:%M:%S %p")
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM students WHERE student_id=%s", [student_id])
    student = cur.fetchone()
    if not student:
        return jsonify({'status': 'error', 'message': 'Student not found'})
    cur.execute("SELECT * FROM attendance WHERE student_id=%s AND date=%s", (student_id, today_str))
    record = cur.fetchone()
    if not record:
        status = 'Late' if cur_time_24 > LATE_CUTOFF else 'Present'
        cur.execute("INSERT INTO attendance(student_id, date, time_in, time_in_am, status, scanned_by) VALUES(%s,%s,%s,%s,%s,%s)", (student_id, today_str, cur_time_24, cur_time_24, status, session['username']))
        db.commit()
        message = f"{student['name']} MORNING IN: {cur_time_12} - {status}"
        try:
            sms_service.send_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
        except:
            pass
        return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
    if record['time_in_am'] and not record['time_out_am']:
        cur.execute("UPDATE attendance SET time_out_am=%s, time_out=%s WHERE id=%s", (cur_time_24, cur_time_24, record['id']))
        db.commit()
        message = f"{student['name']} LUNCH OUT: {cur_time_12}"
        try:
            sms_service.send_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
        except:
            pass
        return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
    if record['time_out_am'] and not record['time_in_pm']:
        cur.execute("UPDATE attendance SET time_in_pm=%s WHERE id=%s", (cur_time_24, record['id']))
        db.commit()
        late_note = " - Late" if cur_time_24 > LATE_CUTOFF_PM else " - Present"
        message = f"{student['name']} AFTERNOON IN: {cur_time_12}{late_note}"
        try:
            sms_service.send_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
        except:
            pass
        return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
    if record['time_in_pm'] and not record['time_out_pm']:
        cur.execute("UPDATE attendance SET time_out_pm=%s WHERE id=%s", (cur_time_24, record['id']))
        db.commit()
        message = f"{student['name']} AFTERNOON OUT: {cur_time_12}"
        try:
            sms_service.send_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
        except:
            pass
        return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
    return jsonify({'status': 'error', 'message': f'{student["name"]} already completed attendance today (4 scans done)'})

@app.route('/attendance')
def attendance():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    filter_date = request.args.get('date', get_ph_date().isoformat())
    cur.execute("SELECT DISTINCT date FROM attendance ORDER BY date DESC")
    all_dates = cur.fetchall()
    cur.execute("SELECT a.*, s.name FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=%s ORDER BY a.id DESC", [filter_date])
    records = cur.fetchall()
    return render_template('attendance.html', records=records, all_dates=all_dates, filter_date=filter_date, school=SCHOOL_NAME, grade=GRADE_LEVEL, format_time=format_time_12hr, late_am=LATE_CUTOFF, late_pm=LATE_CUTOFF_PM)

@app.route('/reports')
def reports():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    today_str = get_ph_date().isoformat()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) as c FROM students")
    total = cur.fetchone()['c']
    cur.execute("SELECT COUNT(DISTINCT student_id) as c FROM attendance WHERE date=%s AND time_in_am IS NOT NULL", [today_str])
    present_today = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) as c FROM attendance WHERE date=%s AND status='Late'", [today_str])
    late_today = cur.fetchone()['c']
    absent_today = 0 if present_today == 0 else total - present_today
    return render_template('reports.html', total=total, present=present_today, late=late_today, absent=absent_today, school=SCHOOL_NAME, grade=GRADE_LEVEL, today=get_ph_date())

@app.route('/reset_attendance', methods=['POST'])
def reset_attendance():
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM attendance")
    db.commit()
    flash('All Attendance Records Have Been Reset Successfully', 'success')
    return redirect(url_for('attendance'))

@app.route('/delete_student/<student_id>', methods=['POST'])
def delete_student(student_id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only - Teachers cannot delete students', 'danger')
        return redirect(url_for('students'))
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM attendance WHERE student_id =%s", (student_id,))
        cur.execute("DELETE FROM students WHERE student_id =%s", (student_id,))
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
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
        flash('Unauthorized! Admin or Teacher access only.', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM students WHERE student_id =%s", (student_id,))
    student = cur.fetchone()
    if request.method == 'POST':
        name = request.form['name']
        section = request.form['grade_section']
        pname = request.form['parent_name']
        pcontact = request.form['parent_contact']
        cur.execute("UPDATE students SET name=%s, grade_section=%s, parent_name=%s, parent_contact=%s WHERE student_id=%s", (name, section, pname, pcontact, student_id))
        db.commit()
        flash(f'Student {name} updated successfully.', 'success')
        return redirect(url_for('students'))
    return render_template('edit_student.html', student=student, school=SCHOOL_NAME, grade=GRADE_LEVEL)

if __name__ == '__main__':
    app.run(debug=True)
