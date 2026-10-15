#!/usr/bin/env python
"""Does the *installed* Safe Exam Browser accept the exit key ScanGrade hands out?

Why this exists, and why it is not a unit test
----------------------------------------------
`tests/unit/test_seb_exit_key.py` pins the algorithm against the client's own
unit-test vector. That answers "is our hash the hash SEB computes", and it is worth
having — but on 15 October 2026 a pupil could not leave a real paper, and the same
week a first diagnosis blamed that algorithm and was **wrong**. The lesson is the one
this repository keeps re-learning: a test that assumes the algorithm cannot tell you
the algorithm is right. So the acceptance test runs against the **client's own
compiled code**, on a machine that has SEB installed.

What it does not do: launch SEB. Starting the client takes the whole desktop into a
kiosk, which is not something a probe may do to the machine running it. What it
checks instead is every machine-decidable step of the unlock:

1. our generator mints a key, and the `.seb` we would hand a pupil carries
   ``sha256(key)`` — read back out of the built file, not from the settings we passed;
2. **the installed ``SafeExamBrowser.Configuration.dll`` hashes the key itself and
   returns that same 64-byte string** — SEB's own code, not a description of it;
3. the client's comparison rule (``IsValidQuitPassword``: hash the typed value,
   compare ``OrdinalIgnoreCase``) accepts the key and refuses a different one, so the
   file is not simply accepting anything;
4. the admin password is a different secret, so a supervisor reading one box cannot
   open the other.

    python deploy/seb_quit_probe.py --self-test          # a freshly minted key
    python deploy/seb_quit_probe.py --file a.seb --password ABCD

Exit codes follow the other probes in this directory: 0 every check passed,
1 a check failed (do not ship), 2 nothing could be measured — no SEB on this box, no
PowerShell, or no file to read. **2 is not a pass**, and the reason it is not called
here is that this probe's whole value is being run on a box that has SEB.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: Where SEB for Windows installs itself. The DLL that owns the hashing is in
#: `Application`, not next to the Config Tool — measured on the box this was written
#: on, where pointing at the tool's own folder answered "Cannot find type".
CANDIDATE_DIRS = (
    pathlib.Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "SafeExamBrowser" / "Application",
    pathlib.Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "SafeExamBrowser" / "Application",
    pathlib.Path("/Applications/SafeExamBrowser.app/Contents/MacOS"),
)

CONFIG_DLL = "SafeExamBrowser.Configuration.dll"
CONTRACTS_DLL = "SafeExamBrowser.Configuration.Contracts.dll"
HASH_TYPE = "SafeExamBrowser.Configuration.Cryptography.HashAlgorithm"


def find_config_dll() -> pathlib.Path | None:
    for directory in CANDIDATE_DIRS:
        if (directory / CONFIG_DLL).is_file():
            return directory / CONFIG_DLL
    return None


def _powershell() -> str | None:
    """PowerShell's own path, or ``None`` when this box has no Windows shell.

    `.NET` is what makes this probe possible at all: the client is a .NET
    application, so its hashing method can be called directly instead of
    re-implemented — which is the exact mistake the probe exists to catch.
    """
    for name in ("powershell.exe", "pwsh", "pwsh.exe"):
        from shutil import which
        found = which(name)
        if found:
            return found
    return None


def seb_hash(password: str) -> str | None:
    """``sha256(password)`` as computed by the *installed* SEB, or ``None``.

    The password crosses into the other process through a file rather than argv: a
    real exit key is a secret the operator is about to read aloud, and a command line
    is visible to every process on the machine.
    """
    dll = find_config_dll()
    shell = _powershell()
    if dll is None or shell is None:
        return None
    handle, path = tempfile.mkstemp(prefix="seb-probe-", suffix=".txt")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(password)          # no newline: SEB hashes exactly what it reads
        script = (
            f"$p = [IO.File]::ReadAllText('{path}');"
            f"$c = '{dll.parent / CONTRACTS_DLL}';"
            "if (Test-Path $c) { [Reflection.Assembly]::LoadFrom($c) | Out-Null };"
            f"$a = [Reflection.Assembly]::LoadFrom('{dll}');"
            f"$t = $a.GetType('{HASH_TYPE}');"
            "if (-not $t) { exit 3 };"
            "$h = [Activator]::CreateInstance($t);"
            "Write-Output $h.GenerateHashFor($p)"
        )
        proc = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        if proc.returncode != 0:
            return None
        lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
        return lines[-1] if lines else None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


#: ``ClientResponsibility.IsValidQuitPassword``: hash the typed value, compare with
#: the stored one under ``StringComparison.OrdinalIgnoreCase``.
def seb_accepts(typed: str, stored: str) -> bool:
    computed = seb_hash(typed)
    if computed is None:
        return False
    return str(stored or "").casefold() == computed.casefold()


def _report(checks: list[tuple[str, bool]]) -> int:
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return 0 if all(ok for _label, ok in checks) else 1


def self_test() -> int:
    """Mint a key, build the file a pupil would get, and ask SEB to verify it."""
    from app.services import seb_crypto, seb_service

    quit_password = seb_crypto.mint_password()
    admin_password = seb_crypto.mint_password()
    settings = seb_service.settings_for(
        None,                  # no exam: the switches are not what is under test here
        start_url="https://scangrade.web.id/panduan/seb/berhasil",
        quit_hash=seb_crypto.sha256_hex(quit_password),
        admin_hash=seb_crypto.sha256_hex(admin_password))
    # Read the file back, so the hash under test is the one the *bytes* carry and not
    # the one the settings map was constructed with.
    on_disk = seb_service.decode_seb(seb_service.seb_file_bytes(settings))

    # Plain ASCII in every line, including the truncations: this output is read in a
    # terminal whose codec is not UTF-8 on Windows, and a probe whose own report comes
    # out mojibaked is a probe nobody trusts.
    print(f"key minted by ScanGrade      : {quit_password} "
          f"({len(quit_password)} characters from {seb_crypto.PASSWORD_ALPHABET})")
    print(f"hash in the generated file   : {on_disk['hashedQuitPassword'][:32]}...")
    installed = seb_hash(quit_password)
    if installed is None:
        print("\n  cannot measure: SEB is not installed on this box (or PowerShell is "
              "unavailable),\n  so the client's own code cannot be asked. Exit 2 - "
              "not a pass.")
        return 2
    print(f"hash from the INSTALLED SEB  : {installed[:32]}...\n")

    return _report([
        ("SEB's own hash of the key equals the file's hashedQuitPassword",
         seb_accepts(quit_password, on_disk["hashedQuitPassword"])),
        ("a different key is refused", not seb_accepts("ZZZZ" if quit_password != "ZZZZ" else "YYYY",
                                                       on_disk["hashedQuitPassword"])),
        ("the admin box is a different secret",
         not seb_accepts(quit_password, on_disk["hashedAdminPassword"])
         and seb_accepts(admin_password, on_disk["hashedAdminPassword"])),
    ])


def check_file(path: pathlib.Path, password: str) -> int:
    from app.services import seb_service

    on_disk = seb_service.decode_seb(path.read_bytes())
    stored = on_disk.get("hashedQuitPassword") or ""
    if not stored:
        print(f"  FAIL  {path} carries no hashedQuitPassword at all")
        return 1
    print(f"{path.name}: hashedQuitPassword {stored[:32]}…")
    if seb_hash(password) is None:
        print("\n  cannot measure: SEB is not installed on this box. Exit 2 - not a pass.")
        return 2
    return _report([
        (f"SEB accepts the key that was read to the supervisor ({password})",
         seb_accepts(password, stored)),
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true",
                        help="mint a key, build a .seb, and verify it with the installed SEB")
    parser.add_argument("--file", type=pathlib.Path, help="a .seb file to check")
    parser.add_argument("--password", help="the key that was read out for it")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.file and args.password:
        return check_file(args.file, args.password)
    parser.error("nothing to do: pass --self-test, or --file together with --password")


if __name__ == "__main__":
    sys.exit(main())
