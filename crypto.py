"""
Transparent Content Encryption for Confidant.
Uses Fernet (AES-128-CBC + HMAC) with PBKDF2 key derivation from a master passphrase.
The key lives in memory only — never written to disk.
Optionally stores the derived key in the OS keyring for biometric unlock.
"""

import os
import sys
import base64
import sqlite3
import keyring
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# When frozen by PyInstaller, use a 'data' subdirectory for user data
if getattr(sys, 'frozen', False):
    _data_dir = os.path.join(os.path.dirname(sys.executable), 'data')
else:
    _data_dir = os.path.dirname(os.path.abspath(__file__))

os.makedirs(_data_dir, exist_ok=True)
DB_FILE = os.path.join(_data_dir, "confidant.db")

KEYRING_SERVICE = "ConfidantVault"
KEYRING_USERNAME = "encryption_key"

# In-memory state — never persisted
_fernet = None
_VERIFY_PLAINTEXT = "CONFIDANT_OK"


def _get_security(key):
    """Read a value from the security table directly (bypasses database.py)."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM security WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def _set_security(key, value):
    """Write a value to the security table directly."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO security (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    ''', (key, value))
    conn.commit()
    conn.close()


def _derive_key(passphrase, salt):
    """Derive a Fernet-compatible key from a passphrase and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode('utf-8')))
    return key


def has_passphrase():
    """Check if a passphrase has been configured."""
    return _get_security("salt") is not None


def is_locked():
    """Check if the app is locked (passphrase set but not yet unlocked)."""
    return has_passphrase() and _fernet is None


def unlock(passphrase):
    """Unlock the app with the master passphrase. Returns True on success."""
    global _fernet

    salt_b64 = _get_security("salt")
    verify_token = _get_security("verify")

    if not salt_b64 or not verify_token:
        return False

    salt = base64.urlsafe_b64decode(salt_b64)
    key = _derive_key(passphrase, salt)

    try:
        f = Fernet(key)
        result = f.decrypt(verify_token.encode('utf-8'))
        if result.decode('utf-8') == _VERIFY_PLAINTEXT:
            _fernet = f
            # Store key in keyring for future biometric unlock
            _store_key_in_keyring(key)
            return True
    except (InvalidToken, Exception):
        pass

    return False


def _store_key_in_keyring(key):
    """Store the derived Fernet key in the OS credential manager."""
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key.decode('utf-8'))
    except Exception:
        pass  # Keyring not available — graceful degradation


def _get_key_from_keyring():
    """Retrieve the Fernet key from the OS credential manager."""
    try:
        stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        return stored.encode('utf-8') if stored else None
    except Exception:
        return None


def try_auto_unlock():
    """Attempt to unlock using the OS keyring (biometric/Windows Hello).
    Call this on startup — if it succeeds, the app opens without a passphrase prompt."""
    global _fernet

    if not has_passphrase() or _fernet is not None:
        return False  # Nothing to unlock, or already unlocked

    key = _get_key_from_keyring()
    if not key:
        return False

    verify_token = _get_security("verify")
    if not verify_token:
        return False

    try:
        f = Fernet(key)
        result = f.decrypt(verify_token.encode('utf-8'))
        if result.decode('utf-8') == _VERIFY_PLAINTEXT:
            _fernet = f
            return True
    except (InvalidToken, Exception):
        # Stored key is invalid — remove it
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            pass

    return False


def get_flask_secret():
    """Derive a stable Flask session secret from the encryption key.
    Falls back to a random key if the app isn't unlocked yet."""
    if _fernet is None:
        return os.urandom(32)
    # HMAC-derive a separate key from the Fernet key for Flask sessions
    import hmac, hashlib
    fernet_key = _fernet._signing_key  # Raw key bytes
    return hmac.new(fernet_key, b"flask-session-key", hashlib.sha256).digest()


def set_passphrase(passphrase):
    """Set the master passphrase for the first time. Encrypts all existing data."""
    global _fernet

    if has_passphrase():
        return False  # Already set

    # Generate random salt
    salt = os.urandom(16)
    salt_b64 = base64.urlsafe_b64encode(salt).decode('utf-8')

    # Derive key
    key = _derive_key(passphrase, salt)
    f = Fernet(key)

    # Store salt and verification token
    verify_token = f.encrypt(_VERIFY_PLAINTEXT.encode('utf-8')).decode('utf-8')
    _set_security("salt", salt_b64)
    _set_security("verify", verify_token)

    # Set the fernet instance so encrypt() works
    _fernet = f

    # Store key in OS keyring for biometric unlock
    _store_key_in_keyring(key)

    # Encrypt all existing plaintext data in place
    _encrypt_existing_data()

    return True


def change_passphrase(old_passphrase, new_passphrase):
    """Change the master passphrase. Re-encrypts all data with a new key."""
    global _fernet

    # Verify old passphrase
    salt_b64 = _get_security("salt")
    if not salt_b64:
        return False

    old_salt = base64.urlsafe_b64decode(salt_b64)
    old_key = _derive_key(old_passphrase, old_salt)

    verify_token = _get_security("verify")
    if not verify_token:
        return False

    try:
        old_f = Fernet(old_key)
        result = old_f.decrypt(verify_token.encode('utf-8'))
        if result.decode('utf-8') != _VERIFY_PLAINTEXT:
            return False
    except (InvalidToken, Exception):
        return False

    # Generate new salt and key
    new_salt = os.urandom(16)
    new_salt_b64 = base64.urlsafe_b64encode(new_salt).decode('utf-8')
    new_key = _derive_key(new_passphrase, new_salt)
    new_f = Fernet(new_key)

    # Re-encrypt all data: decrypt with old key, encrypt with new key
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Re-encrypt files.content
    cursor.execute('SELECT name, content FROM files')
    for name, content in cursor.fetchall():
        if content and content.startswith('gAAAAA'):
            decrypted = old_f.decrypt(content.encode('utf-8')).decode('utf-8')
            cursor.execute('UPDATE files SET content = ? WHERE name = ?',
                           (new_f.encrypt(decrypted.encode('utf-8')).decode('utf-8'), name))

    # Re-encrypt config.value
    cursor.execute('SELECT key, value FROM config')
    for key, value in cursor.fetchall():
        if value and value.startswith('gAAAAA'):
            decrypted = old_f.decrypt(value.encode('utf-8')).decode('utf-8')
            cursor.execute('UPDATE config SET value = ? WHERE key = ?',
                           (new_f.encrypt(decrypted.encode('utf-8')).decode('utf-8'), key))

    # Re-encrypt messages.content
    cursor.execute('SELECT id, content FROM messages')
    for msg_id, content in cursor.fetchall():
        if content and content.startswith('gAAAAA'):
            decrypted = old_f.decrypt(content.encode('utf-8')).decode('utf-8')
            cursor.execute('UPDATE messages SET content = ? WHERE id = ?',
                           (new_f.encrypt(decrypted.encode('utf-8')).decode('utf-8'), msg_id))

    # Re-encrypt vault_cards.content and vault_cards.title
    cursor.execute('SELECT id, title, content FROM vault_cards')
    for card_id, title, content in cursor.fetchall():
        new_title = title
        new_content = content
        if title and title.startswith('gAAAAA'):
            new_title = new_f.encrypt(old_f.decrypt(title.encode('utf-8'))).decode('utf-8')
        if content and content.startswith('gAAAAA'):
            new_content = new_f.encrypt(old_f.decrypt(content.encode('utf-8'))).decode('utf-8')
        if new_title != title or new_content != content:
            cursor.execute('UPDATE vault_cards SET title = ?, content = ? WHERE id = ?',
                           (new_title, new_content, card_id))

    # Re-encrypt conversation_summaries.summary
    cursor.execute('SELECT id, summary FROM conversation_summaries')
    for sum_id, summary in cursor.fetchall():
        if summary and summary.startswith('gAAAAA'):
            decrypted = old_f.decrypt(summary.encode('utf-8')).decode('utf-8')
            cursor.execute('UPDATE conversation_summaries SET summary = ? WHERE id = ?',
                           (new_f.encrypt(decrypted.encode('utf-8')).decode('utf-8'), sum_id))

    # Re-encrypt dreams.content
    cursor.execute('SELECT id, content FROM dreams')
    for dream_id, content in cursor.fetchall():
        if content and content.startswith('gAAAAA'):
            decrypted = old_f.decrypt(content.encode('utf-8')).decode('utf-8')
            cursor.execute('UPDATE dreams SET content = ? WHERE id = ?',
                           (new_f.encrypt(decrypted.encode('utf-8')).decode('utf-8'), dream_id))

    conn.commit()
    conn.close()

    # Update security table
    new_verify = new_f.encrypt(_VERIFY_PLAINTEXT.encode('utf-8')).decode('utf-8')
    _set_security("salt", new_salt_b64)
    _set_security("verify", new_verify)

    # Update in-memory state and keyring
    _fernet = new_f
    _store_key_in_keyring(new_key)

    return True


def encrypt(plaintext):
    """Encrypt a string. If no passphrase is set, returns plaintext unchanged."""
    if _fernet is None or not plaintext:
        return plaintext
    return _fernet.encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt(ciphertext):
    """Decrypt a string. If it doesn't look like a Fernet token, returns as-is."""
    if not ciphertext:
        return ciphertext
    # Fernet tokens always start with 'gAAAAA'
    if not ciphertext.startswith('gAAAAA'):
        return ciphertext
    if _fernet is None:
        return ciphertext  # Locked — can't decrypt
    try:
        return _fernet.decrypt(ciphertext.encode('utf-8')).decode('utf-8')
    except (InvalidToken, Exception):
        return ciphertext  # Return as-is if decryption fails


def _encrypt_existing_data():
    """Encrypt all existing plaintext content in the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Encrypt files.content
    cursor.execute('SELECT name, content FROM files')
    for name, content in cursor.fetchall():
        if content and not content.startswith('gAAAAA'):
            cursor.execute('UPDATE files SET content = ? WHERE name = ?',
                           (encrypt(content), name))

    # Encrypt config.value
    cursor.execute('SELECT key, value FROM config')
    for key, value in cursor.fetchall():
        if value and not value.startswith('gAAAAA'):
            cursor.execute('UPDATE config SET value = ? WHERE key = ?',
                           (encrypt(value), key))

    # Encrypt messages.content
    cursor.execute('SELECT id, content FROM messages')
    for msg_id, content in cursor.fetchall():
        if content and not content.startswith('gAAAAA'):
            cursor.execute('UPDATE messages SET content = ? WHERE id = ?',
                           (encrypt(content), msg_id))

    # Encrypt vault_cards.content and vault_cards.title
    cursor.execute('SELECT id, title, content FROM vault_cards')
    for card_id, title, content in cursor.fetchall():
        updates = {}
        if content and not content.startswith('gAAAAA'):
            updates['content'] = encrypt(content)
        if title and not title.startswith('gAAAAA'):
            updates['title'] = encrypt(title)
        if updates:
            sets = ', '.join(f'{k} = ?' for k in updates)
            cursor.execute(f'UPDATE vault_cards SET {sets} WHERE id = ?',
                           (*updates.values(), card_id))

    # Encrypt conversation_summaries.summary
    cursor.execute('SELECT id, summary FROM conversation_summaries')
    for sum_id, summary in cursor.fetchall():
        if summary and not summary.startswith('gAAAAA'):
            cursor.execute('UPDATE conversation_summaries SET summary = ? WHERE id = ?',
                           (encrypt(summary), sum_id))

    # Encrypt dreams.content
    cursor.execute('SELECT id, content FROM dreams')
    for dream_id, content in cursor.fetchall():
        if content and not content.startswith('gAAAAA'):
            cursor.execute('UPDATE dreams SET content = ? WHERE id = ?',
                           (encrypt(content), dream_id))

    conn.commit()
    conn.close()


# --- WebAuthn Credential Storage ---

def store_webauthn_credential(credential_id, public_key, sign_count):
    """Store a WebAuthn credential (from biometric registration)."""
    import json
    data = json.dumps({
        "credential_id": base64.urlsafe_b64encode(credential_id).decode('utf-8'),
        "public_key": base64.urlsafe_b64encode(public_key).decode('utf-8'),
        "sign_count": sign_count,
    })
    _set_security("webauthn_credential", data)


def get_webauthn_credential():
    """Retrieve stored WebAuthn credential. Returns dict or None."""
    import json
    data = _get_security("webauthn_credential")
    if not data:
        return None
    cred = json.loads(data)
    return {
        "credential_id": base64.urlsafe_b64decode(cred["credential_id"]),
        "public_key": base64.urlsafe_b64decode(cred["public_key"]),
        "sign_count": cred["sign_count"],
    }


def has_webauthn():
    """Check if a WebAuthn credential is registered."""
    return _get_security("webauthn_credential") is not None


def update_sign_count(new_count):
    """Update the sign count after successful authentication."""
    import json
    data = _get_security("webauthn_credential")
    if data:
        cred = json.loads(data)
        cred["sign_count"] = new_count
        _set_security("webauthn_credential", json.dumps(cred))


def reset_auth():
    """
    Clear all authentication state as part of a factory reset.
    - Clears the in-memory Fernet key so the app is fully locked.
    - Deletes the biometric key from the OS keyring.
    The security table is wiped separately by database.factory_reset().
    """
    global _fernet
    _fernet = None
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:
        pass  # Keyring entry may not exist — that's fine
