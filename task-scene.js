/* The woven field is visual art; this sparse overlay contains only observed relations. */
const taskSceneState={nodes:[],curves:[],edgeIds:new Set(),signature:'',bursts:[],seenEvents:new Set(),ringBindings:new Map()};
const RING_PALETTE=['#eef0f6','#dce0eb','#f0a6c6','#e7eaf3','#b7a7ed','#98e0cf','#ece9f2','#d6d18f','#a9d9db'];
function stableHash(value){let h=2166136261;for(const c of String(value)){h^=c.charCodeAt(0);h=Math.imul(h,16777619);}return h>>>0;}
const fraction=(key,salt=0)=>(stableHash(String(key)+':'+salt)%100000)/100000;
function sceneSessions(){return selectedSessions();}
function sceneTopology(){
  const topology=agtState.snapshot?.topology||{nodes:[],edges:[]};
  const visibleSessions=new Set(sceneSessions().map(s=>s.source+':'+s.id));
  const nodes=topology.nodes.filter(n=>(agtState.filter==='all'||n.source===agtState.filter)&&(n.kind==='source'||visibleSessions.has(n.source+':'+n.sessionId)));
  const ids=new Set(nodes.map(n=>n.id));
  return{nodes,edges:topology.edges.filter(e=>ids.has(e.from)&&ids.has(e.to))};
}
function sessionForNode(node){return sceneSessions().find(s=>s.source===node.source&&s.id===node.sessionId);}
function rebuildTaskGraph(){
  if(!Number.isFinite(R)||R<1)return;
  const topology=sceneTopology(),sessions=sceneSessions();
  const selected=sessions.find(s=>s.source+':'+s.id===agtState.selected);
  const visibleSessions=[...(selected?[selected]:[]),...sessions.filter(s=>s!==selected&&isActive(s))].slice(0,6);
  const keys=new Set(visibleSessions.map(s=>s.source+':'+s.id));
  const edges=topology.edges.filter(e=>keys.has(e.source+':'+e.sessionId)||e.kind==='parent-child')
    .sort((a,b)=>Number(b.source+':'+b.sessionId===agtState.selected)-Number(a.source+':'+a.sessionId===agtState.selected)).slice(0,48);
  const ids=new Set(edges.flatMap(e=>[e.from,e.to]));
  const ordered=topology.nodes.filter(n=>ids.has(n.id));
  const sourceNodes=ordered.filter(n=>n.kind==='source'),sessionNodes=ordered.filter(n=>n.kind==='session');
  const nodeMap=new Map();
  for(const node of ordered){
    let p;
    if(node.kind==='source')p={x:R*.10,y:(sourceNodes.indexOf(node)-(sourceNodes.length-1)/2)*R*.32};
    else if(node.kind==='session'){
      const index=sessionNodes.indexOf(node),a=(-.82+1.64*(index+.5)/Math.max(1,sessionNodes.length))*Math.min(thV,1.1);
      p={x:Math.cos(a)*R*.84,y:Math.sin(a)*R*.84};
    }else{
      const peers=ordered.filter(n=>n.kind==='tool'&&n.sessionId===node.sessionId&&n.source===node.source);
      const index=peers.indexOf(node),sessionIndex=Math.max(0,sessionNodes.findIndex(n=>n.sessionId===node.sessionId&&n.source===node.source));
      p={x:R*(.38+.055*(index%4)),y:R*((sessionIndex+.5)/Math.max(1,sessionNodes.length)-.5)*.88+(index/(Math.max(1,peers.length-1))-.5)*R*.30};
    }
    nodeMap.set(node.id,{...node,...p,session:sessionForNode(node)});
  }
  taskSceneState.nodes=[...nodeMap.values()];taskSceneState.curves=[];taskSceneState.edgeIds=new Set(topology.edges.map(e=>e.id));
  for(const edge of edges){
    const from=nodeMap.get(edge.from),to=nodeMap.get(edge.to);if(!from||!to)continue;
    const session=sessions.find(s=>s.id===edge.sessionId&&s.source===edge.source);
    const dx=to.x-from.x,dy=to.y-from.y,bend=.1;
    taskSceneState.curves.push({p0:from,p1:to,c:{x:(from.x+to.x)/2-dy*bend,y:(from.y+to.y)/2+dx*bend},edgeId:edge.id,source:edge.source,sessionId:edge.sessionId,active:isActive(session),selected:agtState.selected===edge.source+':'+edge.sessionId,phase:fraction(edge.id)});
  }
}
const buildVisualWeb=buildWeb;
buildWeb=function(){agtReleaseWebBuffers();buildVisualWeb();rebuildTaskGraph();};
function drawTaskGraph(dt,G){
  if(!taskSceneState.nodes.length)return;
  S.save();S.translate(cx,cy);S.globalCompositeOperation='source-over';
  S.beginPath();S.arc(0,0,R-2,0,TAU);S.clip();
  for(const selected of [false,true]){
    S.beginPath();
    for(const cv of taskSceneState.curves){if(cv.selected!==selected)continue;S.moveTo(cv.p0.x,cv.p0.y);S.quadraticCurveTo(cv.c.x,cv.c.y,cv.p1.x,cv.p1.y);}
    S.strokeStyle=selected?'rgba(152,224,207,.22)':'rgba(161,176,191,.08)';S.lineWidth=selected?.7:.45;S.stroke();
  }
  S.beginPath();
  for(const cv of taskSceneState.curves){if(!cv.active&&!cv.selected)continue;const t=(T*.4+cv.phase)%1,p=qpt(cv,t),tail=qpt(cv,Math.max(0,t-.025));S.moveTo(tail.x,tail.y);S.lineTo(p.x,p.y);}
  S.strokeStyle='rgba(185,242,223,.65)';S.lineWidth=1;S.stroke();
  S.textBaseline='middle';
  for(const node of taskSceneState.nodes){
    const selected=node.sessionId&&agtState.selected===node.source+':'+node.sessionId,active=isActive(node.session||node);
    if(!selected&&node.kind==='tool')continue;
    const radius=node.kind==='source'?4:node.kind==='session'?2.5:1.3;
    S.strokeStyle=selected?'#afd8c9':'#566a66';S.lineWidth=.7;S.beginPath();S.arc(node.x,node.y,radius,0,TAU);S.stroke();
    if(node.kind==='source'||(node.kind==='session'&&selected)){
      const label=node.kind==='source'?(node.source==='codex'?'Codex':'DSH'):node.label;
      S.font=`${node.kind==='source'?'600 9':'8'}px ${FONT}`;S.fillStyle=selected?'#b8d5c9':'#7c938b';S.fillText(label.slice(0,18),node.x+7,node.y-7);
    }
  }
  S.restore();
}
function bindSlot(s){
  const list=sceneSessions(),index=Math.max(0,slots.indexOf(s));
  const session=list.length?list[index%list.length]:null;
  const binding={session,index,style:index%12};
  taskSceneState.ringBindings.set(s,binding);return binding;
}
function releaseTaskCards(){
  const retired=new Set([...slots,...taskSceneState.ringBindings.keys()]);
  taskSceneState.ringBindings.clear();
  for(const slot of retired){if(slot.c){slot.c.width=1;slot.c.height=1;slot.c=null;}}
}
renderSlot=function(s){
  const band=RO-R,h=s.hpx,c=s.c||document.createElement('canvas');
  const pixelWidth=Math.max(1,Math.ceil(band*RING_SC)),pixelHeight=Math.max(1,Math.ceil((h+2)*RING_SC));
  if(c.width!==pixelWidth||c.height!==pixelHeight){c.width=1;c.height=1;c.width=pixelWidth;c.height=pixelHeight;}
  const x=c.getContext('2d');x.setTransform(1,0,0,1,0,0);x.clearRect(0,0,c.width,c.height);x.setTransform(RING_SC,0,0,RING_SC,0,0);x.textBaseline='top';
  const binding=bindSlot(s),session=binding.session,margin=Math.max(8,rimW()+5),width=Math.max(10,band-margin*2),style=binding.style;
  x.fillStyle='#05090a';x.fillRect(0,0,band,h+2);x.save();x.beginPath();x.rect(margin,1,width,h);x.clip();
  x.font=`600 5px ${FONT}`;
  if(!session){x.fillStyle='#7b9186';x.fillText('暂无任务 · 等待连接',margin,4,width);x.restore();s.c=c;return;}
  const key=session.source+':'+session.id,selected=key===agtState.selected;
  const tintIndex=(binding.index+stableHash(session.id))%RING_PALETTE.length;
  const color=RING_PALETTE[tintIndex];
  x.fillStyle=selected?'#eafff2':color;x.fillText(session.title,margin,2,width);
  x.font=`4.6px ${FONT}`;x.fillStyle='#9faeaa';
  const tools=session.tools||[],samples=session.usageSamples||[],pct=occupancy(session);
  const lines=[`来源 ${session.source==='codex'?'Codex':'DSH'} · ${statusText[session.status]}`,`模型 ${session.model}`,`输入 ${pretty(session.inputTokens)}`,`输出 ${pretty(session.outputTokens)}`,`缓存 ${pretty(session.cachedTokens)}`,`上下文 ${session.contextWindow?(pct*100).toFixed(1)+'%':'未提供'}`];
  const primaryWidth=width*.62;
  const textRows=(items,y=10,step=5.2)=>items.forEach((line,i)=>{if(y+i*step<h-2){x.fillStyle=RING_PALETTE[(tintIndex+i)%RING_PALETTE.length];x.fillText(String(line),margin,y+i*step,primaryWidth);}});
  const bars=(values,labels,y=12)=>{const max=Math.max(1,...values),row=Math.max(5,Math.min(9,(h-y-2)/Math.max(1,values.length)));values.forEach((value,i)=>{const yy=y+i*row;if(yy>h-2)return;x.fillStyle='#667c70';x.fillText(labels[i],margin,yy,24);x.fillStyle=color;x.fillRect(margin+27,yy+1,Math.max(.5,(width-55)*value/max),2);x.fillStyle='#c2d6c9';x.fillText(compactValue(value),margin+width-24,yy,24);});};
  if(style===0||style===9){textRows(lines);}
  else if(style===1){bars([session.inputTokens,session.outputTokens,session.cachedTokens],['输入','输出','缓存']);}
  else if(style===2||style===10){textRows(tools.length?tools.slice(0,9).map(t=>`${toolLabel(t.name)} · 近 ${t.calls} 次 · ${statusText[t.status]||'已记录'}`):['暂无工具调用记录']);}
  else if(style===3){
    textRows([`输出 ${pretty(session.outputTokens)} · 真实采样`]);
    const observed=samples.slice(-25),values=observed.slice(1).map((v,i)=>Math.max(0,v.output-observed[i].output)),max=Math.max(1,...values),y=18,hh=Math.max(3,h-y-3);
    x.strokeStyle=color;x.lineWidth=.7;x.beginPath();values.forEach((v,i)=>{const px=margin+i/Math.max(1,values.length-1)*width,py=y+hh-v/max*hh;i?x.lineTo(px,py):x.moveTo(px,py);});x.stroke();
    if(values.length<2)textRows(['等待更多用量采样'],18);
  }else if(style===4){
    textRows([`任务编号 ${session.id.slice(-12)}`,`状态 ${statusText[session.status]}`,session.parentId?`主任务 ${session.parentId.slice(-12)}`:'主任务关系：未记录',`工具种类 ${tools.length}`]);
  }else if(style===5){
    textRows([`上下文 ${session.contextWindow?(pct*100).toFixed(1)+'%':'未提供'}`]);
    const cols=25,rows=Math.max(1,Math.floor((h-20)/4)),cw=width/cols;
    for(let j=0;j<rows;j++)for(let i=0;i<cols;i++){x.fillStyle=session.contextWindow&&j*cols+i<Math.round(pct*cols*rows)?color:'#1c2822';x.fillRect(margin+i*cw,19+j*4,Math.max(.5,cw-1),2.5);}
  }else if(style===6){bars(tools.slice(0,6).map(t=>t.calls),tools.slice(0,6).map(t=>toolLabel(t.name).slice(0,5)));if(!tools.length)textRows(['暂无调用记录']);}
  else if(style===7){textRows(tools.length?tools.slice(0,7).map(t=>`${toolLabel(t.name)} · ${t.durationMs==null?'耗时未提供':t.durationMs+' 毫秒'}`):['耗时数据：未提供']);}
  else if(style===8){
    const events=(agtState.snapshot?.events||[]).filter(e=>e.session===session.id&&e.source===session.source).slice(0,7);
    textRows(events.length?events.map(e=>new Date(e.at*1000).toLocaleTimeString('zh-CN',{hour12:false})+' '+toolLabel(e.message)):['暂无近期事件']);
  }else{textRows([`当前工具 ${session.lastTool?toolLabel(session.lastTool):'暂无记录'}`,`状态 ${statusText[session.status]}`,`关联工具 ${tools.length} 种`,...lines.slice(2)]);}
  drawDenseTelemetry(x,session,binding,margin,width,h,tintIndex);
  x.restore();s.c=c;
};
function drawDenseTelemetry(x,session,binding,margin,width,h,tintIndex){
  const ox=margin+width*.65,w=width*.35-2,tools=session.tools||[],skills=typeof AVAILABLE_SKILLS==='undefined'?[]:AVAILABLE_SKILLS;
  const rgb=i=>RING_PALETTE[(tintIndex+i)%RING_PALETTE.length];
  x.fillStyle='#080a0e';x.fillRect(ox-2,9,w+4,h-9);x.strokeStyle='#313743';x.lineWidth=.45;x.beginPath();x.moveTo(ox-3,9);x.lineTo(ox-3,h);x.stroke();
  x.font=`600 3.9px ${FONT}`;
  const mode=binding.index%6;
  if(mode===0){
    const rows=[['输入',session.inputTokens],['输出',session.outputTokens],['缓存',session.cachedTokens],['工具',tools.length]];
    rows.forEach((row,i)=>{const y=11+i*6;if(y>h-3)return;x.fillStyle=rgb(i);x.fillText(row[0],ox,y);x.fillText(pretty(row[1]),ox+w*.38,y,w*.62);x.strokeStyle='#30343c';x.beginPath();x.moveTo(ox,y+4.9);x.lineTo(ox+w,y+4.9);x.stroke();});
  }else if(mode===1||mode===4){
    x.fillStyle=rgb(2);x.fillText('可用技能 '+skills.length,ox,10,w);
    for(let i=0,y=17;y<h-3&&i<skills.length;i++,y+=5.4){const skill=skills[(binding.index*3+i)%skills.length];x.strokeStyle=rgb(i);x.lineWidth=.5;x.strokeRect(ox,y+.3,3,3);x.fillStyle=rgb(i+2);x.fillText(skill.name,ox+5,y,w-5);}
  }else if(mode===2){
    x.fillStyle=rgb(1);x.fillText('工具 / 调用表',ox,10,w);
    tools.slice(0,8).forEach((tool,i)=>{const y=17+i*6;if(y>h-4)return;x.strokeStyle=rgb(i);x.strokeRect(ox,y+.5,3,3);x.fillStyle=rgb(i+2);x.fillText(toolLabel(tool.name),ox+5,y,w*.73);x.fillStyle=rgb(i+5);x.fillText(String(tool.calls),ox+w-9,y,9);});
    if(!tools.length){x.fillStyle='#697a83';x.fillText('暂无工具记录',ox,18,w);}
  }else if(mode===3){
    x.fillStyle=rgb(2);x.fillText('任务编号编码',ox,10,w);
    const bytes=Array.from(session.id).map(c=>c.charCodeAt(0));
    const cols=16,rows=Math.max(1,Math.floor((h-20)/3.5)),cw=w/cols;
    for(let j=0;j<rows;j++)for(let i=0;i<cols;i++){const byte=bytes[Math.floor((j*cols+i)/8)%bytes.length],bit=(byte>>(i%8))&1;x.fillStyle=bit?rgb(j):'#252936';x.fillRect(ox+i*cw,18+j*3.5,Math.max(.4,cw-.7),2.5);}
  }else{
    x.fillStyle=rgb(1);x.fillText('上下文 '+(occupancy(session)*100).toFixed(1)+'%',ox,10,w);
    const n=24,cw=w/n;for(let i=0;i<n;i++){x.fillStyle=i/n<occupancy(session)?rgb(i):'#252936';x.fillRect(ox+i*cw,18,Math.max(.6,cw-.5),Math.max(4,h*.25));}
    x.font=`3.5px ${FONT}`;x.fillStyle=rgb(4);x.fillText(session.model,ox,Math.min(h-6,23+h*.25),w);
  }
  // Narrow index rail encodes the actual task identifier rather than random digits.
  x.font=`600 3px ${FONT}`;x.fillStyle=rgb(2);x.fillText(session.id.slice(-8),ox,Math.max(10,h-4),w);
}
function syncTaskScene(){
  const topology=sceneTopology();
  const signature=agtState.filter+'|'+agtState.selected+'|'+topology.nodes.map(n=>n.id+':'+n.status+':'+n.label).join(',')+'|'+topology.edges.map(e=>e.id+':'+e.status+':'+e.calls).join(',');
  if(signature!==taskSceneState.signature){taskSceneState.signature=signature;rebuildTaskGraph();}
  taskSceneState.ringBindings.clear();
  const stamps=new Map(sceneSessions().map(session=>[session.source+':'+session.id,JSON.stringify([
    session.id,session.title,session.model,session.status,session.inputTokens,session.outputTokens,session.cachedTokens,
    session.contextTokens,session.contextWindow,session.parentId,session.lastTool,session.lastAt,
    session.tools,session.usageSamples?.slice(-25).map(v=>v.output),agtState.selected===session.source+':'+session.id
  ])]));
  for(const slot of slots){
    const binding=bindSlot(slot),session=binding.session;
    const stamp=session?stamps.get(session.source+':'+session.id):'empty';
    if(slot.telemetryStamp!==stamp){slot.telemetryStamp=stamp;slot.dirty=true;}
  }
  const now=Date.now()/1000;
  for(const event of agtState.snapshot?.events||[]){
    const key=event.id||event.source+':'+event.session+':'+event.at+':'+event.message;
    if(taskSceneState.seenEvents.has(key))continue;taskSceneState.seenEvents.add(key);
    if(now-event.at<7&&taskSceneState.bursts.length<12)taskSceneState.bursts.push({sessionId:event.session,source:event.source,label:toolLabel(event.message),failed:event.status==='failed',life:0});
  }
  if(taskSceneState.seenEvents.size>2000)taskSceneState.seenEvents=new Set([...taskSceneState.seenEvents].slice(-1000));
  byId('sceneLegend').textContent=`光场为视觉动效 · 显示 ${taskSceneState.curves.length} / ${topology.edges.length} 条任务关联`;
}
function drawTaskBursts(dt){
  S.save();camT(S,DPR);S.translate(cx,cy);S.globalCompositeOperation='lighter';
  for(let i=taskSceneState.bursts.length-1;i>=0;i--){
    const b=taskSceneState.bursts[i];b.life+=dt;if(b.life>2.2){taskSceneState.bursts.splice(i,1);continue;}
    const matching=slots.filter(s=>{const v=taskSceneState.ringBindings.get(s)?.session;return v?.id===b.sessionId&&v?.source===b.source;});
    if(!matching.length)continue;
    matching.sort((a,c)=>Math.abs(normA(a.a+ringRot))-Math.abs(normA(c.a+ringRot)));
    const slot=matching[0],angle=slot.a+ringRot,fade=Math.pow(1-b.life/2.2,1.4),length=(RMAX-RO)*Math.min(1,b.life/.35);
    const cs=Math.cos(angle),sn=Math.sin(angle);S.strokeStyle=b.failed?`rgba(240,166,198,${fade})`:`rgba(232,255,247,${fade})`;S.lineWidth=1.1;
    S.beginPath();for(let k=-7;k<=7;k++){const a=angle+k*.0016;S.moveTo(Math.cos(a)*(RO+2),Math.sin(a)*(RO+2));S.lineTo(Math.cos(a)*(RO+length),Math.sin(a)*(RO+length));}S.stroke();
    S.fillStyle=b.failed?'#f0a6c6':'#d4fff0';S.font=`8px ${FONT}`;S.fillText(b.label,cs*(RO+18),sn*(RO+18));slot.flash=Math.max(slot.flash,fade*.7);
  }
  const focus=focusedSession();if(focus?.contextWindow){const span=thV*2*occupancy(focus);S.strokeStyle='#98e0cf';S.lineWidth=2;S.beginPath();S.arc(0,0,R-8,-thV,-thV+span);S.stroke();}
  S.restore();
}
function bindTaskSceneInteraction(){
  const canvas=byId('stage'),tip=byId('nodeTooltip');let hover=null;
  canvas.addEventListener('pointermove',e=>{
    const box=canvas.getBoundingClientRect(),px=e.clientX-box.left,py=e.clientY-box.top;
    const wx=(px-W/2)/cam.z+cam.x-cx,wy=(py-H/2)/cam.z+cam.y-cy;
    hover=taskSceneState.nodes.find(n=>Math.hypot(n.x-wx,n.y-wy)<10/cam.z)||null;
    if(!hover){tip.hidden=true;canvas.style.cursor='default';return;}
    tip.hidden=false;tip.style.left=Math.min(W-280,Math.max(8,px+12))+'px';tip.style.top=Math.max(8,Math.min(H-65,py+12))+'px';
    const kind={source:'来源',session:'任务',tool:'工具'}[hover.kind];tip.textContent=`${kind}：${hover.kind==='tool'?toolLabel(hover.label):hover.label}\n状态：${statusText[hover.status]||'已记录'}`;canvas.style.cursor=hover.sessionId?'pointer':'default';
  });
  canvas.addEventListener('pointerleave',()=>{hover=null;tip.hidden=true;});
  canvas.addEventListener('click',()=>{if(hover?.sessionId)selectTask(hover.source+':'+hover.sessionId);});
}
