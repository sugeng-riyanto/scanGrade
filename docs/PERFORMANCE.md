# Performance — what a page costs, and how it tells you it is working

This is the operational half of "the site feels slow": what a page actually
downloads, what is measured, and the one rule about third-party libraries that
keeps the offline-first promise true.

Measured numbers live in this file because they are the argument for the code.
Nothing here is a Lighthouse score — no headless browser is installed in this
checkout, so the numbers below are byte counts and request counts taken with
`curl` against production and against the repository, plus the deploy gates'
own measurements. Where a claim would need a browser, it says so instead of
guessing.

## 1. Third-party libraries are self-hosted. Always.

**No template may load a library from a remote host.** Every library is vendored
under `app/static/vendor/` and served by this app: Alpine.js, htmx, Chart.js,
Font Awesome, the Inter webfonts, socket.io and Midtrans Snap.

Why this is a rule and not a preference:

* **A school's WiFi blocks or throttles CDNs.** A CDN URL that does not answer
  is not a slow page — it is a page whose buttons do nothing, because Alpine
  never loaded. This is the offline-first promise the exam pages are built on.
* **Privacy.** A third-party CDN sees the IP, the referrer and the timing of
  every visit, including children's. Fetching nothing from them means leaking
  nothing to them.
* **It has already gone wrong twice.** `teacher/analytics.html` and
  `shared/comms.html` each loaded a second copy of Chart.js from a CDN, which
  replaced `window.Chart` and silently discarded the dark-mode label colours
  base.html had set — charts fell back to `#666`, 2.94:1 on a dark card.

The guard is `tests/unit/test_asset_loading.py::test_no_library_is_loaded_from_a_remote_host`.
It parses the templates (a tag split across lines is invisible to a grep, which
is how the analytics duplicate was missed the first time). It is in the deploy
gate, so a release cannot ship one.

Runtime-injected scripts are pinned by name in the same file
(`KNOWN_RUNTIME_SCRIPTS`), because a static scan cannot read a URL assembled in
JavaScript. The one entry is Midtrans Snap.js, which is *local-first* with the
provider only as a fallback.

**Adding a library:** download it, commit it under `app/static/vendor/<name>/`,
reference it from the templates that need it, and add its name to
`LIBRARY_HINTS` in `test_asset_loading.py` if it should be caught by the remote
check. Do not add a `<script src="https://…">`.

## 2. The font payload (the largest single win so far)

`base.html` loaded Inter as six TrueType files, one per weight 400–900, about
325 KB each. Two things were wrong with that:

* **TTF is the one asset class nginx does not gzip here** (`gzip_types` covers
  text/css, javascript, json, svg and woff2). So all of it crossed the wire
  uncompressed.
* The full font was shipped per weight, and five of the six weights are painted
  by ordinary pages.

Measured against production before the change (byte counts from `curl`, no
`Content-Encoding` on any of them):

| | before | after |
|---|---|---|
| weights declared | 6 (400–900) | 5 (400–800, the ones Tailwind can ask for) |
| format | TTF | WOFF2 |
| compressed by nginx | no | n/a (WOFF2 is already compressed) |
| a page pays, common case | 1,627,964 B (5 TTFs fetched) | **121,144 B** (5 latin faces) |
| shipped in `vendor/inter/` | 1,957,224 B | 300,800 B (latin + latin-ext) |

**~13.4x smaller, about 1.5 MB less per visitor**, and font bytes alone were
roughly ten times the ~157 KB of CSS and JS the same page sent.

How it works now (`app/static/vendor/inter/inter.css`):

* The upstream **`@fontsource/inter` v5.3.0** WOFF2 subsets.
* Only weights 400–800 — `font-black` (900) is not used by any template, so it
  is not shipped. The guard is bidirectional:
  `test_every_weight_the_templates_ask_for_is_shipped` and
  `test_no_unused_weight_is_shipped`.
* **`latin` + `latin-ext`, each with its `unicode-range`.** Indonesian and
  English are entirely inside `latin`; the `latin-ext` face is fetched only when
  a page actually renders one of those code points.
* `font-display: swap`, so text paints in the fallback immediately instead of
  being invisible while the font downloads.
* The regular weight is **preloaded** in `base.html` with
  `as="font" type="font/woff2" crossorigin` — a font is otherwise discovered
  only after the stylesheet that names it is parsed and matched, which is a full
  round-trip of invisible text on a slow link.

**Updating the fonts:** change the filenames as well as the files. `/static/` is
served `Cache-Control: immutable, max-age=31536000`, so a replacement published
under the same URL never reaches a browser that already has the old one.

Budgets are enforced by `tests/unit/test_loading_feedback.py`
(160 KiB for the latin faces, 400 KiB for the whole directory).

## 3. Chart.js does not block the parser

Chart.js is 205 KB raw / 69 KB gzipped and sits in `base.html`, so every page
carries it whether or not it draws a chart. It used to be loaded
**synchronously in the head**, which means nothing painted until it had
downloaded and been parsed — the longest single stall on the page.

It is now `defer`red. Deferred scripts download while the document is parsed and
execute before `DOMContentLoaded` and before Alpine initialises a component, so
a page that draws a chart still finds `window.Chart`. Two consequences:

* The inline script that themes the charts (`sgApplyChartTheme`) can no longer
  assume Chart exists when it runs — it now waits for `DOMContentLoaded` and is
  registered immediately when `document.readyState === 'loading'`. Without that
  every chart keeps Chart.js's default `#666` ticks.
* The bytes are unchanged; what changed is that they no longer gate first paint.

## 4. Loading feedback: `app/static/js/sg-ux.js`

Loaded `defer` from `base.html`, dependency-free, and it is the *only* place
that implements "the system is working" so pages do not each invent one.

**The top progress bar** is automatic. Any tracked request (a form submit, a
`fetch`, an `XMLHttpRequest`) lifts a 3px line across the top of the window; it
fills to 90% and stops there, completing only when the last request settles.
Nothing in a page has to know about it. It steps to 90% and no further on
purpose: a bar that reaches 100% while the request is still open is the lie this
file exists to avoid.

It is also **delayed by 350 ms** (`DELAY_MS`), and a request that settles inside
that delay draws nothing. That single number is the difference between feedback
and noise: the exam page syncs a draft every ~12 seconds while a pupil writes,
and a bar flashing on each of those would be worse than no bar at all. Anything
still running after 350 ms is the thing the reader is actually waiting for — and
the indicator still arrives well inside the one second the brief asks for.

**The busy button** is opt-in, because a page that already manages its own
button state must not have this file overwrite it. Mark the control, or the form
that owns it:

```html
<button type="submit" data-sg-busy>…</button>
```

On submit the control keeps its place in the layout, gains a spinner, shows
`Memproses…` / `Working…` (chosen from `<html lang>`, the same attribute the
language toggle maintains), and stops accepting clicks — so the same form cannot
be submitted twice by a teacher whose connection took a moment.

A page whose async work is neither a form nor a fetch (a canvas loop, a
WebSocket round-trip) drives the bar directly:

```js
window.sgBusy.begin();   // or .start()
window.sgBusy.end();     // or .finish()
```

The busy state is released on `pageshow` (Returning to a form via Back must not
find a dead button) and by a slow safety timer, and every hook is wrapped so a
failure here can never break the submit it is decorating.

## 5. What is already good, and should stay that way

* **Compression.** nginx gzips text assets (`gzip_types`), `gzip_static on` for
  js/css/woff2. Verified on production with `Accept-Encoding: gzip`: tailwind.css
  arrived at 17,985 B, alpine.min.js at 16,223 B.
* **Caching.** `/static/` is `public, immutable, max-age=31536000` with ETags.
  This is why changing an asset means changing its filename.
* **The app's own stylesheet is a file, not an inline block.**
  (`deploy/theme_gate.sh` enforces that, and the ordering against tailwind.css.)
* **`X-Supabase-Roundtrips` / `query_meter`.** The perf gate scores *cost* —
  bytes, queries issued per render, round-trips — as well as the clock, so an
  N+1 is refused by a number rather than inferred from a stopwatch. See
  `deploy/perf_gate.py`.

## 6. What is not done yet

Honest list, in rough order of value:

* **Skeleton screens.** Data-backed pages still show a blank area until the
  server answers. A shimmer placeholder shaped like the real content (the
  Instagram/LinkedIn pattern) would make the same wait read as instant. Not
  built.
* **Chart.js is still downloaded by pages that do not chart.** Deferring it
  removed the parse block but not the 69 KB. The safe shape is a Jinja block
  that defaults to *including* it and lets a page opt out — the inverse of the
  current state, so a page that forgets cannot break its charts.
* **Serving brotli.** nginx here does gzip only. Brotli would take the CSS/JS
  set down a further ~15–20%.
* **No browser-based measurement in CI.** Every number in this file is bytes and
  requests, not LCP/FCP/TTI. `docs/measurements/` holds the load-test rungs; a
  Lighthouse rung would need a headless browser on the box.
