"""The item analysis as a spreadsheet and as a filed report.

`item_analysis` answers what a paper's questions did; a school then has to *keep*
that answer. Two documents, because they are used differently: a CSV a teacher
opens in a spreadsheet to sort and filter, and a PDF that goes in the exam file
next to the answer sheets.

Both are built from the same `Analysis` object the screen renders, so a number in
the file and the number on the page cannot disagree — there is no second pass over
the submissions here.

Language: these are server-rendered, so the toggle's Alpine binding cannot reach
them. Each takes a `lang` and picks the label table for it, and the print documents
in this app already open in Indonesian by default; a PDF in the exam file is read
long after whoever exported it has closed the tab, so it says which language it is
in on its own header line.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Mapping

from app.services import analysis_frameworks as af

# Imported here rather than inside `analysis_pdf`, where the rest of reportlab is
# loaded: a flowable has to exist as a *class* before the module finishes, and
# `reportlab` is a hard requirement of this app either way.
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Flowable

#: One label table per language. Kept as pairs-by-key rather than two dicts so a
#: missing translation is a KeyError here and not an Indonesian word on a page
#: that said it was English.
_LABELS: dict[str, dict[str, str]] = {
    "id": {
        "title": "Analisis Butir Soal",
        "summary": "Ringkasan",
        "items": "Statistik butir soal",
        "people": "Kemampuan murid (skala logit)",
        "distractors": "Sebaran pilihan jawaban",
        "splits": "Daya beda kelompok atas dan bawah",
        "notes": "Catatan dan batas",
        "students": "Jumlah murid",
        "item_count": "Jumlah soal",
        "objective": "Soal objektif",
        "essays": "Soal esai",
        "papers": "Kertas dengan data",
        "mean": "Rata-rata nilai",
        "sd": "Simpangan baku nilai",
        "alpha": "Alpha Cronbach",
        "kr20": "KR-20",
        "sem": "SEM (kesalahan baku ukur)",
        "person_rel": "Reliabilitas murid",
        "person_sep": "Separasi murid",
        "item_rel": "Reliabilitas butir",
        "item_sep": "Separasi butir",
        "measure_mean": "Rata-rata logit murid",
        "measure_sd": "Simpangan baku logit murid",
        "extremes": "Murid skor ekstrem",
        "separation_block": "Pemisahan dan keandalan (Winsteps)",
        "side_people": "Murid",
        "side_items": "Butir",
        "row_model": "MODEL",
        "row_real": "REAL",
        "rmse": "RMSE (galat baku rata-rata)",
        "true_sd": "TRUE SD (SD tanpa galat)",
        "separation": "SEPARASI (G)",
        "strata": "STRATA",
        "reliability": "RELIABILITAS",
        "se_real": "SE real",
        "no": "No",
        "kind": "Tipe",
        "marks": "Bobot",
        "answered": "Dijawab",
        "missing": "Kosong",
        "pct": "Tingkat kesulitan (P)",
        "full": "Skor penuh",
        "disc": "Daya beda (D)",
        "pbis": "Korelasi butir-total",
        "measure": "Logit (b)",
        "se": "SE",
        "t": "t",
        "p_value": "p",
        "infit": "Infit MNSQ",
        "outfit": "Outfit MNSQ",
        "flag": "Catatan butir",
        "name": "Nama",
        "raw": "Skor",
        "possible": "Maksimal",
        "measure_p": "Logit (θ)",
        "score": "Nilai",
        "extreme": "Ekstrem",
        "option": "Pilihan",
        "count": "Dipilih",
        "key": "Kunci",
        "upper": "Kelompok atas",
        "lower": "Kelompok bawah",
        "df": "df",
        "yes": "ya",
        "no_word": "tidak",
        "empty": "belum ada data untuk dianalisis",
        "before": "Turunkan versi bahasa di atas berkas, bukan di dalamnya: berkas ini dibuat ",
        "language": "Bahasa laporan",
        # Printed only on a document generated from a public share link. A file
        # whose columns are missing has to say why, or the reader assumes the
        # data was not collected.
        "shared": "Salinan berbagi: nama murid dan kunci jawaban tidak disertakan.",
        "generated": "Dibuat",
        "school": "Sekolah",
        "teacher": "Guru",
        "exam": "Ujian",
        "charts": "Grafik",
        "item_map": "Peta butir soal",
        "map_hint": "Kanan = dikerjakan hampir semua murid; atas = membedakan murid kuat dan lemah.",
        "difficulty_chart": "Kesulitan soal (logit)",
        "difficulty_hint": "Nol = soal pada rata-rata ujian ini; makin ke kiri makin mudah.",
        "people_chart": "Sebaran kemampuan murid",
        "people_hint": "Satu batang satu rentang logit; murid berskor sempurna atau nol "
                       "tidak punya taksiran hingga.",
        "distractor_chart": "Sebaran pilihan jawaban",
        "distractor_hint": "Satu kolom satu soal, dibagi menurut pilihan yang "
                            "menghasilkan tiap jawaban; potongan bertanda adalah kunci "
                            "soal itu.",
        "pct_axis": "Tingkat kesulitan (%)",
        "disc_axis": "Daya beda (D)",
        "logit_axis": "Logit",
        "chosen_count": "Jumlah pemilih",
        "item_no_axis": "No. soal",
        "no_chart": "belum ada data untuk digambar",
        # The paper table has sixteen columns; their long names are the same
        # sixteen words a teacher reads on the screen, where there is room for
        # them. On A4 the header is the screen's short symbols and the names go
        # into one legend line, because a wrapped header column is a header nobody
        # can match to a number.
        "h_no": "Soal",
        "h_full": "Penuh",
        "h_flag": "Catatan",
        "legend": "P = tingkat kesulitan; D = daya beda; r = korelasi butir-total; "
                  "b = kesulitan dalam logit; SE = galat baku; t/p = beda kelompok "
                  "atas dan bawah; Infit/Outfit = kecocokan dengan model.",
        # The key under the drawings. One line per swatch, written as the act a
        # teacher takes rather than as the statistic's name, because the colour is
        # the finding and the number is in the table.
        "chart_legend": "Keterangan warna:",
        "legend_ok": "Baik — soal bekerja seperti seharusnya.",
        "legend_weak": "Daya beda lemah — periksa ulang, mungkin perlu diperbaiki.",
        "legend_bad": "Daya beda negatif atau menyimpang — soal ini merugikan murid kuat.",
        "legend_extreme": "Dijawab sama oleh semua murid — belum mengukur apa pun.",
        "legend_unmeasured": "Belum bisa diukur — kunci belum diisi atau belum ada jawaban.",
        "legend_key": "Kunci jawaban soal itu",
        "legend_distractor": "Pilihan pengecoh yang dipilih murid",
        "legend_students": "Jumlah murid",
        # The four frameworks, and the two blocks the app never published before.
        "framework": "Kerangka analisis",
        "framework_question": "Pertanyaan yang dijawab laporan ini",
        "framework_measures": "Yang dilaporkan",
        "framework_when": "Kapan dipakai",
        "framework_reference": "Rujukan",
        "framework_not_for": "Yang tidak bisa dijawab",
        "cognitive": "Porsi tingkat kognitif (kisi-kisi)",
        "cognitive_basis_marks": "Dihitung dari bobot nilai tiap soal.",
        "cognitive_basis_questions": "Dihitung dari banyak soal, karena ujian ini tidak memakai bobot nilai.",
        "band": "Tingkat",
        "share": "Porsi (%)",
        "unlabelled": "Belum dilabeli",
        "mastery": "Ketuntasan belajar (KKM)",
        "kkm": "KKM",
        "passed": "Tuntas",
        "failed": "Belum tuntas",
        "pass_rate": "Persentase tuntas (%)",
        "class_mean": "Rata-rata kelas",
        "lowest": "Nilai terendah",
        "gap": "Rata-rata dikurangi KKM",
        "mastered": "Soal sudah dikuasai",
        "threshold": "Ambang penguasaan soal",
        "mastery_unconfigured": "Ujian ini belum menetapkan KKM, jadi ketuntasan tidak dihitung: patokan yang tidak dipilih sekolah bukan patokan yang boleh dikarang.",
        "level": "Tingkat kognitif",
        "unchanged": "Soal yang tidak punya tingkat kognitif belum dilabeli, bukan otomatis tingkat rendah.",
    },
    "en": {
        "title": "Item Analysis",
        "summary": "Summary",
        "items": "Item statistics",
        "people": "Student ability (logit scale)",
        "distractors": "Option distribution",
        "splits": "Upper and lower group separation",
        "notes": "Notes and limits",
        "students": "Students",
        "item_count": "Questions",
        "objective": "Objective questions",
        "essays": "Essay questions",
        "papers": "Papers with data",
        "mean": "Mean mark",
        "sd": "SD of marks",
        "alpha": "Cronbach's alpha",
        "kr20": "KR-20",
        "sem": "SEM (standard error of measurement)",
        "person_rel": "Student reliability",
        "person_sep": "Student separation",
        "item_rel": "Item reliability",
        "item_sep": "Item separation",
        "measure_mean": "Mean student logit",
        "measure_sd": "SD of student logits",
        "extremes": "Extreme-score students",
        "separation_block": "Separation and reliability (Winsteps)",
        "side_people": "Students",
        "side_items": "Items",
        "row_model": "MODEL",
        "row_real": "REAL",
        "rmse": "RMSE (average measurement error)",
        "true_sd": "TRUE SD (SD without measurement error)",
        "separation": "SEPARATION (G)",
        "strata": "STRATA",
        "reliability": "RELIABILITY",
        "se_real": "Real SE",
        "no": "No",
        "kind": "Type",
        "marks": "Marks",
        "answered": "Answered",
        "missing": "Blank",
        "pct": "Difficulty (P)",
        "full": "Full credit",
        "disc": "Discrimination (D)",
        "pbis": "Item-total correlation",
        "measure": "Logit (b)",
        "se": "SE",
        "t": "t",
        "p_value": "p",
        "infit": "Infit MNSQ",
        "outfit": "Outfit MNSQ",
        "flag": "Item note",
        "name": "Name",
        "raw": "Score",
        "possible": "Possible",
        "measure_p": "Logit (θ)",
        "score": "Mark",
        "extreme": "Extreme",
        "option": "Option",
        "count": "Chosen",
        "key": "Key",
        "upper": "Upper group",
        "lower": "Lower group",
        "df": "df",
        "yes": "yes",
        "no_word": "no",
        "empty": "no data to analyse yet",
        "before": "Lower the language version, not the file: this report was produced ",
        "language": "Report language",
        "shared": "Shared copy: student names and the answer key are not included.",
        "generated": "Generated",
        "school": "School",
        "teacher": "Teacher",
        "exam": "Exam",
        "charts": "Charts",
        "item_map": "Item map",
        "map_hint": "Right = answered by nearly everyone; up = separates the strong from "
                     "the weak.",
        "difficulty_chart": "Question difficulty (logits)",
        "difficulty_hint": "Zero = a question of average difficulty for this paper; further "
                            "left is easier.",
        "people_chart": "Spread of student ability",
        "people_hint": "One bar per logit range; a paper scoring everything or nothing has "
                        "no finite estimate.",
        "distractor_chart": "Option distribution",
        "distractor_hint": "One column per question, split by the option that earned "
                            "each answer; the marked slice is that question's key.",
        "pct_axis": "Difficulty (%)",
        "disc_axis": "Discrimination (D)",
        "logit_axis": "Logit",
        "chosen_count": "Chosen",
        "item_no_axis": "Question",
        "no_chart": "no data to draw yet",
        "h_no": "No",
        "h_full": "Full",
        "h_flag": "Note",
        "legend": "P = difficulty; D = discrimination; r = item-total correlation; "
                  "b = difficulty in logits; SE = standard error; t/p = upper-versus-lower "
                  "difference; Infit/Outfit = fit to the model.",
        "chart_legend": "Colour key:",
        "legend_ok": "Sound — the question behaved as it should.",
        "legend_weak": "Weak discrimination — look again, it may need rewriting.",
        "legend_bad": "Negative or misfitting — this question penalises the strong students.",
        "legend_extreme": "Everybody answered it the same way — it has measured nothing yet.",
        "legend_unmeasured": "Not measurable yet — no answer key, or no answers.",
        "legend_key": "The option this question is keyed to",
        "legend_distractor": "A distractor the class actually chose",
        "legend_students": "Number of students",
        # The four frameworks, and the two blocks the app never published before.
        "framework": "Assessment framework",
        "framework_question": "The question this report answers",
        "framework_measures": "What it publishes",
        "framework_when": "When it is used",
        "framework_reference": "Reference",
        "framework_not_for": "What it cannot answer",
        "cognitive": "Cognitive level mix (kisi-kisi)",
        "cognitive_basis_marks": "Counted from each question's marks.",
        "cognitive_basis_questions": "Counted by question, because this exam carries no marks per question.",
        "band": "Band",
        "share": "Share (%)",
        "unlabelled": "Not labelled yet",
        "mastery": "Mastery against the KKM",
        "kkm": "KKM",
        "passed": "Met the standard",
        "failed": "Below the standard",
        "pass_rate": "Share meeting the standard (%)",
        "class_mean": "Class mean",
        "lowest": "Lowest mark",
        "gap": "Mean minus the KKM",
        "mastered": "Questions mastered",
        "threshold": "Question mastery threshold",
        "mastery_unconfigured": "This exam sets no KKM, so mastery is not computed: a standard the school never chose is not one a report may invent.",
        "level": "Cognitive level",
        "unchanged": "A question with no cognitive level is unlabelled, not lower order by default.",
    },
}

#: The question kinds, as a reader says them. Keyed by `question_types.KIND_*`.
KIND_LABELS: dict[str, dict[str, str]] = {
    "choice": {"id": "Pilihan ganda", "en": "Multiple choice"},
    "truefalse": {"id": "Benar/Salah", "en": "True/false"},
    "match": {"id": "Menjodohkan", "en": "Matching"},
    "dragdrop": {"id": "Seret & lepas", "en": "Drag and drop"},
    "ordering": {"id": "Mengurutkan", "en": "Ordering"},
    "essay": {"id": "Esai", "en": "Essay"},
}

#: One sentence per finding flag, so the file says what the number means.
FLAG_LABELS: dict[str, dict[str, str]] = {
    "ok": {"id": "Baik", "en": "Sound"},
    "weak": {"id": "Daya beda lemah", "en": "Weak discrimination"},
    "negative": {"id": "Daya beda negatif", "en": "Negative discrimination"},
    "misfit": {"id": "Menyimpang dari model", "en": "Misfits the model"},
    "extreme_easy": {"id": "Semua benar", "en": "Everyone right"},
    "extreme_hard": {"id": "Semua salah", "en": "Everyone wrong"},
    "unscored": {"id": "Belum ada jawaban", "en": "No answers yet"},
    "unkeyed": {"id": "Kunci belum diisi", "en": "No answer key"},
}

#: And one per note the module raises.
NOTE_LABELS: dict[str, dict[str, str]] = {
    "dichotomous": {
        "id": "Kalibrasi memakai skor penuh/lontok, jadi soal menjodohkan yang "
              "mendapat sebagian masih dihitung sebagai satu butir dikotomi; kolom "
              "klasik tetap memakai nilai sebagiannya.",
        "en": "The calibration is full-credit-or-nothing, so a part-credited "
              "matching question is still one dichotomous item; the classical "
              "columns do use its part credit.",
    },
    "partial_credit": {
        "id": "Ujian ini memberi nilai sebagian untuk jawaban yang sebagian benar.",
        "en": "This exam awards part marks for partly correct answers.",
    },
    "essays_teacher_marked": {
        "id": "Nilai esai berasal dari penilaian guru, bukan dari pengoreksi otomatis.",
        "en": "An essay's share is the teacher's own mark, not the auto-grader's.",
    },
    "unanswered_items": {
        "id": "Ada soal yang belum dijawab satu murid pun.",
        "en": "Some questions have not been answered by a single student.",
    },
    "unkeyed_items": {
        "id": "Ada soal objektif yang kuncinya belum diisi, sehingga belum bisa "
              "dinilai maupun dianalisis.",
        "en": "Some objective questions have no answer key, so they can be neither "
              "marked nor analysed.",
    },
    "missing_not_wrong": {
        "id": "Jawaban kosong dihitung hilang, bukan salah, dan tidak masuk "
              "perhitungan tingkat kesulitan soal itu.",
        "en": "A blank answer counts as missing, not wrong, and stays out of that "
              "question's difficulty.",
    },
    "kkm_missing": {
        "id": "Ujian ini belum menetapkan KKM, jadi belum ada patokan untuk "
              "menyatakan tuntas. Isi KKM pada pengaturan ujian.",
        "en": "This exam has no KKM set, so there is no standard to declare "
              "mastery against. Set the KKM on the exam.",
    },
    "levels_partly_set": {
        "id": "Sebagian soal belum punya tingkat kognitif (C1–C6), jadi porsi "
              "LOTS/MOTS/HOTS dihitung dari soal yang sudah dilabeli saja.",
        "en": "Some questions have no cognitive level (C1–C6) yet, so the "
              "LOTS/MOTS/HOTS shares count only the labelled questions.",
    },
    "extreme_items": {
        "id": "Ada soal yang dijawab benar atau salah oleh seluruh murid; angkanya "
              "dihitung dengan koreksi skor ekstrem.",
        "en": "Some questions were answered by every student or by none; their "
              "numbers use the extreme-score correction.",
    },
    "model_real": {
        "id": "Baris MODEL memakai galat seperti yang diprediksi model; baris REAL "
              "menganggap ketidakcocokan itu nyata, sehingga galat baku tiap butir dan "
              "murid digelembungkan oleh infit-nya sendiri. Angka REAL yang paling aman "
              "dikutip, dan STRATA = (4G + 1)/3 adalah banyaknya tingkat yang benar-benar "
              "terpisah.",
        "en": "The MODEL row takes the errors the model predicts; the REAL row assumes "
              "the misfit is real, inflating every item's and student's standard error "
              "by its own infit. REAL is the conservative number to quote, and STRATA = "
              "(4G + 1)/3 is how many statistically distinct levels the spread "
              "supports.",
    },
    "fit_dof": {
        "id": "Infit, outfit dan t-nya mengikuti Wright & Masters (Rating Scale "
              "Analysis, 1982, hlm. 100): outfit = rata-rata kuadrat sisa terstandar, "
              "infit = rata-rata berbobot informasi. Nilai ZSTD memakai transformasi "
              "Wilson–Hilferty dengan derajat kebebasan 2/q² dari sebaran model "
              "(Schulz, Rasch Measurement Transactions 16:2 hlm. 879), bukan jumlah "
              "respons — jumlah respons membuat tanda ketidakcocokan menyala terlalu "
              "sering.",
        "en": "Infit, outfit and their t follow Wright & Masters (Rating Scale "
              "Analysis, 1982, p. 100): outfit is the mean squared standardized "
              "residual, infit the information-weighted one. ZSTD uses the "
              "Wilson–Hilferty transform with degrees of freedom 2/q², estimated from "
              "the model distribution (Schulz, Rasch Measurement Transactions 16:2 p. "
              "879) rather than from the number of responses — the response count "
              "fires the misfit flag far too often.",
    },
    "scale_anchor": {
        "id": "Skala logit dipatok pada rata-rata kesulitan soal ujian ini (0 logit), "
              "seperti konvensi UCON di Winsteps, sehingga t = b/SE menguji \"sesulit "
              "rata-rata soal ujian ini\".",
        "en": "The logit scale is anchored on the mean difficulty of this exam's "
              "questions (0 logits), Winsteps' UCON convention, so that t = b/SE tests "
              "\"as hard as this paper's average question\".",
    },
}


def language(lang: str | None) -> str:
    """`"en"` or `"id"`: this app's print documents default to Indonesian."""
    return "en" if str(lang or "").lower().startswith("en") else "id"


def labels(lang: str | None) -> dict[str, str]:
    """The label table for `lang`, defaulting to Indonesian like the print docs."""
    return _LABELS[language(lang)]


def _pair(table: Mapping[str, Mapping[str, str]], key: str, lang: str) -> str:
    entry = table.get(key) or {}
    return entry.get(lang) or entry.get("id") or key


def _num(value: Any, digits: int = 2) -> str:
    """A number for a cell, or an empty cell when there is none.

    A dash rather than a zero: an item with no data has no difficulty, and `0.00`
    in a spreadsheet is a measurement.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


#: The separation block's statistics, in the order Winsteps prints them, with the
#: digits each one is worth. `text` renders them for a CSV or a page; the workbook
#: takes the raw floats, because a spreadsheet that holds "1.18" as text cannot be
#: averaged or charted — which is the defect this workbook exists to avoid.
_SEPARATION_STATS = (("rmse", 2), ("true_sd", 2), ("separation", 2),
                     ("strata", 2), ("reliability", 3))


def separation_cells(summary, text: bool = True) -> list[list[Any]]:
    """The block's lines that can be formed, keyed rather than labelled.

    `(side, row, rmse, true_sd, separation, strata, reliability)` per line, with the
    side and the row as the keys a reader who re-renders their own words needs
    (`people`/`items`, `real`/`model`) and not as words. That is what lets the
    *page* show Winsteps' block from this one function while the language toggle
    rewrites its labels in the browser — the numbers and the choice of which lines
    exist are decided here, once, so the screen cannot disagree with the filed PDF.

    A line whose **separation is undefined** is left out rather than printed as
    four dashes. Separation is the number the block exists for, and it is undefined
    whenever there is nothing to divide: one marked paper, or no calibrated item.
    TRUE SD of 0.00 with a separation of 0.00 *is* printed — that is a real finding
    about a class whose spread is entirely measurement error.
    """
    out: list[list[Any]] = []
    for side_key, block in (("people", summary.person_stats),
                            ("items", summary.item_stats)):
        for row_key, suffix in (("real", "real"), ("model", "model")):
            values = [getattr(block, f"{name}_{suffix}")
                      for name, _d in _SEPARATION_STATS]
            separation = values[2]
            if separation is None:
                continue
            out.append([side_key, row_key]
                       + [(_num(value, digits) if text
                           else (value if value is not None else ""))
                          for value, (_n, digits) in zip(values, _SEPARATION_STATS)])
    return out


def separation_rows(summary, lang: str = "id", text: bool = True) -> list[list[Any]]:
    """Winsteps' separation block as rows: a header, then REAL and MODEL per side.

    One shape for the CSV, the workbook and the paper, because the number a school
    quotes has to be the same number wherever it read it. REAL comes first: it is
    the conservative row, the one that assumes the misfit is real.
    """
    lang = language(lang)
    t = labels(lang)
    rows: list[list[Any]] = [[t["separation_block"]] + [t[name]
                                                       for name, _d in _SEPARATION_STATS]]
    for cells in separation_cells(summary, text=text):
        side, row = cells[0], cells[1]
        side_key = "side_people" if side == "people" else "side_items"
        row_key = "row_real" if row == "real" else "row_model"
        rows.append([f"{t[side_key]} {t[row_key]}"] + list(cells[2:]))
    return rows


# ── the framework, and the two blocks only it publishes ──────────────────────
#
# One shape per block, read by the page, the CSV, the workbook and the paper, for
# the same reason every other block here is built once: a report that says "23%
# HOTS" on screen and "18%" in the PDF has stopped being a measurement. The page
# re-writes only the *labels*; the numbers and the choice of which rows exist come
# from here, so a change to what a framework reports lands in all four places or
# in none of them.


def framework_rows(framework, lang: str = "id") -> list[list[str]]:
    """One framework's identity as rows: what it answers, and what it cannot.

    The `not_for` line is part of the block rather than a footnote, because it is
    the half that stops a reader quoting a logit as a mark or a HOTS share as a
    verdict on question quality.
    """
    lang = language(lang)
    t = labels(lang)
    words = framework.words(lang)
    return [
        [t["framework"], words["name"]],
        [t["framework_question"], words["question"]],
        [t["framework_measures"], words["measures"]],
        [t["framework_when"], words["when"]],
        [t["framework_reference"], words["reference"]],
        [t["framework_not_for"], words["not_for"]],
    ]


def cognitive_payload(analysis) -> dict[str, Any]:
    """The HOTS/MOTS/LOTS mix: three bands, the unlabelled count, per question.

    Deliberately not rounded into one number. `basis` says whether the shares are
    counted in marks or in questions, because the same paper is HOTS-heavy or not
    depending on which one a reader assumed — and `unset` travels with the bands
    rather than being folded into LOTS, which is the difference between "this
    paper asks for little higher-order thinking" and "nobody has written the
    kisi-kisi yet".
    """
    mix = analysis.cognitive
    return {
        "basis": mix.basis,
        "bands": [{"key": band, "count": mix.counts.get(band, 0),
                   "marks": mix.marks.get(band, 0.0), "share": mix.share(band)}
                  for band in af.BANDS],
        "unset": mix.unset,
        "unset_marks": mix.unset_marks,
        "labelled": mix.labelled,
        "total": mix.total,
        "total_marks": mix.total_marks,
        "questions": [{"no": item.index + 1, "level": item.level,
                       "band": item.band, "marks": round(item.marks, 2)}
                      for item in analysis.items],
    }


def cognitive_rows(analysis, lang: str = "id") -> list[list[Any]]:
    """The cognitive mix as rows: the basis, the three bands, then the unlabelled."""
    lang = language(lang)
    t = labels(lang)
    mix = cognitive_payload(analysis)
    rows: list[list[Any]] = [[t["cognitive"]] + [t[v] for v in ("band", "item_count", "marks", "share")]]
    for band in mix["bands"]:
        rows.append([_pair(af.BAND_NAMES, band["key"], lang),
                     _num(band["count"], 0), _num(band["marks"], 2),
                     _num(band["share"], 1)])
    rows.append([t["unlabelled"], _num(mix["unset"], 0),
                 _num(mix["unset_marks"], 2), ""])
    rows.append([t["cognitive_basis_marks"] if mix["basis"] == "marks"
                 else t["cognitive_basis_questions"]])
    if mix["unset"]:
        rows.append([t["unchanged"]])
    return rows


def mastery_payload(analysis) -> dict[str, Any]:
    """Criterion-referenced mastery: the standard, the verdicts, the questions.

    `configured` is in the payload and not left to the reader: an exam with no KKM
    has no verdict, and a payload that only carried the counts would let a page
    draw "0 passed" as if a standard had been applied.
    """
    mastery = analysis.mastery
    return {
        "configured": mastery.configured,
        "kkm": mastery.kkm,
        "passed": mastery.passed,
        "failed": mastery.failed,
        "pass_rate": mastery.pass_rate,
        "mean": mastery.mean,
        "lowest": mastery.lowest,
        "gap": mastery.gap,
        "items_mastered": mastery.items_mastered,
        "items_measured": mastery.items_measured,
        "questions": [{"no": item.index + 1, "full": item.full,
                       "answered": item.answered,
                       "pct": item.pct, "mastered": item.mastered}
                      for item in analysis.items if item.mastered is not None],
    }


def mastery_rows(analysis, lang: str = "id") -> list[list[Any]]:
    """Mastery as rows: the standard and the counts, then the questions that lag."""
    lang = language(lang)
    t = labels(lang)
    block = mastery_payload(analysis)
    rows: list[list[Any]] = [[t["mastery"]]]
    if not block["configured"]:
        # One sentence instead of five empty rows: the reason there is no verdict
        # is more useful than the absence of a number, and it is the reason a
        # reader can act on.
        rows.append([t["mastery_unconfigured"]])
        return rows
    rows += [
        [t["kkm"], _num(block["kkm"], 0)],
        [t["passed"], _num(block["passed"], 0)],
        [t["failed"], _num(block["failed"], 0)],
        [t["pass_rate"], _num(block["pass_rate"], 1)],
        [t["class_mean"], _num(block["mean"], 2)],
        [t["lowest"], _num(block["lowest"], 2)],
        [t["gap"], _num(block["gap"], 2)],
        [t["threshold"], _num(round(block["kkm"] / 100.0, 2), 2)],
        [t["mastered"], f"{block['items_mastered']} / {block['items_measured']}"],
    ]
    if block["questions"]:
        rows.append([t["no"], t["full"], t["answered"], t["pct"], t["mastered"]])
        for question in block["questions"]:
            rows.append([str(question["no"]), _num(question["full"], 0),
                         _num(question["answered"], 0), _num(question["pct"], 1),
                         t["yes"] if question["mastered"] else t["no"]])
    return rows


def analysis_rows(analysis, lang: str = "id", compact: bool = False) -> list[list[str]]:
    """The item table as rows, header first — the one shape both documents use.

    `compact` swaps the header for the symbols the screen prints (P, D, r, b) with
    the words in one legend line. Sixteen full names on a landscape A4 are five
    columns of wrapped text that no reader can line up with the numbers under
    them, and a header that overlaps its neighbour is worse than an abbreviation.
    """
    lang = language(lang)
    t = labels(lang)
    if compact:
        header = [t["h_no"], t["kind"], t["marks"], t["answered"], t["missing"],
                  "P", "Penuh" if lang == "id" else "Full", "D", "r", "b", "SE", "t",
                  "p", "Infit", "Outfit", t["h_flag"]]
    else:
        header = [t["no"], t["kind"], t["marks"], t["answered"], t["missing"], t["pct"],
                  t["full"], t["disc"], t["pbis"], t["measure"], t["se"], t["t"],
                  t["p_value"], t["infit"], t["outfit"], t["flag"]]
    rows = [header]
    for item in analysis.items:
        rows.append([
            str(item.index + 1),
            _pair(KIND_LABELS, item.kind, lang),
            _num(item.marks, 2),
            str(item.answered),
            str(item.missing),
            _num(item.pct, 1),
            str(item.full),
            _num(item.discrimination, 3),
            _num(item.point_biserial, 3),
            _num(item.measure, 2),
            _num(item.se, 2),
            _num(item.t, 2),
            _num(item.p_value, 4),
            _num(item.infit, 2),
            _num(item.outfit, 2),
            _pair(FLAG_LABELS, item.flag, lang),
        ])
    return rows


def _person_rows(analysis, lang: str) -> list[list[str]]:
    lang = language(lang)
    t = labels(lang)
    rows = [[t["name"], t["raw"], t["possible"], t["measure_p"], t["se"],
             t["score"], t["extreme"]]]
    for person in analysis.people:
        rows.append([
            person.name, str(person.raw), str(person.possible),
            _num(person.measure, 2), _num(person.se, 2),
            _num(person.score, 2), t["yes"] if person.extreme else t["no_word"],
        ])
    return rows


def analysis_csv(analysis, exam: Mapping[str, Any] | None = None,
                 lang: str = "id", public: bool = False,
                 framework=None) -> str:
    """The whole analysis as one CSV: summary, then items, then students.

    One file rather than three because the three sections are read together — a
    teacher sorts the item table and wants the paper's reliability beside it.

    `public=True` is the copy generated from a share link: the students section
    is left out and the answer-key column is left blank, because a link a teacher
    sent to a curriculum lead is not a reason to hand out either. The file says so
    in its own header rather than quietly missing its last section.

    `framework` is the report the reader chose, and the file carries the same one
    the screen did: its identity goes in the header and its own blocks go in the
    body. A CSV named "Item Analysis" that quietly contains a HOTS breakdown the
    RASCH view never showed is a document nobody can date; naming the framework in
    the file is what lets a school file it next to the right conversation.
    """
    lang = language(lang)
    t = labels(lang)
    framework = framework or af.resolve(None)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")

    exam = exam or {}
    writer.writerow([t["title"], str(exam.get("title") or analysis.title or "")])
    writer.writerow([t["exam"], analysis.code or str(exam.get("id") or "")])
    writer.writerow([t["language"], "English" if lang == "en" else "Bahasa Indonesia"])
    writer.writerow([t["framework"], framework.words(lang)["name"]])
    if public:
        writer.writerow([t["shared"]])
    writer.writerow([])

    summary = analysis.summary
    writer.writerow([t["summary"]])
    for key, value, digits in (
        ("students", summary.students, 0),
        ("item_count", summary.items, 0),
        ("objective", summary.objective, 0),
        ("essays", summary.essays, 0),
        ("papers", summary.answered_papers, 0),
        ("mean", summary.total_mean, 2),
        ("sd", summary.total_sd, 2),
        ("alpha", summary.alpha, 3),
        ("kr20", summary.kr20, 3),
        ("sem", summary.sem, 2),
        ("person_rel", summary.person_reliability, 3),
        ("person_sep", summary.person_separation, 2),
        ("item_rel", summary.item_reliability, 3),
        ("item_sep", summary.item_separation, 2),
        ("measure_mean", summary.mean_measure, 3),
        ("measure_sd", summary.sd_measure, 3),
        ("extremes", summary.extremes, 0),
    ):
        writer.writerow([t[key], _num(value, digits)])
    writer.writerow([])

    for row in separation_rows(summary, lang):
        writer.writerow(row)
    writer.writerow([])

    # The two blocks only their own framework publishes. The reason they are
    # conditional rather than always present: the framework *is* the promise about
    # what this file is, and a CTT export that also carried the KKM verdict would
    # be answering a question the reader did not ask it.
    if framework.shows("cognitive"):
        for row in cognitive_rows(analysis, lang):
            writer.writerow(row)
        writer.writerow([])
    if framework.shows("mastery"):
        for row in mastery_rows(analysis, lang):
            writer.writerow(row)
        writer.writerow([])

    writer.writerow([t["items"]])
    for row in analysis_rows(analysis, lang):
        writer.writerow(row)
    writer.writerow([])

    # Only the choice questions have options; the rest are absent rather than a
    # header with no rows under it.
    distractored = [item for item in analysis.items if item.distractors]
    if distractored:
        writer.writerow([t["distractors"]])
        writer.writerow([t["no"], t["option"], t["count"], t["key"]])
        for item in distractored:
            for option in item.distractors:
                writer.writerow([str(item.index + 1), option.label, str(option.count),
                                 "" if public else (t["yes"] if option.key else "")])
        writer.writerow([])

    if analysis.splits:
        writer.writerow([t["splits"]])
        writer.writerow([t["no"], t["upper"], t["lower"], t["t"], t["df"], t["p_value"]])
        for split in analysis.splits:
            writer.writerow([str(split.index + 1), _num(split.upper, 3),
                             _num(split.lower, 3), _num(split.t, 2),
                             _num(split.df, 1), _num(split.p, 4)])
        writer.writerow([])

    if not public:
        writer.writerow([t["people"]])
        for row in _person_rows(analysis, lang):
            writer.writerow(row)
        writer.writerow([])

    notes = af.notes_for(analysis.notes, framework)
    if notes:
        writer.writerow([t["notes"]])
        for note in notes:
            writer.writerow([_pair(NOTE_LABELS, note, lang)])
    return out.getvalue()


def _sheet(key: str, lang: str) -> str:
    """The worksheet name for `key`, safe for Excel."""
    return _pair(SHEET_NAMES, key, lang)


#: The item sheet, as (header key, attribute, number format). Declared as data
#: rather than a chain of `ws.cell(...)` calls so the chart references below can
#: name a column by what it holds — `_ITEM_COLUMN["pct"]` — instead of by a letter
#: somebody has to keep in step with this tuple by hand.
_ITEM_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("no", "index", "0"),
    ("kind", "kind", "@"),
    ("marks", "marks", "0.00"),
    ("answered", "answered", "0"),
    ("missing", "missing", "0"),
    ("pct", "pct", "0.0"),
    ("full", "full", "0"),
    ("disc", "discrimination", "0.000"),
    ("pbis", "point_biserial", "0.000"),
    ("measure", "measure", "0.00"),
    ("se", "se", "0.00"),
    ("t", "t", "0.00"),
    ("p_value", "p_value", "0.0000"),
    ("infit", "infit", "0.00"),
    ("outfit", "outfit", "0.00"),
    ("flag", "flag", "@"),
)

#: `column name -> 1-based Excel column`, for the chart references.
_ITEM_COLUMN: dict[str, int] = {key: index + 1
                                for index, (key, _attr, _fmt) in
                                enumerate(_ITEM_COLUMNS)}


def analysis_xlsx(analysis, exam: Mapping[str, Any] | None = None,
                  lang: str = "id", public: bool = False,
                  framework=None) -> bytes:
    """The analysis as a workbook a school can *use*, charts included.

    The CSV is for reading and the PDF for filing; this is for working. Three
    things separate it from a CSV renamed to `.xlsx`:

    * **The numbers are numbers.** `analysis_rows` formats every cell into text,
      which is right for a print document and wrong for a spreadsheet — a column
      of text sorts `-1.94` above `0.5`, and no chart can be drawn from it. The
      cells here hold the values themselves, with a number format for display.
    * **The drawings are native Excel charts**, anchored on the sheet whose numbers
      they plot, so a teacher can retitle, resize or re-range them, and the figures
      follow the data if a cell is corrected. A pasted image would answer the same
      question and could not be edited.
    * **The colour key travels with it**, as filled cells on the `key` sheet.
      Excel has no chart legend that can say "red means the question penalises the
      strong students", and a workbook whose item column is coloured with nothing
      explaining the colours is a workbook that hides its own finding.

    `public=True` is the workbook generated from a share link: the `people` sheet
    is not created and the answer key is not painted on the `options` sheet — the
    finding colours stay, because what a question *did* is the point of sharing.
    """
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference, ScatterChart, Series
    from openpyxl.chart.marker import Marker
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    lang = language(lang)
    t = labels(lang)
    framework = framework or af.resolve(None)
    exam = exam or {}
    head = Font(bold=True)
    shade = PatternFill("solid", fgColor="eef2f7")

    def label(text: str):
        """Append a one-cell heading row and return its cell.

        `sheet.append(...)` returns `None`, so `sheet.append([x]).font = head` —
        which is how this was first written — raises `AttributeError` on the first
        heading and leaves the workbook unbuilt.
        """
        worksheet.append([text])
        cell = worksheet.cell(row=worksheet.max_row, column=1)
        cell.font = head
        return cell

    book = Workbook()

    # ── summary ──────────────────────────────────────────────────────────────
    worksheet = book.active
    worksheet.title = _sheet("summary", lang)
    worksheet.append([t["title"], str(exam.get("title") or analysis.title or "")])
    worksheet.append([t["exam"], analysis.code or str(exam.get("id") or "")])
    worksheet.append([t["language"],
                      "English" if lang == "en" else "Bahasa Indonesia"])
    worksheet.append([t["framework"], framework.words(lang)["name"]])
    if public:
        worksheet.append([t["shared"]])
    worksheet.append([])
    label(t["summary"])
    sheet = worksheet
    summary = analysis.summary
    for key, value, digits in (
        ("students", summary.students, 0), ("item_count", summary.items, 0),
        ("objective", summary.objective, 0), ("essays", summary.essays, 0),
        ("papers", summary.answered_papers, 0), ("mean", summary.total_mean, 2),
        ("sd", summary.total_sd, 2), ("alpha", summary.alpha, 3),
        ("kr20", summary.kr20, 3), ("sem", summary.sem, 2),
        ("person_rel", summary.person_reliability, 3),
        ("person_sep", summary.person_separation, 2),
        ("item_rel", summary.item_reliability, 3),
        ("item_sep", summary.item_separation, 2),
        ("measure_mean", summary.mean_measure, 3),
        ("measure_sd", summary.sd_measure, 3), ("extremes", summary.extremes, 0),
    ):
        sheet.append([t[key], value if value is not None else ""])
        if value is not None and isinstance(value, (int, float)):
            sheet.cell(row=sheet.max_row, column=2).number_format = f"0.{'0' * digits}" \
                if digits else "0"

    # Winsteps' separation block, as its own grid so the MODEL and REAL rows line
    # up under their statistics the way they do in Winsteps' own Tables 3.1/28.3.
    worksheet.append([])
    for index, row in enumerate(separation_rows(summary, lang, text=False)):
        worksheet.append(row)
        for column, (name, digits) in enumerate(_SEPARATION_STATS, start=2):
            cell = worksheet.cell(row=worksheet.max_row, column=column)
            if index == 0:
                cell.font = head
                cell.fill = shade
            elif isinstance(cell.value, (int, float)):
                cell.number_format = f"0.{'0' * digits}"
    notes = af.notes_for(analysis.notes, framework)
    if notes:
        worksheet.append([])
        label(t["notes"])
        for note in notes:
            worksheet.append([_pair(NOTE_LABELS, note, lang)])
    worksheet.column_dimensions["A"].width = 34
    worksheet.column_dimensions["B"].width = 60

    # ── the framework, and the two blocks only their own framework publishes ──
    sheet = book.create_sheet(_sheet("framework", lang))
    for row in framework_rows(framework, lang):
        sheet.append(row)
    sheet.column_dimensions["A"].width = 40
    sheet.column_dimensions["B"].width = 90
    for row in sheet.iter_rows(min_col=1, max_col=1):
        row[0].font = head
    for row in sheet.iter_rows(min_col=2, max_col=2):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")

    if framework.shows("cognitive"):
        mix = cognitive_payload(analysis)
        sheet = book.create_sheet(_sheet("cognitive", lang))
        sheet.append([t["cognitive"], t["band"], t["item_count"], t["marks"],
                      t["share"]])
        for cell in sheet[1]:
            cell.font = head
            cell.fill = shade
        for band in mix["bands"]:
            sheet.append(["", _pair(af.BAND_NAMES, band["key"], lang), band["count"],
                          band["marks"], band["share"] if band["share"] is not None else ""])
            for column, digits in ((3, 0), (4, 2), (5, 1)):
                cell = sheet.cell(row=sheet.max_row, column=column)
                if isinstance(cell.value, (int, float)):
                    cell.number_format = "0" if digits == 0 else f"0.{'0' * digits}"
        # The unlabelled row and the basis sentence go after the bands, not inside
        # them: a paper whose kisi-kisi is unwritten is not a paper with 0% HOTS.
        sheet.append(["", t["unlabelled"], mix["unset"], mix["unset_marks"], ""])
        sheet.append([])
        sheet.append([t["cognitive_basis_marks"] if mix["basis"] == "marks"
                      else t["cognitive_basis_questions"]])
        sheet.append([])
        sheet.append([t["no"], t["level"], t["band"], t["marks"]])
        for cell in sheet[sheet.max_row]:
            cell.font = head
        for question in mix["questions"]:
            level = af.level(question["level"])
            sheet.append([question["no"],
                          level.name[lang] if level else t["unlabelled"],
                          _pair(af.BAND_NAMES, question["band"], lang)
                          if question["band"] else "",
                          question["marks"]])
        sheet.column_dimensions["B"].width = 34
        sheet.column_dimensions["C"].width = 30

    if framework.shows("mastery"):
        block = mastery_payload(analysis)
        sheet = book.create_sheet(_sheet("mastery", lang))
        if not block["configured"]:
            sheet.append([t["mastery_unconfigured"]])
            sheet.column_dimensions["A"].width = 90
        else:
            sheet.append([t["mastery"]])
            sheet.cell(row=1, column=1).font = head
            for key, value, digits in (
                ("kkm", block["kkm"], 0), ("passed", block["passed"], 0),
                ("failed", block["failed"], 0), ("pass_rate", block["pass_rate"], 1),
                ("class_mean", block["mean"], 2), ("lowest", block["lowest"], 2),
                ("gap", block["gap"], 2),
                ("threshold", round(block["kkm"] / 100.0, 2), 2),
            ):
                sheet.append([t[key], value if value is not None else ""])
                cell = sheet.cell(row=sheet.max_row, column=2)
                if isinstance(cell.value, (int, float)):
                    cell.number_format = "0" if digits == 0 else f"0.{'0' * digits}"
            sheet.append([t["mastered"], block["items_mastered"], block["items_measured"]])
            sheet.append([])
            sheet.append([t["no"], t["full"], t["answered"], t["pct"], t["mastered"]])
            for cell in sheet[sheet.max_row]:
                cell.font = head
                cell.fill = shade
            for question in block["questions"]:
                sheet.append([question["no"], question["full"], question["answered"],
                              question["pct"],
                              t["yes"] if question["mastered"] else t["no"]])
            sheet.column_dimensions["A"].width = 26
            sheet.column_dimensions["E"].width = 16

    # ── items, with the two charts that belong to them ───────────────────────
    items = list(analysis.items)
    sheet = book.create_sheet(_sheet("items", lang))
    sheet.append([t[key] for key, _attr, _fmt in _ITEM_COLUMNS])
    for cell in sheet[1]:
        cell.font = head
        cell.fill = shade
    for item in items:
        row = []
        for key, attr, _fmt in _ITEM_COLUMNS:
            if key == "no":
                raw = item.index + 1
            elif key == "kind":
                raw = _pair(KIND_LABELS, item.kind, lang)
            elif key == "flag":
                raw = _pair(FLAG_LABELS, item.flag, lang)
            else:
                raw = getattr(item, attr)
            row.append("" if raw is None else raw)
        sheet.append(row)
        for index, (_key, _attr, fmt) in enumerate(_ITEM_COLUMNS, start=1):
            cell = sheet.cell(row=sheet.max_row, column=index)
            if fmt != "@":
                cell.number_format = fmt
        # The finding, in the palette the page and the PDF use, so a teacher can
        # filter by colour as well as sort by number.
        sheet.cell(row=sheet.max_row, column=_ITEM_COLUMN["flag"]).fill = PatternFill(
            "solid", fgColor=tone_for(item.flag).lstrip("#"))
    sheet.freeze_panes = "A2"
    for index in range(1, len(_ITEM_COLUMNS) + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 11

    last = len(items) + 1
    if items:
        # The item map: difficulty against discrimination, one point per question.
        # The same two columns the page and the PDF plot, so a school that edits a
        # number here sees the picture move rather than a picture of yesterday.
        chart = ScatterChart()
        chart.title = t["item_map"]
        chart.x_axis.title = t["pct_axis"]
        chart.y_axis.title = t["disc_axis"]
        chart.height, chart.width = 9.0, 15.0
        xref = Reference(sheet, min_col=_ITEM_COLUMN["pct"], min_row=2, max_row=last)
        yref = Reference(sheet, min_col=_ITEM_COLUMN["disc"], min_row=1, max_row=last)
        series = Series(yref, xref, title_from_data=True)
        series.marker = Marker(symbol="circle", size=7)
        series.graphicalProperties.line.noFill = True
        chart.series.append(series)
        sheet.add_chart(chart, f"{get_column_letter(len(_ITEM_COLUMNS) + 2)}2")

        chart = BarChart()
        chart.type = "col"
        chart.title = t["difficulty_chart"]
        chart.x_axis.title = t["item_no_axis"]
        chart.y_axis.title = t["logit_axis"]
        chart.height, chart.width = 9.0, 15.0
        data = Reference(sheet, min_col=_ITEM_COLUMN["measure"], min_row=1, max_row=last)
        cats = Reference(sheet, min_col=_ITEM_COLUMN["no"], min_row=2, max_row=last)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        sheet.add_chart(chart, f"{get_column_letter(len(_ITEM_COLUMNS) + 2)}21")

    # ── options ──────────────────────────────────────────────────────────────
    chosen = [item for item in items if item.distractors]
    if chosen:
        sheet = book.create_sheet(_sheet("options", lang))
        sheet.append([t["no"], t["option"], t["count"], t["key"]])
        for cell in sheet[1]:
            cell.font = head
            cell.fill = shade
        letters = sorted({option.label for item in chosen for option in item.distractors})
        # A matrix beside the list, and the chart is drawn from the matrix: a
        # stacked column is one column per question, which a flat (question,
        # option, count) table cannot express to Excel without a pivot table.
        # The matrix starts in column F, clear of the four-column list.
        first_matrix_col = 6
        sheet.cell(row=1, column=first_matrix_col, value=t["distractor_chart"]).font = head
        for offset, letter in enumerate(letters):
            sheet.cell(row=2, column=first_matrix_col + offset, value=letter).font = head
        for row_offset, item in enumerate(chosen, start=3):
            sheet.cell(row=row_offset, column=first_matrix_col - 1,
                       value=item.index + 1).font = head
            counts = {option.label: option for option in item.distractors}
            for offset, letter in enumerate(letters):
                option = counts.get(letter)
                cell = sheet.cell(row=row_offset, column=first_matrix_col + offset,
                                  value=(option.count if option else None))
                if option is not None and option.key and not public:
                    cell.fill = PatternFill("solid", fgColor=_KEY.lstrip("#"))
        for item in chosen:
            for option in item.distractors:
                sheet.append([item.index + 1, option.label, option.count,
                              "" if public else ("✓" if option.key else "")])
        chart = BarChart()
        chart.type = "col"
        chart.grouping = "stacked"
        chart.overlap = 100
        chart.title = t["distractor_chart"]
        chart.x_axis.title = t["item_no_axis"]
        chart.y_axis.title = t["chosen_count"]
        chart.height, chart.width = 9.0, 16.0
        data = Reference(sheet, min_col=first_matrix_col,
                         max_col=first_matrix_col + len(letters) - 1,
                         min_row=2, max_row=len(chosen) + 2)
        cats = Reference(sheet, min_col=first_matrix_col - 1, min_row=3,
                         max_row=len(chosen) + 2)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        sheet.add_chart(chart, f"{get_column_letter(first_matrix_col + len(letters) + 1)}4")
        for column in range(1, 5):
            sheet.column_dimensions[get_column_letter(column)].width = 12

    # ── upper and lower groups ───────────────────────────────────────────────
    if analysis.splits:
        sheet = book.create_sheet(_sheet("groups", lang))
        sheet.append([t["no"], t["upper"], t["lower"], t["t"], t["df"], t["p_value"]])
        for cell in sheet[1]:
            cell.font = head
            cell.fill = shade
        for split in analysis.splits:
            sheet.append([split.index + 1, split.upper, split.lower, split.t, split.df,
                          split.p])
            for column, fmt in enumerate(("0", "0.000", "0.000", "0.00", "0.0", "0.0000"),
                                         start=1):
                sheet.cell(row=sheet.max_row, column=column).number_format = fmt
        for column in range(1, 7):
            sheet.column_dimensions[get_column_letter(column)].width = 13

    # ── students, with the ability histogram ─────────────────────────────────
    # A shared copy has no students sheet at all, rather than one with the name
    # column blanked: an empty name column reads as data that was lost, and a
    # workbook that quietly omitted its last sheet is worse than one that says so
    # on the summary sheet. The histogram goes with the names — the bins are the
    # class, and a class small enough to be one bar is a person.
    if not public:
        sheet = book.create_sheet(_sheet("people", lang))
        sheet.append([t["name"], t["raw"], t["possible"], t["measure_p"], t["se"],
                      t["score"], t["extreme"]])
        for cell in sheet[1]:
            cell.font = head
            cell.fill = shade
        people = list(analysis.people)
        for person in people:
            sheet.append([person.name, person.raw, person.possible, person.measure,
                          person.se, person.score,
                          t["yes"] if person.extreme else t["no_word"]])
        bins = list(analysis.person_bins)
        if bins:
            first_bin_row = len(people) + 4
            sheet.cell(row=first_bin_row - 1, column=1, value=t["people_chart"]).font = head
            sheet.cell(row=first_bin_row, column=1, value=t["logit_axis"]).font = head
            sheet.cell(row=first_bin_row, column=2, value=t["legend_students"]).font = head
            for offset, (centre, count) in enumerate(bins, start=1):
                sheet.cell(row=first_bin_row + offset, column=1, value=centre)
                sheet.cell(row=first_bin_row + offset, column=2, value=count)
            chart = BarChart()
            chart.type = "col"
            chart.title = t["people_chart"]
            chart.x_axis.title = t["logit_axis"]
            chart.y_axis.title = t["legend_students"]
            chart.height, chart.width = 9.0, 16.0
            data = Reference(sheet, min_col=2, min_row=first_bin_row,
                             max_row=first_bin_row + len(bins))
            cats = Reference(sheet, min_col=1, min_row=first_bin_row + 1,
                             max_row=first_bin_row + len(bins))
            chart.add_data(data, titles_from_data=True)
            chart.set_categories(cats)
            sheet.add_chart(chart, f"{get_column_letter(4)}{first_bin_row - 1}")
        for column, width in enumerate((30, 9, 9, 9, 8, 9, 10), start=1):
            sheet.column_dimensions[get_column_letter(column)].width = width

    # ── how to read it ───────────────────────────────────────────────────────
    worksheet = book.create_sheet(_sheet("key", lang))
    worksheet.append([t["chart_legend"]])
    worksheet.cell(row=1, column=1).font = head
    for colour, text in legend_entries(lang):
        worksheet.append(["", text])
        worksheet.cell(row=worksheet.max_row, column=1).fill = PatternFill(
            "solid", fgColor=colour.lstrip("#"))
    worksheet.append([])
    worksheet.append([t["legend_key"]])
    worksheet.cell(row=worksheet.max_row, column=1).fill = PatternFill(
        "solid", fgColor=_KEY.lstrip("#"))
    worksheet.append([t["legend_distractor"]])
    worksheet.cell(row=worksheet.max_row, column=1).fill = PatternFill(
        "solid", fgColor=_ACCENT.lstrip("#"))
    worksheet.append([])
    worksheet.append([t["legend"]])
    worksheet.cell(row=worksheet.max_row, column=1).alignment = Alignment(
        wrap_text=True, vertical="top")
    worksheet.column_dimensions["A"].width = 6
    sheet.column_dimensions["B"].width = 96

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def filename(analysis, exam: Mapping[str, Any] | None, suffix: str) -> str:
    """`analisis-<exam>-<code>.<suffix>`, safe for a Content-Disposition header."""
    exam = exam or {}
    raw = f"{exam.get('title') or analysis.title or 'exam'}-{analysis.code or ''}"
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw).strip("-")
    while "--" in stem:
        stem = stem.replace("--", "-")
    return f"analisis-{stem[:60]}.{suffix}" if stem else f"analisis.{suffix}"


# ── the drawings ─────────────────────────────────────────────────────────────
#
# A filed analysis is read as a shape before it is read as a number: "which
# questions were too hard" is a picture first. reportlab's own chart classes are
# configured through a dozen attributes and fail *quietly* — a chart with no data
# renders an empty box, and a wrong axis range renders a plausible one — so these
# four drawings are made here, straight onto the canvas, from the same numbers the
# tables and the screen use. Every range comes from the data, an axis that nobody
# set is not invented, and a chart with nothing to draw says so in words.

#: Ink. Deliberately few: a chart that needs a legend to be read is a table.
_ACCENT = "#c2410c"      # bars and dots — the measurements
_KEY = "#0f766e"        # the option a question is keyed to
_MUTED = "#64748b"      # axes, ticks, captions
_GRID = "#e2e8f0"       # the frame
_STUDENT = "#0ea5e9"    # the ability histogram

#: What each finding looks like, as ink.
#:
#: **One palette, and the only one.** The page's item map, its logit chart, this
#: PDF and the image the copy button puts on the clipboard all read it. Two
#: readings of one paper that colour "weak discrimination" differently are two
#: readings nobody can place side by side in the same report — and the page's own
#: colours used to live in the Alpine component as five `rgba(...)` literals, which
#: is the same information written where no document could reach it.
#:
#: The hues are not arbitrary: red is a question that measured the wrong way,
#: amber one that barely measured, indigo one that measured nothing because
#: everybody answered it the same, and grey one that could not be measured at all.
#: A teacher acts on those four things and no more.
FLAG_TONES: dict[str, str] = {
    "ok": "#059669",
    "weak": "#d97706",
    "negative": "#dc2626",
    "misfit": "#dc2626",
    "extreme_easy": "#6366f1",
    "extreme_hard": "#6366f1",
    "unkeyed": "#78716c",
    "unscored": "#78716c",
}

#: The flag the palette falls back to, for a flag a newer analyser adds before
#: anybody writes it a colour. Grey rather than a random hue: an unknown finding is
#: "not classified here", which is what grey already means in this palette.
FLAG_FALLBACK = "#78716c"

#: The legend, in the order it is read: the groups a teacher acts on, one swatch
#: per group rather than one per flag, because "negative" and "misfit" are the same
#: act (rebuild the question) and eight swatches is a paragraph, not a key. Each
#: entry is (the flags it covers, the label key for that group).
FLAG_LEGEND: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ok",), "legend_ok"),
    (("weak",), "legend_weak"),
    (("negative", "misfit"), "legend_bad"),
    (("extreme_easy", "extreme_hard"), "legend_extreme"),
    (("unkeyed", "unscored"), "legend_unmeasured"),
)


#: Worksheet names, short on purpose.
#:
#: Excel refuses a sheet name over 31 characters or containing any of `: \\ / ? *
#: [ ]`. The label table is written for a *page* — "Daya beda kelompok atas dan
#: bawah" is 32 — so the sheets get their own names rather than a truncation of
#: the reader's language, which is how a workbook ends up with "Kemampuan murid
#: (skala logit)" cut in half. Each is checked against Excel's rules by a test.
SHEET_NAMES: dict[str, dict[str, str]] = {
    "summary": {"id": "Ringkasan", "en": "Summary"},
    "framework": {"id": "Kerangka analisis", "en": "Framework"},
    "cognitive": {"id": "Tingkat kognitif", "en": "Cognitive mix"},
    "mastery": {"id": "Ketuntasan", "en": "Mastery"},
    "items": {"id": "Butir soal", "en": "Items"},
    "options": {"id": "Pilihan jawaban", "en": "Options"},
    "groups": {"id": "Kelompok atas-bawah", "en": "Upper-lower"},
    "people": {"id": "Kemampuan murid", "en": "Students"},
    "key": {"id": "Cara membaca", "en": "How to read"},
}


def tone_for(flag: str) -> str:
    """The ink for a finding flag, with a defined answer for an unknown one."""
    return FLAG_TONES.get(flag, FLAG_FALLBACK)


def legend_entries(lang: str) -> list[tuple[str, str]]:
    """`(colour, label)` per group, for every surface that draws a key."""
    t = labels(lang)
    return [(tone_for(flags[0]), t[label_key])
            for flags, label_key in FLAG_LEGEND]


def chart_palette() -> dict[str, str]:
    """The palette as the browser wants it: `flag -> #rrggbb`."""
    return dict(FLAG_TONES)


def _round_tick(value: float) -> str:
    """A tick label: integers as integers, otherwise one decimal."""
    return f"{value:.0f}" if abs(value - round(value)) < 1e-9 else f"{value:.1f}"


def _fit(text: str, canvas, width: float, font: str, size: float) -> str:
    """`text` shortened with an ellipsis until it fits `width`.

    A one-line note in a fixed band has a budget, and the alternative to trimming
    it is what the first version did: draw it anyway, straight across the chart
    beside it. A clipped note is a worse sentence and a better document.
    """
    if canvas.stringWidth(text, font, size) <= width:
        return text
    for cut in range(len(text) - 1, 8, -1):
        candidate = text[:cut].rstrip() + "\u2026"
        if canvas.stringWidth(candidate, font, size) <= width:
            return candidate
    return text[:8] + "\u2026"


class _Chart:
    """One drawing, described by its data rather than by hundreds of attributes."""

    def __init__(self, kind: str, title: str, values=None, labels=None, keys=None,
                 x_label: str = "", y_label: str = "", points=None, columns=None,
                 column_keys=None, tones=None, note: str = ""):
        self.kind = kind          # "bars" | "hist" | "scatter" | "stacked"
        self.title = title
        self.values = list(values or [])
        self.labels = list(labels or [])
        self.keys = list(keys or [])          # True where a bar is a question's key
        # The ink per bar or per point, from `FLAG_TONES`. Empty means "the
        # measurement colour": a chart whose colours mean nothing gets no key and
        # needs none.
        self.tones = list(tones or [])
        # One line saying how to read *this* drawing. It rides in the box rather
        # than in a paragraph under the row of charts, where four hints ran
        # together into a block of six-point text nobody could match to a picture.
        self.note = note
        self.x_label = x_label
        self.y_label = y_label
        self.points = list(points or [])      # (x, y, label) for the scatter
        # One entry per column, each a list of values stacked bottom-up, with a
        # matching `column_keys` saying which entry is the key. Per column and
        # not one flag list for the whole chart: a matching question and a
        # four-option multiple choice do not have the same options in the same
        # order, so a single list would mark the wrong slice on one of them.
        self.columns = [list(column) for column in (columns or [])]
        self.column_keys = [list(flags) for flags in (column_keys or [])]

    def empty(self) -> bool:
        if self.kind == "scatter":
            return not self.points
        if self.kind == "stacked":
            return not any(any(column) for column in self.columns)
        return not self.values

    def ink(self, index: int, fallback: str) -> str:
        """The colour for bar/point `index`, or the chart's own colour."""
        if index < len(self.tones) and self.tones[index]:
            return self.tones[index]
        return fallback


class _ChartGrid(Flowable):
    """A row of charts, drawn as one flowable.

    One flowable rather than a Table of Drawings: a Table decides a cell's size
    from its own idea of the flowable, and a chart that disagrees with that is a
    chart that silently overlaps the next cell.
    """

    #: Room reserved at the top of a box for its title and its note line. The plot
    #: starts *below* this, which is the whole of one real defect: the bars used to
    #: grow into the title from underneath and the two overprinted.
    #:
    #: 30 rather than the 22 a title and a note line need, because the *topmost tick
    #: label* is drawn on the plot's top edge and its ascender reaches above it. At
    #: 22 that label climbed into the band where the y-axis caption sits — measured
    #: on the built file, `Daya beda (D)` and the tick `0.5` shared 2.9pt of height.
    #: A tick label is not a decoration to shuffle sideways: it is a scale number,
    #: so the band moves instead.
    HEAD = 30.0
    #: And at the bottom, for the tick labels and the axis caption.
    FOOT = 20.0
    #: From the boxes' bottom edge down to the first row of the colour key,
    #: baseline to edge. The key is painted from *its own* bottom edge upward, so
    #: without this the first row of a two-row key landed 1pt *inside* the boxes
    #: and printed over the x-axis captions underneath them — `Logit` and `Baik —
    #: soal bekerja seperti seharusnya.` overlapped by 4.6pt.
    KEY_GAP = 9.0
    #: And the translation that lands the key there. `_ChartLegend.draw` paints its
    #: last row 3 above its own origin and each earlier row 11 higher, so its first
    #: row's baseline sits at `height - 8` in its own space while the reserved band
    #: puts the boxes' edge at `KEY_GAP + height` — which makes the offset a
    #: constant, 8, however many rows the key needs. It was `legend.height - 9`,
    #: which is only right for a one-row key and is how the two-row one ended up
    #: six points high.
    KEY_ORIGIN = 8.0

    #: The legend rides *inside* this flowable rather than after it, and that is a
    #: fix rather than a preference: as its own flowable it was the last item of an
    #: already-full page, so the key landed on page 2 under nothing while the
    #: drawings it explains stayed on page 1.
    def __init__(self, charts: list[_Chart], empty_text: str, columns: int = 2,
                 box_height: float = 142.0, gap: float = 10.0,
                 legend: Flowable | None = None):
        super().__init__()
        self.charts = [c for c in charts if not c.empty()]
        self.empty_text = empty_text
        self.columns = max(1, columns)
        self.box_height = box_height
        self.gap = gap
        self.legend = legend
        self.width = 0.0
        self.rows = (len(self.charts) + self.columns - 1) // self.columns

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        self.height = (self.rows * self.box_height
                       + max(0, self.rows - 1) * self.gap) if self.charts else 0.0
        if self.legend is not None and self.charts:
            legend_w, legend_h = self.legend.wrap(avail_width, avail_height)
            del legend_w
            self.height += legend_h + self.KEY_GAP
        return self.width, self.height

    def draw(self):
        if not self.charts:
            return
        box_w = (self.width - (self.columns - 1) * self.gap) / self.columns
        for index, chart in enumerate(self.charts):
            row, col = divmod(index, self.columns)
            x = col * (box_w + self.gap)
            y = self.height - (row + 1) * self.box_height - row * self.gap
            self._draw_box(chart, x, y, box_w, self.box_height)
        if self.legend is not None:
            canvas = self.canv
            # A nested flowable is not bound to a canvas by reportlab — only the
            # one the frame draws gets one — so the legend is handed ours. Without
            # this it raised on `self.legend.draw()`.
            self.legend.canv = canvas
            canvas.saveState()
            canvas.translate(0, self.KEY_ORIGIN)
            self.legend.draw()
            canvas.restoreState()

    # ── the frame every chart shares ─────────────────────────────────────────

    def _draw_box(self, chart: _Chart, x0: float, y0: float, w: float, h: float):
        canvas = self.canv
        canvas.saveState()
        canvas.setStrokeColor(_GRID)
        canvas.setLineWidth(0.4)
        canvas.rect(x0, y0, w, h, stroke=1, fill=0)

        # The title band, and the axis names *in* it rather than over the plot:
        # `x` on the right where a reader looks for the horizontal axis, `y` on the
        # left of the line just under it. The note — how to read this drawing — sits
        # under the title, in the same band, so it cannot be mistaken for a caption
        # belonging to the chart beside it.
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor("#334155")
        canvas.drawString(x0 + 5, y0 + h - 10, chart.title)
        if chart.y_label:
            canvas.setFont("Helvetica", 6)
            canvas.setFillColor(_MUTED)
            canvas.drawString(x0 + 5, y0 + h - 19, chart.y_label)
        if chart.x_label:
            canvas.setFont("Helvetica", 6)
            canvas.setFillColor(_MUTED)
            canvas.drawRightString(x0 + w - 5, y0 + h - 10, chart.x_label)
        if chart.note:
            canvas.setFont("Helvetica", 5.5)
            canvas.setFillColor(_MUTED)
            canvas.drawRightString(x0 + w - 5, y0 + h - 19,
                                   _fit(chart.note, canvas, w - 12, "Helvetica", 5.5))

        # The plot area, inside a margin that leaves room for the tick labels and
        # for the axis caption at the foot of the box.
        px0, px1 = x0 + 30, x0 + w - 10
        py0, py1 = y0 + self.FOOT, y0 + h - self.HEAD
        if chart.kind == "scatter":
            self._scatter(chart, px0, py0, px1, py1)
        elif chart.kind == "stacked":
            self._stacked(chart, px0, py0, px1, py1)
        else:
            self._bars(chart, px0, py0, px1, py1)
        canvas.restoreState()

    def _axes(self, canvas, values, px0, py0, px1, py1) -> tuple[float, float]:
        """Vertical scale for a bar chart: 0 is always in range, and inked."""
        low = min(0.0, min(values))
        high = max(0.0, max(values))
        if high - low < 1e-9:
            high = low + 1
        span = high - low
        # Ten per cent of headroom, not six: at six a full-height bar reached into
        # the title band and the two printed over each other.
        low -= span * 0.08
        high += span * 0.10

        def y(value):
            return py0 + (value - low) / (high - low) * (py1 - py0)

        canvas.setStrokeColor(_GRID)
        canvas.setLineWidth(0.3)
        canvas.line(px0, y(0), px1, y(0))
        canvas.setStrokeColor(_MUTED)
        canvas.setFont("Helvetica", 5.5)
        canvas.setFillColor(_MUTED)
        for tick in (low, (low + high) / 2, high):
            canvas.line(px0 - 2, y(tick), px0, y(tick))
            canvas.drawRightString(px0 - 3, y(tick) - 1.8, _round_tick(tick))
        return y(0), y

    def _bars(self, chart: _Chart, px0, py0, px1, py1):
        canvas = self.canv
        canvas.setFont("Helvetica", 6)
        _, y = self._axes(canvas, chart.values, px0, py0, px1, py1)
        count = len(chart.values)
        step = (px1 - px0) / count
        bar_w = min(step * 0.72, 22.0)
        # Every label when they fit, otherwise every k-th: a row of overlapping
        # numbers reads as noise, which is worse than a sparse axis.
        every = 1 if count <= 16 else max(1, count // 12)
        label_y = py0 - 6
        for index, value in enumerate(chart.values):
            centre = px0 + step * (index + 0.5)
            top, base = y(value), y(0)
            key = bool(chart.keys[index]) if index < len(chart.keys) else False
            canvas.setFillColor(chart.ink(index, _KEY if key else _ACCENT))
            canvas.rect(centre - bar_w / 2, min(top, base), bar_w, abs(top - base),
                        stroke=0, fill=1)
            if index % every == 0 and index < len(chart.labels):
                canvas.setFillColor(_MUTED)
                canvas.drawCentredString(centre, label_y,
                                         str(chart.labels[index])[:6])
        canvas.setStrokeColor(_MUTED)
        canvas.line(px0, py0 - 2, px1, py0 - 2)
        if chart.x_label:
            canvas.setFont("Helvetica", 5.5)
            canvas.setFillColor(_MUTED)
            # 15, not 13: the tick labels sit at 6, and at 13 the caption's ascenders
            # met their descenders. Measured on the built file as a 2.6pt overlap
            # between the tick `50` and `Tingkat kesulitan (%)`.
            canvas.drawCentredString((px0 + px1) / 2, py0 - 15, chart.x_label)

    def _stacked(self, chart: _Chart, px0, py0, px1, py1):
        """One column per question, split by the option that earned each answer.

        A forty-question paper has two hundred options. Drawn as two hundred bars
        side by side — which is what this used to do — each one is a two-point
        hairline and the picture is a grey wall: every question looks identical
        because every bar is the same width, not because the answers are. Drawn as
        forty columns, one per question, the same numbers read: a thick orange
        slice is a distractor that took a real share of the class, a thin one is a
        distractor nobody chose, and a key that does not dominate its column is a
        question whose key is being argued with.

        Counts, not shares, so the column heights also say how many sat the
        question — a question half the class skipped is short, and that is worth
        seeing next to one everybody answered.
        """
        canvas = self.canv
        totals = [sum(column) for column in chart.columns]
        _, y = self._axes(canvas, totals, px0, py0, px1, py1)
        count = len(chart.columns)
        step = (px1 - px0) / count
        bar_w = min(step * 0.78, 26.0)
        for index, column in enumerate(chart.columns):
            centre = px0 + step * (index + 0.5)
            cursor = 0.0
            for option, value in enumerate(column):
                if not value:
                    continue
                flags = (chart.column_keys[index]
                         if index < len(chart.column_keys) else [])
                keyed = bool(flags[option]) if option < len(flags) else False
                canvas.setFillColor(_KEY if keyed else _ACCENT)
                bottom, top = y(cursor), y(cursor + value)
                canvas.rect(centre - bar_w / 2, bottom, bar_w, top - bottom,
                            stroke=0, fill=1)
                cursor += value
            # A hairline between the slices: two shades of one colour stacked with
            # no seam read as one tall bar, which is exactly the reading to avoid.
            if len([v for v in column if v]) > 1:
                canvas.setStrokeColor("#ffffff")
                canvas.setLineWidth(0.5)
                cursor = 0.0
                for value in column[:-1]:
                    cursor += value
                    if cursor:
                        canvas.line(centre - bar_w / 2, y(cursor),
                                    centre + bar_w / 2, y(cursor))
        # Numbered where the numbers fit and not where they would touch: the width
        # of a two-digit label decides how many questions it can sit under.
        canvas.setFont("Helvetica", 6)
        canvas.setFillColor(_MUTED)
        widest = max((canvas.stringWidth(str(label) + " ", "Helvetica", 6)
                      for label in chart.labels), default=0.0)
        every = max(1, int((widest + 1.0) / step) + 1)
        for index in range(0, count, every):
            if index < len(chart.labels):
                canvas.drawCentredString(px0 + step * (index + 0.5), py0 - 6,
                                         str(chart.labels[index]))
        canvas.setStrokeColor(_MUTED)
        canvas.line(px0, py0 - 2, px1, py0 - 2)
        if chart.x_label:
            canvas.setFont("Helvetica", 5.5)
            canvas.setFillColor(_MUTED)
            canvas.drawCentredString((px0 + px1) / 2, py0 - 13, chart.x_label)

    def _scatter(self, chart: _Chart, px0, py0, px1, py1):
        """Difficulty (x, 0-100) against discrimination (y).

        Both ranges are fixed by what the numbers mean rather than by the data:
        0-100% is what a difficulty *is*, and 0-1 what a discrimination can be
        (0.2 is the usual floor for a useful item). A range fitted to the data
        would make two questions look far apart when they are nearly identical.
        """
        canvas = self.canv
        xs = [p[0] for p in chart.points]
        ys = [p[1] for p in chart.points]
        x_low, x_high = 0.0, 100.0
        y_low = min(0.0, min(ys))
        y_high = max(0.5, max(ys))

        def x(v):
            return px0 + (v - x_low) / (x_high - x_low) * (px1 - px0)

        def y(v):
            return py0 + (v - y_low) / (y_high - y_low) * (py1 - py0)

        canvas.setStrokeColor(_GRID)
        canvas.setLineWidth(0.3)
        canvas.line(px0, y(0), px1, y(0))            # "no discrimination"
        canvas.line(x(50), py0, x(50), py1)          # a half-right question
        canvas.setStrokeColor(_MUTED)
        canvas.setFont("Helvetica", 5.5)
        canvas.setFillColor(_MUTED)
        # The tick labels are drawn first and their boxes kept, because a point
        # label placed *left* of the plot lands in the column where the y scale is
        # printed: on the built file the question labels `40` and `39` came out on
        # top of the `0` tick, sharing 6.2pt of height. A scale number is the one
        # label on a chart that must never be obscured, so the ticks are obstacles
        # the point labels have to avoid, like each other.
        taken: list[tuple[float, float, float, float]] = []
        for tick in (x_low, 50.0, x_high):
            canvas.line(x(tick), py0, x(tick), py0 - 2)
            # The same 6 as every other drawing: at 8 this chart's tick labels sat
            # closer to the x-axis caption than a tick and a caption can.
            canvas.drawCentredString(x(tick), py0 - 6, _round_tick(tick))
            half = canvas.stringWidth(_round_tick(tick), "Helvetica", 5.5) / 2
            taken.append((x(tick) - half, py0 - 7.2, x(tick) + half, py0 - 2.0))
        for tick in (y_low, (y_low + y_high) / 2, y_high):
            canvas.line(px0 - 2, y(tick), px0, y(tick))
            canvas.drawRightString(px0 - 3, y(tick) - 1.8, _round_tick(tick))
            span = canvas.stringWidth(_round_tick(tick), "Helvetica", 5.5)
            taken.append((px0 - 3 - span, y(tick) - 3.0, px0 - 3, y(tick) + 2.2))
        # Labels are placed into the first free of four positions, and dropped
        # rather than overlapped when none is free. Two questions in the same
        # corner of the map — which is *normal*, a hard paper clusters there — used
        # to print two numbers on top of each other, and a tangle of digits reads
        # as neither item.
        #
        # 5.4 tall, not the 4.2 this first used: a 5pt label reports a 7pt box on
        # the built page, so labels the rule had called free came out touching —
        # `24` and `29` shared 3.6pt of height. The four offsets are derived from
        # that height and a clearance, so they move together if either changes.
        ink = 5.4
        step = ink + 1.2
        for position, point in enumerate(chart.points):
            xv, yv, label = point[0], point[1], point[2]
            flag = point[3] if len(point) > 3 else ""
            canvas.setFillColor(chart.ink(position, tone_for(flag) if flag else _ACCENT))
            canvas.circle(x(xv), y(yv), 2.0, stroke=0, fill=1)
            canvas.setFont("Helvetica", 5)
            text = str(label)[:4]
            width = canvas.stringWidth(text, "Helvetica", 5) + 1
            for dx, dy, anchor in ((2.4, -ink / 2, "left"),
                                   (-2.4 - width, -ink / 2, "left"),
                                   (0, -ink / 2 + step, "centre"),
                                   (0, -ink / 2 - step, "centre")):
                left = x(xv) + dx if anchor == "left" else x(xv) + dx - width / 2
                box = (left, y(yv) + dy, left + width, y(yv) + dy + ink)
                if any(box[0] < t[2] and t[0] < box[2]
                       and box[1] < t[3] and t[1] < box[3] for t in taken):
                    continue
                taken.append(box)
                canvas.setFillColor(_MUTED)
                if anchor == "left":
                    canvas.drawString(box[0], box[1] + 1, text)
                else:
                    canvas.drawCentredString(x(xv) + dx, box[1] + 1, text)
                break
        if chart.x_label:
            canvas.setFont("Helvetica", 5.5)
            canvas.setFillColor(_MUTED)
            canvas.drawCentredString((px0 + px1) / 2, py0 - 15, chart.x_label)


class _ChartLegend(Flowable):
    """The key under the drawings, as swatches with words beside them.

    It exists because the drawings have colours that *mean* something and the file
    is read on its own: a teacher opening a PDF a term later has the shapes and
    none of the page's context, and a red dot with nothing saying what red is is a
    decoration. The same shape is what the copy button composes into the image it
    puts on the clipboard, so a chart pasted into a report carries its key with it
    rather than depending on the document it was cut from.

    Wraps rather than truncates: a legend that hides its last entry to fit one
    line is a legend that lies by omission, and the labels are long by design.
    """

    SWATCH = 6.0
    GAP = 4.0
    BETWEEN = 16.0

    def __init__(self, entries: list[tuple[str, str]], heading: str = "",
                 extra: list[tuple[str, str]] | None = None,
                 empty_text: str = ""):
        super().__init__()
        self.entries = list(entries)
        self.heading = heading
        self.extra = list(extra or [])
        self.empty_text = empty_text
        self.width = 0.0
        self.rows: list[list[tuple[float, str, str]]] = []

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        # The heading is drawn inline, as the first item of the first row, so it
        # cannot end up stranded on a line of its own above a wrapped legend.
        items: list[tuple[str, str]] = ([] if not self.heading
                                       else [("", self.heading)])
        items = items + [(colour, text) for colour, text in self.entries]
        items = items + [(colour, text) for colour, text in self.extra]
        self.rows = []
        row: list[tuple[float, str, str]] = []
        used = 0.0
        for colour, text in items:
            width = self._measure(colour, text)
            if row and used + self.BETWEEN + width > avail_width:
                self.rows.append(row)
                row, used = [], 0.0
            if row:
                used += self.BETWEEN
            row.append((self._offset(used), colour, text))
            used += width
        if row:
            self.rows.append(row)
        self.height = max(1, len(self.rows)) * 11.0
        return self.width, self.height

    def _measure(self, colour: str, text: str) -> float:
        # `pdfmetrics.stringWidth` rather than `self.canv.stringWidth`: `wrap()` runs
        # before a flowable is bound to a canvas, so measuring through the canvas
        # raised `'_ChartLegend' object has no attribute 'canv'` the first time the
        # legend was measured — which is to say, every time.
        if not colour:
            return stringWidth(text, "Helvetica-Bold", 6.5)
        return self.SWATCH + self.GAP + stringWidth(text, "Helvetica", 6.5)

    def _offset(self, used: float) -> float:
        return used

    def draw(self):
        if not self.rows:
            return
        canvas = self.canv
        canvas.saveState()
        for row_index, row in enumerate(self.rows):
            y = self.height - (row_index + 1) * 11.0 + 3.0
            for x, colour, text in row:
                if not colour:
                    canvas.setFont("Helvetica-Bold", 6.5)
                    canvas.setFillColor("#334155")
                    canvas.drawString(x, y, text)
                    continue
                canvas.setFillColor(colour)
                canvas.rect(x, y + 0.5, self.SWATCH, self.SWATCH, stroke=0, fill=1)
                canvas.setFont("Helvetica", 6.5)
                canvas.setFillColor("#475569")
                canvas.drawString(x + self.SWATCH + self.GAP, y, text)
        canvas.restoreState()


def _chart_specs(analysis, lang: str) -> list[_Chart]:
    """The four drawings, from the analysis the screen renders.

    A question with no calibration has no place on the logit chart and no point
    on the item map, so it is left out of *those* drawings and still appears in
    the item table — the same decision `_chart_payload` makes for the page.
    """
    t = labels(lang)
    items = analysis.items
    # Every point and every bar carries the finding it belongs to, so the colour in
    # the file is the colour on the screen and the key under the drawings is honest
    # about both.
    mapped = [item for item in items
              if item.pct is not None and item.discrimination is not None]
    calibrated = [item for item in items if item.measure is not None]
    charts = [
        _Chart("scatter", t["item_map"], x_label=t["pct_axis"],
               y_label=t["disc_axis"], note=t["map_hint"],
               points=[(item.pct, item.discrimination, item.index + 1, item.flag)
                       for item in mapped]),
        _Chart("bars", t["difficulty_chart"], x_label=t["logit_axis"],
               y_label=t["logit_axis"], note=t["difficulty_hint"],
               values=[item.measure for item in calibrated],
               labels=[item.index + 1 for item in calibrated],
               tones=[tone_for(item.flag) for item in calibrated]),
        # The histogram counts *people*, not choices: `chosen_count` ("Jumlah
        # pemilih") is the option chart's axis, and printing it here labelled the
        # students themselves as voters — the workbook for the same drawing already
        # said `legend_students`, so the two documents disagreed about one chart.
        _Chart("hist", t["people_chart"], x_label=t["logit_axis"],
               y_label=t["legend_students"], note=t["people_hint"],
               values=[count for _centre, count in analysis.person_bins],
               labels=[_round_tick(centre) for centre, _count in analysis.person_bins]),
    ]
    # The distractor bars read as one question's options in a row: "3A" is option
    # A of question 3, and the keyed option is filled in the other colour, because
    # the shape of a distractor is "chosen by the weak" only next to its key.
    columns, names, keys = [], [], []
    for item in items:
        if not item.distractors:
            continue
        columns.append([option.count for option in item.distractors])
        names.append(item.index + 1)
        keys.append([bool(option.key) for option in item.distractors])
    if columns:
        charts.append(_Chart("stacked", t["distractor_chart"],
                             x_label=t["item_no_axis"], y_label=t["chosen_count"],
                             note=t["distractor_hint"],
                             columns=columns, labels=names, column_keys=keys))
    return charts


def chart_key(lang: str, charts: list[_Chart]) -> list[tuple[str, str]]:
    """The swatches the drawings in `charts` actually use.

    A key for a colour no drawing on the page uses is a paragraph about a palette,
    so the sections are conditional: the flag key appears when a chart colours by
    finding, and the option key when a question's options are drawn stacked.
    """
    t = labels(lang)
    # The item map is coloured by finding whatever else is true of it, and the
    # difficulty bars carry their own tones; a key with no drawing behind it is an
    # explanation of a palette.
    uses_flags = any(chart.kind == "scatter" or chart.tones for chart in charts)
    uses_options = any(chart.kind == "stacked" for chart in charts)
    key: list[tuple[str, str]] = []
    if uses_flags:
        key.extend(legend_entries(lang))
    if uses_options:
        key.append((_KEY, t["legend_key"]))
        key.append((_ACCENT, t["legend_distractor"]))
    return key


def analysis_pdf(analysis, exam: Mapping[str, Any] | None = None,
                 school: str = "", teacher: str = "", lang: str = "id",
                 public: bool = False, framework=None) -> bytes:
    """The analysis as a filed report: identity, summary, charts, items, students.

    Landscape, because the item table is sixteen columns wide and a portrait A4
    would either shrink it past reading or split it across pages.

    `public=True` is the report generated from a share link: no students table,
    and a line saying so. The identity block keeps the exam, not the teacher — a
    stranger reading a shared report needs to know which paper it is.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (CondPageBreak, KeepTogether, PageBreak,
                                    Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    lang = language(lang)
    t = labels(lang)
    framework = framework or af.resolve(None)
    exam = exam or {}
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=8, leading=10)
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=15, leading=18,
                           spaceAfter=2)
    heading = ParagraphStyle("heading", parent=styles["Heading2"], fontSize=10.5,
                             leading=13, spaceBefore=8, spaceAfter=3)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=f"{t['title']} - {exam.get('title') or analysis.title}")
    flow: list[Any] = [Paragraph(str(t["title"]), title)]

    identity = [f"{t['exam']}: <b>{exam.get('title') or analysis.title}</b>"]
    if school:
        identity.append(f"{t['school']}: {school}")
    if teacher:
        identity.append(f"{t['teacher']}: {teacher}")
    if analysis.code:
        identity.append(f"ID: {analysis.code}")
    identity.append(f"{t['language']}: {'English' if lang == 'en' else 'Bahasa Indonesia'}")
    identity.append(f"{t['framework']}: <b>{framework.words(lang)['name']}</b>")
    if public:
        identity.append(f"<b>{t['shared']}</b>")
    flow.append(Paragraph(" &nbsp;|&nbsp; ".join(identity), body))

    # ── the framework this report is ─────────────────────────────────────────
    # Its own block on page one, before any number. A filed report is read months
    # later by somebody who did not choose the framework, and "what question does
    # this answer, and what can it not answer" is the sentence that decides whether
    # the rest is quoted for the right thing. `framework_rows` is what the CSV and
    # the workbook print, so the three documents make the same promise.
    flow.append(Paragraph(str(t["framework"]), heading))
    promise = [[Paragraph(str(label), ParagraphStyle(
                    "fl", parent=body, fontSize=7, leading=9,
                    textColor=colors.HexColor("#334155"))),
                Paragraph(str(value), ParagraphStyle("fv", parent=body, fontSize=8,
                                                     leading=10))]
               for label, value in framework_rows(framework, lang)]
    promise_table = Table(promise, colWidths=[52 * mm, 178 * mm])
    promise_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    flow.append(promise_table)

    summary = analysis.summary
    flow.append(Paragraph(str(t["summary"]), heading))
    pairs = [
        (t["students"], summary.students), (t["item_count"], summary.items),
        (t["objective"], summary.objective), (t["essays"], summary.essays),
        (t["papers"], summary.answered_papers), (t["mean"], summary.total_mean),
        (t["sd"], summary.total_sd), (t["alpha"], summary.alpha),
        (t["kr20"], summary.kr20), (t["sem"], summary.sem),
        (t["person_rel"], summary.person_reliability),
        (t["person_sep"], summary.person_separation),
        (t["item_rel"], summary.item_reliability),
        (t["item_sep"], summary.item_separation),
        (t["measure_mean"], summary.mean_measure),
        (t["measure_sd"], summary.sd_measure),
        (t["extremes"], summary.extremes),
    ]
    # A labelled grid rather than a sentence, so a number can be found without
    # reading. Each cell is a **Paragraph**, not a string: reportlab renders a
    # plain string literally, so the markup below used to arrive in the PDF as
    # text — `<font size=7 ...>Students</font><br/>1` printed verbatim, which both
    # looked broken and pushed the last column past the right margin.
    cell_style = ParagraphStyle("cell", parent=body, fontSize=8, leading=10)
    cells = [
        Paragraph(f"<font size=7 color='#666666'>{name}</font><br/>"
                  f"{_num(value, 3) or '-'}", cell_style)
        for name, value in pairs
    ]
    rows = [cells[i:i + 5] for i in range(0, len(cells), 5)]
    while len(rows[-1]) < 5:
        rows[-1].append(Paragraph("", cell_style))
    summary_table = Table(rows, colWidths=[52 * mm] * 5)
    summary_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    flow.append(summary_table)

    # ── Winsteps' separation block ───────────────────────────────────────────
    # Its own grid rather than more cells in the one above, because the shape is
    # the message: two rows per side, REAL under MODEL, sharing the logit scale's
    # errors. `separation_rows` is what the CSV and the workbook print too, so the
    # number cannot differ between the three documents.
    block = separation_rows(summary, lang, text=False)
    # Only for the framework whose promise it is: a classical report prints no
    # separation and no logit scale, because it publishes neither.
    if framework.shows("separation") and any(value != "" for row in block[1:]
                                             for value in row[1:]):
        flow.append(Paragraph(str(t["separation_block"]), heading))
        head_style = ParagraphStyle("sephead", parent=body, fontSize=7.5, leading=9,
                                    textColor=colors.HexColor("#334155"))
        number_style = ParagraphStyle("sepnum", parent=body, fontSize=7.5, leading=9,
                                      alignment=2)
        grid = [[Paragraph(str(cell), head_style) if isinstance(cell, str)
                 else Paragraph(_num(cell, digits), number_style)
                 for cell, digits in zip(row, [0, 2, 2, 2, 2, 3])]
                for row in block]
        separation_table = Table(grid, colWidths=[46 * mm, 32 * mm, 32 * mm,
                                                  32 * mm, 26 * mm, 32 * mm])
        separation_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        flow.append(separation_table)

    # ── the drawings ─────────────────────────────────────────────────────────
    # Before the tables, and that order is the point: a teacher opens this file
    # to see *which* questions misbehaved, and the answer is a shape. The tables
    # that follow are where the shape gets checked against its numbers.
    charts = _chart_specs(analysis, lang)
    flow.append(Paragraph(str(t["charts"]), heading))
    drawn = [chart for chart in charts if not chart.empty()]
    if drawn:
        # Two drawings to a row, so a paper of five questions and a paper of forty
        # get the same size picture: the box is the box, and the number of items
        # decides how many there are, not how big each one is.
        key = chart_key(lang, drawn)
        legend = (_ChartLegend(key, heading=str(t["chart_legend"])) if key else None)
        flow.append(_ChartGrid(drawn, str(t["no_chart"]), legend=legend))
    else:
        flow.append(Paragraph(str(t["no_chart"]), body))

    # The tables start on a page of their own. The alternative was measured: the
    # charts filled the first page, the item heading and its legend landed at the
    # foot of it, and the table they belong to opened the next — a heading stranded
    # from its table, which is the defect the first version of this document had.
    if drawn:
        flow.append(PageBreak())
    flow.append(Paragraph(str(t["items"]), heading))
    flow.append(Paragraph(str(t["legend"]), body))
    item_rows = analysis_rows(analysis, lang, compact=True)
    # Explicit widths, scaled to the content box. Left to itself reportlab sizes a
    # column to its longest cell, so one long "Negative discrimination" or a wide
    # Indonesian flag label pushed the table past the page margin — the defect the
    # summary grid had as well, one column over.
    weights = [16, 62, 30, 36, 30, 34, 30, 38, 42, 34, 24, 26, 30, 34, 36, 96]
    available = doc.width
    item_widths = [available * w / sum(weights) for w in weights]
    item_table = Table(item_rows, repeatRows=1, colWidths=item_widths)
    item_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
        ("ALIGN", (2, 1), (-2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    flow.append(item_table)

    # Each section after the first can begin on whatever page has room; the rule is
    # only that a heading never ends a page with its table overleaf.
    flow.append(CondPageBreak(90))
    flow.append(Paragraph(str(t["splits"]), heading))
    split_rows = [[t["no"], t["upper"], t["lower"], t["t"], t["df"], t["p_value"]]]
    for split in analysis.splits:
        split_rows.append([str(split.index + 1), _num(split.upper, 3),
                           _num(split.lower, 3), _num(split.t, 2),
                           _num(split.df, 1), _num(split.p, 4)])
    if len(split_rows) == 1:
        flow.append(Paragraph(str(t["empty"]), body))
    else:
        # Percentages, not fractions: reportlab reads a bare number as *points*
        # (only `"30%"` and `"*"` are relative), so `[0.09, 0.22, …]` collapsed
        # every column into the same half-point and printed the table on top of
        # itself.
        split_table = Table(split_rows, repeatRows=1,
                            colWidths=["9%", "22%", "22%", "16%", "13%", "18%"])
        split_table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ]))
        flow.append(split_table)

    if not public:
        flow.append(CondPageBreak(90))
        flow.append(Paragraph(str(t["people"]), heading))
        people_rows = _person_rows(analysis, lang)
        people_table = Table(people_rows, repeatRows=1,
                             colWidths=["34%", "11%", "11%", "11%", "11%", "11%", "11%"])
        people_table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ]))
        flow.append(people_table)

    # ── the cognitive mix, and the mastery verdict ───────────────────────────
    # Each only when the chosen framework publishes it. They are the two blocks
    # this app never printed before, and both answer with the *school's* own
    # documents: the kisi-kisi and the KKM.
    if framework.shows("cognitive"):
        mix = cognitive_payload(analysis)
        flow.append(CondPageBreak(80))
        flow.append(Paragraph(str(t["cognitive"]), heading))
        flow.append(Paragraph(
            str(t["cognitive_basis_marks"] if mix["basis"] == "marks"
                else t["cognitive_basis_questions"]), body))
        band_rows = [[t["band"], t["item_count"], t["marks"], t["share"]]]
        for band in mix["bands"]:
            band_rows.append([_pair(af.BAND_NAMES, band["key"], lang),
                              _num(band["count"], 0), _num(band["marks"], 2),
                              _num(band["share"], 1)])
        # The unlabelled row is inside the grid, not a footnote: it is the row that
        # says whether this table is a kisi-kisi check or a statement about an
        # empty one.
        band_rows.append([t["unlabelled"], _num(mix["unset"], 0),
                          _num(mix["unset_marks"], 2), ""])
        band_table = Table(band_rows, colWidths=[70 * mm, 30 * mm, 30 * mm, 30 * mm])
        band_table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ]))
        flow.append(band_table)
        levels = [[t["no"], t["level"], t["band"], t["marks"]]]
        for question in mix["questions"]:
            found = af.level(question["level"])
            levels.append([str(question["no"]),
                           found.name[lang] if found else t["unlabelled"],
                           _pair(af.BAND_NAMES, question["band"], lang)
                           if question["band"] else "",
                           _num(question["marks"], 2)])
        level_table = Table(levels, repeatRows=1,
                            colWidths=[20 * mm, 55 * mm, 55 * mm, 25 * mm])
        level_table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
        ]))
        flow.append(level_table)

    if framework.shows("mastery"):
        verdict = mastery_payload(analysis)
        flow.append(CondPageBreak(80))
        flow.append(Paragraph(str(t["mastery"]), heading))
        if not verdict["configured"]:
            # The absence of a verdict, said out loud. Five rows of zeros would
            # read as a standard that nobody met, when the truth is that no
            # standard was set.
            flow.append(Paragraph(str(t["mastery_unconfigured"]), body))
        else:
            cells = [
                (t["kkm"], verdict["kkm"], 0), (t["passed"], verdict["passed"], 0),
                (t["failed"], verdict["failed"], 0),
                (t["pass_rate"], verdict["pass_rate"], 1),
                (t["class_mean"], verdict["mean"], 2),
                (t["lowest"], verdict["lowest"], 2),
                (t["gap"], verdict["gap"], 2),
                (t["threshold"], round(verdict["kkm"] / 100.0, 2), 2),
                (t["mastered"], f"{verdict['items_mastered']} / {verdict['items_measured']}", None),
            ]
            grid = [[Paragraph(f"<font size=7 color='#666666'>{name}</font><br/>"
                               f"{_num(value, digits) if digits is not None else value or '-'}",
                               ParagraphStyle("mv", parent=body, fontSize=8, leading=10))
                     for name, value, digits in cells[i:i + 5]]
                    for i in range(0, len(cells), 5)]
            mastery_table = Table(grid, colWidths=[46 * mm] * 5)
            mastery_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            flow.append(mastery_table)
            if verdict["questions"]:
                flow.append(Spacer(1, 3))
                question_rows = [[t["no"], t["full"], t["answered"], t["pct"],
                                  t["mastered"]]]
                for question in verdict["questions"]:
                    question_rows.append([str(question["no"]), _num(question["full"], 0),
                                          _num(question["answered"], 0),
                                          _num(question["pct"], 1),
                                          t["yes"] if question["mastered"] else t["no"]])
                question_table = Table(question_rows, repeatRows=1,
                                       colWidths=[20 * mm, 25 * mm, 25 * mm, 25 * mm, 30 * mm])
                question_table.setStyle(TableStyle([
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
                    ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d8dee8")),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ]))
                flow.append(question_table)

    shown_notes = af.notes_for(analysis.notes, framework)
    if shown_notes:
        flow.append(Spacer(1, 4))
        notes = [Paragraph(str(t["notes"]), heading)]
        for note in shown_notes:
            notes.append(Paragraph("&bull; " + _pair(NOTE_LABELS, note, lang), body))
        flow.append(KeepTogether(notes))

    doc.build(flow)
    return buf.getvalue()
