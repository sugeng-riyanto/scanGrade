"""The manual staging run is the only place the media rows can be measured.

The media rows and seven webhook probes need a deployed host, a staging
database with four synthetic identities and a staging LiveKit project — none of
which a CI job can invent. `.github/workflows/scanmeet-staging-media.yml` is how
they are exercised from GitHub: the owner stores the eight values as environment
secrets, presses Run, and the evidence lands in the job summary and an artefact.

That makes the workflow itself load-bearing, so its properties are asserted
rather than trusted:

* it cannot run by itself (dispatch only) and cannot reach the live site —
  `--allow-production` is the escape hatch the driver warns about, and a staging
  run that needed it would be measuring production;
* it fails **by name** when a value is missing, because a skipped or
  `continue-on-error` job is a green tick over an examination nobody measured;
* it reports the pair of exit codes, not the fact that steps ran: 0 and 0 only.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "scanmeet-staging-media.yml"

SECRETS = (
    "SCANMEET_STAGING_URL", "SCANMEET_EXAM_ID", "SCANMEET_TEACHER",
    "SCANMEET_STUDENT", "SCANMEET_STUDENT2", "SCANMEET_OUTSIDER",
    "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
)


@pytest.fixture(scope="module")
def doc():
    yaml = pytest.importorskip("yaml")
    assert WORKFLOW.is_file(), WORKFLOW
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def text():
    """The workflow's commands, with its explanatory comments removed.

    The file explains why `continue-on-error`, `--allow-production` and
    `requirements-live.txt` are absent, and a rule that read the prose would
    refuse the explanation — the same shape the documentation guards hit.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _job(doc):
    return doc["jobs"]["measure"]


def _steps(doc):
    return _job(doc)["steps"]


def _step(doc, name):
    for step in _steps(doc):
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def test_it_runs_only_when_a_person_asks(doc):
    # YAML 1.1 reads a bare `on` as a boolean, so both spellings are asked for.
    triggers = doc.get("on", doc.get(True))
    assert list(triggers) == ["workflow_dispatch"], (
        "a real-environment run must never start by itself: a push-triggered job "
        "would either fail on every push or need production credentials")


def test_it_runs_in_the_protected_environment_that_holds_the_values(doc):
    job = _job(doc)
    assert job["environment"] == "scanmeet-staging"
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] <= 45


def test_every_value_it_needs_is_read_from_that_environment(doc):
    env = _job(doc)["env"]
    for name in SECRETS:
        assert env.get(name) == "${{ secrets.%s }}" % name, name


def test_a_missing_value_fails_by_name_instead_of_skipping(doc, text):
    guard = _step(doc, "refuse to measure with a missing value")["run"]
    for name in SECRETS:
        assert name in guard, f"the guard does not name {name}"
    assert "is empty" in guard
    assert "exit 1" in guard
    assert "continue-on-error" not in text, (
        "a step allowed to fail is a step whose result nobody reads")
    # `|| true` is a legitimate way to probe for an optional binary (the browser
    # lookup uses it, exactly as this repository's other gates do); silencing a
    # *gate* is not, so the rule reads the lines that run one.
    silenced = [line for line in text.splitlines()
                if "deploy." in line and "|| true" in line]
    assert not silenced, silenced


def test_the_driver_cannot_be_pointed_at_production(doc, text):
    assert "--allow-production" not in text, (
        "the escape hatch measures the live site; a staging run must not carry it")
    media = _step(doc, "thirteen media rows")["run"]
    assert "--signoff" in media and "--json-out reading.json" in media
    for flag in ("--url", "--exam", "--teacher", "--student", "--student2",
                 "--outsider"):
        assert flag in media, flag


def test_both_halves_are_measured_on_the_same_commit(doc):
    text = WORKFLOW.read_text(encoding="utf-8")
    webhook = _step(doc, "seven webhook probes")["run"]
    assert "deploy.scanmeet_webhook_probe" in webhook
    assert "deploy.scanmeet_media_gate" in _step(doc, "thirteen media rows")["run"]
    assert "git rev-parse HEAD" in text, (
        "the evidence has to name the commit it measured")


def test_the_verdict_is_the_exit_codes_not_the_steps_running(doc):
    verdict = _step(doc, "fail unless both halves were measured")["run"]
    assert 'webhook" != "0"' in verdict or '"${webhook:-}"' in verdict
    assert '"${media:-}"' in verdict
    assert "exit 1" in verdict
    assert _step(doc, "fail unless both halves were measured").get("if") == "always()"


def test_the_sheet_and_the_reading_survive_the_run(doc):
    record = _step(doc, "record the sheet and the readings")
    assert record.get("if") == "always()"
    assert "GITHUB_STEP_SUMMARY" in record["run"]
    upload = _step(doc, "keep the machine-readable reading")
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload.get("if") == "always()"
    for path in ("reading.json", "webhook.txt", "media.txt"):
        assert path in upload["with"]["path"]


def test_it_never_deploys_and_never_pushes(doc, text):
    for forbidden in ("git push", "deploy.sh", "scp ", "ssh ", "systemctl"):
        assert forbidden not in text, forbidden


def test_the_files_it_names_are_in_this_checkout(text):
    assert "requirements.txt" in text
    assert (ROOT / "deploy" / "scanmeet_media_gate.py").is_file()
    assert (ROOT / "deploy" / "scanmeet_webhook_probe.py").is_file()


def test_it_installs_the_checkout_and_not_the_server_s_sdk(text):
    """`requirements-live.txt` pins a PyJWT that conflicts with `redis` here.

    Neither tool the workflow runs uses the LiveKit server SDK — the driver
    speaks CDP and HTTP — so installing it would fail the run for a package
    nothing imports.
    """
    assert "requirements-live.txt" not in text
