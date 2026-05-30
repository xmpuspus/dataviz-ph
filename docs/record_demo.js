// Narrative hero recorder for dataviz.ph: question -> one story -> the map -> loop.
// Serve public/ on :8099 first, then `node docs/record_demo.js`, then convert the
// webm to a gif (fps=15, scale=900, two-pass palette, trim ~1.5s of load intro).
// Beats:
//   1. Cold open: autoplay the DPWH-vs-poverty bubbles, then pause.
//   2. Search "Sulu", pin it -> trail + end-label + CI whisker, hover its tooltip.
//   3. Switch to the Map, play the choropleth animating across years.
//   4. Back to bubbles, hold a settled full cloud (Sulu pinned) as the loop point.
//
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
    viewport: { width: W, height: H },
    deviceScaleFactor: 2,
    recordVideo: { dir: OUT, size: { width: W, height: H } },
  });
  const page = await ctx.newPage();
  const year = () => page.evaluate(() => document.getElementById('year-display').textContent);

  await page.goto('http://localhost:8099/?cb=' + Date.now(), { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForSelector('#axis-pick-y', { timeout: 15000 });
  await page.waitForTimeout(700);

  // Beat 1: let the cold-open autoplay run, then pause for the story.
  await page.waitForTimeout(4200);
  if (await page.locator('#big-play').evaluate((e) => e.classList.contains('playing'))) {
    await page.locator('#big-play').click();
  }
  await page.waitForTimeout(700);

  // Beat 2: pin Sulu via search -> trail + end-label + CI whisker. Hold it in
  // bubbles so the pinned story reads, then hover its tooltip (value + 95% CI).
  await page.locator('#search').click();
  await page.locator('#search').fill('Sulu');
  await page.waitForTimeout(600);
  await page.locator('#search-results li').first().click();
  await page.waitForTimeout(2000);
  const sulu = await page.evaluate(() => {
    const chart = echarts.getInstanceByDom(document.getElementById('chart'));
    const opt = chart.getOption();
    const sIdx = opt.series.findIndex(s => s.type === 'scatter' && s.id !== 'compare' && Array.isArray(s.data) && s.data.length > 5);
    if (sIdx < 0) return null;
    const item = opt.series[sIdx].data.find(d => d && d.value && d.value[3] === 'Sulu');
    if (!item) return null;
    const px = chart.convertToPixel({ seriesIndex: sIdx }, [item.value[0], item.value[1]]);
    const rect = document.getElementById('chart').getBoundingClientRect();
    return { x: rect.left + px[0], y: rect.top + px[1] };
  });
  if (sulu) { await page.mouse.move(sulu.x, sulu.y, { steps: 16 }); await page.waitForTimeout(2200); }
  else { await page.waitForTimeout(2000); }
  await page.mouse.move(60, 60, { steps: 8 });
  await page.waitForTimeout(1500);

  // Beat 3: the Map. Wait for the province geo to render, then animate years.
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
  await page.waitForTimeout(1200);
  await page.locator('#big-play').click(); // animate the choropleth across years
  for (let i = 0; i < 80; i++) { if ((await year()) === '2024') break; await page.waitForTimeout(100); }
  await page.waitForTimeout(1400);

  // Beat 4: back to bubbles and hold a settled, full cloud (Sulu still pinned) as
  // the loop point. No replay click, so the end frame is stable, not a tween.
  await page.locator('.chart-type-btn[data-type=bubbles]').click();
  await page.waitForTimeout(2800);

  await page.close();
  await ctx.close();
  await browser.close();

  const webms = fs.readdirSync(OUT).filter(f => f.endsWith('.webm')).map(f => OUT + '/' + f);
  console.log('recorded:', webms[webms.length - 1]);
})().catch(e => { console.error(e); process.exit(1); });
