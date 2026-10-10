"""Two things a SEB config needs to keep: a hash nobody can read, and a copy some staff must.

The same password is stored **twice**, and the two copies are not a mistake
--------------------------------------------------------------
This is the part of the SEB feature that looks redundant and is not:

* :func:`sha256_hex` produces the value that goes into the ``.seb`` file as
  ``hashedQuitPassword`` / ``hashedAdminPassword``. SEB compares against it to
  decide whether the person typing at the locked machine may leave the exam. It
  is one-way, and it is what makes the exit actually locked. **This is the copy
  the file gets, and the file never gets the other one.**
* :func:`encrypt` / :func:`decrypt` produce the copy in
  ``exam_seb_credential.*_password_enc``. It exists for the human case the whole
  feature is built around: a pupil whose phone dies mid-paper, or who genuinely
  needs to leave early, is stuck behind a password *the server generated and
  nobody typed*. Without a recoverable copy, that password would be lost at the
  moment it was created and the only way out would be to edit and re-issue the
  ``.seb`` file — so the recoverable copy is what lets a teacher, a school admin
  or the invigilator on duty read the password out loud.

Why the ``.seb`` file must never carry the recoverable copy: the pupil can open
that file. It carries the hash and only the hash, which is exactly why the pupil
who downloads it still cannot leave the exam.

What the format of the hash is, and why it is not a guess
--------------------------------------------------------
``hashedQuitPassword`` is a bare Base16 SHA-256 string, **lower case, with no
``SHA256:`` prefix**. That is not inferred from a library: it is what the
published worked example on the official Config Key page contains —
``"hashedQuitPassword":"8577da2ea54085708b3b851bc50315a36bb740ba5135e747cfb12457b5d3060f"``
— and it is the shape a real client compares against. A ``SHA256:`` prefix, which
several blog posts show, would hash to a different Config Key and would never
match the password a supervisor typed.

The encryption is a real AEAD, not a home-made cipher
-----------------------------------------------------
At-rest confidentiality of a value the application must hand back in clear is not
a place to invent anything, so this uses AES-256-GCM from ``cryptography`` with a
key derived per-application via HKDF-SHA256 from ``SECRET_KEY``, and a fresh
96-bit nonce from the operating system's CSPRNG for every encryption. The stored
form is versioned (``v1:``) so a future key rotation can add ``v2`` without
guessing what an old blob is.

``SECRET_KEY`` is already the app's root secret — session cookies and the media
URL signatures are built from it (:mod:`app.services.exam_media`). Reusing it
means a school has **nothing extra to manage** to get SEB working, which is the
same reason the schema derives the key from application credentials rather than a
separate ``.env`` file. The consequence, stated plainly: rotating ``SECRET_KEY``
makes every stored SEB password unreadable, and it is *not* the same as losing
them, because the hash in the issued ``.seb`` file still works — only the "read it
aloud" path breaks, and re-issuing the credential repairs it.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets

#: Bumped when the stored blob's construction changes. A reader that does not
#: recognise a version must refuse rather than guess — a wrong decryption that
#: "succeeds" would hand a supervisor a string that is not the password.
ENC_VERSION = "v1"

#: HKDF context. Fixed, and part of the derivation: changing it silently changes
#: every key, which is why it is named here rather than built at a call site.
KEY_INFO = b"scangrade/seb/at-rest/v1"

#: The alphabet a generated password is drawn from, and the reason it is not
#: `string.ascii_letters + string.digits`: this password is *read aloud over the
#: phone* to a pupil or typed by a supervisor from a screen, so `0`/`O` and
#: `1`/`l`/`I` are the difference between a working exit and a support call. The
#: characters removed are the ones a human confuses, not arbitrary ones.
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"

#: 12 characters over the 57-symbol alphabet above: about 70 bits, which is far
#: past guessing for a value that is only ever valid for one exam and is rate
#: limited at the door that reads it.
PASSWORD_LENGTH = 12


class SebCryptoError(RuntimeError):
    """The at-rest copy could not be produced or recovered, with the reason."""


def sha256_hex(text: str) -> str:
    """The Base16 SHA-256 SEB expects in ``hashedQuitPassword``.

    Lower case and unprefixed, per the official example. Nothing here trims or
    normalises: a password with a leading space hashes with it, exactly as SEB
    will hash what the supervisor typed.
    """
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def mint_password(length: int = PASSWORD_LENGTH) -> str:
    """A fresh password from :data:`PASSWORD_ALPHABET`, using the OS CSPRNG.

    ``secrets`` rather than ``random``: this value is the only thing standing
    between a pupil and the closed exam, so a predictable sequence would be a
    lock with the key taped to it.
    """
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(max(1, int(length))))


# ── the key, derived from the app's own root secret ─────────────────────────

def key_from(master: bytes | str) -> bytes:
    """A 32-byte AEAD key derived from a master secret (HKDF-SHA256).

    Separate from :func:`_master` so the derivation is testable without a Flask
    app: a test can pin that the same master always yields the same key and that
    the context string is part of the derivation.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    if isinstance(master, str):
        master = master.encode("utf-8")
    if not master:
        raise SebCryptoError("no master secret to derive an SEB key from")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=KEY_INFO).derive(master)


def _master_secret() -> bytes:
    """``SECRET_KEY`` as bytes, refusing rather than degrading.

    A missing ``SECRET_KEY`` must not silently become an empty key: that would
    encrypt every school's SEB passwords under a key everybody knows. The
    application already refuses to boot without a real one
    (``app/config.py`` rejects the repository placeholder outside testing), so
    this only fires in a misconfigured process — and it fires loudly.
    """
    from flask import current_app

    secret = (current_app.config.get("SECRET_KEY") or "")
    if not secret:
        raise SebCryptoError(
            "SECRET_KEY is not configured, so SEB passwords cannot be stored")
    return secret.encode("utf-8") if isinstance(secret, str) else secret


def encrypt(plaintext: str, *, secret: bytes | str | None = None) -> str:
    """The stored, reversible copy: ``v1:<b64 nonce>:<b64 ciphertext+tag>``.

    A fresh nonce per call, from ``os.urandom``. Reusing a nonce under one key is
    the classic way GCM fails completely, so it is generated here and never
    derived from the plaintext.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = key_from(secret if secret is not None else _master_secret())
    nonce = os.urandom(12)
    blob = AESGCM(key).encrypt(nonce, str(plaintext or "").encode("utf-8"), None)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode("ascii")  # noqa: E731
    return f"{ENC_VERSION}:{b64(nonce)}:{b64(blob)}"


def decrypt(stored: str, *, secret: bytes | str | None = None) -> str:
    """Recover the plaintext password, or raise :class:`SebCryptoError`.

    Refuses an unrecognised version and a tampered blob (GCM's tag check) rather
    than returning a plausible-looking string. A wrong answer here is worse than
    an error: the supervisor would read a value to a pupil that does not unlock
    anything, and neither of them would know why.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag

    if not stored:
        raise SebCryptoError("nothing stored to decrypt")
    parts = str(stored).split(":")
    if len(parts) != 3 or parts[0] != ENC_VERSION:
        raise SebCryptoError(
            f"unrecognised stored SEB ciphertext (expected {ENC_VERSION}:…)")
    try:
        nonce = base64.urlsafe_b64decode(parts[1].encode("ascii"))
        blob = base64.urlsafe_b64decode(parts[2].encode("ascii"))
    except Exception as exc:  # noqa: BLE001 — malformed base64 is a refusal
        raise SebCryptoError(f"stored SEB ciphertext is not decodable: {exc}") from exc
    key = key_from(secret if secret is not None else _master_secret())
    try:
        return AESGCM(key).decrypt(nonce, blob, None).decode("utf-8")
    except InvalidTag as exc:
        raise SebCryptoError(
            "the stored SEB password did not authenticate — it was tampered with, "
            "or SECRET_KEY changed since it was stored") from exc


__all__ = [
    "ENC_VERSION", "KEY_INFO", "PASSWORD_ALPHABET", "PASSWORD_LENGTH",
    "SebCryptoError", "decrypt", "encrypt", "key_from", "mint_password",
    "sha256_hex",
]
