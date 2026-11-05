/* Keep the upstream motion language while bounding raster work on wide desktops. */
const AGT_QUALITY={
  low:{pixels:1150000,dpr:1,web:650,runners:.28,comets:12,cards:2,trail:.6,flyers:4000},
  balanced:{pixels:1700000,dpr:1.25,web:1000,runners:.48,comets:22,cards:4,trail:.8,flyers:8000},
  high:{pixels:4200000,dpr:1.75,web:1800,runners:1,comets:40,cards:6,trail:1.25,flyers:14000}
};
let agtQuality=AGT_QUALITY[agtSettings.quality]||AGT_QUALITY.balanced;
function agtPixelRatio(w,h){return Math.min(window.devicePixelRatio||1,agtQuality.dpr,Math.sqrt(agtQuality.pixels/Math.max(1,w*h)));}
window.agtSetQuality=function(name){
  const next=AGT_QUALITY[name]||AGT_QUALITY.balanced;
  if(next===agtQuality)return;
  agtQuality=next;
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
  [trailC,farC,glowC,laneC,agtGlowBlur].forEach(agtReleaseCanvas);
  agtGlowBlur=null;
}
const spawnVisualComet=spawnComet;
spawnComet=function(){if(comets.length<agtQuality.comets)spawnVisualComet();};
const spawnVisualFlyBurst=spawnFlyBurst;
spawnFlyBurst=function(){if(flyers.length>=agtQuality.flyers)return;spawnVisualFlyBurst();if(flyers.length>agtQuality.flyers)flyers.length=agtQuality.flyers;};
let agtArtComposite=null,agtArtSource=null,agtArtAt=-1;
function drawArtField(){
  const ws=2*R+4;
  if(agtArtSource!==webBase){agtArtSource=webBase;agtArtComposite=mkd(ws,ws,Math.min(DPR,agtQuality.trail));agtArtAt=-1;}
  if(agtArtAt<0||T-agtArtAt>=1/24){
    agtArtAt=T;const x=agtArtComposite.x;x.globalAlpha=1;x.globalCompositeOperation='source-over';x.drawImage(webBase,0,0,ws,ws);
    x.globalCompositeOperation='lighter';
    webLayers.forEach((c,k)=>{x.globalAlpha=Math.min(1,C.webPulseBase+C.webPulseAmp*layerEnv[k]);x.drawImage(c,0,0,ws,ws);});x.globalAlpha=1;
  }
  S.drawImage(agtArtComposite,-R-2,-R-2,ws,ws);S.globalCompositeOperation='lighter';
}
window.agtRenderStats={fps:0,targetFps:60};
let agtStatsAt=0,agtStatsFrames=0;
function agtNoteFrame(now){
  if(!agtStatsAt){agtStatsAt=now;agtStatsFrames=0;return;}
  agtStatsFrames++;
  if(now-agtStatsAt>=1000){
    const stats=window.agtRenderStats;stats.fps=Math.round(agtStatsFrames*1000/(now-agtStatsAt));stats.targetFps=agtState.fps;
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
  if(!agtGlowBlur||agtGlowBlur.width!==glowC.width||agtGlowBlur.height!==glowC.height)agtGlowBlur=mkd(glowC.width,glowC.height,1);
  const x=agtGlowBlur.x;x.clearRect(0,0,agtGlowBlur.width,agtGlowBlur.height);
  if(HAS_FILTER)x.filter='blur(2px)';x.drawImage(glowC,0,0);if(HAS_FILTER)x.filter='none';
  S.globalAlpha=Math.min(1,.9*C.glow);S.drawImage(agtGlowBlur,0,0,W,H);S.globalAlpha=1;
}
