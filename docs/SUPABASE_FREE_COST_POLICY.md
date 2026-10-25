# Supabase Free cost policy — ScanGrade

**Goal:** keep Supabase subscription on **Free** while protecting live exams. This policy does not change billing, quota settings, database data, or authentication. Free service may be rate-limited or paused when its quotas are exceeded; a green CI build cannot guarantee a zero-cost production workload.

## Release rules

1. **Never auto-upgrade billing.** Owner must explicitly authorize a paid plan in Supabase Dashboard. Check organization-wide Usage and Billing before onboarding new schools.
2. **No new broad PostgREST reads.** Shared query module wildcard `select("*")` count is capped by `tests/unit/test_supabase_free_cost_guard.py` at its audited baseline of 16; reduce the baseline as queries are narrowed. Prefer explicit fields, authorization filters, pagination and cheap metadata views. This budget is scoped to the shared module, not a claim that the rest of the repository is fully audited.
3. **Protect autosave correctness first.** No skipped or delayed answer persistence solely to save egress. Use narrow responses (`return=minimal` where safe), keep answer/canvas integrity, and preserve exam timer/submit semantics. `test_crud_egress.py` must remain in the full CI unit suite.
4. **Do not fetch large question/canvas/media fields for exam lists.** Load full exam content only when a student is authorized to take that exam; never expose answer keys to students.
5. **Avoid polling amplification.** Use bounded intervals and cache only non-sensitive, stable aggregates; never cross-cache school/student-specific data or bypass row-level security.
6. **No silent destructive quota response.** If Supabase returns quota/HTTP 402 or repeated 429/5xx, make the failure visible, do not claim 'saved', and retain the local answer recovery path. Do not migrate or prune a live production database while exams run.
7. **Separate production and staging.** No production user data, database credentials or LiveKit secrets on staging. Synthetic fixtures only. A new staging Supabase project shares Free organization quotas when in the same organization.
8. **Test at realistic load before promising 500 concurrent pupils.** Measure request/response bytes, per-student query counts, autosave success, latency, concurrency and Supabase Usage. Never run multi-browser load on the 1 GB production VPS.

## Usage thresholds (manual until dashboard API is available)

On every release and at least weekly during pilots, the operator checks Supabase Dashboard → Organization → Usage and Billing, including project DB sizes and the billing cycle:

| Organization egress share of published Free quota | Action |
| --- | --- |
| 60% | Review the largest PostgREST requests and projected days remaining |
| 80% | Stop nonessential staging traffic and prioritize query/polling fixes |
| 90% | Freeze new onboarding/load tests and assess alternative capacity |
| 100% or quota/restriction warning | Treat production as at-risk; preserve exams, report the exact notice and plan recovery; no automatic paid upgrade |

These are **operational review thresholds**, not automatically enforced account quotas. No in-repo test can read Supabase account usage without authenticated organization access. Never place service-role keys in CI output or public issues.

## Audit starting point

On baseline `main@4e455df1` the shared `app/models/supabase_queries.py` had **16** exact `select("*")` calls. Other modules can still have expensive reads; this check prevents growth in the shared module and preserves existing network-level regression tests. It does not replace a whole-repository audit or dashboard measurements.

Production deployment requires a separate, approved plan; merging a PR targeting `main` can trigger the VPS auto-deploy path. For that reason, this policy goes through CI and PR review first.
