"""Password hashing helpers."""

from passlib.hash import sha512_crypt  # type: ignore[import-untyped]

def hash_password(password: str) -> str:
    """Hash password for /etc/shadow."""
    # XCP-ng uses sha512 with 5000 rounds by default
    return sha512_crypt.using(rounds=5000).hash(password)
