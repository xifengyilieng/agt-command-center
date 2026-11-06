// Synthetic state-sync integration. All requests are intercepted; no collector or task data is read.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const origin = 'http://agt.sync.test';

function fixture(title = '合成执行中的任务') {
  const at = Date.now() / 1000, id = 'synthetic-sync-task';
  const session = {id, source:'codex', title, model:'synthetic-model', status:'running',
    isSubagent:false, parentId:null, cwd:'synthetic', lastAt:at, lastTool:'exec_command',
    inputTokens:1000, outputTokens:250, cachedTokens:500, contextTokens:1500, contextWindow:32000,
    tools:[], usageSamples:[]};
  return {at, discovering:false, sources:[{id:'codex', online:true, running:1, sessions:1, threads:1, subagents:0, note:''}],
    sessions:[session], events:[], history:[],
    topology:{nodes:[{id:'source:codex', kind:'source', source:'codex', label:'Codex', status:'running'},
      {id:'session:codex:'+id, kind:'session', source:'codex', sessionId:id, label:title, status:'running'}],
      edges:[{id:'owner:'+id, from:'source:codex', to:'session:codex:'+id, kind:'source-session',
        source:'codex', sessionId:id, status:'running'}]},
    totals:{online:1, running:1, sessions:1, threads:1, subagents:0, inputTokens:1000, outputTokens:250,
      outputRateInfo:{value:null, status:'waiting', reason:'Synthetic fixture'}}};
}

(async () => {
  // Capture one generated version before opening the browser.
  const assets = new Map();
  for (const name of ['index.html', 'runtime.js', 'screen.css']) {
    assets.set('/'+name, {body:await fs.readFile(path.join(root,name)), contentType:
      name.endsWith('.html')?'text/html':name.endsWith('.js')?'application/javascript':'text/css'});
  }
  for (const weight of [400,600,800]) assets.set(`/fonts/jetbrains-mono-${weight}.ttf`,
    {body:await fs.readFile(path.join(root,`vendor/fonts/jetbrains-mono-${weight}.ttf`)), contentType:'font/ttf'});
  const runtimeSha256 = crypto.createHash('sha256').update(assets.get('/runtime.js').body).digest('hex');
  const browser = await chromium.launch({headless:true, channel:process.env.AGT_BROWSER_CHANNEL||'msedge'});
  const page = await browser.newPage({viewport:{width:1280,height:800}});
  const errors = [], unexpectedRequests = [], held = [], holdWaiters = [];
  const service = {state:fixture(), epoch:'a', revision:1, mode:'state', heartbeat:null,
    holdNext:false, requests:[], changed:0, unchanged:0, errors:0};
  const etag = () => `W/"agt-fixture-${service.epoch}-${service.revision}"`;
  function change(title) { service.state=fixture(title); service.revision++; }
  function nextHeld() {
    return new Promise((resolve,reject) => {
      const timeout=setTimeout(()=>reject(Error('next natural request was not held')),2000);
      holdWaiters.push(value=>{clearTimeout(timeout);resolve(value);});
    });
  }
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(() => {
    window.__syncFetch={active:0,max:0,aborted:0};
    const fetchOriginal=window.fetch.bind(window);
    window.fetch=async function(url,options) {
      if(!String(url).includes('/api/state'))return fetchOriginal(url,options);
      const stats=window.__syncFetch;stats.active++;stats.max=Math.max(stats.max,stats.active);
      try{return await fetchOriginal(url,options);}
      catch(error){if(error.name==='AbortError')stats.aborted++;throw error;}
      finally{stats.active--;}
    };
    window.__syncRetrySchedules=[];
    const timeoutOriginal=window.setTimeout.bind(window);
    window.setTimeout=function(callback,delay,...args) {
      // Exercise real HTTP failures and retry scheduling without waiting out
      // a 15-second outage. Normal 750ms polling and fetch timeouts stay real.
      if(callback?.name==='pollAGT') {
        window.__syncRetrySchedules.push(Number(delay));
        if(window.__syncFastRetries&&delay>=800)delay=25;
      }
      return timeoutOriginal(callback,delay,...args);
    };
  });
  await page.route('**/*',async route => {
    const url=new URL(route.request().url());
    if(url.origin!==origin){unexpectedRequests.push(url.origin+url.pathname);await route.abort();return;}
    if(url.pathname!=='/api/state') {
      const asset=assets.get(url.pathname==='/'?'/index.html':url.pathname);
      await route.fulfill(asset?asset:{status:404,body:''});return;
    }
    const request={at:performance.now(), ifNoneMatch:route.request().headers()['if-none-match']||'', mode:service.mode};
    service.requests.push(request);
    let heldRequest=false;
    if(service.holdNext) {
      heldRequest=true;
      service.holdNext=false;
      await new Promise(resolve=>{
        const pending={release:resolve};held.push(pending);
        holdWaiters.shift()?.(pending);
      });
    }
    try {
      if(service.mode==='error'){service.errors++;await route.fulfill({status:503,body:'Synthetic unavailable'});return;}
      const headers={ETag:etag(),'X-AGT-Checked-At':String(service.heartbeat??Date.now()/1000)};
      if(service.mode==='discovering') {
        await route.fulfill({json:{at:Date.now()/1000,discovering:true,sources:[],sessions:[]},headers});return;
      }
      if(request.ifNoneMatch===etag()){service.unchanged++;await route.fulfill({status:304,headers});}
      else{service.changed++;service.state.at=Date.now()/1000;await route.fulfill({json:service.state,headers});}
    } catch(error) {
      // An intentionally cancelled held request may have lost its route.
      if(!heldRequest)throw error;
    }
  });
  let report;
  try {
    await page.goto(origin+'/');
    await page.waitForFunction(()=>typeof agtState!=='undefined'&&agtState.snapshot?.sessions.length===1);
    await page.evaluate(()=>{
      // Rendering performance is measured in the separate smoothness probe.
      // Motion pause deliberately leaves the production data loop running.
      agtState.paused=true;syncScreenControls();
      window.__syncStableButton=document.querySelector('#agents button');
    });
    await page.waitForFunction(()=>typeof agtPoll!=='undefined'&&agtPoll.checkedAt>0&&!agtPoll.inflight);
    const firstTag=await page.evaluate(()=>agtPoll.etag);
    await page.waitForFunction(()=>window.__syncFetch.active===0&&agtPoll.checkedAt>agtState.snapshot.at+.2,{},{timeout:2000});
    assert.ok(service.unchanged>=1,'normal polling must request and receive304');
    assert.equal(await page.evaluate(()=>document.querySelector('#agents button')===window.__syncStableButton),true);

    service.heartbeat=Date.now()/1000-20;
    await page.waitForFunction(()=>agtState.failed&&document.getElementById('connection').textContent.includes('更新停滞'),{},{timeout:1800});
    const frozen=await page.evaluate(()=>agtPoll.checkedAt);
    assert.ok(Math.abs(frozen-service.heartbeat)<.001,'304 must use collector heartbeat, not request time');
    service.heartbeat=null;
    await page.waitForFunction(()=>!agtState.failed,{},{timeout:1800});
    assert.equal(await page.evaluate(()=>document.querySelector('#agents button')===window.__syncStableButton),true);

    const naturalTitle='自然轮询收到的新活动任务';
    change(naturalTitle);
    const naturalAt=performance.now();
    await page.waitForFunction(title=>document.getElementById('stBig').textContent===title,naturalTitle,{timeout:1600,polling:'raf'});
    const naturalUpdateMs=performance.now()-naturalAt;
    assert.ok(naturalUpdateMs<1500,'active title must reach DOM through natural polling');

    service.epoch='restart';service.revision=1;service.state=fixture('采集器重启后的合成任务');
    await page.waitForFunction(title=>document.getElementById('stBig').textContent===title,service.state.sessions[0].title,{timeout:1600});
    assert.notEqual(await page.evaluate(()=>agtPoll.etag),firstTag);
    assert.equal(await page.evaluate(()=>agtPoll.etag),etag());

    async function testWake(kind) {
      const pending=nextHeld();service.holdNext=true;
      await page.evaluate(kind=>{
        if(kind==='host')window.wallpaperPropertyListener.setPaused(true);
        else {
          window.__syncHidden=true;
          Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.__syncHidden});
          document.dispatchEvent(new Event('visibilitychange'));
        }
      },kind);
      const hold=await pending;
      const before=await page.evaluate(()=>({aborted:window.__syncFetch.aborted,failures:agtPoll.failures}));
      const title=kind==='host'?'宿主恢复取消旧请求并同步':'恢复可见取消旧请求并同步';change(title);
      const began=performance.now();
      await page.evaluate(kind=>{
        if(kind==='host')window.wallpaperPropertyListener.setPaused(false);
        else{window.__syncHidden=false;document.dispatchEvent(new Event('visibilitychange'));}
      },kind);
      await page.waitForFunction(title=>document.getElementById('stBig').textContent===title,title,{timeout:1200,polling:'raf'});
      const elapsed=performance.now()-began;
      const after=await page.evaluate(()=>({aborted:window.__syncFetch.aborted,failures:agtPoll.failures}));
      assert.ok(elapsed<1000,'resume must cancel held fetch and refresh within1s');
      assert.equal(after.failures,before.failures,'intentional wake abort must not count as service failure');
      assert.ok(after.aborted>before.aborted,'held fetch must actually be aborted');
      hold.release();
      return elapsed;
    }
    const hostAbortWakeMs=await testWake('host');
    const visibilityAbortWakeMs=await testWake('visibility');

    service.mode='error';
    await page.evaluate(()=>{window.__syncFastRetries=true;agtWakeData();});
    await page.waitForFunction(()=>agtPoll.failures>=5,{},{timeout:2500,polling:10});
    const maxFailures=await page.evaluate(()=>agtPoll.failures);
    const backoffs=await page.evaluate(()=>window.__syncRetrySchedules.slice());
    assert.ok(backoffs.some(ms=>ms>14000),'HTTP failures must exercise the production15s backoff');
    service.mode='discovering';
    await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('识别来源'),{},{timeout:1500,polling:10});
    const afterDiscovering=await page.evaluate(()=>({failures:agtPoll.failures,delay:window.__syncRetrySchedules.at(-1)}));
    assert.equal(afterDiscovering.failures,0,'a reachable discovering server must clear previous failures');
    assert.ok(afterDiscovering.delay<1000,'discovery must restore normal polling without15s backoff');
    await page.evaluate(()=>{window.__syncFastRetries=false;});
    service.mode='state';change('故障后恢复正常采集');
    const recoverAt=performance.now();
    await page.waitForFunction(title=>document.getElementById('stBig').textContent===title,service.state.sessions[0].title,{timeout:1600,polling:'raf'});
    const discoveryRecoveryMs=performance.now()-recoverAt;
    const fetchStats=await page.evaluate(()=>({...window.__syncFetch}));
    assert.equal(fetchStats.max,1,'wake/reconnect must not create overlapping state requests');
    assert.equal(await page.evaluate(()=>agtState.failed),false);
    assert.deepEqual(errors,[]);assert.deepEqual(unexpectedRequests,[]);
    report={ok:true,runtimeSha256,naturalUpdateMs,hostAbortWakeMs,visibilityAbortWakeMs,discoveryRecoveryMs,
      genuine304Freshness:true,frozenHeartbeatDetected:true,unchangedButtonRetained:true,restartEtagChanged:true,
      maxFailures,discoveryFailures:afterDiscovering.failures,discoveryNextPollMs:afterDiscovering.delay,
      fetchStats,http:{requests:service.requests.length,changed:service.changed,unchanged:service.unchanged,errors:service.errors},
      errors,unexpectedRequests,limitsNote:'All network intercepted; synthetic data only. Error retry waits accelerated, original requested backoffs asserted. Visibility uses a synthetic visibilitychange with document.hidden override. Normal polling and resume timings use real browser time.'};
    const output=path.join(root,'artifacts','state-sync-smoke.json');
    await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify({...report,output}));
  } finally {
    for(const pending of held)pending.release();
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
