import http from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { readFile, writeFile, mkdir, rename } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { Spectrum, attachment } from 'spectrum-ts';
import { imessage } from 'spectrum-ts/providers/imessage';
import {resolveSpace,safeDiagnostic} from './routing.mjs';

const token = process.env.PHOTON_BRIDGE_TOKEN || '';
const recipient = process.env.PHOTON_TEST_RECIPIENT || '';
const backend = process.env.PHOTON_BACKEND_URL || 'http://127.0.0.1:8000';
const projectId = process.env.PHOTON_PROJECT_ID;
const projectSecret = process.env.PHOTON_PROJECT_SECRET;
const storeDir = fileURLToPath(new URL('../.local/photon/', import.meta.url));
const storePath = storeDir + 'bridge.json';
let saved = { sends: {}, conversations: {} };
try { saved = JSON.parse(await readFile(storePath, 'utf8')); } catch {}
saved.routes ||= {};
const inboundSpaces=new Map();
const persist = async () => { await mkdir(storeDir, {recursive:true}); await writeFile(storePath+'.tmp', JSON.stringify(saved)); await rename(storePath+'.tmp',storePath); };
const canonical = value => value.startsWith('+') ? value.replace(/\D/g,'') : value.toLowerCase();
const authorized = value => { const a=Buffer.from(value||''),b=Buffer.from('Bearer '+token); return !!token && a.length===b.length && timingSafeEqual(a,b); };
let app, connection = {connected:false,status:'needs_setup',message:'Set PHOTON_PROJECT_ID, PHOTON_PROJECT_SECRET, PHOTON_TEST_RECIPIENT and PHOTON_BRIDGE_TOKEN.'};
let sends = Promise.resolve();
let delivery={phase:'idle',error_code:null};
function safeCode(error){if(String(error?.message||'').includes('Target not allowed for this project')) return 'TARGET_NOT_ALLOWED';const value=error?.code ?? error?.status ?? error?.name ?? 'UNKNOWN';return /^[A-Za-z0-9_-]{1,40}$/.test(String(value)) ? String(value) : 'UNKNOWN';}
const phase=value=>{delivery={phase:value,error_code:null};console.log('Photon delivery phase: '+value);};

async function send(data) {
  if (!connection.connected || !app) throw new Error('Photon SDK is not connected');
  if (canonical(data.recipient || '') !== canonical(recipient)) throw new Error('Recipient is not approved');
  if (!/^[a-zA-Z0-9:_-]{1,150}$/.test(data.idempotency_key||'') || typeof data.text !== 'string' || data.text.length>6000) throw new Error('Invalid message');
  const prior = saved.sends[data.idempotency_key] || {image:false,text:false};
  if (prior.text && (!data.image || prior.image)) return {accepted:true,duplicate:true};
  const im = imessage(app);
  phase('resolve_conversation');
  const space = await resolveSpace(im,recipient,inboundSpaces,saved.routes,canonical);
  saved.conversations[space.id] = {session_id:data.session_id, question_index:data.question_index, recipient};
  await persist();
  if(!prior.text){
    phase('send_text');
    await space.send(data.text);
    prior.text=true;
    saved.sends[data.idempotency_key]=prior;
    await persist();
  }
  if (data.image && !prior.image) {
    phase('send_image');
    const bytes=Buffer.from(data.image.base64||'', 'base64');
    if(bytes.length>2_000_000 || !['image/png','image/jpeg'].includes(data.image.mimeType)) throw new Error('Invalid thumbnail');
    await space.send(attachment(bytes, {name:data.image.name || 'sign.jpg', mimeType:data.image.mimeType, id:data.idempotency_key+':image'}));
    prior.image = true;
    saved.sends[data.idempotency_key]=prior;
    await persist();
  }
  phase('delivered');
  return {accepted:true,duplicate:false};
}

const server=http.createServer(async (req,res)=>{
  const respond=(code,data)=>{res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(!authorized(req.headers.authorization)) return respond(401,{error:'Invalid bridge authentication'});
  if(req.method==='GET' && req.url==='/health') return respond(200,{...connection,delivery});
  if(req.method!=='POST' || req.url!=='/send') return respond(404,{error:'Not found'});
  try {
    let raw='', size=0;
    for await(const chunk of req){size+=chunk.length;if(size>3_000_000) throw new Error('Request too large');raw+=chunk;}
    const data=JSON.parse(raw);
    const pending=sends.then(()=>send(data));
    sends=pending.catch(()=>{});
    respond(200,await pending);
  } catch(error) {
    delivery={phase:delivery.phase,error_code:safeCode(error)};
    console.error('Photon delivery failed: phase='+delivery.phase+' code='+delivery.error_code,JSON.stringify(safeDiagnostic(error,[token,projectSecret,projectId,recipient])));
    respond(503,{accepted:false,error:'Photon delivery failed',phase:delivery.phase,code:delivery.error_code});
  }
});
server.listen(Number(process.env.PHOTON_BRIDGE_PORT || 3001),'127.0.0.1',()=>console.log('Photon bridge listening on localhost.'));

async function connect(){
  if(!projectId || !projectSecret || !recipient || !token) return;
  connection={connected:false,status:'connecting',message:'Connecting to Photon managed iMessage.'};
  try {
    app=await Spectrum({projectId,projectSecret,providers:[imessage.config()],options:{logLevel:'error'}});
    connection={connected:true,status:'ready',message:'Spectrum initialized. Delivery is confirmed only after Send succeeds.'};
    for await(const [space,message] of app.messages){
      if(message.platform!=='imessage' || message.sender?.kind==='agent' || message.content?.type!=='text') continue;
      const sender=message.sender?.id || message.sender?.address || '';
      if(canonical(sender)!==canonical(recipient)) continue;
      inboundSpaces.set(canonical(sender),space);
      saved.routes[canonical(sender)]={id:space.id,phone:space.phone,type:space.type};
      await persist();
      const conversation=saved.conversations[space.id] || Object.values(saved.conversations).find(row=>canonical(row.recipient)===canonical(sender));
      if(!conversation) continue;
      const payload={...conversation, sender, message_id:message.id,answer:message.content.text};
      // Transport retries preserve message ID; backend grades each answer once.
      for(let attempt=0;attempt<5;attempt++){
        try {
          const response=await fetch(backend+'/api/photon/practice/reply',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(25000)});
          if(response.ok || [401,404,409,422].includes(response.status)) break;
        } catch {}
        await new Promise(resolve=>setTimeout(resolve,1000*(attempt+1)));
      }
    }
    connection={connected:false,status:'disconnected',message:'Photon inbound stream ended. Restart the bridge.'};
  } catch {
    connection={connected:false,status:'connection_failed',message:'Photon connection failed. Check project credentials and managed iMessage line.'};
    console.error('Photon connection failed; secrets and recipient omitted.');
  }
}
void connect();
for(const signal of ['SIGINT','SIGTERM']) process.on(signal,async()=>{server.close();await app?.stop();process.exit(0);});
