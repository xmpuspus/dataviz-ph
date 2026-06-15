// Social-cut recorder for dataviz.ph (square or landscape).
//
// Drives a tight, self-narrating sequence (no scripted captions — only the tool's
// own copy carries it):
//   1. DPWH spend per capita vs poverty at 2018; the headline states the stakes.
//   2. Play 2018 -> 2024 (Rosling-style): the cloud marches right but doesn't
//      slide down; the finding lands on "no link, rho = +0.12".
//   3. Click "GDP vs poverty": the SAME provinces snap into a clean downward
//      diagonal; the finding lands on "rho = -0.53".
//   4. Click "Inflation vs poverty": a THIRD joined source (PSA regional CPI vs
//      regional poverty, 18 regions) — breadth of the unification, "rho = +0.09".
// The preset tab bar stays on screen the whole time.
//
//   DEMO_MODE=square    -> 1080x1080 (default)
//   DEMO_MODE=landscape -> 1280x720
//
//   python3 -m http.server 8099 --directory public &
//   DEMO_MODE=square node docs/record_linkedin_demo.js
// then convert the webm to a gif (two-pass palette; see the wrapper recipe).
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

const MODE = process.env.DEMO_MODE === 'landscape' ? 'landscape' : 'square';
const DIM = MODE === 'landscape' ? { w: 1280, h: 720 } : { w: 1080, h: 1080 };
const OUT = process.env.DEMO_OUT || `/tmp/dataviz-${MODE}-record`;
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });

// Shared trims, then mode-specific layout. Square keeps the app's stacked mobile
// layout (chart leads, finding moved up under the headline). Landscape forces the
// desktop grid to a single full-width column (no sidebar) and a shorter chart.
const BASE_CSS = `
  #story-tagline, #story-why, #story-caveat, #chart-howto, #controls,
  footer, .noscript, .topbar-right { display: none !important; }
  #title-strip { padding-top: 6px !important; padding-bottom: 2px !important; }
  #mobile-detail-slot { border: 0 !important; margin: 6px 0 0 !important; padding: 0 !important; }
  #story-finding { font-size: 14px !important; line-height: 1.45 !important; max-width: none !important; }
  body { overflow: hidden !important; }
`;
const MODE_CSS = MODE === 'landscape'
  ? `main { grid-template-columns: 1fr !important; }
     #title-strip { flex-direction: column !important; align-items: stretch !important; }
     .title-text { width: 100% !important; }
     #chart { height: 486px !important; }`
  : `#chart { height: 744px !important; }`;

(async () => {
  const browser = await PW.chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: DIM.w, height: DIM.h }, deviceScaleFactor: 2,
    recordVideo: { dir: OUT, size: { width: DIM.w, height: DIM.h } },
  });
  const page = await ctx.newPage();

  await page.goto('http://localhost:8099/#story=spend-vs-poverty&year=2018', {
    waitUntil: 'networkidle', timeout: 20000,
  });
  await page.waitForSelector('#story-finding:not([hidden])', { timeout: 15000 });
  await page.waitForSelector('#chart canvas', { timeout: 15000 });

  // Keep the finding (the punchline) on screen: move its mobile slot up under the
  // headline. Harmless in landscape (the finding already lives in the title strip).
  await page.evaluate(() => {
    const slot = document.getElementById('mobile-detail-slot');
    const titleText = document.querySelector('.title-text');
    if (slot && titleText) titleText.appendChild(slot);
  });
  await page.addStyleTag({ content: BASE_CSS + MODE_CSS });
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
  const tab = (name) => page.getByRole('button', { name, exact: true }).click();

  // 1. DPWH vs poverty, 2018 — the stakes.
  await page.waitForTimeout(1800);
  // 2. Rosling sweep 2018 -> 2024.
  await playToEnd('2024', 10000);
  await page.waitForTimeout(1300);

  // 3. Pivot to GDP — same provinces, a joined source, opposite story.
  await tab('GDP vs poverty');
  await page.waitForTimeout(850);
  await playToEnd('2024', 6000);
  await page.waitForTimeout(2200);

  // 4. A third joined source: regional inflation vs regional poverty (18 regions).
  await tab('Inflation vs poverty');
  await page.waitForTimeout(850);
  await playToEnd('2023', 5000);
  await page.waitForTimeout(2100);

  // 5. Back to DPWH for a clean loop point.
  await tab('DPWH vs poverty');
  await page.waitForTimeout(900);

  await page.close();
  await ctx.close();
  await browser.close();
  const webms = fs.readdirSync(OUT).filter(f => f.endsWith('.webm')).map(f => OUT + '/' + f);
  console.log(MODE, 'recorded:', webms[webms.length - 1]);
})().catch(e => { console.error(e); process.exit(1); });
