"""Execute the exam's real handlers with both tablet event orders and a fake clock.

This reproduces Android's iPad-only exemption gap and fullscreenchange arriving
before orientationchange. A browser simulation is not a physical tablet sign-off.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[2] / "app/templates/student/take_exam.html"
NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const page = fs.readFileSync(process.argv[2], 'utf8');
const between = (a,b) => page.slice(page.indexOf(a), page.indexOf(b,page.indexOf(a)));
const methods = between('checkFullscreen() {','async resumeFullscreen() {') +
 between('recordNotCharged(kind, trigger) {', "_applyViolationBanner(n, kind = 'tab_switch') {");
const M = eval('({' + methods + '})');
const setup = between("document.addEventListener('visibilitychange', () => {",
 '            // Fullscreen: mandatory and blocking.');
let now, timers, id, events, charges, rows, handlers, mediaHandlers, focused;
function tick(ms) {
 const end = now + ms;
 while (true) {
  const t = [...timers.entries()].filter(([i,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
  if(!t) break;
  timers.delete(t[0]); now=t[1].at; t[1].fn();
 }
 now=end;
}
global.setTimeout = (fn, ms=0) => {timers.set(++id,{fn,at:now+ms});return id;};
global.clearTimeout = i => timers.delete(i);
Date.now = () => now;
function fresh(platform='android') {
 now=1000000; timers=new Map(); id=0; events=[]; charges=[]; rows=[];
 handlers={};mediaHandlers=[];focused=true;
 const on=(name,fn)=>{(handlers[name] ||= []).push(fn);};
 global.window=global;
 global.addEventListener=on;
 global.matchMedia=()=>({addEventListener:(name,fn)=>mediaHandlers.push(fn)});
 global.screen={orientation:{angle:0,addEventListener:on}};
 global.orientation=0;
 global.document={hidden:false,hasFocus:()=>focused,addEventListener:on};
 Object.defineProperty(global,'navigator',{configurable:true,value:platform==='android'?
  {userAgent:'Mozilla/5.0 (Linux; Android 14; Tablet) Chrome/130.0',platform:'Linux armv8l',maxTouchPoints:5}:
  platform==='ipad'?{userAgent:'Mozilla/5.0 (Macintosh) Version/17.0 Safari/605.1.15',platform:'MacIntel',maxTouchPoints:5}:
  {userAgent:'Mozilla/5.0 (X11; Linux x86_64)',platform:'Linux x86_64',maxTouchPoints:0}});
 global.fetch=(url,opts)=>{rows.push(JSON.parse(opts.body).logs[0]);return Promise.resolve({ok:true,json:async()=>({violations:[]})});};
 const a=Object.assign({submitted:false,_antiCheatArmed:true,_isFocusTab:true,
 antiCheat:{enabled:true,fullscreen_required:true}, _sawFullscreen:true,
 _fsWasAbsent:false,_fsArmedAt:0,_fsGraceMs:4000,fullscreenBlocked:false,
 _hiddenAbsenceCharged:false,rotationGraceMs:1500,_rotationGraceUntil:0,
 _rotationForgiven:false,_rotationTimer:null,_fsLossTimer:null,
 _inFullscreen:true,violationCount:0,questions:[{}],currentQ:0,
 _fsElement(){return this._inFullscreen?{}:null;},_fsSupported(){return true;},
 requestExamFullscreen(){return Promise.resolve(false);},closePageZoom(){},
 _csrfToken(){return 'csrf';},_ev(kind){events.push(kind);},
 handleViolation(kind,trigger){charges.push({kind,trigger});},
 startAwayGrace(kind,trigger){charges.push({kind,trigger});},
 endAway(){},claimExamFocus(){}},M);
 a.watchOrientation();
 eval('(function(){'+setup+'})').call(a);
 return a;
}
const emit=(name)=>{for(const fn of handlers[name]||[])fn({type:name});};
const rotate=()=>{screen.orientation.angle=90;global.orientation=90;emit('orientationchange');};
const result=a=>({charges:charges.slice(),rows:rows.map(r=>r.violation_type),blocked:a.fullscreenBlocked});
const out={};
for(const platform of ['android','ipad']) {
 let a=fresh(platform);rotate();a._inFullscreen=false;a.checkFullscreen();tick(4000);
 out[platform+'RotationFirst']=result(a);
 a=fresh(platform);a._inFullscreen=false;a.checkFullscreen();tick(100);rotate();tick(4000);
 out[platform+'FullscreenFirst']=result(a);
}
let a=fresh();a._inFullscreen=false;a.checkFullscreen();tick(2000);out.androidExit=result(a);
a=fresh('desktop');a._inFullscreen=false;a.checkFullscreen();out.desktopExit=result(a);
a=fresh();a._inFullscreen=false;a.checkFullscreen();tick(100);a._inFullscreen=true;a.checkFullscreen();tick(2000);out.recovered=result(a);
a=fresh();a._inFullscreen=false;a.checkFullscreen();tick(100);mediaHandlers.forEach(fn=>fn());tick(4000);out.viewportOnly=result(a);
a=fresh();rotate();a._inFullscreen=false;a.checkFullscreen();document.hidden=true;emit('visibilitychange');tick(4000);out.hiddenDuringRotation=result(a);
a=fresh();focused=false;emit('blur');focused=true;tick(2000);out.focusRecoveredWithoutEvent=result(a);
a=fresh();focused=false;emit('blur');tick(2000);out.realBlur=result(a);
a=fresh();a._inFullscreen=false;a.checkFullscreen();document.hidden=true;tick(2000);out.hiddenPending=result(a);
a=fresh();rotate();a._inFullscreen=false;a.checkFullscreen();emit('change');mediaHandlers.forEach(fn=>fn({type:'change'}));tick(4000);out.duplicateSignals=result(a);
a=fresh();delete screen.orientation.angle;delete global.orientation;a.watchOrientation();a._inFullscreen=false;a.checkFullscreen();tick(100);emit('orientationchange');tick(4000);out.legacyRotation=result(a);
a=fresh();a._inFullscreen=false;a.checkFullscreen();a.submitted=true;tick(2000);out.submittedPending=result(a);
a=fresh();a._inFullscreen=false;a.checkFullscreen();a.antiCheat.enabled=false;tick(2000);out.disabledPending=result(a);
a=fresh();a._inFullscreen=false;a.checkFullscreen();a._isFocusTab=false;tick(2000);out.otherExamTab=result(a);
a=fresh();a._inFullscreen=false;a.checkFullscreen();tick(2000);a.checkFullscreen();rotate();tick(4000);out.rotationAfterRealExit=result(a);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def readings(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is required to execute the exam handlers")
    path = tmp_path_factory.mktemp("tablet-order") / "harness.cjs"
    path.write_text(HARNESS, encoding="utf-8")
    done = subprocess.run(
        [NODE, str(path), str(PAGE)], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("platform", ["android", "ipad"])
@pytest.mark.parametrize("order", ["RotationFirst", "FullscreenFirst"])
def test_rotation_blocks_without_penalty_in_either_event_order(
    readings, platform, order
):
    row = readings[platform + order]
    assert row == {"charges": [], "rows": ["orientation_shift"], "blocked": True}


def test_android_exit_without_rotation_is_still_charged_once(readings):
    row = readings["androidExit"]
    assert [c["kind"] for c in row["charges"]] == ["fullscreen_exit"]
    assert row["blocked"]


def test_desktop_exit_remains_immediate(readings):
    assert len(readings["desktopExit"]["charges"]) == 1


def test_restored_fullscreen_cancels_pending_tablet_charge(readings):
    assert readings["recovered"] == {"charges": [], "rows": [], "blocked": False}


def test_viewport_resize_alone_does_not_forgive_an_exit(readings):
    assert [c["kind"] for c in readings["viewportOnly"]["charges"]] == [
        "fullscreen_exit"
    ]


def test_rotation_does_not_hide_a_real_tab_switch(readings):
    assert [c["kind"] for c in readings["hiddenDuringRotation"]["charges"]] == [
        "tab_switch"
    ]


def test_fullscreen_timer_does_not_double_bill_a_hidden_page(readings):
    assert readings["hiddenPending"]["charges"] == []


def test_focus_recovered_without_focus_event_does_not_start_penalty(readings):
    assert readings["focusRecoveredWithoutEvent"]["charges"] == []


def test_sustained_focus_loss_is_still_detected(readings):
    assert [c["kind"] for c in readings["realBlur"]["charges"]] == ["focus_lost"]


def test_duplicate_orientation_signals_file_one_row(readings):
    assert readings["duplicateSignals"] == {
        "charges": [],
        "rows": ["orientation_shift"],
        "blocked": True,
    }


def test_native_orientation_fallback_without_angle_api(readings):
    assert readings["legacyRotation"] == {
        "charges": [],
        "rows": ["orientation_shift"],
        "blocked": True,
    }


@pytest.mark.parametrize("key", ["submittedPending", "disabledPending", "otherExamTab"])
def test_pending_timer_respects_exam_and_tab_state(readings, key):
    assert readings[key]["charges"] == []


def test_rotation_cannot_refund_or_double_charge_an_existing_exit(readings):
    assert [c["kind"] for c in readings["rotationAfterRealExit"]["charges"]] == [
        "fullscreen_exit"
    ]
