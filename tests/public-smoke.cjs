// Portable clean-install UI check; no local task data or wallpaper installation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const base = process.env.AGT_TEST_URL || 'http://127.0.0.1:18767';
const empty = { at: Date.now()/1000, sources: [], sessions: [], events: [],
  topology: {nodes: [], edges: []},
  totals: {online: 0, running: 0, sessions: 0, threads: 0, subagents: 0, outputTokens: 0, outputRate: 0} };

(async () => {
  const options = { headless: true };
  if (process.env.AGT_BROWSER_CHANNEL) options.channel = process.env.AGT_BROWSER_CHANNEL;
  const browser = await chromium.launch(options);
  try {
    const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
    const errors = [], media = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.resourceType() === 'media') media.push(request.url()); });
    await page.route('**/api/state', route => route.fulfill({contentType: 'application/json', body: JSON.stringify(empty)}));
    const response = await page.goto(base, {waitUntil: 'domcontentloaded'});
    assert.equal(response.status(), 200);
    await page.waitForFunction(() => typeof agtState !== 'undefined' && agtState.snapshot);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(500);
    assert.equal(await page.locator('.panels>.p').count(), 6);
    assert.equal(await page.locator('.panels canvas').count(), 3);
    assert.equal(await page.evaluate(() => agtState.snapshot.sessions.length), 0);
    assert.equal(await page.evaluate(() => taskSceneState.curves.length), 0);
    assert.equal(await page.locator('#toolsMenu').isVisible(), false);
    assert.equal(await page.locator('audio').count(), 0);
    if (process.env.AGT_PREVIEW_PATH) {
      const destination = path.resolve(process.env.AGT_PREVIEW_PATH);
      fs.mkdirSync(path.dirname(destination), {recursive: true});
      await page.screenshot({path: destination, fullPage: true});
    }
    await page.locator('#toolsToggle').click();
    await page.locator('#settingsButton').click();
    assert.equal(await page.locator('#settingsDialog').evaluate(el => el.open), true);
    await page.locator('#saveSettings').click();
    await page.locator('#toolsToggle').click();
    await page.locator('#motionButton').click();
    assert.equal(await page.evaluate(() => agtState.paused), true);
    const before = await page.evaluate(() => T);
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => T), before);
    await page.locator('#motionButton').click();
    assert.equal(await page.evaluate(() => agtState.paused), false);
    if (await page.locator('#toolsMenu').isVisible()) await page.locator('#toolsToggle').click();
    await page.locator('#togglePanels').click();
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => document.body.classList.contains('panels-collapsed'));
    await page.locator('#togglePanels').click();
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    assert.deepEqual(media, []);
    console.log(JSON.stringify({ok:true, emptyState:true, menu:true, settings:true, pause:true, collapsePersists:true, mobile:true, errors}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
