"""
Fernet-encrypted credential store.

Credentials are stored in /app/data/credentials as a Fernet-encrypted JSON file.
Master key is read from the CRED_MASTER_KEY environment variable.
Plaintext passwords are never written to disk.

In-memory cache: credentials are decrypted once and held in _cache.
Cache is invalidated whenever credentials are saved or deleted.
"""
import os
import json
import threading
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken


def _cred_file() -> Path:
    return Path(os.getenv("DATA_DIR", "/app/data")) / "credentials"


def _get_fernet() -> Fernet:
    key = os.getenv("CRED_MASTER_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "CRED_MASTER_KEY environment variable is not set. "
            "Run init.py to generate a key and add it to .env"
        )
    return Fernet(key.encode())


# ─── In-memory cache ──────────────────────────────────────────────────────────
_cache: dict | None = None   # None = not yet loaded
_cache_lock = threading.Lock()


def _invalidate() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _load() -> dict:
    global _cache
    with _cache_lock:
        if _cache is not None:
            return _cache

        # File read happens inside the lock — prevents two threads from
        # both seeing _cache=None and double-loading the credential file.
        path = _cred_file()
        if not path.exists() or path.stat().st_size == 0:
            _cache = {}
            return {}
        raw = path.read_bytes()
        if raw == b"{}":
            _cache = {}
            return {}
        try:
            decrypted = _get_fernet().decrypt(raw)
            data = json.loads(decrypted)
        except InvalidToken:
            raise RuntimeError(
                "Failed to decrypt credentials — wrong CRED_MASTER_KEY, or file is corrupt."
            )
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Credentials file is corrupt: {e}")

        _cache = data
        return data


def _save(data: dict) -> None:
    path = _cred_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not data:
        path.write_bytes(b"{}")
    else:
        encrypted = _get_fernet().encrypt(json.dumps(data).encode())
        path.write_bytes(encrypted)
    _invalidate()  # force reload on next access


def save_credential(ip: str, user: str, password: str) -> None:
    """Encrypt and store a password for ip|user."""
    data = _load()
    data[f"{ip}|{user}"] = password
    _save(data)


def get_credential(ip: str, user: str) -> str | None:
    """Return decrypted password for ip|user, or None if not stored."""
    data = _load()
    return data.get(f"{ip}|{user}")


def delete_credential(ip: str, user: str) -> bool:
    """Delete stored credential. Returns True if it existed."""
    data = _load()
    key = f"{ip}|{user}"
    if key not in data:
        return False
    del data[key]
    _save(data)
    return True


def credential_exists(ip: str, user: str) -> bool:
    """Return True if a credential is stored for ip|user."""
    data = _load()
    return f"{ip}|{user}" in data


def list_stored() -> list[dict]:
    """Return list of {ip, user} dicts — no passwords."""
    data = _load()
    result = []
    for k in data:
        ip, user = k.split("|", 1)
        result.append({"ip": ip, "user": user})
    return result
