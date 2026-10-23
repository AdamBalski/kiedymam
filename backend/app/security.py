import hashlib
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(40)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
