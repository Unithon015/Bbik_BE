from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError


class PasswordHasher:
    """Argon2id password hashing with OWASP's minimum memory profile."""

    def __init__(self) -> None:
        self._hasher = Argon2Hasher(
            time_cost=2,
            memory_cost=19_456,
            parallelism=1,
            hash_len=32,
            salt_len=16,
        )
        self._dummy_hash = self._hasher.hash("bbik-dummy-password-verification")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        candidate = password_hash or self._dummy_hash
        try:
            valid = self._hasher.verify(candidate, password)
        except (InvalidHashError, VerificationError):
            valid = False
        return valid if password_hash else False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)
