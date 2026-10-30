# ScanMeet implementation tracking

Product decisions approved by Sugeng Riyanto, M.Sc., 31 October 2026 (WIB).
These 12 specification documents are copied from the 22-Markdown agent pack so
repository-based work has the same requirements. They are targets, not claims
that every feature already exists. Existing repository AGENTS/deploy gates apply.

## First delivery: exam entry and media lifecycle

Implemented in this change:
- Always-visible ScanMeet entry card on the saved exam editor, including the server-OFF explanation.
- Server-authorized ON/OFF control; pupil link copy/WhatsApp compose without automatic sending.
- In-page pupil media panel, omitted from preview and when global feature flags are OFF.
- Explicit embedded join gesture and same-origin, versioned, lazy SDK loading.
- Status refresh and capture cleanup on OFF, ended/denied session and transport disconnect.
- Generation guard for SDK/token/connection/camera permission resolving after OFF.
- Microphone permission/revocation cleanup, keeping exam answers, autosave and timer code unchanged.
- Same-school admin may read/disable a teacher session without receiving the teacher's private pupil tracks.

Not implemented by this PR: supervisor assignment/safe takeover, standalone meetings,
new attendance/export features, camera effects, production scenes or local recorder.
The existing creator-only token boundary remains enforced until takeover is proven.
No feature flags, entitlement, production data, service or migrations are changed.

## Evidence and release boundary

The old client failed seven of the initial eight lifecycle DOM regressions; after
the fix all eight passed. Further SDK/token-race and same-origin tests were added.
DOM/route/template tests do not prove native permission prompts, browser fullscreen
behavior, actual SFU isolation or physical tablet/iOS compatibility. The real-media
gate and an embedded exam-page test must be run on the exact deployed staging SHA
before production activation. No claim of a new real-device sign-off.

Critical next proof: camera/microphone permission prompts during an armed exam must
not create false anti-cheat penalties. This change adds no blanket anti-cheat exemption.
Takeover will require server/SFU revocation and quota-safe transfer; merely changing
created_by is prohibited. See 02-ACCESS.md, 07-ACCEPTANCE.md and 09-IMPLEMENTATION.md.
