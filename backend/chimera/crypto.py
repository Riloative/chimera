"""At-rest encryption for provider credentials (Fernet: AES-128-CBC + HMAC-SHA256).

The key comes from ``CHIMERA_ENCRYPTION_KEY`` or a generated ``.encryption_key``
file (mode 0600) in the data directory. Encrypted values are stored as
``enc:v1:<token>``; plaintext values from older versions are still readable and
are re-encrypted at startup.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"
log = logging.getLogger("chimera.crypto")


class SecretBox:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        if value.startswith(PREFIX):
            return value
        return PREFIX + self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        if not value.startswith(PREFIX):
            return value  # legacy plaintext
        try:
            return self._fernet.decrypt(value[len(PREFIX) :].encode()).decode()
        except InvalidToken:
            log.error("Stored credential could not be decrypted (wrong CHIMERA_ENCRYPTION_KEY?)")
            return None
