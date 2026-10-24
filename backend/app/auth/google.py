import datetime as dt
import json
import logging
import uuid

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from app.config import Settings
from app.crypto.envelope import EnvelopeCrypto
from app.db.database import Database
from app.security import new_token, token_hash

LOG = logging.getLogger(__name__)
SCOPES = ("openid", "email", "profile", "https://www.googleapis.com/auth/calendar.app.created")
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
SESSION_DAYS = 30
OAUTH_STATE_MINUTES = 10


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def new_oauth_state(database: Database, return_path: str = "/") -> tuple[str, str]:
    state = new_token()
    nonce = new_token()
    with database.transaction(immediate=True) as db:
        db.execute("DELETE FROM oauth_states WHERE expires_at < ?", (iso(now_utc()),))
        db.execute(
            "INSERT INTO oauth_states(state_hash, nonce, return_path, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash(state), nonce, return_path if return_path.startswith("/") else "/", iso(now_utc() + dt.timedelta(minutes=OAUTH_STATE_MINUTES))),
        )
    return state, nonce


async def consume_oauth_state(database: Database, state: str) -> dict | None:
    with database.transaction(immediate=True) as db:
        row = db.execute(
            "SELECT nonce, return_path, expires_at FROM oauth_states WHERE state_hash=?",
            (token_hash(state),),
        ).fetchone()
        db.execute("DELETE FROM oauth_states WHERE state_hash=?", (token_hash(state),))
    if not row or row["expires_at"] < iso(now_utc()):
        return None
    return dict(row)


async def exchange_code(settings: Settings, code: str) -> dict:
    client = settings.google_client["web"]
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5)) as http:
        response = await http.post(
            client["token_uri"],
            data={
                "code": code,
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        LOG.warning("Google OAuth code exchange failed with status %s", response.status_code)
        raise ValueError("Google authorization failed; please try signing in again")
    return response.json()


async def verify_id_token(token: str, settings: Settings, expected_nonce: str) -> dict:
    client_id = settings.google_client["web"]["client_id"]
    claims = await __import__("asyncio").to_thread(
        id_token.verify_oauth2_token, token, GoogleRequest(), client_id
    )
    if claims.get("nonce") != expected_nonce:
        raise ValueError("Google authorization nonce did not match")
    if not claims.get("sub") or not claims.get("email") or not claims.get("email_verified"):
        raise ValueError("Google did not return a verified email address")
    return claims


def upsert_user(database: Database, claims: dict) -> str:
    user_id = str(uuid.uuid4())
    now = iso(now_utc())
    with database.transaction(immediate=True) as db:
        db.execute(
            """INSERT INTO users(id, google_sub, email, display_name, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(google_sub) DO UPDATE SET email=excluded.email,
                 display_name=excluded.display_name""",
            (user_id, claims["sub"], claims["email"], claims.get("name") or claims["email"], now),
        )
        row = db.execute("SELECT id FROM users WHERE google_sub=?", (claims["sub"],)).fetchone()
        return row["id"]


def store_oauth_credentials(
    database: Database, crypto: EnvelopeCrypto, user_id: str, payload: dict
) -> None:
    existing = crypto.decrypt_for_user(user_id)
    if not payload.get("refresh_token") and existing and existing.get("refresh_token"):
        payload["refresh_token"] = existing["refresh_token"]
    if not payload.get("refresh_token"):
        raise ValueError("Google did not provide offline calendar access. Revoke this app in Google settings and sign in again.")
    crypto.encrypt_for_user(user_id, payload)


def create_session(database: Database, user_id: str) -> str:
    raw_token = new_token()
    now = now_utc()
    with database.transaction(immediate=True) as db:
        db.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token_hash(raw_token), user_id, iso(now + dt.timedelta(days=SESSION_DAYS)), iso(now)),
        )
    return raw_token


def get_session(database: Database, raw_token: str | None) -> dict | None:
    if not raw_token:
        return None
    with database.read() as db:
        row = db.execute(
            """SELECT u.id, u.email, u.display_name FROM sessions s
               JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.expires_at>?""",
            (token_hash(raw_token), iso(now_utc())),
        ).fetchone()
    return dict(row) if row else None


def delete_session(database: Database, raw_token: str | None) -> None:
    if raw_token:
        with database.transaction(immediate=True) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(raw_token),))


def load_credentials(crypto: EnvelopeCrypto, user_id: str) -> dict:
    payload = crypto.decrypt_for_user(user_id)
    if not payload:
        raise RuntimeError("Google Calendar connection is missing")
    return payload


def replace_credentials(crypto: EnvelopeCrypto, user_id: str, payload: dict) -> None:
    crypto.encrypt_for_user(user_id, payload)
