import os
import uuid
import sqlite3
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_from_directory, g)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'youtube-clone-secret-key-change-in-production'

# Use absolute paths so the app works from ANY working directory
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
DB_PATH       = os.path.join(BASE_DIR, 'youtube.db')

ALLOWED_VIDEO = {'mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv'}
ALLOWED_IMAGE = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024   # 2 GB


# Database

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db:
        db.close()


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
            created       TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS videos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid        TEXT    UNIQUE NOT NULL,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            title       TEXT    NOT NULL,
            description TEXT    DEFAULT '',
            filename    TEXT    NOT NULL,
            thumbnail   TEXT    DEFAULT NULL,
            views       INTEGER DEFAULT 0,
            created     TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS likes (
            user_id  INTEGER NOT NULL REFERENCES users(id),
            video_id INTEGER NOT NULL REFERENCES videos(id),
            PRIMARY KEY (user_id, video_id)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL REFERENCES users(id),
            video_id INTEGER NOT NULL REFERENCES videos(id),
            body     TEXT    NOT NULL,
            created  TEXT    DEFAULT (datetime('now'))
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
    ''')
    # Safely add new columns to existing databases
    for col, definition in [
        ('banner',        'TEXT DEFAULT NULL'),
        ('channel_name',  'TEXT DEFAULT NULL'),
        ('channel_links', "TEXT DEFAULT ''"),
    ]:
        try:
            db.execute(f'ALTER TABLE users ADD COLUMN {col} {definition}')
        except Exception:
            pass
    db.commit()
    db.close()


# Helpers

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'info')
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def current_user():
    if 'user_id' not in session:
        return None
    db = get_db()
    return db.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()


def allowed_video(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE


def save_file(file, subfolder=''):
    """Save uploaded file; always return forward-slash path for URL safety."""
    ext    = file.filename.rsplit('.', 1)[1].lower()
    fname  = f"{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(UPLOAD_FOLDER, subfolder)
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, fname))
    return f"{subfolder}/{fname}" if subfolder else fname


def time_ago(dt_str):
    try:
        dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
    except Exception:
        return dt_str
    diff = datetime.utcnow() - dt
    s    = diff.total_seconds()
    if s < 60:       return 'just now'
    if s < 3600:     return f"{int(s//60)}m ago"
    if s < 86400:    return f"{int(s//3600)}h ago"
    if s < 2592000:  return f"{int(s//86400)}d ago"
    if s < 31536000: return f"{int(s//2592000)}mo ago"
    return f"{int(s//31536000)}y ago"


def fmt_views(n):
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)


app.jinja_env.globals.update(
    time_ago=time_ago, fmt_views=fmt_views, current_user=current_user
)


# File serving  (uses absolute UPLOAD_FOLDER — the key fix)

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# Auth

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
        db.execute('INSERT INTO users (username, email, password) VALUES (?,?,?)',
                   (username, email, generate_password_hash(password)))
        db.commit()
        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        session['user_id'] = user['id']
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


# Home & Search

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
            WHERE v.title LIKE ? OR v.description LIKE ?
            ORDER BY v.created DESC LIMIT ? OFFSET ?
        ''', (f'%{q}%', f'%{q}%', limit, offset)).fetchall()
    else:
        rows = db.execute('''
            SELECT v.*, u.username, u.avatar, u.channel_name,
                   (SELECT COUNT(*) FROM likes WHERE video_id=v.id) AS like_count
            FROM videos v JOIN users u ON v.user_id=u.id
            ORDER BY v.created DESC LIMIT ? OFFSET ?
        ''', (limit, offset)).fetchall()
    videos = []
    for r in rows:
        display_name = r['channel_name'] or r['username']
        videos.append({
            'uuid':       r['uuid'],
            'title':      r['title'],
            'thumbnail':  r['thumbnail'],
            'views':      fmt_views(r['views']),
            'username':   display_name,
            'user_id':    r['user_id'],
            'created':    time_ago(r['created']),
            'like_count': r['like_count'],
        })
    return jsonify({'videos': videos, 'has_more': len(rows) == limit})


@app.route('/search')
def search():
    q = request.args.get('q', '')
    return render_template('index.html', search_query=q)


# Upload

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

        vid_uuid = uuid.uuid4().hex
        db = get_db()
        db.execute(
            'INSERT INTO videos (uuid, user_id, title, description, filename, thumbnail) '
            'VALUES (?,?,?,?,?,?)',
            (vid_uuid, session['user_id'], title, desc, vid_filename, thumb_filename)
        )
        db.commit()
        flash('Video uploaded successfully!', 'success')
        return redirect(url_for('watch', vid_uuid=vid_uuid))
    return render_template('upload.html')


# Watch

@app.route('/watch/<vid_uuid>')
def watch(vid_uuid):
    db    = get_db()
    video = db.execute('''
        SELECT v.*, u.username, u.avatar, u.channel_name, u.id AS channel_id,
               (SELECT COUNT(*) FROM likes WHERE video_id=v.id)               AS like_count,
               (SELECT COUNT(*) FROM subscriptions WHERE channel_id=v.user_id) AS sub_count
        FROM videos v JOIN users u ON v.user_id=u.id
        WHERE v.uuid=?
    ''', (vid_uuid,)).fetchone()
    if not video:
        flash('Video not found.', 'error')
        return redirect(url_for('index'))

    db.execute('UPDATE videos SET views=views+1 WHERE uuid=?', (vid_uuid,))
    db.commit()

    comments = db.execute('''
        SELECT c.*, u.username, u.avatar FROM comments c
        JOIN users u ON c.user_id=u.id
        WHERE c.video_id=? ORDER BY c.created DESC
    ''', (video['id'],)).fetchall()

    uid    = session.get('user_id')
    liked  = bool(uid and db.execute(
        'SELECT 1 FROM likes WHERE user_id=? AND video_id=?', (uid, video['id'])).fetchone())
    subbed = bool(uid and db.execute(
        'SELECT 1 FROM subscriptions WHERE subscriber_id=? AND channel_id=?',
        (uid, video['channel_id'])).fetchone())
    saved  = bool(uid and db.execute(
        'SELECT 1 FROM saved_videos WHERE user_id=? AND video_id=?',
        (uid, video['id'])).fetchone())

    related = db.execute('''
        SELECT v.uuid, v.title, v.thumbnail, v.views, u.username, u.channel_name
        FROM videos v JOIN users u ON v.user_id=u.id
        WHERE v.uuid != ? ORDER BY RANDOM() LIMIT 10
    ''', (vid_uuid,)).fetchall()

    return render_template('watch.html', video=video, comments=comments,
                           liked=liked, subbed=subbed, saved=saved, related=related)


# Like

@app.route('/api/like/<vid_uuid>', methods=['POST'])
@login_required
def toggle_like(vid_uuid):
    db    = get_db()
    video = db.execute('SELECT id FROM videos WHERE uuid=?', (vid_uuid,)).fetchone()
    if not video:
        return jsonify({'error': 'Not found'}), 404
    uid      = session['user_id']
    existing = db.execute(
        'SELECT 1 FROM likes WHERE user_id=? AND video_id=?', (uid, video['id'])).fetchone()
    if existing:
        db.execute('DELETE FROM likes WHERE user_id=? AND video_id=?', (uid, video['id']))
        liked = False
    else:
        db.execute('INSERT INTO likes (user_id, video_id) VALUES (?,?)', (uid, video['id']))
        liked = True
    db.commit()
    count = db.execute(
        'SELECT COUNT(*) FROM likes WHERE video_id=?', (video['id'],)).fetchone()[0]
    return jsonify({'liked': liked, 'count': count})


# Save

@app.route('/api/save/<vid_uuid>', methods=['POST'])
@login_required
def toggle_save(vid_uuid):
    db    = get_db()
    video = db.execute('SELECT id FROM videos WHERE uuid=?', (vid_uuid,)).fetchone()
    if not video:
        return jsonify({'error': 'Not found'}), 404
    uid      = session['user_id']
    existing = db.execute(
        'SELECT 1 FROM saved_videos WHERE user_id=? AND video_id=?', (uid, video['id'])).fetchone()
    if existing:
        db.execute('DELETE FROM saved_videos WHERE user_id=? AND video_id=?', (uid, video['id']))
        saved = False
    else:
        db.execute('INSERT INTO saved_videos (user_id, video_id) VALUES (?,?)', (uid, video['id']))
        saved = True
    db.commit()
    return jsonify({'saved': saved})


# Comments

@app.route('/api/comment/<vid_uuid>', methods=['POST'])
@login_required
def add_comment(vid_uuid):
    db    = get_db()
    video = db.execute('SELECT id FROM videos WHERE uuid=?', (vid_uuid,)).fetchone()
    if not video:
        return jsonify({'error': 'Not found'}), 404
    body = request.json.get('body', '').strip()
    if not body:
        return jsonify({'error': 'Empty comment'}), 400
    db.execute('INSERT INTO comments (user_id, video_id, body) VALUES (?,?,?)',
               (session['user_id'], video['id'], body))
    db.commit()
    user = db.execute('SELECT username, avatar FROM users WHERE id=?',
                      (session['user_id'],)).fetchone()
    return jsonify({'username': user['username'], 'avatar': user['avatar'],
                    'body': body, 'created': 'just now'})


# Subscribe

@app.route('/api/subscribe/<int:channel_id>', methods=['POST'])
@login_required
def toggle_subscribe(channel_id):
    uid = session['user_id']
    if uid == channel_id:
        return jsonify({'error': 'Cannot subscribe to yourself'}), 400
    db       = get_db()
    existing = db.execute(
        'SELECT 1 FROM subscriptions WHERE subscriber_id=? AND channel_id=?',
        (uid, channel_id)).fetchone()
    if existing:
        db.execute('DELETE FROM subscriptions WHERE subscriber_id=? AND channel_id=?',
                   (uid, channel_id))
        subbed = False
    else:
        db.execute('INSERT INTO subscriptions (subscriber_id, channel_id) VALUES (?,?)',
                   (uid, channel_id))
        subbed = True
    db.commit()
    count = db.execute(
        'SELECT COUNT(*) FROM subscriptions WHERE channel_id=?', (channel_id,)).fetchone()[0]
    return jsonify({'subbed': subbed, 'count': count})


# Channel

@app.route('/channel/<int:channel_id>')
def channel(channel_id):
    db    = get_db()
    owner = db.execute('SELECT * FROM users WHERE id=?', (channel_id,)).fetchone()
    if not owner:
        flash('Channel not found.', 'error')
        return redirect(url_for('index'))
    videos = db.execute('''
        SELECT v.*, (SELECT COUNT(*) FROM likes WHERE video_id=v.id) AS like_count
        FROM videos v WHERE v.user_id=? ORDER BY v.created DESC
    ''', (channel_id,)).fetchall()
    sub_count = db.execute(
        'SELECT COUNT(*) FROM subscriptions WHERE channel_id=?', (channel_id,)).fetchone()[0]
    uid      = session.get('user_id')
    subbed   = bool(uid and db.execute(
        'SELECT 1 FROM subscriptions WHERE subscriber_id=? AND channel_id=?',
        (uid, channel_id)).fetchone())
    is_owner = (uid == channel_id)
    return render_template('channel.html', owner=owner, videos=videos,
                           sub_count=sub_count, subbed=subbed, is_owner=is_owner)


# Channel Customization

@app.route('/channel/<int:channel_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_channel(channel_id):
    if session['user_id'] != channel_id:
        flash('You can only edit your own channel.', 'error')
        return redirect(url_for('channel', channel_id=channel_id))

    db = get_db()
    if request.method == 'POST':
        channel_name  = request.form.get('channel_name', '').strip()
        bio           = request.form.get('bio', '').strip()
        channel_links = request.form.get('channel_links', '').strip()
        avatar_file   = request.files.get('avatar')
        banner_file   = request.files.get('banner')

        updates = {
            'channel_name':  channel_name or None,
            'bio':           bio,
            'channel_links': channel_links,
        }

        if avatar_file and avatar_file.filename and allowed_image(avatar_file.filename):
            old = db.execute('SELECT avatar FROM users WHERE id=?', (channel_id,)).fetchone()
            if old and old['avatar']:
                old_path = os.path.join(UPLOAD_FOLDER, old['avatar'])
                if os.path.exists(old_path):
                    try: os.remove(old_path)
                    except: pass
            updates['avatar'] = save_file(avatar_file, 'avatars')

        if banner_file and banner_file.filename and allowed_image(banner_file.filename):
            old = db.execute('SELECT banner FROM users WHERE id=?', (channel_id,)).fetchone()
            if old and old['banner']:
                old_path = os.path.join(UPLOAD_FOLDER, old['banner'])
                if os.path.exists(old_path):
                    try: os.remove(old_path)
                    except: pass
            updates['banner'] = save_file(banner_file, 'banners')

        set_clause = ', '.join(f'{k}=?' for k in updates)
        values     = list(updates.values()) + [channel_id]
        db.execute(f'UPDATE users SET {set_clause} WHERE id=?', values)
        db.commit()
        flash('Channel updated!', 'success')
        return redirect(url_for('channel', channel_id=channel_id))

    owner = db.execute('SELECT * FROM users WHERE id=?', (channel_id,)).fetchone()
    return render_template('edit_channel.html', owner=owner)


# Library

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
        WHERE sv.user_id=?
        ORDER BY sv.saved_at DESC
    ''', (session['user_id'],)).fetchall()
    return render_template('library.html', saved=saved)


# Boot

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)