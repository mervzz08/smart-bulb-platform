"""Two ways to authenticate API callers:

* API keys (X-API-Key header): simple, long-lived, good for scripts and
  dashboards. Only SHA-256 hashes of keys are configured, never the keys.
* OAuth 2.0 client credentials (RFC 6749 section 4.4): a backend service trades
  its client_id/secret for a short-lived, scoped JWT bearer token, so the
  long-lived secret is sent once instead of on every request.

Both produce a Principal with scopes; routes declare the scope they need.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Literal

import jwt
from fastapi import HTTPException, Request, status

from .config import OAuthClient, Settings

ISSUER = "bulb-bridge"
AUDIENCE = "bulb-bridge-api"
_DUMMY_HASH = "0" * 64


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]
    method: Literal["api_key", "oauth"]


class Authenticator:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    # ---- OAuth 2.0 -------------------------------------------------------
    def authenticate_client(self, client_id: str, secret: str) -> OAuthClient | None:
        client = self._s.oauth_clients.get(client_id)
        # Hash and compare even for unknown clients, so response time does not
        # reveal which client IDs exist.
        expected = client.secret_sha256 if client else _DUMMY_HASH
        ok = hmac.compare_digest(sha256_hex(secret), expected)
        return client if client and ok else None

    def issue_token(self, client: OAuthClient, scopes: frozenset[str]) -> dict:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": client.client_id,
            "scope": " ".join(sorted(scopes)),
            "iat": now,
            "exp": now + self._s.token_ttl_s,
            "jti": secrets.token_hex(8),
        }
        return {
            "access_token": jwt.encode(claims, self._s.jwt_secret, algorithm="HS256"),
            "token_type": "Bearer",
            "expires_in": self._s.token_ttl_s,
            "scope": claims["scope"],
        }

    def principal_from_token(self, token: str) -> Principal | None:
        try:
            claims = jwt.decode(
                token,
                self._s.jwt_secret,
                algorithms=["HS256"],  # pinned: never let the token choose its own algorithm
                audience=AUDIENCE,
                issuer=ISSUER,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError:
            return None
        return Principal(claims["sub"], frozenset(str(claims.get("scope", "")).split()), "oauth")

    # ---- API keys --------------------------------------------------------
    def principal_from_api_key(self, key: str) -> Principal | None:
        entry = self._s.api_keys.get(sha256_hex(key))
        return Principal(entry.name, entry.scopes, "api_key") if entry else None

    # ---- request ---------------------------------------------------------
    def from_request(self, request: Request) -> Principal | None:
        key = request.headers.get("x-api-key")
        if key:
            return self.principal_from_api_key(key)
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and token:
            return self.principal_from_token(token.strip())
        return None


def require_scope(scope: str) -> Callable[[Request], Principal]:
    def dependency(request: Request) -> Principal:
        principal = request.app.state.auth.from_request(request)
        if principal is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid credentials",
                                headers={"WWW-Authenticate": 'Bearer realm="bulb-bridge"'})
        if scope not in principal.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing scope: {scope}",
                                headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'})
        return principal

    return dependency
