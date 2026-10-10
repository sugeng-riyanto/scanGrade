"""The ``.seb`` file a pupil opens, and the Config Key that proves they opened ours.

Three things live here, and they are one story
---------------------------------------------
1. **The settings** the server generates for an exam (:func:`settings_for`) —
   generated **from that exam's own switches**, so the escape hatches a teacher
   closed on the exam form are the ones the file closes. The server *is* the author
   of the config, which is precisely the situation the Config Key exists for.
2. **The Config Key** (:func:`issue`) — computed by
   :mod:`app.services.seb_config_key` over exactly those settings, stored on the
   exam, and shipped inside the file.
3. **The check** (:func:`verify`) — the exam door recomputes
   ``SHA256(absolute URL + stored Config Key)`` and compares it with the header a
   real client sends.

Only omission is safe, and that is a property of the algorithm
--------------------------------------------------------------
The client hashes the keys *actually present in the file it opened* — the
official documentation says so directly ("The SEB client only uses setting
key/values to calculate the checksum, which are actually contained in an opened
config file"), and that is why a newer SEB that adds a setting does not change the
key of an old file. The consequence is worth stating because it decides this whole
module: **omitting a key is always self-consistent**, while *guessing a value
wrong* is what breaks everything.

So the generated config is deliberately conservative: every key set below is a
boolean or a string whose meaning is unambiguous, or the one integer whose units
are obvious (``taskBarHeight``, in pixels). **Enum-valued settings are left
unset** — ``sebConfigPurpose``, ``browserViewMode``, the URL-filter
``action`` codes, ``newBrowserWindowByLinkPolicy`` — because their numbering is
not stated in the documentation this module was written against, and a wrong enum
is not a cosmetic error: a wrong URL-filter action blocks the paper itself, and a
wrong ``sebConfigPurpose`` files the config as client settings instead of starting
an exam. An omitted key takes SEB's default, which the documentation defines as
the safe value, and the Config Key still matches because both sides hash the same
absent key. Enums can be added later, by a release whose author has ratified them
against a real client — which is Phase 11's job, and the reason it is listed as a
blocker rather than a formality.

No URL filtering, on purpose
----------------------------
``URLFilterEnable`` is **off**. The filter's allow/block action codes could not be
verified from documentation, and getting them backwards would block the exam page
itself — a paper that cannot be opened, for every pupil at once. Nothing is given
up by leaving it off: SEB's locked mode has already removed the address bar, the
tab strip, the taskbar and the other applications, so there is no way to navigate
anywhere else. A dead end that cannot be misconfigured is worth more here than a
filter that might be.

What the file does **not** contain, and why that matters
--------------------------------------------------------
It carries ``hashedQuitPassword`` / ``hashedAdminPassword`` — the SHA-256 the
client compares against — and **never** the recoverable copy of either password
(``exam_seb_credential.*_password_enc``). A pupil can open this file, and that is
exactly why the file is written from the hash alone: a pupil in possession of the
file still cannot leave the exam.

One honest limit, stated here rather than discovered in production
------------------------------------------------------------------
The Config Key is computed over the settings, so a pupil who holds the file can
compute the same key and could forge the header from an ordinary browser. SEB is
therefore a **strong deterrent and a real lock on the client**, not a proof that
the person is honest — and the UI says so. What it does guarantee is that the
paper opens only in SEB configured *our* way, because any edit to the file changes
the key and the door refuses.
"""
from __future__ import annotations

import gzip
import plistlib
from datetime import datetime, timezone

from app.services import seb_config_key as ck
from app.services import seb_crypto
from app.utils import exam_window
from app.utils.logger import get_logger

logger = get_logger("seb_service")

#: The four-byte prefix of an unencrypted ``.seb`` payload. ``plnd`` is one of the
#: five the official file-format page defines, and it is the one that needs no
#: password to open — which is the point, because SEB prompts the pupil for the
#: ``.seb`` password and a pupil who must be given a password to sit the exam is
#: not a pupil kept away from the config. Tampering is caught by the Config Key
#: instead, which is a *stronger* answer than a password: any edit changes it.
PLAIN_PREFIX = b"plnd"

#: Metadata about which client saved the file. Exempt from the hash (the algorithm
#: drops it), so it costs nothing and makes the file legible to a human.
ORIGINATOR_VERSION = "ScanGrade_1.0"

#: What a generated password backs, and the two labels the UI and the audit log
#: use. Named once so a route cannot ask for "admin" while the column is "admin".
KIND_QUIT = "quit"
KIND_ADMIN = "admin"
KINDS = (KIND_QUIT, KIND_ADMIN)


def _xml_escape(text: str) -> str:
    """Escape the three characters XML cannot carry literally in text content."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _plist_body(value, indent: int = 0) -> str:
    """One plist value, indented, with its type preserved.

    Type preservation is the whole job: ``<integer>40</integer>`` must not become
    ``<string>40</string>``, because the client would then hash a string where the
    server hashed an int and no validation would ever succeed. Booleans are tested
    **before** ints for that reason — ``True`` is an ``int`` in Python, and a
    ``<integer>1</integer>`` in place of ``<true/>`` is exactly the silent
    divergence this function exists to prevent.
    """
    pad = "  " * indent
    inner = "  " * (indent + 1)
    if value is True:
        return f"{pad}<true/>"
    if value is False:
        return f"{pad}<false/>"
    if isinstance(value, bool):                       # pragma: no cover - exhaustive
        return f"{pad}<true/>" if value else f"{pad}<false/>"
    if isinstance(value, int):
        return f"{pad}<integer>{value}</integer>"
    if isinstance(value, float):                      # pragma: no cover - banned upstream
        raise ck.ConfigKeyError("a float reached the plist writer")
    if isinstance(value, str):
        return f"{pad}<string>{_xml_escape(value)}</string>"
    if isinstance(value, (bytes, bytearray)):
        import base64
        return f"{pad}<data>{base64.b64encode(bytes(value)).decode('ascii')}</data>"
    if isinstance(value, datetime):
        return f"{pad}<date>{value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}</date>"
    if isinstance(value, dict):
        if not value:
            return f"{pad}<dict/>"
        rows = [f"{pad}<dict>"]
        for key in sorted(value, key=lambda k: (str(k).casefold(), str(k))):
            rows.append(f"{inner}<key>{_xml_escape(key)}</key>")
            rows.append(_plist_body(value[key], indent + 1))
        rows.append(f"{pad}</dict>")
        return "\n".join(rows)
    if isinstance(value, (list, tuple)):
        if not value:
            return f"{pad}<array/>"
        rows = [f"{pad}<array>"]
        for item in value:
            rows.append(_plist_body(item, indent + 1))
        rows.append(f"{pad}</array>")
        return "\n".join(rows)
    raise ck.ConfigKeyError(
        f"cannot write {type(value).__name__} into a plist: the client would not "
        "read back the value the Config Key was computed over")


PLIST_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
)


def plist_xml(settings: dict) -> bytes:
    """The plist XML for ``settings`` — the inner document of a ``.seb`` file."""
    body = _plist_body(settings)
    return (PLIST_HEADER + '<plist version="1.0">\n' + body + "\n</plist>\n").encode("utf-8")


def seb_file_bytes(settings: dict) -> bytes:
    """The ``.seb`` file: ``gzip("plnd" + gzip(plist XML))``.

    Straight from the official file-format page, which gives both layers: the
    plain XML is gzip-compressed and prefixed with ``plnd``, and the result is
    gzip-compressed again as the file itself ("an encrypted ``.seb`` file uses the
    gzip compression twice"). Keeping the documented shape matters beyond
    tidiness — SEB checks the four-byte prefix to decide how to read the payload,
    so a file without it is not a config file at all.
    """
    inner = gzip.compress(plist_xml(settings), mtime=0)
    return gzip.compress(PLAIN_PREFIX + inner, mtime=0)


def decode_seb(data: bytes) -> dict:
    """Read a ``.seb`` payload back into a settings map.

    Exists so the round trip can be *executed* rather than reasoned about: a test
    writes a file, reads it back, and recomputes the Config Key from the result.
    That is the only way to catch a type or a value lost in the plist writer,
    which would otherwise show up as every pupil in the school being refused.

    The inner layer is decompressed defensively. Our writer always gzips it (the
    documented procedure), but a payload that is already XML is accepted too,
    because this reader's job is to read what a **client** produced as well as what
    we did — and refusing a file a real SEB wrote would make the self-test lie.
    """
    if not data:
        raise ck.ConfigKeyError("empty .seb payload")
    try:
        body = gzip.decompress(data)
    except OSError as exc:
        raise ck.ConfigKeyError(f"the .seb payload is not gzip: {exc}") from exc
    if body[:4] != PLAIN_PREFIX:
        raise ck.ConfigKeyError(
            "this .seb payload is not plain ('plnd'); an encrypted one cannot be "
            "read without its password")
    inner = body[4:]
    try:
        xml = gzip.decompress(inner)
    except OSError:
        xml = inner
    return plistlib.loads(xml)


# ── the settings the server authors ─────────────────────────────────────────

#: Which of the exam's own switches owns which SEB setting.
#:
#: One table, and the only place the mapping exists. A switch the exam form posts
#: but this table does not name is a switch the ``.seb`` file does not carry, and
#: that is visible here at a glance rather than inferred from a page: the file is a
#: *browser lockdown*, so what it mirrors is exactly the escape hatches the teacher
#: chose to close — the app's own page-level anti-cheat (copy/paste, the watermark,
#: the calculator) is enforced by `take_exam.html` and is deliberately not duplicated
#: into a config the pupil can read.
#:
#: The direction is fixed and inverted in exactly one place: **the exam column says
#: block, the SEB key says allow.** `block_screenshot = true` means screenshots must
#: be refused, and SEB spells that `enablePrintScreen = false`. Two of these keys
#: spell it the same way round by accident of naming (`allowScreenSharing`), which is
#: why the rule is written down instead of being read off the names.
EXAM_OWNS = {
    "enableRightMouse": "block_right_click",
    "enablePrintScreen": "block_screenshot",
    "allowScreenSharing": "block_screenshot",
    "allowDisplayMirroring": "block_screenshot",
    "allowVideoCapture": "block_screenshot",
    "allowAirPlay": "block_screenshot",
}

#: A column the exam row does not carry means the capability stays **blocked**.
#:
#: This is the fail-closed direction on purpose. The rows this function is handed do
#: not all come from the same select — the panel, the download and the install test
#: read different column lists — and PostgREST reports a column left out of a select
#: as *absent* and never as an error. "We could not read the switch" must therefore
#: never read as "the teacher allowed it".
BLOCKED_WHEN_UNKNOWN = True


def _exam_values(exam) -> dict:
    """The SEB settings the exam's own switches decide, and nothing else.

    Every value is a ``bool`` **by construction** — ``not bool(...)`` cannot produce
    a float, and a float is the one type a keyed config may not carry (Windows and
    macOS serialise ``<real>`` differently, so one float would mean two Config Keys
    and break the "one key, every platform" promise). The type check still runs on
    the finished map in :func:`settings_for`: a construction argument is an argument,
    and this is the module that refuses rather than trusts.
    """
    row = exam if isinstance(exam, dict) else {}
    return {seb_key: not bool(row.get(column, BLOCKED_WHEN_UNKNOWN))
            for seb_key, column in EXAM_OWNS.items()}


def _template(start_url: str, quit_hash: str, admin_hash: str) -> dict:
    """The settings that are the same for every exam, before the switches apply.

    ``startURL`` is the exam page's absolute address, and it is not decoration: the
    pupil opens the file and SEB goes straight to the paper, with no address bar to
    type anything else into.
    """
    return {
        "originatorVersion": ORIGINATOR_VERSION,
        "startURL": str(start_url or ""),
        "quitURL": "",
        "sendBrowserExamKey": True,
        # The two secrets SEB compares against, as bare lower-case Base16 — see
        # app/services/seb_crypto.py for why that exact shape and not "SHA256:…".
        "hashedQuitPassword": quit_hash,
        "hashedAdminPassword": admin_hash,
        # Leaving the exam is possible but only through the password.
        "allowQuit": True,
        # A pupil whose wifi drops must be able to reload; the app is offline-first
        # and a page that cannot be refreshed is a lost sitting.
        "allowReload": True,
        "browserWindowAllowReload": True,
        "enableJavaScript": True,
        "enablePlugIns": True,
        # The keyboard and mouse shortcuts that leave the paper.
        "enableRightMouse": False,
        "enablePrintScreen": False,
        "enableAltTab": False,
        "enableAltEsc": False,
        "enableAltF4": False,
        "enableCtrlEsc": False,
        "enableEsc": False,
        "enableF1": False, "enableF2": False, "enableF3": False, "enableF4": False,
        "enableF5": False, "enableF6": False, "enableF7": False, "enableF8": False,
        "enableF9": False, "enableF10": False, "enableF11": False, "enableF12": False,
        # Capturing or mirroring the paper, which is also what the weak
        # environment signals look for from the browser side.
        "allowScreenSharing": False,
        "allowDisplayMirroring": False,
        "allowVideoCapture": False,
        "allowAirPlay": False,
        "allowSiri": False,
        "allowDictation": False,
        # A virtual machine is where "another computer" cheating happens, and
        # refusing it here is the *hard* half of the environment story (the
        # browser-side signals in app/services/environment_signals.py are the soft,
        # non-punitive half).
        "allowVirtualMachine": False,
        "allowFlashFullscreen": False,
        "blockPopUpWindows": True,
        "allowPreferencesWindow": False,
        # Taskbar and clock: the paper is full-screen, the clock stays.
        "showTaskBar": False,
        "showReloadButton": True,
        "showTime": True,
        "taskBarHeight": 40,
        # See the module docstring: the filter's action codes are unverifiable, and
        # a wrong one blocks the paper.
        "URLFilterEnable": False,
    }


def settings_for(exam, *, start_url: str, quit_hash: str, admin_hash: str) -> dict:
    """The generated config for one exam — **from the exam's own settings**.

    The exam is the first, required argument rather than an optional extra, so a
    caller cannot generate a file that quietly ignores the teacher's switches: an
    exam passed as ``None`` is the install test (no switches to honour), and that is
    a decision the call site has to write down.

    Two properties are the contract, and both are guarded by
    ``tests/unit/test_seb_generator.py``:

    * **one Config Key for every platform.** Nothing here takes a platform, a build
      or a version, and nothing writes a Browser Exam Key — the BEK is a hash over
      the SEB *binary's* signature, so it differs per platform and can never be
      computed server-side. What this produces is the Config Key, which the official
      documentation states is "same in each platform version of SEB";
    * **no fractional value, anywhere.** The finished map is handed to
      :func:`app.services.seb_config_key.check_types`, so a float is refused here —
      naming the setting — instead of surviving until the file refuses to open.
    """
    settings = _template(start_url, quit_hash, admin_hash)
    settings.update(_exam_values(exam))
    ck.check_types(settings)
    return settings


def current_key(exam, credential: dict | None) -> str | None:
    """The Config Key this exam's **current** settings generate, or ``None``.

    Exists because the file is now generated from the exam row, and the row can be
    edited after the file was issued. Three callers must agree about that: the
    download (which refuses a file the door would reject), the panel (which says so
    on screen) and the JavaScript API path (which verifies a client against the
    stored key). One function, so "has this config drifted?" has one answer.
    """
    if not credential:
        return None
    exam_id = str((exam or {}).get("id") or "")
    if not exam_id:
        # Without an id there is no ``startURL`` to hash, and inventing a URL here
        # would report drift for every exam instead of for the one that changed.
        return None
    return ck.config_key(settings_for(
        exam,
        start_url=start_url(exam_id),
        quit_hash=credential.get("quit_password_hash") or "",
        admin_hash=credential.get("admin_password_hash") or ""))


def file_is_current(exam, credential: dict | None) -> bool:
    """Whether the exam's settings still generate the key the file was built with.

    ``False`` means a new download would not open this paper, so serving it would
    hand a pupil a file that cannot start. What is *not* claimed here: a file already
    distributed keeps working until it is re-issued, because the stored key is still
    the one it carries — the correct teaching is "re-issue and redistribute", not
    "every pupil is locked out". The panel says exactly that.
    """
    stored = (exam or {}).get("seb_config_key")
    if not stored:
        return False
    return current_key(exam, credential) == stored


# ── minting and storing ─────────────────────────────────────────────────────

def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a failed read is an empty read."""
    try:
        return query.execute().data or []
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("seb_service: read failed: %s", exc)
        return []


def credentials_for(supabase, exam_id: str) -> dict | None:
    """The credential row for an exam, or ``None``.

    Returns the row **including** the encrypted columns. Callers must never hand
    this to a template: the page gets the result of :func:`reveal`, and only on a
    request that :mod:`app.services.seb_access` has already allowed and logged.
    """
    rows = _rows(supabase.table("exam_seb_credential").select("*").eq("exam_id", exam_id))
    return rows[0] if rows else None


def issue(supabase, exam: dict, *, start_url: str, actor_id: str | None = None) -> dict:
    """Mint this exam's SEB credentials and its Config Key. Idempotent in effect.

    Called when a teacher turns ``require_seb`` on — and again whenever the exam's
    settings change or a teacher asks for the file again, because a stale Config
    Key is a paper nobody can open. Re-issuing **replaces** the passwords: leaving
    the old ones alive would mean two passwords work and the audit log cannot say
    which was read, and the file carries only the new hash anyway.

    Returns the settings map and the Config Key so the caller can stream the file
    without a second read, plus the plaintext passwords **for the caller's own
    immediate use only** — they are stored encrypted, never returned by a route
    that a page renders.
    """
    quit_password = seb_crypto.mint_password()
    admin_password = seb_crypto.mint_password()
    settings = settings_for(exam, start_url=start_url,
                            quit_hash=seb_crypto.sha256_hex(quit_password),
                            admin_hash=seb_crypto.sha256_hex(admin_password))
    key = ck.config_key(settings)

    payload = {
        "exam_id": exam.get("id"),
        "school_id": exam.get("school_id"),
        "quit_password_hash": settings["hashedQuitPassword"],
        "quit_password_enc": seb_crypto.encrypt(quit_password),
        "admin_password_hash": settings["hashedAdminPassword"],
        "admin_password_enc": seb_crypto.encrypt(admin_password),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if actor_id:
        payload["created_by"] = actor_id

    written = _rows(supabase.table("exam_seb_credential")
                    .upsert(payload, on_conflict="exam_id"))
    if not written:
        logger.error("seb_service: could not store credentials for exam %s", exam.get("id"))
        return {"ok": False, "reason": "write_failed"}

    # The Config Key lives on the exam because it belongs to the exam (one value for
    # the whole class). See migration 062 for why it is not duplicated here.
    updated = _rows(supabase.table("exams").update({"seb_config_key": key})
                    .eq("id", exam.get("id")))
    if not updated:
        logger.error("seb_service: could not store the Config Key for exam %s", exam.get("id"))
        return {"ok": False, "reason": "write_failed"}

    exam["seb_config_key"] = key
    return {
        "ok": True, "reason": "",
        "config_key": key,
        "settings": settings,
        "quit_password": quit_password,
        "admin_password": admin_password,
    }


def reveal(credential: dict, kind: str) -> str:
    """The plaintext of one stored password, or raise.

    One place, so a route cannot invent a column name and end up reading the hash
    where it meant the ciphertext. An unknown ``kind`` is refused rather than
    defaulting, because defaulting is how a caller asking for the admin password
    would silently be handed the quit password.
    """
    if kind not in KINDS:
        raise seb_crypto.SebCryptoError(f"unknown SEB password kind: {kind!r}")
    column = "quit_password_enc" if kind == KIND_QUIT else "admin_password_enc"
    return seb_crypto.decrypt((credential or {}).get(column) or "")


# ── the door ────────────────────────────────────────────────────────────────

#: The route the config starts a client on. Named here rather than in the file
#: that writes the config, because it is the *door* that must agree with it: the
#: hash a client sends is over "the absolute URL it is on", so a door hashing a
#: different string than the one the file carried refuses every honest client.
def start_url(exam_id: str) -> str:
    """The absolute exam URL that goes into the config, and into the hash.

    Built from ``request.url_root`` and not from a configured base URL, the same
    choice ``_share_card`` makes in ``app/routes/teacher.py``: the address the file
    sends a pupil to must be the address this server actually answers on. No query
    string, deliberately — it is a *starting* address, and a parameter that changed
    between the download and the sitting would change the hash the door checks.
    """
    from flask import request, url_for
    return request.url_root.rstrip("/") + url_for("student.take_exam", exam_id=exam_id)


# ── the JavaScript API: the same key, on a client that cannot send it ────────
#
# SEB for macOS/iOS 3.0+ runs on WKWebView, which **cannot** put the Config Key in
# an HTTP header at all — the project's developer documentation says so outright and
# offers the JavaScript API instead. Without this path the feature would silently
# refuse every iPad and every modern macOS client, which is the worst shape a
# failure can take here: the school blames SEB, and the paper is unopenable.
#
# The value is the *same* value. The documentation is explicit that
# `SafeExamBrowser.security.configKey` is "the CK hashed with the URL of the page"
# and that "the keys are identical to the ones send in the HTTP request header". So
# this is not a second verification to keep in step with the first — it is
# :func:`seb_config_key.header_matches` reached by another transport, and there is
# deliberately no second comparison function that could drift from it.
#
# The one thing that is genuinely different: the value is bound to **the URL of the
# page the script ran on**, so the page and the verifier must agree about that
# string exactly like the file and the door agree about `start_url`.
# :func:`claim_url` is that one definition.

#: How long a claim stays good. Short on purpose: a claim is a *page-load* proof,
#: and the page that makes one can always make another, so nothing is gained by
#: letting it outlive the visit — while a long window would leave a bypass a pupil
#: could re-use an hour later without the client ever proving itself again.
JS_CLAIM_TTL = 300

#: Where the claim is kept. The session cookie, which is signed with the app's
#: secret: a pupil cannot forge one, and it travels with the account it belongs to.
#: What is stored is **not** the key or the hash — only that this pupil proved
#: itself, when, and how — because a cookie is the one place a derived secret should
#: never be parked.
JS_CLAIM_SESSION_KEY = "seb_js_claim"

#: Recorded with the claim so a report can say which transport was used. A school
#: asking "how many of my pupils are on iPads" has no other way to find out, and
#: "the header" versus "the JavaScript API" is the whole answer.
JS_CLAIM_METHOD = "js_api"


def claim_url(exam_id: str) -> str:
    """The absolute URL of the claim page — **and** the string the client hashed.

    The JavaScript API's value covers the page it ran on, so this is not a
    convenience: the page the pupil loads and the verifier that judges the claim
    must compute the same address, or every honest client is refused. One function,
    the same discipline `start_url` follows for the file.
    """
    from flask import request, url_for
    return request.url_root.rstrip("/") + url_for("seb.js_claim", exam_id=exam_id)


def claim_valid(claim, exam_id: str, now=None) -> bool:
    """Whether a stored claim still proves this sitting, for this exam.

    Four ways to be wrong, all of them refused: a claim that is not a mapping, one
    made for *another* exam (a pupil holding the claim from the paper next door),
    one with no timestamp or an unparseable one, and an expired one. The elapsed-time
    arithmetic lives here rather than at the call site so the door and the page
    cannot disagree about what "fresh" means — and the timestamp is read with
    :func:`app.utils.exam_window.parse_dt`, the app's one ISO reader, because the
    session round-trips it as a string and a second parser is a second set of bugs.
    """
    if not isinstance(claim, dict):
        return False
    if str(claim.get("exam_id") or "") != str(exam_id or ""):
        return False
    if claim.get("method") != JS_CLAIM_METHOD:
        return False
    at = exam_window.parse_dt(claim.get("at"))
    if at is None:
        return False
    moment = now or datetime.now(timezone.utc)
    age = (moment - at).total_seconds()
    # A claim dated in the future is not fresher than one from now: it means a clock
    # this app does not control was trusted somewhere, so it is refused rather than
    # waved through. Sixty seconds of slack is for the ordinary skew between two
    # machines, not for a pupil with a generous idea of the time.
    return -60 <= age <= JS_CLAIM_TTL


def gated(exam: dict) -> bool:
    """Whether this paper is behind SEB at all."""
    return bool((exam or {}).get("require_seb"))


def verify(exam: dict, request_url: str, header_value: str | None) -> bool:
    """Whether a request may open this paper.

    Three answers, and the middle one is the design:

    * ``require_seb`` is off → **yes**. An exam nobody gated must be untouched by
      this feature; that is the regression every other test here leans on.
    * gated, and the header matches the URL and the stored key → **yes**.
    * anything else → **no**, including the case where the exam is gated but no key
      was ever issued. That last one **fails closed on purpose**: failing open
      would mean a toggle that silently protects nobody, which is worse than an
      exam that refuses and says so. The state cannot be reached by the normal
      route — ``seb.enable`` writes ``require_seb`` and the key in the *same*
      update, and ``seb.reissue`` mints both — so it means somebody edited the row
      by hand or an issue failed halfway. Failing closed turns that into a paper
      nobody can open, which a school reports the same morning, instead of a
      requirement that is quietly enforcing nothing.
    """
    if not gated(exam):
        return True
    key = (exam or {}).get("seb_config_key")
    return ck.header_matches(request_url, header_value, key)


# ── the save paths: the same key, read without ever deciding ─────────────────
#
# `docs/features/SEB_PHASE11.md` Langkah 5 asked one question of a real client: does
# the Config Key header ride on an **XHR** (the draft sync and the submit POST), or
# only on page navigations? On 9 October 2026 a real Windows SEB 3.10.2 answered it.
# The probe page's own `fetch()` to `/probe-xhr` arrived carrying
# `X-SafeExamBrowser-ConfigKeyHash` = SHA256(of *its own* URL + the key we computed),
# judged per URL, `--report` exit **0** — so a save path really does receive a header
# to read, and a sitting that has been moved into an ordinary browser is visible
# there.
#
# What this is NOT: a second door. Nothing here refuses, and the reason is the iPad.
# SEB for macOS/iOS runs on WKWebView, which cannot attach the Config Key to **any**
# request; a save path that refused on a missing header would end a sitting the pupil
# is allowed to finish, and would do it to every iPad pupil mid-paper with their
# answers already in the row. Refusal stays where it belongs — at the page door
# (`verify`), which hands a client that cannot prove itself to the JavaScript
# handshake instead of turning it away.
#
# So the save paths **observe**: the state is classified here, in the one module that
# owns every SEB rule, and the routes record it. A school asking "did our gated
# papers really arrive from SEB" gets a measurement instead of an assumption, and no
# pupil loses a paper to a header their client cannot send.

#: The three answers a save-path request can give. There is deliberately no fourth
#: one meaning "refuse" — that is :func:`verify`, and it is a different question.
KEY_MATCH = "match"
KEY_MISMATCH = "mismatch"
KEY_ABSENT = "absent"


def key_state(exam: dict, request_url: str, header_value: str | None) -> str | None:
    """What this request's Config Key header shows — ``None`` when nothing is gated.

    The URL is the one the **client** had, and that is a measured fact rather than a
    preference: a client hashes the address it is fetching (the probe's favicon
    request carried the hash of the favicon, and its XHR the hash of the XHR), so a
    save is judged against ``request.url`` and deliberately *not* against
    :func:`start_url` — the door hashes the config's address, which is a different
    request entirely.

    An exam with ``require_seb`` off has nothing to observe and answers ``None``:
    that is what keeps this free on the overwhelming majority of papers.
    """
    if not gated(exam):
        return None
    if not header_value:
        return KEY_ABSENT
    key = (exam or {}).get("seb_config_key")
    return KEY_MATCH if ck.header_matches(request_url, header_value, key) else KEY_MISMATCH


def observe_save_key(exam: dict, exam_id: str, request_url: str,
                     header_value: str | None, where: str) -> str | None:
    """Record what a save-path request showed, and return the state. Never refuses.

    One function for both paths, so the sync route and the submit route cannot
    describe the same event two ways — and so a reader looking for "where is the
    Config Key checked on a save" finds one answer instead of two.

    ``where`` names the path in the line, because "which request was this" is the
    first thing the reader asks. Returns the state so a caller (or a test) can
    assert on it without parsing a log.
    """
    state = key_state(exam, request_url, header_value)
    if state is not None:
        logger.info("SEB header on XHR: %s (%s, exam %s)", state, where, exam_id)
    return state


__all__ = [
    "BLOCKED_WHEN_UNKNOWN", "EXAM_OWNS", "KEY_ABSENT", "KEY_MATCH",
    "KEY_MISMATCH", "KIND_ADMIN", "KIND_QUIT", "KINDS",
    "ORIGINATOR_VERSION", "PLAIN_PREFIX", "credentials_for", "current_key",
    "decode_seb", "file_is_current", "gated", "issue", "key_state",
    "observe_save_key", "plist_xml", "reveal",
    "seb_file_bytes", "settings_for", "start_url", "verify",
]
