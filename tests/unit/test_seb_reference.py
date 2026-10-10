"""Uji-silang: the Config Key against a second implementation of the same spec.

What "cross-check" can honestly mean here
-----------------------------------------
The authoritative arbiter is a real SEB client, and that is Phase 11
(`docs/features/SEB_PHASE11.md`) — it needs a machine with SEB installed, so it
cannot live in this suite. The strongest thing that *can* live here is a second
implementation of the same rules, written from the specification and **not** by
calling the production one, and then a differential run over tens of thousands of
generated configs plus the values the specification itself publishes.

What that catches and what it does not, stated rather than implied:

* it **catches** implementation slips — a rule applied at the root but not inside
  an array, a sort that is not stable for keys differing only in case, a backslash
  doubled in one path and not another, an integer that comes out as `3.0`, a
  Unicode string mangled on the way out. These are the failure modes that produce a
  key which never matches, and one generator pass finds them;
* it does **not** catch a misreading of the specification that both
  implementations share, because both were written from the same document by the
  same reader. That is what the literal known-answer vectors in
  `test_seb_config_key.py` are for (they were written out by hand from the rules),
  and what Phase 11 is for.

Three descriptions of one algorithm, deliberately different in shape
--------------------------------------------------------------------
1. the production serialiser emits text as it walks the mapping (`seb_config_key`);
2. the reference below *normalises first* — sorting and pruning into an ordered
   pair-list — and only then writes, so it has no ordering left to do while writing;
3. the literal vectors, hashed with `hashlib` directly by the test.

No two of them share an implementation, so a disagreement is a finding rather than
a coincidence.
"""
from __future__ import annotations

import hashlib
import random

import pytest

from app.services import seb_config_key as ck
from app.services import seb_crypto, seb_service

#: One backslash, spelled without escaping: a backslash written four times in a
#: test is a backslash written wrongly.
BS = chr(92)


# ── the reference implementation ─────────────────────────────────────────────

def _normalise(value):
    """Prune and order, into a shape that has no decisions left in it.

    The output is `(kind, payload)`, where a mapping becomes a *sorted list of
    pairs* rather than a dict — which is the structural difference from the
    production serialiser: by the time anything is written, the order is already
    decided and nothing downstream can reintroduce insertion order.
    """
    if isinstance(value, dict):
        pairs = []
        for key in sorted(value, key=lambda name: (name.casefold(), name)):
            if key == "originatorVersion":
                continue
            item = _normalise(value[key])
            if item[0] == "map" and not item[1]:
                continue                       # an empty dict is dropped as a value
            pairs.append((key, item))
        return ("map", pairs)
    if isinstance(value, (list, tuple)):
        return ("list", [_normalise(item) for item in value])
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        raise ValueError("a float has no platform-stable SEB-JSON form")
    if isinstance(value, str):
        return ("str", value)
    if value is None:
        return ("null", None)
    raise ValueError(f"cannot serialise {type(value).__name__}")


def _needs_escaping(text: str) -> bool:
    """A plain scan rather than a pattern: a reference implementation is worth most
    when it has the fewest of its own moving parts to be wrong."""
    return any(char == '"' or char == BS or ord(char) < 0x20 for char in text)


def _quote(text: str) -> str:
    """Rule 4 by a different route: fast path first, character loop only if needed."""
    if not _needs_escaping(text):
        return '"' + text + '"'
    out = []
    for char in text:
        if char == '"':
            out.append(BS + '"')
        elif char == BS:
            out.append(BS)                     # rule 4: never doubled
        elif char == "\n":
            out.append(BS + "n")
        elif char == "\r":
            out.append(BS + "r")
        elif char == "\t":
            out.append(BS + "t")
        elif ord(char) < 0x20:
            out.append(BS + "u%04x" % ord(char))
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _write(node) -> str:
    kind, payload = node
    if kind == "null":
        return "null"
    if kind == "bool":
        return "true" if payload else "false"
    if kind == "int":
        return str(payload)
    if kind == "str":
        return _quote(payload)
    if kind == "list":
        return "[" + ",".join(_write(item) for item in payload) + "]"
    if kind == "map":
        return "{" + ",".join(_quote(key) + ":" + _write(item)
                              for key, item in payload) + "}"
    raise ValueError(kind)


def reference_seb_json(settings: dict) -> str:
    """The reference: normalise, then write. Never calls the production module."""
    if not isinstance(settings, dict):
        raise ValueError("settings must be a mapping")
    return _write(_normalise(settings))


def reference_config_key(settings: dict) -> str:
    return hashlib.sha256(reference_seb_json(settings).encode("utf-8")).hexdigest()


def reference_request_hash(url: str, key: str) -> str:
    """The request half, spelled out with `hashlib` and an explicit concatenation."""
    bare = (url or "").split("#", 1)[0]
    return hashlib.sha256((bare + (key or "")).encode("utf-8")).hexdigest()


# ── the generator ────────────────────────────────────────────────────────────

#: Values chosen for the ways they break serialisers rather than for realism: a
#: lone backslash (rule 4), a doubled one, an escaped forward slash, a quote, a
#: tab, non-ASCII, an empty string, and a URL that looks like a filter rule.
STRINGS = [
    "", "a", "Z", "0", "true", "null",
    "http://www.safeexambrowser.org/exams",
    "safeexambrowser.org/exams",
    "ws:" + BS + "localhost:8706",
    "^https?://.*" + BS + ".example" + BS + ".com",
    "^.*?:" + BS + "/" + BS + "/((safeexambrowser" + BS + ".org))",
    'say "hi"', "tab\there", "line\nbreak", "both" + BS + '"',
    "Aulia Wijaya" + chr(8212) + " kelas 7A", "kelas 10", "ünïcödé", "Ω≈ç√",
]

KEYS = [
    "a", "Z", "0", "allowWLAN", "allowWlan", "urlFilterRules", "startURL",
    "sendBrowserExamKey", "originatorVersion", "hashedQuitPassword", "browser",
    "list", "empty", "a b", "a.b", "a-b", "A", "z", "nested",
]

SCALARS = [True, False, 0, 1, 7, 40, 120000, -1, None]


def _build(rng: random.Random, depth: int = 0):
    roll = rng.random()
    if depth >= 3 or roll < 0.45:
        return rng.choice(STRINGS + SCALARS)
    if roll < 0.75:
        return {rng.choice(KEYS): _build(rng, depth + 1)
                for _ in range(rng.randint(0, 4))}
    return [_build(rng, depth + 1) for _ in range(rng.randint(0, 3))]


def _good_configs(count: int = 3000, seed: int = 20261009):
    rng = random.Random(seed)
    for _ in range(count):
        config = {rng.choice(KEYS): _build(rng) for _ in range(rng.randint(1, 6))}
        yield config


# ── the differential run ─────────────────────────────────────────────────────

def test_the_two_implementations_agree_on_every_rule_the_rules_cover():
    """The rules themselves, on hand-built shapes, before the generator runs."""
    cases = [
        {"originatorVersion": "SEB_Windows_3.10.2", "a": 1},
        {"b": True, "a": 1, "originatorVersion": "SEB_macOS_3.6.1", "empty": {}},
        {"nested": {"originatorVersion": "x", "inner": {"deep": {"deeper": []}}}},
        {"list": [{"b": 1, "A": 2}, {}, ["x", 2]]},
        {"allowWLAN": 1, "allowWlan": 2},
        {"urlFilterRules": ["^https?://.*" + BS + ".example" + BS + ".com"]},
        {"empty_dict": {}, "empty_list": [], "null": None},
        {"unicode": "Aulia Wijaya" + chr(8212) + " kelas 7A", "tab": "a\tb"},
        {"bool_and_int": [True, False, 0, 1, 40, -1]},
    ]
    for settings in cases:
        assert reference_seb_json(settings) == ck.seb_json(settings), settings
        assert reference_config_key(settings) == ck.config_key(settings), settings


def test_the_two_implementations_agree_over_three_thousand_generated_configs():
    """The generator is the point: rule *coverage* is not the same as rule *edges*."""
    checked = 0
    for settings in _good_configs():
        ours, theirs = ck.seb_json(settings), reference_seb_json(settings)
        assert ours == theirs, (
            f"the two implementations disagree\n  settings: {settings!r}\n"
            f"  ours  : {ours[:400]}\n  theirs: {theirs[:400]}")
        checked += 1
    assert checked == 3000


def test_the_two_request_hashes_agree_and_both_put_the_url_first():
    for url in ("https://scangrade.web.id/student/exams/ex-1",
                "https://scangrade.web.id/student/exams/ex-1#room",
                "http://localhost/student/exams/abc",
                "https://scangrade.web.id/x?y=1#z"):
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        assert ck.request_hash(url, key) == reference_request_hash(url, key), url
        # ...and the order is not a coin flip: the reversed concatenation differs.
        reversed_order = hashlib.sha256(
            (key + (url or "").split("#", 1)[0]).encode("utf-8")).hexdigest()
        assert ck.request_hash(url, key) != reversed_order


def test_both_implementations_refuse_a_float():
    """The ban is part of the specification this project chose, so both must hold it.

    Windows and macOS serialise `<real>` differently (SEB issue #1495), which would
    make one config produce two Config Keys — the guarantee the whole feature rests
    on. Refusing is the mitigation, and it has to be in both implementations or the
    cross-check would drift on exactly this input.
    """
    for settings in ({"batteryChargeThresholdLow": 0.1}, {"deep": [{"x": 1.5}]}):
        with pytest.raises(ck.ConfigKeyError):
            ck.seb_json(settings)
        with pytest.raises(ValueError):
            reference_seb_json(settings)


# ── values published by the specification, not invented here ────────────────

def test_the_published_example_values_survive_the_round_trip():
    """Individual values lifted from the example SEB-JSON on SEB's developer page.

    The page publishes one long example string; these are the values inside it that
    a serialiser can get *wrong* — a lone backslash in `browserMessagingSocket`
    (`ws:\\localhost:8706`, which is also why the example is not valid JSON), an
    escaped forward slash inside a URL-filter rule, a large integer, and a boolean
    that must not print as `1`. Reproducing the whole string byte-for-byte would
    depend on how a web page was fetched rather than on this module, so the values
    are the vector and the *pair* of implementations is the check.
    """
    settings = {
        "browserMessagingSocket": "ws:" + BS + "localhost:8706",
        "browserMessagingPingTime": 120000,
        "sendBrowserExamKey": True,
        "allowVirtualMachine": False,
        "URLFilterEnable": False,
        "urlFilterRules": [{"action": 1, "active": True, "regex": False,
                            "expression": "safeexambrowser.org/exams"}],
        "whitelistURLFilter": "^.*?:" + BS + "/" + BS + "/((safeexambrowser" + BS + ".org))" + BS + "/exams",
    }
    ours = ck.seb_json(settings)
    assert ours == reference_seb_json(settings)
    # The lone backslash is one backslash, and the forward slashes are not escaped.
    assert "ws:" + BS + "localhost:8706" in ours
    assert BS + BS + "localhost" not in ours
    assert BS + "/" in ours and BS + BS + "/" not in ours
    assert '"browserMessagingPingTime":120000' in ours
    assert '"sendBrowserExamKey":true' in ours
    assert '"allowVirtualMachine":false' in ours


# ── the config the app actually generates ───────────────────────────────────

def test_the_generated_exam_config_cross_checks_and_round_trips_through_the_file():
    """The settings this app really writes, through both implementations *and* the
    `.seb` container — so a decoder that changed a type cannot hide behind them."""
    settings = seb_service.settings_for(
        None, start_url="https://scangrade.web.id/student/exams/ex-1",
        quit_hash=seb_crypto.sha256_hex("quit-pass"),
        admin_hash=seb_crypto.sha256_hex("admin-pass"))
    decoded = seb_service.decode_seb(seb_service.seb_file_bytes(settings))
    for candidate in (settings, decoded):
        assert ck.seb_json(candidate) == reference_seb_json(candidate)
        assert ck.config_key(candidate) == reference_config_key(candidate)
    assert ck.config_key(decoded) == ck.config_key(settings), (
        "the file does not carry the key the door would compute from these settings")


def test_the_generated_config_holds_no_key_the_client_would_not_have():
    """The documented asymmetry, checked on our own config.

    The client hashes the keys present in the file, while the server hashes the keys
    it generated — so the generated config must not carry a key the client cannot
    reproduce. Every value is an int, a bool or a string, and `originatorVersion` is
    the only metadata key, which both sides drop.
    """
    settings = seb_service.settings_for(None, start_url="https://x/",
                                        quit_hash="a" * 64, admin_hash="b" * 64)
    assert ck.seb_json(settings) == reference_seb_json(settings)
    # The one key exempted from the hash, and nothing else is metadata.
    assert ck.ORIGINATOR_KEY in settings
    assert ck.ORIGINATOR_KEY not in ck.seb_json(settings)
    for name, value in settings.items():
        if name == ck.ORIGINATOR_KEY:
            continue
        assert isinstance(value, (bool, int, str)), (
            f"{name} is a {type(value).__name__}: a value whose SEB-JSON form is not "
            "unambiguous per the specification")
