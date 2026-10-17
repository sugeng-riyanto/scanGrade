"""The text a pupil wrote, read from whichever shape the answer was stored in.

An essay answer is not one string. The exam page serialises it as

    {"type": "essay", "text": "...", "paragraphs": [{...}], "pages": {"0": {...}}}

because the editor keeps paragraphs and canvas annotations apart, and because a
paper sat on an older release may carry only one of those keys. Every surface
that *shows* the answer — the marking page, the split-pane queue, the AI prompt —
needs the same reading of that shape, and each one that read it for itself is how
the marking page came to print the first **300** characters of an essay and the
queue the first **100**: the pupil's work was in the database, complete, and the
teacher was marking a preview of it.

`text` is the authority when it is there: the page joins the paragraphs with a
blank line as the pupil types, so this is the string the AI prompt already fences
and the string the pupil's own character count shows. `paragraphs` is the fallback
for a row written before that field existed (or by a client that sends only the
structure), and `pages[*].textBoxes` is the last resort — an answer typed into
boxes on the paper rather than into the editor.

Nothing here decides *marks*. It reads, and it returns the whole answer; a caller
that wants a summary can cut it, but no gate in this app may do that on the way to
a teacher.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: What separates two paragraphs in the joined form, exactly as the page joins
#: them (`paragraphs.map(p => p.text).join('\n\n')`). Kept in one place so the
#: fallback cannot invent a third convention for the same text.
PARAGRAPH_JOIN = "\n\n"


def _paragraph_text(paragraph: Any) -> str:
    """One paragraph's text, whether it was stored as a dict or a bare string."""
    if isinstance(paragraph, str):
        return paragraph.strip()
    if isinstance(paragraph, Mapping):
        text = paragraph.get("text")
        return text.strip() if isinstance(text, str) else ""
    return ""


def _text_boxes(pages: Any) -> str:
    """Text typed into boxes laid over the paper, in page order.

    A `textBoxes` entry carries `text`; anything else on the page (a drawing) is
    not text and is skipped rather than stringified, which is what kept a base64
    PNG out of a teacher's answer box on the old report.
    """
    if not isinstance(pages, Mapping):
        return ""
    typed: list[str] = []
    for key in sorted(pages, key=lambda k: str(k)):
        content = pages[key]
        if not isinstance(content, Mapping):
            continue
        boxes = content.get("textBoxes")
        if not isinstance(boxes, Sequence) or isinstance(boxes, (str, bytes)):
            continue
        for box in boxes:
            text = _paragraph_text(box)
            if text:
                typed.append(text)
    return "\n".join(typed)


def essay_answer_text(answer: Any) -> str:
    """The pupil's whole answer as plain text; `""` when there is none.

    Plain text, never markup: the editor stores `innerHTML` beside the text for
    the formatting it renders, and a teacher reading `<p>` tags is a teacher
    reading the wrong thing. `text`/`paragraphs` are already plain (written from
    `innerText`), so this does not strip tags — it only refuses to reach into the
    markup when a plain reading exists.
    """
    if isinstance(answer, str):
        return answer.strip()
    if not isinstance(answer, Mapping):
        return ""

    text = answer.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    paragraphs = answer.get("paragraphs")
    if isinstance(paragraphs, Sequence) and not isinstance(paragraphs, (str, bytes)):
        joined = PARAGRAPH_JOIN.join(
            part for part in (_paragraph_text(p) for p in paragraphs) if part
        )
        if joined.strip():
            return joined.strip()

    boxes = _text_boxes(answer.get("pages"))
    if boxes.strip():
        return boxes.strip()

    # A bare-string answer wrapped in the structured shape: an older client wrote
    # `{"answer": "..."}` and nothing else. `answer` is a *choice* key everywhere
    # else, so it is read last and only when it is text.
    bare = answer.get("answer")
    return bare.strip() if isinstance(bare, str) else ""
