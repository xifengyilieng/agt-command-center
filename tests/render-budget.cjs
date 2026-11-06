// Isolated buffer-budget and resize regression; never starts the task collector.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const empty = {at:Date.now()/1000,sources:[],sessions:[],events:[],topology:{nodes:[],edges:[]},totals:{online:0,running:0,threads:0,subagents:0,outputTokens:0}};

async function readBudget(page) {
  return page.evaluate(() => {
    const buffers=[stage,agtFxCanvas,webBase,...webLayers,trailC,farC,glowC,laneC,agtArtComposite,agtGlowBlur,flowGrid,cAdv,cDiff,cIdx,...slots.map(s=>s.c),...agtCardCanvasPool].filter(Boolean);
    const owned=new Set(buffers);
    const pixels=canvas=>canvas.width*canvas.height;
    return {
      quality:agtSettings.quality,targetFps:agtFrameRate(),stagePixels:pixels(stage),
      sharpRatio:stage.width/W,uiRatio:agtUiPixelRatio(),effectsPixels:pixels(agtFxCanvas),
      panelRatios:[cAdv,cDiff,cIdx].filter(c=>c.w>0).map(c=>c.width/c.w),
      surfaceMax:Math.max(...[webBase,...webLayers,trailC,laneC,agtArtComposite,agtGlowBlur].filter(Boolean).map(pixels)),
      cachedCards:slots.filter(s=>s.c).length,cardScales:slots.filter(s=>s.c).map(s=>s.cacheDpr),
      poolSize:agtCardCanvasPool.length,
      cardPixels:slots.reduce((sum,s)=>sum+(s.c?pixels(s.c):0),0)+agtCardCanvasPool.reduce((sum,c)=>sum+pixels(c),0),
      totalPixels:buffers.reduce((sum,c)=>sum+pixels(c),0),
      limits:{effects:agtQuality.pixels,surface:agtQuality.surfacePixels,cards:agtQuality.cardPixels,cache:agtQuality.cardCache},
      retired:window.__retiredCanvases.length,
      unreleased:window.__retiredCanvases.filter(c=>!owned.has(c)&&(c.width!==1||c.height!==1)).length
    };
  });
}
function checkBudget(report) {
  assert.ok(report.sharpRatio>=report.uiRatio-.01,'information stage must keep native text resolution');
  assert.ok(report.panelRatios.every(r=>r>=report.uiRatio-.02),'panel text resolution must be independent of effects budget');
  assert.ok(report.effectsPixels<=report.limits.effects*1.02,JSON.stringify(report));
  assert.ok(report.surfaceMax<=report.limits.surface*1.02,JSON.stringify(report));
  assert.ok(report.cardPixels<=report.limits.cards*1.08,JSON.stringify(report));
  assert.ok(report.cachedCards<=report.limits.cache,JSON.stringify(report));
  assert.ok(report.poolSize<=24,JSON.stringify(report));
  assert.ok(report.cardScales.every(r=>r>=2.4),'visible ring text must keep the sharp raster scale');
  assert.equal(report.unreleased,0,'retired canvas backing stores must be released');
}

(async()=>{
  const options={headless:true};
  if(process.env.AGT_BROWSER_CHANNEL)options.channel=process.env.AGT_BROWSER_CHANNEL;
  else if(process.platform==='win32')options.channel='msedge';
  const browser=await chromium.launch(options);
  try{
    const page=await browser.newPage({viewport:{width:2560,height:1080}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(()=>{
      // Exercise migration of an existing installation without changing its name.
      localStorage.setItem('agt.screen.v3',JSON.stringify({quality:'high',name:'资源回归'}));
      window.__retiredCanvases=[];
      const random = Math.random;
      let layoutSeed = null;
      window.__withCardSeed = (seed, build) => {
        layoutSeed = seed;
        try { build(); } finally { layoutSeed = null; }
      };
      Math.random = () => {
        if (layoutSeed === null) return random();
        layoutSeed = (Math.imul(layoutSeed, 1664525) + 1013904223) >>> 0;
        return layoutSeed / 4294967296;
      };
    });
    await page.route('http://agt.test/**',async route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/api/state'){await route.fulfill({json:empty});return;}
      const file=path.resolve(root,'.'+decodeURIComponent(url.pathname==='/'?'/index.html':url.pathname));
      if(!file.startsWith(root+path.sep)){await route.abort();return;}
      try{
        const contentType=file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':file.endsWith('.woff2')?'font/woff2':'application/octet-stream';
        await route.fulfill({contentType,body:await fs.readFile(file)});
      }catch{await route.fulfill({status:404,body:''});}
    });
    await page.goto('http://agt.test/');
    await page.waitForFunction(()=>typeof ready!=='undefined'&&ready&&agtState.snapshot&&agtArtComposite);
    await page.waitForTimeout(400);
    const baseline=await readBudget(page);checkBudget(baseline);
    assert.equal(baseline.quality,'balanced');assert.equal(baseline.targetFps,60);
    assert.equal(await page.title(),'资源回归');
    for(let i=0;i<9;i++){
      await page.evaluate(()=>{
        window.__retiredCanvases.push(...[agtFxCanvas,webBase,...webLayers,trailC,farC,glowC,laneC,agtArtComposite,agtGlowBlur,flowGrid,...slots.map(s=>s.c)].filter(Boolean));
      });
      const size=[{width:3440,height:1440},{width:7680,height:2160},{width:1920,height:1080}][i%3];
      await page.setViewportSize(size);
      await page.waitForTimeout(350);
      await page.waitForFunction(()=>ready&&agtArtComposite);
      checkBudget(await readBudget(page));
    }
    const after=await readBudget(page);
    const rotations = await page.evaluate(() => {
      const camera = C.camera;
      const budget = agtQuality.cards;
      const reports = [];
      try {
        C.camera = 0;
        // These two layouts continued growing height buckets on their third turn.
        // Give both five warm-up turns before checking the steady backing cost.
        for (const seed of [3, 8]) {
          window.__withCardSeed(seed, buildCards);
          agtQuality.cards = slots.length;
          rot.t = rot.dur;
          rot.hold = 10;
          const seen = new Set();
          const backingResizesByTurn = [];
          let maxCached = 0, peakPixels = 0, evicted = 0;
          for (let turn = 0; turn < 6; turn++) {
            const initialResizes = agtCardBackingResizes;
            for (let step = 0; step < 24; step++) {
              const old = slots.map(slot => [slot, slot.c]).filter(([, canvas]) => canvas);
              ringRot = step * TAU / 24;
              drawStage(0);
              const cached = slots.filter(slot => slot.c);
              cached.forEach(slot => seen.add(slot));
              const owned = new Set([...cached.map(slot => slot.c), ...agtCardCanvasPool]);
              for (const [slot, canvas] of old) {
                if (slot.c === canvas) continue;
                if (!owned.has(canvas) && (canvas.width !== 1 || canvas.height !== 1)) {
                  throw Error('unowned card backing store retained');
                }
                evicted++;
              }
              maxCached = Math.max(maxCached, cached.length);
              const pixels = [...owned].reduce((sum, canvas) => sum + canvas.width * canvas.height, 0);
              peakPixels = Math.max(peakPixels, pixels);
            }
            backingResizesByTurn.push(agtCardBackingResizes - initialResizes);
          }
          reports.push({seed, seen: seen.size, slots: slots.length, maxCached, evicted, peakPixels,
            cacheLimit: agtQuality.cardCache, pixelLimit: agtQuality.cardPixels, backingResizesByTurn});
        }
      } finally {
        C.camera = camera;
        agtQuality.cards = budget;
      }
      return reports;
    });
    for (const rotation of rotations) {
      const detail = JSON.stringify(rotation);
      assert.ok(rotation.seen >= rotation.slots * .85, detail);
      assert.ok(rotation.maxCached <= rotation.cacheLimit, detail);
      assert.ok(rotation.peakPixels <= rotation.pixelLimit, detail);
      assert.ok(rotation.evicted > 0, 'rotating through the full ring must evict invisible cards');
      assert.ok(rotation.backingResizesByTurn.at(-1) <= 4, `warmed rotation must reuse card backing stores: ${detail}`);
    }
    checkBudget(await readBudget(page));
    assert.equal(await page.evaluate(()=>{
      const cards=[...slots.map(s=>s.c),...agtCardCanvasPool].filter(Boolean);releaseTaskCards();
      const released=cards.every(c=>c.width===1&&c.height===1);resizeAll();return released;
    }),true,'explicit layout teardown must release cached and pooled backings');
    // A user can still explicitly opt into a higher preset after migration.
    await page.evaluate(()=>{agtSettings.quality='high';saveScreenSettings();applyScreenSettings();});
    await page.waitForTimeout(350);checkBudget(await readBudget(page));
    assert.equal(await page.evaluate(()=>agtFrameRate()),60);
    await page.evaluate(()=>{agtState.wallpaperPaused=true;});
    await page.waitForTimeout(350);
    const stopped=await page.evaluate(()=>T);await page.waitForTimeout(500);
    assert.equal(await page.evaluate(()=>T),stopped);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,baseline,after,resizes:9,rotations,hostPause:true,errors}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
