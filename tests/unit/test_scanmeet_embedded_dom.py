"""Drive media lifecycle in a DOM; SFU/device acceptance remains a separate gate."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r'''
const fs=require('node:fs');
const path=require('node:path');
const root=process.argv[2];
const {JSDOM}=require(path.join(root,'node_modules/jsdom'));
const source=fs.readFileSync(path.join(root,'app/static/js/scan-live-exam.js'),'utf8');
async function scenario(mode){
 const dom=new JSDOM(`<section id="scan-live" hidden data-exam-id="exam-1" data-role="student" data-csrf="csrf" data-embedded="1" data-sdk="/static/vendor/sdk.js">
 <button id="live-join" hidden>Join</button><button id="live-hand" disabled>Hand</button><button id="live-mic" hidden>Mic</button>
 <p id="live-status"></p><p id="live-warning" hidden></p><p id="live-switch-off" hidden></p><p id="live-raise-status"></p>
 <p id="live-session-time" hidden></p><div id="live-grid"></div></section>`,{url:'https://scanmeet.test/student/exams/exam-1',runScripts:'outside-only'});
 const w=dom.window, timers=[], calls=[], events={};
 let enabled=mode!=='off', active=true, camera=0, mic=0, stopped=0, disconnected=0;
 let connectingResolve, cameraResolve, tokenResolve, sdkResolve, micResolve;
 let approved=mode==='mic-race'||mode==='mic-revoked', micPublication=null, micStops=0;
 const raceCamera=mode==='camera-race'||mode==='handover-camera', raceConnect=mode==='connect-race'||mode==='handover-connect';
 let denied=false, mediaVersion=1, handoverPending=false;
 const publication={isMuted:false,track:{kind:'video',stop(){stopped++},attach(){return w.document.createElement('video')}}};
 class Room{
  constructor(){this.localParticipant={
   setTrackSubscriptionPermissions(){calls.push('privacy')},
   async setCameraEnabled(value){if(value){camera++;calls.push('camera');if(raceCamera)await new Promise(r=>cameraResolve=r)}else{stopped++}},
   async setMicrophoneEnabled(value){
    if(value){mic++;if(mode==='mic-race')await new Promise(r=>micResolve=r);
     micPublication={isMuted:false,track:{stop(){micStops++}}};this.trackPublications.set('mic',micPublication);
    }else if(micPublication){micPublication.isMuted=true;micPublication.track.stop()}
   },
   async publishTrack(){calls.push('publish')},
   getTrackPublication(source){return source==='camera'?publication:micPublication},
   trackPublications:new Map([['cam',publication]])};this.remoteParticipants=new Map();}
  on(event,fn){events[event]=fn;return this}
  async connect(){if(raceConnect)await new Promise(r=>connectingResolve=r)}
  async disconnect(){disconnected++;publication.track.stop();if(events.disconnected)events.disconnected();}
 }
 const sdk={Room,Track:{Source:{Camera:'camera'}},async createLocalVideoTrack(){camera++;calls.push('camera');if(raceCamera)await new Promise(r=>cameraResolve=r);return publication.track},RoomEvent:{TrackSubscribed:'sub',TrackUnsubscribed:'unsub',ParticipantDisconnected:'left',Disconnected:'disconnected'}};
 w.LivekitClient=sdk;
 if(mode==='sdk-race'||mode==='sdk-reject'){
  w.LivekitClient=undefined;
  if(mode==='sdk-reject')w.document.getElementById('scan-live').dataset.sdk='https://outside.invalid/sdk.js';
  w.document.head.append=script=>{w.document.head.appendChild(script);sdkResolve=()=>{w.LivekitClient=sdk;script.onload()}};
 }
 w.sgT=(id,en)=>en;
 w.setInterval=(fn,ms)=>{timers.push({fn,ms});return timers.length};w.clearInterval=()=>{};
 w.fetch=async(url,opts)=>{
  calls.push({url,method:opts?.method||'GET'});
  if(mode==='initial-error')throw Error('synthetic network error');
  if(url.endsWith('/token')){const issuedVersion=mediaVersion;if(mode==='token-race')await new Promise(r=>tokenResolve=r);return{ok:!denied,status:denied?403:200,json:async()=>({supervisor_identity:'p_teacher',url:'wss://synthetic.invalid',token:'synthetic',media_version:issuedVersion})}};
  if(url.includes('/hand-raises'))return {ok:true,status:200,json:async()=>({request:approved?{status:'approved'}:null})};
  return {ok:!denied,status:denied?403:200,json:async()=>({enabled,role:'student',handover_pending:handoverPending,request:approved?{status:'approved'}:null,session:active?{id:'session-1',media_version:mediaVersion}:null})};
 };
 const drain=async()=>{for(let i=0;i<24;i++)await Promise.resolve();await new Promise(r=>setImmediate(r))};
 w.eval(source);await drain();
 const before={camera,mic,joinHidden:w.document.getElementById('live-join').hidden};
 if(mode!=='initial'){
  w.document.getElementById('live-join').click();await drain();
  if(mode==='off-live'||raceCamera||raceConnect||mode==='token-race'||mode==='sdk-race'){
   if(mode.startsWith('handover-')){mediaVersion=2;handoverPending=true}else enabled=false;
   for(const t of timers.filter(t=>t.ms>=4000&&t.ms<=20000))t.fn();
   await drain();
   if(cameraResolve)cameraResolve();if(connectingResolve)connectingResolve();if(tokenResolve)tokenResolve();if(sdkResolve)sdkResolve();await drain();
  }
  if(mode==='handover-active'){
   mediaVersion=2;handoverPending=true;for(const t of timers.filter(t=>t.ms>=4000&&t.ms<=20000))t.fn();await drain();
   handoverPending=false;for(const t of timers.filter(t=>t.ms>=4000&&t.ms<=20000))t.fn();await drain();
  }
  if(mode==='ended'){
   active=false;for(const t of timers.filter(t=>t.ms>=4000&&t.ms<=20000))t.fn();await drain();
  }
  if(mode==='mic-race'||mode==='mic-revoked'){
   for(const t of timers.filter(t=>t.ms>=4000&&t.ms<=20000))t.fn();await drain();
   w.document.getElementById('live-mic').click();await drain();
   if(mode==='mic-race')enabled=false;else approved=false;
   for(const t of timers.filter(t=>t.ms>=4000&&t.ms<=20000))t.fn();await drain();
   if(micResolve)micResolve();await drain();
  }
  if(mode==='denied'){
   denied=true;for(const t of timers.filter(t=>t.ms>=4000&&t.ms<=20000))t.fn();await drain();
  }
 }
 const out={before,camera,mic,micStops,stopped,disconnected,calls,panelHidden:w.document.getElementById('scan-live').hidden,micLabel:w.document.getElementById('live-mic').textContent,handDisabled:w.document.getElementById('live-hand').disabled,
   status:w.document.getElementById('live-status').textContent,joinHidden:w.document.getElementById('live-join').hidden};
 dom.window.close();return out;
}
(async()=>{const result={};for(const mode of ['initial','join','off','off-live','camera-race','connect-race','ended','denied','token-race','sdk-race','sdk-reject','mic-race','mic-revoked','initial-error','handover-camera','handover-connect','handover-active'])result[mode]=await scenario(mode);console.log(JSON.stringify(result))})();
'''

@pytest.fixture(scope='module')
def readings(tmp_path_factory):
    node=shutil.which('node')
    if not node:
        pytest.skip('node is needed for ScanMeet DOM tests')
    script=tmp_path_factory.mktemp('scanmeet-embedded')/'lifecycle.cjs'
    script.write_text(HARNESS)
    result=subprocess.run([node,str(script),str(ROOT)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)

def test_embedded_camera_requires_explicit_join(readings):
    r=readings['initial']
    assert r['camera']==0 and r['mic']==0
    assert not r['before']['joinHidden']

def test_join_publishes_camera_after_privacy_and_never_mic(readings):
    r=readings['join']
    assert r['before']['camera']==0 and r['camera']==1 and r['mic']==0
    assert r['calls'].index('privacy') < r['calls'].index('camera')
    assert not r['handDisabled']

def test_off_ignores_join_even_when_clicked_by_script(readings):
    r=readings['off']
    assert r['camera']==0 and r['mic']==0 and r['handDisabled']
    assert not any(isinstance(c,dict) and c['url'].endswith('/token') for c in r['calls'])

@pytest.mark.parametrize('mode',['off-live','ended','denied'])
def test_revoked_or_ended_room_stops_local_capture(readings,mode):
    r=readings[mode]
    assert r['disconnected']>=1 and r['stopped']>=1
    assert r['handDisabled'] and r['mic']==0

@pytest.mark.parametrize('mode',['camera-race','connect-race'])
def test_off_during_pending_permission_or_connect_does_not_restore_live(readings,mode):
    r=readings[mode]
    assert r['disconnected']>=1 and r['stopped']>=1
    assert r['handDisabled'] and r['joinHidden'] and r['mic']==0


@pytest.mark.parametrize('mode',['token-race','sdk-race'])
def test_late_sdk_or_token_does_not_start_camera_after_off(readings,mode):
    r=readings[mode]
    assert r['camera']==0 and r['mic']==0 and r['handDisabled']
    assert r['joinHidden']

def test_cross_origin_sdk_is_refused_without_camera_or_token(readings):
    r=readings['sdk-reject']
    assert r['camera']==0 and r['mic']==0
    assert not any(isinstance(c,dict) and c['url'].endswith('/token') for c in r['calls'])


def test_microphone_permission_resolving_after_off_is_released(readings):
    r=readings['mic-race']
    assert r['mic']==1 and r['micStops']>=1 and r['disconnected']>=1 and r['handDisabled']

def test_revoked_speaking_approval_stops_microphone_without_ending_camera(readings):
    r=readings['mic-revoked']
    assert r['mic']==1 and r['micStops']>=1 and r['disconnected']==0
    assert r['micLabel']=='Enable microphone'


def test_pupil_poll_does_not_fetch_a_second_copy_of_exam_and_session(readings):
    calls=readings['mic-revoked']['calls']
    redundant=[c for c in calls if isinstance(c,dict) and c['method']=='GET' and '/hand-raises' in c['url']]
    assert redundant==[], 'pupil polling must combine room state and own microphone approval'


def test_initial_api_failure_is_visible_without_requesting_media(readings):
    r=readings['initial-error']
    assert not r['panelHidden'] and r['status']
    assert r['camera']==0 and r['mic']==0 and r['handDisabled']


@pytest.mark.parametrize('mode', ['handover-camera', 'handover-connect'])
def test_retired_generation_does_not_publish_after_pending_permission(readings, mode):
    r = readings[mode]
    assert 'publish' not in r['calls']
    assert r['disconnected'] >= 1 and r['stopped'] >= 1
    assert r['mic'] == 0


def test_handover_rejoins_new_generation_without_opening_microphone(readings):
    r = readings['handover-active']
    assert r['camera'] == 2 and r['calls'].count('publish') == 2
    assert r['disconnected'] >= 1 and r['mic'] == 0
