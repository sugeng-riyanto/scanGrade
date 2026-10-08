"""The Config Key, rule by rule, against the documented algorithm.

Why a known-answer test and not only property tests: a serializer that is *almost*
right produces a key that *never* matches, and every property test here would still
pass. The strongest check available without an installed SEB is to write the
expected SEB-JSON string out **literally** and hash it independently of the module —
two descriptions of the same rules, which is what catches a rule that both the
implementation and its author got wrong the same way.

`chr(92)` is used wherever a backslash is meant, rather than a literal, because a
backslash written four times in a test is a backslash written wrongly — which is
exactly one of the two defects this file's first version shipped.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from app.services import seb_config_key as ck

#: One backslash, spelled without escaping.
BS = chr(92)


# ── the rules ────────────────────────────────────────────────────────────────

def test_originator_version_is_dropped_before_anything_else():
    """Rule 1. Keeping it would move the key on an upgrade that changed no setting."""
    with_origin = {"originatorVersion": "SEB_Windows_3.10.2", "urlFilterEnable": True}
    without = {"urlFilterEnable": True}
    assert ck.seb_json(with_origin) == ck.seb_json(without)
    assert "originatorVersion" not in ck.seb_json(with_origin)
    # ...and it is exempt even when nested, not only at the root.
    nested = {"browser": {"originatorVersion": "x", "startUrl": "https://a/"}}
    assert ck.seb_json(nested) == ck.seb_json({"browser": {"startUrl": "https://a/"}})


def test_keys_are_sorted_recursively_case_insensitively():
    """Rule 2 — dicts *and* the dicts inside arrays."""
    settings = {"zeta": 1, "Alpha": 2, "beta": {"y": 1, "X": 2},
                "list": [{"b": 1, "A": 2}]}
    out = ck.seb_json(settings)
    assert out == ('{"Alpha":2,"beta":{"X":2,"y":1},"list":[{"A":2,"b":1}],"zeta":1}')


def test_case_only_keys_order_deterministically():
    """Rule 2 sorts case-insensitively; the tie-break is ours, and it must be stable.

    The specification asks for value-based case-insensitive ordering and then says
    keys that differ only in case "should not be used" at all, so it does not settle
    the tie-break. What is *not* optional is determinism: a key whose order depends on
    how the dict was built is a different key on a different machine. The exact string
    is pinned so a future refactor cannot change it quietly.
    """
    a = ck.seb_json({"allowWLAN": 1, "allowWlan": 2})
    b = ck.seb_json({"allowWlan": 2, "allowWLAN": 1})
    assert a == b, "the same settings produced two different strings"
    assert "allowWLAN" in a and "allowWlan" in a
    assert a == '{"allowWLAN":1,"allowWlan":2}'


def test_no_whitespace_and_no_line_formatting():
    """Rule 3."""
    out = ck.seb_json({"a": 1, "b": [1, 2], "c": {"d": True}})
    assert " " not in out and "\n" not in out and "\t" not in out
    assert out == '{"a":1,"b":[1,2],"c":{"d":true}}'


def test_backslashes_are_not_escaped_but_quotes_are():
    """Rule 4, and the one place it has to bend.

    A URL-filter rule carries backslashes and the specification says they stay
    literal — escaping them would change every byte after the first one. A double
    quote inside a string still cannot, or the string would not survive a reader.
    """
    rule = "^https?://.*" + BS + ".example" + BS + ".com"
    out = ck.seb_json({"urlFilterRules": [rule]})
    assert out.count(BS) == rule.count(BS), "a backslash was doubled or dropped"
    assert BS + ".example" in out
    assert ck.seb_json({"a": 'say "hi"'}) == '{"a":"say ' + BS + '"hi' + BS + '""}'


def test_empty_dicts_are_dropped_but_empty_lists_are_kept():
    """Rule 5, and the distinction that matters.

    The specification names empty `<dict>` elements. `[]` is a *setting* — "no URL
    filters" is not the same statement as "this config says nothing about filters" —
    so it is kept.
    """
    assert ck.seb_json({"a": {}, "b": 1}) == '{"b":1}'
    assert ck.seb_json({"a": {"b": {}}, "c": 1}) == '{"c":1}'
    assert ck.seb_json({"urlFilterRules": [], "a": 1}) == '{"a":1,"urlFilterRules":[]}'
    # An empty dict inside a list is dropped as an element, not as the list.
    assert ck.seb_json({"l": [{}, {"a": 1}]}) == '{"l":[{},{"a":1}]}'


def test_strings_are_utf8():
    """Rule 6."""
    out = ck.seb_json({"nama": "Aulia Wijaya \u2014 kelas 7A"})
    assert "Aulia Wijaya" in out and "7A" in out
    assert out.encode("utf-8").decode("utf-8") == out


# ── the key itself ───────────────────────────────────────────────────────────

def test_the_key_is_a_lowercase_base16_sha256():
    """Rule 7: 32 bytes, 64 characters, lower-case a-f."""
    key = ck.config_key({"startUrl": "https://scangrade.web.id/exam/1"})
    assert len(key) == 64
    assert key == key.lower()
    assert all(c in "0123456789abcdef" for c in key)


def test_a_known_answer_states_the_exact_bytes():
    """The strongest check without a real client: hash a literal string.

    `EXPECTED` is written out by hand from the rules, and the hash is computed with
    `hashlib` directly, so neither the serializer nor its output is taken on trust.
    """
    settings = {"b": True, "a": 1, "originatorVersion": "SEB_macOS_3.6.1",
                "list": ["x", 2], "empty": {}}
    EXPECTED = '{"a":1,"b":true,"list":["x",2]}'
    assert ck.seb_json(settings) == EXPECTED
    assert ck.config_key(settings) == hashlib.sha256(EXPECTED.encode("utf-8")).hexdigest()
    # And it is stable: the same settings, in another insertion order, same key.
    assert ck.config_key(dict(reversed(list(settings.items())))) == ck.config_key(settings)


def test_a_float_is_refused_anywhere_in_the_map():
    """SEB issue #1495: `<real>` serialises differently on Windows and macOS, so a
    keyed config containing one can produce two different Config Keys — which
    contradicts the one-key-for-every-platform guarantee this whole feature rests on.
    """
    for settings in ({"batteryChargeThresholdLow": 0.1},
                     {"nested": {"deep": [1, 2.5]}},
                     {"list": [{"x": 1.0}]}):
        with pytest.raises(ck.ConfigKeyError) as caught:
            ck.seb_json(settings)
        assert "float" in str(caught.value)
    # An int is not a float, and a bool is not either even though it is an int subclass.
    assert ck.seb_json({"count": 3}) == '{"count":3}'
    assert ck.seb_json({"on": True}) == '{"on":true}'


def test_settings_must_be_a_mapping_and_keys_must_be_strings():
    for bad in (["not", "a", "map"], "nope", 5):
        with pytest.raises(ck.ConfigKeyError):
            ck.seb_json(bad)
    with pytest.raises(ck.ConfigKeyError):
        ck.seb_json({1: "x"})


# ── the request half: URL first, and no fragment ─────────────────────────────

def test_the_url_comes_first_and_that_order_is_pinned():
    """The trap the project's own forum records: `key + url` fails every validation."""
    url = "https://scangrade.web.id/student/exams/abc"
    key = "d" * 64
    expected = hashlib.sha256((url + key).encode("utf-8")).hexdigest()
    assert ck.request_hash(url, key) == expected
    reversed_order = hashlib.sha256((key + url).encode("utf-8")).hexdigest()
    assert ck.request_hash(url, key) != reversed_order, (
        "request_hash hashes the Config Key before the URL — the specification (and "
        "the accepted forum answer) say URL first, and the reverse fails 100% of live "
        "validations")


def test_a_page_fragment_is_removed_before_hashing():
    """`#room` is an anchor, not a resource — the exam page has one."""
    url = "https://scangrade.web.id/teacher/exams/b6a00fc4/sessions#room"
    bare = "https://scangrade.web.id/teacher/exams/b6a00fc4/sessions"
    assert ck.absolute_url_without_fragment(url) == bare
    assert ck.request_hash(url, "k" * 64) == ck.request_hash(bare, "k" * 64)


def test_the_header_is_accepted_with_a_semicolon_and_whitespace():
    """The documented header example ends with `;` — clients send it."""
    url = "https://scangrade.web.id/student/exams/abc"
    key = "f" * 64
    good = ck.request_hash(url, key)
    assert ck.header_matches(url, good, key)
    assert ck.header_matches(url, good + ";", key)
    assert ck.header_matches(url, "  " + good.upper() + " ; ", key)


def test_the_header_is_refused_when_it_disagrees_or_is_missing():
    url = "https://scangrade.web.id/student/exams/abc"
    key = "a" * 64
    assert not ck.header_matches(url, ck.request_hash(url, "b" * 64), key)
    assert not ck.header_matches(url + "?x=1", ck.request_hash(url, key), key)
    assert not ck.header_matches(url, "", key)
    assert not ck.header_matches(url, None, key)
    assert not ck.header_matches(url, ck.request_hash(url, key), "")
    assert not ck.header_matches(url, ck.request_hash(url, key), None)


def test_two_different_configs_never_share_a_key():
    a = ck.config_key({"startUrl": "https://scangrade.web.id/exam/1"})
    b = ck.config_key({"startUrl": "https://scangrade.web.id/exam/2"})
    assert a != b


def test_the_serialiser_is_not_a_json_round_trip():
    """Documents the deliberate departure: rule 4 makes SEB-JSON *not* JSON.

    Asserted so a future reader reaching for `json.loads` on our own output finds a
    test explaining why it will not parse, rather than "fixing" the serialiser into
    one that escapes backslashes and therefore produces the wrong key.
    """
    out = ck.seb_json({"urlFilterRules": ["^https?://.*" + BS + ".example"]})
    assert BS + ".example" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)          # a lone backslash is not a valid JSON escape
