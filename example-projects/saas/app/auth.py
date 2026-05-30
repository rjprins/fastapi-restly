"""Stand-in password hashing utilities.

The functions here are deliberately minimal — production code should use
``passlib`` / ``argon2`` / ``bcrypt`` instead — but they are real one-way
hashes so the example can demonstrate the security-relevant flow:
the request body's plaintext ``password`` is converted into a stored
``password_hash`` *before* persistence.

The whole reason this helper exists is to demonstrate the canonical
three-tier override: ``UserView`` overrides the *bare* business ``create``
verb (auth-free, commit-free), builds the row with ``make_new_object``, sets
``password_hash``, then ``save_object`` (flush, no commit). Because the commit
happens later in ``handle_create``, the hash is on the row before it is
persisted — the old "set the hash after a post-flush commit and watch the
plaintext leak" trap is structurally gone.
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
    digest = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, _ITERATIONS)
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
