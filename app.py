import os
import uuid
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory, abort
import psycopg2
import psycopg2.extras

app = Flask(__name__, static_folder='.')

DATABASE_URL = os.environ.get('DATABASE_URL', '')
ADMIN_PASSWORD = 'admin123'

def get_db():
    """Get a database connection."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """Initialize database tables."""
    if not DATABASE_URL:
        print("WARNING: No DATABASE_URL set — using file-based fallback")
        return
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            whatsapp TEXT DEFAULT '',
            transaction_id TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            token TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            approved_at TIMESTAMP
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS student_ids (
            id TEXT PRIMARY KEY,
            used BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            used_at TIMESTAMP
        )
    ''')
    
    # Seed default student IDs if table is empty
    cur.execute("SELECT COUNT(*) FROM student_ids")
    count = cur.fetchone()[0]
    if count == 0:
        for sid in ['HH-001', 'HH-002', 'HH-003', 'HH-004', 'HH-005']:
            cur.execute(
                "INSERT INTO student_ids (id, used) VALUES (%s, %s)",
                (sid, False)
            )
    
    conn.commit()
    cur.close()
    conn.close()

def generate_token():
    return uuid.uuid4().hex

# ────────── INIT DB ──────────
init_db()

# ────────── FILE FALLBACK ──────────
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
SUBMISSIONS_FILE = os.path.join(DATA_DIR, 'submissions.json')
STUDENT_IDS_FILE = os.path.join(DATA_DIR, 'student_ids.json')
os.makedirs(DATA_DIR, exist_ok=True)

def load_submissions_file():
    if not os.path.exists(SUBMISSIONS_FILE):
        return []
    with open(SUBMISSIONS_FILE, 'r') as f:
        return json.load(f)

def save_submissions_file(data):
    with open(SUBMISSIONS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def load_student_ids_file():
    if not os.path.exists(STUDENT_IDS_FILE):
        return []
    with open(STUDENT_IDS_FILE, 'r') as f:
        return json.load(f)

def save_student_ids_file(data):
    with open(STUDENT_IDS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# ────────── API ENDPOINTS ──────────

@app.route('/api/submit', methods=['POST'])
def api_submit():
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    whatsapp = data.get('whatsapp', '').strip()
    transaction_id = data.get('transaction_id', '').strip()

    if not name or not email:
        return jsonify({'error': 'Name and email are required'}), 400

    sub_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO submissions (id, name, email, whatsapp, transaction_id, status, created_at) VALUES (%s, %s, %s, %s, %s, 'pending', %s)",
                (sub_id, name, email, whatsapp, transaction_id, created_at)
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            return jsonify({'error': f'Database error: {str(e)}'}), 500
    else:
        submissions = load_submissions_file()
        submission = {
            'id': sub_id, 'name': name, 'email': email,
            'whatsapp': whatsapp, 'transaction_id': transaction_id,
            'status': 'pending', 'token': None, 'created_at': created_at
        }
        submissions.append(submission)
        save_submissions_file(submissions)

    return jsonify({
        'message': 'Your request is pending approval — we will notify you shortly.',
        'id': sub_id
    }), 200

@app.route('/api/submissions', methods=['GET'])
def api_get_submissions():
    auth = request.headers.get('Authorization', '')
    if auth != f'Bearer {ADMIN_PASSWORD}':
        return jsonify({'error': 'Unauthorized'}), 401

    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM submissions ORDER BY created_at DESC")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            # Convert datetime objects to strings
            for row in rows:
                for key in ('created_at', 'approved_at'):
                    if row.get(key):
                        row[key] = row[key].isoformat() if hasattr(row[key], 'isoformat') else str(row[key])
            return jsonify([dict(r) for r in rows]), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        submissions = load_submissions_file()
        submissions.sort(key=lambda s: s.get('created_at', ''), reverse=True)
        return jsonify(submissions), 200

@app.route('/api/approve', methods=['POST'])
def api_approve():
    auth = request.headers.get('Authorization', '')
    if auth != f'Bearer {ADMIN_PASSWORD}':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(force=True)
    submission_id = data.get('id', '')
    action = data.get('action', 'approve')

    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor()
            if action == 'approve':
                token = generate_token()
                cur.execute(
                    "UPDATE submissions SET status='approved', token=%s, approved_at=NOW() WHERE id=%s",
                    (token, submission_id)
                )
            else:
                cur.execute(
                    "UPDATE submissions SET status='rejected', token=NULL WHERE id=%s",
                    (submission_id,)
                )
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'message': f'Submission {action}d successfully'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        submissions = load_submissions_file()
        found = False
        for sub in submissions:
            if sub['id'] == submission_id:
                if action == 'approve':
                    sub['status'] = 'approved'
                    sub['token'] = generate_token()
                    sub['approved_at'] = datetime.now(timezone.utc).isoformat()
                else:
                    sub['status'] = 'rejected'
                    sub['token'] = None
                found = True
                break
        if not found:
            return jsonify({'error': 'Submission not found'}), 404
        save_submissions_file(submissions)
        return jsonify({'message': f'Submission {action}d successfully'}), 200

@app.route('/api/check-access', methods=['GET'])
def api_check_access():
    token = request.args.get('token', '').strip()
    if not token:
        return jsonify({'valid': False, 'error': 'No token provided'}), 400

    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT name, email FROM submissions WHERE token=%s AND status='approved'",
                (token,)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                return jsonify({'valid': True, 'name': row['name'], 'email': row['email']}), 200
            return jsonify({'valid': False, 'error': 'Invalid or expired token'}), 403
        except Exception as e:
            return jsonify({'valid': False, 'error': str(e)}), 500
    else:
        submissions = load_submissions_file()
        for sub in submissions:
            if sub.get('token') == token and sub.get('status') == 'approved':
                return jsonify({'valid': True, 'name': sub['name'], 'email': sub['email']}), 200
        return jsonify({'valid': False, 'error': 'Invalid or expired token'}), 403

# ────────── STUDENT DISCOUNT API ──────────

@app.route('/api/validate-student', methods=['POST'])
def api_validate_student():
    data = request.get_json(force=True)
    student_id = data.get('student_id', '').strip().upper()

    if not student_id:
        return jsonify({'valid': False, 'error': 'Please enter a Student ID'}), 400

    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM student_ids WHERE id=%s", (student_id,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if not row:
                return jsonify({'valid': False, 'error': 'Invalid Student ID. Please check and try again.'}), 400
            if row['used']:
                return jsonify({'valid': False, 'error': 'This Student ID has already been used'}), 400
            return jsonify({
                'valid': True, 'discount': 30,
                'message': '✅ 30% discount applied! Price reduced from $1 (300 PKR) to $0.70 (≈ 210 PKR)'
            }), 200
        except Exception as e:
            return jsonify({'valid': False, 'error': str(e)}), 500
    else:
        ids = load_student_ids_file()
        for entry in ids:
            if entry['id'] == student_id:
                if entry['used']:
                    return jsonify({'valid': False, 'error': 'This Student ID has already been used'}), 400
                return jsonify({
                    'valid': True, 'discount': 30,
                    'message': '✅ 30% discount applied! Price reduced from $1 (300 PKR) to $0.70 (≈ 210 PKR)'
                }), 200
        return jsonify({'valid': False, 'error': 'Invalid Student ID. Please check and try again.'}), 400

@app.route('/api/mark-student-used', methods=['POST'])
def api_mark_student_used():
    data = request.get_json(force=True)
    student_id = data.get('student_id', '').strip().upper()

    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE student_ids SET used=TRUE, used_at=NOW() WHERE id=%s", (student_id,))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'message': 'Student ID marked as used'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        ids = load_student_ids_file()
        for entry in ids:
            if entry['id'] == student_id:
                entry['used'] = True
                entry['used_at'] = datetime.now(timezone.utc).isoformat()
                save_student_ids_file(ids)
                return jsonify({'message': 'Student ID marked as used'}), 200
        return jsonify({'error': 'Student ID not found'}), 404

@app.route('/api/student-ids', methods=['GET'])
def api_get_student_ids():
    auth = request.headers.get('Authorization', '')
    if auth != f'Bearer {ADMIN_PASSWORD}':
        return jsonify({'error': 'Unauthorized'}), 401

    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM student_ids ORDER BY id")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            for row in rows:
                for key in ('created_at', 'used_at'):
                    if row.get(key):
                        row[key] = row[key].isoformat() if hasattr(row[key], 'isoformat') else str(row[key])
            return jsonify([dict(r) for r in rows]), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        ids = load_student_ids_file()
        return jsonify(ids), 200

@app.route('/api/reset-student-id', methods=['POST'])
def api_reset_student_id():
    auth = request.headers.get('Authorization', '')
    if auth != f'Bearer {ADMIN_PASSWORD}':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(force=True)
    student_id = data.get('student_id', '').strip().upper()

    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE student_ids SET used=FALSE, used_at=NULL WHERE id=%s", (student_id,))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'message': f'Student ID {student_id} reset to unused'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        ids = load_student_ids_file()
        for entry in ids:
            if entry['id'] == student_id:
                entry['used'] = False
                entry.pop('used_at', None)
                save_student_ids_file(ids)
                return jsonify({'message': f'Student ID {student_id} reset to unused'}), 200
        return jsonify({'error': 'Student ID not found'}), 404

@app.route('/api/add-student-id', methods=['POST'])
def api_add_student_id():
    auth = request.headers.get('Authorization', '')
    if auth != f'Bearer {ADMIN_PASSWORD}':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(force=True)
    student_id = data.get('student_id', '').strip().upper()

    if not student_id:
        return jsonify({'error': 'Student ID is required'}), 400

    if DATABASE_URL:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("INSERT INTO student_ids (id, used) VALUES (%s, FALSE) ON CONFLICT (id) DO NOTHING", (student_id,))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'message': f'Student ID {student_id} added successfully'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        ids = load_student_ids_file()
        for entry in ids:
            if entry['id'] == student_id:
                return jsonify({'error': 'Student ID already exists'}), 400
        ids.append({'id': student_id, 'used': False, 'created_at': datetime.now(timezone.utc).isoformat()})
        save_student_ids_file(ids)
        return jsonify({'message': f'Student ID {student_id} added successfully'}), 200


# ────────── STATIC FILE SERVING ──────────

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    try:
        return send_from_directory('.', filename)
    except:
        abort(404)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    print(f"Starting Healthcare Hustlers server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)