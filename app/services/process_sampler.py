"""Where the box's CPU actually goes, split by the process that spent it.

Why this exists
---------------
The capacity work put numbers on this box: one vCPU, user time 90-93% while
loaded, a ceiling of roughly 20 requests per second, and so a cost of some tens
of milliseconds of CPU per request. None of those can say *which process* spends
it. `vmstat` is box-wide. `/metrics` is the app's own view. The box's own
loopback sampler (`docs/measurements/rung-500-vps-samples.txt`) records load and
memory, not per-process CPU. And the tool that normally answers this —
`pidstat`, `top` over SSH — is exactly what is unavailable here.

So the box reports it about itself, over the one channel that always answers: an
authenticated HTTP read.

Cumulative seconds, never a percentage
--------------------------------------
Every reading is **cumulative CPU seconds** for that process, with its start
time. A percentage sampled once is meaningless: `psutil` derives one only by
comparing against that same object's previous call, so a fresh `Process` per
request would report 0% forever. The interval belongs to the *reader* — take two
samples, subtract, divide by the elapsed wall time, and the CPU each process
spent in between is a fact rather than an estimate.

That is also why this never sleeps. `psutil.cpu_percent(interval=0.1)` blocks
for a tenth of a second *inside a request*, which would make the instrument part
of what it measures. The start time travels with the counter so a restart is
visible rather than arithmetic: gunicorn's graceful reload retires workers and
starts new ones, and a reset counter must not read as a negative delta or as a
process that suddenly used nothing.

The role comes from the command line as well as the name
--------------------------------------------------------
`comm` is truncated to 15 characters and holds whatever the process renamed
itself to. Celery sets its title through `setproctitle` when that is installed,
so the same worker is `celery` on one box and `python3` on another. The role is
therefore decided from the name first and the command line second. A process
matching neither is `other`, and nothing is dropped: the attribution is meant to
account for the whole box, not only the parts already known about.

A process that cannot be read is skipped, never zero-filled
-----------------------------------------------------------
A process can exit between the enumeration and the read, and `/proc` may refuse a
field for another user's process. A zero for such a process reads as "this one
used no CPU", which is the one answer this must never invent — the same rule
`deploy_status_service` follows. So the reading is simply left out.

The share no process owns
-------------------------
Summing `cpu_seconds` over every process gives user time, not wall time: the TCP
stack, softirq work and the scheduler run in no process's account. On a box whose
whole question is "where does the CPU go", that remainder has to be visible, so
every sample also carries the kernel's own tally from `/proc/stat`. It costs one
file read and no extra request. When `/proc/stat` cannot be read the family is
left out rather than reported as zero, because a zero there reads as "the box was
idle" — the one answer that must never be invented.

Why it is not part of `/metrics`
--------------------------------
That endpoint answers any signed-in user, and a process inventory is not
something a student needs. Enumerating processes is a super-admin read, so it has
its own route and its own guard rather than widening an existing one.
"""

from __future__ import annotations

import time

#: `comm` (or the process title) -> the component a reader reasons about.
NAME_ROLES = {
    "nginx": "nginx",
    "gunicorn": "gunicorn",
    "redis-server": "redis",
    "redis": "redis",
    "celery": "celery",
    "celeryd": "celery",
}

#: Checked in order against the command line, for a process that renamed itself
#: past what `comm` can hold.
CMDLINE_ROLES = (
    ("gunicorn", "gunicorn"),
    ("nginx", "nginx"),
    ("redis-server", "redis"),
    ("celery", "celery"),
)

OTHER = "other"


def role_of(comm: str, cmdline: str = "") -> str:
    """The component this process belongs to, from its name, then its command line."""
    name = (comm or "").strip().lower()
    if name in NAME_ROLES:
        return NAME_ROLES[name]
    line = (cmdline or "").lower()
    for needle, role in CMDLINE_ROLES:
        if needle in line:
            return role
    return OTHER


def collect(*, process_iter=None) -> list[dict]:
    """Every process this user can read, as cumulative readings.

    Never raises: a process that exits mid-enumeration, or a field `/proc`
    refuses to hand over, is left out of the reading rather than reported as a
    zero. `process_iter` is injectable so a test can present such a process
    without needing one to misbehave on the machine running the test.
    """
    import psutil

    rows = psutil.process_iter if process_iter is None else process_iter
    readings: list[dict] = []
    for proc in rows(["pid", "name"]):
        try:
            comm = proc.info.get("name") or ""
            try:
                cmdline = " ".join(proc.cmdline())
            except psutil.Error:
                cmdline = ""
            # oneshot() batches the /proc reads into one open per process.
            with proc.oneshot():
                cpu = proc.cpu_times()
                rss = proc.memory_info().rss
                threads = proc.num_threads()
                created = proc.create_time()
            readings.append({
                "pid": proc.info.get("pid"),
                "comm": comm,
                "role": role_of(comm, cmdline),
                "cpu_seconds": float(cpu.user) + float(cpu.system),
                "rss_bytes": int(rss),
                "threads": int(threads),
                "create_time": float(created),
            })
        except psutil.Error:
            continue
    return readings


#: `/proc/stat`'s cpu line, in order. The last two (`guest`, `guest_nice`) are
#: already counted inside `user`/`nice`, so including them would double-count.
BOX_CPU_MODES = ("user", "nice", "system", "idle", "iowait", "irq",
                 "softirq", "steal")

#: USER_HZ. `/proc/stat` counts in jiffies, and 100 is the rate the kernel is
#: built with on this box; anything else would scale every figure silently.
USER_HZ = 100.0


def box_cpu() -> dict:
    """The kernel's own CPU tally in seconds, or {} when it cannot be read.

    Empty rather than zeroed: a zero here reads as an idle box.
    """
    try:
        with open("/proc/stat", encoding="ascii") as handle:
            first = handle.readline()
    except OSError:
        return {}
    parts = first.split()
    if not parts or parts[0] != "cpu":
        return {}
    values: dict[str, float] = {}
    for mode, raw in zip(BOX_CPU_MODES, parts[1:]):
        try:
            values[mode] = int(raw) / USER_HZ
        except ValueError:
            return {}
    values["total"] = sum(values.values())
    # Idle and waiting are not the box working; everything else is the box busy,
    # including the irq/softirq and system time no process is charged for.
    values["busy"] = values["total"] - values.get("idle", 0.0) - values.get("iowait", 0.0)
    return values


def _quote(value) -> str:
    """A Prometheus label value: backslash, quote and newline escaped."""
    return (str(value).replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n"))


def _labels(reading: dict) -> str:
    return (f'role="{_quote(reading.get("role", OTHER))}",'
            f'comm="{_quote(reading.get("comm", ""))}",'
            f'pid="{_quote(reading.get("pid", ""))}"')


def render(readings, *, cpu_count=None, now=None, box=None) -> str:
    """The Prometheus text a sampler parses.

    Cumulative, so the deltas are the reader's; `scangrade_role_cpu_seconds`
    sums each role so a reader does not have to do that arithmetic itself, and
    `scangrade_box_cpu_seconds` carries the kernel's own tally so the share no
    process owns is visible. `box={}` renders no box family at all, which is what
    a platform without `/proc/stat` gets.
    """
    if cpu_count is None:
        import psutil
        cpu_count = psutil.cpu_count()
    if now is None:
        now = time.time()
    if box is None:
        box = box_cpu()

    ordered = sorted(readings, key=lambda r: -float(r.get("cpu_seconds") or 0.0))

    totals: dict[str, float] = {}
    for reading in ordered:
        role = reading.get("role", OTHER)
        totals[role] = totals.get(role, 0.0) + float(reading.get("cpu_seconds") or 0.0)

    out = [
        "# ScanGrade per-process CPU sample.",
        "# cpu_seconds is CUMULATIVE since the process started: take two samples,",
        "# subtract, and divide by the elapsed wall time to get that process's CPU.",
        "# A restarted process shows a smaller counter with a newer create_time.",
        "# HELP scangrade_sample_epoch_seconds When the box took this reading",
        "# TYPE scangrade_sample_epoch_seconds gauge",
        f"scangrade_sample_epoch_seconds {float(now):.6f}",
        "# HELP scangrade_box_cpu_count Logical CPUs on the box",
        "# TYPE scangrade_box_cpu_count gauge",
        f"scangrade_box_cpu_count {int(cpu_count)}",
    ]
    if box:
        out += [
            "# HELP scangrade_box_cpu_seconds The kernel's own CPU tally, by mode",
            "# TYPE scangrade_box_cpu_seconds counter",
        ]
        for mode in BOX_CPU_MODES:
            if mode in box:
                out.append(f'scangrade_box_cpu_seconds{{mode="{mode}"}} {float(box[mode]):.2f}')
        for key in ("busy", "total"):
            if key in box:
                out.append(f'scangrade_box_cpu_seconds_{key} {float(box[key]):.2f}')
    out += [
        "# HELP scangrade_process_cpu_seconds Cumulative CPU seconds (user+system)",
        "# TYPE scangrade_process_cpu_seconds counter",
    ]
    for reading in ordered:
        out.append(f"scangrade_process_cpu_seconds{{{_labels(reading)}}} "
                   f"{float(reading.get('cpu_seconds') or 0.0):.6f}")

    out += [
        "# HELP scangrade_role_cpu_seconds Cumulative CPU seconds summed by role",
        "# TYPE scangrade_role_cpu_seconds counter",
    ]
    for role in sorted(totals):
        out.append(f'scangrade_role_cpu_seconds{{role="{_quote(role)}"}} {totals[role]:.6f}')

    out += [
        "# HELP scangrade_process_create_time_seconds When the process started",
        "# TYPE scangrade_process_create_time_seconds gauge",
    ]
    for reading in ordered:
        out.append(f"scangrade_process_create_time_seconds{{{_labels(reading)}}} "
                   f"{float(reading.get('create_time') or 0.0):.6f}")

    out += [
        "# HELP scangrade_process_rss_bytes Resident memory of the process",
        "# TYPE scangrade_process_rss_bytes gauge",
    ]
    for reading in ordered:
        out.append(f"scangrade_process_rss_bytes{{{_labels(reading)}}} "
                   f"{int(reading.get('rss_bytes') or 0)}")

    out += [
        "# HELP scangrade_process_threads Threads in the process",
        "# TYPE scangrade_process_threads gauge",
    ]
    for reading in ordered:
        out.append(f"scangrade_process_threads{{{_labels(reading)}}} "
                   f"{int(reading.get('threads') or 0)}")

    out.append("")
    return "\n".join(out)


def sample_text() -> str:
    """Collect and render in one call — what the route serves."""
    return render(collect())
