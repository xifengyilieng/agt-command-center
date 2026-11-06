// Exercise the production scheduler against deterministic host timestamps.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../render-performance.js'), 'utf8');

function harness(quality = 'balanced') {
  let now = 1;
  const context = vm.createContext({
    performance: {now: () => now},
    window: {},
    document: {hidden: false},
    agtSettings: {quality},
    agtState: {fps: 60, paused: false, wallpaperPaused: false},
    nextFrameAt: 0,
    spawnComet() {},
    spawnFlyBurst() {},
    drawStage() {},
    requestAnimationFrame() {},
  });
  const run = code => vm.runInContext(code, context);
  run(source);
  run('agtAdaptive.ignoreUntil=0');
  return {
    run,
    tick(delta) {
      now += delta;
      return run(`agtCanRenderFrame(${now},1000/agtFrameRate())`);
    },
    measureWindow(workMs, fps) {
      if (!run('agtAdaptive.startedAt')) run(`agtMeasureWork(${workMs})`);
      const end = run('agtAdaptive.startedAt') + 2000;
      while (now < end) {
        now = Math.min(end, now + 1000 / fps);
        run(`agtMeasureWork(${workMs})`);
      }
    },
  };
}

function cadence(deltas, quality) {
  const test = harness(quality);
  for (let i = 0; i < 24; i++) test.tick(deltas[i % deltas.length]);
  const before = test.run('window.agtRenderStats.limiterSkips');
  let drawn = 0;
  let elapsed = 0;
  for (let i = 0; i < 1440; i++) {
    const delta = deltas[i % deltas.length];
    elapsed += delta;
    if (test.tick(delta)) drawn++;
  }
  return {
    fps: drawn * 1000 / elapsed,
    raw: test.run('window.agtRenderStats.rawRafFps'),
    skipped: test.run('window.agtRenderStats.limiterSkips') - before,
  };
}

const jittered = cadence([15.3, 1000 / 30 - 15.3]);
assert.equal(jittered.skipped, 0, 'a jittered 60 Hz host already paces frames');
assert.ok(Math.abs(jittered.fps - 60) < .1);
assert.ok(Math.abs(jittered.raw - 60) < .2);

const fast = cadence([1000 / 144]);
assert.ok(Math.abs(fast.fps - 60) < .2, '144 Hz still obeys the 60 FPS limit');
assert.ok(Math.abs(fast.raw - 144) < .2);
assert.ok(fast.skipped > 800);

const low = cadence([1000 / 60], 'low');
assert.ok(Math.abs(low.fps - 30) < .1, 'explicit low preset still caps at 30 FPS');
assert.ok(Math.abs(low.raw - 60) < .2);
assert.equal(low.skipped, 720);

function countAfterSwitch(initialHz, quality) {
  const test = harness(quality);
  for (let i = 0; i < 24; i++) test.tick(1000 / initialHz);
  let drawn = 0;
  for (let i = 0; i < 24; i++) if (test.tick(1000 / 120)) drawn++;
  return drawn;
}
assert.equal(countAfterSwitch(30, 'balanced'), 12, '30→120 Hz must obey the 60 FPS cap immediately');
assert.equal(countAfterSwitch(15, 'low'), 6, '15→120 Hz must obey the explicit 30 FPS cap immediately');

const effects = harness();
effects.measureWindow(3, 48);
effects.measureWindow(3, 48);
assert.equal(effects.run('agtAdaptive.level'), 1, 'slow cadence can reduce effect density');
assert.equal(effects.run('agtFrameRate()'), 60);

const pressure = harness();
pressure.run('agtAdaptive.level=2');
for (const fps of [51, 39, 45, 32, 51, 38]) pressure.measureWindow(3, fps);
assert.equal(pressure.run('agtFrameRate()'), 60, 'slow callbacks with cheap draw work cannot force 30 FPS');
for (let i = 0; i < 3; i++) pressure.measureWindow(18, 50);
assert.equal(pressure.run('agtFrameRate()'), 60);
pressure.measureWindow(3, 50);
for (let i = 0; i < 3; i++) pressure.measureWindow(18, 50);
assert.equal(pressure.run('agtFrameRate()'), 60, 'a cheap window resets CPU pressure');
pressure.measureWindow(18, 50);
assert.equal(pressure.run('agtFrameRate()'), 30, 'four consecutive over-budget windows use the CPU fallback');

console.log(JSON.stringify({ok: true, jittered, fast, low, sustainedCpuFallback: true}));
