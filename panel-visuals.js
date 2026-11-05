/* HALOFLUX's slow advection field is visual art; the grids use observed metrics. */
const cAdv=byId('cAdv'),cDiff=byId('cDiff'),cIdx=byId('cIdx');
const flowState={mode:'advection',generation:0};
let flowParticles=[],flowGrid=null;
function panelsVisible(){return !agtSettings.panelsCollapsed&&!agtState.focusMode&&!document.body.classList.contains('panels-collapsed');}
let flowSeed=0x7b12ce;
function flowRandom(){flowSeed+=0x6D2B79F5;let t=flowSeed;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296;}
function resetFlowParticle(p,index,initial=false){p.x=flowRandom()*cAdv.w;p.y=flowRandom()*cAdv.h;p.life=(initial?0:1)+flowRandom()*3;}
resizePanels=function(){
  if(flowGrid){flowGrid.width=1;flowGrid.height=1;flowGrid=null;}
  [cAdv,cDiff,cIdx].forEach(fit);
  flowParticles=Array.from({length:320},(_,i)=>{const p={};resetFlowParticle(p,i,true);return p;});
  flowGrid=mkd(cAdv.w,cAdv.h,1);
  flowGrid.x.fillStyle='rgba(120,125,135,.25)';
  for(let i=6;i<cAdv.w;i+=12)for(let j=6;j<cAdv.h;j+=12)flowGrid.x.fillRect(i,j,1,1);
  cAdv.dataset.flowMode=flowState.mode;
};
// Match the original field and 22 px/s speed: the previous 65 px/s field
// changed direction more than twice as quickly, turning its curls into noise.
function flowAngle(x,y){return Math.sin(x*.035+T*.6)*Math.cos(y*.05-T*.4)*Math.PI+Math.sin((x+y)*.01+T*.2);}
function setFlowMode(mode){
  if(!['advection','projection','render'].includes(mode))return;
  flowState.mode=mode;cAdv.dataset.flowMode=mode;
  document.querySelectorAll('#advBtns [data-flow-mode]').forEach(button=>{
    const selected=button.dataset.flowMode===mode;
    button.classList.toggle('on',selected);button.setAttribute('aria-pressed',String(selected));
  });
  if(cAdv.x)cAdv.x.clearRect(0,0,cAdv.w,cAdv.h);
}
function bindFlowControls(){
  const modes=['advection','projection','render'];
  const descriptions=['原版平流：缓慢、连续的粒子轨迹','方向投影：查看流场方向箭头','轨迹渲染：突出粒子束与连续尾迹'];
  document.querySelectorAll('#advBtns button,#advBtns span').forEach((button,index)=>{
    button.dataset.flowMode=button.dataset.flowMode||modes[index];
    button.title=descriptions[index];
    if(button.tagName!=='BUTTON'){
      button.setAttribute('role','button');button.tabIndex=0;
      button.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setFlowMode(button.dataset.flowMode);}};
    }
    button.onclick=()=>setFlowMode(button.dataset.flowMode);
  });
  setFlowMode(flowState.mode);
}
function drawAdv(dt){
  const x=cAdv.x,w=cAdv.w,h=cAdv.h;if(!panelsVisible()||!x||w<1||h<1)return;
  const projection=flowState.mode==='projection',render=flowState.mode==='render';
  // Exponential fade keeps the same tail length when the panel is throttled.
  const fade=projection?1:1-Math.pow(render?.9:.78,Math.max(0,dt)*60);
  x.fillStyle=`rgba(14,15,18,${fade})`;x.fillRect(0,0,w,h);
  if(flowGrid)x.drawImage(flowGrid,0,0,w,h);
  if(projection){
    x.strokeStyle='rgba(152,224,207,.72)';x.lineWidth=.7;x.beginPath();
    for(let px=9;px<w;px+=18)for(let py=9;py<h;py+=18){
      const a=flowAngle(px,py),dx=Math.cos(a),dy=Math.sin(a),headX=px+dx*5,headY=py+dy*5;
      x.moveTo(px-dx*5,py-dy*5);x.lineTo(headX,headY);
      x.moveTo(headX-dx*2.6-dy*1.8,headY-dy*2.6+dx*1.8);x.lineTo(headX,headY);
      x.lineTo(headX-dx*2.6+dy*1.8,headY-dy*2.6-dx*1.8);
    }x.stroke();
  }
  x.beginPath();x.strokeStyle=projection?'rgba(238,243,255,.34)':render?'rgba(238,243,255,.9)':'rgba(235,238,244,.7)';x.lineWidth=render?1:.8;
  for(let i=0;i<flowParticles.length;i++){
    const p=flowParticles[i],a=flowAngle(p.x,p.y),dx=Math.cos(a),dy=Math.sin(a);
    const nx=p.x+dx*22*dt,ny=p.y+dy*22*dt;
    x.moveTo(p.x,p.y);x.lineTo(nx+dx*(render?3:2),ny+dy*(render?3:2));p.x=nx;p.y=ny;p.life-=dt;
    if(p.life<0||nx<0||nx>w||ny<0||ny>h)resetFlowParticle(p,i);
  }x.stroke();
}
function drawDiff(){
  const x=cDiff.x,w=cDiff.w,h=cDiff.h;if(!panelsVisible()||!x||w<1||h<1)return;
  if(agtSettings.panelView==='original'){cDiff.setAttribute('aria-label','HALOFLUX 原版动态色场：视觉演示，不表示任务数据');drawOriginalDiff();return;}
  x.clearRect(0,0,w,h);x.textBaseline='top';
  const font=Math.max(8,Math.min(10,w/36)),gap=12,left=(w-gap)*.48,right=left+gap,rw=w-right;
  x.font=`600 ${font}px ${FONT}`;x.fillStyle='#a9b4bd';x.fillText('来源 → 当前任务',0,0);x.fillText('当前上下文',right,0);
  const focus=focusedSession(),sources=agtState.snapshot?.sources||[];
  const bodyTop=font+10,footer=font+8,bodyH=Math.max(14,h-bodyTop-footer);
  const targetY=bodyTop+bodyH/2;
  for(let i=0;i<2;i++){
    const source=sources.find(v=>v.id===(i?'dsh':'codex')),online=!!source?.online,yy=bodyTop+(i+.5)*bodyH/2;
    const selected=online&&focus?.source===source.id;
    x.strokeStyle=selected?'#98e0cf':'#333d44';x.lineWidth=selected?1.2:.7;
    x.beginPath();x.moveTo(6,yy);x.bezierCurveTo(left*.35,yy,left*.65,targetY,left-8,targetY);x.stroke();
    x.fillStyle=online?'#98e0cf':'#4f5962';x.fillRect(1,yy-2,4,4);
    x.font=`${font}px ${FONT}`;x.fillText((i?'DSH':'Codex')+(online?' · '+(source.threads??source.sessions??0)+' 主任务':' · 未打开'),12,yy-font-3,left-20);
  }
  x.fillStyle=focus?'#d4e4dc':'#63716c';x.fillRect(left-10,targetY-3,6,6);
  x.font=`${font}px ${FONT}`;x.fillText(focus?focus.model:'等待任务',0,h-footer,left);
  const known=!!focus?.contextWindow,pct=known?occupancy(focus):0;
  x.fillStyle=pct>.8?'#f0a6c6':'#d1e4db';x.textAlign='right';x.font=`600 ${font}px ${FONT}`;x.fillText(known?(pct*100).toFixed(1)+'%':'未提供',w,0);x.textAlign='left';
  const cols=22,rows=8,cw=rw/cols,ch=Math.max(1,bodyH/rows);
  for(let j=0;j<rows;j++)for(let i=0;i<cols;i++){
    const index=j*cols+i,filled=known&&index<Math.round(pct*rows*cols);
    x.fillStyle=filled?(pct>.8?'#db94b4':'#92cbb8'):'#232a31';x.fillRect(right+i*cw,bodyTop+j*ch,Math.max(.5,cw-.7),Math.max(.5,ch-.8));
  }
  x.fillStyle='#87998f';x.font=`${font}px ${FONT}`;x.fillText(known?compactValue(focus.contextTokens)+' / '+compactValue(focus.contextWindow):'来源未报告上下文上限',right,h-footer,rw);
  cDiff.setAttribute('aria-label',`来源连接：${sources.map(s=>(s.id==='codex'?'Codex':'DSH')+(s.online?'在线':'未打开')).join('，')}；当前上下文${known?(pct*100).toFixed(1)+'%':'未提供'}`);
}
function drawIdx(){
  const x=cIdx.x,w=cIdx.w,h=cIdx.h;if(!panelsVisible()||!x||w<1||h<1)return;
  if(agtSettings.panelView==='original'){cIdx.setAttribute('aria-label','HALOFLUX 原版扫描矩阵：视觉演示，不表示任务数据');drawOriginalIdx();return;}
  x.clearRect(0,0,w,h);x.textBaseline='middle';
  const sessions=selectedSessions().slice(0,6),labels=['上下文','输入','输出','缓存'],font=Math.max(8,Math.min(10,w/37));
  const left=Math.min(72,w*.23),right=24,cols=28,top=14,cw=(w-left-right)/cols,ch=(h-top)/6;
  x.font=`600 ${font}px ${FONT}`;
  for(let k=0;k<4;k++){x.fillStyle=['#98e0cf','#b7b4ce','#f0a6c6','#9f94bd'][k];x.fillText(labels[k],left+k*7*cw,6);}
  const maxima=[1,Math.max(1,...sessions.map(s=>s.inputTokens)),Math.max(1,...sessions.map(s=>s.outputTokens)),Math.max(1,...sessions.map(s=>s.cachedTokens))];
  const scan=Math.floor(T*6)%cols;
  for(let j=0;j<6;j++){
    const s=sessions[j],values=s?[occupancy(s),s.inputTokens,s.outputTokens,s.cachedTokens]:[0,0,0,0],yc=top+j*ch+ch/2;
    const selected=s&&agtState.selected===s.source+':'+s.id;
    x.fillStyle=selected?'#ddede4':s?'#889b91':'#39463e';x.font=`${font}px ${FONT}`;
    x.fillText(s?String(j+1).padStart(2,'0')+' '+(s.title||'未命名').slice(0,5):'—',0,yc,left-4);
    for(let i=0;i<cols;i++){
      const kind=Math.floor(i/7),level=(i%7+.5)/7,ratio=values[kind]/maxima[kind],filled=s&&ratio>level;
      x.fillStyle=filled?['#98e0cf','#b7b4ce','#f0a6c6','#9f94bd'][kind]:'#252d33';
      if(i===scan&&filled)x.fillStyle='#f0f6f2';
      x.fillRect(left+i*cw,top+j*ch+1,Math.max(.5,cw-.8),Math.max(.5,ch-2));
    }
    x.fillStyle=s&&isActive(s)?'#f0a6c6':'#7f8d86';if(s)x.fillText(isActive(s)?'执行':'空闲',w-right+3,yc,right-3);
  }
  cIdx.setAttribute('aria-label','前六个任务：上下文为实际占比；输入、输出与缓存按当前六个任务最大值归一比较');
}
