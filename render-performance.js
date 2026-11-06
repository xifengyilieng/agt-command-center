/* Keep the upstream motion language while bounding raster work on wide desktops. */
const AGT_QUALITY={
  low:{pixels:1100000,dpr:1,web:650,runners:.3,comets:14,cards:4,trail:.65,flyers:4000,surfacePixels:900000,cardPixels:8000000,cardDpr:2.4,cardCache:64,farPixels:300000,fps:30,panelFps:30,fieldFps:30},
  balanced:{pixels:1700000,dpr:1.25,web:1000,runners:.48,comets:22,cards:5,trail:.8,flyers:6500,surfacePixels:1100000,cardPixels:12000000,cardDpr:2.4,cardCache:64,farPixels:450000,fps:60,panelFps:30,fieldFps:30},
  high:{pixels:2600000,dpr:1.5,web:1500,runners:.7,comets:30,cards:6,trail:1,flyers:10000,surfacePixels:1700000,cardPixels:16000000,cardDpr:3,cardCache:80,farPixels:700000,fps:60,panelFps:30,fieldFps:60}
};
let agtQuality=AGT_QUALITY[agtSettings.quality]||AGT_QUALITY.balanced;
let agtCardDpr=agtQuality.cardDpr,agtSharpDpr=1,agtFxCanvas=null;
let agtPanelsDirty=true,agtPanelPaintAt=0,agtNextDataPanelAt=0,agtPanelWakePending=false;
function agtMarkPanelsDirty(){
  agtPanelsDirty=true;
  // Pausing animation must not freeze newly received monitoring data.
  if(typeof ready!=='undefined'&&ready&&!document.hidden&&(agtState.paused||agtState.wallpaperPaused)&&!agtPanelWakePending){
    agtPanelWakePending=true;requestAnimationFrame(now=>{
      agtPanelWakePending=false;agtDrawDataPanels(now);
      if(agtFxCanvas&&!document.hidden&&(agtState.paused||agtState.wallpaperPaused)){
        const budget=agtQuality.cards;try{agtQuality.cards=slots.length;agtPaintInformationFrame(0);}finally{agtQuality.cards=budget;}
      }
    });
  }
}
function agtUiPixelRatio(){return Math.max(1,Math.min(2,window.devicePixelRatio||1));}
function agtNextDeadline(now,deadline,interval){return deadline?deadline+interval*Math.max(1,Math.floor((now-deadline)/interval)+1):now+interval;}
function agtDrawDataPanels(now){
  if(!panelsVisible())return;
  if(agtPanelsDirty||(agtSettings.panelView==='original'&&now+.75>=agtNextDataPanelAt)){
    agtPanelsDirty=false;agtPanelPaintAt=now;agtNextDataPanelAt=agtNextDeadline(now,agtNextDataPanelAt,1000/30);drawDiff();drawIdx();
  }
}
const agtAdaptive={level:0,fallback:false,startedAt:0,workMs:0,frames:0,slowWindows:0,goodWindows:0,cpuPressureWindows:0,resizePending:false,ignoreUntil:performance.now()+2500};
const agtRawTiming={last:null,spans:new Float64Array(16),cursor:0,count:0,total:0,reportAt:null,reportFrames:0};
function agtCanRenderFrame(now,interval){
  const timing=agtRawTiming;
  const latestGap=timing.last===null?null:now-timing.last;
  if(latestGap!==null){
    if(latestGap>0&&latestGap<100){
      if(timing.count===timing.spans.length)timing.total-=timing.spans[timing.cursor];
      else timing.count++;
      timing.spans[timing.cursor]=latestGap;
      timing.total+=latestGap;
      timing.cursor=(timing.cursor+1)%timing.spans.length;
    }else if(latestGap>=100){
      timing.count=0;timing.cursor=0;timing.total=0;
    }
  }
  timing.last=now;
  if(timing.reportAt===null)timing.reportAt=now;
  else{
    timing.reportFrames++;
    const elapsed=now-timing.reportAt;
    if(elapsed>=1000){
      window.agtRenderStats.rawRafFps=Number((timing.reportFrames*1000/elapsed).toFixed(2));
      timing.reportAt=now;timing.reportFrames=0;
    }
  }

  // The host already provides the desired cadence on a 60 Hz desktop.
  // Avoid rejecting its slightly early callbacks with a second limiter.
  const hostPaces=latestGap>=interval*.75&&timing.count===timing.spans.length&&timing.total/timing.count>=interval*.99;
  if(hostPaces){nextFrameAt=now+interval;return true;}
  if(now+.75<nextFrameAt){window.agtRenderStats.limiterSkips++;return false;}
  nextFrameAt=agtNextDeadline(now,nextFrameAt,interval);
  return true;
}
function agtFxStride(){return agtAdaptive.level>=1?2:1;}
function agtPixelRatio(w,h){const scale=agtAdaptive.level>=2?.75:1;return scale*Math.min(window.devicePixelRatio||1,agtQuality.dpr,Math.sqrt(agtQuality.pixels/Math.max(1,w*h)));}
function agtSurfaceRatio(w,h,d){return Math.min(d,Math.sqrt(agtQuality.surfacePixels/Math.max(1,w*h)));}
function agtFrameRate(){return Math.min(agtState.fps,agtQuality.fps,agtAdaptive.fallback?30:60);}
function agtResizeStageSurfaces(){
  agtSharpDpr=agtUiPixelRatio();
  stage.width=Math.max(1,Math.round(W*agtSharpDpr));stage.height=Math.max(1,Math.round(H*agtSharpDpr));
  agtReleaseCanvas(agtFxCanvas);agtFxCanvas=document.createElement('canvas');
  agtFxCanvas.width=Math.max(1,Math.round(W*DPR));agtFxCanvas.height=Math.max(1,Math.round(H*DPR));agtFxCanvas.x=agtFxCanvas.getContext('2d');
  Object.assign(agtAdaptive,{startedAt:0,workMs:0,frames:0,slowWindows:0,goodWindows:0,cpuPressureWindows:0,ignoreUntil:performance.now()+1800});
  agtMarkPanelsDirty();
}
function agtScheduleFrame(callback){
  // Wallpaper Engine disables browser timer throttling. Explicitly sleep while covered.
  if(agtFrameRaf||agtSleepTimer)return;
  const queue=()=>{agtFrameRaf=requestAnimationFrame(now=>{agtFrameRaf=0;callback(now);});};
  if(document.hidden||agtState.paused||agtState.wallpaperPaused)agtSleepTimer=setTimeout(()=>{agtSleepTimer=0;queue();},250);
  else queue();
}
let agtFrameRaf=0,agtSleepTimer=0;
function agtWakeRender(){
  if(typeof started==='undefined'||!started)return;
  if(agtSleepTimer){clearTimeout(agtSleepTimer);agtSleepTimer=0;}
  last=performance.now();nextFrameAt=0;nextPanelAt=0;agtLastFrameStamp=0;
  Object.assign(agtRawTiming,{last:null,cursor:0,count:0,total:0,reportAt:null,reportFrames:0});
  Object.assign(agtAdaptive,{startedAt:0,workMs:0,frames:0,slowWindows:0,goodWindows:0,cpuPressureWindows:0,ignoreUntil:performance.now()+1800});
  agtScheduleFrame(frame);
}
let agtCardFrame=0;
const agtCardCanvasPool=[];
let agtCardBackingResizes=0;
function agtTakeCardCanvas(width,height){
  const bw=Math.ceil(width/64)*64,bh=Math.ceil(height/32)*32;
  let index=-1;
  for(let i=0;i<agtCardCanvasPool.length;i++){
    const c=agtCardCanvasPool[i];
    if(c.width>=bw&&c.height>=bh&&(index<0||c.width*c.height<agtCardCanvasPool[index].width*agtCardCanvasPool[index].height))index=i;
  }
  if(index<0)index=agtCardCanvasPool.findIndex(c=>c.width===1);
  if(index<0&&agtCardCanvasPool.length){
    let cost=Infinity;
    for(let i=0;i<agtCardCanvasPool.length;i++){
      const c=agtCardCanvasPool[i],growth=Math.max(bw,c.width)*Math.max(bh,c.height)-c.width*c.height;
      if(growth<cost){cost=growth;index=i;}
    }
  }
  if(index>=0)return agtCardCanvasPool.splice(index,1)[0];
  const canvas=document.createElement('canvas');canvas.width=1;canvas.height=1;return canvas;
}
function agtReleaseCardPool(){agtCardCanvasPool.forEach(agtReleaseCanvas);}
function agtRetireCard(slot,retain=true){
  const canvas=slot.c;if(!canvas)return;
  slot.c=null;slot.cacheDpr=0;
  if(!retain||agtCardCanvasPool.length>=24)agtReleaseCanvas(canvas);
  if(agtCardCanvasPool.length<24)agtCardCanvasPool.push(canvas);
}
function agtBeginCardFrame(){agtCardFrame++;}
function agtDesiredCardDpr(){const wanted=agtSharpDpr*cam.z;return Math.max(agtQuality.cardDpr,wanted>3?4:wanted>2.4?3:2.4);}
function agtTouchCard(slot){slot.lastPaintFrame=agtCardFrame;}
function agtTrimCardCache(){
  const cached=slots.filter(s=>s.c);let pixels=cached.reduce((sum,s)=>sum+s.c.width*s.c.height,0),count=cached.length;
  const retired=cached.filter(s=>s.lastPaintFrame!==agtCardFrame).sort((a,b)=>(a.lastPaintFrame||0)-(b.lastPaintFrame||0));
  for(const slot of retired){
    if(count<=agtQuality.cardCache)break;
    pixels-=slot.c.width*slot.c.height;count--;agtRetireCard(slot);
  }
  let pooledPixels=agtCardCanvasPool.reduce((sum,c)=>sum+c.width*c.height,0);
  for(const canvas of agtCardCanvasPool){
    if(pixels+pooledPixels<=agtQuality.cardPixels)break;
    pooledPixels-=canvas.width*canvas.height-1;agtReleaseCanvas(canvas);
  }
  for(const slot of retired){
    if(pixels+pooledPixels<=agtQuality.cardPixels)break;
    if(!slot.c)continue;
    pixels-=slot.c.width*slot.c.height;agtRetireCard(slot,false);
  }
}
window.agtSetQuality=function(name){
  const next=AGT_QUALITY[name]||AGT_QUALITY.balanced;
  if(next===agtQuality)return;
  agtQuality=next;agtCardDpr=next.cardDpr;Object.assign(agtAdaptive,{level:0,fallback:false,startedAt:0,workMs:0,frames:0,slowWindows:0,goodWindows:0,cpuPressureWindows:0});
  if(started)requestAnimationFrame(resizeAll);
};
function agtPaintPausedFrame(){
  const budget=agtQuality.cards;
  try{agtQuality.cards=slots.length;drawStage(0);drawAdv(0);drawDiff();drawIdx();}
  finally{agtQuality.cards=budget;}
}
function agtReleaseCanvas(canvas){if(canvas){canvas.width=1;canvas.height=1;}}
function agtReleaseWebBuffers(){
  [webBase,...webLayers,agtArtComposite].forEach(agtReleaseCanvas);
  webBase=null;webLayers=[];agtArtComposite=null;agtArtSource=null;
}
function agtReleaseStageBuffers(){
  agtReleaseWebBuffers();
  [trailC,farC,glowC,laneC,agtGlowBlur,agtFxCanvas].forEach(agtReleaseCanvas);
  agtReleaseCardPool();
  agtGlowBlur=null;
}
const spawnVisualComet=spawnComet;
spawnComet=function(){if(comets.length<agtQuality.comets)spawnVisualComet();};
const spawnVisualFlyBurst=spawnFlyBurst;
spawnFlyBurst=function(){if(flyers.length>=agtQuality.flyers)return;spawnVisualFlyBurst();if(flyers.length>agtQuality.flyers)flyers.length=agtQuality.flyers;};
let agtArtComposite=null,agtArtSource=null,agtArtAt=-1,agtArtNextAt=0;
function drawArtField(){
  const ws=2*R+4;
  if(agtArtSource!==webBase){agtReleaseCanvas(agtArtComposite);agtArtSource=webBase;agtArtComposite=mkd(ws,ws,Math.min(DPR,agtQuality.trail));agtArtAt=-1;agtArtNextAt=0;}
  if(agtArtAt<0||T+.00075>=agtArtNextAt){
    agtArtAt=T;agtArtNextAt=agtNextDeadline(T,agtArtNextAt,1/agtQuality.fieldFps);const x=agtArtComposite.x;x.globalAlpha=1;x.globalCompositeOperation='source-over';x.drawImage(webBase,0,0,ws,ws);
    x.globalCompositeOperation='lighter';
    webLayers.forEach((c,k)=>{x.globalAlpha=Math.min(1,C.webPulseBase+C.webPulseAmp*layerEnv[k]);x.drawImage(c,0,0,ws,ws);});x.globalAlpha=1;
  }
  S.drawImage(agtArtComposite,-R-2,-R-2,ws,ws);S.globalCompositeOperation='lighter';
}
const drawEffectStage=drawStage;
function agtPaintInformationFrame(dt){
  const previousDpr=DPR,previousContext=S,previousMain=S_MAIN;
  try{
    S=agtSharpContext;S_MAIN=S;DPR=agtSharpDpr;
    S.setTransform(1,0,0,1,0,0);S.globalAlpha=1;S.globalCompositeOperation='copy';S.drawImage(agtFxCanvas,0,0,stage.width,stage.height);S.globalCompositeOperation='source-over';
    camT(S,DPR);drawTaskGraph(dt,glowC.x);drawRing(dt);drawTaskBursts(dt);
    S.setTransform(DPR,0,0,DPR,0,0);drawCallout();
  }finally{DPR=previousDpr;S=previousContext;S_MAIN=previousMain;}
}
drawStage=function(dt){
  const begin=performance.now();
  try{S=agtFxCanvas.x;S_MAIN=S;drawEffectStage(dt);agtPaintInformationFrame(dt);}
  finally{S=agtSharpContext;S_MAIN=S;}
  agtMeasureWork(performance.now()-begin);
};
function agtMeasureWork(ms){
  const now=performance.now(),a=agtAdaptive;
  if(document.hidden||agtState.paused||agtState.wallpaperPaused||now<a.ignoreUntil)return;
  if(!a.startedAt){a.startedAt=now;return;}
  a.workMs+=ms;a.frames++;
  if(now-a.startedAt<2000)return;
  const average=a.workMs/Math.max(1,a.frames);window.agtRenderStats.workMs=Number(average.toFixed(2));
  const actualFps=a.frames*1000/(now-a.startedAt);
  window.agtRenderStats.measuredFps=Number(actualFps.toFixed(2));
  const targetFps=agtFrameRate(),overloaded=targetFps>=55&&(average>14||actualFps<53);
  a.slowWindows=overloaded?a.slowWindows+1:0;a.goodWindows=average<8&&actualFps>=targetFps*.95?a.goodWindows+1:0;
  a.cpuPressureWindows=a.level>=2&&targetFps>=55&&average>14?a.cpuPressureWindows+1:0;
  if(a.slowWindows>=2){
    a.slowWindows=0;
    if(a.level<2){a.level++;if(a.level===2&&!a.resizePending){a.resizePending=true;requestAnimationFrame(()=>{a.resizePending=false;resizeAll();});}}
  }
  // A slow host callback is reason to trim effects, not proof of CPU overload.
  if(a.cpuPressureWindows>=4)a.fallback=true;
  // Recover only after a long quiet period; avoid repeated buffer resizing.
  if(a.goodWindows>=15){a.goodWindows=0;if(a.fallback)a.fallback=false;else if(a.level>0){a.level--;if(a.level===1)requestAnimationFrame(resizeAll);}}
  Object.assign(window.agtRenderStats,{effectsLevel:a.level,fallback:a.fallback});a.startedAt=now;a.workMs=0;a.frames=0;
}
window.agtRenderStats={fps:0,targetFps:60,workMs:0,effectsLevel:0,fallback:false,frames:0,frameP50Ms:0,frameP95Ms:0,frameP99Ms:0,frameMaxMs:0,frameSamples:0,measuredFps:0,rawRafFps:0,limiterSkips:0};
let agtStatsAt=0,agtStatsFrames=0,agtLastFrameStamp=0;
const agtFrameIntervals=[];
function agtNoteFrame(now){
  if(agtLastFrameStamp&&now>agtLastFrameStamp&&now-agtLastFrameStamp<500){agtFrameIntervals.push(now-agtLastFrameStamp);if(agtFrameIntervals.length>240)agtFrameIntervals.shift();}
  agtLastFrameStamp=now;window.agtRenderStats.frames++;
  if(!agtStatsAt){agtStatsAt=now;agtStatsFrames=0;return;}
  agtStatsFrames++;
  if(now-agtStatsAt>=1000){
    const stats=window.agtRenderStats;stats.fps=Math.round(agtStatsFrames*1000/(now-agtStatsAt));stats.targetFps=agtFrameRate();
    const sorted=[...agtFrameIntervals].sort((a,b)=>a-b),percentile=q=>Number((sorted[Math.floor((sorted.length-1)*q)]||0).toFixed(2));
    const cardCanvases=[...slots.map(s=>s.c),...agtCardCanvasPool].filter(Boolean);
    Object.assign(stats,{frameP50Ms:percentile(.5),frameP95Ms:percentile(.95),frameP99Ms:percentile(.99),frameMaxMs:percentile(1),frameSamples:sorted.length,cardCanvases:cardCanvases.length,cardPixels:cardCanvases.reduce((sum,c)=>sum+c.width*c.height,0),cardBackingResizes:agtCardBackingResizes,stageDpr:agtSharpDpr});
    const label=byId('renderFps');if(label)label.textContent=`实际 ${stats.fps} FPS · 目标 ${stats.targetFps} FPS`;
    agtStatsAt=now;agtStatsFrames=0;
  }
}
function drawArtRunners(dt){
  const tx=trailC.x,ws=2*R+4,surge=.5+2.5*env;
  const count=Math.max(0,Math.round(C.runnerCount*agtQuality.runners));
  while(runners.length<count)runners.push(newRunner(true));
  if(runners.length>count)runners.length=count;
  tx.globalCompositeOperation='destination-out';
  tx.fillStyle=`rgba(0,0,0,${1-Math.exp(-dt*(3.2+env*3))})`;tx.fillRect(0,0,ws,ws);
  tx.globalCompositeOperation='source-over';tx.save();tx.translate(R+2,R+2);tx.lineCap='round';
  const paths=[new Path2D(),new Path2D()];
  for(let i=0;i<runners.length;i++){
    const r=runners[i];r.t+=dt*C.runnerSpeed*(r.mode===1?r.sp*surge:r.mode===2?r.sp+1.3*r.t*r.t:r.sp);
    if(r.t>=1){runners[i]=newRunner(false);continue;}
    const p=qpt(r.cv,r.t),path=paths[r.b?1:0];path.moveTo(r.px,r.py);path.lineTo(p.x,p.y);r.px=p.x;r.py=p.y;
  }
  tx.lineWidth=.9*C.runnerWidth;tx.strokeStyle=`rgba(250,251,255,${Math.min(1,.75*C.runnerBright)})`;tx.stroke(paths[0]);
  tx.lineWidth=1.3*C.runnerWidth;tx.strokeStyle=`rgba(240,166,198,${Math.min(1,.9*C.runnerBright)})`;tx.stroke(paths[1]);
  tx.restore();S.drawImage(trailC,-R-2,-R-2,ws,ws);
}
let agtGlowBlur=null;
function compositeGlow(){
  if(C.glow<=0)return;
  if(!agtGlowBlur||agtGlowBlur.width!==glowC.width||agtGlowBlur.height!==glowC.height){agtReleaseCanvas(agtGlowBlur);agtGlowBlur=mkd(glowC.width,glowC.height,1);}
  const x=agtGlowBlur.x;x.clearRect(0,0,agtGlowBlur.width,agtGlowBlur.height);
  if(HAS_FILTER)x.filter='blur(2px)';x.drawImage(glowC,0,0);if(HAS_FILTER)x.filter='none';
  S.globalAlpha=Math.min(1,.9*C.glow);S.drawImage(agtGlowBlur,0,0,W,H);S.globalAlpha=1;
}
