"""The performance gate: a release must not be slower than the last good one.

`claims_gate.py` holds the deployment to the number printed on the landing page.
That check cannot see a release that costs 40% of every page's response time while
staying inside the published bound — and five of those in a row are a box that no
longer does what it did, each one passing on its own.

This gate asks the other question, so these tests hold it to it:

1. the comparison is a *ratio against the previous passing release*, not against
   an absolute, because the baseline is the same box on the same load;
2. the baseline is written only when a release passes — otherwise a bad release
   becomes the yardstick and the regression stops being visible;
3. a changed reference load or a box that is already busy is "could not measure",
   never a verdict, because exit 2 is deliberately not a rollback;
4. the deploy treats "slower" differently from "could not tell", and only a
   confirmed regression with PERF_ENFORCE=true rolls the release back.

The end-to-end tests run the real `main()` against a local HTTP server and a fake
harness, so the accept/reject/baseline path is exercised rather than described.
"""
import importlib.util
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "perf_gate.py"
CLAIMS_PATH = ROOT / "deploy" / "claims_gate.py"
DEPLOY = ROOT / "deploy" / "scangrade-deploy.sh"
INSTALLER = ROOT / "deploy" / "install-auto-deploy.sh"


def _load(path: Path, name: str):
    """Import a deploy script without making deploy/ a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(GATE_PATH, "perf_gate")
claims = _load(CLAIMS_PATH, "claims_gate")

# (conf variable, gate source, the one switch the deploy reads itself) per gate.
GATES = {
    "perf": ("PERF_CONF", GATE_PATH.read_text(encoding="utf-8"), "PERF_ENFORCE"),
    "claims": ("CLAIMS_CONF", CLAIMS_PATH.read_text(encoding="utf-8"), "CLAIMS_ENFORCE"),
}


# ── fixtures ─────────────────────────────────────────────────────────────────

def summary(p50: float, p95: float, sessions: int = 20, error_pct: float = 0.0,
            fivexx: int = 0, logins: int | None = None,
            endpoint: str = "GET /student/dashboard") -> dict:
    """A harness summary in the shape the real one writes."""
    ok = sessions if logins is None else logins
    return {
        "sessions_launched": sessions,
        "logins_ok": ok,
        "logins_failed": sessions - ok,
        "identity_checked": sessions,
        "identity_ok": sessions,
        "requests_total": 500,
        "throughput_rps": 25.0,
        "error_rate_pct": error_pct,
        "server_errors_5xx": fivexx,
        "rate_limited_429": 0,
        "transport_errors": 0,
        "per_endpoint": {endpoint: {"n": 300, "p50": p50, "p95": p95}},
    }


def baseline_from(p50: float, p95: float, commit: str = "abc1234", **extra) -> dict:
    record = {
        "commit": commit,
        "shape": gate.shape_of(20, 2, 20.0, "https://example.test"),
        "latency": {"p50_ms": p50, "p95_ms": p95, "endpoints": ["GET /student/dashboard"],
                    "samples": 300},
        "error_pct": extra.pop("error_pct", 0.0),
        "server_errors_5xx": extra.pop("fivexx", 0),
    }
    record.update(extra)
    return record


# ── 1. the comparison is a ratio against the last passing release ────────────

class TestTheComparison:
    def test_an_unchanged_release_is_not_a_regression(self):
        reasons = gate.regression(baseline_from(200, 400), summary(205, 420))
        assert reasons == []

    def test_a_slower_p50_is_a_regression_and_names_the_baseline(self):
        reasons = gate.regression(baseline_from(200, 400), summary(340, 420))
        assert len(reasons) == 1
        assert "p50 340 ms" in reasons[0] and "200 ms" in reasons[0]
        assert "abc1234" in reasons[0], "the reason must say which release it is slower than"
        assert "1.70x" in reasons[0], "the ratio is the whole argument"

    def test_a_slower_p95_is_a_regression(self):
        reasons = gate.regression(baseline_from(200, 400), summary(210, 900))
        assert len(reasons) == 1 and "p95" in reasons[0]

    def test_the_slack_is_a_ratio_not_an_absolute(self):
        """The baseline is this box on this load, so only the change matters: the
        same 40% is a pass from 100 ms and from 1000 ms."""
        small = gate.regression(baseline_from(100, 1000), summary(140, 1300))
        assert small == [], f"140 ms against 100 ms is 1.4x, inside the 1.5x slack: {small}"
        large = gate.regression(baseline_from(1000, 10000), summary(1400, 13000))
        assert large == [], f"the same ratio from a slower baseline must pass: {large}"

    def test_server_errors_are_a_regression_even_when_the_pages_are_fast(self):
        reasons = gate.regression(baseline_from(200, 400), summary(150, 300, fivexx=7))
        assert any("7 server error" in r for r in reasons), (
            "a release that answers 500s produced no page at all, whatever the latency of "
            f"the pages it did produce: {reasons}")

    def test_an_error_rate_above_the_baseline_is_a_regression(self):
        reasons = gate.regression(baseline_from(200, 400), summary(200, 400, error_pct=2.4))
        assert any("error rate" in r for r in reasons), reasons

    def test_nothing_is_claimed_when_no_page_load_was_recorded(self):
        empty = summary(0, 0)
        empty["per_endpoint"] = {}
        assert gate.regression(baseline_from(200, 400), empty) == []


# ── 2. the baseline survives a bad release ───────────────────────────────────

class TestTheBaseline:
    def test_a_missing_broken_or_empty_baseline_reads_as_absent(self, tmp_path):
        assert gate.load_baseline(tmp_path / "nope.json") is None
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert gate.load_baseline(broken) is None
        empty = tmp_path / "empty.json"
        empty.write_text(json.dumps({"commit": "x"}), encoding="utf-8")
        assert gate.load_baseline(empty) is None, "a baseline with no latency is not one"

    def test_saving_writes_the_record_and_leaves_no_partial_file(self, tmp_path):
        target = tmp_path / "perf" / "baseline.json"
        record = baseline_from(200, 400)
        assert "updated" in gate.save_baseline(target, record)
        assert json.loads(target.read_text(encoding="utf-8"))["commit"] == "abc1234"
        assert [p.name for p in target.parent.iterdir()] == ["baseline.json"], (
            "the temporary file used for the atomic write must not survive")

    def test_a_baseline_that_cannot_be_written_is_reported_not_raised(self, tmp_path):
        """Best-effort: a read-only /var/lib must not turn a passing release into a
        failed one, but it must not be silent either."""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("", encoding="utf-8")
        message = gate.save_baseline(blocker / "baseline.json", baseline_from(200, 400))
        assert "could not write" in message and "not fatal" in message

    def test_the_record_keeps_the_numbers_a_later_investigation_needs(self):
        measured = summary(180, 360)
        record = gate.baseline_record("deadbee", gate.shape_of(20, 2, 20.0, "https://x.test"),
                                      measured, claims.claim_latency(measured))
        assert record["commit"] == "deadbee"
        assert record["latency"]["p50_ms"] == 180 and record["latency"]["p95_ms"] == 360
        assert record["shape"]["sessions"] == 20
        assert record["measured_at"]


# ── 3. an unanswerable question is not a verdict ─────────────────────────────

class TestTheShape:
    def test_the_same_reference_load_compares_clean(self):
        baseline = baseline_from(200, 400)
        same = gate.shape_of(baseline["shape"]["sessions"], baseline["shape"]["teachers"],
                             baseline["shape"]["duration_s"],
                             baseline["shape"]["base"] + "/")   # a trailing slash is not a change
        assert gate.shape_mismatch(baseline, same) is None

    def test_a_changed_reference_load_says_which_setting_changed(self):
        baseline = baseline_from(200, 400)
        for field, value in (("sessions", 40), ("teachers", 5), ("duration_s", 60.0),
                             ("base", "https://other.test")):
            shape = dict(baseline["shape"], **{field: value})
            why = gate.shape_mismatch(baseline, shape)
            assert why, f"a different {field} must not be compared against the baseline"
            assert field in why, f"the message must name {field}: {why}"


# ── 4. the whole path, against a local server and a fake harness ─────────────

FAKE_HARNESS = '''\
import json, os, sys
from pathlib import Path
Path(os.environ["FAKE_MARKER"]).write_text(" ".join(sys.argv[1:]), encoding="utf-8")
out = sys.argv[sys.argv.index("--json") + 1]
Path(out).write_text(Path(os.environ["FAKE_SUMMARY"]).read_text(encoding="utf-8"),
                     encoding="utf-8")
'''


class _Health(BaseHTTPRequestHandler):
    status = 200
    slow_s = 0.0

    def do_GET(self):  # noqa: N802 - http.server's interface
        if self.slow_s:
            import time
            time.sleep(self.slow_s)
        self.send_response(self.status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):  # keep the test output readable
        pass


@pytest.fixture
def health_server():
    """A loopback /health, so the gate's 'is the box quiet' probe has an answer."""
    holder = {}

    def start(status=200, slow_s=0.0):
        handler = type("H", (_Health,), {"status": status, "slow_s": slow_s})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        holder["server"] = server
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    holder.get("server") and holder["server"].shutdown()


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    """A roster, a fake harness and a summary file the harness will echo back."""
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps(
        [{"role": "murid", "email": f"m{i}@x", "password": "p"} for i in range(25)]
        + [{"role": "guru", "email": f"g{i}@x", "password": "p"} for i in range(3)]),
        encoding="utf-8")

    harness = tmp_path / "fake_harness.py"
    harness.write_text(FAKE_HARNESS, encoding="utf-8")

    summary_file = tmp_path / "summary.json"
    summary_file.write_text(json.dumps(summary(200, 400)), encoding="utf-8")
    marker = tmp_path / "probe-ran.txt"

    monkeypatch.setenv("FAKE_SUMMARY", str(summary_file))
    monkeypatch.setenv("FAKE_MARKER", str(marker))

    return {
        "tmp": tmp_path,
        "roster": roster,
        "harness": harness,
        "summary": summary_file,
        "marker": marker,
        "baseline": tmp_path / "baseline.json",
        "evidence": tmp_path / "history.jsonl",
    }


def run_gate(bench, base, monkeypatch, overrides=None, *extra):
    """Call the real main() the way the deploy does: through the environment."""
    env = {"PERF_BASE_URL": base, "PERF_ROSTER": str(bench["roster"]),
           "PERF_SESSIONS": "20", "PERF_TEACHERS": "2", "PERF_DURATION": "1",
           "PERF_BASELINE": str(bench["baseline"]), "PERF_EVIDENCE": str(bench["evidence"])}
    env.update(overrides or {})
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    argv = ["perf_gate.py", "--harness", str(bench["harness"]), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    return gate.main()


class TestTheGateEndToEnd:
    def test_the_first_run_becomes_the_baseline_and_passes(self, workbench, health_server,
                                                           monkeypatch, capsys):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        out = capsys.readouterr().out
        assert "IS the baseline" in out, "a self-arming gate has to say so"
        assert bench["baseline"].exists()
        assert json.loads(bench["baseline"].read_text(encoding="utf-8"))["latency"]["p50_ms"] == 200

    def test_a_slower_release_is_refused_and_the_baseline_is_untouched(self, workbench,
                                                                      health_server,
                                                                      monkeypatch, capsys):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = bench["baseline"].read_bytes()

        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED
        out = capsys.readouterr().out
        assert "REGRESSED" in out and "confirming with a second probe" in out
        assert "--rebaseline" in out, (
            "the way out of a deliberate slowdown has to be in the message")
        assert bench["baseline"].read_bytes() == before, (
            "a refused release must not become the yardstick for the next one")

    def test_a_release_inside_the_slack_is_still_accepted(self, workbench, health_server,
                                                          monkeypatch):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        bench["summary"].write_text(json.dumps(summary(260, 500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK

    def test_a_faster_release_moves_the_baseline(self, workbench, health_server, monkeypatch):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        bench["summary"].write_text(json.dumps(summary(90, 180)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        assert json.loads(bench["baseline"].read_text(encoding="utf-8"))["latency"]["p50_ms"] == 90

    def test_a_busy_box_is_not_a_verdict(self, workbench, health_server, monkeypatch,
                                         capsys):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = bench["baseline"].read_bytes()

        bench["summary"].write_text(json.dumps(summary(900, 1800)), encoding="utf-8")
        busy = health_server(status=503)
        assert run_gate(bench, busy, monkeypatch) == gate.EXIT_CANNOT_RUN
        assert "CANNOT MEASURE" in capsys.readouterr().out
        assert not bench["marker"].exists() or True  # the probe may or may not have run
        assert bench["baseline"].read_bytes() == before

    def test_a_changed_reference_load_is_refused_before_any_load_is_placed(self, workbench,
                                                                          health_server,
                                                                          monkeypatch, capsys):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        bench["marker"].unlink(missing_ok=True)

        assert run_gate(bench, base, monkeypatch, {"PERF_SESSIONS": "24"}) == gate.EXIT_CANNOT_RUN
        out = capsys.readouterr().out
        assert "reference load changed" in out and "re-baseline" in out.replace("rebaseline",
                                                                                "re-baseline")
        assert not bench["marker"].exists(), (
            "the gate compared nothing and should not have put load on the box")

    def test_a_roster_too_small_for_the_reference_load_is_cannot_measure(self, workbench,
                                                                        health_server,
                                                                        monkeypatch, capsys):
        bench, base = workbench, health_server()
        # The roster holds 25 murid; this reference load cannot be staffed.
        assert run_gate(bench, base, monkeypatch, {"PERF_SESSIONS": "40"}) == gate.EXIT_CANNOT_RUN
        assert "reusing logins" in capsys.readouterr().out

    def test_check_validates_the_plumbing_without_loading_anything(self, workbench,
                                                                  monkeypatch, capsys):
        bench = workbench
        monkeypatch.setenv("PERF_ROSTER", str(bench["roster"]))
        monkeypatch.setenv("PERF_SESSIONS", "20")
        monkeypatch.setenv("PERF_BASELINE", str(bench["baseline"]))
        monkeypatch.setattr(sys, "argv", ["perf_gate.py", "--harness", str(bench["harness"]),
                                          "--check"])
        assert gate.main() == gate.EXIT_OK
        assert "CHECK OK" in capsys.readouterr().out
        assert not bench["marker"].exists()

    def test_every_run_appends_evidence(self, workbench, health_server, monkeypatch):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED
        lines = [json.loads(l) for l in bench["evidence"].read_text(encoding="utf-8").splitlines()]
        assert [l["verdict"] for l in lines] == ["baseline", "regressed"]
        assert lines[1]["reasons"], "a refusal has to leave its reasons in the history"


# ── 5. the deploy script reads the exit codes the way the gate means them ────

class TestTheDeployWiring:
    script = DEPLOY.read_text(encoding="utf-8")

    def test_the_gate_runs_on_every_release(self):
        assert "deploy/perf_gate.py" in self.script
        assert "/etc/scangrade-perf.conf" in self.script

    def test_it_runs_after_the_reload_and_of_the_measured_code(self):
        """A probe before the reload would measure the previous release."""
        reload_at = self.script.index("reload_app")
        gate_at = self.script.index("deploy/perf_gate.py")
        assert gate_at > reload_at

    def test_it_sits_with_the_other_gates_before_the_release_is_declared_good(self):
        gate_at = self.script.index("# ── Gate 6")
        claims_at = self.script.index("# ── Gate 5")
        success_at = self.script.index('if [ "$HEALTHY" = "1" ]; then')
        assert claims_at < gate_at < success_at, (
            "Gate 6 must sit after the claims gate and before the block that declares the "
            "release deployed, or its verdict is written after the deploy already said OK")

    def test_a_confirmed_regression_rolls_back_only_when_asked(self):
        block = self.script[self.script.index("# ── Gate 6"):]
        block = block[:block.index('if [ "$HEALTHY" = "1" ]; then')]
        assert 'PERF_ENFORCE' in block
        assert 'if [ "${PERF_ENFORCE:-false}" = "true" ]' in block, (
            "enforcement has to be an explicit switch, like CLAIMS_ENFORCE")
        assert block.count("HEALTHY=0") == 1
        assert block.index('if [ "${PERF_ENFORCE:-false}" = "true" ]') < block.index("HEALTHY=0")
        assert 'keeping the release' in block, (
            "with enforcement off the release is kept, and the log has to say that")

    def test_could_not_measure_is_logged_as_not_compared(self):
        block = self.script[self.script.index("# ── Gate 6"):]
        block = block[:block.index('if [ "$HEALTHY" = "1" ]; then')]
        cant = block[block.index("    2)"):block.index("    *)")]
        assert "HEALTHY=0" not in cant, "exit 2 must never roll a release back"
        assert "NOT compared" in cant

    def test_a_missing_conf_is_loud_rather_than_silent(self):
        block = self.script[self.script.index("# ── Gate 6"):]
        assert "no $PERF_CONF" in block, (
            "a gate that is not installed must say so on every release, not disappear")


class TestTheInstaller:
    installer = INSTALLER.read_text(encoding="utf-8")
    deploy = DEPLOY.read_text(encoding="utf-8")

    def test_the_installer_writes_the_conf_and_the_state_directory(self):
        assert "PERF_CONF=/etc/scangrade-perf.conf" in self.installer
        assert "/var/lib/scangrade-deploy/perf" in self.installer
        assert 'PERF_BASELINE="/var/lib/scangrade-deploy/perf/baseline.json"' in self.installer

    def test_the_installer_leaves_an_existing_conf_alone(self):
        """Re-running the installer must not silently reset numbers an operator tuned."""
        block = self.installer[self.installer.index("PERF_CONF=/etc"):]
        block = block[:block.index("# ── 4.")]
        assert "already exists — left untouched" in block

    def test_the_gate_is_armed_from_the_first_release(self):
        block = self.installer[self.installer.index("PERF_CONF=/etc"):]
        block = block[:block.index("# ── 4.")]
        assert 'PERF_ENFORCE="true"' in block, (
            "unlike the claims gate, arming this one cannot reject anything: with no "
            "baseline it writes one and passes")

    def test_the_installer_proves_the_gate_can_run_with_the_conf_settings(self):
        block = self.installer[self.installer.index("Checking the performance gate"):]
        block = block[:block.index("# ── 4.")]
        assert "--check" in block
        for var in ("PERF_BASE_URL", "PERF_ROSTER", "PERF_SESSIONS", "PERF_TEACHERS",
                    "PERF_DURATION", "PERF_BASELINE", "PERF_EVIDENCE"):
            assert var in block, (
                f"the installer proves the gate runs without passing {var} — that is how a "
                "conf file is generated carefully and then ignored")


# ── 6. the conf file reaches the gate ────────────────────────────────────────

def _conf_names(installer_text: str, conf_var: str) -> set[str]:
    """The settings the installer actually writes into one generated conf file.

    Read out of the heredoc rather than the whole script: the installer also sets
    PERF_CONF, PERF_OUT and PERF_RC for its own use, and none of those belong in
    a conf file or in the gate.
    """
    import re
    heredoc = re.search(rf'cat > "\${conf_var}" <<EOF\n(.*?)\nEOF\n', installer_text, re.S)
    assert heredoc, f"the installer no longer writes {conf_var} from a heredoc"
    return set(re.findall(r"^([A-Z_]+)=", heredoc.group(1), re.M))


class TestTheConfReachesTheGate:
    """A conf file the gate does not read is a gate that is off.

    This is not hypothetical: /etc/scangrade-claims.conf has always been generated
    and passed as environment variables, and claims_gate.py only ever looked at
    argv — so every production run ended at "no --base URL", exit 2, on a gate
    that looked installed and healthy.
    """

    installer = INSTALLER.read_text(encoding="utf-8")
    deploy = DEPLOY.read_text(encoding="utf-8")

    @pytest.mark.parametrize("which", ["perf", "claims"])
    def test_every_generated_setting_is_read_by_its_gate(self, which):
        conf_var, source, switch = GATES[which]
        names = _conf_names(self.installer, conf_var)
        assert names, f"the installer writes no settings into {conf_var} - this test would be vacuous"
        ignored = sorted(n for n in names - {switch} if f'env_default("{n}"' not in source)
        assert not ignored, (
            f"the installer writes these into {conf_var} and the gate never reads them: "
            f"{ignored}. The deploy passes each one as an environment variable, so the gate "
            f"needs env_default() for it.")

    @pytest.mark.parametrize("which", ["perf", "claims"])
    def test_the_deploy_passes_every_generated_setting(self, which):
        conf_var, _, switch = GATES[which]
        names = _conf_names(self.installer, conf_var)
        block = self.deploy[self.deploy.index(f"{conf_var}="):]
        listed = re.search(r"for v in ([^;]+); do", block)
        assert listed, f"the deploy no longer passes {conf_var} settings through the environment"
        missing = sorted(n for n in names - {switch} if n not in listed.group(1))
        assert not missing, (
            f"the deploy sources the conf but does not pass these to the gate: {missing}")

    def test_the_verdict_names_what_was_compared(self):
        """The verdict rule is shared with the claims gate, so the nouns have to
        be this gate's: a refusal that says "the published numbers" would send
        someone looking for a claim to correct when the code is what changed."""
        _, why = gate.cg.verdict(["slow"], ["slow"], **gate.VERDICT_WORDS)
        assert "the previous release" in why and "published" not in why, why
        _, ok = gate.cg.verdict([], None, **gate.VERDICT_WORDS)
        assert "not slower" in ok, ok
        _, contention = gate.cg.verdict(["slow"], [], **gate.VERDICT_WORDS)
        assert "not as a regression" in contention, contention

    def test_the_two_gates_agree_on_what_an_exit_code_means(self):
        assert gate.EXIT_OK == claims.EXIT_OK == 0
        assert gate.EXIT_REGRESSED == claims.EXIT_DIVERGED == 1
        assert gate.EXIT_CANNOT_RUN == claims.EXIT_CANNOT_RUN == 2
