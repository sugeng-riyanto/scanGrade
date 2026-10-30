# ScanMeet timed packages

Membership prices follow Bahasa Cerdas Guru Pro, adapted to one concurrent classroom of 40 participants per school. Requests never collect payment or grant access automatically. Super Admin verifies capacity and payment before activation; default access remains OFF.

| Package | Price | Minutes per session | Participants including supervisors | Sessions | Validity |
| --- | ---: | ---: | ---: | ---: | --- |
| Trial | Rp0 | 45 | 40 | 1 | 7 days |
| Premium Bulanan | Rp49,000 | 120 | 40 | No session-count limit | 30 days |
| Premium Tahunan | Rp399,000 | 120 | 40 | No session-count limit | 365 days |

Trial is once per school. Archiving keeps trial history. The catalog in `app/services/scanmeet_packages.py` is authoritative for the offer page, administration and activation. Paid prices match `lib/billing/plans.ts` (GURU_PRO_MONTHLY / GURU_PRO_YEARLY) in https://github.com/dominikus02-source/bahasa-cerdas. Jumpa teacher class tiers are read from `lib/jumpa/tiers.ts`: premium classes last 120 minutes; ScanMeet adapts capacity to the requested 40 including the supervisor. The trial is a ScanMeet pilot offer, not Jumpa's recurring free tier. This is not a claim of mathematically optimal profit. Review actual traffic and supplier invoices before expanding availability.

## School and operator workflow

Authenticated users can view `/scanmeet`. Teachers and school managers can submit one pending request for their own school. Pupils see the offer and ask their teacher. A request never changes a license. Super Admin reviews, approves or declines requests from `/super-admin/scanmeet-schools`; search accepts the eight-digit NPSN or a school name.

Activation replaces the previous allowance and requires confirmation. Extending the same unexpired membership adds validity while preserving usage. Premium session count is not limited; the deadline, 40-person cap and one active classroom per school still apply. After expiry, extension starts a fresh validity period. Existing finite contracts retain their stored allowance until explicitly renewed. Paid activation and extension require explicit payment verification. Existing live rooms must end before replacement. Revocation and archive disable new access and queue media cleanup; archive preserves history instead of deleting school/exam records.

Forms use CSRF protection and optimistic version checks. A session reservation updates the license version so a stale administration form cannot erase usage. Requests and licenses remain private to service-role server handlers. School identity, price and limits from a browser are never trusted.

## Session enforcement

A PostgreSQL trigger locks the school license and reserves one session atomically at logical room creation. A failed duplicate insertion or school-concurrency refusal rolls back its reservation. Reconnects to that room do not reserve another session. A failed media connection after room creation still consumes the session; the customer should contact the operator for review. No automatic refund is promised.

The stored deadline is the earlier of the session duration and license expiry. Join tokens cannot outlive that deadline. LiveKit rooms receive a participant cap including teachers. Clients show a countdown and disconnect at expiry. The existing `scanmeet-cleanup` worker also scans deadlines/disabled licenses and writes durable deletion debt before ending rows; the queue retries provider failures. Keep that worker and its timer running. Server cleanup runs on the worker schedule, so provider disconnection may lag the exact deadline if clients are offline or modified; API access is denied immediately after expiry. No exam submission, answer, autosave or exam timer is changed.

Legacy custom licenses without a plan retain their previous terms. Migration 076 is additive and backfills trial history for existing trial rows. Migration 077 adds the stored concurrency marker and replaces the reservation trigger to support paid memberships with a NULL allowance. Install both before deploying the new catalog. NULL concurrency preserves older contract terms; a missing allowance without the membership marker is rejected. Do not enable all schools or change the global Live flags as part of this migration.

## Capacity and supplier costs

The existing LiveKit project is reused only with operator authorization. Never copy secrets into this repository or collect them in a chat. No supplier plan upgrade or automatic billing is initiated by this feature.

LiveKit bills participant minutes and transferred data, not only elapsed room time. One full 40-person premium session consumes up to 40 × 120 = 4,800 participant minutes; the 45-minute trial consumes up to 1,800. No session-count limit is not a promise of unlimited provider resources. Verify and fund provider capacity before manual approval; never automatically upgrade the supplier plan. Video resolution, subscriptions, retransmissions and teacher viewing patterns affect data cost. Free project allowances are shared and finite; they are unsuitable as an unlimited promise to every school. Super Admin must verify available capacity before granting a request.

Primary references (checked 30 October 2026):
- https://livekit.io/pricing
- https://docs.livekit.io/home/cloud/quotas-and-limits/
- https://docs.livekit.io/home/cloud/billing/

## Form submission fix

HTML form controls named `action` override the form's `action` property in browsers. The management handler reads the action attribute explicitly, so activation selects and revocation hidden fields cannot redirect the POST to a relative `[object HTMLSelectElement]` / `[object HTMLInputElement]` path and produce NOT_FOUND. The DOM test explicitly models this browser behavior because jsdom does not implement that named-property override.

## Verification

Offline tests cover catalog/expiry, stale writes, authorization, scoped requests, durable cleanup, rendered forms, JavaScript double submission/errors and SDK token/cap boundaries. SQL checks in `tests/scanmeet_package_sql_checks.sql` run inside a transaction against an isolated empty test database and must be rolled back. They test real PostgreSQL quota reservation, duplicate insert rollback, exhausted/disabled license rejection and private table grants. They must never run against the production school database.

A real-media staging pass from an earlier release proves the underlying media workflow, not this new timed-package feature on physical devices. Verify a small pilot with real teacher/pupil devices before broader activation.
