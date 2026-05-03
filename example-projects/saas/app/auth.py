"""Stand-in password hashing utilities.

The functions here are deliberately minimal — production code should use
``passlib`` / ``argon2`` / ``bcrypt`` instead — but they are real one-way
hashes so the example can demonstrate the security-relevant flow:
the request body's plaintext ``password`` is converted into a stored
``password_hash`` *before* persistence.

The whole reason this helper exists is to make the mistake from
``rut-notes/discussion_save_object.md`` reproducible: if ``handle_create``
calls ``super().handle_create()`` and only then sets ``password_hash``, the
in-memory mutation never reaches the database — the row is committed
with ``password_hash = ""``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

_ITERATIONS = 100_000
_SALT_BYTES = 16


def hash_password(plaintext: str) -> str:
    """Return a salted PBKDF2-SHA256 digest formatted as ``salt$digest`` hex."""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plaintext.encode("utf-8"), salt, _ITERATIONS
    )
    return f"{salt.hex()}${digest.hex()}"


def verify_password(plaintext: str, stored: str) -> bool:
    """Constant-time check of ``plaintext`` against the stored ``salt$digest``."""
    if not stored or "$" not in stored:
        return False
    salt_hex, digest_hex = stored.split("$", 1)
    salt = bytes.fromhex(salt_hex)
    expected = hashlib.pbkdf2_hmac(
        "sha256", plaintext.encode("utf-8"), salt, _ITERATIONS
    )
    return hmac.compare_digest(expected.hex(), digest_hex)


def random_token(n_bytes: int = 32) -> str:
    """Helper for password reset tokens / API keys in the showcase."""
    return secrets.token_urlsafe(n_bytes)
