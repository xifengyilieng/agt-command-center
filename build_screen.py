"""Build a compact Chinese screen with task-bound ring content and topology."""
from pathlib import Path
import re
import json
import os

ROOT = Path(__file__).resolve().parent
upstream = (ROOT/'vendor/haloflux/lab.html').read_text(encoding='utf-8')
head, script = upstream.split('<script>',1)
script = script.split('</script>',1)[0]
start = script.index('/* =========================================================\n   HEADER KPIs')
end = script.index('/* =========================================================\n   LOOP',start)
prefix,suffix = script[:start],script[end:]
prefix = re.sub(r'setInterval\(\(\) => \{\n  chkEl\.style\.opacity = 0;.*?\}, 3600\);','',prefix,flags=re.S)
# Geometry, ring rims, layered outer glow and camera remain the upstream engine.
# Restore the original woven field, beat bursts and ring impacts as visual art.
# The sparse real-task overlay is separate, with one curve per recorded edge.
a=prefix.index('  // runners: speed surges on the beat')
b=prefix.index('  S.restore();\n  camT(S, DPR);',a)
prefix=prefix[:a]+'  drawArtRunners(dt);\n'+prefix[b:]
a=prefix.index('  S.drawImage(webBase, -R - 2, -R - 2, ws, ws);')
b=prefix.index('  drawArtRunners(dt);',a)
prefix=prefix[:a]+'  drawArtField();\n'+prefix[b:]
prefix=prefix.replace('  // ring occlusion:', '  drawTaskGraph(dt, G);\n  // ring occlusion:')
prefix=prefix.replace("    if (rnd() < 0.35) { b.fillStyle = 'rgba(200,204,212,.5)'; b.fillText('n' + ((rnd() * 99) | 0), px + 7, py); }",'')
prefix=prefix.replace('const N = Math.min(2600, Math.round(1000 + (W * H) / 800));','const N = agtQuality.web;')
prefix=prefix.replace("for (let i = 0; i < Math.round(6 * C.dataFlicker); i++) pick(slots).c = null;",'// Telemetry cards refresh incrementally when their actual values change.')
prefix=prefix.replace('let regen = 1;','let regen = agtQuality.cards;')
prefix=prefix.replace("if (!s.c || (regen > 0 && rnd() < 0.02)) { renderSlot(s); regen--; }", "if ((!s.c || s.dirty) && regen > 0) { renderSlot(s); s.dirty=false; regen--; }\n    if (!s.c) continue;")
prefix=prefix.replace('Math.min(DPR, 1.5)','Math.min(DPR, agtQuality.trail)')
prefix=prefix.replace('  runners = []; for (let i = 0; i < C.runnerCount; i++) runners.push(newRunner(true));','  runners = [];')
a=prefix.index("  if (HAS_FILTER) S.filter = 'blur(6px)';")
b=prefix.index("  S.globalAlpha = 1; S.globalCompositeOperation = 'source-over';",a)
prefix=prefix[:a]+'  compositeGlow();\n'+prefix[b:]
prefix=prefix.replace("const FONT = '\"JetBrains Mono\",Consolas,monospace';", "const FONT = '\"JetBrains Mono\",\"Microsoft YaHei\",Consolas,monospace';")
prefix=prefix.replace('  drawRing(dt);','  drawRing(dt);\n  drawTaskBursts(dt);')
prefix=prefix.replace('function resizeStage() {','function resizeStage() {\n  agtReleaseStageBuffers();')
prefix=prefix.replace('function buildCards() {','function buildCards() {\n  releaseTaskCards();')
suffix=suffix.replace('let last = performance.now();','let last = performance.now(), nextFrameAt=0, lastPanelAt=0;')
suffix=suffix.replace('  const dt = Math.min(0.05, (now - last) / 1000); last = now; T += dt;',
  '  if (document.hidden || agtState.paused || agtState.wallpaperPaused) { last=now; nextFrameAt=0; lastPanelAt=now; requestAnimationFrame(frame); return; }\n'
  '  const interval=1000/agtState.fps;\n'
  '  if (now+.75<nextFrameAt) { requestAnimationFrame(frame); return; }\n'
  '  nextFrameAt=Math.max(nextFrameAt+interval,now);\n'
  '  const dt = Math.min(.1, Math.max(0,(now-last)/1000)); last=now; T+=dt;agtNoteFrame(now);')
suffix=suffix.replace('  drawAdv(dt); drawDiff(); drawVal(); drawIdx(); agentsTick(dt);',
  '  drawAdv(dt);\n  if(agtSettings.panelView===\'original\'||now-lastPanelAt>=1000/20){lastPanelAt=now;drawDiff();drawIdx();}')
suffix=suffix.replace('DPR = Math.min(window.devicePixelRatio || 1, 2); resizeStage();','DPR = agtPixelRatio(r.width,r.height); resizeStage();')
suffix=suffix.replace('resizePanels(); ready = true;', 'resizePanels(); ready = true; if(agtState.paused||agtState.wallpaperPaused)agtPaintPausedFrame();')
suffix=suffix.replace('slots.forEach(s => s.c = null)', 'slots.forEach(s => s.dirty = true)')
suffix=suffix.replace("'paperlab.cfg.v2'","'agt.haloflux.live.cfg.v3'")
suffix=suffix[:suffix.index('/* =========================================================\n   BGM')]
suffix=suffix.replace("e.target.matches('input,textarea')", "e.target.matches('input,textarea,select') || e.ctrlKey || e.metaKey || e.altKey")
# All upstream visual parameters are functional again, including comet controls.
for old,new in [('CONSOLE','画面参数')]:
 suffix=suffix.replace(old,new)
head=head.split('<body>',1)[0]
head=re.sub(r'^\.music[^\n]*\n|^@keyframes eqb[^\n]*\n','',head,flags=re.M)
head=re.sub(r'<link rel="preconnect"[^>]*>\n','',head)
head=re.sub(r'<link href="https://fonts.googleapis.com/[^>]*>\n','',head)
head=head.replace('<html lang="en">','<html lang="zh-CN">').replace('<title>Paper Lab Console</title>','<title>AGT 作战指挥台</title>')
head=head.replace('</head>','<link rel="stylesheet" href="/screen.css?v=4">\n</head>')
skills=[]
for skill_file in sorted((Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex').expanduser()/'skills').glob('*/SKILL.md')):
 try:
  meta=skill_file.read_text(encoding='utf-8')[:2500]
  match=re.search(r'^name:\s*[\"\']?([^\n\"\']+)',meta,re.M)
  skills.append({'name':match.group(1).strip() if match else skill_file.parent.name,'id':skill_file.parent.name})
 except (OSError,UnicodeError):pass
inventory='const AVAILABLE_SKILLS='+json.dumps(skills,ensure_ascii=False)+';\n'
runtime=prefix+'\n'+inventory+(ROOT/'live-ui.js').read_text(encoding='utf-8')+'\n'+(ROOT/'task-scene.js').read_text(encoding='utf-8')+'\n'+(ROOT/'original-panel-effects.js').read_text(encoding='utf-8')+'\n'+(ROOT/'panel-visuals.js').read_text(encoding='utf-8')+'\n'+(ROOT/'render-performance.js').read_text(encoding='utf-8')+'\n'+(ROOT/'desktop-controls.js').read_text(encoding='utf-8')+'\n'+suffix+'\nagtBoot();agtStartDesktopControls();\n'
(ROOT/'runtime.js').write_text(runtime,encoding='utf-8')
(ROOT/'index.html').write_text(head+(ROOT/'screen.html').read_text(encoding='utf-8'),encoding='utf-8')
print('Built AGT: upstream comet impacts, cached woven light field, sparse observed links, six panels, '+str(len(skills))+' available skills.')
