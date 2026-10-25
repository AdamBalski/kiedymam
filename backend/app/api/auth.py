import hmac
import logging
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.auth import google
from app.config import Settings
from app.crypto.envelope import EnvelopeCrypto
from app.db.database import Database

LOG = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["authentication"])
SESSION_COOKIE = "umb_session"
CSRF_COOKIE = "umb_csrf"


def get_services(request: Request) -> tuple[Settings, Database, EnvelopeCrypto]:
    return request.app.state.settings, request.app.state.database, request.app.state.crypto


def current_user(request: Request) -> dict:
    _, database, _ = get_services(request)
    user = google.get_session(database, request.cookies.get(SESSION_COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    metrics = getattr(request.app.state, "metrics", None)
    if metrics:
        metrics.add_counter(
            "http.server.authenticated_request.count",
            attributes={"user.email": user["email"].strip().casefold()},
        )
    return user


def require_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("X-CSRF-Token", "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


@router.get("/google")
async def start_google_login(request: Request, return_path: str = "/"):
    settings, database, _ = get_services(request)
    # Keep sign-in returns on known same-origin app routes.
    if return_path not in ("/", "/?tab=contact"):
        return_path = "/"
    state, nonce = google.new_oauth_state(database, return_path)
    client = settings.google_client["web"]
    params = {
        "client_id": client["client_id"],
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(google.SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
        "nonce": nonce,
    }
    return RedirectResponse(f"{client['auth_uri']}?{urlencode(params)}", status_code=302)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    scope: str | None = None,
):
    settings, database, crypto = get_services(request)
    if error or not code or not state:
        raise HTTPException(status_code=400, detail="Google sign-in was not completed")
    oauth_state = await google.consume_oauth_state(database, state)
    if not oauth_state:
        raise HTTPException(status_code=400, detail="Sign-in state expired; try again")
    try:
        payload = await google.exchange_code(settings, code)
        granted_scopes = set((payload.get("scope") or scope or "").split())
        if google.CALENDAR_SCOPE not in granted_scopes:
            return RedirectResponse(
                f"{settings.public_url(oauth_state['return_path'])}?auth_error=calendar_permission_required",
                status_code=303,
            )
        claims = await google.verify_id_token(payload.get("id_token", ""), settings, oauth_state["nonce"])
        user_id = google.upsert_user(database, claims)
        client = settings.google_client["web"]
        payload.update({
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "token_uri": client["token_uri"],
            "scopes": sorted(granted_scopes),
            "expires_at": int(time.time()) + int(payload.get("expires_in", 3600)),
        })
        google.store_oauth_credentials(database, crypto, user_id, payload)
        session = google.create_session(database, user_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        LOG.exception("Google sign-in failed")
        raise HTTPException(status_code=502, detail="Google sign-in failed; please try again")

    csrf = google.new_token()
    response = RedirectResponse(settings.public_url(oauth_state["return_path"]), status_code=303)
    secure = settings.app_origin.startswith("https://")
    cookie_path = settings.app_base_path or "/"
    response.set_cookie(SESSION_COOKIE, session, httponly=True, secure=secure, samesite="lax", path=cookie_path, max_age=google.SESSION_DAYS * 86400)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=secure, samesite="strict", path=cookie_path, max_age=google.SESSION_DAYS * 86400)
    return response


@router.get("/me")
async def me(request: Request, user: dict = Depends(current_user)) -> dict:
    admin_email = request.app.state.settings.admin_email
    return {
        "id": user["id"], "email": user["email"], "display_name": user["display_name"],
        "is_admin": bool(admin_email and user["email"].strip().casefold() == admin_email),
    }


@router.post("/logout")
async def logout(request: Request, response: Response, _: None = Depends(require_csrf)) -> dict[str, bool]:
    settings, database, _ = get_services(request)
    google.delete_session(database, request.cookies.get(SESSION_COOKIE))
    cookie_path = settings.app_base_path or "/"
    response.delete_cookie(SESSION_COOKIE, path=cookie_path)
    response.delete_cookie(CSRF_COOKIE, path=cookie_path)
    return {"logged_out": True}
