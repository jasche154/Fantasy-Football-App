"""Encrypt OAuth tokens at rest with Fernet (AES-128-CBC + HMAC).

Generate a key once and put it in backend/.env as TOKEN_ENCRYPTION_KEY:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from cryptography.fernet import Fernet, InvalidToken


class TokenCryptoError(RuntimeError):
    pass


def _fernet(key: str | None) -> Fernet:
    if not key:
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEY is not set in backend/.env (see README).")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key.") from exc


def require_valid_key(key: str | None) -> None:
    """Fail fast (before spending a one-time OAuth code) if tokens couldn't be stored."""
    _fernet(key)


def sign_state(purpose: str, key: str | None) -> str:
    """An OAuth `state` value only this app can produce. Fernet tokens carry their creation
    time, so it can expire without the server remembering anything (restarts are harmless)."""
    return _fernet(key).encrypt(purpose.encode()).decode()


def state_is_valid(state: str, purpose: str, key: str | None, max_age_seconds: int) -> bool:
    try:
        return _fernet(key).decrypt(state.encode(), ttl=max_age_seconds) == purpose.encode()
    except InvalidToken:
        return False


def encrypt(value: str | None, key: str | None) -> str | None:
    return None if value is None else _fernet(key).encrypt(value.encode()).decode()


def decrypt(value: str | None, key: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _fernet(key).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise TokenCryptoError("Stored token can't be decrypted; was TOKEN_ENCRYPTION_KEY changed?") from exc
