import hashlib
import secrets


def generate_api_key() -> str:
    return f"sk_{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_prefix(raw_key: str) -> str:
    return raw_key[:11]  # "sk_" + 8 chars, enough to recognize a key in a list
