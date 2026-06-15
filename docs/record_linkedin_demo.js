// LinkedIn square (1080x1080) recorder for dataviz.ph.
//
// Drives a tight, self-narrating sequence (no scripted captions — only the tool's
// own copy carries it):
//   1. DPWH spend per capita vs poverty at 2018; the headline states the stakes.
//   2. Play 2018 -> 2024 (Rosling-style): the cloud marches right but doesn't
//      slide down; the finding lands on "no link, rho = +0.12".
//   3. Click the "GDP vs poverty" tab: the SAME provinces snap into a clean
//      downward diagonal; the finding lands on "rho = -0.53".
// The preset tab bar stays on screen, so the other joined sources (all-gov spend,
// spend-vs-GDP, inflation) are visible the whole time.
//
//   python3 -m http.server 8099 --directory public &   (or let the wrapper do it)
//   node docs/record_linkedin_demo.js
// then convert the webm to a gif (see the ffmpeg recipe used by the wrapper).
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
const OUT = process.env.DEMO_OUT || '/tmp/dataviz-linkedin-record';
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
const S = 1080; // square

// Slim the page to a square-friendly frame: drop the secondary copy + the
// below-fold controls/footer, keep the tab bar, headline, finding, chart-type
// strip, and the chart. Grow the chart to fill the freed vertical space.
const SLIM_CSS = `
  #story-tagline, #story-why, #story-caveat, #chart-howto, #controls,
  footer, .noscript, .topbar-right { display: none !important; }
  #title-strip { padding-top: 6px !important; padding-bottom: 2px !important; }
  #mobile-detail-slot { border: 0 !important; margin: 6px 0 0 !important; padding: 0 !important; }
  #story-finding { font-size: 14px !important; line-height: 1.45 !important; max-width: none !important; }
  #chart { height: 744px !important; }
  body { overflow: hidden !important; }
`;

(async () => {
  const browser = await PW.chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: S, height: S }, deviceScaleFactor: 2,
    recordVideo: { dir: OUT, size: { width: S, height: S } },
  });
  const page = await ctx.newPage();

  // Deep-link (hash present) => the first-visit arc is skipped, so the sequence
  // below is fully under our control. Motion stays on.
  await page.goto('http://localhost:8099/#story=spend-vs-poverty&year=2018', {
    waitUntil: 'networkidle', timeout: 20000,
  });
  await page.waitForSelector('#story-finding:not([hidden])', { timeout: 15000 });
  await page.waitForSelector('#chart canvas', { timeout: 15000 });

  // Below 1100px the app relocates the finding into #mobile-detail-slot (below the
  // chart). Move that slot up under the headline so the finding — the punchline —
  // stays on screen in the square frame, wherever the app re-places it on render.
  await page.evaluate(() => {
    const slot = document.getElementById('mobile-detail-slot');
    const titleText = document.querySelector('.title-text');
    if (slot && titleText) titleText.appendChild(slot);
  });
  await page.addStyleTag({ content: SLIM_CSS });
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  await page.waitForTimeout(700);

  const year = () =>
    page.evaluate(() => (document.getElementById('year-display') || {}).textContent || '');
  const playToEnd = async (target, maxMs = 10000) => {
    await page.evaluate(() => window.__datavizph_startPlay && window.__datavizph_startPlay());
    const t0 = Date.now();
    while (Date.now() - t0 < maxMs) {
      if ((await year()).trim() === target) break;
      await page.waitForTimeout(120);
    }
    await page.evaluate(() => window.__datavizph_stopPlay && window.__datavizph_stopPlay());
  };

  // 1. Hold on DPWH vs poverty, 2018 — the stakes.
  await page.waitForTimeout(1900);

  // 2. Rosling sweep 2018 -> 2024.
  await playToEnd('2024', 10000);
  await page.waitForTimeout(1500);

  // 3. The pivot: switch the joined source to GDP. Same provinces, new dataset.
  await page.getByRole('button', { name: 'GDP vs poverty', exact: true }).click();
  await page.waitForTimeout(900);
  // brief play across the short GDP panel, then hold the diagonal payoff
  await playToEnd('2024', 6000);
  await page.waitForTimeout(2600);

  // 4. Return to DPWH for a clean loop point.
  await page.getByRole('button', { name: 'DPWH vs poverty', exact: true }).click();
  await page.waitForTimeout(1100);

  await page.close();
  await ctx.close();
  await browser.close();
  const webms = fs.readdirSync(OUT).filter(f => f.endsWith('.webm')).map(f => OUT + '/' + f);
  console.log('recorded:', webms[webms.length - 1]);
})().catch(e => { console.error(e); process.exit(1); });
