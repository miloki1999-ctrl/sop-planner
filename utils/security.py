"""
Password hashing helpers. Uses `bcrypt` directly rather than passlib's
CryptContext wrapper, which has known version-detection issues with recent
bcrypt releases (raises AttributeError / 72-byte errors on some installs).
"""
import bcrypt


def hash_password(plain_password: str) -> str:
    pw_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except Exception:
        return False
