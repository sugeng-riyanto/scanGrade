"""The auth door's own words have to follow the toggle too.

Translating the twelve auth pages left the *messages* behind: the copy was bound
with ``t('Indonesia','English')`` while every error still arrived from the route as
a bare Python string. English mode therefore read "Wrong email or password" for the
headings and "Email atau password salah" for the alert directly under them — the
half-translation that looks, to a reader, like the toggle is broken.

The server cannot pick a language here: the choice lives in ``localStorage``, which
it never sees. So a message is an ``(id, en)`` pair and the template renders both,
letting Alpine choose. These tests hold that arrangement, because the failure mode
is silent — a route that goes back to a literal string works perfectly, in
Indonesian, forever.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUTH_ROUTE = ROOT / "app" / "routes" / "auth.py"
UTILS_AUTH = ROOT / "app" / "utils" / "auth.py"
CATALOGUE = ROOT / "app" / "utils" / "auth_messages.py"
AUTH_TEMPLATES = sorted((ROOT / "app" / "templates" / "auth").glob("*.html"))
CHROME = ROOT / "app" / "templates" / "auth" / "_chrome.html"

import sys
sys.path.insert(0, str(ROOT / "tests" / "unit"))
from test_language_toggle import INDONESIAN_MARKERS, _markers_in  # noqa: E402

from app.utils.auth_messages import (  # noqa: E402
    MESSAGES, RATE_LIMIT_ACTIONS, auth_error, first, rate_limit_error,
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _string_literals(node: ast.AST) -> list[str]:
    """Every string literal appearing under ``node``, including f-string pieces."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


def _catalogue_call_keys(path: Path) -> dict[str, set[str]]:
    """The keys ``auth_error()`` / ``rate_limit_error()`` are called with."""
    found = {"auth_error": set(), "rate_limit_error": set()}
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None)
        if name in found and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                found[name].add(first_arg.value)
    return found


def _render_template_errors(path: Path) -> list[ast.AST]:
    """The ``error=`` arguments of every ``render_template()`` call."""
    out = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "render_template":
            continue
        for kw in node.keywords:
            if kw.arg == "error":
                out.append(kw.value)
    return out


# ── the catalogue is bilingual, and really is ─────────────────────

@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_every_pair_carries_both_languages(key):
    id_text, en_text = MESSAGES[key]

    assert id_text.strip() and en_text.strip(), f"{key} has an empty half"
    assert id_text != en_text, (
        f"{key} translates to itself — either the English half is a typo or the "
        f"message does not belong in the catalogue")


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_the_english_half_is_english(key):
    """The failure this catches is the one that survives reading: an English half
    copied from the Indonesian, which renders identically in both modes and looks
    like a page nobody got round to."""
    id_text, en_text = MESSAGES[key]
    leaked = _markers_in(en_text)

    assert not leaked, (
        f"{key}: the English half still reads Indonesian ({sorted(leaked)}): {en_text!r}")


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_the_indonesian_half_is_indonesian(key):
    """The opposite typo: an English string in the Indonesian column, which would
    show English to a reader who chose Indonesian."""
    id_text, _ = MESSAGES[key]
    assert _markers_in(id_text), (
        f"{key}: the Indonesian half carries no Indonesian word, so it is probably "
        f"the English string in both columns: {id_text!r}")


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_placeholders_appear_in_both_halves(key):
    """A count in one language and not the other is a sentence that reads wrong in
    exactly one mode — the kind of thing a screenshot in the other language hides."""
    id_text, en_text = MESSAGES[key]
    assert _placeholders(id_text) == _placeholders(en_text), (
        f"{key}: the halves do not fill the same placeholders "
        f"({_placeholders(id_text)} vs {_placeholders(en_text)})")


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


# ── the routes go through the catalogue ──────────────────────────

def test_every_error_the_auth_routes_render_is_a_pair_or_a_reason():
    """A literal is the defect: it is Indonesian in both modes and nothing fails."""
    offenders = []
    for value in _render_template_errors(AUTH_ROUTE):
        if isinstance(value, ast.Call):
            called = getattr(value.func, "id", None)
            if called in ("auth_error", "rate_limit_error"):
                continue
            offenders.append(f"unexpected call {called}()")
        elif isinstance(value, ast.Name):
            # `error=message` — the classified login failure, built by
            # `_classify_login_error`, which returns a pair.
            continue
        else:
            offenders.append(ast.dump(value)[:70])

    assert not offenders, (
        "every `error=` in auth.py must be auth_error()/rate_limit_error() or a "
        "name bound to one, so both languages reach the page:\n  "
        + "\n  ".join(offenders))


def test_the_classified_login_failure_is_a_pair():
    """This one is easy to miss: the message never appears in a template, only as
    `error=message`, so a literal inside `_classify_login_error` is invisible to the
    check above."""
    source = AUTH_ROUTE.read_text(encoding="utf-8")
    body = source[source.index("def _classify_login_error"):]
    body = body[:body.index("\ndef ")]

    assert "auth_error(" in body, "_classify_login_error builds no catalogue message"
    assert not re.search(r"return\s+(True|False),\s*[\"']", body), (
        "a returned bare string is Indonesian in both languages:\n"
        + "\n".join(l for l in body.splitlines() if re.search(r"[\"'][A-Z]", l)))


def test_every_key_the_routes_use_exists():
    """A typo is a KeyError on one branch of the login flow — which is to say, in
    production, for the one user who hits it."""
    used = _catalogue_call_keys(AUTH_ROUTE)

    missing = sorted(used["auth_error"] - set(MESSAGES))
    assert not missing, f"auth_error() called with unknown keys: {missing}"

    missing = sorted(used["rate_limit_error"] - set(RATE_LIMIT_ACTIONS))
    assert not missing, f"rate_limit_error() called with unknown keys: {missing}"


def test_the_catalogue_is_not_larger_than_its_use():
    """A message nobody calls is a message nobody translated: it picks up no
    reviewer and drifts. Keys shared with the decorators are the exception."""
    used = set(_catalogue_call_keys(AUTH_ROUTE)["auth_error"])
    used |= set(_catalogue_call_keys(UTILS_AUTH)["auth_error"])

    unused = sorted(set(MESSAGES) - used)
    assert not unused, (
        "these catalogue entries are never produced by a route, so no reader can "
        "reach them — delete them or wire them up:\n  " + "\n  ".join(unused))


def test_no_auth_page_prints_a_message_raw():
    """A pair printed with ``{{ error }}`` renders as a Python tuple; a pair
    printed with the macro renders as one language. Only the macro is right."""
    offenders = []
    for path in AUTH_TEMPLATES:
        text = path.read_text(encoding="utf-8")
        for name in ("error", "message"):
            if re.search(r"\{\{\s*" + name + r"\s*\}\}", text):
                offenders.append(f"{path.name}: {{{{ {name} }}}}")

    assert not offenders, (
        "these messages are (id, en) pairs now and must go through alert_body():\n  "
        + "\n  ".join(offenders))


def test_every_auth_page_that_shows_a_message_imports_the_macro():
    showing = [p for p in AUTH_TEMPLATES
               if "{% if error %}" in p.read_text(encoding="utf-8")
               or "get_flashed_messages" in p.read_text(encoding="utf-8")]

    assert showing, "the auth pages no longer render their own errors — this test is vacuous"
    for path in showing:
        assert "alert_body" in path.read_text(encoding="utf-8"), (
            f"{path.name} shows a message without importing alert_body, so it would "
            f"print the pair raw")


# ── the macro itself ─────────────────────────────────────────────

def test_the_macro_renders_both_halves_and_chooses_none_server_side():
    text = CHROME.read_text(encoding="utf-8")

    assert re.search(r"\{%-?\s*macro alert_body\(value\)\s*-?%\}", text), \
        "alert_body is gone"
    assert "{{ value[0] }}" in text and "{{ value[1] }}" in text, (
        "the macro must render both halves — one of them is missing, which is a "
        "language nobody can read")
    assert "lang!=='en'" in text and "lang==='en'" in text, (
        "the macro must let Alpine choose, because the server cannot")
    assert re.search(r"\{%-?\s*if value is sequence and value is not string", text), (
        "the macro must pass a non-pair through untouched: a Supabase error is a "
        "string and splitting it would print its first two characters")


def test_a_partial_is_only_used_by_a_page_that_extends_base():
    """The exemption for `_`-prefixed partials in the toggle guard is only safe if
    the partial is inlined somewhere that has a scope. A page that imports it and
    does not extend base.html would read `lang` as undefined — which is falsy, so it
    would render Indonesian forever without an error."""
    importers = [p for p in AUTH_TEMPLATES
                 if "auth/_chrome.html" in p.read_text(encoding="utf-8")]

    assert importers, "no page imports the auth chrome — this test is vacuous"
    for path in importers:
        text = path.read_text(encoding="utf-8")
        assert re.search(r"""\{%-?\s*extends\s+["']base\.html["']""", text), (
            f"{path.name} imports the scope-dependent chrome but does not extend "
            f"base.html, so it has no `lang` to read")


# ── the helpers ──────────────────────────────────────────────────

def test_a_rate_limit_notice_rounds_a_partial_minute_up():
    """Telling someone to wait 0 minutes is telling them to retry immediately and
    fail, so 1 second must read as one minute."""
    for retry, expected in ((1, 1), (59, 1), (60, 1), (61, 2), (600, 10)):
        _, en_text = rate_limit_error("login", retry)
        assert f"{expected} minute" in en_text, (retry, en_text)


def test_a_format_placeholder_is_filled_in_both_languages():
    id_text, en_text = auth_error("npsn_taken", school="SMA Negeri 1")

    assert "SMA Negeri 1" in id_text and "SMA Negeri 1" in en_text, (
        "the school name reached only one language")
    assert "{" not in id_text and "{" not in en_text, "an unfilled placeholder shipped"


def test_the_api_keeps_receiving_a_string():
    """`jsonify({'error': pair})` would hand an API client an array where it expects
    a string. The pair is a page concern; these two helpers are the boundary."""
    assert first(auth_error("session_required")) == "Silakan login terlebih dahulu"
    assert first("Supabase said no") == "Supabase said no"
