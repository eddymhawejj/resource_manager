"""Generate encrypted tokens for guacamole-lite.

Token format must match guacamole-lite's Crypt.js:
  1. JSON-encode the payload
  2. PKCS7-pad and AES-256-CBC encrypt
  3. Base64-encode {iv, value} JSON object
"""

import base64
import json
import os

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad


def encrypt_token(payload: dict, secret_key: str) -> str:
    """Encrypt a connection payload for guacamole-lite.

    Args:
        payload: dict like {"connection": {"type": "rdp", "settings": {...}}}
        secret_key: 32-byte string (AES-256 key)

    Returns:
        Base64-encoded encrypted token string.
    """
    key = secret_key.encode('utf-8')[:32].ljust(32, b'\0')
    iv = os.urandom(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)

    plaintext = json.dumps(payload).encode('utf-8')
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))

    data = {
        'iv': base64.b64encode(iv).decode('utf-8'),
        'value': base64.b64encode(ciphertext).decode('utf-8'),
    }
    return base64.b64encode(json.dumps(data).encode('utf-8')).decode('utf-8')
