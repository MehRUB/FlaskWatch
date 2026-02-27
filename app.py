import os
import uuid
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)  # Railway variables always win over .env
except ImportError:
    pass
import sqlite3
import secrets
import base64
import threading
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_from_directory, g)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'flasktube-secret-change-in-production'

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
DB_PATH       = os.path.join(BASE_DIR, 'youtube.db')

ALLOWED_VIDEO = {'mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv'}
ALLOWED_IMAGE = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024

# ── Config from environment variables ─────────────────────────────────────────
GMAIL_USER  = os.environ.get('GMAIL_USER', '')
GMAIL_PASS  = os.environ.get('GMAIL_PASS', '')
SITE_URL    = os.environ.get('SITE_URL', 'http://localhost:5000')
GOOGLE_VISION_KEY  = os.environ.get('GOOGLE_VISION_KEY', '')
ADMIN_EMAIL        = 'mehdiprodmus@gmail.com'  # only this email gets admin


# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            email         TEXT    UNIQUE NOT NULL,
            password      TEXT    NOT NULL,
            avatar        TEXT    DEFAULT NULL,
            banner        TEXT    DEFAULT NULL,
            bio           TEXT    DEFAULT '',
            channel_name  TEXT    DEFAULT NULL,
            channel_links TEXT    DEFAULT '',
            is_verified   INTEGER DEFAULT 0,
            verify_token  TEXT    DEFAULT NULL,
            created       TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS videos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid         TEXT    UNIQUE NOT NULL,
            user_id      INTEGER NOT NULL REFERENCES users(id),
            title        TEXT    NOT NULL,
            description  TEXT    DEFAULT '',
            filename     TEXT    NOT NULL,
            thumbnail    TEXT    DEFAULT NULL,
            views        INTEGER DEFAULT 0,
            is_removed   INTEGER DEFAULT 0,
            remove_reason TEXT   DEFAULT NULL,
            created      TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS likes (
            user_id  INTEGER NOT NULL REFERENCES users(id),
            video_id INTEGER NOT NULL REFERENCES videos(id),
            PRIMARY KEY (user_id, video_id)
        );
        CREATE TABLE IF NOT EXISTS comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            video_id   INTEGER NOT NULL REFERENCES videos(id),
            body       TEXT    NOT NULL,
            is_removed INTEGER DEFAULT 0,
            created    TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            subscriber_id INTEGER NOT NULL REFERENCES users(id),
            channel_id    INTEGER NOT NULL REFERENCES users(id),
            PRIMARY KEY (subscriber_id, channel_id)
        );
        CREATE TABLE IF NOT EXISTS saved_videos (
            user_id  INTEGER NOT NULL REFERENCES users(id),
            video_id INTEGER NOT NULL REFERENCES videos(id),
            saved_at TEXT    DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, video_id)
        );
        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER REFERENCES users(id),
            video_id    INTEGER REFERENCES videos(id),
            comment_id  INTEGER REFERENCES comments(id),
            reason      TEXT    NOT NULL,
            status      TEXT    DEFAULT 'pending',
            created     TEXT    DEFAULT (datetime('now'))
        );
    ''')
    # Safe migrations for existing databases
    migrations = [
        ('users',   'banner',        'TEXT DEFAULT NULL'),
        ('users',   'channel_name',  'TEXT DEFAULT NULL'),
        ('users',   'channel_links', "TEXT DEFAULT ''"),
        ('users',   'is_verified',   'INTEGER DEFAULT 0'),
        ('users',   'verify_token',  'TEXT DEFAULT NULL'),
        ('videos',  'is_removed',    'INTEGER DEFAULT 0'),
        ('videos',  'remove_reason', 'TEXT DEFAULT NULL'),
        ('comments','is_removed',    'INTEGER DEFAULT 0'),
    ]
    for table, col, defn in migrations:
        try: db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {defn}')
        except: pass
    db.commit()
    db.close()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'info')
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user or user['email'] != ADMIN_EMAIL or not user['is_verified']:
            flash('Access denied.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def current_user():
    if 'user_id' not in session: return None
    return get_db().execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()

def is_admin():
    user = current_user()
    return bool(user and user['email'] == ADMIN_EMAIL and user['is_verified'])

def allowed_video(fn): return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_VIDEO
def allowed_image(fn): return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_IMAGE

def save_file(file, subfolder=''):
    ext    = file.filename.rsplit('.', 1)[1].lower()
    fname  = f'{uuid.uuid4().hex}.{ext}'
    folder = os.path.join(UPLOAD_FOLDER, subfolder)
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, fname))
    return f'{subfolder}/{fname}' if subfolder else fname

def time_ago(dt_str):
    try: dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
    except: return dt_str
    s = (datetime.utcnow() - dt).total_seconds()
    if s < 60:       return 'just now'
    if s < 3600:     return f'{int(s//60)}m ago'
    if s < 86400:    return f'{int(s//3600)}h ago'
    if s < 2592000:  return f'{int(s//86400)}d ago'
    if s < 31536000: return f'{int(s//2592000)}mo ago'
    return f'{int(s//31536000)}y ago'

def fmt_views(n):
    if n >= 1_000_000: return f'{n/1_000_000:.1f}M'
    if n >= 1_000:     return f'{n/1_000:.1f}K'
    return str(n)

app.jinja_env.globals.update(
    time_ago=time_ago, fmt_views=fmt_views,
    current_user=current_user, is_admin=is_admin
)


# ─── Email via Gmail SMTP ─────────────────────────────────────────────────────

def send_verification_email(to_email, token):
    gmail_user = os.environ.get('GMAIL_USER')
    gmail_pass = os.environ.get('GMAIL_PASS')
    site_url = os.environ.get('SITE_URL')

    msg = MIMEText(f'Verify your account here: {site_url}/verify/{token}')
    msg['Subject'] = 'FlaskTube Verification'
    msg['From'] = gmail_user
    msg['To'] = to_email

    try:
        # We use standard SMTP on port 2525
        server = smtplib.SMTP('smtp.gmail.com', 2525, timeout=10)
        server.starttls()  # Secure the connection
        server.login(gmail_user, gmail_pass)
        server.send_message(msg)
        server.quit()
        print(f'[EMAIL SENT] to {to_email}')
        return True
    except Exception as e:
        print(f'[EMAIL ERROR] {e}')
        return False

# ─── AI Content Moderation via Google Vision ──────────────────────────────────

def scan_image_for_explicit_content(image_path):
    """
    Scan an image using Google Vision API Safe Search.
    Returns True if content is LIKELY or VERY_LIKELY explicit/violent.
    Returns False if clean or if API is not configured.
    """
    if not GOOGLE_VISION_KEY:
        return False  # No API key = skip scanning

    try:
        import urllib.request, json

        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        payload = json.dumps({
            'requests': [{
                'image': {'content': image_data},
                'features': [{'type': 'SAFE_SEARCH_DETECTION'}]
            }]
        }).encode()

        req = urllib.request.Request(
            f'https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_KEY}',
            data=payload,
            headers={'Content-Type': 'application/json'}
        )
        res  = urllib.request.urlopen(req, timeout=10)
        data = json.loads(res.read())

        safe    = data['responses'][0].get('safeSearchAnnotation', {})
        bad     = {'LIKELY', 'VERY_LIKELY'}
        flagged = (
            safe.get('adult')    in bad or
            safe.get('violence') in bad or
            safe.get('racy')     in bad
        )

        if flagged:
            print(f'[AI MODERATION] Flagged image: {image_path} — {safe}')

        return flagged

    except Exception as e:
        print(f'[VISION API ERROR] {e}')
        return False  # On error, don't block the upload


def extract_video_thumbnail(video_path, output_path):
    """Extract first frame from video using ffmpeg (if available)."""
    try:
        import subprocess
        result = subprocess.run([
            'ffmpeg', '-i', video_path,
            '-ss', '00:00:01',
            '-vframes', '1',
            '-q:v', '2',
            output_path,
            '-y'
        ], capture_output=True, timeout=30)
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception:
        return False


# ─── File serving ─────────────────────────────────────────────────────────────

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email    = request.form['email'].strip()
        password = request.form['password']
        db = get_db()

        if db.execute('SELECT id FROM users WHERE username=? OR email=?',
                      (username, email)).fetchone():
            flash('Username or email already taken.', 'error')
            return redirect(url_for('register'))

        token = secrets.token_urlsafe(32)
        db.execute(
            'INSERT INTO users (username, email, password, verify_token, is_verified) VALUES (?,?,?,?,0)',
            (username, email, generate_password_hash(password), token)
        )
        db.commit()
        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        session['user_id'] = user['id']

        threading.Thread(target=send_verification_email, args=(email, token), daemon=True).start()
        flash('Account created! Check your email to verify your account.', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/verify/<token>')
def verify_email(token):
    db   = get_db()
    user = db.execute('SELECT * FROM users WHERE verify_token=?', (token,)).fetchone()
    if not user:
        flash('Invalid or expired verification link.', 'error')
        return redirect(url_for('index'))
    db.execute('UPDATE users SET is_verified=1, verify_token=NULL WHERE id=?', (user['id'],))
    db.commit()
    session['user_id'] = user['id']
    flash('✅ Email verified! You can now upload videos and comment.', 'success')
    return redirect(url_for('index'))


@app.route('/resend-verification')
@login_required
def resend_verification():
    db   = get_db()
    user = current_user()
    if user['is_verified']:
        flash('Your email is already verified.', 'info')
        return redirect(url_for('index'))
    token = secrets.token_urlsafe(32)
    db.execute('UPDATE users SET verify_token=? WHERE id=?', (token, user['id']))
    db.commit()
    threading.Thread(target=send_verification_email, args=(user['email'], token), daemon=True).start()
    flash('Verification email sent! Check your inbox (and spam folder).', 'success')
    return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        db   = get_db()
        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if not user or not check_password_hash(user['password'], password):
            flash('Invalid username or password.', 'error')
            return redirect(url_for('login'))
        session['user_id'] = user['id']
        return redirect(request.args.get('next') or url_for('index'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# ─── Home & Search ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/videos')
def api_videos():
    page   = int(request.args.get('page', 1))
    limit  = 12
    offset = (page - 1) * limit
    q      = request.args.get('q', '').strip()
    db     = get_db()
    if q:
        rows = db.execute('''
            SELECT v.*, u.username, u.avatar, u.channel_name,
                   (SELECT COUNT(*) FROM likes WHERE video_id=v.id) AS like_count
            FROM videos v JOIN users u ON v.user_id=u.id
            WHERE v.is_removed=0 AND (v.title LIKE ? OR v.description LIKE ?)
            ORDER BY v.created DESC LIMIT ? OFFSET ?
        ''', (f'%{q}%', f'%{q}%', limit, offset)).fetchall()
    else:
        rows = db.execute('''
            SELECT v.*, u.username, u.avatar, u.channel_name,
                   (SELECT COUNT(*) FROM likes WHERE video_id=v.id) AS like_count
            FROM videos v JOIN users u ON v.user_id=u.id
            WHERE v.is_removed=0
            ORDER BY v.created DESC LIMIT ? OFFSET ?
        ''', (limit, offset)).fetchall()
    videos = [{
        'uuid':       r['uuid'],      'title':     r['title'],
        'thumbnail':  r['thumbnail'], 'views':     fmt_views(r['views']),
        'username':   r['channel_name'] or r['username'],
        'user_id':    r['user_id'],   'created':   time_ago(r['created']),
        'like_count': r['like_count'],
    } for r in rows]
    return jsonify({'videos': videos, 'has_more': len(rows) == limit})

@app.route('/search')
def search():
    return render_template('index.html', search_query=request.args.get('q', ''))


# ─── Upload ───────────────────────────────────────────────────────────────────

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    user = current_user()
    if not user['is_verified']:
        return render_template('verify_required.html')

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        desc  = request.form.get('description', '').strip()
        video = request.files.get('video')
        thumb = request.files.get('thumbnail')

        if not title or not video or not video.filename:
            flash('Title and a video file are required.', 'error')
            return redirect(url_for('upload'))
        if not allowed_video(video.filename):
            flash('Unsupported video format. Use MP4, WebM, MOV, AVI, or MKV.', 'error')
            return redirect(url_for('upload'))

        vid_filename   = save_file(video, 'videos')
        thumb_filename = None

        # Handle thumbnail
        if thumb and thumb.filename and allowed_image(thumb.filename):
            thumb_filename = save_file(thumb, 'thumbnails')
        else:
            # Try auto-extract thumbnail from video using ffmpeg
            vid_abs   = os.path.join(UPLOAD_FOLDER, vid_filename)
            thumb_out = os.path.join(UPLOAD_FOLDER, 'thumbnails', f'{uuid.uuid4().hex}.jpg')
            os.makedirs(os.path.dirname(thumb_out), exist_ok=True)
            if extract_video_thumbnail(vid_abs, thumb_out):
                thumb_filename = 'thumbnails/' + os.path.basename(thumb_out)

        # ── AI Moderation ──────────────────────────────────────────────────────
        # Scan thumbnail for explicit content (fast, cheap — just one image)
        if thumb_filename and GOOGLE_VISION_KEY:
            thumb_abs = os.path.join(UPLOAD_FOLDER, thumb_filename)
            if scan_image_for_explicit_content(thumb_abs):
                # Delete the uploaded files
                try: os.remove(os.path.join(UPLOAD_FOLDER, vid_filename))
                except: pass
                try: os.remove(thumb_abs)
                except: pass
                flash('⚠️ Your video was rejected because it appears to contain inappropriate content.', 'error')
                return redirect(url_for('upload'))

        vid_uuid = uuid.uuid4().hex
        db = get_db()
        db.execute(
            'INSERT INTO videos (uuid, user_id, title, description, filename, thumbnail) VALUES (?,?,?,?,?,?)',
            (vid_uuid, session['user_id'], title, desc, vid_filename, thumb_filename)
        )
        db.commit()
        flash('Video uploaded successfully!', 'success')
        return redirect(url_for('watch', vid_uuid=vid_uuid))
    return render_template('upload.html')


# ─── Watch ────────────────────────────────────────────────────────────────────

@app.route('/watch/<vid_uuid>')
def watch(vid_uuid):
    db    = get_db()
    video = db.execute('''
        SELECT v.*, u.username, u.avatar, u.channel_name, u.id AS channel_id,
               (SELECT COUNT(*) FROM likes WHERE video_id=v.id)                AS like_count,
               (SELECT COUNT(*) FROM subscriptions WHERE channel_id=v.user_id) AS sub_count
        FROM videos v JOIN users u ON v.user_id=u.id
        WHERE v.uuid=? AND v.is_removed=0
    ''', (vid_uuid,)).fetchone()
    if not video:
        flash('Video not found or has been removed.', 'error')
        return redirect(url_for('index'))

    db.execute('UPDATE videos SET views=views+1 WHERE uuid=?', (vid_uuid,))
    db.commit()

    comments = db.execute('''
        SELECT c.*, u.username, u.avatar FROM comments c
        JOIN users u ON c.user_id=u.id
        WHERE c.video_id=? AND c.is_removed=0 ORDER BY c.created DESC
    ''', (video['id'],)).fetchall()

    uid    = session.get('user_id')
    liked  = bool(uid and db.execute('SELECT 1 FROM likes WHERE user_id=? AND video_id=?',           (uid, video['id'])).fetchone())
    subbed = bool(uid and db.execute('SELECT 1 FROM subscriptions WHERE subscriber_id=? AND channel_id=?', (uid, video['channel_id'])).fetchone())
    saved  = bool(uid and db.execute('SELECT 1 FROM saved_videos WHERE user_id=? AND video_id=?',    (uid, video['id'])).fetchone())

    related = db.execute('''
        SELECT v.uuid, v.title, v.thumbnail, v.views, u.username, u.channel_name
        FROM videos v JOIN users u ON v.user_id=u.id
        WHERE v.uuid != ? AND v.is_removed=0 ORDER BY RANDOM() LIMIT 10
    ''', (vid_uuid,)).fetchall()

    return render_template('watch.html', video=video, comments=comments,
                           liked=liked, subbed=subbed, saved=saved, related=related)


# ─── Like / Save / Comment / Subscribe ───────────────────────────────────────

@app.route('/api/like/<vid_uuid>', methods=['POST'])
@login_required
def toggle_like(vid_uuid):
    db    = get_db()
    video = db.execute('SELECT id FROM videos WHERE uuid=? AND is_removed=0', (vid_uuid,)).fetchone()
    if not video: return jsonify({'error': 'Not found'}), 404
    uid = session['user_id']
    if db.execute('SELECT 1 FROM likes WHERE user_id=? AND video_id=?', (uid, video['id'])).fetchone():
        db.execute('DELETE FROM likes WHERE user_id=? AND video_id=?', (uid, video['id']))
        liked = False
    else:
        db.execute('INSERT INTO likes (user_id, video_id) VALUES (?,?)', (uid, video['id']))
        liked = True
    db.commit()
    count = db.execute('SELECT COUNT(*) FROM likes WHERE video_id=?', (video['id'],)).fetchone()[0]
    return jsonify({'liked': liked, 'count': count})

@app.route('/api/save/<vid_uuid>', methods=['POST'])
@login_required
def toggle_save(vid_uuid):
    db    = get_db()
    video = db.execute('SELECT id FROM videos WHERE uuid=? AND is_removed=0', (vid_uuid,)).fetchone()
    if not video: return jsonify({'error': 'Not found'}), 404
    uid = session['user_id']
    if db.execute('SELECT 1 FROM saved_videos WHERE user_id=? AND video_id=?', (uid, video['id'])).fetchone():
        db.execute('DELETE FROM saved_videos WHERE user_id=? AND video_id=?', (uid, video['id']))
        saved = False
    else:
        db.execute('INSERT INTO saved_videos (user_id, video_id) VALUES (?,?)', (uid, video['id']))
        saved = True
    db.commit()
    return jsonify({'saved': saved})

@app.route('/api/comment/<vid_uuid>', methods=['POST'])
@login_required
def add_comment(vid_uuid):
    user = current_user()
    if not user['is_verified']:
        return jsonify({'error': 'Please verify your email to comment.'}), 403
    db    = get_db()
    video = db.execute('SELECT id FROM videos WHERE uuid=? AND is_removed=0', (vid_uuid,)).fetchone()
    if not video: return jsonify({'error': 'Not found'}), 404
    body = request.json.get('body', '').strip()
    if not body: return jsonify({'error': 'Empty comment'}), 400
    db.execute('INSERT INTO comments (user_id, video_id, body) VALUES (?,?,?)',
               (session['user_id'], video['id'], body))
    db.commit()
    u = db.execute('SELECT username, avatar FROM users WHERE id=?', (session['user_id'],)).fetchone()
    return jsonify({'username': u['username'], 'avatar': u['avatar'], 'body': body, 'created': 'just now'})

@app.route('/api/subscribe/<int:channel_id>', methods=['POST'])
@login_required
def toggle_subscribe(channel_id):
    uid = session['user_id']
    if uid == channel_id: return jsonify({'error': 'Cannot subscribe to yourself'}), 400
    db = get_db()
    if db.execute('SELECT 1 FROM subscriptions WHERE subscriber_id=? AND channel_id=?', (uid, channel_id)).fetchone():
        db.execute('DELETE FROM subscriptions WHERE subscriber_id=? AND channel_id=?', (uid, channel_id))
        subbed = False
    else:
        db.execute('INSERT INTO subscriptions (subscriber_id, channel_id) VALUES (?,?)', (uid, channel_id))
        subbed = True
    db.commit()
    count = db.execute('SELECT COUNT(*) FROM subscriptions WHERE channel_id=?', (channel_id,)).fetchone()[0]
    return jsonify({'subbed': subbed, 'count': count})


# ─── Reporting ────────────────────────────────────────────────────────────────

@app.route('/api/report', methods=['POST'])
@login_required
def report():
    data       = request.json
    video_id   = data.get('video_id')
    comment_id = data.get('comment_id')
    reason     = data.get('reason', '').strip()
    if not reason: return jsonify({'error': 'Reason required'}), 400
    db = get_db()
    if video_id:
        row = db.execute('SELECT id FROM videos WHERE uuid=?', (video_id,)).fetchone()
        if not row: return jsonify({'error': 'Not found'}), 404
        video_id = row['id']
    db.execute('INSERT INTO reports (reporter_id, video_id, comment_id, reason) VALUES (?,?,?,?)',
               (session['user_id'], video_id, comment_id, reason))
    db.commit()
    return jsonify({'ok': True})


# ─── Admin ────────────────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin():
    db = get_db()
    reports = db.execute('''
        SELECT r.*,
               u.username  AS reporter_name,
               v.title     AS video_title,  v.uuid      AS video_uuid,
               v.is_removed AS video_removed,
               c.body      AS comment_body, c.is_removed AS comment_removed,
               cu.username AS commenter_name
        FROM reports r
        LEFT JOIN users    u  ON r.reporter_id = u.id
        LEFT JOIN videos   v  ON r.video_id    = v.id
        LEFT JOIN comments c  ON r.comment_id  = c.id
        LEFT JOIN users    cu ON c.user_id     = cu.id
        ORDER BY r.created DESC
    ''').fetchall()
    stats = {
        'total_users':     db.execute('SELECT COUNT(*) FROM users').fetchone()[0],
        'total_videos':    db.execute('SELECT COUNT(*) FROM videos WHERE is_removed=0').fetchone()[0],
        'pending_reports': db.execute("SELECT COUNT(*) FROM reports WHERE status='pending'").fetchone()[0],
        'removed_videos':  db.execute('SELECT COUNT(*) FROM videos WHERE is_removed=1').fetchone()[0],
        'ai_enabled':      bool(GOOGLE_VISION_KEY),
        'email_enabled':   bool(GMAIL_USER),
    }
    return render_template('admin.html', reports=reports, stats=stats)

@app.route('/admin/remove-video/<vid_uuid>', methods=['POST'])
@admin_required
def admin_remove_video(vid_uuid):
    db = get_db()
    db.execute('UPDATE videos SET is_removed=1 WHERE uuid=?', (vid_uuid,))
    db.execute("UPDATE reports SET status='resolved' WHERE video_id=(SELECT id FROM videos WHERE uuid=?)", (vid_uuid,))
    db.commit()
    flash('Video removed.', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/restore-video/<vid_uuid>', methods=['POST'])
@admin_required
def admin_restore_video(vid_uuid):
    get_db().execute('UPDATE videos SET is_removed=0 WHERE uuid=?', (vid_uuid,))
    get_db().commit()
    flash('Video restored.', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/remove-comment/<int:comment_id>', methods=['POST'])
@admin_required
def admin_remove_comment(comment_id):
    db = get_db()
    db.execute('UPDATE comments SET is_removed=1 WHERE id=?', (comment_id,))
    db.execute("UPDATE reports SET status='resolved' WHERE comment_id=?", (comment_id,))
    db.commit()
    flash('Comment removed.', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/dismiss-report/<int:report_id>', methods=['POST'])
@admin_required
def admin_dismiss_report(report_id):
    db = get_db()
    db.execute("UPDATE reports SET status='dismissed' WHERE id=?", (report_id,))
    db.commit()
    flash('Report dismissed.', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/ban-user/<int:user_id>', methods=['POST'])
@admin_required
def admin_ban_user(user_id):
    target = get_db().execute('SELECT email FROM users WHERE id=?', (user_id,)).fetchone()
    if target and target['email'] == ADMIN_EMAIL:
        flash('Cannot ban the admin.', 'error')
        return redirect(url_for('admin'))
    db = get_db()
    db.execute('UPDATE videos SET is_removed=1 WHERE user_id=?', (user_id,))
    db.execute('DELETE FROM users WHERE id=?', (user_id,))
    db.commit()
    flash('User banned and all their content removed.', 'success')
    return redirect(url_for('admin'))


# ─── Channel ──────────────────────────────────────────────────────────────────

@app.route('/channel/<int:channel_id>')
def channel(channel_id):
    db    = get_db()
    owner = db.execute('SELECT * FROM users WHERE id=?', (channel_id,)).fetchone()
    if not owner:
        flash('Channel not found.', 'error')
        return redirect(url_for('index'))
    videos = db.execute('''
        SELECT v.*, (SELECT COUNT(*) FROM likes WHERE video_id=v.id) AS like_count
        FROM videos v WHERE v.user_id=? AND v.is_removed=0 ORDER BY v.created DESC
    ''', (channel_id,)).fetchall()
    sub_count = db.execute('SELECT COUNT(*) FROM subscriptions WHERE channel_id=?', (channel_id,)).fetchone()[0]
    uid      = session.get('user_id')
    subbed   = bool(uid and db.execute('SELECT 1 FROM subscriptions WHERE subscriber_id=? AND channel_id=?', (uid, channel_id)).fetchone())
    return render_template('channel.html', owner=owner, videos=videos,
                           sub_count=sub_count, subbed=subbed, is_owner=(uid == channel_id))

@app.route('/channel/<int:channel_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_channel(channel_id):
    if session['user_id'] != channel_id:
        flash('You can only edit your own channel.', 'error')
        return redirect(url_for('channel', channel_id=channel_id))
    db = get_db()
    if request.method == 'POST':
        updates = {
            'channel_name':  request.form.get('channel_name', '').strip() or None,
            'bio':           request.form.get('bio', '').strip(),
            'channel_links': request.form.get('channel_links', '').strip(),
        }
        for field, subfolder in [('avatar', 'avatars'), ('banner', 'banners')]:
            f = request.files.get(field)
            if f and f.filename and allowed_image(f.filename):
                old = db.execute(f'SELECT {field} FROM users WHERE id=?', (channel_id,)).fetchone()
                if old and old[field]:
                    try: os.remove(os.path.join(UPLOAD_FOLDER, old[field]))
                    except: pass
                updates[field] = save_file(f, subfolder)
        set_clause = ', '.join(f'{k}=?' for k in updates)
        db.execute(f'UPDATE users SET {set_clause} WHERE id=?', list(updates.values()) + [channel_id])
        db.commit()
        flash('Channel updated!', 'success')
        return redirect(url_for('channel', channel_id=channel_id))
    owner = db.execute('SELECT * FROM users WHERE id=?', (channel_id,)).fetchone()
    return render_template('edit_channel.html', owner=owner)


# ─── Library ──────────────────────────────────────────────────────────────────

@app.route('/library')
@login_required
def library():
    db    = get_db()
    saved = db.execute('''
        SELECT v.*, u.username, u.channel_name, sv.saved_at,
               (SELECT COUNT(*) FROM likes WHERE video_id=v.id) AS like_count
        FROM saved_videos sv
        JOIN videos v ON sv.video_id=v.id
        JOIN users u ON v.user_id=u.id
        WHERE sv.user_id=? AND v.is_removed=0
        ORDER BY sv.saved_at DESC
    ''', (session['user_id'],)).fetchall()
    return render_template('library.html', saved=saved)


# ─── Boot ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)