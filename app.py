from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, g, send_file
import qrcode
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlite3
from datetime import datetime, timezone, timedelta
import requests
import zipfile
import io
import base64
from PIL import Image, ImageDraw, ImageFont
from werkzeug.utils import secure_filename
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

LATE_CUTOFF = "07:15:00"
MORNING_OUT_TIME = "12:00:00"
LATE_CUTOFF_PM = "13:15:00"
AFTERNOON_OUT_TIME = "16:30:00"

SCHOOL_NAME = "San Miguel Elementary School"
GRADE_LEVEL = "Grade 6"
IPROG_API_TOKEN = os.environ.get("IPROG_API_TOKEN", "YOUR_IPROG_API_TOKEN_HERE")
IPROG_API_URL = "https://sms.iprogtech.com/api/v1/sms_messages"
IS_SQLITE = False
UPLOAD_FOLDER = os.path.join('static', 'uploads')
QR_FOLDER = os.path.join('static', 'qr_codes')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)

def get_ph_date():
    return datetime.now(PH_TZ).date()

def get_ph_datetime():
    return datetime.now(PH_TZ)

def format_time_12hr(t):
    if not t:
        return ""
    try:
        return datetime.strptime(t, "%H:%M:%S").strftime("%I:%M:%S %p")
    except:
        return t

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
            g._database = pg_db
            return pg_db
        except Exception as e:
            raise e
    IS_SQLITE = True
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
            cur.execute('CREATE TABLE IF NOT EXISTS students (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, grade_section TEXT NOT NULL, parent_name TEXT, parent_contact TEXT, qr_code_path TEXT, photo TEXT, photo_data TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, date TEXT NOT NULL, time_in TEXT, time_out TEXT, status TEXT DEFAULT "Present", scanned_by TEXT, time_in_am TEXT, time_out_am TEXT, time_in_pm TEXT, time_out_pm TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS teachers (id INTEGER PRIMARY KEY AUTOINCREMENT, teacher_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, subject TEXT, contact TEXT)')
            cur.execute("SELECT * FROM users WHERE username='admin'")
            if not cur.fetchone():
                cur.execute("INSERT INTO users (username, password, role, status) VALUES ('admin', 'admin123', 'Admin', 'approved')")
        else:
            cur.execute('CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT \'Teacher\', status TEXT DEFAULT \'pending\')')
            cur.execute('CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, grade_section TEXT NOT NULL, parent_name TEXT, parent_contact TEXT, qr_code_path TEXT, photo TEXT, photo_data TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS attendance ( id SERIAL PRIMARY KEY, student_id TEXT NOT NULL, date TEXT NOT NULL, time_in TEXT, time_out TEXT, status TEXT DEFAULT \'Present\', scanned_by TEXT, time_in_am TEXT, time_out_am TEXT, time_in_pm TEXT, time_out_pm TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS teachers (id SERIAL PRIMARY KEY, teacher_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, subject TEXT, contact TEXT)')
            cur.execute("SELECT * FROM users WHERE username='admin'")
            if not cur.fetchone():
                cur.execute("INSERT INTO users (username, password, role, status) VALUES ('admin', 'admin123', 'Admin', 'approved')")
        try:
            cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS photo_data TEXT")
        except:
            pass
        db.commit()

try:
    init_db()
except Exception as e:
    print(f"DB Init Error: {e}")

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
        return r.status_code == 200
    except Exception:
        return False

def generate_id_card(student):
    W, H = 700, 1050
    card = Image.new('RGB', (W, H), '#ffffff')
    draw = ImageDraw.Draw(card)
    def get_font(size, bold=False):
        paths = ["arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        for fp in paths:
            try:
                return ImageFont.truetype(fp, size)
            except:
                continue
        return ImageFont.load_default()
    font_header = get_font(26, True)
    font_name = get_font(32, True)
    font_detail = get_font(24, False)
    font_small = get_font(20, False)
    font_qr = get_font(18, True)
    draw.rectangle([0, 0, W, 110], fill='#0d47a1')
    draw.text((W//2, 55), SCHOOL_NAME.upper(), fill='white', font=font_header, anchor='mm')
    draw.rectangle([0, 110, W, 155], fill='#ffca28')
    draw.text((W//2, 132), GRADE_LEVEL, fill='black', font=font_small, anchor='mm')
    photo_data = student.get('photo_data')
    photo_file = student.get('photo')
    p_img = None
    if photo_data:
        try:
            decoded = base64.b64decode(photo_data)
            p_img = Image.open(io.BytesIO(decoded)).convert('RGB')
        except:
            p_img = None
    if not p_img and photo_file:
        for pp in [os.path.join(UPLOAD_FOLDER, str(photo_file)), os.path.join('static', str(photo_file))]:
            if os.path.exists(pp):
                try:
                    p_img = Image.open(pp).convert('RGB')
                    break
                except:
                    pass
    photo_size = 280
    photo_x = (W - photo_size)//2
    photo_y = 185
    if p_img:
        p_img = p_img.resize((photo_size, photo_size))
        mask = Image.new('L', (photo_size, photo_size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, photo_size, photo_size), fill=255)
        card.paste(p_img, (photo_x, photo_y), mask)
        draw.ellipse([photo_x, photo_y, photo_x+photo_size, photo_y+photo_size], outline='#0d47a1', width=4)
    else:
        draw.rectangle([photo_x, photo_y, photo_x+photo_size, photo_y+photo_size], fill='#e0e0e0')
        draw.text((W//2, photo_y+photo_size//2), "NO PHOTO", fill='#616161', font=font_detail, anchor='mm')
    draw.text((W//2, 530), str(student.get('name','')).upper(), fill='#0a2a5e', font=font_name, anchor='mm')
    draw.text((W//2, 575), f"ID: {student.get('student_id','')}", fill='#424242', font=font_detail, anchor='mm')
    draw.text((W//2, 610), f"{student.get('grade_section','')}", fill='#424242', font=font_detail, anchor='mm')
    qr_data = str(student.get('student_id',''))
    qr_img = qrcode.make(qr_data)
    qr_img = qr_img.resize((320, 320))
    card.paste(qr_img, ((W-320)//2, 660))
    draw.text((W//2, 1010), "SCAN FOR ATTENDANCE", fill='#757575', font=font_qr, anchor='mm')
    return card

@app.route('/student_photo/<student_id>')
def student_photo(student_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT photo_data FROM students WHERE student_id=%s", [student_id])
    s = cur.fetchone()
    if s and s.get('photo_data'):
        try:
            img_bytes = base64.b64decode(s['photo_data'])
            return send_file(io.BytesIO(img_bytes), mimetype='image/jpeg')
        except:
            pass
    return redirect(url_for('static', filename='default.png'))

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
    absent_today = total - present_today if present_today!=0 else 0
    cur.execute("SELECT s.name, a.time_in_am, a.time_out_am, a.time_in_pm, a.time_out_pm, a.status FROM attendance a JOIN students s ON a.student_id=s.student_id WHERE a.date=%s ORDER BY a.id DESC LIMIT 5", [today_str])
    recent = cur.fetchall()
    return render_template('dashboard.html', total=total, present=present_today, late=late_today, absent=absent_today, recent=recent, school=SCHOOL_NAME, grade=GRADE_LEVEL, today=get_ph_date(), format_time=format_time_12hr, late_am=LATE_CUTOFF, late_pm=LATE_CUTOFF_PM)

@app.route('/students')
def students():
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
        flash('Unauthorized!', 'danger')
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
    return render_template('students.html', students=students, total=total, present_today=present_today, absent_today=absent_today, late_today=late_today, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/download_qr/<student_id>')
def download_qr(student_id):
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
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
        cur.execute("UPDATE teachers SET teacher_id=%s, name=%s, subject=%s, contact=%s WHERE id=%s", (teacher_id, name, subject, contact, id))
        db.commit()
        flash(f'Teacher {name} updated', 'success')
        return redirect(url_for('teachers'))
    return render_template('edit_teacher.html', teacher=teacher, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/delete_teacher/<int:id>', methods=['POST'])
def delete_teacher(id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM teachers WHERE id =%s", (id,))
    db.commit()
    return redirect(url_for('teachers'))

@app.route('/register_student', methods=['GET', 'POST'])
def register_student():
    if not session.get('logged_in') or session['role']!= 'Admin':
        flash('Admin access only', 'danger')
        return redirect(url_for('students'))
    qr_path = None
    student_name = None
    student_id = None
    if request.method == 'POST':
        student_id = request.form['student_id'].strip()
        name = request.form['name'].strip()
        section = request.form['grade_section'].strip()
        pname = request.form['parent_name'].strip()
        pcontact = request.form['parent_contact'].strip()
        photo_filename = None
        photo_data_b64 = None
        file = request.files.get('photo')
        if file and file.filename!= '':
            try:
                file_bytes = file.read()
                photo_data_b64 = base64.b64encode(file_bytes).decode('utf-8')
                ext = os.path.splitext(secure_filename(file.filename))[1].lower() or '.jpg'
                photo_filename = f"{secure_filename(student_id)}{ext}"
                try:
                    with open(os.path.join(UPLOAD_FOLDER, photo_filename), 'wb') as f:
                        f.write(file_bytes)
                except:
                    pass
            except Exception as e:
                print(f"Photo read error: {e}")
        qr = qrcode.make(student_id)
        qr_filename = f"qr_codes/{student_id}.png"
        qr_full_path = f"static/{qr_filename}"
        try:
            qr.save(qr_full_path)
        except:
            pass
        db = get_db()
        cur = db.cursor()
        try:
            cur.execute("INSERT INTO students(student_id, name, grade_section, parent_name, parent_contact, qr_code_path, photo, photo_data) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", (student_id, name, section, pname, pcontact, qr_filename, photo_filename, photo_data_b64))
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
    try:
        if not session.get('logged_in'):
            return jsonify({'status': 'error', 'message': 'Session expired'}), 401
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'status': 'error', 'message': 'No JSON'}), 400
        student_id = data.get('student_id') or data.get('qr_data')
        if not student_id:
            return jsonify({'status': 'error', 'message': 'No student ID'}), 400
        student_id = str(student_id).strip()
        slot = str(data.get('slot', '')).lower().strip()
        if not slot:
            st = str(data.get('scan_type', 'in')).lower().strip()
            if st == 'in':
                slot = 'morning_in'
            else:
                slot = 'morning_out'
        now = get_ph_datetime()
        today_str = get_ph_date().isoformat()
        cur_time_24 = now.strftime("%H:%M:%S")
        cur_time_12 = now.strftime("%I:%M:%S %p")
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT * FROM students WHERE student_id=%s", [student_id])
        student = cur.fetchone()
        if not student:
            return jsonify({'status': 'error', 'message': f'Student ID {student_id} not found'}), 404
        cur.execute("SELECT * FROM attendance WHERE student_id=%s AND date=%s", (student_id, today_str))
        record = cur.fetchone()

        if slot == 'morning_in':
            status = 'Late' if cur_time_24 > LATE_CUTOFF else 'Present'
            if not record:
                cur.execute("INSERT INTO attendance(student_id, date, time_in, time_in_am, status, scanned_by) VALUES(%s,%s,%s,%s,%s,%s)", (student_id, today_str, cur_time_24, cur_time_24, status, session.get('username','system')))
                db.commit()
                message = f"{student['name']} MORNING IN: {cur_time_12} - {status}"
                try:
                    send_iprog_sms(student.get('parent_contact'), f"{SCHOOL_NAME}: {message}")
                except:
                    pass
                return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
            if not record.get('time_in_am'):
                cur.execute("UPDATE attendance SET time_in_am=%s, time_in=%s, status=%s WHERE id=%s", (cur_time_24, cur_time_24, status, record['id']))
                db.commit()
                message = f"{student['name']} MORNING IN: {cur_time_12} - {status}"
                try:
                    send_iprog_sms(student.get('parent_contact'), f"{SCHOOL_NAME}: {message}")
                except:
                    pass
                return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
            return jsonify({'status': 'error', 'message': f"{student['name']} already has MORNING IN"})

        if slot == 'morning_out':
            if not record or not record.get('time_in_am'):
                return jsonify({'status': 'error', 'message': f"{student['name']} no MORNING IN yet"})
            if record.get('time_out_am'):
                return jsonify({'status': 'error', 'message': f"{student['name']} already has MORNING OUT"})
            cur.execute("UPDATE attendance SET time_out_am=%s, time_out=%s WHERE id=%s", (cur_time_24, cur_time_24, record['id']))
            db.commit()
            message = f"{student['name']} MORNING OUT: {cur_time_12}"
            try:
                send_iprog_sms(student.get('parent_contact'), f"{SCHOOL_NAME}: {message}")
            except:
                pass
            return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})

        if slot == 'afternoon_in':
            status_pm = 'Late' if cur_time_24 > LATE_CUTOFF_PM else 'Present'
            if not record:
                cur.execute("INSERT INTO attendance(student_id, date, time_in_pm, status, scanned_by) VALUES(%s,%s,%s,%s,%s)", (student_id, today_str, cur_time_24, status_pm, session.get('username','system')))
                db.commit()
                message = f"{student['name']} AFTERNOON IN: {cur_time_12} - {status_pm} (Half Day - No Morning)"
                try:
                    send_iprog_sms(student.get('parent_contact'), f"{SCHOOL_NAME}: {message}")
                except:
                    pass
                return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})
            if record.get('time_in_pm'):
                return jsonify({'status': 'error', 'message': f"{student['name']} already has AFTERNOON IN"})
            cur.execute("UPDATE attendance SET time_in_pm=%s WHERE id=%s", (cur_time_24, record['id']))
            if not record.get('time_in_am'):
                cur.execute("UPDATE attendance SET status=%s WHERE id=%s", (status_pm, record['id']))
            db.commit()
            half_note = " (Half Day - No Morning)" if not record.get('time_in_am') else ""
            message = f"{student['name']} AFTERNOON IN: {cur_time_12} - {status_pm}{half_note}"
            try:
                send_iprog_sms(student.get('parent_contact'), f"{SCHOOL_NAME}: {message}")
            except:
                pass
            return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})

        if slot == 'afternoon_out':
            if not record or not record.get('time_in_pm'):
                return jsonify({'status': 'error', 'message': f"{student['name']} no AFTERNOON IN yet"})
            if record.get('time_out_pm'):
                return jsonify({'status': 'error', 'message': f"{student['name']} already has AFTERNOON OUT"})
            cur.execute("UPDATE attendance SET time_out_pm=%s WHERE id=%s", (cur_time_24, record['id']))
            db.commit()
            message = f"{student['name']} AFTERNOON OUT: {cur_time_12}"
            try:
                send_iprog_sms(student.get('parent_contact'), f"{SCHOOL_NAME}: {message}")
            except:
                pass
            return jsonify({'status': 'success', 'name': student['name'], 'section': student['grade_section'], 'time': cur_time_12, 'message': message})

        return jsonify({'status': 'error', 'message': 'Invalid slot'}), 400

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': f'Server error: {str(e)}'}), 500

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
    attendance_rate = round((present / total * 100), 1) if total > 0 else 0
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
        trend_absent.append(max(0, total - p))
    cur.execute("SELECT grade_section, COUNT(*) as total_students FROM students GROUP BY grade_section")
    sections = cur.fetchall()
    section_stats = []
    for sec in sections:
        cur.execute("SELECT COUNT(DISTINCT s.student_id) as c FROM attendance a JOIN students s ON a.student_id = s.student_id WHERE a.date=%s AND s.grade_section=%s AND a.time_in_am IS NOT NULL", [filter_date, sec['grade_section']])
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
        flash('Unauthorized', 'danger')
        return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM attendance")
    db.commit()
    flash('Attendance Reset', 'success')
    return redirect(url_for('attendance'))

@app.route('/delete_student/<student_id>', methods=['POST'])
def delete_student(student_id):
    if not session.get('logged_in') or session['role']!= 'Admin':
        return redirect(url_for('students'))
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM attendance WHERE student_id =%s", (student_id,))
    cur.execute("DELETE FROM students WHERE student_id =%s", (student_id,))
    db.commit()
    return redirect(url_for('students'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/edit_student/<student_id>', methods=['GET', 'POST'])
def edit_student(student_id):
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM students WHERE student_id =%s", (student_id,))
    student = cur.fetchone()
    if not student:
        return redirect(url_for('students'))
    if request.method == 'POST':
        name = request.form['name'].strip()
        section = request.form['grade_section'].strip()
        pname = request.form['parent_name'].strip()
        pcontact = request.form['parent_contact'].strip()
        photo_filename = student.get('photo')
        photo_data_b64 = student.get('photo_data')
        file = request.files.get('photo')
        if file and file.filename!= '':
            try:
                file_bytes = file.read()
                photo_data_b64 = base64.b64encode(file_bytes).decode('utf-8')
                ext = os.path.splitext(secure_filename(file.filename))[1].lower() or '.jpg'
                photo_filename = f"{secure_filename(student_id)}{ext}"
                try:
                    with open(os.path.join(UPLOAD_FOLDER, photo_filename), 'wb') as f:
                        f.write(file_bytes)
                except:
                    pass
            except Exception as e:
                print(f"Photo error: {e}")
        cur.execute("UPDATE students SET name=%s, grade_section=%s, parent_name=%s, parent_contact=%s, photo=%s, photo_data=%s WHERE student_id=%s", (name, section, pname, pcontact, photo_filename, photo_data_b64, student_id))
        db.commit()
        flash(f'Student {name} updated', 'success')
        return redirect(url_for('students'))
    return render_template('edit_student.html', student=student, school=SCHOOL_NAME, grade=GRADE_LEVEL)

@app.route('/view_attendance/<student_id>')
def view_attendance(student_id):
    if not session.get('logged_in') or session['role'] not in ['Admin', 'Teacher']:
        return redirect(url_for('dashboard'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM students WHERE student_id=%s", [student_id])
    student = cur.fetchone()
    if not student:
        return redirect(url_for('students'))
    cur.execute("SELECT DISTINCT date FROM attendance ORDER BY date DESC")
    all_dates = cur.fetchall()
    school_days = len(all_dates) or 1
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
