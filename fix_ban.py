import sqlite3
import os

# This matches the DB_PATH configured in your app.py
# On Windows, this usually resolves to C:\app\data\youtube.db
# If your database is elsewhere, you might need to adjust this path.
DB_PATH = '/app/data/youtube.db'

print(f"Attempting to connect to database at: {DB_PATH}")

try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check for banned IPs
    cursor.execute("SELECT COUNT(*) FROM banned_ips")
    count = cursor.fetchone()[0]
    print(f"Found {count} banned IP(s).")
    
    cursor.execute("DELETE FROM banned_ips")
    conn.commit()
    print("Success! All IP bans have been removed.")
    conn.close()
except Exception as e:
    print(f"Error: {e}")