import hmac
import os
import secrets
from pathlib import Path
from typing import Optional
from fastapi import Header, HTTPException, Query, Request
from app.config import settings

TOKEN_FILE = settings.data_dir / ".rally_token"
APPROVAL_SECRET_FILE = settings.data_dir / ".rally_approval_secret"

ALLOWED_HOSTS = {"127.0.0.1:8765", "localhost:8765", "127.0.0.1", "localhost", "testserver"}
ALLOWED_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "null",  # Electron local file:// origin
}


def get_or_create_api_token() -> str:
    """Retrieves or generates the persistent 256-bit local API bearer token."""
    if TOKEN_FILE.exists():
        try:
            token = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if len(token) >= 32:
                return token
        except Exception:
            pass

    token = secrets.token_hex(32)
    try:
        TOKEN_FILE.write_text(token, encoding="utf-8")
        if os.name == "nt":
            # On Windows, mark file hidden to avoid accidental exposure
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(TOKEN_FILE), 0x02)  # FILE_ATTRIBUTE_HIDDEN
            except Exception:
                pass
    except Exception as exc:
        print(f"[SECURITY] Warning: Could not write token file: {exc}")
    return token


def get_or_create_approval_secret() -> str:
    """Retrieves or generates the out-of-band credential required for consequential approvals."""
    if APPROVAL_SECRET_FILE.exists():
        try:
            secret = APPROVAL_SECRET_FILE.read_text(encoding="utf-8").strip()
            if len(secret) >= 32:
                return secret
        except Exception:
            pass

    secret = secrets.token_hex(32)
    try:
        APPROVAL_SECRET_FILE.write_text(secret, encoding="utf-8")
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(APPROVAL_SECRET_FILE), 0x02)
            except Exception:
                pass
    except Exception as exc:
        print(f"[SECURITY] Warning: Could not write approval secret file: {exc}")
    return secret


API_TOKEN = get_or_create_api_token()
APPROVAL_SECRET = get_or_create_approval_secret()


def validate_host_and_origin(request: Request) -> None:
    """
    Prevents DNS rebinding attacks and cross-origin hijacking from external websites.
    Rejects any request claiming to hit localhost via an external domain.
    """
    host = request.headers.get("host", "").lower().strip()
    # Allow test client or local hostnames
    if host and not any(host.startswith(h) for h in ALLOWED_HOSTS):
        raise HTTPException(
            status_code=403,
            detail=f"Security violation: Host '{host}' rejected. RALLY only binds to localhost/127.0.0.1.",
        )

    origin = request.headers.get("origin")
    if origin:
        origin_clean = origin.lower().rstrip("/")
        if origin_clean not in ALLOWED_ORIGINS and not origin_clean.startswith("http://127.0.0.1:") and not origin_clean.startswith("http://localhost:"):
            raise HTTPException(
                status_code=403,
                detail=f"Security violation: Untrusted Origin '{origin}' blocked. External web pages cannot control RALLY.",
            )


def verify_bearer_token(
    request: Request,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
) -> str:
    """
    Verifies that the request possesses the valid local bearer token.
    Allows header 'Authorization: Bearer <token>' or query param '?token=' (for EventSource SSE).
    Exempts the health check endpoint for monitoring.
    """
    if request.url.path in {"/api/v1/health", "/docs", "/openapi.json"}:
        return ""

    # Always enforce Host and Origin validation
    validate_host_and_origin(request)

    extracted = None
    if authorization and authorization.startswith("Bearer "):
        extracted = authorization[7:].strip()
    elif token:
        extracted = token.strip()

    if not extracted:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: Missing RALLY bearer token in Authorization header or token query parameter.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(extracted, API_TOKEN):
        raise HTTPException(
            status_code=401,
            detail="Authentication failed: Invalid RALLY bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return extracted


def verify_approval_secret(
    request: Request,
    x_rally_approval_token: Optional[str] = Header(None),
) -> str:
    """
    Enforces out-of-band authorization for consequential actions (Tier 2/3 approvals).
    Prevents the agent or compromised scripts from self-approving actions.
    Requires 'X-Rally-Approval-Token' header holding the out-of-band approval secret.
    """
    validate_host_and_origin(request)

    if not x_rally_approval_token:
        raise HTTPException(
            status_code=403,
            detail="Self-approval blocked: Consequential action approval requires out-of-band X-Rally-Approval-Token.",
        )

    if not hmac.compare_digest(x_rally_approval_token.strip(), APPROVAL_SECRET):
        raise HTTPException(
            status_code=403,
            detail="Approval rejected: Invalid out-of-band approval token.",
        )

    return x_rally_approval_token
