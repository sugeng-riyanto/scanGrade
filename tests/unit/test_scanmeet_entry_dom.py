"""Actual editor controls: scoped CSRF toggle, no media, and safe copy fallback."""
import json
from pathlib import Path
import shutil
import subprocess
import pytest
ROOT=Path(__file__).resolve().parents[2]
HARNESS=r'''
const fs=require('node:fs'),path=require('node:path'),root=process.argv[2];
const {JSDOM}=require(path.join(root,'node_modules/jsdom'));
const script=fs.readFileSync(path.join(root,'app/static/js/scanmeet-exam-entry.js'),'utf8');
async function go(mode){
 const dom=new JSDOM(`<section data-scanmeet-entry data-exam-id="exam-1" data-available="${mode==='off'?'0':'1'}" data-csrf="csrf">
 <p data-entry-status></p>${mode==='off'?'':'<button data-entry-toggle disabled>Enable</button>'}
 <input data-entry-link readonly value="https://scanmeet.test/student/exams/exam-1"><button data-entry-copy>Copy</button></section>`,{url:'https://scanmeet.test',runScripts:'outside-only'});
 const w=dom.window,calls=[];let enabled=false;
 w.setInterval=()=>1;w.sgT=(id,en)=>en;w.confirm=()=>mode!=='cancel';
 w.fetch=async(url,opts)=>{calls.push({url,method:opts.method,headers:opts.headers,body:opts.body});
  if(opts.method==='POST')enabled=JSON.parse(opts.body).enabled;
  return{ok:mode!=='denied',status:403,json:async()=>mode==='denied'?{error:'Access denied <script>bad</script>'}:{enabled,session:null}}};
 w.eval(script);
 const drain=async()=>{for(let i=0;i<20;i++)await Promise.resolve()};await drain();
 const b=w.document.querySelector('[data-entry-toggle]');
 if(b&&!b.disabled){b.click();b.click();await drain();if(mode==='cancel'){b.click();await drain()}}
 w.document.querySelector('[data-entry-copy]').click();await drain();
 const out={calls,enabled,selected:w.document.querySelector('input').selectionEnd===w.document.querySelector('input').value.length,
  status:w.document.querySelector('[data-entry-status]').textContent,disabled:b?.disabled,
  scripts:w.document.querySelectorAll('script').length};dom.window.close();return out;
}
(async()=>{const out={};for(const mode of ['on','off','denied','cancel'])out[mode]=await go(mode);console.log(JSON.stringify(out))})();
'''
@pytest.fixture(scope='module')
def data(tmp_path_factory):
    node=shutil.which('node')
    if not node:pytest.skip('node required for ScanMeet DOM')
    script=tmp_path_factory.mktemp('scanmeet-entry')/'entry.cjs';script.write_text(HARNESS)
    result=subprocess.run([node,str(script),str(ROOT)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)

def test_only_one_toggle_write_with_csrf_despite_double_click(data):
    writes=[c for c in data['on']['calls'] if c['method']=='POST']
    assert len(writes)==1
    assert writes[0]['url']=='/api/live/exams/exam-1/enabled'
    assert writes[0]['headers']['X-CSRF-Token']=='csrf'
    assert json.loads(writes[0]['body'])=={'enabled':True}

def test_global_off_editor_does_not_call_media_or_api(data):
    assert data['off']['calls']==[] and data['off']['selected']

def test_denial_keeps_toggle_disabled_and_does_not_inject_error_markup(data):
    assert data['denied']['disabled'] and data['denied']['scripts']==0
    assert all(c['method']=='GET' for c in data['denied']['calls'])

def test_cancelled_off_has_no_second_post(data):
    writes=[c for c in data['cancel']['calls'] if c['method']=='POST']
    assert len(writes)==1 and data['cancel']['enabled']
