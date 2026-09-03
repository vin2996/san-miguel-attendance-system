from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, g
import qrcode
import os
import sqlite3
from datetime import datetime, date
import sms_service

# POSTGRES IMPORT
try:
    import psycopg2
    import psycopg2.extras
except:
    psycopg2 = None

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

# --- DATABASE CONFIG - AUTO DETECT ---
DATABASE_URL = os.environ.get('DATABASE_URL')
DATABASE = 'qr_attendance.db' # pang local fallback lang

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
        if DATABASE_URL and psycopg2:
            # POSTGRES - PARA SA RENDER - PERMANENTE
            db = g._database = psycopg2.connect(DATABASE_URL, sslmode='require')
        else:
            # SQLITE - PARA SA LOCAL LAPTOP MO LANG
            db = g._database = sqlite3.connect(DATABASE, timeout=10.0)
            db.row_factory = sqlite3.Row
    return db

def is_postgres():
    return DATABASE_URL is not None and psycopg2 is not None

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cur = db.cursor()

        if is_postgres():
            # POSTGRES TABLE - SERIAL GAMIT
            cur.execute('CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT \'Teacher\', status TEXT DEFAULT \'pending\')')
            cur.execute('CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, grade_section TEXT NOT NULL, parent_name TEXT, parent_contact TEXT, qr_code_path TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS attendance (id SERIAL PRIMARY KEY, student_id TEXT NOT NULL, date TEXT NOT NULL, time_in TEXT, time_out TEXT, status TEXT DEFAULT \'Present\', scanned_by TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS teachers (id SERIAL PRIMARY KEY, teacher_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, subject TEXT, contact TEXT)')
            try:
                cur.execute("ALTER TABLE attendance ADD COLUMN time_in_am TEXT")
            except:
                db.rollback()
            try:
                cur.execute("ALTER TABLE attendance ADD COLUMN time_out_am TEXT")
            except:
                db.rollback()
            try:
                cur.execute("ALTER TABLE attendance ADD COLUMN time_in_pm TEXT")
            except:
                db.rollback()
            try:
                cur.execute("ALTER TABLE attendance ADD COLUMN time_out_pm TEXT")
            except:
                db.rollback()
            cur.execute("SELECT * FROM users WHERE username='admin'")
            if not cur.fetchone():
                cur.execute("INSERT INTO users (username, password, role, status) VALUES ('admin', 'admin123', 'Admin', 'approved')")
            db.commit()
        else:
            # SQLITE - LOCAL MO LANG
            db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT "Teacher", status TEXT DEFAULT "pending")')
            db.execute('CREATE TABLE IF NOT EXISTS students (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, grade_section TEXT NOT NULL, parent_name TEXT, parent_contact TEXT, qr_code_path TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, date TEXT NOT NULL, time_in TEXT, time_out TEXT, status TEXT DEFAULT "Present", scanned_by TEXT, FOREIGN KEY (student_id) REFERENCES students(student_id))')
            db.execute('CREATE TABLE IF NOT EXISTS teachers (id INTEGER PRIMARY KEY AUTOINCREMENT, teacher_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, subject TEXT, contact TEXT)')
            try:
                db.execute("ALTER TABLE attendance ADD COLUMN time_in_am TEXT")
            except:
                pass
            try:
                db.execute("ALTER TABLE attendance ADD COLUMN time_out_am TEXT")
            except:
                pass
            try:
                db.execute("ALTER TABLE attendance ADD COLUMN time_in_pm TEXT")
            except:
                pass
            try:
                db.execute("ALTER TABLE attendance ADD COLUMN time_out_pm TEXT")
            except:
                pass
            cur = db.execute("SELECT * FROM users WHERE username='admin'")
            if not cur.fetchone():
                db.execute("INSERT INTO users (username, password, role, status) VALUES ('admin', 'admin123', 'Admin', 'approved')")
            db.commit()

        cur.close()

init_db()

# HELPER PARA MAGING DICT ANG RESULT KAHIT POSTGRES O SQLITE
def dict_fetchone(cursor):
    row = cursor.fetchone()
    if not row:
        return None
    if is_postgres():
        if isinstance(row, dict):
            return row
        # psycopg2 returns tuple - convert
        desc = [d[0] for d in cursor.description]
        return dict(zip(desc, row))
    else:
        return row

def dict_fetchall(cursor):
    rows = cursor.fetchall()
    if is_postgres():
        desc = [d[0] for d in cursor.description]
        return [dict(zip(desc, r)) if not isinstance(r, dict) else r for r in rows]
    else:
        return rows

# LAHAT NG ROUTES MO - SAME LOGIC - DATABASE LANG NAKA %s PAG POSTGRES
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']; password = request.form['password']
        db = get_db()
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if is_postgres() else db.cursor()
        if is_postgres():
            cur.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
        else:
            cur.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = dict_fetchone(cur)
        cur.close()
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
            cur = db.cursor()
            if is_postgres():
                cur.execute("INSERT INTO users(username, password, role, status) VALUES(%s,%s,%s, 'pending')", (username, password, role))
            else:
                cur.execute("INSERT INTO users(username, password, role, status) VALUES(?,?,?, 'pending')", (username, password, role))
            db.commit(); cur.close()
            flash('Registration Sent! Wait for Admin approval.', 'success'); return redirect(url_for('login'))
        except:
            flash('Username already exists', 'danger')
    return render_template('register_user.html', school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/approvals')
def approvals():
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Admin access only', 'danger'); return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if is_postgres() else db.cursor()
    cur.execute("SELECT * FROM users WHERE status='pending'")
    pending_users = dict_fetchall(cur)
    cur.close()
    return render_template('approvals.html', users=pending_users, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/approve_user/<int:id>')
def approve_user(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Admin access only', 'danger'); return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if is_postgres() else db.cursor()
    if is_postgres():
        cur.execute("SELECT * FROM users WHERE id=%s", [id])
    else:
        cur.execute("SELECT * FROM users WHERE id=?", [id])
    user = dict_fetchone(cur)
    if user:
        if is_postgres():
            cur.execute("UPDATE users SET status='approved' WHERE id=%s", [id])
        else:
            cur.execute("UPDATE users SET status='approved' WHERE id=?", [id])
        if user['role'] == 'Teacher':
            try:
                if is_postgres():
                    cur.execute("SELECT * FROM teachers WHERE teacher_id=%s", (user['username'],))
                else:
                    cur.execute("SELECT * FROM teachers WHERE teacher_id=?", (user['username'],))
                exists = dict_fetchone(cur)
                if not exists:
                    if is_postgres():
                        cur.execute("INSERT INTO teachers(teacher_id, name, subject, contact) VALUES(%s,%s,%s,%s)", (user['username'], user['username'], 'Not Set', 'Not Set'))
                    else:
                        cur.execute("INSERT INTO teachers(teacher_id, name, subject, contact) VALUES(?,?,?,?)", (user['username'], user['username'], 'Not Set', 'Not Set'))
            except:
                pass
        db.commit()
        flash('User Approved Successfully & Added to Teachers List', 'success')
    cur.close()
    return redirect(url_for('approvals'))

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    today_str = get_ph_date().isoformat()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM students" if is_postgres() else "SELECT COUNT(*) FROM students")
    total = cur.fetchone()[0]
    if is_postgres():
        cur.execute("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date=%s AND time_in_am IS NOT NULL", [today_str])
    else:
        cur.execute("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date=? AND time_in_am IS NOT NULL", [today_str])
    present_today = cur.fetchone()[0]
    if is_postgres():
        cur.execute("SELECT COUNT(*) FROM attendance WHERE date=%s AND status='Late'", [today_str])
    else:
        cur.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Late'", [today_str])
    late_today = cur.fetchone()[0]
    absent_today = 0 if present_today == 0 else total - present_today
    if is_postgres():
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT s.name, a.time_in_am, a.time_out_am, a.time_in_pm, a.time_out_pm, a.status, a.scanned_by FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=%s ORDER BY a.id DESC LIMIT 5", [today_str])
    else:
        cur.execute("SELECT s.name, a.time_in_am, a.time_out_am, a.time_in_pm, a.time_out_pm, a.status, a.scanned_by FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=? ORDER BY a.id DESC LIMIT 5", [today_str])
    recent = dict_fetchall(cur)
    cur.close()
    return render_template('dashboard.html', total=total, present=present_today, late=late_today, absent=absent_today, recent=recent, school=SCHOOL_NAME, grade=GRADE_LEVEL, today=get_ph_date(), format_time=format_time_12hr, late_am=LATE_CUTOFF, late_pm=LATE_CUTOFF_PM)

@app.route('/students')
def students():
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
        flash('Unauthorized! Admin or Teacher access only.', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if is_postgres() else db.cursor()
    cur.execute("SELECT * FROM students")
    students = dict_fetchall(cur)
    cur.close()
    return render_template('students.html', students=students, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/teachers')
def teachers():
    if not session.get('logged_in') or session['role']!= 'Admin':
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if is_postgres() else db.cursor()
    cur.execute("SELECT * FROM teachers ORDER BY name")
    teachers = dict_fetchall(cur)
    cur.close()
    return render_template('teachers.html', teachers=teachers, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/edit_teacher/<int:id>', methods=['GET', 'POST'])
def edit_teacher(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only.', 'danger'); return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if is_postgres() else db.cursor()
    if is_postgres():
        cur.execute("SELECT * FROM teachers WHERE id =%s", (id,))
    else:
        cur.execute("SELECT * FROM teachers WHERE id =?", (id,))
    teacher = dict_fetchone(cur)
    if request.method == 'POST':
        teacher_id = request.form['teacher_id']; name = request.form['name']; subject = request.form['subject']; contact = request.form['contact']
        try:
            if is_postgres():
                cur.execute("UPDATE teachers SET teacher_id=%s, name=%s, subject=%s, contact=%s WHERE id=%s", (teacher_id, name, subject, contact, id))
            else:
                cur.execute("UPDATE teachers SET teacher_id=?, name=?, subject=?, contact=? WHERE id=?", (teacher_id, name, subject, contact, id))
            db.commit(); flash(f'Teacher {name} updated successfully.', 'success'); cur.close(); return redirect(url_for('teachers'))
        except:
            flash('Error: Teacher ID already exists', 'danger')
    cur.close()
    return render_template('edit_teacher.html', teacher=teacher, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/delete_teacher/<int:id>', methods=['POST'])
def delete_teacher(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only.', 'danger'); return redirect(url_for('dashboard'))
    db = get_db()
    try:
        cur = db.cursor()
        if is_postgres():
            cur.execute("DELETE FROM teachers WHERE id =%s", (id,))
        else:
            cur.execute("DELETE FROM teachers WHERE id =?", (id,))
        db.commit(); cur.close(); flash('Teacher deleted successfully.', 'success')
    except Exception as e:
        db.rollback(); flash(f'Error deleting teacher: {e}', 'danger')
    return redirect(url_for('teachers'))

@app.route('/register_student', methods=['GET', 'POST'])
def register_student():
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Admin access only - Teachers cannot add students', 'danger')
        return redirect(url_for('students'))
    qr_path = None; student_name = None; student_id = None
    if request.method == 'POST':
        student_id = request.form['student_id']; name = request.form['name']; section = request.form['grade_section']; pname = request.form['parent_name']; pcontact = request.form['parent_contact']
        os.makedirs('static/qr_codes', exist_ok=True); qr = qrcode.make(student_id); qr_filename = f"qr_codes/{student_id}.png"; qr_full_path = f"static/{qr_filename}"; qr.save(qr_full_path)
        db = get_db()
        try:
            cur = db.cursor()
            if is_postgres():
                cur.execute("INSERT INTO students(student_id, name, grade_section, parent_name, parent_contact, qr_code_path) VALUES(%s,%s,%s,%s,%s,%s)", (student_id, name, section, pname, pcontact, qr_filename))
            else:
                cur.execute("INSERT INTO students(student_id, name, grade_section, parent_name, parent_contact, qr_code_path) VALUES(?,?,?,?,?,?)", (student_id, name, section, pname, pcontact, qr_filename))
            db.commit(); cur.close(); flash('Student Registered Successfully', 'success'); qr_path = qr_filename; student_name = name
        except:
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
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if is_postgres() else db.cursor()
    if is_postgres():
        cur.execute("SELECT * FROM students WHERE student_id=%s", [student_id])
    else:
        cur.execute("SELECT * FROM students WHERE student_id=?", [student_id])
    student = dict_fetchone(cur)
    if not student:
        cur.close(); return jsonify({'status': 'error', 'message': 'Student not found'})
    if is_postgres():
        cur.execute("SELECT * FROM attendance WHERE student_id=%s AND date=%s", (student_id, today_str))
    else:
        cur.execute("SELECT * FROM attendance WHERE student_id=? AND date=?", (student_id, today_str))
    record = dict_fetchone(cur)
    if not record:
        status = 'Late' if cur_time_24 > LATE_CUTOFF else 'Present'
        if is_postgres():
            cur.execute("INSERT INTO attendance(student_id, date, time_in, time_in_am, status, scanned_by) VALUES(%s,%s,%s,%s,%s,%s)", (student_id, today_str, cur_time_24, cur_time_24, status, session['username']))
        else:
            cur.execute("INSERT INTO attendance(student_id, date, time_in, time_in_am, status, scanned_by) VALUES(?,?,?,?,?,?)", (student_id, today_str, cur_time_24, cur_time_24, status, session['username']))
        db.commit()
        message = f"{student['name']} MORNING IN: {cur_time_12} - {status}"
        try: sms_service.send_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
        except: pass
        cur.close()
        return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
    if record['time_in_am'] and not record['time_out_am']:
        if is_postgres():
            cur.execute("UPDATE attendance SET time_out_am=%s, time_out=%s WHERE id=%s", (cur_time_24, cur_time_24, record['id']))
        else:
            cur.execute("UPDATE attendance SET time_out_am=?, time_out=? WHERE id=?", (cur_time_24, cur_time_24, record['id']))
        db.commit()
        message = f"{student['name']} LUNCH OUT: {cur_time_12}"
        try: sms_service.send_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
        except: pass
        cur.close()
        return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
    if record['time_out_am'] and not record['time_in_pm']:
        if is_postgres():
            cur.execute("UPDATE attendance SET time_in_pm=%s WHERE id=%s", (cur_time_24, record['id']))
        else:
            cur.execute("UPDATE attendance SET time_in_pm=? WHERE id=?", (cur_time_24, record['id']))
        db.commit()
        late_note = " - Late" if cur_time_24 > LATE_CUTOFF_PM else " - Present"
        message = f"{student['name']} AFTERNOON IN: {cur_time_12}{late_note}"
        try: sms_service.send_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
        except: pass
        cur.close()
        return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
    if record['time_in_pm'] and not record['time_out_pm']:
        if is_postgres():
            cur.execute("UPDATE attendance SET time_out_pm=%s WHERE id=%s", (cur_time_24, record['id']))
        else:
            cur.execute("UPDATE attendance SET time_out_pm=? WHERE id=?", (cur_time_24, record['id']))
        db.commit()
        message = f"{student['name']} AFTERNOON OUT: {cur_time_12}"
        try: sms_service.send_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
        except: pass
        cur.close()
        return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
    cur.close()
    return jsonify({'status': 'error', 'message': f'{student["name"]} already completed attendance today (4 scans done)'})

@app.route('/attendance')
def attendance():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    db = get_db()
    filter_date = request.args.get('date', get_ph_date().isoformat())
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if is_postgres() else db.cursor()
    cur.execute("SELECT DISTINCT date FROM attendance ORDER BY date DESC")
    all_dates = dict_fetchall(cur)
    if is_postgres():
        cur.execute("SELECT a.*, s.name FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=%s ORDER BY a.id DESC", [filter_date])
    else:
        cur.execute("SELECT a.*, s.name FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=? ORDER BY a.id DESC", [filter_date])
    records = dict_fetchall(cur)
    cur.close()
    return render_template('attendance.html', records=records, all_dates=all_dates, filter_date=filter_date, school=SCHOOL_NAME, grade=GRADE_LEVEL, format_time=format_time_12hr, late_am=LATE_CUTOFF, late_pm=LATE_CUTOFF_PM)

@app.route('/reports')
def reports():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    today_str = get_ph_date().isoformat()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM students")
    total = cur.fetchone()[0]
    if is_postgres():
        cur.execute("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date=%s AND time_in_am IS NOT NULL", [today_str])
    else:
        cur.execute("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date=? AND time_in_am IS NOT NULL", [today_str])
    present_today = cur.fetchone()[0]
    if is_postgres():
        cur.execute("SELECT COUNT(*) FROM attendance WHERE date=%s AND status='Late'", [today_str])
    else:
        cur.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Late'", [today_str])
    late_today = cur.fetchone()[0]
    absent_today = 0 if present_today == 0 else total - present_today
    cur.close()
    return render_template('reports.html', total=total, present=present_today, late=late_today, absent=absent_today, school=SCHOOL_NAME, grade=GRADE_LEVEL, today=get_ph_date())

@app.route('/reset_attendance', methods=['POST'])
def reset_attendance():
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized Access', 'danger'); return redirect(url_for('login'))
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM attendance"); db.commit(); cur.close()
    flash('All Attendance Records Have Been Reset Successfully', 'success')
    return redirect(url_for('attendance'))

@app.route('/delete_student/<student_id>', methods=['POST'])
def delete_student(student_id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Unauthorized! Admin access only - Teachers cannot delete students', 'danger')
        return redirect(url_for('students'))
    db = get_db()
    try:
        cur = db.cursor()
        if is_postgres():
            cur.execute("DELETE FROM attendance WHERE student_id =%s", (student_id,))
            cur.execute("DELETE FROM students WHERE student_id =%s", (student_id,))
        else:
            cur.execute("DELETE FROM attendance WHERE student_id =?", (student_id,))
            cur.execute("DELETE FROM students WHERE student_id =?", (student_id,))
        db.commit(); cur.close()
        qr_file = f"static/qr_codes/{student_id}.png"
        if os.path.exists(qr_file):
            os.remove(qr_file)
        flash(f'Student {student_id} deleted successfully.', 'success')
    except Exception as e:
        db.rollback(); flash(f'Error deleting student: {e}', 'danger')
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
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if is_postgres() else db.cursor()
    if is_postgres():
        cur.execute("SELECT * FROM students WHERE student_id =%s", (student_id,))
    else:
        cur.execute("SELECT * FROM students WHERE student_id =?", (student_id,))
    student = dict_fetchone(cur)
    if request.method == 'POST':
        name = request.form['name']; section = request.form['grade_section']; pname = request.form['parent_name']; pcontact = request.form['parent_contact']
        if is_postgres():
            cur.execute("UPDATE students SET name=%s, grade_section=%s, parent_name=%s, parent_contact=%s WHERE student_id=%s", (name, section, pname, pcontact, student_id))
        else:
            cur.execute("UPDATE students SET name=?, grade_section=?, parent_name=?, parent_contact=? WHERE student_id=?", (name, section, pname, pcontact, student_id))
        db.commit(); cur.close(); flash(f'Student {name} updated successfully.', 'success'); return redirect(url_for('students'))
    cur.close()
    return render_template('edit_student.html', student=student, school=SCHOOL_NAME, grade=GRADE_LEVEL)

if __name__ == '__main__':
    app.run(debug=True)
