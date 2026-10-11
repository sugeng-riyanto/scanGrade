# Design system

The page-level rules ScanGrade keeps to, and where each one is enforced. It is a
short document on purpose: it records the rules that have a **guard**, because a
rule without one is a preference.

## Colour: tokens, two themes, no pure black or white

`app/static/css/theme.css` declares the palette twice — once at `:root` (light)
and once in `.dark` — with the same names in both. Surfaces and text are read
from those tokens, never typed as a literal:

| Token | Light | Dark |
|---|---|---|
| `--bg-body` | `#f1f5f9` | `#0f1117` |
| `--bg-card` | `#ffffff` | `#191c2b` |
| `--bg-subtle` | `#f8fafc` | `#202436` |
| `--bg-hover` | `#f1f5f9` | `#262b41` |
| `--text` | `#1e293b` | `#e6e9f0` |
| `--text-dim` | `#5d6a7d` | `#a8b0c8` |
| `--text-muted` | `#606c80` | `#949db5` |
| `--border` | `#e2e8f0` | `#2f3448` |
| `--paper` | `#ffffff` | `#e8ecf2` |
| `--paper-ink` | `#111827` | `#111827` |
| `--on-accent` | `#ffffff` | `#ffffff` |
| `--on-chrome` | `#f1f5f9` | `#f1f5f9` |

Two rules follow from the table, and both are load-bearing:

* **No pure `#000000` or `#FFFFFF` for a surface or an icon.** Dark mode is a
  ladder of greys (`#0f1117` → `#191c2b` → `#202436` → `#262b41`), not black with
  white on it; light mode is off-white, not a glare. This is the Material / HIG
  convention and it is also the a11y answer — a pure pair is the worst case for
  halation and for anyone with astigmatism. The one deliberate exception is a
  *sheet*: `--paper` is the answer canvas, it stays light in both themes because
  the ink drawn on it is dark, and it steps down the ladder in dark mode all the
  same — so the exception is a token, named once, rather than `#fff` typed into the
  five pages that draw a paper.
* **The two themes declare the same tokens, value for value.** A token only one
  theme declares moves when the theme changes.

A page written with Tailwind's literal palette (`bg-white`, `text-stone-500`) is
remapped onto the tokens rather than rewritten: `theme.css` carries
`:where(.dark) :is(.bg-white)` and siblings for each family. New work should still
reach for the token.

### The four role tokens

The last four rows are *roles* rather than surfaces, and they are what let the exam
and correction pages stop spelling colours by hand. A page-local `<style>` block is
not reached by the utility remap, so a rule in it that says `background: #fff` is a
decision nobody recorded — and in dark mode an unreadable one, because the label on
a white box inherits the theme's own light `--text`. Naming the colours is what
makes them auditable, and it is why one declaration now serves both themes: the
exam page's `.dark` block is down to the two border colours the themes genuinely do
not share, and the correction pages need none at all.

* **`--paper` / `--paper-ink`** — the sheet an answer is drawn on, and the pen and
type on it. The one surface that is light in *both* themes, because the ink on it is
dark and a sheet that followed the theme into black would hide the answer (the same
reasoning that keeps the OMR mockup light). The ink is a tiered near-black rather
than `#000`, and it is what the boxes on the sheet read instead of inheriting
`--text` — without that pin a `<textarea>` on paper painted white on white.
* **`--on-accent` / `--on-chrome`** — a label on a saturated accent fill, and on the
chrome that is dark in both themes (the canvas tool dots). `--on-accent` is white
because that is what `text-white` already means on every `bg-<hue>-600` button, and
it is held to that convention rather than to 4.5:1 — the same bar the saturated
fills themselves are held to.

**Enforced by:** `tests/unit/test_dark_theme_contrast.py` (both themes declare the
same tokens; measured contrast ratios), `tests/unit/test_theme_literals.py` (the
pages that draw a paper carry no pure white or black, and the four roles are
measured in both themes) and `tests/unit/test_exam_tablet_dark.py`.
Tokens are applied as CSS custom properties, never as a hard-coded hex in a
template.

## Stacking: one named layer scale

Every overlay takes its height from a name in `theme.css`
(`--sg-layer-*`, with a `.sg-layer-<name>` class beside it), grouped as `app`,
`paper` and `canvas`. Within a group the order is strictly ascending with no
height used twice. `--sg-layer-out` is the declared "this region is in no stack"
state.

**No template, stylesheet or script writes a stacking height as a number** — not
`z-50`, not `z-[100]`, not `z-index: 80` in a `style` attribute, not
`zIndex = "99999"`. A hand-picked height is how two panels come to share one, and
how a modal ends up under a toast on one page and over it on another.

**Enforced by:** `tests/unit/test_layer_scale.py`.

## App-level zoom (and why the browser's own is left alone)

Zoom in ScanGrade is an **application** zoom: a control resizes content by setting
a CSS custom property, and never touches the browser's zoom, the viewport, or the
`<meta name="viewport">` tag.

The pupil exam page is the reference implementation. `A-` / `A+` move through
`[0.85, 1.0, 1.15, 1.3, 1.5]` and write `--sg-exam-scale` on the exam stage, which
`.sg-exam-zoom` reads as `zoom: var(--sg-exam-scale, 1)`. The chosen step is stored
as the `text_scale` preference, so it follows the pupil to their next device.

Three rules, and each exists for a measured reason:

1. **Pinch-zoom is never switched off.** WCAG 1.4.4 (Resize text) and 1.4.10
   (Reflow) forbid `user-scalable=no` and a capped `maximum-scale`. The application
   zoom is *additive*: a pupil who pinches gets the browser's zoom on top of it.
2. **A zoom raises no event the anti-cheat ladder can see.** The ladder treats a
   window `resize`, a `fullscreenchange` and a `visibilitychange` as things that
   happened *to the exam*. A CSS custom property produces none of them, which is
   exactly why the scale is a property and not a `transform`, a reflow of the
   window, or anything that reaches for the global.
3. **The steps are a closed set.** The control is a pair of buttons, so a value
   between two steps could not be reached by pressing either one. The page would
   show a scale no button can produce and jump on the next press. The same list
   lives in `app/services/user_preferences.py`; the two are pinned together.

The magnified-diagram lightbox follows the same rule: an overlay **inside** the
page, never a new tab and never a viewport zoom. It sits on the maximized-viewer
layer (`.sg-layer-media`), which is above the fullscreen blocker's scrim — so
losing fullscreen closes it, or the paper would stay readable through the overlay
while the answers behind it were blocked.

**Enforced by:** `tests/unit/test_exam_view_zoom.py` (the module's arithmetic run
in node, the "write one property on one element and reach for nothing else"
tripwire, and the page's wiring), `tests/unit/test_ui_preferences.py`.

## Touch and breakpoints

* **Finger targets are ≥ 44px under `@media (pointer: coarse)`** — not
  `max-width`. A 768px tablet is as much a finger as a 375px phone while a 768px
  window on a mouse desktop is not, and a width query is wrong about both in the
  same direction. The exam builder's `pointer: coarse` block raises 42 controls to
  44px with no overflow; see `docs/features/EXAM_BUILDER.md`.
* **Tablet gets its own rules rather than a scale of the phone or desktop layout.**
  The pupil exam page carries `@media (min-width: 641px)` for the side-by-side
  paper, `@media (pointer: coarse) and (min-width: 641px)` for finger controls, and
  `@media (min-width: 769px) and (max-width: 1023px)` for the tablet band itself.
  The builder page was measured at 820×1180, 1180×820, 800×1280, 1280×800,
  1024×768, 1112×834 and 768×1024.

## Third-party code is self-hosted

Every script, stylesheet and font is served from this application's own
`app/static/`, with a cache-busting content version where it is our own asset.
Vendor files live under `app/static/vendor/` (Alpine, htmx, Chart.js, …).

The two exceptions are the ones that are *someone else's page*: the YouTube embed a
teacher may paste for a listening question, and the payment provider on the
checkout. Neither is loaded on a page a pupil reads without an explicit action.

The reasons are performance (a school network in Indonesia can be slow or block a
CDN outright, and Alpine failing to load is a blank page for every pupil at once)
and privacy (a request to a third-party CDN tells that CDN an Indonesian child's IP
and the page they were on). `chart.js` is loaded at the end of the body, not in the
head, so nothing paints after 69 KB of charting library on a page that draws no
chart.

**Enforced by:** `tests/unit/test_static_tree.py` (every file under `/static/` is
one the app asks for and is in a commit) and the vendor/script-order guards in
`tests/unit/test_exam_script_order.py`.

## Loading order is a rule, not a preference

Alpine is loaded with `defer`, which means it starts in the microtask after the
document stops loading — *before* the next deferred script in the document. A page
helper that defines a global read by `x-data` therefore has to be a **classic
script** in the body, not a deferred one. A deferred helper produced a blank white
exam page. `exam-media.js`, `tools.js` and `exam-view.js` are all classic scripts
at the end of the pupil exam page for this reason.

**Enforced by:** `tests/unit/test_exam_script_order.py`.
