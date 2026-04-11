"""
Fernet-encrypted credential store.

Credentials are stored in /app/data/credentials as a Fernet-encrypted JSON file.
Master key is read from the CRED_MASTER_KEY environment variable.
Plaintext passwords are never written to disk.
"""
import os
import json
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


def _load() -> dict:
    path = _cred_file()
    if not path.exists() or path.stat().st_size == 0:
        return {}
    raw = path.read_bytes()
    if raw == b"{}":
        return {}
    try:
        decrypted = _get_fernet().decrypt(raw)
        return json.loads(decrypted)
    except InvalidToken:
        raise RuntimeError(
            "Failed to decrypt credentials — wrong CRED_MASTER_KEY, or file is corrupt."
        )
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Credentials file is corrupt: {e}")


def _save(data: dict) -> None:
    path = _cred_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not data:
        path.write_bytes(b"{}")
        return
    encrypted = _get_fernet().encrypt(json.dumps(data).encode())
    path.write_bytes(encrypted)


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
