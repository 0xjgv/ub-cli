"""Verify minisign signatures against a known public key."""

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

TRUSTED = "trusted comment: "


def verify(public_key: str, message: bytes, signature: str) -> str:
    """Return the trusted comment of a valid signature, or raise ValueError."""
    key = base64.b64decode(public_key, validate=True)
    if len(key) != 42 or key[:2] != b"Ed":
        raise ValueError("invalid minisign public key")
    lines = signature.splitlines()
    if len(lines) != 4 or not lines[2].startswith(TRUSTED):
        raise ValueError("malformed minisign signature")
    blob = base64.b64decode(lines[1], validate=True)
    global_signature = base64.b64decode(lines[3], validate=True)
    if len(blob) != 74 or len(global_signature) != 64:
        raise ValueError("malformed minisign signature")
    algorithm, key_id, file_signature = blob[:2], blob[2:10], blob[10:]
    if key_id != key[2:10]:
        raise ValueError("signature was made with a different key")
    if algorithm == b"ED":
        message = hashlib.blake2b(message).digest()
    elif algorithm != b"Ed":
        raise ValueError("unsupported minisign signature algorithm")
    trusted = lines[2][len(TRUSTED):]
    verifier = Ed25519PublicKey.from_public_bytes(key[10:])
    try:
        verifier.verify(file_signature, message)
        verifier.verify(global_signature, file_signature + trusted.encode())
    except InvalidSignature:
        raise ValueError("signature verification failed") from None
    return trusted
