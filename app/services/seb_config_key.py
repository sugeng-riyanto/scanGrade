"""The Config Key: one key, computed here, that a real SEB client will agree with.

What this is, in one sentence: the Config Key ("kunci masuk") is a SHA-256 over the
**SEB-JSON** of a config file's settings, and a client proves it is using *our*
config by sending `X-SafeExamBrowser-ConfigKeyHash` = SHA256(absolute URL + that
key). The server recomputes both halves and refuses the paper when they disagree.

Why Config Key and not Browser Exam Key
---------------------------------------
This is the correction that shapes every phase onto this module, and it is written
here so nobody re-derives the wrong assumption. The BEK is a hash over the config
**and the SEB binary's own signature**, so it differs per SEB version and platform
and **cannot be computed by a server** — the only alternative is collecting a
registered key per platform. The Config Key holds no signature: the project's own
developer documentation states it is "same in each platform version of SEB" and
that its "most important advantage" is that it "can be calculated in an exam
system … (server-side)". One value per exam, for every platform. There is no BEK
column anywhere in this schema, and that absence is the enforcement.

The rules, from that documentation, in the order it gives them
--------------------------------------------------------------
1. drop `originatorVersion` **before** anything else (it is metadata about which
   SEB version saved the file, and keeping it would make the key change on an
   upgrade that changed no setting);
2. sort every dictionary, recursively — including those inside arrays —
   alphabetically by key name, case-insensitively, because a hash is order
   dependent and JSON objects have no defined order;
3. no whitespace and no line formatting;
4. no character escaping — a backslash in a URL-filter rule stays a backslash;
5. drop empty dictionaries (outdated clients could emit them);
6. strings are UTF-8; `<data>` becomes Base64; `<date>` becomes ISO 8601;
7. SHA-256, Base16, lower case, 64 characters.

Floats are FORBIDDEN in a config we key — and that is a measured decision
-----------------------------------------------------------------------
SEB issue #1495 (open, untriaged, reproduced against SEB 3.10.2 for Windows and
3.6.1 for macOS) shows the two platforms emit **different bytes for the same
`<real>` value** — Windows `1.23456789012346`, macOS `1.234567890123457` — so a
config containing one float can produce two different Config Keys, which directly
contradicts the cross-platform guarantee above. Rather than carry that, this module
**refuses** a settings map containing a float: `ConfigKeyError`. Every setting a
proctored exam needs is a bool, an int, a string or a list, so the divergence is
avoidable rather than merely mitigated.

The one thing this module cannot do
-----------------------------------
Prove byte-equality with a real client. Rules 2-6 above decide the exact bytes, and
a serializer that is *almost* right produces a key that *never* matches — which is
why `tests/unit/test_seb_config_key.py` pins every rule against the documented
examples, and why the self-test against an installed SEB (Phase 11) is the only
thing that makes this verified rather than merely conformant. That limit is stated
in the code rather than discovered in production.
"""
from __future__ import annotations

import hashlib
import hmac

#: Dropped before serialising: metadata about the SEB version that saved the file,
#: exempt from the hash by the specification.
ORIGINATOR_KEY = "originatorVersion"

#: The header a client sends. Named once: the validation door and the tests must not
#: disagree about the spelling, which is the kind of difference that fails closed
#: and silently.
CONFIG_KEY_HEADER = "X-SafeExamBrowser-ConfigKeyHash"


class ConfigKeyError(ValueError):
    """A settings map that cannot carry a Config Key, with the reason in the text."""


def _round_trip(value) -> bool:
    """Whether a float survives `repr` unchanged — the check a float ban needs."""
    try:
        return float(repr(value)) == value
    except Exception:  # noqa: BLE001 — a value we cannot reason about is not safe
        return False


def _check_types(value, path: str = "") -> None:
    """Reject floats, and anything else JSON cannot carry, before hashing.

    A float is not a style preference: see the module docstring. The failure is
    raised rather than coerced, because silently rounding it here would produce a
    key the client cannot reproduce — a paper that refuses to open, with no error
    anywhere saying why.
    """
    if isinstance(value, float):
        raise ConfigKeyError(
            f"a float is not allowed in a keyed SEB config (at {path or '<root>'}): "
            "Windows and macOS serialise <real> differently (SEB issue #1495), so the "
            "Config Key would differ per platform. Use an int, or a string."
        )
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigKeyError(f"non-string key at {path or '<root>'}: {key!r}")
            _check_types(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_types(item, f"{path}[{index}]")


def _prune(value):
    """Apply rules 1 and 5, recursively: no originator, and no empty dictionary."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == ORIGINATOR_KEY:
                continue
            pruned = _prune(item)
            # An empty dict is dropped; an empty list is NOT. The specification names
            # empty <dict> elements only, and a config that means "no URL filters" as
            # `[]` is a different setting from one that omits the key.
            if isinstance(pruned, dict) and not pruned:
                continue
            out[key] = pruned
        return out
    if isinstance(value, (list, tuple)):
        return [_prune(item) for item in value]
    return value


def _sort_key(name: str):
    """Case-insensitive ordering, per rule 2.

    `casefold()` rather than `lower()`: the documentation asks for value-based
    case-insensitive ordering, and two keys differing only in case must order the
    same way on every machine. The specification also says such keys *should not be
    used* — this is the deterministic answer for the ones that are.
    """
    return (name.casefold(), name)


def _encode_string(text: str) -> str:
    """A JSON string with rule 4 applied: backslashes stay literal.

    The specification is explicit that a backslash "as found in URL filter rules"
    must not be escaped, so this does not use `json.dumps` — which would double every
    one of them and change the hash. A double quote still has to be escaped, because
    a quote inside a string is the one thing that would not survive the round trip.
    Control characters are escaped as JSON requires, for the same reason.
    """
    out = ['"']
    for char in text:
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\")          # rule 4: NOT doubled
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20:
            out.append("\\u%04x" % ord(char))
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def seb_json(settings: dict) -> str:
    """The SEB-JSON string for `settings` — the exact bytes rule 7 hashes.

    Deterministic by construction: the same settings map always produces the same
    string, on any machine, with no dependence on dict insertion order.
    """
    if not isinstance(settings, dict):
        raise ConfigKeyError("settings must be a mapping")
    _check_types(settings)
    return _encode(_prune(settings))


def _encode(value) -> str:
    """A compact, sorted, unescaped-backslash serialiser (rules 2-6)."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, bool):                     # pragma: no cover - unreachable
        return "true" if value else "false"
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):                    # pragma: no cover - _check_types
        raise ConfigKeyError("a float reached the serialiser")
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, dict):
        parts = []
        for key in sorted(value, key=_sort_key):
            parts.append(_encode_string(key) + ":" + _encode(value[key]))
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    raise ConfigKeyError(f"cannot serialise {type(value).__name__} into SEB-JSON")


def config_key(settings: dict) -> str:
    """The Config Key: SHA-256 of the SEB-JSON, Base16, lower case (rule 7)."""
    return hashlib.sha256(seb_json(settings).encode("utf-8")).hexdigest()


def absolute_url_without_fragment(request_url: str) -> str:
    """The URL as the specification defines the hashed one.

    "If the absolute URL contains a Fragment (the last part of an URL starting with
    #, when using page anchors), the fragment needs to be removed." A page anchor is
    a position on a page, not a different resource, so keeping it would make one
    paper produce as many keys as it has anchors — and the exam page has one
    (`#room`).
    """
    return (request_url or "").split("#", 1)[0]


def request_hash(request_url: str, key: str) -> str:
    """SHA256(absolute URL + Config Key) — **URL first**, and this order is a trap.

    The specification says it twice ("concatenate this URL string with the Config
    Key hash string") and the accepted answer on the project's own forum is somebody
    who assumed the reverse and watched every validation fail. Pinned by a test so it
    cannot be quietly swapped back.
    """
    material = absolute_url_without_fragment(request_url) + (key or "")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def header_matches(request_url: str, header_value: str, key: str) -> bool:
    """Whether the header a client sent matches this URL and key.

    Two robustness rules learned from what clients actually send: the documented
    header example ends with a semicolon, so a trailing `;` and surrounding
    whitespace are tolerated; and the comparison is constant-time, because a
    byte-by-byte early exit on a secret-derived value is a timing oracle for
    guessing it.
    """
    if not key or not header_value:
        return False
    sent = str(header_value).strip().rstrip(";").strip().lower()
    return hmac.compare_digest(sent, request_hash(request_url, key))


__all__ = [
    "CONFIG_KEY_HEADER", "ORIGINATOR_KEY", "ConfigKeyError",
    "absolute_url_without_fragment", "config_key", "header_matches",
    "request_hash", "seb_json",
]
