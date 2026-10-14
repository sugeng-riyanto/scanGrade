"""Mutation check for the Gate 2 reason card.

The claim is one sentence — `/super-admin/deploy-status` names *why* the construct
gate refused a release, which variable, or that the environment file this account
cannot open is the cause — and it has five moving parts: the app's own rule (one
list, used by both the refusal and the page), the reader that decides the key, the
measurement of the file, the wiring that hands the reading to the page, and the
card's own gating. Each entry below breaks exactly one of them and demands a red
suite; a survivor is a hole in the guard rather than a curiosity.

Three guard files run **together**, because a mutation that satisfies one by breaking
another is not a passing mutation: `test_gate2_reason.py` owns the reader and the
card, `test_env_and_email.py` owns the rule `Config.validate()` refuses on and the
single reader of an environment file, and `test_deploy_status.py` owns the report
those readings are assembled into (and the page as a whole).

Run:  python deploy/mutation_check.py --run mutate_gate2_reason      (~30 minutes)
      python deploy/mutation_check.py --check                       (anchors only)
"""
from __future__ import annotations

import sys

from tests.mutation.harness import main

GUARDS = ["tests/unit/test_gate2_reason.py", "tests/unit/test_env_and_email.py",
          "tests/unit/test_deploy_status.py"]
NL = chr(10)
CONFIG = "app/config.py"
SERVICE = "app/services/deploy_status_service.py"
TEMPLATE = "app/templates/super_admin/deploy_status.html"

MUTATIONS = [
    # ── the rule, which the refusal and the page must share ─────────────────
    ("the refusal stops using the list the page names",
     CONFIG,
     "        missing = cls.missing_required()",
     '        missing = [v for v in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")\n'
     "                   if not getattr(cls, v)]"),
    ("the required list quietly loses a variable",
     CONFIG,
     '    REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SECRET_KEY")',
     '    REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")'),
    ("the env-file reader names a file the loader never reads",
     CONFIG,
     '        return find_dotenv(usecwd=False) or ""',
     '        return ".env"'),

    # ── the reader: which names, and whether the file explains them ─────────
    ("the names are cut down to the first one",
     SERVICE,
     '    names = [name.strip().strip(".,;:") for name in found.group(1).split(",")]',
     '    names = [name.strip().strip(".,;:") for name in found.group(1).split(",")[:1]]'),
    ("the name filter stops being a name filter",
     SERVICE,
     '    return [name for name in names if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)]',
     "    return names"),
    ("the names are read raw out of the sentence",
     SERVICE,
     '    names = [name.strip().strip(".,;:") for name in found.group(1).split(",")]',
     '    names = [name for name in found.group(1).split(",")]'),
    ("an unreadable file is blamed for a fault that named no variable",
     SERVICE,
     "    if not named:" + NL
     + "        # No variable named: the fault is the app's own, and the file — readable or",
     "    if False:" + NL
     + "        # No variable named: the fault is the app's own, and the file — readable or"),
    ("a readable file is reported as unreadable",
     SERVICE,
     '    if env_file["exists"] and env_file["readable"] is False:',
     '    if env_file["exists"]:'),
    ("the file that could not be opened reads as opened",
     SERVICE,
     '        state["readable"] = False' + NL + '        state["error"] = f"{type(exc).__name__}: {exc}"',
     '        state["readable"] = True' + NL + '        state["error"] = f"{type(exc).__name__}: {exc}"'),
    ("a missing file is reported as one that cannot be read",
     SERVICE,
     '    if not state["exists"]:' + NL + "        return state",
     '    if not state["exists"]:' + NL + '        state["readable"] = False' + NL
     + "        return state"),
    ("the placeholder secret is not recognised",
     SERVICE,
     'PLACEHOLDER_SECRET_RE = re.compile(r"FLASK_SECRET_KEY is unset or still the")',
     'PLACEHOLDER_SECRET_RE = re.compile(r"never matches anything at all")'),
    ("a refusal that named no variable is reported as missing variables",
     SERVICE,
     '        state["reason_key"] = CONSTRUCT_OTHER',
     '        state["reason_key"] = CONSTRUCT_MISSING_VARS'),
    ("a blank line in the record becomes a line the card renders",
     SERVICE,
     '             if isinstance(line, str) and line.strip()]',
     "             if isinstance(line, str)]"),
    ("a quarantine record for another gate reads as a construct refusal",
     SERVICE,
     '    elif (quarantine or {}).get("gate_key") == "app_did_not_construct":',
     '    elif (quarantine or {}).get("gate_key"):'),

    # ── the wiring, and the card's own gating ───────────────────────────────
    ("the report stops measuring the box's own environment file",
     SERVICE,
     "    construct = construct_state(last_stop, quarantine, env_path=env_file)",
     "    construct = construct_state(last_stop, quarantine)"),
    ("the reason block appears whatever the last refusal was",
     TEMPLATE,
     "                    {% if C.relevant %}",
     "                    {% if true %}"),
    ("the card stops saying which record the reason came from",
     TEMPLATE,
     "                        {% if C.because == 'quarantine' %}",
     "                        {% if false %}"),
    ("the app's own list is shown for a refusal that named nothing",
     TEMPLATE,
     "                        {% if C.required and C.reason_key == 'missing_vars' %}",
     "                        {% if C.required %}"),
    ("the card stops naming the variables the probe named",
     TEMPLATE,
     '                            <code class="font-mono text-xs text-rose-700 break-all text-right">{{ C.variables|join(\', \') }}</code>',
     '                            <code class="font-mono text-xs text-rose-700 break-all text-right"></code>'),
    ("an unreadable file reads as readable on the card",
     TEMPLATE,
     "x-text=\"t('TIDAK terbaca oleh akun ini','NOT readable by this account')\"",
     "x-text=\"t('terbaca oleh akun ini','readable by this account')\""),
    ("the remedy for an unreadable file is dropped from one half",
     TEMPLATE,
     "(chmod 640 dengan grup akun itu), atau pindahkan nilainya",
     "atau pindahkan nilainya"),
    ("the card stops saying which file it measured",
     TEMPLATE,
     '                                <code class="font-mono text-xs text-surface-700 break-all">{{ C.env_file.path or \'—\' }}</code>',
     '                                <code class="font-mono text-xs text-surface-700 break-all"></code>'),
]

if __name__ == "__main__":
    sys.exit(main("mutate_gate2_reason", GUARDS, MUTATIONS))
