# Question media: audio and video on an exam

A listening question can carry media in two ways, and both still work:

1. **Pasted links** — a Google Drive file URL and/or a YouTube video URL, typed into
   the builder. This is what every paper built before the upload path existed uses.
2. **Uploaded files** — the file is stored in this app's own private bucket and
   served by the app itself. No Google account, no third-party network.

The parts that matter to a teacher are at the top; the parts that matter to a
reviewer are at the bottom.

## For a teacher

**Pasted links (unchanged).** Paste a Drive *file* link (`…/file/d/ID/view`, not a
folder or a document) and a YouTube URL. The builder previews exactly what a pupil's
browser will walk, and says so out loud when a link cannot be resolved instead of
drawing an empty player.

**Uploaded files.** In the builder, open a question's *Media Pendukung* panel and use
*Unggah berkas — tanpa Google Drive*: choose Audio or Video, pick the file, and the
builder uploads it and starts a preview. What is saved into the paper is the storage
path; the preview link is a token that expires, so it is never stored.

| | audio | video |
|---|---|---|
| accepted | mp3, m4a, aac, ogg, oga, opus, wav, webm, flac | mp4, m4v, webm, ogv, mov |
| ceiling | 25 MB | 45 MB |

The video ceiling sits deliberately below the app's own `MAX_CONTENT_LENGTH`
(50 MB): Flask refuses a bigger request body *before* any handler runs, so a ceiling
above it would show the teacher a bare 413 with no sentence from us.

**A paper must be saved once before it can hold an uploaded file.** The storage path
is scoped to the paper's own id, and a brand-new paper has no id yet — the panel says
so plainly rather than offering a control that cannot work. Pasted links can be added
at any point, before or after the first save.

**Removing a file.** The *Hapus berkas* button clears it from the question. A
replaced file is left in the bucket (it is private and unreachable without a token);
nothing reads it again.

## For a reviewer

### The link is bound to one sitting, not to a file

The page is handed `/media/<token>`. The token is a signed claim over four things —
the storage path, the user it was minted for, the paper, and an expiry (15 minutes) —
signed with the app's `SECRET_KEY`. The route checks the claim's subject against the
session's own user id, so a URL copied out of DevTools is refused (**403**) in
anybody else's browser, and refused again once its minutes are up. That is the whole
difference between a *signed* URL and a *sitting-bound* one.

### Storage is never exposed to a browser

The app mints a short-lived (120 s) signed Storage URL for **its own** request and
streams the bytes through the response, forwarding the caller's `Range` header so
seeking inside a track does not re-download it. The browser never sees a Storage URL:
a public bucket link is exactly the arrangement this replaced.

Responses carry `Accept-Ranges: bytes` (without it a browser offers no seek bar) and
`Cache-Control: private, no-store` — the link is bound to one sitting, and a shared or
proxy cache holding it would undo that.

### A question may only point at its own paper's object

The save route reads the storage path back out of the form, so the path is the one
field where a hand-written POST could choose which object a paper points at. It is
accepted only when it starts with the paper's own id, taken from the route
(`request.view_args`), never from a form field. The create route has no prefix to
check against, so it cannot attach an uploaded file at all.

Duplicating a paper copies its media paths verbatim, which would leave the copy
pointing at the original's objects — playback would still work, but the copy's next
save would refuse those paths and the copy would lose its audio silently. So
duplication copies the objects into the copy's own prefix; a file that cannot be
copied is dropped rather than left half-pointing.

### What is refused, and with what status

`POST /teacher/exams/<exam_id>/media` (JSON reply) is the builder's upload endpoint:

| status | when |
|---|---|
| 400 | no file in the request |
| 403 / 404 | the paper is not the caller's (or does not exist) |
| 413 | the file is over the ceiling for its kind — judged **before** the bytes reach Storage |
| 422 | the extension or the claimed content type does not fit the kind |
| 502 | Storage refused the write |

The object's content type is decided by the server from the extension, not trusted
from the client: a browser plays what the object says it is, and
`application/octet-stream` is a player that never starts.

### Legacy papers

`with_media_urls` returns a **copy**: an entry that carries a `file` gains a `url`
for the page, and an entry that does not (a pasted Drive or YouTube link, including
the bare-string shape older papers use) is passed through untouched. Nothing is ever
written back into the stored JSONB, because a token stored there would be expired
long before anyone read it.

`app/static/js/exam-media.js` is the single place a link becomes something a browser
can play — shared by the builder's preview and the pupil's page. It now also passes
the app's own `/media/<token>` URLs straight through (they are already the source, and
`new URL('/media/x')` throws), while refusing `//host/path`, which starts with a slash
and is *not* ours.

## Where the code lives

| file | what |
|---|---|
| `app/services/exam_media.py` | the token, `with_media_urls`, upload validation, the signed fetch and the stream |
| `app/routes/media.py` | `GET /media/<token>` — verify, check the sitting, stream |
| `app/routes/teacher.py` | `POST /teacher/exams/<exam_id>/media`, `_question_media_from_form`, `_relocate_question_media` |
| `app/templates/teacher/exam_form.html` | the upload control and the hidden fields the save route reads |
| `app/templates/student/take_exam.html` | the hosted players, beside the pasted-link ones |
| `app/static/js/exam-media.js` | link resolution, shared by both pages |

Guards: `tests/unit/test_exam_media_service.py`, `test_exam_media_routes.py`,
`test_exam_media_wiring.py` (51 tests; the JS one is *run* in node, and the Jinja
for the upload endpoint is *rendered* rather than grepped — an unfiltered branch in
a `<script>` renders `&#34;` and takes the builder's whole script down); the defects
they claim to catch are injected by `.freebuff/mutate_exam_media.py` (**23/23
caught**).
