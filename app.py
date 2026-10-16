from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, g, send_file
import qrcode
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlite3
from datetime import datetime, date, timezone, timedelta
import requests
import zipfile
import io
from PIL import Image, ImageDraw, ImageFont
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass
try:
    from zoneinfo import ZoneInfo
    try:
        PH_TZ = ZoneInfo('Asia/Manila')
    except Exception:
        PH_TZ = timezone(timedelta(hours=8))
except ImportError:
    PH_TZ = timezone(timedelta(hours=8))

app = Flask(__name__)
app.secret_key = 'superdupersecretkey123'

LATE_CUTOFF = "07:30:00"
LATE_CUTOFF_PM = "13:00:00"
SCHOOL_NAME = "San Miguel Elementary School"
GRADE_LEVEL = "Grade 6"

IPROG_API_TOKEN = os.environ.get("IPROG_API_TOKEN", "YOUR_IPROG_API_TOKEN_HERE")
IPROG_API_URL = "https://sms.iprogtech.com/api/v1/sms_messages"
IS_SQLITE = False
UPLOAD_FOLDER = os.path.join('static', 'uploads')
QR_FOLDER = os.path.join('static', 'qr_codes')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)

def send_iprog_sms(phone_number, message):
    try:
        if not phone_number:
            return False
        p = phone_number.strip()
        if p.startswith("+"):
            p = p[1:]
        if p.startswith("0"):
            p = "63" + p[1:]
        payload = {"api_token": IPROG_API_TOKEN, "phone_number": p, "message": message, "sms_provider": 2}
        r = requests.post(IPROG_API_URL, json=payload, timeout=15)
        print(f"[IPROG] TO:{p} STATUS:{r.status_code} RESP:{r.text}")
        return r.status_code == 200
    except Exception as e:
        print(f"[IPROG FAILED] {e}")
        return False

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

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

class SQLiteCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor
    def execute(self, sql, params=None):
        sql = sql.replace("%s", "?")
        if params is None:
            return self._cursor.execute(sql)
        return self._cursor.execute(sql, params)
    def fetchone(self):
        return self._cursor.fetchone()
    def fetchall(self):
        return self._cursor.fetchall()

class SQLiteConnWrapper:
    def __init__(self, conn):
        self._conn = conn
    def cursor(self):
        return SQLiteCursorWrapper(self._conn.cursor())
    def commit(self):
        return self._conn.commit()
    def rollback(self):
        return self._conn.rollback()
    def close(self):
        return self._conn.close()

def get_sqlite_db():
    conn = sqlite3.connect('qr_attendance.db')
    conn.row_factory = dict_factory
    return SQLiteConnWrapper(conn)

def get_db():
    global IS_SQLITE
    db = getattr(g, '_database', None)
    if db is not None:
        return db

    url = os.environ.get('DATABASE_URL')
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        try:
            pg_db = psycopg2.connect(url, sslmode='require', cursor_factory=RealDictCursor, connect_timeout=15)
            IS_SQLITE = False
            print("✅ CONNECTED TO POSTGRES - DATA WILL PERSIST")
            g._database = pg_db
            return pg_db
        except Exception as e:
            print(f"❌ CRITICAL POSTGRES ERROR: {e}")
            print("❌ WILL NOT FALLBACK TO SQLITE ON RENDER")
            raise e

    IS_SQLITE = True
    print("⚠️ LOCAL MODE: USING SQLITE qr_attendance.db")
    db = g._database = get_sqlite_db()
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        try:
            db.close()
        except:
            pass

def init_db():
    with app.app_context():
        db = get_db()
        cur = db.cursor()
        if IS_SQLITE:
            cur.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT "Teacher", status TEXT DEFAULT "pending")')
            cur.execute('CREATE TABLE IF NOT EXISTS students (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, grade_section TEXT NOT NULL, parent_name TEXT, parent_contact TEXT, qr_code_path TEXT, photo TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, date TEXT NOT NULL, time_in TEXT, time_out TEXT, status TEXT DEFAULT "Present", scanned_by TEXT, time_in_am TEXT, time_out_am TEXT, time_in_pm TEXT, time_out_pm TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS teachers (id INTEGER PRIMARY KEY AUTOINCREMENT, teacher_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, subject TEXT, contact TEXT)')
            cur.execute("SELECT * FROM users WHERE username='admin'")
            if not cur.fetchone():
                cur.execute("INSERT INTO users (username, password, role, status) VALUES ('admin', 'admin123', 'Admin', 'approved')")
            try:
                cur.execute("SELECT photo FROM students LIMIT 1")
            except:
                try:
                    cur.execute("ALTER TABLE students ADD COLUMN photo TEXT")
                except:
                    pass
        else:
            cur.execute('CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT \'Teacher\', status TEXT DEFAULT \'pending\')')
            cur.execute('CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, grade_section TEXT NOT NULL, parent_name TEXT, parent_contact TEXT, qr_code_path TEXT, photo TEXT)')
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
            try:
                cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS photo TEXT")
            except:
                pass
        db.commit()
        print(f"✅ DB INIT DONE - IS_SQLITE={IS_SQLITE}")

try:
    init_db()
except Exception as e:
    print(f"DB Init Error: {e}")

def generate_id_card(student):
    W, H = 700, 1050
    card = Image.new('RGB', (W, H), '#ffffff')
    draw = ImageDraw.Draw(card)
    try:
        font_header = ImageFont.truetype("arialbd.ttf", 28)
        font_name = ImageFont.truetype("arialbd.ttf", 36)
        font_detail = ImageFont.truetype("arial.ttf", 26)
        font_small = ImageFont.truetype("arial.ttf", 22)
    except:
        font_header = ImageFont.load_default()
        font_name = ImageFont.load_default()
        font_detail = ImageFont.load_default()
        font_small = ImageFont.load_default()
    draw.rectangle([0, 0, W, 110], fill='#0d47a1')
    try:
        draw.text((W//2, 55), SCHOOL_NAME.upper(), fill='white', font=font_header, anchor='mm')
    except:
        draw.text((W//2 - 150, 40), SCHOOL_NAME.upper(), fill='white', font=font_header)
    draw.rectangle([0, 110, W, 145], fill='#ffca28')
    try:
        draw.text((W//2, 127), GRADE_LEVEL, fill='black', font=font_small, anchor='mm')
    except:
        draw.text((W//2 - 40, 117), GRADE_LEVEL, fill='black', font=font_small)
    photo_file = student.get('photo')
    img_path = None
    if photo_file:
        p1 = os.path.join(UPLOAD_FOLDER, str(photo_file))
        p2 = os.path.join('static', str(photo_file))
        p3 = str(photo_file)
        for pp in [p1, p2, p3]:
            if os.path.exists(pp):
                img_path = pp
                break
    photo_size = 280
    photo_x = (W - photo_size)//2
    photo_y = 175
    if img_path:
        try:
            p_img = Image.open(img_path).convert('RGB')
            p_img = p_img.resize((photo_size, photo_size))
            mask = Image.new('L', (photo_size, photo_size), 0)
            ImageDraw.Draw(mask).ellipse((0,0,photo_size,photo_size), fill=255)
            card.paste(p_img, (photo_x, photo_y), mask)
            draw.ellipse([photo_x, photo_y, photo_x+photo_size, photo_y+photo_size], outline='#0d47a1', width=4)
        except:
            draw.rectangle([photo_x, photo_y, photo_x+photo_size, photo_y+photo_size], fill='#e0e0e0')
    else:
        draw.rectangle([photo_x, photo_y, photo_x+photo_size, photo_y+photo_size], fill='#e0e0e0')
        try:
            draw.text((W//2, photo_y+photo_size//2), "NO PHOTO", fill='#616161', font=font_detail, anchor='mm')
        except:
            draw.text((W//2-60, photo_y+photo_size//2), "NO PHOTO", fill='#616161', font=font_detail)
    try:
        draw.text((W//2, 520), str(student.get('name','')).upper(), fill='black', font=font_name, anchor='mm')
        draw.text((W//2, 565), f"ID: {student.get('student_id','')}", fill='#424242', font=font_detail, anchor='mm')
        draw.text((W//2, 600), f"{student.get('grade_section','')}", fill='#424242', font=font_detail, anchor='mm')
    except:
        draw.text((50, 520), str(student.get('name','')).upper(), fill='black', font=font_name)
        draw.text((50, 565), f"ID: {student.get('student_id','')}", fill='#424242', font=font_detail)
    qr_data = str(student.get('student_id',''))
    qr_img = qrcode.make(qr_data)
    qr_img = qr_img.resize((320, 320))
    card.paste(qr_img, ((W-320)//2, 650))
    try:
        draw.text((W//2, 1000), "SCAN FOR ATTENDANCE", fill='#757575', font=font_small, anchor='mm')
    except:
        draw.text((W//2-110, 1000), "SCAN FOR ATTENDANCE", fill='#757575', font=font_small)
    return card

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
        username = request.form['username'].strip()
        password = request.form['password']
        role = request.form['role']
        full_name = request.form.get('full_name', username).strip()
        subject = request.form.get('subject', 'Not Set').strip()
        contact = request.form.get('contact', 'Not Set').strip()
        db = get_db()
        cur = db.cursor()
        try:
            cur.execute("INSERT INTO users(username, password, role, status) VALUES(%s,%s,%s, 'pending')", (username, password, role))
            if role == 'Teacher':
                cur.execute("SELECT * FROM teachers WHERE teacher_id=%s", (username,))
                if not cur.fetchone():
                    cur.execute("INSERT INTO teachers(teacher_id, name, subject, contact) VALUES(%s,%s,%s,%s)", (username, full_name, subject, contact))
            db.commit()
            flash('Registration Sent! Wait for Admin approval.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.rollback()
            flash(f'Username already exists: {e}', 'danger')
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
    cur.execute("SELECT COUNT(DISTINCT attendance.student_id) as c FROM attendance WHERE attendance.date=%s AND attendance.time_in_am IS NOT NULL", [today_str])
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
    today_str = get_ph_date().isoformat()
    cur.execute("SELECT COUNT(*) as c FROM students")
    total = cur.fetchone()['c']
    cur.execute("SELECT COUNT(DISTINCT attendance.student_id) as c FROM attendance WHERE attendance.date=%s AND attendance.time_in_am IS NOT NULL", [today_str])
    present_today = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) as c FROM attendance WHERE date=%s AND status='Late'", [today_str])
    late_today = cur.fetchone()['c']
    absent_today = total - present_today
    if absent_today < 0:
        absent_today = 0
    if total == 0:
        absent_today = 0
    return render_template('students.html', students=students, total=total, present_today=present_today, absent_today=absent_today, late_today=late_today, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/download_qr/<student_id>')
def download_qr(student_id):
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
        flash('Unauthorized! Admin or Teacher access only.', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM students WHERE student_id=%s", [student_id])
    student = cur.fetchone()
    if not student:
        flash('Student not found', 'danger')
        return redirect(url_for('students'))
    card = generate_id_card(student)
    buf = io.BytesIO()
    card.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True, download_name=f"ID_{student['name'].replace(' ', '_')}_{student['student_id']}.png")

@app.route('/download_all_qr')
def download_all_qr():
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
        flash('Unauthorized! Admin or Teacher access only.', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM students")
    all_students = cur.fetchall()
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for st in all_students:
            try:
                card = generate_id_card(st)
                img_buf = io.BytesIO()
                card.save(img_buf, format='PNG')
                fname = f"ID_{st['name'].replace(' ', '_')}_{st['student_id']}.png"
                zf.writestr(fname, img_buf.getvalue())
            except Exception as e:
                print(f"ID gen failed for {st['student_id']}: {e}")
    memory_file.seek(0)
    return send_file(memory_file, download_name='all_student_ids.zip', as_attachment=True, mimetype='application/zip')

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
        photo_filename = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename!= '':
                ext = os.path.splitext(file.filename)[1]
                photo_filename = f"{student_id}{ext}"
                save_path = os.path.join(UPLOAD_FOLDER, photo_filename)
                file.save(save_path)
        os.makedirs('static/qr_codes', exist_ok=True)
        qr = qrcode.make(student_id)
        qr_filename = f"qr_codes/{student_id}.png"
        qr_full_path = f"static/{qr_filename}"
        qr.save(qr_full_path)
        db = get_db()
        cur = db.cursor()
        try:
            cur.execute("INSERT INTO students(student_id, name, grade_section, parent_name, parent_contact, qr_code_path, photo) VALUES(%s,%s,%s,%s,%s,%s,%s)", (student_id, name, section, pname, pcontact, qr_filename, photo_filename))
            db.commit()
            flash('Student Registered Successfully', 'success')
            qr_path = qr_filename
            student_name = name
        except Exception as e:
            db.rollback()
            flash(f'Error: {e}', 'danger')
    return render_template('register_student.html', school=SCHOOL_NAME, grade=GRADE_LEVEL, qr_path=qr_path, student_name=student_name, student_id=student_id)

@app.route('/scanner')
def scanner():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('scanner.html', school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/scan', methods=['POST'])
def scan():
    data = request.get_json()
    student_id = data.get('student_id')
    if not student_id:
        student_id = data.get('qr_data')
    scan_type = data.get('scan_type', 'in')
    if scan_type:
        scan_type = scan_type.lower()
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
    if record:
        last_time_str = None
        if record.get('time_out_pm'):
            last_time_str = record.get('time_out_pm')
        elif record.get('time_in_pm'):
            last_time_str = record.get('time_in_pm')
        elif record.get('time_out_am'):
            last_time_str = record.get('time_out_am')
        elif record.get('time_in_am'):
            last_time_str = record.get('time_in_am')
        if last_time_str:
            try:
                last_dt = datetime.strptime(f"{today_str} {last_time_str}", "%Y-%m-%d %H:%M:%S")
                last_dt = last_dt.replace(tzinfo=PH_TZ)
                diff = (now - last_dt).total_seconds()
                if diff < 60:
                    return jsonify({'status': 'error', 'message': f'{student["name"]} already scanned {int(diff)}s ago. Wait 60s.'})
            except:
                pass
        if scan_type == 'in':
            if not record:
                status = 'Late' if cur_time_24 > LATE_CUTOFF else 'Present'
                cur.execute("INSERT INTO attendance(student_id, date, time_in, time_in_am, status, scanned_by) VALUES(%s,%s,%s,%s,%s,%s)", (student_id, today_str, cur_time_24, cur_time_24, status, session.get('username','system')))
                db.commit()
                message = f"{student['name']} MORNING IN: {cur_time_12} - {status}"
                send_iprog_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
                return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
            if not record['time_in_am']:
                status = 'Late' if cur_time_24 > LATE_CUTOFF else 'Present'
                cur.execute("UPDATE attendance SET time_in_am=%s, time_in=%s, status=%s WHERE id=%s", (cur_time_24, cur_time_24, status, record['id']))
                db.commit()
                message = f"{student['name']} MORNING IN: {cur_time_12} - {status}"
                send_iprog_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
                return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
            if record['time_out_am'] and not record['time_in_pm']:
                cur.execute("UPDATE attendance SET time_in_pm=%s WHERE id=%s", (cur_time_24, record['id']))
                db.commit()
                late_note = " - Late" if cur_time_24 > LATE_CUTOFF_PM else " - Present"
                message = f"{student['name']} AFTERNOON IN: {cur_time_12}{late_note}"
                send_iprog_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
                return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
            return jsonify({'status': 'error', 'message': f'{student["name"]} already completed IN today'})
        if scan_type == 'out':
            if not record:
                return jsonify({'status': 'error', 'message': f'{student["name"]} no AM IN yet. Use TIME IN first.'})
            if record['time_in_am'] and not record['time_out_am']:
                cur.execute("UPDATE attendance SET time_out_am=%s, time_out=%s WHERE id=%s", (cur_time_24, cur_time_24, record['id']))
                db.commit()
                message = f"{student['name']} LUNCH OUT: {cur_time_12}"
                send_iprog_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
                return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
            if record['time_in_pm'] and not record['time_out_pm']:
                cur.execute("UPDATE attendance SET time_out_pm=%s WHERE id=%s", (cur_time_24, record['id']))
                db.commit()
                message = f"{student['name']} AFTERNOON OUT: {cur_time_12}"
                send_iprog_sms(student['parent_contact'], f"{SCHOOL_NAME}: {message}. Thank you.")
                return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
            return jsonify({'status': 'error', 'message': f'{student["name"]} already completed OUT today'})
        return jsonify({'status': 'error', 'message': 'Invalid scan type'})

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
    db = get_db()
    cur = db.cursor()
    filter_date = get_ph_date().isoformat()
    cur.execute("SELECT COUNT(*) as c FROM students")
    total = cur.fetchone()['c']
    cur.execute("SELECT COUNT(DISTINCT attendance.student_id) as c FROM attendance WHERE attendance.date=%s AND attendance.time_in_am IS NOT NULL", [filter_date])
    present = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) as c FROM attendance WHERE date=%s AND status='Late'", [filter_date])
    late = cur.fetchone()['c']
    absent = total - present
    if absent < 0:
        absent = 0
    if total == 0:
        absent = 0
    attendance_rate = 0
    if total > 0:
        attendance_rate = round((present / total) * 100, 1)
    cur.execute("SELECT date FROM attendance GROUP BY date ORDER BY date DESC LIMIT 7")
    dates = cur.fetchall()
    trend_labels = []
    trend_present = []
    trend_absent = []
    for d in reversed(dates):
        dd = str(d['date'])
        cur.execute("SELECT COUNT(DISTINCT attendance.student_id) as c FROM attendance WHERE attendance.date=%s AND attendance.time_in_am IS NOT NULL", [dd])
        p = cur.fetchone()['c']
        trend_labels.append(dd)
        trend_present.append(p)
        trend_absent.append(total - p if total - p > 0 else 0)
    cur.execute("SELECT grade_section, COUNT(*) as total_students FROM students GROUP BY grade_section")
    sections = cur.fetchall()
    section_stats = []
    for sec in sections:
        cur.execute("SELECT COUNT(DISTINCT attendance.student_id) as c FROM attendance JOIN students ON attendance.student_id = students.student_id WHERE attendance.date=%s AND students.grade_section=%s AND attendance.time_in_am IS NOT NULL", [filter_date, sec['grade_section']])
        sp = cur.fetchone()['c']
        section_stats.append({'section': sec['grade_section'], 'total': sec['total_students'], 'present': sp, 'absent': sec['total_students'] - sp})
    cur.execute("SELECT student_id, name, grade_section FROM students WHERE student_id NOT IN (SELECT student_id FROM attendance WHERE date=%s AND time_in_am IS NOT NULL)", [filter_date])
    absent_list = cur.fetchall()
    cur.execute("SELECT s.student_id, s.name, s.grade_section, a.time_in_am FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=%s AND a.status='Late' ORDER BY a.time_in_am ASC", [filter_date])
    late_list = cur.fetchall()
    return render_template('reports.html', total=total, present=present, late=late, absent=absent, attendance_rate=attendance_rate, filter_date=filter_date, trend_labels=trend_labels, trend_present=trend_present, trend_absent=trend_absent, section_stats=section_stats, absent_list=absent_list, late_list=late_list, school=SCHOOL_NAME, grade=GRADE_LEVEL, today=get_ph_date(), format_time=format_time_12hr)

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
        photo_filename = student.get('photo') if student else None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename!= '':
                ext = os.path.splitext(file.filename)[1]
                photo_filename = f"{student_id}{ext}"
                save_path = os.path.join(UPLOAD_FOLDER, photo_filename)
                file.save(save_path)
        if photo_filename:
            cur.execute("UPDATE students SET name=%s, grade_section=%s, parent_name=%s, parent_contact=%s, photo=%s WHERE student_id=%s", (name, section, pname, pcontact, photo_filename, student_id))
        else:
            cur.execute("UPDATE students SET name=%s, grade_section=%s, parent_name=%s, parent_contact=%s WHERE student_id=%s", (name, section, pname, pcontact, student_id))
        db.commit()
        flash(f'Student {name} updated successfully.', 'success')
        return redirect(url_for('students'))
    return render_template('edit_student.html', student=student, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/view_attendance/<student_id>')
def view_attendance(student_id):
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
        flash('Unauthorized! Admin or Teacher access only.', 'danger')
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM students WHERE student_id=%s", [student_id])
    student = cur.fetchone()
    if not student:
        flash('Student not found', 'danger')
        return redirect(url_for('students'))
    cur.execute("SELECT DISTINCT date FROM attendance ORDER BY date DESC")
    all_dates = cur.fetchall()
    school_days = 0
    for d in all_dates:
        try:
            dt = datetime.strptime(str(d['date']), "%Y-%m-%d").date()
            if dt.weekday() < 5:
                school_days += 1
        except:
            school_days += 1
    if school_days == 0:
        school_days = len(all_dates)
    cur.execute("SELECT * FROM attendance WHERE student_id=%s ORDER BY date DESC", [student_id])
    records = cur.fetchall()
    present = 0
    late = 0
    for r in records:
        if r.get('time_in_am') or r.get('time_in_pm'):
            present += 1
        if r.get('status') == 'Late':
            late += 1
    absent = school_days - present
    if absent < 0:
        absent = 0
    return render_template('view_attendance.html', student=student, records=records, school_days=school_days, present=present, absent=absent, late=late, school=SCHOOL_NAME, grade=GRADE_LEVEL, format_time=format_time_12hr)

if __name__ == '__main__':
    app.run(debug=True)