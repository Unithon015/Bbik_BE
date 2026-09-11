import unittest

from src.infrastructure.security.password_hasher import PasswordHasher


class PasswordHasherTest(unittest.TestCase):
    def test_hashes_and_verifies_without_storing_plaintext(self):
        hasher = PasswordHasher()
        password = "correct horse battery staple"
        password_hash = hasher.hash(password)

        self.assertNotIn(password, password_hash)
        self.assertTrue(hasher.verify(password_hash, password))
        self.assertFalse(hasher.verify(password_hash, "wrong password"))
        self.assertFalse(hasher.verify(None, password))
