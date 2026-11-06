// Synthetic, isolated visual/performance check. Never opens local task data or installs a wallpaper.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const baseline=process.argv.includes('--baseline');
const label=process.env.AGT_SMOOTH_LABEL||(baseline?'baseline':'candidate');
const seconds=Number(process.env.AGT_SMOOTH_SECONDS||15);
const output=path.join(root,'artifacts',`smoothness-${label}-20261105.json`);

function fixture(){
  const at=Date.now()/1000;
  const sessions=Array.from({length:32},(_,i)=>({
    id:`synthetic-task-${String(i).padStart(2,'0')}`,source:i%2?'dsh':'codex',
    title:`清晰度与流畅度合成任务 ${i+1}`,model:'synthetic-model',status:i<8?'running':'idle',
    isSubagent:i>=16,parentId:i>=16?`synthetic-task-${String(i%16).padStart(2,'0')}`:null,
    inputTokens:15000+i*1000,outputTokens:2000+i*150,cachedTokens:5000+i*100,
    contextTokens:22000+i*300,contextWindow:128000,lastAt:at-i,lastTool:'exec_command',cwd:'synthetic',
    tools:[{name:'exec_command',calls:10+i,status:'completed',durationMs:45},{name:'apply_patch',calls:2,status:'completed',durationMs:12}],
    usageSamples:Array.from({length:25},(_,n)=>({at:at-50+n*2,output:1000+n*30+i}))
  }));
  const sources=['codex','dsh'].map(id=>({id,online:true,running:4,sessions:16,threads:8,subagents:8,note:'Synthetic test source'}));
  const nodes=sources.map(s=>({id:`source:${s.id}`,kind:'source',source:s.id,label:s.id,status:'running'}));
  const edges=[];
  for(const session of sessions){
    const id=`session:${session.source}:${session.id}`;
    nodes.push({id,kind:'session',source:session.source,sessionId:session.id,label:session.title,status:session.status});
    edges.push({id:`edge:${id}`,from:`source:${session.source}`,to:id,kind:'source-session',source:session.source,sessionId:session.id,status:session.status,calls:10});
    for(const tool of session.tools){const tid=`tool:${session.id}:${tool.name}`;nodes.push({id:tid,kind:'tool',source:session.source,sessionId:session.id,label:tool.name,status:tool.status});edges.push({id:`edge:${tid}`,from:id,to:tid,kind:'session-tool',source:session.source,sessionId:session.id,status:tool.status,calls:tool.calls});}
  }
  return{at,discovering:false,sources,sessions,events:[],history:[],topology:{nodes,edges},totals:{online:2,running:8,sessions:32,threads:16,subagents:16,inputTokens:1000000,outputTokens:150000,cachedTokens:300000,outputRateInfo:{value:null,status:'idle'}}};
}
function summarize(values){
  if(!values.length)return{count:0};
  const sorted=[...values].sort((a,b)=>a-b),sum=values.reduce((a,b)=>a+b,0),pct=q=>sorted[Math.floor((sorted.length-1)*q)];
  const histogram={};for(const v of values){const key=String(Math.round(v/5)*5);histogram[key]=(histogram[key]||0)+1;}
  return{count:values.length,meanMs:sum/values.length,p50Ms:pct(.5),p95Ms:pct(.95),p99Ms:pct(.99),maxMs:sorted.at(-1),over50ms:values.filter(v=>v>50.5).length,histogram5ms:histogram};
}

(async()=>{
  // Snapshot generated resources once so a concurrent build cannot mix versions.
  const assets=new Map();
  for(const name of ['index.html','runtime.js','screen.css'])assets.set('/'+name,await fs.readFile(path.join(root,name)));
  const hash=crypto.createHash('sha256').update(assets.get('/runtime.js')).digest('hex');
  console.log(JSON.stringify({phase:'resources-captured',label,runtimeSha256:hash}));
  const options={headless:true,channel:process.env.AGT_BROWSER_CHANNEL||'msedge'};
  const browser=await chromium.launch(options);
  let report;
  try{
    const page=await browser.newPage({viewport:{width:2560,height:1080},deviceScaleFactor:1});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    let state=fixture(),revision=1;
    const http={requests:0,unchanged:0,changed:0};
    await page.route('http://agt.smooth.test/**',async route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/api/state'){
        http.requests++;const etag=`"synthetic-${revision}"`,checkedAt=String(Date.now()/1000);
        if(route.request().headers()['if-none-match']===etag){http.unchanged++;await route.fulfill({status:304,headers:{ETag:etag,'X-AGT-Checked-At':checkedAt}});}
        else{http.changed++;state.at=Number(checkedAt);await route.fulfill({json:state,headers:{ETag:etag,'X-AGT-Checked-At':checkedAt}});}
        return;
      }
      const name=url.pathname==='/'?'/index.html':decodeURIComponent(url.pathname);
      const file=path.resolve(root,'.'+name);
      if(!file.startsWith(root+path.sep)){await route.abort();return;}
      try{const body=assets.has(name)?assets.get(name):await fs.readFile(file);await route.fulfill({body,contentType:name.endsWith('.html')?'text/html':name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'application/octet-stream'});}catch{await route.fulfill({status:404,body:''});}
    });
    await page.goto('http://agt.smooth.test/');
    await page.waitForFunction(()=>typeof ready!=='undefined'&&ready&&agtState.snapshot?.sessions.length===32&&T>0);
    await page.evaluate(()=>document.fonts.ready);
    await page.waitForTimeout(1000);
    if(!baseline){
      // Prewarm the bounded rotating-card cache across three complete revolutions.
      // This is outside timing collection and restores the original ring angle.
      await page.evaluate(()=>{const angle=ringRot;for(let i=0;i<216;i++){ringRot=angle+(i%72)*Math.PI*2/72;drawStage(0);}ringRot=angle;drawStage(0);});
      await page.waitForTimeout(350);
    }
    const before=await page.evaluate(()=>{
      window.__smooth={frames:[],drawMs:[],panel:[],panelMs:[],field:[],mutations:0,canvasesCreated:0};
      window.__stableAgentButton=document.querySelector('#agents button');
      const stats=window.__smooth;
      const note=agtNoteFrame;agtNoteFrame=function(now){stats.frames.push(now);return note(now);};
      const draw=drawStage;drawStage=function(dt){const start=performance.now();try{return draw(dt);}finally{stats.drawMs.push(performance.now()-start);}};
      const panel=drawAdv;drawAdv=function(dt){const start=performance.now();stats.panel.push(start);try{return panel(dt);}finally{stats.panelMs.push(performance.now()-start);}};
      const field=drawArtField;let previous=-1;drawArtField=function(){const result=field();if(agtArtAt!==previous){stats.field.push(performance.now());previous=agtArtAt;}return result;};
      const create=document.createElement.bind(document);document.createElement=function(name,...args){if(name.toLowerCase()==='canvas')stats.canvasesCreated++;return create(name,...args);};
      new MutationObserver(records=>{stats.mutations+=records.length;}).observe(document.querySelector('.app'),{subtree:true,childList:true,characterData:true});
      return{at:Date.now(),heap:performance.memory?.usedJSHeapSize||null,backingResizes:typeof agtCardBackingResizes==='number'?agtCardBackingResizes:null};
    });
    const firstChunk=Math.max(0,seconds-15);
    if(firstChunk)await page.waitForTimeout(firstChunk*1000);
    const lateBefore=await page.evaluate(()=>({backingResizes:typeof agtCardBackingResizes==='number'?agtCardBackingResizes:null,created:window.__smooth.canvasesCreated}));
    await page.waitForTimeout((seconds-firstChunk)*1000);
    const measured=await page.evaluate(()=>{
      function geometry(c){const r=c.getBoundingClientRect();return{id:c.id,css:[r.width,r.height],backing:[c.width,c.height],ratioX:c.width/Math.max(1,r.width),ratioY:c.height/Math.max(1,r.height)};}
      const fx=typeof agtFxCanvas!=='undefined'?agtFxCanvas:null;
      const pool=typeof agtCardCanvasPool!=='undefined'?agtCardCanvasPool:[];
      const known=[stage,fx,webBase,...webLayers,trailC,farC,glowC,laneC,agtArtComposite,agtGlowBlur,flowGrid,cAdv,cDiff,cIdx,...slots.map(s=>s.c),...pool].filter(Boolean);
      return{stats:window.__smooth,at:Date.now(),heap:performance.memory?.usedJSHeapSize||null,quality:agtSettings.quality,targetFps:agtFrameRate(),renderStats:{...window.agtRenderStats},adaptive:typeof agtAdaptive!=='undefined'?{...agtAdaptive}:null,dpr:devicePixelRatio,stage:geometry(stage),panels:[cAdv,cDiff,cIdx].map(geometry),cardDpr:typeof agtCardDpr==='number'?agtCardDpr:null,cardCount:slots.length,pooledCards:pool.length,backingResizes:typeof agtCardBackingResizes==='number'?agtCardBackingResizes:null,pixelTotal:known.reduce((n,c)=>n+c.width*c.height,0),unchangedButtonRetained:window.__stableAgentButton===document.querySelector('#agents button'),connectionFresh:!agtState.failed,checkedAtAge:typeof agtPoll!=='undefined'?Date.now()/1000-agtPoll.checkedAt:null};
    });
    const intervals=times=>times.slice(1).map((v,i)=>v-times[i]);
    const frame=summarize(intervals(measured.stats.frames));frame.fps=1000/frame.meanMs;
    const panel=summarize(intervals(measured.stats.panel));panel.fps=1000/panel.meanMs;
    const field=summarize(intervals(measured.stats.field));field.fps=1000/field.meanMs;
    // Event dispatch to visible DOM and two RAF opportunities; excludes automation transport.
    const click=await page.evaluate(async()=>{
      const button=document.getElementById('toolsToggle'),menu=document.getElementById('toolsMenu'),at=performance.now();button.click();
      const mutationMs=performance.now()-at;await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
      return{mutationMs,twoRafMs:performance.now()-at,visible:!menu.hidden};
    });
    const changedTitle='合成任务更新已到达画面';state.sessions[0].title=changedTitle;revision++;
    const beganNatural=performance.now();
    if(!baseline)await page.waitForFunction(title=>document.getElementById('stBig').textContent===title,changedTitle,{polling:'raf',timeout:1800});
    const naturalUpdateMs=baseline?null:performance.now()-beganNatural;
    const update=await page.evaluate(async ({title,baseline})=>{
      const button=document.getElementById('motionButton');button.click();
      const paused=T;await new Promise(resolve=>setTimeout(resolve,350));const stopped=T===paused;
      button.click();const resumeAt=performance.now();await new Promise(resolve=>{function check(){if(T!==paused)resolve();else requestAnimationFrame(check);}requestAnimationFrame(check);});
      const resumeMs=performance.now()-resumeAt;
      const at=performance.now();if(baseline)await pollAGT();await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
      return{pauseStopped:stopped,resumeMs,fetchToTwoRafMs:baseline?performance.now()-at:null,titleVisible:document.getElementById('stBig').textContent===title};
    },{title:changedTitle,baseline});
    update.naturalUpdateMs=naturalUpdateMs;
    if(!baseline){
      await page.evaluate(()=>{document.getElementById('motionButton').click();window.__pausedStamp=T;window.__pausedRaster=stage.toDataURL();});
      // Change every card's visible text; the currently focused session may be
      // outside the rotating ring's visible arc at this exact camera position.
      const pausedTitle='暂停中到达的新任务数据';state.sessions.forEach((session,i)=>{session.title=i?pausedTitle+' '+i:pausedTitle;});revision++;
      await page.waitForFunction(title=>document.getElementById('stBig').textContent===title,pausedTitle,{polling:'raf',timeout:1800});
      await page.evaluate(async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);});
      const pausedData=await page.evaluate(()=>({simulationStopped:T===window.__pausedStamp,rasterRepainted:stage.toDataURL()!==window.__pausedRaster}));
      update.pausedData=pausedData;
      await page.evaluate(()=>document.getElementById('motionButton').click());
      await page.evaluate(()=>window.wallpaperPropertyListener.setPaused(true));
      await page.waitForTimeout(900);
      const resumeTitle='宿主恢复立即同步的新任务';state.sessions[0].title=resumeTitle;revision++;
      const beganResume=performance.now();await page.evaluate(()=>window.wallpaperPropertyListener.setPaused(false));
      await page.waitForFunction(title=>document.getElementById('stBig').textContent===title,resumeTitle,{polling:'raf',timeout:1500});
      update.hostResumeFreshMs=performance.now()-beganResume;
    }
    const {stats,...geometry}=measured;
    report={label,runtimeSha256:hash,viewport:{width:2560,height:1080},syntheticSessions:32,durationMs:measured.at-before.at,warmupRevolutions:baseline?0:3,frame,panel,field,drawCpu:summarize(stats.drawMs),panelCpu:summarize(stats.panelMs),mutations:stats.mutations,canvasesCreatedAfterWarmup:stats.canvasesCreated,canvasesCreatedLast15Seconds:stats.canvasesCreated-lateBefore.created,cardBackingResizesDuringMeasurement:measured.backingResizes===null?null:measured.backingResizes-before.backingResizes,cardBackingResizesLast15Seconds:measured.backingResizes===null?null:measured.backingResizes-lateBefore.backingResizes,heapBefore:before.heap,...geometry,click,update,http,errors,limitsNote:'Browser timing is a comparison probe, not native Wallpaper Engine presentation timing. Click measures DOM event dispatch through two RAF opportunities, excluding native DeskGo command polling. Private bytes and physical disk must be sampled separately.'};
    await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify(report,null,2)+'\n');
    await page.screenshot({path:path.join(root,'artifacts',`smoothness-${label}-20261105.png`)});
    await page.screenshot({path:path.join(root,'artifacts',`smoothness-${label}-ring-crop-20261105.png`),clip:{x:100,y:150,width:720,height:440}});
    await page.locator('.panels').screenshot({path:path.join(root,'artifacts',`smoothness-${label}-panels-crop-20261105.png`)});
    assert.deepEqual(errors,[]);assert.equal(click.visible,true);assert.equal(update.pauseStopped,true);assert.equal(update.titleVisible,true);
    if(!baseline){
      assert.ok(measured.panels.every(p=>p.ratioX>=measured.dpr*.98&&p.ratioY>=measured.dpr*.98),'panel text must retain native raster density');
      assert.ok(frame.fps>=28,'rendered motion should sustain at least28FPS');
      assert.ok(frame.p95Ms<=50.5,'95% of rendered intervals should fit50ms');
      assert.ok(panel.fps>=14,'particle panel should animate at least14FPS');
      assert.ok(update.resumeMs<350,'resume must render within350ms');
      assert.ok(update.naturalUpdateMs<=1500,'new fixture data must reach DOM naturally within1.5s');
      assert.ok(update.hostResumeFreshMs<=1000,'host resume must refresh data within1s');
      assert.ok(http.unchanged>=5,'unchanged polls must exercise304responses');
      assert.equal(measured.unchangedButtonRetained,true,'unchanged updates must preserve agent button identity');
      assert.equal(measured.connectionFresh,true,'304checkedAt must keep connection fresh');
      assert.equal(update.pausedData.simulationStopped,true,'paused data refresh must not advance simulation time');
      assert.equal(update.pausedData.rasterRepainted,true,'paused data refresh must repaint the sharp information canvas');
    }
    console.log(JSON.stringify({ok:true,output,frame,panel,field,panels:measured.panels,drawCpu:report.drawCpu,click,update,errors}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
