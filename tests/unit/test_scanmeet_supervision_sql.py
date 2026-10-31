"""Run the actual ScanMeet migration/RPCs in an ephemeral PostgreSQL engine.

This checks SQL compilation, constraints and sequential transitions. Supabase
Auth, actual concurrent backend connections and LiveKit require staging signoff.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_supervision_migration_and_transactional_rpcs():
    result = subprocess.run(["node", str(ROOT / "deploy/scanmeet_supervision_sql_check.cjs")],
                            cwd=ROOT, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS supervision SQL assertions" in result.stdout
    assert '"schools":0,"sessions":0' in result.stdout
