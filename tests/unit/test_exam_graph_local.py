"""The graph panel's own calculator, executed — because a panel that needs a key
is a panel a school without one cannot use.

Desmos issues API keys to an account holder, and its free tier covers school or
personal projects rather than a commercial service, so `DESMOS_API_KEY` is empty
on this deployment and was on the last. The panel used to answer that with a
sentence; it now draws the curve itself, from `exam-graph-local.js` and the
Chart.js every page already carries.

Two things are checked here rather than asserted in a comment:

**The formula reader is arithmetic.** `2x^2` is `2·x²` and not `(2x)²`, `-x^2` is
negative, `2^3^2` is right-associative, and a name this reader does not know is a
typo rather than a variable. It is a recursive-descent parser returning closures
— **no `eval`** — because the text is typed by a pupil inside the page that holds
their answers.

**The mount draws, zooms and lets go.** jsdom has no canvas, so Chart is stubbed;
what is proved is the module's own side of the contract: a curve sampled from the
expression, a view that pans and zooms, an error that leaves the last good curve
on the axes, and a `destroy()` that takes the canvas out of the page.

The honest limit: jsdom does not paint, so this proves the sampling and the DOM
operations, not the pixels.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "app" / "static" / "js" / "exam-graph-local.js"
TAKE = ROOT / "app" / "templates" / "student" / "take_exam.html"

NODE = shutil.which("node")

HARNESS = r"""
const { JSDOM } = require('jsdom');
const fs = require('fs');

const dom = new JSDOM(
    '<!doctype html><html><body><div id="host" style="width:600px;height:440px"></div></body></html>',
    { pretendToBeVisual: true });
const { window } = dom;
window.eval(fs.readFileSync(process.env.SG_GRAPH_LOCAL, 'utf8'));
const api = window.SGGraphLocal;

const results = [];
function record(name, got, want) { results.push([name, got, want]); }
function round(v) { return Math.round(v * 1e6) / 1e6; }

function value(src, x) { return round(api.compile(src).fn(x)); }
function code(src) {
    try { api.compile(src); return 'compiled'; } catch (err) { return err.code || 'error'; }
}

/* ── the reader ──────────────────────────────────────────────────────────── */
record('a plain expression', value('2x + 1', 2), 5);
record('power before multiplication', value('2x^2', 3), 18);
record('unary minus is not a subtraction', value('-x^2', 3), -9);
record('powers are right associative', value('2^3^2', 0), 512);
record('a negative exponent', value('2^-1', 0), 0.5);
record('a bracket multiplies', value('3(x+1)', 1), 6);
record('two brackets multiply', value('(x+1)(x-1)', 5), 24);
record('a number before a bracket', value('2(3)', 0), 6);
record('x before a bracket', value('x(2)', 3), 6);
record('functions read as functions',
       value('sqrt(4) + ln(e) + abs(-3) + log(100)', 0), 8);
record('trigonometry is in radians', value('sin(pi/2)', 0), 1);
record('a constant is a number', Math.round(api.compile('pi').fn(0) * 1000), 3142);
record('y= is a label, not a function part', api.compile('y = x + 1').text, 'x + 1');
record('and the label is stripped, not evaluated', value('y = x + 1', 1), 2);
record('empty is its own code', code('   '), 'empty');
record('a dangling operator is a syntax error', code('x^'), 'syntax');
record('an unknown name is a typo', code('z + 1'), 'syntax');
record('a function without an argument', code('sin'), 'syntax');
record('a stray character is refused', code('x @ 2'), 'syntax');
record('division by zero is Infinity, not a crash', String(api.compile('1/x').fn(0)), 'Infinity');
record('the root of a negative is NaN, not a crash', String(api.compile('sqrt(-1)').fn(0)), 'NaN');

/* ── the mount ───────────────────────────────────────────────────────────── */
class FakeChart {
    constructor(ctx, config) {
        this.ctx = ctx;
        this.config = config;
        this.data = config.data;
        this.options = config.options;
        this.updates = 0;
        this.destroyed = false;
        FakeChart.last = this;
    }
    update() { this.updates += 1; }
    resize() { return true; }
    destroy() { this.destroyed = true; }
}
window.Chart = FakeChart;

const host = window.document.getElementById('host');
const plot = api.mount(host);
record('a host gets a canvas', !!host.querySelector('canvas'), true);
record('the chart is built from the config', !!FakeChart.last, true);

record('the first curve needs no key', plot.setExpression('x^2').ok, true);
const data = plot.chart.data.datasets[0].data;
record('the curve is sampled', data.length > 100, true);
const near = data.filter((p) => p && Math.abs(p.x - 2) < 0.02).pop();
record('a sample is the expression', round(near.y), 4);
record('the first view is ten either side',
       JSON.stringify(plot.view()), JSON.stringify({ x0: -10, x1: 10, y0: -10, y1: 10 }));

plot.zoom(2);
record('zoom narrows the view',
       JSON.stringify([plot.view().x0, plot.view().x1]), JSON.stringify([-5, 5]));
plot.reset();
record('reset restores it', plot.view().x0, -10);

record('an empty formula is reported', plot.setExpression('').code, 'empty');
record('a broken formula is reported', plot.setExpression('x^').code, 'syntax');
record('and the last good curve stays',
       plot.chart.data.datasets[0].data.length, data.length);
record('a good formula clears the way back', plot.setExpression('2x').ok, true);

plot.destroy();
record('destroy takes the canvas with it', host.querySelector('canvas'), null);
record('and stops the chart', FakeChart.last.destroyed, true);
record('no host is no plot', api.mount(null), null);

process.stdout.write(JSON.stringify(results));
"""


def _run_harness():
    env = dict(os.environ)
    env["SG_GRAPH_LOCAL"] = str(MODULE)
    proc = subprocess.run([NODE, "-e", HARNESS], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return {name: (got, want) for name, got, want in json.loads(proc.stdout)}


needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the reader")
needs_jsdom = pytest.mark.skipif(
    NODE is None or subprocess.run(
        [NODE, "-e", "require.resolve('jsdom')"], cwd=str(ROOT),
        capture_output=True).returncode != 0,
    reason="needs the jsdom dev dependency (`npm install`)",
)


@needs_node
@needs_jsdom
class TestTheReaderAndTheMount:
    @pytest.fixture(scope="class")
    def results(self):
        return _run_harness()

    @pytest.mark.parametrize("name", [
        "a plain expression",
        "power before multiplication",
        "unary minus is not a subtraction",
        "powers are right associative",
        "a negative exponent",
        "a bracket multiplies",
        "two brackets multiply",
        "a number before a bracket",
        "x before a bracket",
        "functions read as functions",
        "trigonometry is in radians",
        "a constant is a number",
        "y= is a label, not a function part",
        "and the label is stripped, not evaluated",
        "empty is its own code",
        "a dangling operator is a syntax error",
        "an unknown name is a typo",
        "a function without an argument",
        "a stray character is refused",
        "division by zero is Infinity, not a crash",
        "the root of a negative is NaN, not a crash",
        "a host gets a canvas",
        "the chart is built from the config",
        "the first curve needs no key",
        "the curve is sampled",
        "a sample is the expression",
        "the first view is ten either side",
        "zoom narrows the view",
        "reset restores it",
        "an empty formula is reported",
        "a broken formula is reported",
        "and the last good curve stays",
        "a good formula clears the way back",
        "destroy takes the canvas with it",
        "and stops the chart",
        "no host is no plot",
    ])
    def test_case(self, results, name):
        got, want = results[name]
        assert got == want, f"{name}: {got!r} != {want!r}"


class TestTheModuleIsSafeAndLocal:
    def test_the_formula_is_never_evaluated(self):
        js = MODULE.read_text(encoding="utf-8")
        assert "eval(" not in js, (
            "a formula typed by a pupil must not be run as code in the page that "
            "holds their answers"
        )
        assert "new Function" not in js

    def test_it_fetches_nothing(self):
        js = MODULE.read_text(encoding="utf-8")
        assert "http://" not in js and "https://" not in js, (
            "the point of this engine is that a school link that cannot reach "
            "Desmos still gets a graph"
        )
        assert "XMLHttpRequest" not in js and "fetch(" not in js

    def test_it_draws_on_the_chartjs_the_app_already_ships(self):
        js = MODULE.read_text(encoding="utf-8")
        assert "var Chart = root.Chart;" in js
        assert (ROOT / "app" / "static" / "vendor" / "chart.umd.min.js").is_file(), (
            "the module's whole cost argument is that every page already loads this"
        )


class TestThePageWiresItUp:
    def test_the_module_is_on_the_exam_page(self):
        text = TAKE.read_text(encoding="utf-8")
        assert '<script src="/static/js/exam-graph.js"></script>' in text
        assert '<script src="/static/js/exam-graph-local.js"></script>' in text

    def test_no_key_is_not_a_failure(self):
        text = TAKE.read_text(encoding="utf-8")
        start = text.index("        async openGraph(force) {")
        end = text.index("        // ── The keyless plot", start)
        body = text[start:end]
        assert "if (!this.graphKey) {" in body
        assert "this.mountGraphLocal();" in body, (
            "an empty key must mount the local plot, not report the key as missing"
        )

    def test_the_local_controls_are_the_page_and_the_buttons_are_fingers(self):
        text = TAKE.read_text(encoding="utf-8")
        for needle in ("plotGraph()", "graphZoom(1.5)", "graphZoom(1 / 1.5)", "graphReset()"):
            assert needle in text, needle
        assert 'x-show="graphMode === \'local\'"' in text
        # The controls a pupil presses during a paper answer to the same 44px floor
        # as every other control on this page.
        assert 'class="w-11 h-11 rounded-lg border border-surface-300 bg-white text-surface-700"' in text
        assert 'class="flex-1 min-w-0 h-11 px-3 rounded-lg border text-xs font-mono bg-white text-surface-900"' in text

    def test_every_string_the_new_panel_shows_is_a_pair(self):
        text = TAKE.read_text(encoding="utf-8")
        for pair in (
            "t('Grafik','Graph')",
            "t('Contoh: x^2 - 4','e.g. x^2 - 4')",
            "t('Rumus untuk digambar','Formula to plot')",
            "t('Gambar','Plot')",
            "t('Perbesar','Zoom in')",
            "t('Perkecil','Zoom out')",
            "t('Kembalikan tampilan','Reset the view')",
            "t('Tulis rumusnya dulu','Type the formula first')",
            "t('Rumus belum bisa dibaca','That formula could not be read')",
            "t('Kalkulator lokal','Local calculator')",
        ):
            assert pair in text, pair

    def test_the_panel_makes_room_for_its_own_controls(self):
        text = TAKE.read_text(encoding="utf-8")
        assert "flex flex-col bg-white rounded-2xl shadow-2xl border border-surface-200 overflow-hidden" in text
        assert '<div class="relative flex-1 min-h-0">' in text, (
            "the controls sit above the plot, so the plot has to take what is left "
            "rather than the panel's full height"
        )
