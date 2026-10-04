import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import net from 'node:net';

async function unusedPort(){
  const server=net.createServer();
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const port=server.address().port;
  await new Promise(resolve=>server.close(resolve));
  return port;
}
function bounded(promise,milliseconds){
  let timer;
  const timeout=new Promise(resolve=>{timer=setTimeout(()=>resolve(false),milliseconds);});
  return Promise.race([promise,timeout]).finally(()=>clearTimeout(timer));
}

test('real SDK bridge startup, authentication and honest missing setup', {timeout:15000}, async()=>{
  const port=await unusedPort();
  const child=spawn(process.execPath,['server.mjs'],{cwd:import.meta.dirname,env:{...process.env,PHOTON_BRIDGE_PORT:String(port),PHOTON_BRIDGE_TOKEN:'unit-token',PHOTON_PROJECT_ID:'',PHOTON_PROJECT_SECRET:'',PHOTON_TEST_RECIPIENT:''},stdio:['ignore','pipe','pipe']});
  // Subscribe before startup: an early failed child must never make teardown wait forever.
  let exited=false, spawnError;
  const exit=new Promise(resolve=>{
    child.once('exit',()=>{exited=true;resolve(true);});
    child.once('error',error=>{exited=true;spawnError=error;resolve(true);});
  });
  let output='',errors='';
  child.stdout.on('data',chunk=>{output+=chunk.toString();});
  child.stderr.on('data',chunk=>{errors+=chunk.toString();});
  const url=`http://127.0.0.1:${port}`;
  const request=(path,options={})=>fetch(url+path,{...options,signal:AbortSignal.timeout(3000)});
  try {
    for(let i=0;i<60 && !output.includes('Photon bridge listening') && !exited;i++) await new Promise(r=>setTimeout(r,100));
    assert.ok(!exited && output.includes('Photon bridge listening'),`bridge startup failed: ${spawnError?.message || errors}`);
    const unauthorized=await request('/health');
    assert.equal(unauthorized.status,401);
    await unauthorized.arrayBuffer();
    const health=await request('/health',{headers:{Authorization:'Bearer unit-token'}});
    const status=await health.json();
    assert.equal(status.connected,false);assert.equal(status.status,'needs_setup');
    const rejected=await request('/send',{method:'POST',headers:{Authorization:'Bearer unit-token','Content-Type':'application/json'},body:JSON.stringify({recipient:'+15555555555',text:'must not send',idempotency_key:'unit'})});
    assert.equal(rejected.status,503);assert.equal((await rejected.json()).accepted,false);
  } finally {
    if(!exited){
      child.kill('SIGTERM');
      if(!await bounded(exit,1000)){child.kill('SIGKILL');await bounded(exit,1000);}
    }
    child.stdout.destroy();child.stderr.destroy();
  }
});


test('native inbound routing is preferred and survives as an explicit stored route',async()=>{
  const {resolveSpace,safeDiagnostic}=await import('./routing.mjs');
  const native={id:'native-id',phone:'shared',send(){}};
  const im={space:{create:async()=>{throw Error('should not synthesize');},get:async(id,params)=>({id,params})},user:async()=>{throw Error('should not resolve user');}};
  const canon=value=>value;
  assert.equal(await resolveSpace(im,'approved',new Map([['approved',native]]),{},canon),native);
  assert.deepEqual(await resolveSpace(im,'approved',new Map(),{approved:{id:'native-id',phone:'shared'}},canon),{id:'native-id',params:{phone:'shared'}});
  const safe=safeDiagnostic({message:'secret-value +15551234567 a@example.com',cause:{message:'secret-value'}},['secret-value']);
  assert.ok(!JSON.stringify(safe).includes('secret-value'));
  assert.ok(!JSON.stringify(safe).includes('15551234567'));
});
