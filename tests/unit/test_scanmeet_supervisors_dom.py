"""Drive admin actions with real DOM events and server-issued versions."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_assignment_and_handover_ui_use_scoped_versions(tmp_path):
    script = tmp_path / "supervisors.cjs"
    script.write_text(r'''
const fs = require('node:fs'), path = require('node:path');
const root = process.argv[2];
const {JSDOM} = require(path.join(root, 'node_modules/jsdom'));
const dom = new JSDOM(`<section id="supervisor-management" data-exam-id="exam" data-csrf="csrf">
<p id="supervisor-status"></p><form id="supervisor-form"><select id="supervisor-target"></select>
<input id="supervisor-until" type="datetime-local" required><input id="supervisor-reason" required>
<button id="supervisor-grant" type="submit"></button><button id="supervisor-revoke" type="button"></button>
<button id="supervisor-transfer" type="button"></button></form><input id="supervisor-link"></section>`,
{url:'https://staging.invalid/live/exam/exam',runScripts:'outside-only'});
const w=dom.window, $=id=>w.document.getElementById(id), posts=[];
const data={owner_id:'owner',candidates:[{id:'owner',full_name:'Owner',role:'guru'},
{id:'deputy',full_name:'Deputy',role:'guru'}], assignments:[],
session:{id:'session',created_by:'owner',media_version:3,transfer_state:'ready'}};
w.fetch=async(url,opts)=>{
 if(opts.method==='POST'){
  const body=JSON.parse(opts.body);posts.push({url,body,headers:opts.headers,credentials:opts.credentials});
  if(url.endsWith('/supervisors'))data.assignments=[{supervisor_id:'deputy',version:1,revoked_at:null,valid_until:new Date(Date.now()+86400000).toISOString()}];
 }
 return {ok:true,json:async()=>data};
};
const drain=async()=>{for(let i=0;i<30;i++)await Promise.resolve();await new Promise(r=>setImmediate(r))};
(async()=>{
 w.eval(fs.readFileSync(path.join(root,'app/static/js/scanmeet-supervisors.js'),'utf8'));await drain();
 const ownerDisabled=$('supervisor-grant').disabled;
 $('supervisor-target').value='deputy';$('supervisor-target').dispatchEvent(new w.Event('change'));
 $('supervisor-reason').value='Covering absent teacher';
 $('supervisor-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await drain();
 $('supervisor-transfer').click();await drain();
 console.log(JSON.stringify({ownerDisabled,posts,link:$('supervisor-link').value,status:$('supervisor-status').textContent}));
 dom.window.close();
})().catch(e=>{console.error(e);process.exit(1)});
''')
    result = subprocess.run(["node", str(script), str(ROOT)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["ownerDisabled"]
    grant, transfer = data["posts"]
    assert grant["body"]["version"] == 0 and grant["body"]["revoke"] is False
    assert transfer["body"]["session_id"] == "session" and transfer["body"]["version"] == 3
    for post in data["posts"]:
        assert post["body"]["target_id"] == "deputy"
        assert "actor_id" not in post["body"] and "school_id" not in post["body"]
        assert post["headers"]["X-CSRFToken"] == "csrf" and post["credentials"] == "same-origin"
    assert data["link"] == "https://staging.invalid/live/exam/exam"
    assert data["status"]
