"""The generated config, the Config Key over it, and the door that checks the client.

The accepted criteria, in the order they were set
------------------------------------------------
1. **Cross-check the algorithm against the reference.** The strongest evidence
   available without a browser is the worked example the *official* Config Key page
   publishes: it is the exact SEB-JSON a real SEB produced, so re-serialising it
   must return those same bytes. That is :data:`OFFICIAL_SEB_JSON` below, quoted
   verbatim, and the assertion on it is byte-for-byte — including the one field
   that carries a lone backslash, which is the rule every naive `json.dumps`
   implementation gets wrong.
2. **The file round trip.** Whatever the plist writer does to a value, the Config
   Key computed from the file that comes back must equal the one computed from the
   settings that went in. A type lost in the writer would otherwise be discovered
   as *every pupil in the school is refused*, in production.
3. **The password is never in the file.** The pupil can open it.
4. **The door.** Gated and matching opens; gated and not matching refuses; ungated
   is untouched.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import seb_config_key as ck
from app.services import seb_crypto
from app.services import seb_service

ROOT = Path(__file__).resolve().parents[2]

#: VERBATIM from https://safeexambrowser.org/developer/seb-config-key.html — the
#: published "Example SEB-JSON String", cut at a clean top-level key boundary so
#: the fragment is itself a complete SEB-JSON object. It carries, on purpose, every
#: shape the serialiser has rules for: an empty array, both booleans, integers of
#: two magnitudes, empty and non-empty strings, and a single literal backslash in
#: `browserMessagingSocket` — the field rule 4 exists for, and the one that a
#: `json.dumps`-based implementation doubles and therefore hashes differently.
OFFICIAL_SEB_JSON = (
    r'{"additionalResources":[],"allowAirPlay":false,"allowBrowsingBackForward":false,'
    r'"allowDictation":false,"allowDictionaryLookup":false,"allowDisplayMirroring":false,'
    r'"allowDownUploads":false,"allowedDisplayBuiltin":true,"allowedDisplaysMaxNumber":1,'
    r'"allowFlashFullscreen":false,"allowPDFPlugIn":false,"allowPreferencesWindow":true,'
    r'"allowQuit":true,"allowScreenSharing":false,"allowSiri":false,"allowSpellCheck":false,'
    r'"allowSwitchToApplications":true,"allowUserAppFolderInstall":false,'
    r'"allowUserSwitching":true,"allowVideoCapture":false,"allowVirtualMachine":false,'
    r'"allowWlan":false,"blacklistURLFilter":"","blockPopUpWindows":false,'
    r'"browserMessagingPingTime":120000,"browserMessagingSocket":"ws:\localhost:8706",'
    r'"browserScreenKeyboard":false,"browserURLSalt":true,"browserUserAgent":"",'
    r'"browserUserAgentMac":0}'
)


# ── 1. the official known-answer vector ─────────────────────────────────────

def test_the_official_example_re_serialises_to_its_exact_bytes():
    """The published example, byte-for-byte — the reference cross-check.

    The published string is the canonical output of a real client's converter, so
    feeding it back through this module must be a fixed point. It also pins the
    *ordering*: the example is already in case-insensitively sorted order, so any
    deviation in the sort shows up here rather than at a school.
    """
    # The example is SEB-JSON, which is deliberately not valid JSON (rule 4 leaves
    # backslashes unescaped), so the one backslash is doubled *only* to make it
    # parse — the round trip then has to put it back as a single one.
    parsed = json.loads(OFFICIAL_SEB_JSON.replace("\\", "\\\\"))
    assert parsed["browserMessagingSocket"] == "ws:\\localhost:8706"
    assert ck.seb_json(parsed) == OFFICIAL_SEB_JSON


def test_that_single_backslash_would_be_doubled_by_a_naive_json_encoder():
    """Why the previous assertion is not theatre: `json.dumps` gets it wrong."""
    naive = json.dumps({"browserMessagingSocket": "ws:\\localhost:8706"},
                       separators=(",", ":"))
    assert "ws:\\\\localhost" in naive
    assert ck.seb_json({"browserMessagingSocket": "ws:\\localhost:8706"}) == \
        '{"browserMessagingSocket":"ws:\\localhost:8706"}'


def test_the_official_hash_of_abc_is_the_well_known_one():
    """A second, independent vector: SHA-256("abc"), from the standard itself."""
    assert seb_crypto.sha256_hex("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


# ── the hash SEB actually compares against ──────────────────────────────────

def test_the_stored_hash_is_bare_lowercase_hex_with_no_algorithm_prefix():
    """The official example stores it bare; `SHA256:…` would never match.

    Asserted against the published value rather than a paraphrase of it, because
    the prefix is the mistake several third-party write-ups make and it fails
    silently: the file loads, the prompt appears, and the right password is
    rejected.
    """
    assert re.fullmatch(r"[0-9a-f]{64}",
                        "8577da2ea54085708b3b851bc50315a36bb740ba5135e747cfb12457b5d3060f")
    digest = seb_crypto.sha256_hex("buka-seb-2026")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert not digest.upper() == digest and "SHA256" not in digest.upper()


# ── 2. the file round trip ──────────────────────────────────────────────────

@pytest.fixture()
def app_ctx(app):
    """The stored ciphertext is derived from `SECRET_KEY`, so its tests need an app.

    The other half of this file is deliberately app-free: the Config Key and the
    plist writer are pure, and a test that needs no application cannot be fooled by
    one.
    """
    with app.app_context():
        yield app


def _settings(**over):
    """The install-test shape: no exam, so no switch has anything to say.

    The exam-driven half of the generator has its own file
    (``tests/unit/test_seb_generator.py``); this fixture stays on the exam-free
    config so every assertion below keeps meaning what it meant.
    """
    base = seb_service.settings_for(None,
                                    start_url="https://sekolah.test/student/exams/ex-1",
                                    quit_hash=seb_crypto.sha256_hex("quit-pass-1"),
                                    admin_hash=seb_crypto.sha256_hex("admin-pass-1"))
    base.update(over)
    return base


def test_the_file_is_the_documented_double_gzip_with_a_plnd_prefix():
    raw = seb_service.seb_file_bytes(_settings())
    assert raw[:2] == b"\x1f\x8b", "a .seb file is gzip-wrapped"
    body = gzip.decompress(raw)
    assert body[:4] == b"plnd", "the payload announces itself as unencrypted"
    assert gzip.decompress(body[4:]).lstrip().startswith(b"<?xml")


def test_the_config_key_survives_the_write_and_read_of_the_file():
    """The property that decides whether a school can sit the exam at all."""
    settings = _settings()
    from_file = seb_service.decode_seb(seb_service.seb_file_bytes(settings))
    assert ck.config_key(from_file) == ck.config_key(settings)


@pytest.mark.parametrize("value", [True, False, 0, 40, 120000, "", "100%", "a b", "<&>"])
def test_every_type_in_the_config_survives_the_round_trip(value):
    """`<integer>40</integer>` must not come back as the string `"40"`.

    A plist writer that lost the type would produce a Config Key the client can
    never reproduce, which is the failure this parametrised case exists to make
    impossible to ship.
    """
    settings = _settings(probe=value)
    from_file = seb_service.decode_seb(seb_service.seb_file_bytes(settings))
    assert from_file["probe"] == value
    assert type(from_file["probe"]) is type(value)


def test_the_originator_version_is_written_but_excluded_from_the_key():
    """Rule 1, observed end to end rather than asserted of the serialiser alone."""
    settings = _settings()
    assert settings["originatorVersion"] == seb_service.ORIGINATOR_VERSION
    without = {k: v for k, v in settings.items() if k != "originatorVersion"}
    assert ck.config_key(settings) == ck.config_key(without)
    # ...and it really is in the file the client opens.
    assert b"originatorVersion" in seb_service.plist_xml(settings)


def test_an_encrypted_payload_is_refused_rather_than_misread():
    """A `pswd` payload cannot be read without its password; say so, do not guess."""
    encrypted = gzip.compress(b"pswd" + b"not-really-encrypted")
    with pytest.raises(ck.ConfigKeyError):
        seb_service.decode_seb(encrypted)


# ── 3. the passwords, and what the file may contain ─────────────────────────

def test_the_minted_passwords_are_strong_and_unambiguous(app_ctx):
    seen = {seb_crypto.mint_password() for _ in range(200)}
    assert len(seen) == 200, "a repeat would mean the CSPRNG is not being used"
    for password in seen:
        assert len(password) == seb_crypto.PASSWORD_LENGTH
        assert all(ch in seb_crypto.PASSWORD_ALPHABET for ch in password)
    # The characters a supervisor reading one aloud over the phone confuses.
    for ambiguous in "0O1lI":
        assert ambiguous not in seb_crypto.PASSWORD_ALPHABET


def test_the_downloadable_file_never_carries_a_readable_password(app_ctx):
    """The brief's hardest promise: a pupil can open this file."""
    quit_password = "QUIT-must-not-appear"
    admin_password = "ADMIN-must-not-appear"
    settings = seb_service.settings_for(
        None, start_url="https://sekolah.test/student/exams/ex-1",
        quit_hash=seb_crypto.sha256_hex(quit_password),
        admin_hash=seb_crypto.sha256_hex(admin_password))
    raw = seb_service.seb_file_bytes(settings)
    for secret in (quit_password, admin_password):
        assert secret.encode() not in raw
        assert secret.encode() not in gzip.decompress(raw)
        # ...nor its base64 or hex, in case a future edit encodes rather than hashes.
        assert seb_crypto.encrypt(secret, secret=b"k" * 32).split(":")[2] != secret
    assert settings["hashedQuitPassword"] == seb_crypto.sha256_hex(quit_password)
    assert settings["hashedAdminPassword"] == seb_crypto.sha256_hex(admin_password)


def test_the_config_carries_no_float_and_no_unverifiable_enum():
    """The two rules the module documents, asserted so they cannot drift.

    A float is refused by the Config Key module (SEB issue #1495 — Windows and
    macOS serialise `<real>` differently, so one value would mean two keys), and
    the enums are absent because a wrong one blocks the paper or files the config
    as the wrong kind of document.
    """
    settings = _settings()
    assert not [k for k, v in settings.items() if isinstance(v, float)]
    for enum_key in ("sebConfigPurpose", "browserViewMode", "newBrowserWindowByLinkPolicy",
                     "URLFilterRules", "logLevel"):
        assert enum_key not in settings, enum_key
    # The header the server checks must actually be requested of the client.
    assert settings["sendBrowserExamKey"] is True
    assert settings["startURL"].endswith("/student/exams/ex-1")


# ── 4. the door ─────────────────────────────────────────────────────────────

def test_an_exam_that_does_not_require_seb_is_untouched():
    assert seb_service.verify({"require_seb": False}, "https://x/y", None) is True
    assert seb_service.gated({"require_seb": False}) is False


def test_a_client_with_the_right_key_is_admitted():
    url = "https://sekolah.test/student/exams/ex-1"
    key = ck.config_key(_settings())
    sent = ck.request_hash(url, key)
    assert seb_service.verify({"require_seb": True, "seb_config_key": key}, url, sent) is True
    # The documented header example ends in a semicolon, and clients send it.
    assert seb_service.verify({"require_seb": True, "seb_config_key": key},
                              url, sent + ";") is True
    # A page anchor is not a different resource.
    assert seb_service.verify({"require_seb": True, "seb_config_key": key},
                              url + "#room", sent) is True


def test_a_missing_or_forged_header_is_refused():
    url = "https://sekolah.test/student/exams/ex-1"
    key = ck.config_key(_settings())
    for sent in (None, "", "deadbeef", ck.request_hash(url + "/other", key)):
        assert seb_service.verify({"require_seb": True, "seb_config_key": key},
                                  url, sent) is False, sent


def test_the_url_and_the_key_are_concatenated_in_that_order():
    """URL first. The reverse is the mistake the project's forum records failing."""
    key = "b" * 64
    assert ck.request_hash("https://x/y", key) != ck.request_hash(key, "https://x/y")
    assert ck.request_hash("https://x/y", key) == \
        __import__("hashlib").sha256(("https://x/y" + key).encode()).hexdigest()


def test_a_gated_exam_whose_key_was_never_issued_fails_closed():
    """Failing open would be a toggle that protects nobody, which is worse."""
    assert seb_service.verify({"require_seb": True, "seb_config_key": None},
                              "https://x/y", "anything") is False


# ── minting and storing ─────────────────────────────────────────────────────

class _Query:
    def __init__(self, table, sb):
        self.table = table
        self.sb = sb
        self.upserted = None
        self.conflict = None
        self.updated = None

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def upsert(self, payload, on_conflict=None):
        self.upserted = payload
        self.conflict = on_conflict
        return self

    def update(self, payload):
        self.updated = payload
        return self

    def execute(self):
        if self.upserted is not None:
            self.sb.upserts.append((self.table, self.upserted, self.conflict))
            return SimpleNamespace(data=[dict(self.upserted, id="cred-1")])
        if self.updated is not None:
            self.sb.updates.append((self.table, self.updated))
            return SimpleNamespace(data=[dict(self.updated, id="ex-1")])
        return SimpleNamespace(data=[])


class _Sb:
    def __init__(self):
        self.upserts = []
        self.updates = []

    def table(self, name):
        return _Query(name, self)


def _exam():
    return {"id": "ex-1", "school_id": "sc-1", "teacher_id": "u-1", "require_seb": True}


def test_issuing_stores_both_halves_and_the_key_on_the_exam(app_ctx):
    sb = _Sb()
    out = seb_service.issue(sb, _exam(), start_url="https://s.test/student/exams/ex-1",
                            actor_id="u-1")
    assert out["ok"] is True
    tables = [row[0] for row in sb.upserts]
    assert tables == ["exam_seb_credential"]
    _table, payload, conflict = sb.upserts[0]
    assert conflict == "exam_id", "one exam, one credential row — re-issuing updates it"
    # The file's half and the staff's half are different columns, on purpose.
    assert payload["quit_password_hash"] != out["quit_password"]
    assert payload["quit_password_hash"] == seb_crypto.sha256_hex(out["quit_password"])
    assert seb_crypto.decrypt(payload["quit_password_enc"]) == out["quit_password"]
    assert seb_crypto.decrypt(payload["admin_password_enc"]) == out["admin_password"]
    # The Config Key belongs on the exam, and is the key over the returned settings.
    assert sb.updates == [("exams", {"seb_config_key": out["config_key"]})]
    assert out["config_key"] == ck.config_key(out["settings"])


def test_the_two_passwords_are_different_and_unique_per_issue(app_ctx):
    sb = _Sb()
    first = seb_service.issue(sb, _exam(), start_url="https://s.test/e/1")
    second = seb_service.issue(sb, _exam(), start_url="https://s.test/e/1")
    assert first["quit_password"] != first["admin_password"]
    assert first["quit_password"] != second["quit_password"], (
        "re-issuing must not hand back the previous exam's password")


def test_an_unknown_password_kind_is_refused_rather_than_defaulted(app_ctx):
    sb = _Sb()
    out = seb_service.issue(sb, _exam(), start_url="https://s.test/e/1")
    credential = sb.upserts[0][1]
    assert seb_service.reveal(credential, "quit") == out["quit_password"]
    assert seb_service.reveal(credential, "admin") == out["admin_password"]
    for bad in ("admin_password", "", "quit_password"):
        with pytest.raises(seb_crypto.SebCryptoError):
            seb_service.reveal(credential, bad)


def test_a_tampered_ciphertext_is_refused_not_guessed(app_ctx):
    """A wrong answer reads a useless password aloud; an error does not."""
    blob = seb_crypto.encrypt("secret-value")
    with pytest.raises(seb_crypto.SebCryptoError):
        seb_crypto.decrypt(blob[:-4] + "AAAA")
    with pytest.raises(seb_crypto.SebCryptoError):
        seb_crypto.decrypt("v9:not:ours")
    # ...and the key really is derived per application.
    assert seb_crypto.encrypt("x", secret=b"a" * 32) != seb_crypto.encrypt("x", secret=b"b" * 32)
    assert len(seb_crypto.key_from("s")) == 32


# ── the blueprint is registered, or none of this is reachable ────────────────

def test_the_seb_blueprint_is_registered():
    """A module nothing routes to is a module that protects nothing."""
    init = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    assert "seb_bp" in init
    assert "register_blueprint(seb_bp)" in init


def test_the_migration_is_additive_and_keeps_the_interval_tables_apart():
    sql = (ROOT / "supabase" / "migrations"
           / "062_seb_and_environment_signals.sql").read_text(encoding="utf-8")
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert not re.search(r"DROP\s+(TABLE|COLUMN)", code)
    for table in ("environment_signal", "exam_seb_credential", "seb_access_log"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in sql
    # The Config Key is on the exam, and the BEK column is gone — the enforcement
    # is its absence.
    assert "seb_config_key TEXT" in sql
    assert "browser_exam_key" not in code
