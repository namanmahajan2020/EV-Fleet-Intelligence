"""Minimal HS256 bearer token and PBKDF2 password primitives for the local demo."""
import base64
import hashlib
import hmac
import json
import os
import time

SECRET = os.environ["JWT_SECRET"].encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt, expected = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        actual = _b64(hashlib.pbkdf2_hmac("sha256", password.encode(), base64.urlsafe_b64decode(salt + "=="), int(iterations)))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_token(subject: str, role: str, fleet_id: str, ttl_seconds: int = 3600) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"sub": subject, "role": role, "fleet_id": fleet_id, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")).encode())
    body = f"{header}.{payload}"
    return f"{body}.{_b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())}"


def verify_token(token: str) -> dict | None:
    try:
        header, payload, signature = token.split(".")
        decoded_header = json.loads(base64.urlsafe_b64decode(header + "=="))
        if decoded_header.get("alg") != "HS256" or decoded_header.get("typ") != "JWT":
            return None
        body = f"{header}.{payload}"
        expected = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        if (
            not isinstance(claims.get("exp"), (int, float))
            or claims["exp"] <= time.time()
            or not claims.get("sub")
            or claims.get("role") not in {"fleet_manager", "analyst", "operator"}
        ):
            return None
        return claims
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
