// Per-story Open Graph card generator for dataviz.ph.
//
// Renders one 1200x630 social card per preset story into public/og/<id>.png. The
// card carries the story's headline (the question) and the computed finding's
// first claim (the answer) pulled straight from public/data/stories.json, so the
// shared image can never drift from the data. Run after a data rebuild or when
// headlines/design change:
//
//   node docs/build_og_cards.js
//
// Design mirrors docs/og.html (the homepage card): serif headline on the left,
// a decorative island-colored scatter on the right. The scatter is evocative, not
// data-exact (same disclaimer the homepage card carries).
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
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const STORIES = JSON.parse(fs.readFileSync(path.join(ROOT, 'public/data/stories.json'), 'utf8'));
const OUT = path.join(ROOT, 'public/og');
fs.mkdirSync(OUT, { recursive: true });

// The finding's headline answer: first sentence of the computed finding.
function firstClaim(s) {
  if (!s) return '';
  let head = s.split('. ')[0].trim();
  if (!head.endsWith('.')) head += '.';
  return head;
}

function cardHtml(story) {
  const headline = story.headline || 'dataviz.ph';
  const finding = story.finding || {};
  const answer = firstClaim(finding.sentence) || story.tagline || '';
  const year = story.default_year || 2023;
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return `<!doctype html><html><head><meta charset="utf-8"><style>
  :root{--ink:#111;--muted:#595959;--rule:#e6e6e6;--brand:#0e7c86;
    --luzon:#2b6cb0;--visayas:#38a169;--mindanao:#d97706;--ncr:#6b46c1;--barmm:#c53030;}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{width:1200px;height:630px;background:#fff;
    font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,sans-serif;
    color:var(--ink);-webkit-font-smoothing:antialiased}
  .card{width:1200px;height:630px;display:flex;padding:54px 60px}
  .left{width:610px;display:flex;flex-direction:column}
  .brand{font-size:25px;font-weight:700;letter-spacing:-0.02em}
  .brand .tld{color:var(--brand)}
  h1{font-family:Georgia,"Iowan Old Style",Palatino,"Times New Roman",serif;
    font-weight:600;font-size:46px;line-height:1.13;letter-spacing:-0.01em;margin-top:40px;max-width:580px}
  .answer{color:var(--brand);font-size:23px;line-height:1.4;margin-top:24px;max-width:560px;font-weight:600}
  .foot{margin-top:auto;color:var(--muted);font-size:18px;line-height:1.5}
  .right{width:470px;margin-left:20px;position:relative;
    border:1px solid var(--rule);border-radius:12px;padding:18px}
  .yaxis{font-size:15px;color:var(--muted)}
  .year{position:absolute;right:24px;top:64px;font-size:96px;font-weight:700;color:rgba(0,0,0,0.06);letter-spacing:-0.02em}
  .plot{position:relative;height:360px;margin-top:6px}
  .bub{position:absolute;border-radius:50%;opacity:0.92}
  .xcap{text-align:center;font-size:14px;color:var(--muted);font-style:italic;margin-top:8px}
  .legend{display:flex;gap:15px;margin-top:12px;font-size:14px;align-items:center;flex-wrap:wrap}
  .legend span{display:inline-flex;align-items:center;gap:6px}
  .legend i{width:11px;height:11px;border-radius:50%;display:inline-block}
  </style></head><body>
  <div class="card">
    <div class="left">
      <div class="brand">dataviz.<span class="tld">ph</span></div>
      <h1>${esc(headline)}</h1>
      <p class="answer">${esc(answer)}</p>
      <div class="foot">Sources: PSA OpenStat &middot; PhilGEPS &middot; PSA 2020 Census<br>Correlation, not causation &middot; dataviz.ph</div>
    </div>
    <div class="right">
      <div class="yaxis">poverty %</div>
      <div class="year">${esc(year)}</div>
      <div class="plot" id="plot"></div>
      <div class="xcap">value per capita (log)</div>
      <div class="legend">
        <span><i style="background:var(--luzon)"></i>Luzon</span>
        <span><i style="background:var(--visayas)"></i>Visayas</span>
        <span><i style="background:var(--mindanao)"></i>Mindanao</span>
        <span><i style="background:var(--ncr)"></i>NCR</span>
        <span><i style="background:var(--barmm)"></i>BARMM</span>
      </div>
    </div>
  </div></body></html>`;
}

// The decorative scatter, drawn after layout settles (inline-script timing raced
// the layout on reused pages and left some cards' panels empty).
function drawScatter() {
  const C = { luzon: '#2b6cb0', visayas: '#38a169', mindanao: '#d97706', ncr: '#6b46c1', barmm: '#c53030' };
  const B = [[12, 8, 30, 'barmm'], [22, 5, 40, 'barmm'], [8, 30, 26, 'barmm'],
    [40, 18, 52, 'mindanao'], [46, 12, 42, 'mindanao'], [58, 30, 30, 'mindanao'], [72, 40, 46, 'mindanao'],
    [36, 44, 40, 'visayas'], [60, 55, 64, 'visayas'], [78, 62, 30, 'visayas'],
    [50, 58, 54, 'luzon'], [64, 66, 40, 'luzon'], [82, 72, 34, 'luzon'], [88, 55, 26, 'luzon'], [56, 70, 78, 'ncr']];
  const plot = document.getElementById('plot');
  const w = plot.clientWidth || 434, h = 360;
  for (const [x, y, d, isl] of B) {
    const el = document.createElement('div'); el.className = 'bub';
    el.style.width = d + 'px'; el.style.height = d + 'px'; el.style.background = C[isl];
    el.style.left = (x / 100 * w - d / 2) + 'px'; el.style.top = (y / 100 * h - d / 2) + 'px';
    plot.appendChild(el);
  }
}

(async () => {
  const browser = await PW.chromium.launch();
  for (const story of STORIES) {
    // Fresh page per card so layout is clean before the scatter is drawn.
    const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
    await page.setContent(cardHtml(story), { waitUntil: 'load' });
    await page.evaluate(drawScatter);
    await page.waitForTimeout(80);
    const file = path.join(OUT, `${story.id}.png`);
    await page.screenshot({ path: file, clip: { x: 0, y: 0, width: 1200, height: 630 } });
    console.log('wrote', path.relative(ROOT, file));
    await page.close();
  }
  await browser.close();
})();
