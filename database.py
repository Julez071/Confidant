import sqlite3
import json
import os
import sys
import crypto

# When frozen by PyInstaller, use a 'data' subdirectory for user data
if getattr(sys, 'frozen', False):
    DIRECTORY = os.path.join(os.path.dirname(sys.executable), 'data')
    BUNDLE_DIR = sys._MEIPASS
else:
    DIRECTORY = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = DIRECTORY

os.makedirs(DIRECTORY, exist_ok=True)
DB_FILE = os.path.join(DIRECTORY, "confidant.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vault_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary TEXT NOT NULL,
            first_msg_id INTEGER,
            last_msg_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            name TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS security (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dreams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # --- Migrations: add columns that may not exist in older databases ---
    try:
        cursor.execute('ALTER TABLE messages ADD COLUMN is_hidden INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()


# --- File storage ---

def get_file(name):
    """Get file content by name. Returns empty string if not found."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT content FROM files WHERE name = ?', (name,))
    row = cursor.fetchone()
    conn.close()
    return crypto.decrypt(row[0]) if row else ""

def set_file(name, content):
    """Upsert file content by name."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO files (name, content, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET content = excluded.content, updated_at = CURRENT_TIMESTAMP
    ''', (name, crypto.encrypt(content)))
    conn.commit()
    conn.close()

def get_all_files():
    """Get all files as a dict {name: content}."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT name, content FROM files')
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: crypto.decrypt(row[1]) for row in rows}


# --- Config storage ---

def get_config_value(key, default=""):
    """Get a single config value by key."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM config WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    return crypto.decrypt(row[0]) if row else default

def set_config_value(key, value):
    """Upsert a single config value."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO config (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    ''', (key, crypto.encrypt(str(value))))
    conn.commit()
    conn.close()

def get_all_config():
    """Get all config as a dict {key: value}."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT key, value FROM config')
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: crypto.decrypt(row[1]) for row in rows}



def add_message(role, content, is_hidden=False):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Store content as a JSON string so we can easily handle arrays (for multimodal)
    content_str = json.dumps(content) if not isinstance(content, str) else content
    
    cursor.execute('''
        INSERT INTO messages (role, content, is_hidden) VALUES (?, ?, ?)
    ''', (role, crypto.encrypt(content_str), 1 if is_hidden else 0))
    
    conn.commit()
    conn.close()

def get_history(limit=100):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, role, content, is_hidden FROM (
            SELECT id, role, content, is_hidden FROM messages ORDER BY id DESC LIMIT ?
        ) ORDER BY id ASC
    ''', (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    history = []
    for row in rows:
        role = row[1]
        content_str = crypto.decrypt(row[2])
        try:
            # Try to parse it back into a list/dict if it was JSON
            # Only do this if it looks like JSON structure
            if (content_str.startswith('[') and content_str.endswith(']')) or \
               (content_str.startswith('{') and content_str.endswith('}')):
                content = json.loads(content_str)
            else:
                content = content_str
        except json.JSONDecodeError:
            content = content_str
            
        history.append({
            "id": row[0],
            "role": role, 
            "content": content,
            "is_hidden": bool(row[3]) if row[3] is not None else False
        })
        
    return history

def get_message_by_id(msg_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, content FROM messages WHERE id = ?
    ''', (msg_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        try:
            content = json.loads(crypto.decrypt(row[1]))
        except json.JSONDecodeError:
            content = crypto.decrypt(row[1])
        return {"role": row[0], "content": content}
    return None

# Common stopwords to strip from search queries for better keyword matching
_STOPWORDS = {
    'the', 'a', 'an', 'is', 'was', 'are', 'were', 'that', 'this',
    'about', 'what', 'when', 'where', 'how', 'we', 'you', 'i', 'my',
    'did', 'do', 'does', 'thing', 'something', 'talked', 'discussed', 'said',
    'remember', 'time', 'before', 'of', 'in', 'on', 'to', 'for', 'with',
    'it', 'be', 'has', 'had', 'have', 'not', 'but', 'and', 'or', 'if',
    'from', 'at', 'by', 'our', 'your', 'his', 'her', 'its', 'they', 'them',
    'can', 'will', 'just', 'so', 'than', 'too', 'very', 'some', 'any',
    'me', 'he', 'she', 'us', 'up', 'out', 'no', 'yes',
}

def _extract_keywords(query):
    """Extract meaningful keywords from a natural-language query."""
    words = [w for w in query.lower().split() if w not in _STOPWORDS and len(w) > 2]
    return words if words else [query.lower()]  # Fallback to original query

def search_history(query, limit=20):
    """Search message history. Fetches all, decrypts, then filters by keywords."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    keywords = _extract_keywords(query)
    
    # Fetch all messages (we must decrypt before filtering)
    cursor.execute('SELECT role, content, timestamp FROM messages ORDER BY id DESC')
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for role, content_str, timestamp in rows:
        decrypted = crypto.decrypt(content_str)
        # Check if any keyword matches
        decrypted_lower = decrypted.lower()
        if any(kw in decrypted_lower for kw in keywords):
            results.append({
                "role": role,
                "content": decrypted,
                "timestamp": timestamp
            })
            if len(results) >= limit:
                break
        
    return results

def get_messages_since(hours=24):
    """Get all messages from the last N hours."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, content FROM messages 
        WHERE timestamp >= datetime('now', ?) 
        ORDER BY id ASC
    ''', (f'-{hours} hours',))
    rows = cursor.fetchall()
    conn.close()
    
    messages = []
    for row in rows:
        content_str = crypto.decrypt(row[1])
        try:
            content = json.loads(content_str)
        except (json.JSONDecodeError, ValueError):
            content = content_str
        messages.append({"role": row[0], "content": content})
    return messages

# Initialize the database when the module is imported
init_db()


def _seed_initial_data():
    """Seed template files on first run. Only runs if the files table is empty."""
    existing = get_all_files()
    if existing:
        return  # Not a first run
    
    # Seed system_prompt.md and character.md from bundled templates
    templates = {
        "system_prompt.md": os.path.join(BUNDLE_DIR, "system_prompt.md.bak"),
        "character.md": os.path.join(BUNDLE_DIR, "character.md.bak"),
    }
    for name, bak_path in templates.items():
        if os.path.exists(bak_path):
            with open(bak_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Write directly (no encryption — passphrase not set yet on first run)
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO files (name, content, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET content = excluded.content, updated_at = CURRENT_TIMESTAMP
            ''', (name, content))
            conn.commit()
            conn.close()
    
    # Create empty personal files
    for name in ["partner.md", "significant_others.md", "context.md", "instructions.md"]:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO files (name, content, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (name, ""))
        conn.commit()
        conn.close()

_seed_initial_data()

def add_vault_card(card_type, content, title=""):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO vault_cards (type, title, content) VALUES (?, ?, ?)
    ''', (card_type, crypto.encrypt(title), crypto.encrypt(content)))
    conn.commit()
    conn.close()

def get_vault_cards():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, type, title, content, timestamp FROM vault_cards ORDER BY id DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    cards = []
    for row in rows:
        cards.append({
            "id": row[0],
            "type": row[1],
            "title": crypto.decrypt(row[2]),
            "content": crypto.decrypt(row[3]),
            "timestamp": row[4]
        })
    return cards

def delete_vault_card(card_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM vault_cards WHERE id = ?', (card_id,))
    conn.commit()
    conn.close()

def get_latest_dream_time():
    """Return the timestamp string of the latest dream, or None."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp FROM dreams ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_latest_full_dream():
    """Return the decrypted content of the latest full dream, or None."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM dreams ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return crypto.decrypt(row[0]) if row else None

def add_dream(content):
    """Add a full dream to the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO dreams (content) VALUES (?)
    ''', (crypto.encrypt(content),))
    conn.commit()
    conn.close()

def search_vault(query, limit=5):
    """Search vault cards. Fetches all, decrypts, then filters by keywords."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    keywords = _extract_keywords(query)
    
    # Fetch all non-image cards (we must decrypt before filtering)
    cursor.execute('''
        SELECT type, title, content, timestamp FROM vault_cards 
        WHERE type != 'image'
        ORDER BY id DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for card_type, title, content, timestamp in rows:
        decrypted_title = crypto.decrypt(title) if title else ""
        decrypted_content = crypto.decrypt(content)
        search_text = (decrypted_title + " " + decrypted_content).lower()
        if any(kw in search_text for kw in keywords):
            results.append({
                "type": card_type,
                "title": decrypted_title,
                "content": decrypted_content,
                "timestamp": timestamp
            })
            if len(results) >= limit:
                break
    return results

# --- Conversation summaries ---

def add_conversation_summary(summary, first_msg_id, last_msg_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO conversation_summaries (summary, first_msg_id, last_msg_id) 
        VALUES (?, ?, ?)
    ''', (crypto.encrypt(summary), first_msg_id, last_msg_id))
    conn.commit()
    conn.close()

def get_recent_summaries(limit=3):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT summary, first_msg_id, last_msg_id, timestamp 
        FROM conversation_summaries ORDER BY id DESC LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{"summary": crypto.decrypt(r[0]), "first_msg_id": r[1], "last_msg_id": r[2], "timestamp": r[3]} for r in rows]

def get_last_summarized_msg_id():
    """Return the highest last_msg_id that has been summarized, or 0."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT MAX(last_msg_id) FROM conversation_summaries')
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] else 0

def get_messages_in_range(start_id, end_id):
    """Get messages with IDs between start_id (exclusive) and end_id (inclusive)."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, role, content FROM messages 
        WHERE id > ? AND id <= ? 
        ORDER BY id ASC
    ''', (start_id, end_id))
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        content_str = crypto.decrypt(row[2])
        try:
            if (content_str.startswith('[') or content_str.startswith('{')):
                content = json.loads(content_str)
            else:
                content = content_str
        except json.JSONDecodeError:
            content = content_str
        results.append({"id": row[0], "role": row[1], "content": content})
    return results

def get_oldest_context_msg_id(limit=20):
    """Get the ID of the oldest message in the current context window."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id FROM (
            SELECT id FROM messages ORDER BY id DESC LIMIT ?
        ) ORDER BY id ASC LIMIT 1
    ''', (limit,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def factory_reset():
    """
    Wipe all personal data and restore Confidant to a fresh, first-run state.
    Restores character.md from character.md.bak.
    Clears all messages, vault cards, summaries, dreams, personal files,
    config, and security (passphrase / biometrics).
    system_prompt.md is left untouched — it is never modified by the memory manager.
    """
    # Read the original character template from the bundled .bak file
    bak_path = os.path.join(BUNDLE_DIR, "character.md.bak")
    character_template = ""
    if os.path.exists(bak_path):
        with open(bak_path, 'r', encoding='utf-8') as f:
            character_template = f.read()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Wipe all personal data tables
    cursor.execute('DELETE FROM messages')
    cursor.execute('DELETE FROM vault_cards')
    cursor.execute('DELETE FROM conversation_summaries')
    cursor.execute('DELETE FROM dreams')
    cursor.execute('DELETE FROM config')
    cursor.execute('DELETE FROM security')

    # Wipe personal files only — system_prompt.md is left untouched
    personal_files = ['character.md', 'partner.md', 'significant_others.md',
                      'context.md', 'instructions.md']
    for f in personal_files:
        cursor.execute('DELETE FROM files WHERE name = ?', (f,))

    conn.commit()
    conn.close()

    # Re-insert clean files (unencrypted write — crypto key will be gone after this)
    # We must write directly without encryption since the key is being cleared
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Store files as plain text (no passphrase set yet, so no encryption needed)
    # The next set_passphrase() call will NOT re-encrypt these because encryption
    # only applies when a key is active. Files will be encrypted when new passphrase is set.
    empty_files = ['partner.md', 'significant_others.md', 'context.md', 'instructions.md']
    for fname in empty_files:
        cursor.execute('''
            INSERT INTO files (name, content, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(name) DO UPDATE SET content = excluded.content, updated_at = CURRENT_TIMESTAMP
        ''', (fname, ""))

    # Restore character.md from template (plain text — no key active)
    cursor.execute('''
        INSERT INTO files (name, content, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET content = excluded.content, updated_at = CURRENT_TIMESTAMP
    ''', ('character.md', character_template))

    conn.commit()
    conn.close()
