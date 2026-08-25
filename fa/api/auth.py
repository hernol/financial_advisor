"""Who is asking, and which account they get.

Three modes, chosen by configuration rather than by a build flag:

* **open** — no credential required. Only legitimate on a loopback address, so
  binding anywhere else refuses to start without one of the modes below.
* **token** — a shared secret. What a home network needs: the laptop and the
  phone are the same person, so there is one account and the token only has to
  keep the rest of the network out.
* **supabase** — a signed JWT. The subject maps to a row in ``account_users``,
  which is what makes more than one account possible at all.

Whichever mode is active, every request ends up with an ``account_id``, and the
stores take it. Nothing downstream knows which mode produced it.
"""
from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, Header, HTTPException

from fa.api.deps import get_db
from fa.config import Settings, load_settings
from fa.store.database import Database
from fa.store.schema import LOCAL_ACCOUNT_ID

logger = logging.getLogger(__name__)

OPEN = "open"
TOKEN = "token"
SUPABASE = "supabase"


@dataclass(frozen=True)
class Principal:
    """The caller, resolved to an account."""

    account_id: int
    mode: str
    subject: str = ""
    email: str = ""

    @property
    def is_anonymous(self) -> bool:
        return self.mode == OPEN


def mode_for(settings: Settings) -> str:
    if settings.supabase_url and settings.supabase_jwt_secret:
        return SUPABASE
    if settings.api_token:
        return TOKEN
    return OPEN


def _bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def resolve(
    settings: Settings, db: Database, authorization: str | None
) -> Principal:
    """Turn a credential into a principal, or refuse."""
    mode = mode_for(settings)
    if mode == OPEN:
        return Principal(account_id=LOCAL_ACCOUNT_ID, mode=OPEN)

    presented = _bearer(authorization)
    if not presented:
        raise _unauthorized("Falta el token. Iniciá sesión para usar la API.")

    if mode == TOKEN:
        # Constant time: a plain == leaks the shared secret one character at a
        # time to anyone who can measure the response.
        if not hmac.compare_digest(presented, settings.api_token or ""):
            raise _unauthorized("Token inválido.")
        return Principal(account_id=LOCAL_ACCOUNT_ID, mode=TOKEN)

    claims = verify_supabase_jwt(presented, settings)
    subject = str(claims.get("sub") or "")
    if not subject:
        raise _unauthorized("El token no identifica a ningún usuario.")
    return Principal(
        account_id=account_for(db, subject, str(claims.get("email") or "")),
        mode=SUPABASE,
        subject=subject,
        email=str(claims.get("email") or ""),
    )


def verify_supabase_jwt(token: str, settings: Settings) -> dict[str, Any]:
    """Validate signature, expiry and audience.

    PyJWT is imported lazily: the local and token modes never need it, and a
    missing dependency should say what to install rather than crash on import.
    """
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "SUPABASE_URL está configurada pero falta PyJWT. Instalá 'pyjwt'."
        ) from exc

    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("La sesión expiró. Entrá de nuevo.") from exc
    except jwt.InvalidTokenError as exc:
        # The reason is logged, never returned: telling a caller exactly why a
        # token failed helps them forge a better one.
        logger.warning("rejected a supabase token: %s", exc)
        raise _unauthorized("Token inválido.") from exc


def account_for(db: Database, subject: str, email: str = "") -> int:
    """The account this Supabase user belongs to, creating one on first sight.

    Sign-up and account creation are the same event here. Splitting them would
    mean a user who authenticated successfully still has nowhere to put data.
    """
    row = db.execute(
        "SELECT account_id FROM account_users WHERE auth_id = ?", (subject,)
    ).fetchone()
    if row is not None:
        return int(row["account_id"])

    now = datetime.now(timezone.utc).isoformat()
    account_id = db.insert(
        "INSERT INTO accounts(name, owner_email, plan, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?)",
        (email or "personal", email, "free", now, now),
    )
    db.execute(
        "INSERT INTO account_users(account_id, auth_id, email, role, created_at) "
        "VALUES(?, ?, ?, ?, ?)",
        (account_id, subject, email, "owner", now),
    )
    db.commit()
    logger.info("created account %s for %s", account_id, email or subject)
    return account_id


def current_principal(
    authorization: str | None = Header(default=None),
    db: Database = Depends(get_db),
) -> Principal:
    """FastAPI dependency. Every router depends on this, not on a raw header."""
    return resolve(load_settings(), db, authorization)


def account_id(principal: Principal = Depends(current_principal)) -> int:
    """The shorthand the endpoints actually want."""
    return principal.account_id
