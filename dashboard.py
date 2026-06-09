"""
Dashboard HTML for the Zero-Touch IAM split-screen UI.

Extracted from app.py to keep the serving entry point focused on routing.
Import DASHBOARD_HTML from here; do not modify app.py for UI-only changes.
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zero-Touch IAM</title>
<style>
:root{
  --acc-purple:#A100FF; --acc-deep:#460073; --acc-violet:#7B61FF;
  --bg:#F3F1F8; --card:#FFFFFF; --text:#1A1A2E; --muted:#6B6B82;
  --green:#1E8E3E; --red:#D93025;
}
*{box-sizing:border-box;}
body{margin:0;font-family:'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;}
header.appbar{background:linear-gradient(120deg,var(--acc-deep),var(--acc-purple) 55%,var(--acc-violet));color:#fff;padding:18px 28px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 2px 12px rgba(70,0,115,.25);}
header .brand{font-size:1.25rem;font-weight:700;letter-spacing:.3px;}
header .brand small{display:block;font-weight:400;font-size:.78rem;opacity:.85;margin-top:2px;}
header a{color:#fff;text-decoration:none;font-size:.85rem;border:1px solid rgba(255,255,255,.5);padding:7px 14px;border-radius:20px;}
header a:hover{background:rgba(255,255,255,.15);}
.split{flex:1;display:grid;grid-template-columns:38% 62%;gap:20px;padding:20px;min-height:0;}
.pane{background:var(--card);border-radius:16px;box-shadow:0 4px 24px rgba(70,0,115,.08);display:flex;flex-direction:column;min-height:0;overflow:hidden;}
.pane h2{margin:0;padding:18px 22px;font-size:1rem;border-bottom:1px solid #efe9f6;color:var(--acc-deep);}
.chat{flex:1;overflow-y:auto;padding:18px 22px;display:flex;flex-direction:column;gap:12px;}
.msg{max-width:85%;padding:11px 14px;border-radius:14px;font-size:.92rem;line-height:1.4;}
.msg.user{align-self:flex-end;background:var(--acc-purple);color:#fff;border-bottom-right-radius:4px;}
.msg.bot{align-self:flex-start;background:#F1ECFA;color:var(--text);border-bottom-left-radius:4px;}
.msg.thinking{animation:pulse 1.1s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:.4;}50%{opacity:.95;}}
.confirm{align-self:flex-start;max-width:92%;background:#fff;border:1.5px solid var(--acc-violet);border-radius:14px;padding:14px;font-size:.9rem;}
.confirm .row{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px dashed #eee;}
.confirm .row span{color:var(--muted);}
.confirm .actions{margin-top:12px;display:flex;gap:10px;}
button.cta{background:var(--acc-purple);color:#fff;border:none;border-radius:10px;padding:10px 16px;font-size:.9rem;font-weight:600;cursor:pointer;}
button.cta:hover{filter:brightness(.92);}
button.ghost{background:#fff;color:var(--acc-purple);border:1.5px solid var(--acc-purple);border-radius:10px;padding:10px 16px;font-weight:600;cursor:pointer;}
button.approve{background:var(--green);}
button.deny{background:var(--red);}
button.inject{background:#ffc24b;color:#3a2a00;}
.inputbar{display:flex;gap:10px;padding:16px 22px;border-top:1px solid #efe9f6;}
.inputbar input{flex:1;padding:12px 14px;border:1px solid #ddd;border-radius:10px;font-size:.92rem;}
.inputbar input:focus{outline:none;border-color:var(--acc-purple);}
.backup{padding:14px 22px;border-top:1px solid #efe9f6;display:none;gap:10px;}
.backup .label{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;width:100%;}
.flow{flex:1;position:relative;overflow:hidden;background:linear-gradient(155deg,#2b0a4e 0%,#5b1a8b 42%,#a100ff 100%);}
.edges{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:1;}
.edge{stroke:rgba(255,255,255,.32);stroke-width:2;fill:none;}
.edge.a2a{stroke:rgba(255,255,255,.5);stroke-width:2.5;stroke-dasharray:2 5;}
.edge.active{stroke:#ffffff;stroke-width:3;stroke-dasharray:6 7;animation:flow .6s linear infinite;filter:drop-shadow(0 0 3px rgba(255,255,255,.8));}
.edge.completed{stroke:#7ce6a0;stroke-width:2;stroke-dasharray:none;}
.edge.rejected{stroke:#ff9182;stroke-width:2;stroke-dasharray:none;}
@keyframes flow{to{stroke-dashoffset:-26;}}
.edge-label{fill:rgba(255,255,255,.85);font-size:9px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;}
.zone{position:absolute;border:1.5px dashed rgba(255,255,255,.4);border-radius:14px;z-index:0;}
.zone-label{position:absolute;top:-10px;left:14px;background:#fff;padding:1px 8px;font-size:.62rem;letter-spacing:.4px;color:var(--acc-deep);text-transform:uppercase;border-radius:6px;font-weight:700;}
.zoneA{left:2%;top:26%;width:68%;height:68%;}
.zoneB{left:74%;top:45%;width:23%;height:28%;background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.55);}
.node{position:absolute;transform:translate(-50%,-50%);width:118px;background:var(--card);border:1.5px solid #e3d9f2;border-radius:12px;padding:9px 8px;text-align:center;box-shadow:0 3px 14px rgba(0,0,0,.25);transition:border-color .2s,box-shadow .2s;z-index:2;}
.node .node-title{font-size:.82rem;font-weight:700;color:var(--text);}
.node .chip{display:inline-block;margin-top:5px;font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.4px;padding:2px 8px;border-radius:10px;background:#eee;color:#888;}
.node .node-result{font-size:.64rem;color:var(--muted);margin-top:5px;line-height:1.25;}
.node-tag{position:absolute;top:-8px;right:-6px;background:#ffc24b;color:#3a2a00;font-size:.52rem;font-weight:800;padding:1px 6px;border-radius:8px;letter-spacing:.3px;}
.node.feature{border-color:#ffc24b;box-shadow:0 0 0 2px rgba(255,194,75,.25),0 3px 14px rgba(0,0,0,.25);}
.node.hub{width:134px;border-color:var(--acc-violet);background:linear-gradient(135deg,#fff,#f6f0ff);}
.node.request{background:linear-gradient(135deg,#fff,#eef0ff);border-color:#b9c2ff;}
.node.running{border-color:var(--acc-purple);animation:nodepulse 1s ease-in-out infinite;}
.node.running .chip{background:#efe0ff;color:var(--acc-purple);}
.node.completed{border-color:var(--green);}
.node.completed .chip{background:#e6f4ea;color:var(--green);}
.node.rejected{border-color:var(--red);}
.node.rejected .chip{background:#fce8e6;color:var(--red);}
@keyframes nodepulse{0%,100%{box-shadow:0 0 0 2px rgba(255,255,255,.35),0 3px 14px rgba(0,0,0,.25);}50%{box-shadow:0 0 0 6px rgba(255,255,255,.5),0 3px 18px rgba(161,0,255,.5);}}
.node.flash{animation:greenflash 1.2s ease-out;}
@keyframes greenflash{0%{box-shadow:0 0 0 0 rgba(124,230,160,.9);}45%{box-shadow:0 0 0 18px rgba(124,230,160,0);}100%{box-shadow:0 0 0 0 rgba(124,230,160,0);}}
</style>
</head>
<body>
<header class="appbar">
  <div class="brand">Zero-Touch IAM <small>AI-assisted access provisioning</small></div>
</header>
<div class="split">
  <section class="pane">
    <h2>Request access</h2>
    <div class="chat" id="chat">
      <div class="msg bot">Hi. Tell me what access you need &mdash; for example, "I need to view storage objects on the ops dashboard project".</div>
    </div>
    <div class="backup" id="backup">
      <div class="label">Approver action (demo backup)</div>
      <button class="cta approve" onclick="demoApproval('APPROVED')">Approve</button>
      <button class="cta deny" onclick="demoApproval('DENIED')">Deny</button>
      <button class="cta inject" onclick="demoApproval('INJECT')">Send injection</button>
    </div>
    <div class="inputbar">
      <input id="msg" type="text" placeholder="Describe the access you need..." onkeydown="if(event.key==='Enter')send()">
      <button class="cta" onclick="send()">Send</button>
    </div>
  </section>
  <section class="pane">
    <h2>Live agent flow</h2>
    <div class="flow" id="flow">
      <svg class="edges" id="edges">
        <line id="edge-pubsub" class="edge" x1="35%" y1="11%" x2="35%" y2="40%"></line>
        <line id="edge-modelarmor" class="edge" x1="35%" y1="40%" x2="12%" y2="41%"></line>
        <line id="edge-memorybank" class="edge" x1="35%" y1="40%" x2="58%" y2="41%"></line>
        <line id="edge-lookup" class="edge" x1="35%" y1="40%" x2="10%" y2="84%"></line>
        <line id="edge-context" class="edge" x1="35%" y1="40%" x2="27%" y2="84%"></line>
        <line id="edge-nlu" class="edge" x1="35%" y1="40%" x2="44%" y2="84%"></line>
        <line id="edge-communication" class="edge" x1="35%" y1="40%" x2="61%" y2="84%"></line>
        <line id="edge-provisioning" class="edge a2a" x1="35%" y1="40%" x2="85%" y2="58%"></line>
        <text class="edge-label" x="36.5%" y="26%">Pub/Sub</text>
        <text class="edge-label" x="60%" y="48%">A2A</text>
      </svg>
      <div class="zone zoneA"><span class="zone-label">Orchestrator Service &middot; low privilege</span></div>
      <div class="zone zoneB"><span class="zone-label">Provisioner &middot; high privilege</span></div>
      <div class="node request" id="node-request" style="left:35%;top:11%"><div class="node-title">Conversation Agent</div><div class="chip">idle</div><div class="node-result"></div></div>
      <div class="node hub" id="node-orchestrator" style="left:35%;top:40%"><div class="node-title">Orchestrator</div><div class="chip">idle</div><div class="node-result"></div></div>
      <div class="node feature" id="node-modelarmor" style="left:12%;top:41%"><div class="node-tag">GEAP</div><div class="node-title">Model Armor</div><div class="chip">idle</div><div class="node-result"></div></div>
      <div class="node feature" id="node-memorybank" style="left:58%;top:41%"><div class="node-tag">GEAP</div><div class="node-title">Memory Bank</div><div class="chip">idle</div><div class="node-result"></div></div>
      <div class="node" id="node-lookup" style="left:10%;top:84%"><div class="node-title">Lookup</div><div class="chip">idle</div><div class="node-result"></div></div>
      <div class="node" id="node-context" style="left:27%;top:84%"><div class="node-title">Context</div><div class="chip">idle</div><div class="node-result"></div></div>
      <div class="node" id="node-nlu" style="left:44%;top:84%"><div class="node-title">NLU</div><div class="chip">idle</div><div class="node-result"></div></div>
      <div class="node" id="node-communication" style="left:61%;top:84%"><div class="node-title">Communication</div><div class="chip">idle</div><div class="node-result"></div></div>
      <div class="node" id="node-provisioner" style="left:85%;top:58%"><div class="node-title">Provisioning</div><div class="chip">idle</div><div class="node-result"></div></div>
    </div>
  </section>
</div>
<script>
let lastMatch=null,currentSession=null,streamStarted=false,convo=[];
const chat=document.getElementById('chat');
function addMsg(text,cls){const d=document.createElement('div');d.className='msg '+cls;d.textContent=text;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d;}
async function send(){
  const inp=document.getElementById('msg');const text=inp.value.trim();if(!text)return;
  inp.value='';addMsg(text,'user');
  if(!es){resetGraph();}
  const thinking=addMsg('Thinking...','bot');thinking.classList.add('thinking');
  const reqNode=document.getElementById('node-request');
  if(reqNode){reqNode.classList.remove('completed','rejected');reqNode.classList.add('running');setChip(reqNode,'running');setResult(reqNode,'Interpreting request');}
  try{
    const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,history:convo})});
    const data=await r.json();
    convo.push(text);
    thinking.remove();
    addMsg(data.message||'...','bot');
    if(data.matched){
      lastMatch=data;showConfirm(data);
      if(reqNode){setResult(reqNode,'Awaiting your confirmation');}
    }else if(data.blocked){
      if(reqNode){reqNode.classList.remove('running');reqNode.classList.add('rejected');setChip(reqNode,'rejected');setResult(reqNode,'Blocked by Model Armor');}
      const ma=document.getElementById('node-modelarmor');if(ma){ma.classList.remove('running','completed');ma.classList.add('rejected');setChip(ma,'rejected');setResult(ma,'Prompt injection / policy violation blocked');}
      const me=document.getElementById('edge-modelarmor');if(me){me.classList.remove('active','completed');me.classList.add('rejected');}
    }else if(reqNode){
      reqNode.classList.remove('running');setChip(reqNode,'idle');setResult(reqNode,'Needs more detail');
    }
  }catch(e){
    thinking.remove();addMsg('Error contacting parser: '+e,'bot');
    if(reqNode){reqNode.classList.remove('running');setChip(reqNode,'idle');setResult(reqNode,'Parse error');}
  }
}
function showConfirm(d){
  const c=document.createElement('div');c.className='confirm';
  const rows=[['Requester',d.user_id],['Role',d.requested_role],['Project',d.project_scope],['Timezone',d.user_timezone]];
  rows.forEach(function(kv){const r=document.createElement('div');r.className='row';const a=document.createElement('span');a.textContent=kv[0];const b=document.createElement('b');b.textContent=kv[1];r.appendChild(a);r.appendChild(b);c.appendChild(r);});
  const act=document.createElement('div');act.className='actions';
  const sub=document.createElement('button');sub.className='cta';sub.textContent='Submit request';sub.onclick=function(){submitRequest(c);};
  const no=document.createElement('button');no.className='ghost';no.textContent='Not quite';no.onclick=function(){c.remove();};
  act.appendChild(sub);act.appendChild(no);c.appendChild(act);
  chat.appendChild(c);chat.scrollTop=chat.scrollHeight;
}
async function submitRequest(card){
  if(!lastMatch)return;
  card.querySelector('.actions').textContent='Submitting...';
  try{
    const r=await fetch('/submit_request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(lastMatch)});
    const data=await r.json();
    if(data.session_id){currentSession=data.session_id;convo=[];addMsg('Request queued. Session '+data.session_id.slice(-6)+'. Watch the agent flow on the right.','bot');startStream(data.session_id);}
    else addMsg('Submit failed: '+(data.error||'unknown'),'bot');
  }catch(e){addMsg('Submit error: '+e,'bot');}
}
async function demoApproval(decision){
  if(!currentSession){addMsg('No active session to approve yet.','bot');return;}
  try{
    await fetch('/demo_approval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:currentSession,decision:decision})});
    addMsg('Approver decision sent: '+decision,'bot');
    document.getElementById('backup').style.display='none';
  }catch(e){addMsg('Approval error: '+e,'bot');}
}
const NODE_MAP={
  orchestrator:{node:'node-orchestrator'},
  lookup:{node:'node-lookup',edge:'edge-lookup'},
  context:{node:'node-context',edge:'edge-context'},
  nlu:{node:'node-nlu',edge:'edge-nlu'},
  communication:{node:'node-communication',edge:'edge-communication'},
  provisioning:{node:'node-provisioner',edge:'edge-provisioning'},
  modelarmor:{node:'node-modelarmor',edge:'edge-modelarmor'},
  memorybank:{node:'node-memorybank',edge:'edge-memorybank'}
};
function keyOf(name){return (name||'').toLowerCase().replace(/[^a-z]/g,'');}
function setChip(nodeEl,text){const c=nodeEl.querySelector('.chip');if(c)c.textContent=text;}
function setResult(nodeEl,text){const r=nodeEl.querySelector('.node-result');if(r)r.textContent=text||'';}
function resetGraph(){
  document.querySelectorAll('.node').forEach(function(n){n.classList.remove('running','completed','rejected','flash');setChip(n,'idle');setResult(n,'');});
  document.querySelectorAll('.edge').forEach(function(e){e.classList.remove('active','completed','rejected');});
}
function applyEvent(ev){
  const map=NODE_MAP[keyOf(ev.node)];if(!map)return;
  const nodeEl=document.getElementById(map.node);if(!nodeEl)return;
  const edgeEl=map.edge?document.getElementById(map.edge):null;
  if(ev.status==='running'){
    nodeEl.classList.remove('completed','rejected');nodeEl.classList.add('running');setChip(nodeEl,'running');
    if(edgeEl){edgeEl.classList.remove('completed','rejected');edgeEl.classList.add('active');}
  }else if(ev.status==='completed'){
    nodeEl.classList.remove('running','rejected');nodeEl.classList.add('completed');setChip(nodeEl,'done');
    if(edgeEl){edgeEl.classList.remove('active','rejected');edgeEl.classList.add('completed');}
    if(map.node==='node-provisioner'){nodeEl.classList.remove('flash');void nodeEl.offsetWidth;nodeEl.classList.add('flash');}
  }else if(ev.status==='rejected'){
    nodeEl.classList.remove('running','completed');nodeEl.classList.add('rejected');setChip(nodeEl,'rejected');
    if(edgeEl){edgeEl.classList.remove('active','completed');edgeEl.classList.add('rejected');}
  }
  if(ev.result)setResult(nodeEl,ev.result);
}
let es=null;
function startStream(session){
  resetGraph();
  if(es){es.close();}
  streamStarted=false;
  document.getElementById('backup').style.display='none';
  const req=document.getElementById('node-request');req.classList.add('running');setChip(req,'running');setResult(req,'Publishing to Pub/Sub');
  const pe=document.getElementById('edge-pubsub');if(pe)pe.classList.add('active');
  es=new EventSource('/events/'+encodeURIComponent(session));
  es.onmessage=function(e){
    let data;try{data=JSON.parse(e.data);}catch(_){return;}
    if(!streamStarted){
      streamStarted=true;
      const r=document.getElementById('node-request');r.classList.remove('running');r.classList.add('completed');setChip(r,'done');setResult(r,'Request published');
      const p=document.getElementById('edge-pubsub');if(p){p.classList.remove('active');p.classList.add('completed');}
    }
    if(data.result&&data.result.indexOf('Awaiting approver decision')>=0){document.getElementById('backup').style.display='flex';}
    if(data.node==='__chat__'){addMsg(data.result,'bot');return;}
    if(data.done){es.close();es=null;const h=document.getElementById('node-orchestrator');h.classList.remove('running');setChip(h,'done');return;}
    applyEvent(data);
  };
  es.onerror=function(){/* EventSource auto-retries transient drops */};
}
</script>
</body>
</html>
"""
