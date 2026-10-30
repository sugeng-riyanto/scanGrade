"""Execute management JavaScript in an offline DOM; no browser or network."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""
const fs=require('fs');
const {JSDOM}=require(process.argv[2]+'/node_modules/jsdom');
const script=fs.readFileSync(process.argv[2]+'/app/static/js/scanmeet-management.js','utf8');
async function scenario(mode) {
 const dom=new JSDOM(`<select id="scanmeet-plan"><option value="trial">Trial</option><option value="kelas">Class</option></select>
 <div data-package-code="trial"></div><div data-package-code="kelas" hidden></div>
 <button data-open-dialog="lookup">Open</button><dialog id="lookup"><button data-close-dialog>Close</button></dialog>
 <form action="/save" data-scanmeet-form ${mode==='cancel'?'data-confirm="revoke"':''}>
 <input name="_csrf_token" value="csrf-test"><input name="npsn" value="69893227">
 <button type="submit">Save</button><p data-form-result role="status"></p></form>`,
 {runScripts:'outside-only',url:'https://scangrade.test'});
 const w=dom.window, calls=[],timers=[];
 w.sgT=(id,en)=>id;w.confirm=()=>false;
 w.setTimeout=(fn,ms)=>{timers.push(ms);return timers.length;};w.clearTimeout=()=>{};
 const dialog=w.document.getElementById('lookup');
 dialog.showModal=()=>dialog.setAttribute('open','');dialog.close=()=>dialog.removeAttribute('open');
 w.fetch=async(url,options)=>{calls.push({url,headers:options.headers,body:String(options.body)});
   if(mode==='network')throw Error('offline');
   return {ok:mode!=='error',json:async()=>{if(mode==='html')throw Error('not json');
     return mode==='error'?{error:'Invalid <script>payload</script>'}:{ok:true,message:'Saved'};}};
 };
 w.eval(script);
 const form=w.document.querySelector('form');
 form.dispatchEvent(new w.Event('submit',{cancelable:true}));
 form.dispatchEvent(new w.Event('submit',{cancelable:true}));
 for(let i=0;i<12;i++)await Promise.resolve();
 const select=w.document.getElementById('scanmeet-plan');select.value='kelas';select.dispatchEvent(new w.Event('change'));
 w.document.querySelector('[data-open-dialog]').click();const opened=dialog.hasAttribute('open');
 w.document.querySelector('[data-close-dialog]').click();
 const result={calls,text:form.querySelector('[data-form-result]').textContent,
 disabled:form.querySelector('button').disabled,busy:form.getAttribute('aria-busy'),
 alert:form.querySelector('[data-form-result]').getAttribute('role'),
 scripts:form.querySelectorAll('script').length,reload:timers.includes(1000),
 hidden:[...w.document.querySelectorAll('[data-package-code]')].map(n=>n.hidden),
 opened,closed:!dialog.hasAttribute('open')};
 dom.window.close();return result;
}
(async()=>{const out={};for(const mode of ['success','error','html','network','cancel'])out[mode]=await scenario(mode);console.log(JSON.stringify(out));})();
"""


@pytest.fixture(scope="module")
def dom_results(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for management DOM checks")
    script = tmp_path_factory.mktemp("scanmeet-management") / "check.cjs"
    script.write_text(HARNESS)
    done = subprocess.run([node, str(script), str(ROOT)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_double_submit_sends_one_csrf_protected_request(dom_results):
    row = dom_results["success"]
    assert len(row["calls"]) == 1
    assert row["calls"][0]["headers"]["X-CSRF-Token"] == "csrf-test"
    assert "npsn=69893227" in row["calls"][0]["body"]
    assert row["disabled"] and row["reload"] and row["text"] == "Saved"


@pytest.mark.parametrize("mode", ["error", "html", "network"])
def test_failures_stay_visible_without_reloading_or_inserting_html(dom_results, mode):
    row = dom_results[mode]
    assert row["text"] and row["alert"] == "alert"
    assert not row["reload"] and not row["disabled"] and not row["busy"]
    assert row["scripts"] == 0


def test_cancelled_revocation_performs_no_write(dom_results):
    assert dom_results["cancel"]["calls"] == []


def test_package_preview_and_dialog_work_with_actual_handlers(dom_results):
    row = dom_results["success"]
    assert row["hidden"] == [True, False]
    assert row["opened"] and row["closed"]
