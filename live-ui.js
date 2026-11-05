/* Local telemetry UI. Labels are Chinese; model/tool identifiers remain original. */
const byId=id=>document.getElementById(id);
const escapeHTML=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty=n=>Number(n||0).toLocaleString('zh-CN');
const compactValue=n=>n>=1e8?(n/1e8).toFixed(2)+'亿':n>=1e4?(n/1e4).toFixed(1)+'万':pretty(n);
const isActive=s=>s?.status==='running'||s?.status==='active'||s?.status==='in_progress';
const statusText={running:'执行中',active:'活动中',idle:'空闲',unknown:'待确认',offline:'未打开',completed:'完成',failed:'失败',pending:'等待中',in_progress:'执行中'};
const toolLabel=name=>({exec:'工具编排',exec_command:'终端执行',CommandExecution:'终端执行',McpToolCall:'外部工具',mcp_tool:'外部工具',web_search:'网页检索',apply_patch:'修改文件',wait:'等待结果',write_stdin:'进程交互'})[name]||String(name||'').replace(/^functions\./,'');
const agtState={snapshot:null,filter:'all',selected:null,activeOnly:false,includeSubagents:false,paused:false,failed:false,feedSignature:'',wallpaperPaused:false,fps:location.protocol==='file:'?30:60,focusMode:false};
let agtSettings={name:'AGT 作战指挥台',quiet:false,panelsCollapsed:false,quality:'balanced',panelView:'monitor'};
try{const stored=JSON.parse(localStorage.getItem('agt.screen.v3')||'null');if(stored)Object.assign(agtSettings,stored);}catch{}
if(!['balanced','high','low'].includes(agtSettings.quality))agtSettings.quality='balanced';
if(!['monitor','original'].includes(agtSettings.panelView))agtSettings.panelView='monitor';
let T=0;
function resizePanels(){}
function agentsTick(){}
function sourceSessions(){return(agtState.snapshot?.sessions||[]).filter(s=>agtState.filter==='all'||s.source===agtState.filter);}
function selectedSessions(){return sourceSessions().filter(s=>agtState.includeSubagents||!s.isSubagent).sort((a,b)=>Number(isActive(b))-Number(isActive(a))||Number(b.lastAt||0)-Number(a.lastAt||0));}
function focusedSession(){return selectedSessions().find(s=>s.source+':'+s.id===agtState.selected)||null;}
function occupancy(s){return s?.contextWindow?Math.max(0,Math.min(1,s.contextTokens/s.contextWindow)):0;}
function saveScreenSettings(){try{localStorage.setItem('agt.screen.v3',JSON.stringify(agtSettings));}catch{}}
function applyScreenSettings(){
  document.title=agtSettings.name;
  byId('stageWrap').classList.toggle('quiet',!!agtSettings.quiet);
  document.body.classList.toggle('panels-collapsed',!!agtSettings.panelsCollapsed);
  byId('togglePanels').textContent=agtSettings.panelsCollapsed?'展开信息栏 ▴':'收起信息栏 ▾';
  byId('togglePanels').setAttribute('aria-expanded',String(!agtSettings.panelsCollapsed));
  const safeBottom=wallpaperHost()?Math.max(48,Math.min(120,(screen.height-screen.availHeight)||48)):0;
  document.documentElement.style.setProperty('--safe-bottom',safeBottom+'px');
  const original=agtSettings.panelView==='original';
  byId('sourceCanvasCaption').textContent=original?'原作视觉演示，不代表任务数据':'来源连接 · 当前任务上下文';
  byId('sourceCanvasCaption').title=original?'原作效果为视觉演示，不代表任务数据':'来源在线状态与当前任务的实际上下文占用';
  byId('sourcePanelHeading').textContent=original?'原版场动效':'来源与上下文';
  byId('matrixPanelHeading').textContent=original?'原版扫描动效':'任务用量对比';
  const matrixText=original?'原作视觉演示 · ':'输入 / 输出 / 缓存以当前前六任务最大值作相对比较 · ';
  byId('matrixCaption').innerHTML=escapeHTML(original?matrixText:'相对用量对比 · ')+'<a href="https://github.com/Ryder191/haloflux" target="_blank" rel="noreferrer" title="原作 HALOFLUX · Ryder191">HALOFLUX ↗</a>';
  byId('matrixCaption').title=original?'原作效果为视觉演示，不代表任务数据':matrixText+'上下文按各任务的窗口占比显示';
  if(typeof window.agtSetQuality==='function')window.agtSetQuality(agtSettings.quality);
}
function toast(message){byId('toast').textContent=message;byId('toast').classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>byId('toast').classList.remove('show'),2000);}
function updateCallout(){const s=focusedSession();byId('chkK').textContent=s?`${s.source==='codex'?'Codex':'DSH'} · ${s.model}`:'真实任务关联';byId('chkV').textContent=s?`${statusText[s.status]} · 输出 ${compactValue(s.outputTokens)}`:'等待本机任务';}
function renderWorkspace(){
  const s=focusedSession();
  const rows=s?[
    ...(s.isSubagent?[['归属',s.parentTitle||'协作子任务','']]:[]),
    ['状态',(statusText[s.status]||'待确认')+' · '+(s.source==='codex'?'Codex':'DSH'),isActive(s)?'status-running':''],
    ['模型',s.model||'未提供',''],['当前工具',s.lastTool?toolLabel(s.lastTool):'暂无记录',''],
    ['上下文',s.contextWindow?(occupancy(s)*100).toFixed(1)+'%':'未提供','']
  ]:[['任务','等待软件启动',''],['来源','自动接入 Codex / DSH',''],['状态','本机监听中','status-running']];
  byId('code').innerHTML=(s?`<div class="focus-task-title" title="${escapeHTML(s.title)}">${escapeHTML(s.title||'未命名任务')}</div>`:'')+rows.map(r=>`<div class="code-line"><span class="key">${r[0]}</span><span class="value ${r[2]}" title="${escapeHTML(r[1])}">${escapeHTML(r[1])}</span></div>`).join('');
}
function renderFeed(){
  const events=(agtState.snapshot?.events||[]).filter(e=>agtState.filter==='all'||e.source===agtState.filter).slice(0,5).reverse();
  const sig=JSON.stringify(events);if(sig===agtState.feedSignature)return;agtState.feedSignature=sig;
  byId('feed').innerHTML=events.length?events.map(e=>`<div title="${escapeHTML(e.message)} · ${statusText[e.status]||'已记录'}"><span class="time">${new Date(e.at*1000).toLocaleTimeString('zh-CN',{hour12:false})}</span><span class="vb ${e.status==='failed'?'build':e.source==='dsh'?'build':'read'}">${e.source==='codex'?'Codex':'DSH'}</span>${escapeHTML(toolLabel(e.message))}${e.status==='failed'?' · 失败':''}</div>`).join(''):'<div><span class="vb read">监听</span>等待真实工具调用记录</div>';
}
function renderRoutes(){
  const sources=agtState.snapshot?.sources||[];
  byId('routes').innerHTML='<span class="h">来源</span><span class="h">连接</span><span class="h">执行中</span><span class="h">主任务 / 协作</span>'+sources.map(s=>`<span class="nm">${s.id==='codex'?'Codex':'DSH'}</span><span><span class="chip ${s.online?'on':''}">${s.online?'在线':'未打开'}</span></span><span><span class="chip ${s.running?'pk':''}">${s.running}</span></span><span><span class="chip ${s.online?'done':''}">${s.threads??s.sessions} / ${s.subagents||0}</span></span>`).join('')+`<div class="bridge-text">${agtState.failed?'连接中断，正在重试':agtState.snapshot?'本机同步 · '+new Date(agtState.snapshot.at*1000).toLocaleTimeString('zh-CN',{hour12:false}):'正在发现来源'}</div>`;
  byId('sourceNotes').textContent=sources.map(s=>s.note).filter(Boolean).join(' / ')||'自动接入与退出';
}
function renderAgents(){
  const sessions=selectedSessions();byId('dispatchHeading').textContent=(agtState.includeSubagents?'任务编队 · ':'主任务 · ')+sessions.length;
  const priority=s=>s.source+':'+s.id===agtState.selected?2:isActive(s)?1:0;
  const visible=[...sessions].sort((a,b)=>priority(b)-priority(a)).slice(0,5);
  byId('agents').innerHTML=visible.map(s=>{const key=s.source+':'+s.id,title=s.title||'未命名任务',index=sessions.indexOf(s)+1;return`<button type="button" class="ag ${isActive(s)?'live':''} ${key===agtState.selected?'selected':''}" data-session="${escapeHTML(key)}" title="${escapeHTML((s.source==='codex'?'Codex':'DSH')+' · '+title+' · '+(statusText[s.status]||'待确认'))}"><span class="id">${String(index).padStart(2,'0')}</span><span class="ag-name">${escapeHTML(title)}</span><span class="st">${statusText[s.status]||'待确认'}</span></button>`;}).join('')||'<span class="empty-task">暂无在线任务</span>';
  const children=sourceSessions().filter(s=>s.isSubagent).length;
  byId('dispatchCaption').textContent=`显示 ${visible.length} / ${sessions.length}`+(!agtState.includeSubagents&&children?` · ${children} 个协作子任务可在全部任务中查看`:' · 点击查看任务');
}
function requestRateDisplay(info){
  const valid=info&&typeof info.value==='number'&&Number.isFinite(info.value)&&info.value>0;
  const age=info?.sampleAt?Math.max(0,Date.now()/1000-info.sampleAt):null;
  const ageText=age===null?'':age<60?Math.floor(age)+'秒前':age<3600?Math.floor(age/60)+'分钟前':Math.floor(age/3600)+'小时前';
  const text=valid?(info.kind==='estimated_request_average'?'≈ ':'')+info.value.toFixed(1)+' tok/s':info?.status==='idle'?'空闲':info?.status==='waiting'?'等待采样':'暂不可用';
  const details=[info?.reason,info?.source?'来源 '+info.source:'',valid&&info?.windowMs?'窗口 '+(info.windowMs/1000).toFixed(1)+' 秒':'',info?.includesFirstTokenWait===true?'估算包含首字等待':'',ageText].filter(Boolean).join(' · ');
  return{text,meta:valid?ageText:'',title:details||'请求完成并写入用量后才能计算；当前没有可用样本'};
}
function renderRequestRate(info){
  const display=requestRateDisplay(info);byId('k4').textContent=display.text;byId('k4').title=display.title;byId('rateMeta').textContent=display.meta;byId('rateLabel').textContent='最近请求均速';
}
function renderTaskDialog(){
  const list=selectedSessions().filter(s=>!agtState.activeOnly||isActive(s));
  const children=sourceSessions().filter(s=>s.isSubagent).length;
  byId('taskCount').textContent=`${list.length} 个${agtState.includeSubagents?'任务':'主任务'}`+(!agtState.includeSubagents&&children?` · 另有 ${children} 个协作子任务`:'');
  byId('taskList').innerHTML=list.map(s=>{const key=s.source+':'+s.id;return`<button class="task-card ${s.source} ${key===agtState.selected?'selected':''}" data-session="${escapeHTML(key)}"><span class="task-top"><span>${s.source==='codex'?'Codex':'DSH'} · ${s.isSubagent?'协作子任务':'主任务'} · ${escapeHTML(s.id.slice(-6))}</span><span class="state-tag">${statusText[s.status]}</span></span><div class="task-title">${escapeHTML(s.title)}</div><div class="task-meta">${escapeHTML(s.model)} · 输出 ${pretty(s.outputTokens)}${s.isSubagent&&s.parentTitle?' · 归属 '+escapeHTML(s.parentTitle):''}</div></button>`;}).join('')||'<div class="empty-task">当前筛选下暂无任务</div>';
  const s=focusedSession();byId('focusDetails').innerHTML=s?`<div class="detail-title">${escapeHTML(s.title)}</div><div class="detail-data"><div><span>模型：</span>${escapeHTML(s.model)}</div><div><span>输入：</span>${pretty(s.inputTokens)}</div><div><span>输出：</span>${pretty(s.outputTokens)}</div><div><span>缓存：</span>${pretty(s.cachedTokens)}</div><div><span>上下文：</span>${s.contextWindow?(occupancy(s)*100).toFixed(1)+'%':'未提供'}</div><div><span>状态：</span>${statusText[s.status]}</div></div><div class="muted">${escapeHTML(s.cwd)}</div>`:'';
}
function renderSnapshot(){
  const snap=agtState.snapshot;if(!snap)return;const sessions=selectedSessions();
  if(!sessions.some(s=>s.source+':'+s.id===agtState.selected))agtState.selected=sessions.length?sessions[0].source+':'+sessions[0].id:null;
  const total=snap.totals||{};[total.online,total.running,total.threads??total.sessions,compactValue(total.outputTokens)].forEach((v,i)=>byId('k'+i).textContent=v??0);byId('k2').title=`${total.threads??total.sessions??0} 个主任务 · ${total.subagents||0} 个协作子任务`;renderRequestRate(total.outputRateInfo);
  const focus=focusedSession();byId('stBig').textContent=focus?focus.title:'等待 Codex / DSH 启动';
  const topology=snap.topology||{nodes:[],edges:[]};
  byId('stageSub').textContent=agtState.failed?'连接中断，保留最后记录':`真实节点 ${topology.nodes.length} · 已记录关系 ${topology.edges.length} · ${total.running||0} 个任务执行中`;
  renderWorkspace();renderFeed();renderRoutes();renderAgents();updateCallout();syncTaskScene();
  if(byId('tasksDialog').open)renderTaskDialog();
}
async function pollAGT(){
  try{const host=location.protocol==='file:'?window.agtWallpaperHost:null;const response=await fetch(host?host.api+'/api/state':'/api/state',{cache:'no-store',signal:AbortSignal.timeout(6500),headers:host?{'X-AGT-Token':host.token}:{}});if(!response.ok)throw Error('采集器未连接');const snap=await response.json();if(snap.discovering){byId('connection').innerHTML='<i></i>识别来源';return;}agtState.snapshot=snap;agtState.failed=Date.now()/1000-snap.at>15;byId('connection').classList.toggle('stale',agtState.failed);byId('connection').innerHTML=agtState.failed?'<i></i>更新停滞':'<i></i>实时连接';renderSnapshot();}
  catch{agtState.failed=true;byId('connection').classList.add('stale');byId('connection').innerHTML='<i></i>正在重连';byId('stageSub').textContent='采集器未连接，正在重试';renderRoutes();}
  finally{setTimeout(pollAGT,2000);}
}
function selectTask(key){agtState.selected=key;renderSnapshot();}
function wallpaperHost(){return !!window.agtWallpaperHost;}
function syncScreenControls(){
  const fullscreen=!!document.fullscreenElement;
  byId('motionButton').textContent=agtState.paused?'继续':'暂停';
  byId('motionButton').setAttribute('aria-pressed',String(agtState.paused));
  byId('motionButton').title=agtState.paused?'继续播放所有画面动效':'暂停画面动效，任务数据继续更新';
  document.body.classList.toggle('motion-paused',agtState.paused);
  const focus=wallpaperHost()&&agtState.focusMode;
  document.body.classList.toggle('focus-mode',focus);
  byId('fullscreenButton').textContent=wallpaperHost()?(focus?'退出专注':'专注'):(fullscreen?'退出全屏':'全屏');
  byId('fullscreenButton').setAttribute('aria-pressed',String(wallpaperHost()?focus:fullscreen));
  byId('fullscreenButton').title=wallpaperHost()?'切换专注画面，保留顶部操作按钮':'切换浏览器全屏（F）';
}
async function toggleScreenMode(){
  if(wallpaperHost()){
    agtState.focusMode=!agtState.focusMode;syncScreenControls();
    toast(agtState.focusMode?'已进入专注画面，再次点击可恢复信息栏':'已恢复信息栏');
    return;
  }
  try{
    if(document.fullscreenElement)await document.exitFullscreen();
    else if(document.fullscreenEnabled&&document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();
    else throw Error('fullscreen unavailable');
    syncScreenControls();
  }catch{toast('当前窗口不支持全屏，请使用浏览器全屏按钮');}
}
function applyWallpaperProperties(p){
  if(p.showpanels){agtSettings.panelsCollapsed=!p.showpanels.value;applyScreenSettings();}
  if(p.showheader)document.body.classList.toggle('header-hidden',!p.showheader.value);
  if(p.camera)setParam('camera',p.camera.value?1:0,true);
  if(p.brightness){agtSettings.quiet=false;applyScreenSettings();byId('stage').style.opacity=Math.max(.15,Math.min(1,p.brightness.value/100));}
  if(p.ringspeed)setParam('ringSpeed',Number(p.ringspeed.value),true);
}
function applyWallpaperGeneral(p){if(p.fps)agtState.fps=Math.max(5,Math.min(60,Number(p.fps)));}
window.wallpaperPropertyListener={applyUserProperties:p=>{if(window.agtWallpaperHost)window.agtWallpaperHost.native=true;applyWallpaperProperties(p);},applyGeneralProperties:p=>{if(window.agtWallpaperHost)window.agtWallpaperHost.native=true;applyWallpaperGeneral(p);},setPaused:paused=>{if(window.agtWallpaperHost)window.agtWallpaperHost.native=true;agtState.wallpaperPaused=!!paused;}};
window.addEventListener('message',e=>{
  if(parent===window||e.source!==parent||!['null',location.origin].includes(e.origin)||e.data?.type!=='agt-wallpaper')return;
  if(e.data.properties)applyWallpaperProperties(e.data.properties);
  if(e.data.general)applyWallpaperGeneral(e.data.general);
  if(typeof e.data.paused==='boolean')agtState.wallpaperPaused=e.data.paused;
});
function syncQualityControls(value=byId('qualityInput').value){
  const selected=['balanced','high','low'].includes(value)?value:'balanced';
  byId('qualityInput').value=selected;
  document.querySelectorAll('#qualityOptions [data-quality]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.quality===selected)));
}
function syncPanelViewControls(value=agtSettings.panelView){
  const selected=value==='original'?'original':'monitor';byId('panelViewInput').value=selected;
  document.querySelectorAll('#panelViewOptions [data-panel-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.panelView===selected)));
}
function agtBoot(){
  applyScreenSettings();
  if(window.agtWallpaperHost){const h=window.agtWallpaperHost;applyWallpaperProperties(h.properties);applyWallpaperGeneral(h.general);agtState.wallpaperPaused=h.paused;}
  byId('togglePanels').onclick=()=>{agtSettings.panelsCollapsed=!agtSettings.panelsCollapsed;saveScreenSettings();applyScreenSettings();};
  document.body.classList.toggle('wallpaper-host',wallpaperHost());
  const toolsToggle=byId('toolsToggle'),toolsMenu=byId('toolsMenu');
  const expandTools=open=>{toolsMenu.hidden=!open;toolsToggle.setAttribute('aria-expanded',String(open));toolsToggle.textContent=open?'菜单 ▴':'菜单 ▾';toolsToggle.title=open?'收起工作台操作':'展开工作台操作';};
  toolsToggle.onclick=()=>expandTools(toolsMenu.hidden);
  document.querySelector('.header-tools').addEventListener('click',e=>{if(e.target.closest('#tasksButton,#settingsButton,#consoleButton'))expandTools(false);});
  document.addEventListener('click',e=>{if(!e.target.closest('.header-tools'))expandTools(false);});
  byId('settingsButton').onclick=()=>{byId('nameInput').value=agtSettings.name;byId('quietInput').checked=agtSettings.quiet;byId('panelsInput').checked=!agtSettings.panelsCollapsed;syncQualityControls(agtSettings.quality);syncPanelViewControls();byId('settingsDialog').showModal();};
  byId('qualityOptions').onclick=e=>{const button=e.target.closest('[data-quality]');if(button)syncQualityControls(button.dataset.quality);};
  byId('panelViewOptions').onclick=e=>{const button=e.target.closest('[data-panel-view]');if(button)syncPanelViewControls(button.dataset.panelView);};
  byId('settingsDialog').addEventListener('close',()=>{if(byId('settingsDialog').returnValue!=='save')return;agtSettings={...agtSettings,name:byId('nameInput').value.trim()||'AGT 作战指挥台',quiet:byId('quietInput').checked,panelsCollapsed:!byId('panelsInput').checked,quality:byId('qualityInput').value,panelView:byId('panelViewInput').value};saveScreenSettings();applyScreenSettings();toast('设置已保存');});
  byId('motionButton').onclick=()=>{agtState.paused=!agtState.paused;syncScreenControls();toast(agtState.paused?'动效已暂停，任务数据继续更新':'动效已继续');};
  toggleFS=toggleScreenMode;byId('fullscreenButton').onclick=toggleScreenMode;
  document.addEventListener('fullscreenchange',syncScreenControls);syncScreenControls();
  const showTasks=()=>{renderTaskDialog();byId('tasksDialog').showModal();};byId('tasksButton').onclick=showTasks;byId('allTasksButton').onclick=showTasks;byId('workspaceDetailsButton').onclick=showTasks;byId('closeTasks').onclick=()=>byId('tasksDialog').close();
  byId('activeOnly').onchange=e=>{agtState.activeOnly=e.target.checked;renderTaskDialog();};
  byId('includeSubagents').onchange=e=>{agtState.includeSubagents=e.target.checked;renderSnapshot();};
  byId('taskFilters').onclick=e=>{const b=e.target.closest('[data-filter]');if(!b)return;agtState.filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(el=>el.classList.toggle('on',el===b));renderSnapshot();};
  for(const id of ['agents','taskList'])byId(id).onclick=e=>{const b=e.target.closest('[data-session]');if(b)selectTask(b.dataset.session);};
  const consoleButton=document.querySelector('.console');
  const syncConsole=()=>{const open=panel.classList.contains('open');consoleButton.setAttribute('aria-expanded',String(open));consoleButton.textContent=open?'收起调参':'调参';consoleButton.title=open?'关闭画面参数（C）':'打开画面参数（C）';};
  new MutationObserver(syncConsole).observe(panel,{attributes:true,attributeFilter:['class']});syncConsole();
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){if(!toolsMenu.hidden){expandTools(false);toolsToggle.focus();}panel.classList.remove('open');if(agtState.focusMode){agtState.focusMode=false;syncScreenControls();}}});
  document.querySelectorAll('dialog,.header-tools,#taskFilters,#agents,#allTasksButton,#togglePanels').forEach(el=>el.addEventListener('dblclick',e=>e.stopPropagation()));
  bindTaskSceneInteraction();renderWorkspace();renderFeed();renderRoutes();renderAgents();pollAGT();
  if(typeof bindFlowControls==='function')bindFlowControls();
  if(parent!==window)parent.postMessage({type:'agt-ready'},'*');
}
