import re
import time
from collections import deque
from typing import Deque, Dict, Optional

from app.core.errors import BadRequest, TooManyRequests, Unauthorized

_JWKS_CLIENTS: Dict[str, object] = {}


def _jwks_client(url: str):
    import jwt

    if url not in _JWKS_CLIENTS:
        _JWKS_CLIENTS[url] = jwt.PyJWKClient(url, cache_keys=True)
    return _JWKS_CLIENTS[url]


ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,100}$")


def resolve_user(settings, headers, claimed: Optional[str]) -> str:
    """Return the caller's user id.
    jwt mode: verified Supabase access token (the only mode safe for live money).
    demo mode: the client-asserted id (anyone can claim any id; sandbox use only)."""
    if settings.AUTH_MODE == "jwt":
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise Unauthorized("Missing bearer token")
        import jwt  # PyJWT

        token = auth[7:].strip()
        try:
            if getattr(settings, "SUPABASE_JWKS_URL", ""):
                key = _jwks_client(settings.SUPABASE_JWKS_URL).get_signing_key_from_jwt(token).key
                payload = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience="authenticated")
            else:
                payload = jwt.decode(token, settings.SUPABASE_JWT_SECRET,
                                     algorithms=["HS256"], audience="authenticated")
        except jwt.PyJWTError:
            raise Unauthorized("Invalid or expired token")
        sub = payload.get("sub")
        if not sub:
            raise Unauthorized("Token has no subject")
        return str(sub)
    if not claimed or not ID_RE.match(claimed):
        raise BadRequest("A valid user_id is required")
    return claimed


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: Dict[str, Deque[float]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            raise TooManyRequests("Too many requests. Please slow down.")
        q.append(now)
        if len(self._hits) > 10000:
            self._hits = {k: v for k, v in self._hits.items() if v}
