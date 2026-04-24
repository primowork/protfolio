"""OAuth authentication — Google + Apple Sign In with SQLite/Postgres user store."""
import os, uuid, json, logging, secrets
from datetime import datetime
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse

from sqlalchemy import Column, String, Text, DateTime, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

# ── Config ────────────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI  = os.getenv("GOOGLE_REDIRECT_URI",
                                  "http://localhost:8000/auth/google/callback")

APPLE_CLIENT_ID = os.getenv("APPLE_CLIENT_ID", "")

# ── Database ──────────────────────────────────────────────────────────────────
_base_dir = os.path.dirname(os.path.abspath(__file__))
_data_dir = os.path.join(_base_dir, "data")
os.makedirs(_data_dir, exist_ok=True)

_default_db = "sqlite+aiosqlite:///" + os.path.join(_data_dir, "portfolio.db")
DATABASE_URL = os.getenv("DATABASE_URL", _default_db)

_connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_async_engine(DATABASE_URL, echo=False, connect_args=_connect_args)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id             = Column(String, primary_key=True)
    email          = Column(String, unique=True, nullable=False)
    name           = Column(String)
    picture        = Column(String)
    provider       = Column(String, nullable=False)    # "google" | "apple"
    provider_id    = Column(String, unique=True, nullable=False)
    portfolio_json = Column(Text)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


async def init_db():
    """Create tables on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _get_user(uid):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == uid))
        return result.scalar_one_or_none()


async def _upsert_user(provider, provider_id, email, name=None, picture=None):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.provider_id == provider_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                id=str(uuid.uuid4()),
                email=email, name=name, picture=picture,
                provider=provider, provider_id=provider_id,
            )
            session.add(user)
        else:
            if name:    user.name    = name
            if picture: user.picture = picture
            user.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(user)
        return user


# ── Router ────────────────────────────────────────────────────────────────────
router = APIRouter()


@router.get("/api/auth-config")
async def auth_config():
    """Returns which OAuth providers are configured. Used by frontend to decide whether to show login."""
    return {"google": bool(GOOGLE_CLIENT_ID), "apple": bool(APPLE_CLIENT_ID)}


@router.get("/api/debug/oauth")
async def debug_oauth(request: Request):
    """Read-only diagnostic — safe to expose.

    Shows whether OAuth config env vars are set on the server and whether the
    browser's session cookie is being parsed. NO secrets are returned.

    Also *writes* a counter to the session so on the second hit we can
    verify that the session cookie round-trips correctly through the browser.
    """
    session_secret   = os.getenv("SESSION_SECRET", "")
    is_default_secret = (session_secret == "" or
                         session_secret == "dev-secret-CHANGE-IN-PRODUCTION")

    # increment hit counter — proves cookie round-trip works
    hits = request.session.get("debug_hits", 0) + 1
    request.session["debug_hits"] = hits

    return {
        "google_client_id_set":     bool(GOOGLE_CLIENT_ID),
        "google_client_id_prefix":  GOOGLE_CLIENT_ID[:12] + "..." if GOOGLE_CLIENT_ID else None,
        "google_client_secret_set": bool(GOOGLE_CLIENT_SECRET),
        "google_redirect_uri":      GOOGLE_REDIRECT_URI,
        "session_secret_set":       not is_default_secret,
        "session_https_only":       os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
        "session_keys":             list(request.session.keys()),
        "has_oauth_state":          "oauth_state" in request.session,
        "has_user_id":              "user_id" in request.session,
        "debug_hits":               hits,
        "request_scheme":           request.url.scheme,
        "request_host":             request.headers.get("host"),
        "x_forwarded_proto":        request.headers.get("x-forwarded-proto"),
        "cookies_received":         list(request.cookies.keys()),
    }


@router.get("/auth/google")
async def google_login(request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(400, "Google OAuth not configured — set GOOGLE_CLIENT_ID env var")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    params = urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = None,
                          state: str = None, error: str = None):
    if error:
        return RedirectResponse("/?auth_error=cancelled")
    if not code:
        raise HTTPException(400, "Missing authorization code")
    stored = request.session.get("oauth_state")
    if state != stored:
        # Log *why* the check failed so we can distinguish cookie loss from attack
        sess_keys = list(request.session.keys())
        logging.warning(
            "OAuth state mismatch: got=%r stored=%r session_keys=%s cookie_present=%s",
            state, stored, sess_keys, "cookie" in {k.lower() for k in request.headers.keys()}
        )
        if stored is None:
            raise HTTPException(
                400,
                "Session cookie was lost between /auth/google and callback. "
                "Check SESSION_SECRET + SESSION_HTTPS_ONLY on Railway, then clear browser cookies."
            )
        raise HTTPException(400, "OAuth state mismatch — possible CSRF")
    request.session.pop("oauth_state", None)

    async with httpx.AsyncClient() as client:
        tok = await client.post("https://oauth2.googleapis.com/token", data={
            "code":          code,
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri":  GOOGLE_REDIRECT_URI,
            "grant_type":    "authorization_code",
        })
        tok.raise_for_status()
        access_token = tok.json().get("access_token")

        info_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        info_resp.raise_for_status()
        u = info_resp.json()

    user = await _upsert_user("google", u["sub"], u.get("email", ""),
                              u.get("name"), u.get("picture"))
    request.session["user_id"] = user.id
    return RedirectResponse("/")


@router.post("/auth/apple/callback")
async def apple_callback(request: Request):
    if not APPLE_CLIENT_ID:
        return RedirectResponse("/?auth_error=apple_not_configured")

    try:
        import jwt as pyjwt
        from jwt.algorithms import RSAAlgorithm
    except ImportError:
        logging.error("PyJWT not installed — cannot verify Apple id_token")
        return RedirectResponse("/?auth_error=apple_failed")

    form = await request.form()
    id_token = form.get("id_token")
    if not id_token:
        raise HTTPException(400, "Missing id_token from Apple")

    try:
        header = pyjwt.get_unverified_header(id_token)
        kid = header.get("kid")

        async with httpx.AsyncClient() as client:
            keys_resp = await client.get("https://appleid.apple.com/auth/keys")
            keys_resp.raise_for_status()
            jwks = keys_resp.json()

        pub_key = None
        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                pub_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
                break
        if pub_key is None:
            raise ValueError("Apple signing key not found in JWKS")

        payload = pyjwt.decode(id_token, pub_key, algorithms=["RS256"],
                               audience=APPLE_CLIENT_ID)
        apple_uid = payload["sub"]
        email = payload.get("email") or f"apple_{apple_uid[:8]}@privaterelay.appleid.com"

        user_json = form.get("user") or "{}"
        user_info = json.loads(user_json) if isinstance(user_json, str) else {}
        n = user_info.get("name", {})
        name = f"{n.get('firstName', '')} {n.get('lastName', '')}".strip() or None

        user = await _upsert_user("apple", apple_uid, email, name)
        request.session["user_id"] = user.id
        return RedirectResponse("/")

    except Exception as exc:
        logging.error(f"Apple auth error: {exc}")
        return RedirectResponse("/?auth_error=apple_failed")


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


@router.get("/api/me")
async def me(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await _get_user(uid)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Session expired")
    return {
        "id":       user.id,
        "email":    user.email,
        "name":     user.name,
        "picture":  user.picture,
        "provider": user.provider,
    }


@router.get("/api/user/portfolio")
async def get_portfolio(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401)
    user = await _get_user(uid)
    if not user:
        raise HTTPException(status_code=401)
    data = json.loads(user.portfolio_json) if user.portfolio_json else None
    return {"portfolio": data}


@router.post("/api/user/portfolio")
async def save_portfolio(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401)
    body = await request.json()
    portfolio = body.get("portfolio")
    if portfolio is None:
        raise HTTPException(400, "Missing portfolio field")
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == uid))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401)
        user.portfolio_json = json.dumps(portfolio, ensure_ascii=False)
        user.updated_at = datetime.utcnow()
        await session.commit()
    return {"ok": True}
