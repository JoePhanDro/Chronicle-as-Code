"""Password hashing and token helpers. Passwords are hashed with Argon2id, tokens are stored as SHA-256 digests."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()  # Argon2id with library defaults


class PasswordPolicyError(ValueError):
    """The password does not meet the policy."""


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein")


def hash_password(password: str) -> str:
    validate_password(password)
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# Used when the username does not exist, so unknown users cost the same time as wrong passwords.
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
