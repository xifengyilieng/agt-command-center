/* DeskGo compatibility: authenticated, bounded controls only; no native window messages. */
function agtStartDesktopControls(){
  if(!window.agtWallpaperHost)return;
  if(window.agtDesktopControlStarted)return;
  if(!window.agtWallpaperHost.native){if(!window.agtDesktopControlWaiter)window.agtDesktopControlWaiter=setInterval(()=>{if(window.agtWallpaperHost.native){clearInterval(window.agtDesktopControlWaiter);window.agtDesktopControlWaiter=null;agtStartDesktopControls();}},500);return;}
  window.agtDesktopControlStarted=true;
  const host=window.agtWallpaperHost,clientId=crypto.randomUUID(),keys=new WeakMap(),targets=new Map();let serial=0,busy=false,publishing=false,acknowledged=0;
  const headers={'X-AGT-Token':host.token,'Content-Type':'application/json'};
  function regions(){
    targets.clear();const modal=document.querySelector('dialog[open]'),scope=modal||document;
    return [...scope.querySelectorAll('button,a,input[type=range],input[type=checkbox]')].filter(el=>{
      const r=el.getBoundingClientRect();return !el.disabled&&r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';
    }).slice(0,200).map(el=>{
      if(!keys.has(el))keys.set(el,'control-'+(++serial));const key=keys.get(el),r=el.getBoundingClientRect();targets.set(key,el);
      return{key,x:r.x,y:r.y,width:r.width,height:r.height,kind:el.type==='range'?'range':'click'};
    });
  }
  async function publish(){
    if(publishing)return;publishing=true;
    try{await fetch(host.api+'/api/ui',{method:'POST',headers,signal:AbortSignal.timeout(2000),body:JSON.stringify({clientId,
      screen:{x:screen.availLeft||0,y:screen.availTop||0,width:screen.width,height:screen.height},
      viewport:{width:innerWidth,height:innerHeight},regions:regions(),acknowledged,hostPaused:!!agtState.wallpaperPaused,pageHidden:document.hidden,
      renderStats:window.agtRenderStats||{}})});}catch{}finally{publishing=false;}
  }
  async function receive(){
    if(busy||document.hidden||agtState.wallpaperPaused)return;busy=true;
    try{
      const response=await fetch(host.api+'/api/ui?clientId='+encodeURIComponent(clientId),{headers:{'X-AGT-Token':host.token},signal:AbortSignal.timeout(2000)});
      if(response.ok)for(const cmd of (await response.json()).commands||[]){
        const el=targets.get(cmd.key);if(!el?.isConnected||el.disabled)continue;
        if(cmd.type==='click')el.click();
        if(cmd.type==='input'&&el.type==='range'){
          const min=Number(el.min||0),max=Number(el.max||100),step=Number(el.step)||1;
          el.value=String(Math.max(min,Math.min(max,min+Math.round((max-min)*cmd.ratio/step)*step)));el.dispatchEvent(new Event('input',{bubbles:true}));
        }
        acknowledged++;
      }
    }catch{}finally{busy=false;}
  }
  publish();setInterval(publish,2000);setInterval(receive,100);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){publish();receive();}});
  document.addEventListener('click',()=>setTimeout(publish,0),true);
}
