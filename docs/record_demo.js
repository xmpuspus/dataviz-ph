// Hero recorder for dataviz.ph. Records the guided narrative arc that now plays
// on first load (fresh context => fresh localStorage => the arc auto-runs):
//   0. HOOK    "Eleven years. 5 trillion in road contracts. ... Most people
//              assume it did. Watch." (bubbles dimmed, 2018)
//   1. REVEAL  autoplay 2018->2024; the cloud marches right but never slides
//              down; top-right quadrant shaded; ends on "No link. rho = +0.12".
//   2. SPECIFIC the BARMM trails plunge; "Sulu's PSA estimate dropped 75% to 13%".
//   3. TWIST   cross-fade to GDP vs poverty; the clean downward diagonal; "Now it
//              slides. rho = -0.53. Money for roads didn't track poverty. Wealth did."
// Stops holding the twist (the payoff) as the loop point.
// Serve public/ on :8099, then `node docs/record_demo.js`, then convert the webm
// to a gif (see the ffmpeg recipe in the repo; ~1.5x speed, fps 13, scale 900).
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
  // Fresh context => fresh localStorage => the first-visit arc auto-runs. Motion on.
  const ctx = await browser.newContext({
    viewport: { width: W, height: H }, deviceScaleFactor: 2,
    recordVideo: { dir: OUT, size: { width: W, height: H } },
  });
  const page = await ctx.newPage();
  const beat = () => page.evaluate(
    () => (window.__datavizph_arcBeat ? window.__datavizph_arcBeat() : null),
  );
  const waitBeat = async (name, maxMs = 30000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < maxMs) {
      if ((await beat()) === name) return true;
      await page.waitForTimeout(120);
    }
    return false;
  };

  // ?cb is a query string, not a hash, so initial.hadHash stays false and the arc
  // runs. Cache-bust so a fresh script/data load each run.
  await page.goto('http://localhost:8099/?cb=' + Date.now(), { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForSelector('#story-finding:not([hidden])', { timeout: 15000 });

  // Let the arc carry itself: hook -> reveal (play) -> reveal-end -> specific ->
  // twist. Hold the twist (the diagonal payoff) as the final/loop frame.
  await waitBeat('hook');
  await waitBeat('twist');
  await page.waitForTimeout(3500);

  await page.close();
  await ctx.close();
  await browser.close();
  const webms = fs.readdirSync(OUT).filter(f => f.endsWith('.webm')).map(f => OUT + '/' + f);
  console.log('recorded:', webms[webms.length - 1]);
})().catch(e => { console.error(e); process.exit(1); });
