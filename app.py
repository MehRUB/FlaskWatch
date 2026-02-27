import os
import uuid
import sqlite3
import secrets
import base64
import threading
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_from_directory, g)
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'flasktube-dev-secret')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
DB_PATH       = os.path.join(BASE_DIR, 'youtube.db')

ALLOWED_VIDEO = {'mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv'}
ALLOWED_IMAGE = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024

GMAIL_USER        = os.environ.get('GMAIL_USER', '')
GMAIL_PASS        = os.environ.get('GMAIL_PASS', '')
SITE_URL          = os.environ.get('SITE_URL', 'http://localhost:5000')
GOOGLE_VISION_KEY = os.environ.get('GOOGLE_VISION_KEY', '')

# ── Admin / Moderator emails ───────────────────────────────────────────────────
# Add any email here to give them admin access
ADMIN_EMAILS = {
    'mehdiprodmus@gmail.com',
    # 'another@gmail.com',   ← just uncomment and add more
}


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
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            username       TEXT    UNIQUE NOT NULL,
            email          TEXT    UNIQUE NOT NULL,
            password       TEXT    NOT NULL,
            avatar         TEXT    DEFAULT NULL,
            banner         TEXT    DEFAULT NULL,
            bio            TEXT    DEFAULT '',
            channel_name   TEXT    DEFAULT NULL,
            channel_links  TEXT    DEFAULT '',
            is_verified    INTEGER DEFAULT 0,
            created        TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS videos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid          TEXT    UNIQUE NOT NULL,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            title         TEXT    NOT NULL,
            description   TEXT    DEFAULT '',
            filename      TEXT    NOT NULL,
            thumbnail     TEXT    DEFAULT NULL,
            views         INTEGER DEFAULT 0,
            is_removed    INTEGER DEFAULT 0,
            created       TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS likes (
            user_id  INTEGER NOT NULL REFERENCES users(id),
            video_id INTEGER NOT NULL REFERENCES videos(id),
            PRIMARY KEY (user_id, video_id)
        );
        CREATE TABLE IF NOT EXISTS comment_votes (
            user_id    INTEGER NOT NULL REFERENCES users(id),
            comment_id INTEGER NOT NULL REFERENCES comments(id),
            vote       INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, comment_id)
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
    migrations = [
        ('users',         'banner',        'TEXT DEFAULT NULL'),
        ('users',         'channel_name',  'TEXT DEFAULT NULL'),
        ('users',         'channel_links', "TEXT DEFAULT ''"),
        ('users',         'is_verified',   'INTEGER DEFAULT 0'),
        ('videos',        'is_removed',    'INTEGER DEFAULT 0'),
        ('comments',      'is_removed',    'INTEGER DEFAULT 0'),
    ]
    for table, col, defn in migrations:
        try: db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {defn}')
        except: pass
    # Create comment_votes if missing
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS comment_votes (
            user_id INTEGER NOT NULL, comment_id INTEGER NOT NULL,
            vote INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (user_id, comment_id))''')
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
        if not is_admin():
            flash('Access denied.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def current_user():
    if 'user_id' not in session: return None
    return get_db().execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()

def is_admin():
    user = current_user()
    return bool(user and user['email'] in ADMIN_EMAILS)

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
    current_user=current_user, is_admin=is_admin,
    ADMIN_EMAILS=ADMIN_EMAILS
)

# ─── AI Moderation ────────────────────────────────────────────────────────────

def scan_image_for_explicit_content(image_path):
    if not GOOGLE_VISION_KEY: return False
    try:
        import urllib.request, json
        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        payload = json.dumps({'requests': [{'image': {'content': image_data},
                              'features': [{'type': 'SAFE_SEARCH_DETECTION'}]}]}).encode()
        req = urllib.request.Request(
            f'https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_KEY}',
            data=payload, headers={'Content-Type': 'application/json'})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        safe = data['responses'][0].get('safeSearchAnnotation', {})
        bad  = {'LIKELY', 'VERY_LIKELY'}
        return safe.get('adult') in bad or safe.get('violence') in bad or safe.get('racy') in bad
    except Exception as e:
        print(f'[VISION ERROR] {e}')
        return False

def extract_video_thumbnail(video_path, output_path):
    try:
        import subprocess
        r = subprocess.run(['ffmpeg', '-i', video_path, '-ss', '00:00:01',
                           '-vframes', '1', '-q:v', '2', output_path, '-y'],
                          capture_output=True, timeout=30)
        return r.returncode == 0 and os.path.exists(output_path)
    except: return False


# ─── File serving ─────────────────────────────────────────────────────────────

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password']
        db = get_db()
        if db.execute('SELECT id FROM users WHERE username=? OR email=?', (username, email)).fetchone():
            flash('Username or email already taken.', 'error')
            return redirect(url_for('register'))
            
        # You (mehdiprodmus@gmail.com) get the badge automatically
        auto_verified = 1 if email in ADMIN_EMAILS else 0
        
        db.execute('INSERT INTO users (username, email, password, is_verified) VALUES (?,?,?,?)',
                   (username, email, generate_password_hash(password), auto_verified))
        db.commit()
        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        session['user_id'] = user['id']
        flash('Welcome to FlaskTube!', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')

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


# ─── Account Settings ─────────────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    db = get_db()
    user = current_user()
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_profile':
            channel_name = request.form.get('channel_name', '').strip()
            bio = request.form.get('bio', '').strip()
            db.execute('UPDATE users SET channel_name=?, bio=? WHERE id=?', 
                       (channel_name, bio, user['id']))
            db.commit()
            flash('Profile updated!', 'success')
            
        elif action == 'delete_account':
            if user['email'] in ADMIN_EMAILS:
                flash('Admin accounts cannot be deleted.', 'error')
                return redirect(url_for('settings'))
            
            # Remove content but keep DB integrity
            db.execute('UPDATE videos SET is_removed=1 WHERE user_id=?', (user['id'],))
            db.execute('DELETE FROM users WHERE id=?', (user['id'],))
            db.commit()
            session.clear()
            flash('Account deleted permanently.', 'info')
            return redirect(url_for('index'))
            
    return render_template('settings.html', user=user)

@app.route('/settings/change-password', methods=['POST'])
@login_required
def change_password():
    user     = current_user()
    old_pass = request.form.get('old_password', '')
    new_pass = request.form.get('new_password', '')
    confirm  = request.form.get('confirm_password', '')
    if not check_password_hash(user['password'], old_pass):
        flash('Current password is incorrect.', 'error')
        return redirect(url_for('settings'))
    if len(new_pass) < 6:
        flash('New password must be at least 6 characters.', 'error')
        return redirect(url_for('settings'))
    if new_pass != confirm:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('settings'))
    get_db().execute('UPDATE users SET password=? WHERE id=?',
                     (generate_password_hash(new_pass), user['id']))
    get_db().commit()
    flash('Password changed successfully!', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/change-email', methods=['POST'])
@login_required
def change_email():
    user     = current_user()
    password = request.form.get('password', '')
    new_email = request.form.get('new_email', '').strip()
    if not check_password_hash(user['password'], password):
        flash('Password is incorrect.', 'error')
        return redirect(url_for('settings'))
    if get_db().execute('SELECT id FROM users WHERE email=?', (new_email,)).fetchone():
        flash('That email is already in use.', 'error')
        return redirect(url_for('settings'))
    get_db().execute('UPDATE users SET email=? WHERE id=?', (new_email, user['id']))
    get_db().commit()
    flash('Email updated successfully!', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/delete-account', methods=['POST'])
@login_required
def delete_account():
    user     = current_user()
    password = request.form.get('password', '')
    if not check_password_hash(user['password'], password):
        flash('Password is incorrect.', 'error')
        return redirect(url_for('settings'))
    if user['email'] in ADMIN_EMAILS:
        flash('Admin accounts cannot be deleted.', 'error')
        return redirect(url_for('settings'))
    db = get_db()
    # Remove all their content
    db.execute('UPDATE videos SET is_removed=1 WHERE user_id=?', (user['id'],))
    db.execute('DELETE FROM subscriptions WHERE subscriber_id=? OR channel_id=?', (user['id'], user['id']))
    db.execute('DELETE FROM saved_videos WHERE user_id=?', (user['id'],))
    db.execute('DELETE FROM likes WHERE user_id=?', (user['id'],))
    db.execute('DELETE FROM comment_votes WHERE user_id=?', (user['id'],))
    db.execute('DELETE FROM users WHERE id=?', (user['id'],))
    db.commit()
    session.clear()
    flash('Your account has been deleted.', 'info')
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
            SELECT v.*, u.username, u.avatar, u.channel_name, u.email, u.is_verified,
                   (SELECT COUNT(*) FROM likes WHERE video_id=v.id) AS like_count
            FROM videos v JOIN users u ON v.user_id=u.id
            WHERE v.is_removed=0 AND (v.title LIKE ? OR v.description LIKE ?)
            ORDER BY v.created DESC LIMIT ? OFFSET ?
        ''', (f'%{q}%', f'%{q}%', limit, offset)).fetchall()
    else:
        rows = db.execute('''
            SELECT v.*, u.username, u.avatar, u.channel_name, u.email, u.is_verified,
                   (SELECT COUNT(*) FROM likes WHERE video_id=v.id) AS like_count
            FROM videos v JOIN users u ON v.user_id=u.id
            WHERE v.is_removed=0
            ORDER BY v.created DESC LIMIT ? OFFSET ?
        ''', (limit, offset)).fetchall()
    videos = [{
        'uuid':       r['uuid'],      'title':      r['title'],
        'thumbnail':  r['thumbnail'], 'views':      fmt_views(r['views']),
        'username':   r['channel_name'] or r['username'],
        'user_id':    r['user_id'],   'created':    time_ago(r['created']),
        'like_count': r['like_count'],
        'is_verified': bool(r['is_verified']),
    } for r in rows]
    return jsonify({'videos': videos, 'has_more': len(rows) == limit})

@app.route('/search')
def search():
    return render_template('index.html', search_query=request.args.get('q', ''))


# ─── Upload ───────────────────────────────────────────────────────────────────

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
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
        if thumb and thumb.filename and allowed_image(thumb.filename):
            thumb_filename = save_file(thumb, 'thumbnails')
        else:
            vid_abs   = os.path.join(UPLOAD_FOLDER, vid_filename)
            thumb_out = os.path.join(UPLOAD_FOLDER, 'thumbnails', f'{uuid.uuid4().hex}.jpg')
            os.makedirs(os.path.dirname(thumb_out), exist_ok=True)
            if extract_video_thumbnail(vid_abs, thumb_out):
                thumb_filename = 'thumbnails/' + os.path.basename(thumb_out)
        if thumb_filename and GOOGLE_VISION_KEY:
            thumb_abs = os.path.join(UPLOAD_FOLDER, thumb_filename)
            if scan_image_for_explicit_content(thumb_abs):
                try: os.remove(os.path.join(UPLOAD_FOLDER, vid_filename))
                except: pass
                try: os.remove(thumb_abs)
                except: pass
                flash('⚠️ Your video was rejected — it appears to contain inappropriate content.', 'error')
                return redirect(url_for('upload'))
        vid_uuid = uuid.uuid4().hex
        db = get_db()
        db.execute('INSERT INTO videos (uuid, user_id, title, description, filename, thumbnail) VALUES (?,?,?,?,?,?)',
                   (vid_uuid, session['user_id'], title, desc, vid_filename, thumb_filename))
        db.commit()
        flash('Video uploaded!', 'success')
        return redirect(url_for('watch', vid_uuid=vid_uuid))
    return render_template('upload.html')


# ─── Watch ────────────────────────────────────────────────────────────────────

@app.route('/watch/<vid_uuid>')
def watch(vid_uuid):
    db    = get_db()
    video = db.execute('''
        SELECT v.*, u.username, u.avatar, u.channel_name, u.id AS channel_id,
               u.email AS channel_email, u.is_verified AS channel_verified,
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

    uid = session.get('user_id')
    comments = db.execute('''
        SELECT c.*, u.username, u.avatar, u.id AS commenter_id, u.is_verified AS commenter_verified,
               (SELECT COUNT(*) FROM comment_votes WHERE comment_id=c.id AND vote=1)  AS likes,
               (SELECT COUNT(*) FROM comment_votes WHERE comment_id=c.id AND vote=-1) AS dislikes
        FROM comments c JOIN users u ON c.user_id=u.id
        WHERE c.video_id=? AND c.is_removed=0 ORDER BY c.created DESC
    ''', (video['id'],)).fetchall()

    # Get current user's votes on comments
    user_votes = {}
    if uid:
        votes = db.execute('SELECT comment_id, vote FROM comment_votes WHERE user_id=?', (uid,)).fetchall()
        user_votes = {v['comment_id']: v['vote'] for v in votes}

    liked  = bool(uid and db.execute('SELECT 1 FROM likes WHERE user_id=? AND video_id=?', (uid, video['id'])).fetchone())
    subbed = bool(uid and db.execute('SELECT 1 FROM subscriptions WHERE subscriber_id=? AND channel_id=?', (uid, video['channel_id'])).fetchone())
    saved  = bool(uid and db.execute('SELECT 1 FROM saved_videos WHERE user_id=? AND video_id=?', (uid, video['id'])).fetchone())
    related = db.execute('''
        SELECT v.uuid, v.title, v.thumbnail, v.views, u.username, u.channel_name, u.is_verified
        FROM videos v JOIN users u ON v.user_id=u.id
        WHERE v.uuid != ? AND v.is_removed=0 ORDER BY RANDOM() LIMIT 10
    ''', (vid_uuid,)).fetchall()
    return render_template('watch.html', video=video, comments=comments,
                           liked=liked, subbed=subbed, saved=saved,
                           related=related, user_votes=user_votes,
                           ADMIN_EMAILS=ADMIN_EMAILS)


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
    db    = get_db()
    video = db.execute('SELECT id FROM videos WHERE uuid=? AND is_removed=0', (vid_uuid,)).fetchone()
    if not video: return jsonify({'error': 'Not found'}), 404
    body = request.json.get('body', '').strip()
    if not body: return jsonify({'error': 'Empty comment'}), 400
    db.execute('INSERT INTO comments (user_id, video_id, body) VALUES (?,?,?)',
               (session['user_id'], video['id'], body))
    db.commit()
    u = db.execute('SELECT username, avatar, is_verified FROM users WHERE id=?', (session['user_id'],)).fetchone()
    comment_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    return jsonify({'id': comment_id, 'username': u['username'], 'avatar': u['avatar'],
                    'body': body, 'created': 'just now',
                    'is_verified': bool(u['is_verified']),
                    'commenter_id': session['user_id']})

@app.route('/api/comment-vote/<int:comment_id>', methods=['POST'])
@login_required
def vote_comment(comment_id):
    vote = request.json.get('vote')  # 1 = like, -1 = dislike
    if vote not in (1, -1): return jsonify({'error': 'Invalid vote'}), 400
    uid = session['user_id']
    db  = get_db()
    if not db.execute('SELECT id FROM comments WHERE id=? AND is_removed=0', (comment_id,)).fetchone():
        return jsonify({'error': 'Comment not found'}), 404
    existing = db.execute('SELECT vote FROM comment_votes WHERE user_id=? AND comment_id=?',
                          (uid, comment_id)).fetchone()
    if existing:
        if existing['vote'] == vote:
            # clicking same button = undo
            db.execute('DELETE FROM comment_votes WHERE user_id=? AND comment_id=?', (uid, comment_id))
            user_vote = 0
        else:
            db.execute('UPDATE comment_votes SET vote=? WHERE user_id=? AND comment_id=?',
                       (vote, uid, comment_id))
            user_vote = vote
    else:
        db.execute('INSERT INTO comment_votes (user_id, comment_id, vote) VALUES (?,?,?)',
                   (uid, comment_id, vote))
        user_vote = vote
    db.commit()
    likes    = db.execute('SELECT COUNT(*) FROM comment_votes WHERE comment_id=? AND vote=1',  (comment_id,)).fetchone()[0]
    dislikes = db.execute('SELECT COUNT(*) FROM comment_votes WHERE comment_id=? AND vote=-1', (comment_id,)).fetchone()[0]
    return jsonify({'likes': likes, 'dislikes': dislikes, 'user_vote': user_vote})

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
        SELECT r.*, u.username AS reporter_name,
               v.title AS video_title, v.uuid AS video_uuid, v.is_removed AS video_removed,
               c.body AS comment_body, c.is_removed AS comment_removed,
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
    db = get_db()
    db.execute('UPDATE videos SET is_removed=0 WHERE uuid=?', (vid_uuid,))
    db.commit()
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
    if target and target['email'] in ADMIN_EMAILS:
        flash('Cannot ban an admin.', 'error')
        return redirect(url_for('admin'))
    db = get_db()
    db.execute('UPDATE videos SET is_removed=1 WHERE user_id=?', (user_id,))
    db.execute('DELETE FROM users WHERE id=?', (user_id,))
    db.commit()
    flash('User banned.', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/verify-user/<int:user_id>', methods=['POST'])
@admin_required
def admin_verify_user(user_id):
    db = get_db()
    db.execute('UPDATE users SET is_verified=1 WHERE id=?', (user_id,))
    db.commit()
    flash('User has been verified!', 'success')
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
    is_owner = (uid == channel_id)
    channel_is_admin = owner['email'] in ADMIN_EMAILS
    return render_template('channel.html', owner=owner, videos=videos,
                           sub_count=sub_count, subbed=subbed,
                           is_owner=is_owner, channel_is_admin=channel_is_admin,
                           ADMIN_EMAILS=ADMIN_EMAILS)

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