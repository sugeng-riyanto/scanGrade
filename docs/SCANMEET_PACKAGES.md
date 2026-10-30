# ScanMeet timed packages

Introductory prices per school. Requests never collect payment or grant access automatically. Super Admin verifies capacity and payment before activation; default access remains OFF.

| Package | Price | Minutes per session | Participants including supervisors | Sessions | Validity |
| --- | ---: | ---: | ---: | ---: | --- |
| Trial | Rp0 | 30 | 5 | 3 | 7 days |
| Kelas | Rp149,000 | 60 | 10 | 5 | 30 days |
| Sekolah | Rp399,000 | 120 | 35 | 3 | 30 days |

Trial is once per school. Archiving keeps trial history. The catalog in `app/services/scanmeet_packages.py` is authoritative for the offer page, administration and activation. Prices are introductory recommendations, not a claim of mathematically optimal profit. Review actual traffic and supplier invoices before expanding availability.

## School and operator workflow

Authenticated users can view `/scanmeet`. Teachers and school managers can submit one pending request for their own school. Pupils see the offer and ask their teacher. A request never changes a license. Super Admin reviews, approves or declines requests from `/super-admin/scanmeet-schools`; search accepts the eight-digit NPSN or a school name.

Activation replaces the previous allowance and requires confirmation. Extending the same unexpired plan adds its sessions and validity while preserving usage. After expiry, extension starts a fresh allowance. Paid activation and extension require explicit payment verification. Existing live rooms must end before replacement. Revocation and archive disable new access and queue media cleanup; archive preserves history instead of deleting school/exam records.

Forms use CSRF protection and optimistic version checks. A session reservation updates the license version so a stale administration form cannot erase usage. Requests and licenses remain private to service-role server handlers. School identity, price and limits from a browser are never trusted.

## Session enforcement

A PostgreSQL trigger locks the school license and reserves one session atomically at logical room creation. A duplicate active room insertion rolls back its reservation. Reconnects to that room do not reserve another session. A failed media connection after room creation still consumes the session; the customer should contact the operator for review. No automatic refund is promised.

The stored deadline is the earlier of the session duration and license expiry. Join tokens cannot outlive that deadline. LiveKit rooms receive a participant cap including teachers. Clients show a countdown and disconnect at expiry. The existing `scanmeet-cleanup` worker also scans deadlines/disabled licenses and writes durable deletion debt before ending rows; the queue retries provider failures. Keep that worker and its timer running. Server cleanup runs on the worker schedule, so provider disconnection may lag the exact deadline if clients are offline or modified; API access is denied immediately after expiry. No exam submission, answer, autosave or exam timer is changed.

Legacy custom licenses without a plan retain their previous terms. Migration 076 is additive and backfills trial history for existing trial rows. Install it before deploying code that selects the new columns. Do not enable all schools or change the global Live flags as part of this migration.

## Capacity and supplier costs

The existing LiveKit project is reused only with operator authorization. Never copy secrets into this repository or collect them in a chat. No supplier plan upgrade or automatic billing is initiated by this feature.

LiveKit bills participant minutes and transferred data, not only elapsed room time. The full Sekolah allowance is 35 × 120 × 3 = 12,600 participant minutes; Trial is 450 and Kelas is 3,000. Video resolution, subscriptions, retransmissions and teacher viewing patterns affect data cost. Free project allowances are shared and finite; they are unsuitable as an unlimited promise to every school. Super Admin must verify available capacity before granting a request.

Primary references (checked 30 October 2026):
- https://livekit.io/pricing
- https://docs.livekit.io/home/cloud/quotas-and-limits/
- https://docs.livekit.io/home/cloud/billing/

## Verification

Offline tests cover catalog/expiry, stale writes, authorization, scoped requests, durable cleanup, rendered forms, JavaScript double submission/errors and SDK token/cap boundaries. SQL checks in `tests/scanmeet_package_sql_checks.sql` run inside a transaction against an isolated empty test database and must be rolled back. They test real PostgreSQL quota reservation, duplicate insert rollback, exhausted/disabled license rejection and private table grants. They must never run against the production school database.

A real-media staging pass from an earlier release proves the underlying media workflow, not this new timed-package feature on physical devices. Verify a small pilot with real teacher/pupil devices before broader activation.
