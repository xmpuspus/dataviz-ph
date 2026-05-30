// Insight-led hero recorder for dataviz.ph. The point, not just the interaction:
//   1. DPWH spend vs poverty, autoplay the full span -> a shapeless scatter
//      (finding box on screen: rho +0.12, no clear link). Spend rises, the cloud
//      never tilts toward lower poverty.
//   2. Switch to GDP vs poverty -> the cloud snaps into a clean downward diagonal
//      (rho -0.525). Output predicts poverty; spending didn't.
//   3. Back to DPWH, switch to the Map -> where it landed, animated across years.
//   4. Back to bubbles, hold the full cloud as the loop point.
// Serve public/ on :8099, then `node docs/record_demo.js`, then convert the webm
// to a gif (fps=15, scale=900, two-pass palette, trim ~1.5s of load intro).
// Resolve Playwright from a global/local install, or any npx cache copy.
function loadPlaywright() {
  try { return require('playwright'); } catch (e) { /* fall through */ }
  const fsx = require('fs'), path = require('path'), os = require('os');
  const base = path.join(os.homedir(), '.npm/_npx');
  if (fsx.existsSync(base)) {
    for (const d of fsx.readdirSync(base)) {
      const p = path.join(base, d, 'node_modules/playwright');
      if (fsx.existsSync(p)) return require(p);
    }
  }
  throw new Error('playwright not found; run `npx playwright` once or `npm i -g playwright`');
}
const PW = loadPlaywright();
const fs = require('fs');
const OUT = process.env.DEMO_OUT || '/tmp/dataviz-demo-record';
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
const W = 1440, H = 1200;

(async () => {
  const browser = await PW.chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: W, height: H }, deviceScaleFactor: 2,
    recordVideo: { dir: OUT, size: { width: W, height: H } },
  });
  const page = await ctx.newPage();
  const year = () => page.evaluate(() => document.getElementById('year-display').textContent);
  const stillPlaying = () => page.locator('#big-play').evaluate((e) => e.classList.contains('playing'));

  await page.goto('http://localhost:8099/?cb=' + Date.now(), { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForSelector('#axis-pick-y', { timeout: 15000 });
  await page.waitForTimeout(700);

  // Beat 1: DPWH vs poverty, watch the cold-open autoplay; pause near the end.
  for (let i = 0; i < 90; i++) { if ((await year()) === '2022') break; await page.waitForTimeout(100); }
  if (await stillPlaying()) await page.locator('#big-play').click();
  await page.waitForTimeout(1300);

  // Beat 2: the contrast. GDP vs poverty -> a clear downward diagonal.
  await page.getByRole('button', { name: 'GDP vs poverty', exact: true }).click();
  await page.waitForTimeout(1500);
  await page.locator('#year-next').click();
  await page.waitForTimeout(1700);

  // Beat 3: back to DPWH, then the Map; animate the choropleth to the last year.
  await page.getByRole('button', { name: 'DPWH vs poverty', exact: true }).click();
  await page.waitForTimeout(800);
  await page.locator('.chart-type-btn[data-type=map]').click();
  for (let i = 0; i < 80; i++) {
    const ready = await page.evaluate(() => {
      const c = echarts.getInstanceByDom(document.getElementById('chart'));
      const o = c && c.getOption();
      return !!(o && o.series && o.series.some(s => s.type === 'map'));
    });
    if (ready) break;
    await page.waitForTimeout(100);
  }
  await page.waitForTimeout(900);
  await page.locator('#big-play').click();
  for (let i = 0; i < 60; i++) { if ((await year()) === '2024') break; await page.waitForTimeout(100); }
  await page.waitForTimeout(1200);

  // Beat 4: back to bubbles, hold a settled full cloud as the loop point.
  await page.locator('.chart-type-btn[data-type=bubbles]').click();
  await page.waitForTimeout(2400);

  await page.close();
  await ctx.close();
  await browser.close();
  const webms = fs.readdirSync(OUT).filter(f => f.endsWith('.webm')).map(f => OUT + '/' + f);
  console.log('recorded:', webms[webms.length - 1]);
})().catch(e => { console.error(e); process.exit(1); });
