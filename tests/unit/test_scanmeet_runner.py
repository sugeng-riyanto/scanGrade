"""The runner's own rules, without a browser, a network or a gate.

`deploy/scanmeet_runner.py` is what a second machine runs instead of a
hand-written shell script, so what it can get wrong is what a shell script gets
wrong: it can pass the wrong host, hand a gate a credential on its command line,
call a missing browser a passing run, or lose one gate's exit code inside the
other's. Each of those is a function here, and each one is handed the input that
should catch it.
"""
import sys
from pathlib import Path

import pytest

from deploy import scanmeet_runner as runner

SECRET = "s3cret-pw"
GOOD = {
    "SCANMEET_STAGING_URL": "https://scanmeet-stg.example.org",
    "SCANMEET_EXAM_ID": "exam-1",
    "SCANMEET_TEACHER": f"guru@scanmeet.invalid:{SECRET}",
    "SCANMEET_STUDENT": f"murid1@scanmeet.invalid:{SECRET}",
    "SCANMEET_STUDENT2": f"murid2@scanmeet.invalid:{SECRET}",
    "SCANMEET_OUTSIDER": f"lain@scanmeet.invalid:{SECRET}",
    "LIVEKIT_API_KEY": "key",
    "LIVEKIT_API_SECRET": "secret",
}


# ── what it refuses before it starts ─────────────────────────────────────────

def test_a_missing_value_is_named_not_guessed():
    found = runner.problems({}, ["webhook"])
    assert any("SCANMEET_STAGING_URL" in item for item in found)
    assert any("SCANMEET_EXAM_ID" in item for item in found)
    assert any("LIVEKIT_API_SECRET" in item for item in found)


def test_identities_are_required_for_the_media_rows_only():
    settings = {key: GOOD[key] for key in
                ("SCANMEET_STAGING_URL", "SCANMEET_EXAM_ID",
                 "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")}
    assert runner.problems(settings, ["webhook"]) == []
    assert runner.problems(settings, ["media"]), "media ran without four accounts"


def test_a_malformed_identity_is_refused_rather_than_sent():
    broken = dict(GOOD, SCANMEET_TEACHER="guru@scanmeet.invalid")
    found = runner.problems(broken, ["media"])
    assert any("SCANMEET_TEACHER" in item for item in found)


def test_the_production_host_is_refused_by_name():
    settings = dict(GOOD, SCANMEET_STAGING_URL="https://scangrade.web.id")
    found = runner.problems(settings, ["media"])
    assert any("production" in item for item in found)
    assert runner.problems(settings, ["media"], allow_production=True) == []


def test_a_lookalike_host_is_not_production():
    # `scangrade.web.id.evil.test` is a different host that a naive `in` check
    # would let through, and staging's own subdomains must stay allowed.
    assert runner.production_url("https://scangrade.web.id") is True
    assert runner.production_url("https://www.scangrade.web.id") is True
    assert runner.production_url("https://scangrade.web.id.evil.test") is False
    assert runner.production_url("https://scanmeet-stg.1.2.3.4.sslip.io") is False


def test_production_hostname_cannot_hide_in_url_userinfo_or_dns_dot():
    for url in ("https://user@scangrade.web.id/",
                "https://scangrade.web.id./",
                "https://user:password@scangrade.web.id:443/"):
        assert runner.problems(dict(GOOD, SCANMEET_STAGING_URL=url),
                               ["webhook"]), url


def test_ambiguous_or_non_https_urls_are_refused_before_probe():
    for url in ("scangrade.web.id", "http://scanmeet-stg.example.org",
                "https://user@scanmeet-stg.example.org",
                "https://scanmeet-stg.example.org:",
                "https://[::1"):
        assert not runner.host_of(url), url
        assert runner.problems(dict(GOOD, SCANMEET_STAGING_URL=url),
                               ["webhook"]), url


def test_valid_ipv6_and_trailing_dot_normalization():
    assert runner.host_of("https://[::1]:8443/room") == "::1"
    assert runner.host_of("https://scangrade.web.id./") == "scangrade.web.id"


def test_host_of_strips_scheme_port_and_path():
    assert runner.host_of("https://A.example.org:8443/x?y=1") == "a.example.org"
    assert runner.host_of("") == ""
    assert runner.host_of("not a url") == "not a url".split(":")[0]


# ── what it hands the gates ─────────────────────────────────────────────────

def test_the_browser_is_found_in_the_order_it_promises():
    assert runner.browser({}, "/given/chrome", which=lambda _n: None,
                          exists=lambda p: p == "/given/chrome") == "/given/chrome"
    assert runner.browser({"SG_CHROME": "/env/chrome"}, "",
                          which=lambda _n: "/path/chrome",
                          exists=lambda p: p == "/env/chrome") == "/env/chrome"
    assert runner.browser({}, "", which=lambda n: "/bin/" + n,
                          exists=lambda p: p == runner.CHROME_PATHS[0]) \
        == runner.CHROME_PATHS[0]
    assert runner.browser({}, "", which=lambda _n: None,
                          exists=lambda _p: False) == ""


def test_secrets_travel_in_the_environment_and_never_on_the_command_line():
    env = runner.child_env(GOOD)
    assert env["SCANMEET_TEACHER"] == GOOD["SCANMEET_TEACHER"]
    for gate in ("webhook", "media"):
        argv = runner.command(gate, GOOD, ["--signoff"])
        assert SECRET not in " ".join(argv), f"{gate} put a password in argv"
        assert GOOD["SCANMEET_STAGING_URL"] in argv


def test_each_gate_is_given_its_own_subject_and_flags():
    webhook = runner.command("webhook", GOOD)
    media = runner.command("media", GOOD, ["--signoff", "--json-out", "out.json"])
    assert runner.GATES["webhook"] in webhook and "--exam" not in webhook
    assert "--exam" in media and GOOD["SCANMEET_EXAM_ID"] in media
    assert media[-3:] == ["--signoff", "--json-out", "out.json"]


# ── what it reports ─────────────────────────────────────────────────────────

class Spawned:
    def __init__(self, code):
        self.returncode = code
        self.calls = []


def fake_spawn(code):
    record = Spawned(code)

    def spawn(argv, **kwargs):
        record.calls.append((argv, kwargs))
        return record
    return spawn, record


def test_run_keeps_each_gates_own_exit_code_in_order():
    spawn, record = fake_spawn(1)
    results = runner.run(["webhook", "media"], GOOD, ["--signoff"], spawn=spawn)
    assert [gate for gate, _code in results] == ["webhook", "media"]
    assert [code for _gate, code in results] == [1, 1]
    assert len(record.calls) == 2
    for _argv, kwargs in record.calls:
        assert kwargs["env"]["SCANMEET_STUDENT"] == GOOD["SCANMEET_STUDENT"]


def test_the_verdict_is_the_worst_answer_and_never_a_pass_by_default():
    assert runner.verdict([("webhook", 0), ("media", 0)]) == 0
    assert runner.verdict([("webhook", 0), ("media", 1)]) == 1
    assert runner.verdict([("webhook", 1), ("media", 2)]) == 2, \
        "could-not-measure is not a failure and not a pass"
    assert runner.verdict([]) == 2, "no gate ran, so nothing passed"


# ── the entry point ─────────────────────────────────────────────────────────

def env_file(tmp_path, settings):
    path = tmp_path / "staging.env"
    path.write_text("\n".join(f"{k}={v}" for k, v in settings.items()) + "\n",
                    encoding="utf-8")
    return path


def test_check_launches_nothing(tmp_path, capsys):
    path = env_file(tmp_path, GOOD)
    spawn, record = fake_spawn(0)
    code = runner.main(["--env", str(path), "--all", "--check"], spawn=spawn,
                       which=lambda n: "/bin/" + n, exists=lambda p: True)
    assert code == 0
    assert record.calls == [], "--check ran a gate"
    printed = capsys.readouterr().out
    assert SECRET not in printed, "the report printed a password"
    assert "nothing was launched" in printed


def test_a_missing_env_file_is_could_not_measure(tmp_path):
    code = runner.main(["--env", str(tmp_path / "absent.env"), "--webhook"],
                       spawn=fake_spawn(0)[0])
    assert code == 2


def test_no_gate_named_is_not_a_pass(tmp_path):
    path = env_file(tmp_path, GOOD)
    assert runner.main(["--env", str(path)], spawn=fake_spawn(0)[0]) == 2


def test_a_gate_that_disagreed_makes_the_run_disagree(tmp_path):
    path = env_file(tmp_path, GOOD)
    code = runner.main(["--env", str(path), "--webhook"],
                       spawn=fake_spawn(1)[0], which=lambda n: None,
                       exists=lambda p: False)
    assert code == 1


def test_media_without_a_browser_is_could_not_measure(tmp_path, capsys):
    path = env_file(tmp_path, GOOD)
    spawn, record = fake_spawn(0)
    code = runner.main(["--env", str(path), "--media"], spawn=spawn,
                       which=lambda n: None, exists=lambda p: False)
    assert code == 2
    assert record.calls == [], "a media run started with no browser"
    assert "no Chrome" in capsys.readouterr().out


def test_the_environment_file_is_the_only_source_of_values(tmp_path, monkeypatch):
    """A value in this process's environment must not stand in for the file."""
    monkeypatch.setenv("SCANMEET_STAGING_URL", "https://leaked.example.org")
    path = env_file(tmp_path, {k: v for k, v in GOOD.items()
                              if k != "SCANMEET_STAGING_URL"})
    spawn, record = fake_spawn(0)
    code = runner.main(["--env", str(path), "--webhook"], spawn=spawn,
                       which=lambda n: None, exists=lambda p: False)
    assert code == 2
    assert record.calls == []


def test_every_line_it_prints_is_ascii():
    """A console that cannot encode the character crashes the runner, not the run.

    Measured: a box-drawing character in the per-gate line raised
    `UnicodeEncodeError: 'charmap' codec` on a cp1252 console before the first gate
    had started, and the traceback read as the runner being broken. The gates'
    own output is theirs; this file's is read by whoever runs it.
    """
    import ast

    tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
    printed = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            continue
        for argument in node.args:
            for inner in ast.walk(argument):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    printed.append(inner.value)
    assert printed, "no printed literals were read — the scan looked at nothing"
    offenders = [text for text in printed if not text.isascii()]
    assert not offenders, f"non-ASCII in printed text: {offenders}"
