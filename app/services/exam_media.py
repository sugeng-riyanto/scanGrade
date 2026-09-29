"""Question media served by us: an uploaded file, behind a link that dies with the sitting.

A listening question used to be two pasted URLs — a Drive file and a YouTube video —
resolved in the pupil's browser by ``app/static/js/exam-media.js``. That arrangement
carries three properties nobody chose:

* **the file lives somewhere we cannot see.** A Drive file can be made private, moved
  or deleted after the paper is built, and the pupil's page then shows a player that
  never plays while the teacher's preview, taken at build time, looked fine;
* **the bytes come from a third party**, so a school's network policy can stop a
  question from being heard at all;
* **the link is shareable.** A pupil can open DevTools, copy the Drive URL, and hand
  it to anyone — including someone who is not sitting the paper.

What replaces it is deliberately narrow. A teacher uploads the file to our own
**private** bucket, and the page is handed `/media/<token>`: a URL whose token is a
signed claim over (storage path, the user it was minted for, the paper, an expiry).
The file itself is never publicly addressable, and the link is worth nothing in
another browser — which is the whole difference between a *sitting-bound* URL and a
*signed* one:

* the route refuses a session that is not the token's subject, so copying the URL out
  of the page does not hand the file to a class;
* the token expires in minutes, so a URL that does leak is worthless after it;
* the signed URL that Storage understands is minted *inside* this app, lives for two
  minutes, and is only ever used by our own request. The browser never sees it — which
  is why nothing here returns a Storage URL to a page.

Three decisions worth stating rather than discovering:

* **Pasted links keep working.** Papers written before this still paste a Drive or
  YouTube link; a question carrying one is handed to the page exactly as it was stored
  (see ``with_media_urls``), so the legacy path in ``exam-media.js`` stays reachable and
  nothing that plays today stops playing.
* **The upload ceiling sits below Flask's.** ``MAX_CONTENT_LENGTH`` refuses a request
  body before any handler runs, and a ceiling above it is a promise this module cannot
  keep — the teacher would see a bare 413 with no sentence from us.
* **The bytes are streamed, not buffered.** A Range request is passed through to
  Storage and the 206 answered as it comes, so seeking inside a track does not
  re-download it and a 20 MB file does not sit in the worker's memory on a 1-vCPU box.
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import json
import re
import time

import requests
from flask import Response, current_app

#: The private bucket question media lives in. Private on purpose: a public bucket
#: would hand every pupil the same static URL, which is the arrangement being replaced.
MEDIA_BUCKET = "exam-media"

#: How long a page's media URL stays valid. Long enough to sit a question, short
#: enough that a URL copied out of the page is dead before it can be passed around.
TOKEN_TTL_SECONDS = 900

#: How long *our own* request to Storage may live. This is the "short-lived signed
#: URL" in its narrow sense: it is minted per request, used immediately, and never
#: handed to a browser.
SIGNED_URL_TTL_SECONDS = 120

#: The upstream read size. 64 KiB is small enough that nothing accumulates and large
#: enough that a track does not become thousands of round trips.
CHUNK_BYTES = 64 * 1024

#: What may be uploaded, per kind. `content_type` is what we store the object as, so
#: the browser is told the right thing regardless of what the client claimed.
KINDS = {
    "audio": {
        "max_bytes": 25 * 1000 * 1000,
        "extensions": (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav",
                       ".webm", ".flac"),
        "content_types": ("audio/mpeg", "audio/mp4", "audio/aac", "audio/ogg",
                          "audio/opus", "audio/wav", "audio/x-wav", "audio/webm",
                          "audio/flac", "application/ogg"),
        "content_type": "audio/mpeg",
    },
    "video": {
        # Below `MAX_CONTENT_LENGTH` (50 MB) on purpose — see the module docstring.
        "max_bytes": 45 * 1000 * 1000,
        "extensions": (".mp4", ".m4v", ".webm", ".ogv", ".mov"),
        "content_types": ("video/mp4", "video/webm", "video/ogg", "video/quicktime"),
        "content_type": "video/mp4",
    },
}

#: File extensions per kind, and the type guessed from one when the client sends
#: something vague like `application/octet-stream`.
_EXTENSION_TYPE = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
    ".wav": "audio/wav", ".webm": "video/webm", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".ogv": "video/ogg",
    ".mov": "video/quicktime",
}


# ── the token ───────────────────────────────────────────────────────────────

def b64(data: bytes) -> bytes:
    """URL-safe base64 with the padding stripped, which keeps a token URL-clean."""
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _secret() -> bytes:
    """The signing key, and a refusal to sign without one.

    A token signed with an empty secret is a token anybody can mint, so a box that
    cannot tell us its secret must fail loudly rather than issue forgeable links.
    """
    secret = (current_app.config.get("SECRET_KEY") or "")
    if not secret:
        raise RuntimeError("SECRET_KEY is not configured, so media tokens cannot be signed")
    return str(secret).encode("utf-8")


def sign_media_token(*, path: str, subject: str, exam_id: str,
                     ttl: int = TOKEN_TTL_SECONDS, now: float | None = None) -> str:
    """A claim over (path, subject, paper, expiry), signed with the app's secret."""
    issued = int(now if now is not None else time.time())
    payload = json.dumps({"p": str(path), "s": str(subject), "x": str(exam_id),
                          "e": issued + int(ttl)}, separators=(",", ":"),
                         sort_keys=True).encode("utf-8")
    body = b64(payload)
    mac = b64(hmac.new(_secret(), body, hashlib.sha256).digest())
    return body.decode("ascii") + "." + mac.decode("ascii")


def verify_media_token(token: str, *, now: float | None = None) -> dict | None:
    """The claims this token asserts, or ``None`` for anything that is not one.

    Reached straight off a URL, so every malformed shape — empty, one part, a bad
    signature, JSON that is not JSON, a claim that is not an integer — is a refusal
    rather than an exception. And nothing is trusted before the signature is checked:
    forgiving a bad mac here would let a caller choose which file to fetch.
    """
    try:
        if not token or token.count(".") != 1:
            return None
        body, _, mac = token.partition(".")
        if not body or not mac:
            return None
        expected = b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, mac.encode("ascii")):
            return None
        claims = json.loads(_unb64(body))
        if not isinstance(claims, dict):
            return None
        for key in ("p", "s", "x", "e"):
            if key not in claims:
                return None
        expiry = int(claims["e"])
        if expiry <= int(now if now is not None else time.time()):
            return None
        return {"p": str(claims["p"]), "s": str(claims["s"]), "x": str(claims["x"]),
                "e": expiry}
    except RuntimeError:
        # No secret configured: nothing can be verified, so nothing verifies.
        return None
    except Exception:
        return None


def media_url(path: str, *, subject: str, exam_id: str,
              ttl: int = TOKEN_TTL_SECONDS, now: float | None = None) -> str:
    """The app URL a page is handed for one stored file."""
    token = sign_media_token(path=path, subject=subject, exam_id=exam_id, ttl=ttl,
                             now=now)
    return f"/media/{token}"


# ── what the page is handed ─────────────────────────────────────────────────

def with_media_urls(question_audio, *, subject: str, exam_id: str,
                    ttl: int = TOKEN_TTL_SECONDS) -> dict:
    """The paper's media as the page needs it: a hosted file gains ``url``.

    A copy, never a rewrite: the stored JSONB is what the teacher's builder reads
    back, and a token in it would be expired before it was used. Entries that carry no
    ``file`` — the pasted Drive and YouTube links papers were built with — are passed
    through untouched, including the bare-string shape older papers use.
    """
    handed: dict = {}
    for key, entry in (question_audio or {}).items():
        if isinstance(entry, dict) and entry.get("file"):
            copied = dict(entry)
            copied["url"] = media_url(str(entry["file"]), subject=subject,
                                      exam_id=exam_id, ttl=ttl)
            handed[key] = copied
        else:
            handed[key] = entry
    return handed


# ── what may be uploaded ────────────────────────────────────────────────────

def _extension(filename: str) -> str:
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def validate_media_upload(kind: str, filename: str, content_type: str,
                          size: int) -> str | None:
    """``None`` when this may be stored, else a sentence for the teacher."""
    spec = KINDS.get(kind)
    if spec is None:
        return "Jenis media tidak dikenal" if _indonesian() else "Unknown media kind"

    extension = _extension(filename)
    if not extension:
        return ("Berkas harus punya ekstensi (mp3, mp4, …)"
                if _indonesian() else
                "The file needs an extension (mp3, mp4, …)")
    if extension not in spec["extensions"]:
        allowed = ", ".join(e.lstrip(".") for e in spec["extensions"])
        return (f"Format {extension} tidak didukung untuk {kind}. Gunakan: {allowed}"
                if _indonesian() else
                f"{extension} is not supported for {kind}. Use: {allowed}")

    claimed = (content_type or "").split(";")[0].strip().lower()
    if claimed and claimed not in spec["content_types"] and claimed != "application/octet-stream":
        return ("Tipe berkas tidak cocok dengan jenis media yang dipilih"
                if _indonesian() else
                "The file's type does not match the media kind selected")

    if int(size or 0) > int(spec["max_bytes"]):
        limit = spec["max_bytes"] // (1000 * 1000)
        return (f"Berkas terlalu besar — batas {limit} MB"
                if _indonesian() else
                f"The file is too large — the limit is {limit} MB")
    return None


def _indonesian() -> bool:
    """The request's language, when there is a request at all.

    This module is also called from tests and jobs with no request context, where
    falling back to English is the only honest answer.
    """
    try:
        from flask import g
        return getattr(g, "lang", None) == "id" or getattr(g, "locale", None) == "id"
    except Exception:
        return False


def _slug(filename: str) -> str:
    """A URL-safe tail for a storage path.

    The uploaded name is untrusted: it arrives from a browser form, and a name
    carrying `../` would otherwise place an object outside the paper's prefix — which
    is the difference between one teacher's file and another school's.
    """
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    extension = _extension(name)
    stem = name[: len(name) - len(extension)] if extension else name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return (stem[:60] or "media") + extension


def media_storage_path(exam_id: str, index: int, filename: str) -> str:
    """Where one question's file lives: scoped to its paper, named by its question."""
    return f"{exam_id}/{int(index)}/{_slug(filename)}"


def upload_media(supabase, *, exam_id: str, index: int, kind: str, file_obj,
                 filename: str, content_type: str, size: int) -> dict:
    """Store one question's file and answer with what the builder saves.

    The stored object's type is decided here rather than trusted from the client: a
    browser plays what the object says it is, and `application/octet-stream` is a
    player that does not start.
    """
    ensure_bucket(supabase)
    path = media_storage_path(exam_id, index, filename)
    extension = _extension(filename)
    stored_type = _EXTENSION_TYPE.get(extension) or KINDS[kind]["content_type"]
    data = file_obj.read() if hasattr(file_obj, "read") else file_obj
    supabase.storage.from_(MEDIA_BUCKET).upload(
        path, data, {"content-type": stored_type, "upsert": "true"})
    return {"file": path, "name": _slug(filename), "kind": kind,
            "bytes": int(size or (len(data) if data is not None else 0))}


def ensure_bucket(supabase) -> None:
    """Create the media bucket if this project has never seen it.

    Idempotent, and tolerant of the client's shapes: a project where the bucket
    exists is the normal case, and one where it does not must not fail an upload.
    """
    try:
        supabase.storage.get_bucket(MEDIA_BUCKET)
        return
    except Exception:
        pass
    try:
        supabase.storage.create_bucket(MEDIA_BUCKET, {"public": False})
        current_app.logger.info("created the %s storage bucket", MEDIA_BUCKET)
    except Exception as exc:                                   # pragma: no cover - race
        current_app.logger.warning("could not create the %s bucket: %s",
                                   MEDIA_BUCKET, exc)


# ── the bytes ───────────────────────────────────────────────────────────────

def signed_download_url(supabase, path: str, ttl: int = SIGNED_URL_TTL_SECONDS) -> str:
    """A short-lived Storage URL for *our own* fetch — never for a page.

    The client has answered with `signedURL`, `signedUrl` and a relative
    `/object/sign/…` path across versions, so all three are read; a relative one is
    made absolute against the project URL, and an absolute one is used as it comes.
    """
    reply = supabase.storage.from_(MEDIA_BUCKET).create_signed_url(path, ttl)
    url = ""
    if isinstance(reply, dict):
        url = (reply.get("signedURL") or reply.get("signedUrl")
               or reply.get("signed_url") or "")
    if not url:
        raise RuntimeError(f"storage did not return a signed URL for {path}")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base = str(current_app.config.get("SUPABASE_URL") or "").rstrip("/")
    if not base:
        raise RuntimeError("SUPABASE_URL is not configured, so a relative signed URL "
                           "cannot be made fetchable")
    if not url.startswith("/"):
        url = "/" + url
    if not url.startswith("/storage/v1/"):
        url = "/storage/v1" + url
    return base + url


def open_media(url: str, range_header: str | None = None):
    """One upstream request, streamed, with the caller's Range forwarded.

    The Range is the difference between a pupil scrubbing a track and re-downloading
    it: answered without one, Storage sends the whole file and the audio element has
    nothing to seek in.
    """
    headers = {"Range": range_header} if range_header else {}
    return requests.get(url, headers=headers, stream=True, timeout=30)


def stream_media(supabase, path: str, range_header: str | None = None) -> Response:
    """The file, as the browser should receive it.

    `Accept-Ranges` is set even on a 200 because without it a browser will not offer
    the seek bar at all. `no-store` on purpose: the URL is bound to one sitting, and a
    shared or proxy cache holding it would undo exactly that.

    The body is a plain generator over the upstream response: nothing here reaches for
    a request-bound object once it starts yielding, so this stays callable (and
    testable) outside a request context, and a request that disconnects simply
    closes the upstream alongside it.
    """
    upstream = open_media(signed_download_url(supabase, path), range_header)

    def body():
        try:
            for chunk in upstream.iter_content(CHUNK_BYTES):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Type": upstream.headers.get("Content-Type", "application/octet-stream"),
        # Range responses are the one place a length must survive: the player decides
        # whether it can seek in a stream by whether it knows how long it is.
        "X-Content-Type-Options": "nosniff",
    }
    for passthrough in ("Content-Range", "Content-Length", "Content-Encoding"):
        value = upstream.headers.get(passthrough)
        if value:
            headers[passthrough] = value
    return Response(body(), status=upstream.status_code, headers=headers)
