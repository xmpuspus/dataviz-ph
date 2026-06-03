// dataviz.ph: story switcher, inflation toggle, CSV download, a11y mirror table,
// compare-two-years overlay, keyboard scrubber.

const PALETTE = {
  luzon: "#2b6cb0",
  visayas: "#38a169",
  mindanao: "#d97706",
  ncr: "#6b46c1",
  barmm: "#c53030",
};

const ISLAND_LABEL = {
  luzon: "Luzon",
  visayas: "Visayas",
  mindanao: "Mindanao",
  ncr: "NCR",
  barmm: "BARMM",
};

const PHP = new Intl.NumberFormat("en-PH", {
  style: "currency",
  currency: "PHP",
  maximumFractionDigits: 0,
});
const PCT = new Intl.NumberFormat("en-PH", {
  maximumFractionDigits: 1,
  minimumFractionDigits: 1,
});
const COUNT = new Intl.NumberFormat("en-PH");

const OUTLIER_TOP_N = 3;

// True while the play loop is auto-stepping years. Used to silence the sr-only
// live summary so a screen reader is not flooded with one announcement per frame.
let IS_AUTOPLAYING = false;

const REDUCE_MOTION = !!(
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
);

// Pick black or white text for a solid color background so labels stay legible.
// The lighter island hues (Visayas green, Mindanao amber) fail 4.5:1 against
// white, so chips on those get dark text instead.
function readableInk(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return "#fff";
  const n = parseInt(m[1], 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  const lum = 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  // Contrast vs white is (1.05)/(lum+0.05); use dark ink when that drops below 4.5.
  return 1.05 / (lum + 0.05) >= 4.5 ? "#fff" : "#111";
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[c]);
}

async function fetchJson(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`Failed to fetch ${path}: HTTP ${r.status}`);
  return r.json();
}

// The "view" is the active chart configuration. It's derived from the preset
// (state.story) the user picked plus any custom X/Y indicator overrides from
// the dropdowns. Computed each render so panel_years is always the intersection
// of the two indicator series' coverage.
function makeView(state, data) {
  const xId = state.xIndicator || state.story.x;
  const yId = state.yIndicator || state.story.y;
  const xMeta = data.indicators[xId];
  const yMeta = data.indicators[yId];
  if (!xMeta || !yMeta) {
    return { ...state.story, isCustom: false };
  }
  const xYears = new Set(xMeta.panel_years || []);
  const panel_years = (yMeta.panel_years || []).filter((y) => xYears.has(y));
  const isCustom = xId !== state.story.x || yId !== state.story.y;
  let default_year = state.story.default_year;
  if (!panel_years.includes(default_year)) {
    default_year = panel_years[panel_years.length - 1];
  }
  // For custom (picker-driven) views, look up a written-out headline for this
  // pair before falling back to the generic "Y vs X / Custom view" template.
  const pairMeta = isCustom ? pairHeadlineFor(xId, yId, data) : null;
  const headline = isCustom
    ? (pairMeta ? pairMeta.headline : `${yMeta.name} vs ${xMeta.name}`)
    : state.story.headline;
  const tagline = isCustom
    ? (pairMeta
        ? pairMeta.tagline
        : `Custom view. ${panel_years.length} years of overlap: ${panel_years[0]} to ${panel_years[panel_years.length - 1]}.`)
    : state.story.tagline;
  return {
    ...state.story,
    x: xId,
    y: yId,
    panel_years,
    default_year,
    headline,
    tagline,
    isCustom,
  };
}

async function loadData() {
  const [
    provinces,
    poverty,
    subsistence,
    spend,
    allSpend,
    dohSpend,
    infraSpend,
    gdp,
    dpwhShare,
    cpiYoy,
    povertyChange,
    population,
    indicators,
    stories,
    pairHeadlines,
    manifest,
  ] = await Promise.all([
    fetchJson("data/provinces.json"),
    fetchJson("data/poverty.json"),
    fetchJson("data/subsistence.json").catch(() => []),
    fetchJson("data/dpwh_spend_per_capita.json"),
    fetchJson("data/all_spend_per_capita.json"),
    fetchJson("data/doh_spend_per_capita.json").catch(() => []),
    fetchJson("data/infra_spend_per_capita.json").catch(() => []),
    fetchJson("data/gdp_per_capita.json"),
    fetchJson("data/dpwh_share_pct.json").catch(() => []),
    fetchJson("data/cpi_yoy_pct.json").catch(() => []),
    fetchJson("data/poverty_change_pp.json").catch(() => []),
    fetchJson("data/population.json").catch(() => []),
    fetchJson("data/indicators.json"),
    fetchJson("data/stories.json"),
    fetchJson("data/pair_headlines.json").catch(() => ({})),
    fetchJson("data/manifest.json").catch(() => null),
  ]);
  return {
    provinces,
    indicatorRows: {
      poverty: indexRows(poverty),
      subsistence_incidence: indexRows(subsistence),
      dpwh_spend_per_capita: indexRows(spend),
      all_spend_per_capita: indexRows(allSpend),
      doh_spend_per_capita: indexRows(dohSpend),
      infra_spend_per_capita: indexRows(infraSpend),
      gdp_per_capita: indexRows(gdp),
      dpwh_share_pct: indexRows(dpwhShare),
      cpi_yoy_pct: indexRows(cpiYoy),
      poverty_change_pp: indexRows(povertyChange),
      population: indexRows(population),
    },
    indicators: Object.fromEntries(indicators.map((i) => [i.id, i])),
    stories,
    pairHeadlines,
    manifest,
  };
}

// Look up a per-pair headline + tagline by sorted indicator IDs.
function pairHeadlineFor(xId, yId, data) {
  const ph = data && data.pairHeadlines;
  if (!ph) return null;
  const key = [xId, yId].sort().join("|");
  return ph[key] || null;
}

function renderFreshness(manifest) {
  const target = document.getElementById("data-freshness");
  if (!target) return;
  if (!manifest || !manifest.built_at) {
    target.textContent = "";
    return;
  }
  let dateText = manifest.built_at;
  try {
    const d = new Date(manifest.built_at);
    dateText = d.toLocaleDateString("en-PH", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch (_e) {
    // keep raw ISO if parsing fails
  }
  const vintages = manifest.source_vintages || {};
  const povertyV = vintages.poverty || "PSA 2018/2021/2023 anchors";
  target.textContent = `Built ${dateText}. Poverty anchors: ${povertyV}.`;
}

function indexRows(rows) {
  const out = {};
  for (const r of rows) out[`${r.psgc}-${r.year}`] = r;
  return out;
}

// Bubble size: log10(pop) mapped to 6..36 px so NCR doesn't dominate.
function sizeFor(pop) {
  if (!pop) return 6;
  const l = Math.log10(pop);
  const px = 6 + (l - 5) * 14.3;
  return Math.max(6, Math.min(36, px));
}

// Reference circles for the size key. Each px is the exact diameter sizeFor()
// gives a province of that population, so the legend can never drift from the
// encoding. Spans the real PH range: small province ~100k to Metro Manila ~13M.
const SIZE_LEGEND_REFS = [
  { pop: 100000, label: "100k" },
  { pop: 1000000, label: "1M" },
  { pop: 10000000, label: "10M" },
];

let _sizeLegendDrawn = false;
function renderSizeLegend() {
  if (_sizeLegendDrawn) return;
  const row = document.getElementById("size-legend-row");
  if (!row) return;
  row.replaceChildren();
  for (const ref of SIZE_LEGEND_REFS) {
    const d = Math.round(sizeFor(ref.pop));
    const item = document.createElement("div");
    item.className = "size-legend-item";
    const circle = document.createElement("span");
    circle.className = "size-legend-circle";
    circle.style.width = `${d}px`;
    circle.style.height = `${d}px`;
    const label = document.createElement("span");
    label.className = "size-legend-label";
    label.textContent = ref.label;
    item.append(circle, label);
    row.appendChild(item);
  }
  _sizeLegendDrawn = true;
}

// First-read scaffold dismissal persists so a returning reader is not nagged.
// localStorage can throw (private mode / disabled storage); fail open to shown.
const HOWTO_KEY = "datavizph_howto_dismissed";
function readHowtoDismissed() {
  try {
    return localStorage.getItem(HOWTO_KEY) === "1";
  } catch {
    return false;
  }
}
function persistHowtoDismissed() {
  try {
    localStorage.setItem(HOWTO_KEY, "1");
  } catch {
    /* storage unavailable: dismissal lasts for this session only */
  }
}

const DEFLATABLE_INDICATORS = new Set([
  "dpwh_spend_per_capita",
  "all_spend_per_capita",
]);

// PSA CPI 2018=100 series starts at 2018. Years before that cannot be deflated
// honestly without backcasting against the older 2006 base. We drop the bubble
// rather than silently fall back to nominal.
const CPI_BASE_YEAR = 2018;

function indicatorValue(row, indicatorId, state) {
  if (!row) return null;
  if (DEFLATABLE_INDICATORS.has(indicatorId) && state.deflate) {
    if (row.value_real === null || row.value_real === undefined) return null;
    return row.value_real;
  }
  return row.value;
}

// PSA convention: poverty/subsistence estimates with a coefficient of variation
// above 30% are too imprecise to rank. We flag and dim those bubbles.
const CV_UNRELIABLE = 30;

// Pull PSA's published 95% CI + CV off a row, or null if the row carries none
// (only the survey anchor years do; interpolated years are model estimates).
function precisionOf(row) {
  if (!row || row.ci_lo === undefined || row.ci_lo === null) return null;
  return { cv: row.cv, se: row.se, ci_lo: row.ci_lo, ci_hi: row.ci_hi };
}

// Global value extent for an indicator across ALL its panel years, deflate-aware.
// Used by the map so a province's colour reflects real change between years, not
// a min/max that silently rescales each frame. Cached per (indicator, currency).
const _extentCache = new Map();
function globalExtent(indicatorId, data, state) {
  const key = `${indicatorId}|${state.deflate ? "real" : "nom"}`;
  if (_extentCache.has(key)) return _extentCache.get(key);
  const meta = data.indicators[indicatorId] || {};
  const years = meta.panel_years || [];
  let min = Infinity;
  let max = -Infinity;
  for (const psgc of Object.keys(data.provinces)) {
    for (const y of years) {
      const row = lookupRow(indicatorId, psgc, y, data);
      const v = row ? indicatorValue(row, indicatorId, state) : null;
      if (v === null || v === undefined) continue;
      if (v < min) min = v;
      if (v > max) max = v;
    }
  }
  if (!isFinite(min)) {
    min = 0;
    max = 1;
  }
  if (min === max) max = min + 1;
  const ext = { min, max };
  _extentCache.set(key, ext);
  return ext;
}

// Indicator row lookup with national-only fallback: if the per-province row
// is missing AND the indicator is flagged national_only, return the national
// row (psgc='000000000') so every bubble gets the national value at that year.
function lookupRow(indicatorId, psgc, year, data) {
  const rows = data.indicatorRows[indicatorId];
  if (!rows) return null;
  const exact = rows[`${psgc}-${year}`];
  if (exact) return exact;
  const meta = data.indicators[indicatorId];
  if (meta && meta.national_only) {
    return rows[`000000000-${year}`] || null;
  }
  return null;
}

// Anchor-year cache for the projection helper. Per (indicatorId, psgc), list
// of {year, value} for rows where interp=false && extrap=false.
const _anchorsCache = new Map();
function getAnchors(indicatorId, psgc, data) {
  const key = `${indicatorId}|${psgc}`;
  if (_anchorsCache.has(key)) return _anchorsCache.get(key);
  const rows = data.indicatorRows[indicatorId];
  const out = [];
  if (rows) {
    for (const k of Object.keys(rows)) {
      if (!k.startsWith(`${psgc}-`)) continue;
      const r = rows[k];
      if (r && !r.interp && !r.extrap && r.value !== null && r.value !== undefined) {
        out.push({ year: parseInt(k.split("-")[1], 10), value: r.value });
      }
    }
    out.sort((a, b) => a.year - b.year);
  }
  _anchorsCache.set(key, out);
  return out;
}

// Linear projection from the two nearest anchors. Returns the projected value
// or null if fewer than 2 anchors are available.
function projectValue(indicatorId, psgc, year, data) {
  const anchors = getAnchors(indicatorId, psgc, data);
  if (anchors.length < 2) {
    return anchors.length === 1 ? anchors[0].value : null;
  }
  const first = anchors[0];
  const last = anchors[anchors.length - 1];
  let a, b;
  if (year < first.year) {
    a = first;
    b = anchors[1];
  } else if (year > last.year) {
    a = anchors[anchors.length - 2];
    b = last;
  } else {
    // Inside the anchor range; the existing linear_fill in the ETL already
    // handles this. Return the value of the nearest anchor as a fallback.
    return anchors.reduce((acc, x) =>
      Math.abs(x.year - year) < Math.abs(acc.year - year) ? x : acc,
    ).value;
  }
  if (b.year === a.year) return a.value;
  const slope = (b.value - a.value) / (b.year - a.year);
  let projected = a.value + slope * (year - a.year);
  // Reasonable clipping for known indicators.
  if (indicatorId === "poverty" || indicatorId === "dpwh_share_pct") {
    projected = Math.max(0, Math.min(100, projected));
  }
  if (indicatorId.endsWith("_per_capita") || indicatorId === "population") {
    projected = Math.max(0, projected);
  }
  return projected;
}

// Build per-province point for a year in the current story.
function pointFor(psgc, year, story, data, state) {
  const info = data.provinces[psgc];
  if (!info) return null;
  const xRow = lookupRow(story.x, psgc, year, data);
  const yRow = lookupRow(story.y, psgc, year, data);
  if (!xRow || !yRow) return null;
  let xVal = indicatorValue(xRow, story.x, state);
  let yVal = indicatorValue(yRow, story.y, state);
  let projected = false;
  // When extrapolate mode is on, swap held-constant extrap values for
  // linear-projected values from the nearest two anchors.
  if (state.extrapolate) {
    if (yRow.extrap) {
      const proj = projectValue(story.y, psgc, year, data);
      if (proj !== null) {
        yVal = proj;
        projected = true;
      }
    }
    if (xRow.extrap) {
      const proj = projectValue(story.x, psgc, year, data);
      if (proj !== null) {
        xVal = proj;
        projected = true;
      }
    }
  }
  if (xVal === null || yVal === null) return null;
  // Precision (95% CI + CV) attaches to whichever axis carries an indicator that
  // PSA publishes with measures of precision (poverty / subsistence). Suppressed
  // when the spend deflator is active (spend has no CI) or on non-anchor years.
  const xPrec = state.deflate && DEFLATABLE_INDICATORS.has(story.x) ? null : precisionOf(xRow);
  const yPrec = state.deflate && DEFLATABLE_INDICATORS.has(story.y) ? null : precisionOf(yRow);
  return {
    psgc,
    name: info.name,
    island: info.island_group,
    pop: info.population_2020,
    year,
    x: xVal,
    y: yVal,
    interp: !!yRow.interp,
    extrap: !!yRow.extrap,
    projected,
    xPrec,
    yPrec,
  };
}

function buildSeriesData(year, story, data, state) {
  const points = [];
  for (const psgc of Object.keys(data.provinces)) {
    const p = pointFor(psgc, year, story, data, state);
    if (p) points.push(p);
  }
  if (!points.length) return [];

  const narrow = window.innerWidth < 600;
  const outlier = new Set();
  if (!narrow) {
    const byX = [...points].sort((a, b) => b.x - a.x).slice(0, OUTLIER_TOP_N);
    const byY = [...points].sort((a, b) => b.y - a.y).slice(0, OUTLIER_TOP_N);
    for (const p of byX) outlier.add(p.psgc);
    for (const p of byY) outlier.add(p.psgc);
  }

  return points.map((p) => {
    const isSel = state.sel.has(p.psgc);
    const showLabel = isSel || outlier.has(p.psgc);
    const color = PALETTE[p.island] || "#999";
    const isAnchor = !p.interp && !p.extrap;
    const isProjected = !!p.projected;
    // Imprecise = PSA reports a CV above the reliability threshold on either axis.
    // Such bubbles get an amber dashed ring + reduced opacity so a too-uncertain
    // estimate never reads as a confident data point.
    const cvY = p.yPrec && p.yPrec.cv;
    const cvX = p.xPrec && p.xPrec.cv;
    const imprecise =
      (cvY != null && cvY > CV_UNRELIABLE) || (cvX != null && cvX > CV_UNRELIABLE);
    const itemStyle = {
      color,
      opacity: imprecise ? 0.3 : isProjected ? 0.45 : isAnchor ? 0.88 : 0.55,
      borderColor: imprecise ? "#c05621" : isProjected ? color : isAnchor ? "#fff" : color,
      borderWidth: imprecise ? 1.6 : isProjected ? 2 : isAnchor ? 0.6 : 1.5,
      borderType: imprecise || isProjected ? "dashed" : "solid",
    };
    return {
      id: p.psgc,
      name: p.name,
      value: [p.x, p.y, p.pop, p.name, p.year, p.interp, p.island, p.extrap],
      xPrec: p.xPrec,
      yPrec: p.yPrec,
      symbolSize: sizeFor(p.pop),
      itemStyle,
      label: {
        show: showLabel,
        position: "right",
        formatter: p.name,
        color: isSel ? "#111" : "#555",
        fontWeight: isSel ? 600 : 400,
        fontSize: isSel ? 12 : 11,
        backgroundColor: isSel ? "rgba(255,255,255,0.85)" : "transparent",
        padding: isSel ? [2, 4] : 0,
        borderRadius: 3,
        distance: 6,
      },
    };
  });
}

// Pick the 3 most-moved provinces (largest absolute Y change between the first
// and last year that has data for them). Used to seed auto-trails on first
// visit so the trace effect is visible without requiring a click.
function autoTrailProvinces(story, data, n = 3) {
  const movements = [];
  for (const psgc of Object.keys(data.provinces)) {
    let first = null;
    let last = null;
    for (const y of story.panel_years) {
      const yRow = data.indicatorRows[story.y][`${psgc}-${y}`];
      if (!yRow || yRow.value === null || yRow.value === undefined) continue;
      if (first === null) first = yRow.value;
      last = yRow.value;
    }
    if (first === null || last === null) continue;
    movements.push({ psgc, delta: Math.abs(last - first) });
  }
  movements.sort((a, b) => b.delta - a.delta);
  return movements.slice(0, n).map((m) => m.psgc);
}

function buildTrails(year, story, data, state) {
  // Selected provinces: bold trail. Auto-trail (only when nothing is selected):
  // faint trail for the 3 most-moved provinces, so the Gapminder trace effect
  // is visible without requiring a click.
  const trails = [];
  const autoTrails =
    state.sel.size === 0 ? autoTrailProvinces(story, data) : [];
  const psgcList = [...state.sel, ...autoTrails];

  for (const psgc of psgcList) {
    const isSel = state.sel.has(psgc);
    const pts = [];
    for (const y of story.panel_years) {
      if (y > year) break;
      const p = pointFor(psgc, y, story, data, state);
      if (p) pts.push([p.x, p.y]);
    }
    if (pts.length < 2) continue;
    const info = data.provinces[psgc];
    const color = PALETTE[info.island_group] || "#999";
    const lineOpacity = isSel ? 0.7 : 0.32;
    const dotOpacity = isSel ? 0.9 : 0.5;
    trails.push({
      type: "line",
      name: `trail_${psgc}`,
      data: pts,
      // Small dot at each year the bubble has data for. Gapminder signature.
      symbol: "circle",
      symbolSize: isSel ? 4 : 3,
      showSymbol: true,
      // Straight segments between true measured points. Smoothing overshoots
      // when X swings year-to-year (e.g. spend volatility) and creates loops
      // that misrepresent the path.
      smooth: false,
      lineStyle: { color, width: isSel ? 2 : 1.2, opacity: lineOpacity },
      itemStyle: { color, opacity: dotOpacity, borderWidth: 0 },
      tooltip: { show: false },
      animationDurationUpdate: 600,
      z: isSel ? 2 : 1,
    });
  }
  return trails;
}

// 95% CI whiskers for selected provinces at the current year (bubble chart only).
// A vertical bar when the Y axis carries precision, horizontal when X does. Only
// the survey anchor years have precision, so most years draw nothing -- which is
// itself honest: PSA only measured those years.
function ciWhisker(id, pts, color) {
  return {
    id,
    type: "line",
    name: id,
    data: pts,
    symbol: "none",
    lineStyle: { color, width: 1.5, opacity: 0.7 },
    tooltip: { show: false },
    silent: true,
    z: 3,
    animationDurationUpdate: 300,
  };
}

function buildErrorBars(year, story, data, state) {
  const out = new Map();
  for (const psgc of state.sel) {
    const p = pointFor(psgc, year, story, data, state);
    if (!p) continue;
    const color = PALETTE[p.island] || "#999";
    if (p.yPrec && p.yPrec.ci_lo != null) {
      out.set(
        `err_y_${psgc}`,
        ciWhisker(`err_y_${psgc}`, [[p.x, p.yPrec.ci_lo], [p.x, p.yPrec.ci_hi]], color),
      );
    }
    if (p.xPrec && p.xPrec.ci_lo != null) {
      out.set(
        `err_x_${psgc}`,
        ciWhisker(`err_x_${psgc}`, [[p.xPrec.ci_lo, p.y], [p.xPrec.ci_hi, p.y]], color),
      );
    }
  }
  return out;
}

function unitFor(indicatorId, state) {
  if (DEFLATABLE_INDICATORS.has(indicatorId)) {
    return state.deflate ? "PHP per person (2018-real)" : "PHP per person (nominal)";
  }
  if (indicatorId === "gdp_per_capita") return "PHP per person (constant 2018)";
  if (indicatorId === "poverty") return "%";
  return "";
}

function formatValue(v, indicatorId) {
  if (indicatorId === "poverty" || indicatorId === "dpwh_share_pct" ||
      indicatorId === "cpi_yoy_pct") {
    return `${PCT.format(v)}%`;
  }
  if (indicatorId === "poverty_change_pp") {
    const sign = v >= 0 ? "+" : "";
    return `${sign}${PCT.format(v)} pp`;
  }
  if (indicatorId === "population") return COUNT.format(v);
  return PHP.format(v);
}

function shortAxisName(indicatorId, state) {
  if (indicatorId === "dpwh_spend_per_capita") {
    return state.deflate
      ? "DPWH spend per capita, PHP 2018-real"
      : "DPWH spend per capita, PHP nominal";
  }
  if (indicatorId === "all_spend_per_capita") {
    return state.deflate
      ? "All gov spend per capita, PHP 2018-real"
      : "All gov spend per capita, PHP nominal";
  }
  if (indicatorId === "doh_spend_per_capita") {
    return state.deflate
      ? "DOH spend per capita, PHP 2018-real"
      : "DOH spend per capita, PHP nominal";
  }
  if (indicatorId === "infra_spend_per_capita") {
    return state.deflate
      ? "Infra spend per capita, PHP 2018-real"
      : "Infra spend per capita, PHP nominal";
  }
  if (indicatorId === "gdp_per_capita") return "Per capita GDP, PHP (constant 2018)";
  if (indicatorId === "poverty") return "Poverty incidence among families (%)";
  if (indicatorId === "dpwh_share_pct") return "DPWH share of all spend (%)";
  if (indicatorId === "population") return "Population (2020 Census)";
  if (indicatorId === "poverty_change_pp") return "Poverty change 2018 to 2023 (pp)";
  if (indicatorId === "cpi_yoy_pct") return "National CPI year-on-year (%)";
  return indicatorId;
}

// Subtitle for the canvas axis (unit + caveat). The indicator name itself is
// rendered as an HTML pill overlaid on the chart by attachAxisInfoButtons.
function shortAxisCaption(indicatorId, state) {
  if (indicatorId === "dpwh_spend_per_capita" || indicatorId === "all_spend_per_capita" ||
      indicatorId === "doh_spend_per_capita" || indicatorId === "infra_spend_per_capita") {
    return state.deflate ? "PHP per person, 2018-real" : "PHP per person, nominal";
  }
  if (indicatorId === "gdp_per_capita") return "PHP per person, constant 2018 prices";
  if (indicatorId === "poverty") return "percent of families below poverty line";
  if (indicatorId === "dpwh_share_pct") return "percent of total province spend";
  if (indicatorId === "population") return "people, 2020 Census";
  if (indicatorId === "poverty_change_pp") return "percentage points, 2023 minus 2018";
  if (indicatorId === "cpi_yoy_pct") return "percent, national series only";
  return "";
}

// One tooltip line for a 95% CI + CV on a given axis, or "" if no precision.
// Uses "to" (not an en/em dash) for the range per the project copy rules.
function ciTooltipLine(label, prec, indicatorId) {
  if (!prec || prec.ci_lo === undefined || prec.ci_lo === null) return "";
  const lo = formatValue(prec.ci_lo, indicatorId);
  const hi = formatValue(prec.ci_hi, indicatorId);
  const cvTxt = prec.cv != null ? ` (CV ${PCT.format(prec.cv)}%)` : "";
  const warn =
    prec.cv != null && prec.cv > CV_UNRELIABLE
      ? ' <span style="color:#a13b2d;font-weight:600">imprecise</span>'
      : "";
  return (
    `<div style="color:#595959;font-size:11px">${escapeHtml(label)} 95% CI: ` +
    `${escapeHtml(lo)} to ${escapeHtml(hi)}${escapeHtml(cvTxt)}${warn}</div>`
  );
}

const IS_TOUCH =
  typeof window !== "undefined" &&
  window.matchMedia &&
  window.matchMedia("(hover: none)").matches;

function baseOption(story, data, state) {
  const xIndicator = story.x;
  const yIndicator = story.y;
  const logX = state.logX;
  return {
    // Bottom margin reserves 200px for: tick labels (~30px), X picker pill (~36px),
    // caption (italic ~14px), timeline component, and the big play button.
    grid: { left: 60, right: 28, top: 56, bottom: 200 },
    xAxis: {
      type: logX ? "log" : "value",
      // Indicator name lives in the HTML picker pill ABOVE this caption.
      // nameGap places this italic caption far enough below the axis to sit
      // under the pill, not on top of it.
      name: shortAxisCaption(xIndicator, state) + (logX ? " · log scale" : " · linear"),
      nameLocation: "middle",
      nameGap: 100,
      nameTextStyle: { fontSize: 11, color: "#6b6b6b", fontStyle: "italic" },
      min: logX ? undefined : 0,
      axisLine: { lineStyle: { color: "#ccc" } },
      axisTick: { show: false },
      splitLine: { show: true, lineStyle: { color: "#f0f0f0" } },
      axisLabel: {
        color: "#595959",
        formatter: (v) => {
          if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
          if (v >= 1000) return (v / 1000).toFixed(0) + "k";
          return v.toString();
        },
      },
    },
    yAxis: {
      type: "value",
      // Y indicator name lives in the HTML picker pill at top-left of the chart.
      // We deliberately don't put a canvas Y axis name here; it overlaps the
      // tick labels at the chart's left edge and the pill already names it.
      name: "",
      nameLocation: "middle",
      nameGap: 0,
      nameTextStyle: { fontSize: 0 },
      min: yIndicator === "poverty_change_pp" ? undefined : 0,
      max: yIndicator === "poverty" ? 80 : undefined,
      scale: yIndicator === "poverty_change_pp",
      axisLine: { lineStyle: { color: "#ccc" } },
      axisTick: { show: false },
      splitLine: { show: true, lineStyle: { color: "#f0f0f0" } },
      axisLabel: {
        color: "#595959",
        formatter: (v) => {
          if (yIndicator === "poverty" || yIndicator === "dpwh_share_pct" ||
              yIndicator === "cpi_yoy_pct" || yIndicator === "poverty_change_pp") {
            return v + "%";
          }
          if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
          if (v >= 1000) return (v / 1000).toFixed(0) + "k";
          return v.toString();
        },
      },
    },
    tooltip: {
      trigger: "item",
      // On touch devices ECharts' default 'mousemove|click' fires on the synthetic
      // mouseleave of the first tap and dismisses immediately. 'click' keeps the
      // tooltip visible until the next click anywhere.
      triggerOn: IS_TOUCH ? "click" : "mousemove|click",
      enterable: false,
      backgroundColor: "rgba(255,255,255,0.97)",
      borderColor: "#ddd",
      textStyle: { color: "#111" },
      formatter: (p) => {
        if (!p.value || p.value.length < 8) return "";
        const [x, y, pop, name, year, interp, island, extrap] = p.value;
        const color = PALETTE[island] || "#999";
        const swatch = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px;vertical-align:middle"></span>`;
        // Note describes the Y indicator's interpolation state. Skip on plain
        // anchor-year data points where the note adds no information.
        let noteHtml = "";
        if (extrap || interp) {
          const yName = (data.indicators[yIndicator] && data.indicators[yIndicator].name) ||
            yIndicator;
          const note = extrap
            ? `${yName} held constant from nearest PSA anchor (PSA does not publish this year)`
            : `${yName} linearly interpolated between PSA anchors`;
          noteHtml = `<div style="color:#595959;font-size:11px;margin-top:6px;border-top:1px solid #eee;padding-top:4px">${escapeHtml(note)}</div>`;
        }
        const xPrec = p.data && p.data.xPrec;
        const yPrec = p.data && p.data.yPrec;
        return (
          `<div style="font-weight:600;margin-bottom:4px">${swatch}${escapeHtml(name)} · ${year}</div>` +
          `<div style="color:#595959;font-size:11px;margin-bottom:6px">${escapeHtml(ISLAND_LABEL[island] || island)}</div>` +
          `<div>${escapeHtml(shortAxisName(xIndicator, state))}: <b>${escapeHtml(formatValue(x, xIndicator))}</b></div>` +
          ciTooltipLine(shortAxisName(xIndicator, state), xPrec, xIndicator) +
          `<div>${escapeHtml(shortAxisName(yIndicator, state))}: <b>${escapeHtml(formatValue(y, yIndicator))}</b></div>` +
          ciTooltipLine(shortAxisName(yIndicator, state), yPrec, yIndicator) +
          `<div>Population (2020 Census): ${escapeHtml(COUNT.format(pop))}</div>` +
          noteHtml
        );
      },
    },
    animationDurationUpdate: 700,
    animationEasingUpdate: "cubicInOut",
  };
}

function buildCompareSeries(story, data, state) {
  if (!state.compareYear || !story.panel_years.includes(state.compareYear)) return null;
  if (state.compareYear === state.year) return null;
  const rows = buildSeriesData(state.compareYear, story, data, state);
  // Render compare bubbles as ghosts: no labels, lower opacity, distinct border.
  return {
    id: "compare",
    type: "scatter",
    z: 0,
    silent: true,
    data: rows.map((r) => ({
      ...r,
      label: { show: false },
      itemStyle: {
        ...r.itemStyle,
        opacity: 0.18,
        borderColor: r.itemStyle.color,
        borderWidth: 2,
      },
      // ECharts will tween between series with same id; force a new id so the
      // compare overlay does not migrate the live bubbles when state changes.
      id: `cmp_${r.id}`,
    })),
  };
}

function buildCompareConnectors(year, story, data, state) {
  if (!state.compareYear || !story.panel_years.includes(state.compareYear)) return [];
  if (state.compareYear === year) return [];
  const out = [];
  for (const psgc of Object.keys(data.provinces)) {
    const live = pointFor(psgc, year, story, data, state);
    const ghost = pointFor(psgc, state.compareYear, story, data, state);
    if (!live || !ghost) continue;
    const color = PALETTE[live.island] || "#999";
    out.push({
      id: `cmp_link_${psgc}`,
      type: "line",
      name: `compare_link_${psgc}`,
      data: [
        [ghost.x, ghost.y],
        [live.x, live.y],
      ],
      symbol: "none",
      lineStyle: { color, width: 1, opacity: 0.35, type: "dotted" },
      tooltip: { show: false },
      silent: true,
      z: 0,
      animationDurationUpdate: 600,
    });
  }
  return out;
}

function buildOption(view, data, state) {
  if (state.chartType === "line") return buildLineOption(view, data, state);
  if (state.chartType === "bar") return buildBarOption(view, data, state);
  if (state.chartType === "map") return buildMapOption(view, data, state);
  return buildBubbleOption(view, data, state);
}

// Lazily-loaded province polygons, registered with ECharts once on first map use.
let PROVINCE_GEO = null;
let _mapLoading = false;
function ensureProvinceMap(onReady, onFail) {
  if (PROVINCE_GEO) {
    onReady();
    return;
  }
  if (_mapLoading) return;
  _mapLoading = true;
  fetchJson("data/ph-provinces.geojson")
    .then((geo) => {
      PROVINCE_GEO = geo;
      echarts.registerMap("ph-provinces", geo);
      _mapLoading = false;
      onReady();
    })
    .catch((e) => {
      _mapLoading = false;
      console.error("province map failed to load", e);
      if (onFail) onFail();
    });
}

function buildMapOption(view, data, state) {
  const yId = view.y;
  const yMeta = data.indicators[yId] || {};

  const rows = [];
  for (const psgc of Object.keys(data.provinces)) {
    const row = lookupRow(yId, psgc, state.year, data);
    const val = row ? indicatorValue(row, yId, state) : null;
    const name = data.provinces[psgc].name;
    if (val === null || val === undefined) {
      rows.push({ name, value: null, psgc });
    } else {
      const prec = state.deflate && DEFLATABLE_INDICATORS.has(yId) ? null : precisionOf(row);
      const imprecise = prec && prec.cv != null && prec.cv > CV_UNRELIABLE;
      rows.push({
        name,
        value: val,
        psgc,
        selected: state.sel.has(psgc),
        prec,
        // Amber outline marks provinces whose estimate is too imprecise to trust.
        ...(imprecise
          ? { itemStyle: { borderColor: "#c05621", borderWidth: 1.4, borderType: "dashed" } }
          : {}),
      });
    }
  }
  // Fixed global min/max across ALL years so a province's colour reflects real
  // change between years, not a scale that silently rescales each frame.
  const { min, max } = globalExtent(yId, data, state);

  return {
    // Faint year watermark (like bubble mode). The indicator name lives in the
    // picker pill top-left, so a centered title here would just collide with it
    // on narrow screens, so the watermark carries the year without overlapping.
    title: {
      text: `${state.year}`,
      left: "center",
      top: "middle",
      textStyle: { fontSize: 64, fontWeight: 700, color: "rgba(0,0,0,0.07)" },
    },
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(255,255,255,0.97)",
      borderColor: "#ddd",
      textStyle: { color: "#111" },
      formatter: (p) => {
        const v = p.data && p.data.value;
        const vs =
          v === null || v === undefined ? "no data" : escapeHtml(formatValue(v, yId));
        const prec = p.data && p.data.prec;
        return (
          `<div style="font-weight:600">${escapeHtml(p.name)}</div>` +
          `<div>${escapeHtml(yMeta.name || yId)}: <b>${vs}</b></div>` +
          ciTooltipLine(yMeta.name || yId, prec, yId)
        );
      },
    },
    visualMap: {
      min,
      max,
      calculable: true,
      // Right edge, vertically centered: clear of the play button (bottom-left),
      // the indicator picker (top-left), and the title (top-center).
      right: 16,
      top: "middle",
      orient: "vertical",
      itemHeight: 140,
      text: [formatValue(max, yId), formatValue(min, yId)],
      // Single-hue sequential ramp (light to dark blue). The mapped indicators
      // (poverty %, subsistence %, per-capita spend/GDP) are sequential magnitudes
      // with no meaningful zero-midpoint, so a diverging two-hue ramp would imply
      // a neutral centre that does not exist. Dark = more, light = less.
      inRange: { color: ["#dce8f5", "#7fa9d4", "#2b6cb0", "#08306b"] },
      textStyle: { color: "#595959", fontSize: 11 },
    },
    series: [
      {
        id: "map",
        type: "map",
        map: "ph-provinces",
        nameProperty: "name",
        roam: true,
        // Fit the (tall) archipelago inside the container instead of sizing by
        // width. Nudged left so the legend has the right margin to itself.
        layoutCenter: ["44%", "50%"],
        layoutSize: "100%",
        scaleLimit: { min: 1, max: 8 },
        selectedMode: "multiple",
        data: rows,
        itemStyle: { areaColor: "#eee", borderColor: "#fff", borderWidth: 0.5 },
        emphasis: { label: { show: false }, itemStyle: { areaColor: "#f6c453" } },
        select: {
          label: { show: false },
          itemStyle: { borderColor: "#111", borderWidth: 1.5, areaColor: null },
        },
      },
    ],
  };
}

function buildBubbleOption(story, data, state) {
  const years = story.panel_years;
  // Pre-compute the full trail set so base-option stubs and per-step series
  // agree on which series IDs exist. Auto-trails are added only when nothing
  // is user-selected; otherwise the chart focuses on the user's pick.
  const autoTrails =
    state.sel.size === 0 ? autoTrailProvinces(story, data) : [];
  const trailIds = [...state.sel, ...autoTrails];
  // CI whisker ids: two per selected province (x + y); empty on non-anchor years.
  const errIds = [];
  for (const psgc of state.sel) errIds.push(`err_y_${psgc}`, `err_x_${psgc}`);
  const compareSeries = buildCompareSeries(story, data, state);

  const xDeflatable = DEFLATABLE_INDICATORS.has(story.x);
  // For the base option we only need an empty stub per province for the connector
  // ids that may be active. Use union of every year's connectors so notMerge:true
  // re-attaches them on each step.
  const allConnectorIds = new Set();
  const stepOptions = years.map((year) => {
    const allTrails = buildTrails(year, story, data, state);
    const trailsByPsgc = new Map(
      allTrails.map((t) => [t.name.replace(/^trail_/, ""), t]),
    );
    const stepSeries = [{ id: "bubbles", data: buildSeriesData(year, story, data, state) }];
    for (const psgc of trailIds) {
      const t = trailsByPsgc.get(psgc);
      stepSeries.push(
        t || { id: `trail_${psgc}`, type: "line", data: [], symbol: "none" },
      );
    }
    if (compareSeries) {
      stepSeries.push({ id: "compare", data: compareSeries.data });
      const connectors = buildCompareConnectors(year, story, data, state);
      for (const c of connectors) {
        stepSeries.push(c);
        allConnectorIds.add(c.id);
      }
    }
    const errBars = buildErrorBars(year, story, data, state);
    for (const id of errIds) {
      stepSeries.push(errBars.get(id) || { id, type: "line", data: [], symbol: "none" });
    }
    const titleBlocks = [
      {
        text: compareSeries ? `${year} vs ${state.compareYear}` : `${year}`,
        left: "center",
        top: 10,
        textStyle: { fontSize: 48, fontWeight: 700, color: "rgba(0,0,0,0.08)" },
      },
    ];
    if (compareSeries) {
      titleBlocks.push({
        text: `Compare: ${state.compareYear}`,
        right: 28,
        top: 8,
        textStyle: {
          fontSize: 11,
          fontWeight: 600,
          color: "#595959",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Helvetica Neue', Helvetica, Arial, sans-serif",
        },
        backgroundColor: "rgba(247, 247, 248, 0.95)",
        borderColor: "#e6e6e6",
        borderWidth: 1,
        borderRadius: 99,
        padding: [3, 9],
      });
    }
    if (xDeflatable && state.deflate && year < CPI_BASE_YEAR) {
      titleBlocks.push({
        text:
          "Showing nominal PHP only. PSA CPI 2018-base does not cover this year, so 2018-real values cannot be computed.",
        left: "center",
        top: 4,
        textStyle: {
          fontSize: 12,
          fontWeight: 500,
          color: "#a13b2d",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Helvetica Neue', Helvetica, Arial, sans-serif",
        },
        backgroundColor: "rgba(252, 239, 234, 0.95)",
        borderColor: "#e6c0b5",
        borderWidth: 1,
        borderRadius: 4,
        padding: [4, 10],
      });
    }
    return {
      series: stepSeries,
      title: titleBlocks,
    };
  });

  const baseTrailStubs = trailIds.map((psgc) => ({
    id: `trail_${psgc}`,
    type: "line",
    data: [],
    symbol: "none",
    smooth: true,
    lineStyle: {
      color: PALETTE[data.provinces[psgc]?.island_group] || "#999",
      width: 1.5,
      opacity: 0.45,
    },
    tooltip: { show: false },
    animationDurationUpdate: 600,
    z: 1,
  }));

  return {
    baseOption: {
      ...baseOption(story, data, state),
      timeline: {
        axisType: "category",
        data: years,
        currentIndex: Math.max(0, years.indexOf(state.year)),
        autoPlay: false,
        playInterval: years.length <= 3 ? 1500 : 1100,
        loop: false,
        bottom: 14,
        left: 90,
        right: 28,
        symbol: "none",
        lineStyle: { color: "#ccc" },
        checkpointStyle: { color: "#111", borderColor: "#fff", borderWidth: 2 },
        controlStyle: {
          show: false,
          showNextBtn: false,
          showPrevBtn: false,
        },
        label: { color: "#595959", fontSize: 12 },
      },
      series: [
        {
          id: "bubbles",
          type: "scatter",
          data: [],
          emphasis: {
            focus: "self",
            scale: 1.2,
            label: { show: true, color: "#111", fontWeight: 600 },
          },
        },
        ...baseTrailStubs,
        ...errIds.map((id) => ({
          id,
          type: "line",
          data: [],
          symbol: "none",
          silent: true,
          z: 3,
        })),
        ...(compareSeries ? [{ id: "compare", type: "scatter", data: [], silent: true, z: 0 }] : []),
        ...[...allConnectorIds].map((id) => ({
          id,
          type: "line",
          data: [],
          symbol: "none",
          silent: true,
          z: 0,
        })),
      ],
    },
    options: stepOptions,
  };
}

// ---------- line chart (Y indicator over time, multi-province) ----------

function buildLineOption(view, data, state) {
  const years = view.panel_years;
  const yId = view.y;
  const yMeta = data.indicators[yId] || {};

  // Highlight selected + auto-trail. Other provinces are faded background context.
  const auto =
    state.sel.size === 0 ? new Set(autoTrailProvinces(view, data)) : new Set();
  const highlighted = new Set([...state.sel, ...auto]);

  const series = [];
  for (const psgc of Object.keys(data.provinces)) {
    const info = data.provinces[psgc];
    const color = PALETTE[info.island_group] || "#999";
    const pts = [];
    for (const y of years) {
      const row = lookupRow(yId, psgc, y, data);
      if (!row) {
        pts.push(null);
        continue;
      }
      const val = indicatorValue(row, yId, state);
      pts.push(val === null ? null : val);
    }
    // Drop provinces with no data for this indicator at all.
    if (pts.every((v) => v === null)) continue;
    const isHi = highlighted.has(psgc);
    series.push({
      id: `line_${psgc}`,
      name: info.name,
      type: "line",
      data: pts,
      symbol: isHi ? "circle" : "none",
      symbolSize: isHi ? 5 : 0,
      smooth: false,
      lineStyle: {
        color,
        width: isHi ? 2.2 : 1,
        opacity: isHi ? 0.85 : 0.26,
      },
      itemStyle: { color, opacity: isHi ? 0.95 : 0.4 },
      emphasis: {
        focus: "series",
        lineStyle: { width: 3, opacity: 1 },
      },
      endLabel: isHi
        ? {
            show: true,
            formatter: info.name,
            color: "#111",
            fontSize: 11,
            fontWeight: 600,
            // A white text halo (not an opaque box) keeps labels legible over the
            // faint lines without one label's box hiding a neighbour's text when
            // two converge (e.g. Sulu over Lanao del Sur near 13% in 2023-24).
            textBorderColor: "#fff",
            textBorderWidth: 3,
          }
        : { show: false },
      z: isHi ? 2 : 1,
      // De-collide end labels: when two highlighted lines converge (e.g. Sulu and
      // Lanao del Sur both near 13% poverty in 2023-24) their end labels stack and
      // overprint. shiftY nudges overlapping labels apart vertically.
      labelLayout: isHi ? { moveOverlap: "shiftY", hideOverlap: false } : undefined,
    });
  }

  return {
    grid: { left: 70, right: 120, top: 44, bottom: 60 },
    xAxis: {
      type: "category",
      data: years,
      boundaryGap: false,
      axisLine: { lineStyle: { color: "#ccc" } },
      axisTick: { show: false },
      axisLabel: { color: "#595959" },
      name: "Year",
      nameLocation: "middle",
      nameGap: 28,
      nameTextStyle: { fontSize: 11, color: "#6b6b6b", fontStyle: "italic" },
    },
    yAxis: {
      type: "value",
      name: shortAxisCaption(yId, state),
      nameLocation: "middle",
      nameGap: 56,
      nameTextStyle: { fontSize: 11, color: "#6b6b6b", fontStyle: "italic" },
      scale: yId === "poverty_change_pp",
      axisLabel: {
        color: "#595959",
        formatter: (v) => {
          if (yId === "poverty" || yId === "dpwh_share_pct" ||
              yId === "cpi_yoy_pct" || yId === "poverty_change_pp") {
            return v + "%";
          }
          if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
          if (v >= 1000) return (v / 1000).toFixed(0) + "k";
          return v.toString();
        },
      },
      splitLine: { show: true, lineStyle: { color: "#f0f0f0" } },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: "#bbb" } },
      backgroundColor: "rgba(255,255,255,0.97)",
      borderColor: "#ddd",
      textStyle: { color: "#111", fontSize: 12 },
      formatter: (params) => {
        if (!Array.isArray(params) || !params.length) return "";
        const year = params[0].axisValue;
        const visible = params
          .filter((p) => p.value !== null && p.value !== undefined)
          .sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity))
          .slice(0, 12);
        let html = `<div style="font-weight:600;margin-bottom:4px">${escapeHtml(String(year))}</div>`;
        for (const p of visible) {
          html += `<div><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.color};margin-right:6px;vertical-align:middle"></span>${escapeHtml(p.seriesName)}: <b>${escapeHtml(formatValue(p.value, yId))}</b></div>`;
        }
        if (params.length > visible.length) {
          html += `<div style="color:#6b6b6b;font-size:11px;margin-top:4px">... ${params.length - visible.length} more</div>`;
        }
        return html;
      },
    },
    title: {
      text: `${yMeta.name || yId} over time`,
      left: "center",
      top: 8,
      textStyle: { fontSize: 13, fontWeight: 600, color: "#444" },
    },
    series,
  };
}

// ---------- bar chart (Y indicator ranked at current year) ----------

function buildBarOption(view, data, state) {
  const yId = view.y;
  const yMeta = data.indicators[yId] || {};
  // On a phone the plot column is too narrow for the default ~5 percent ticks,
  // so they overprint into one smear. Ask for fewer and let ECharts hide any
  // that still collide.
  const narrow = window.innerWidth <= 520;

  // Collect (psgc, value) for the current year, drop null.
  const rows = [];
  for (const psgc of Object.keys(data.provinces)) {
    const row = lookupRow(yId, psgc, state.year, data);
    if (!row) continue;
    const val = indicatorValue(row, yId, state);
    if (val === null) continue;
    const prec = state.deflate && DEFLATABLE_INDICATORS.has(yId) ? null : precisionOf(row);
    rows.push({ psgc, name: data.provinces[psgc].name, value: val,
      island: data.provinces[psgc].island_group, prec });
  }
  rows.sort((a, b) => b.value - a.value);
  const precByName = new Map(rows.map((r) => [r.name, r.prec]));

  return {
    grid: { left: 170, right: 80, top: 48, bottom: 40 },
    xAxis: {
      type: "value",
      name: shortAxisCaption(yId, state),
      nameLocation: "middle",
      nameGap: 28,
      nameTextStyle: { fontSize: 11, color: "#6b6b6b", fontStyle: "italic" },
      scale: yId === "poverty_change_pp",
      splitNumber: narrow ? 3 : 5,
      axisLabel: {
        color: "#595959",
        hideOverlap: true,
        formatter: (v) => {
          if (yId === "poverty" || yId === "dpwh_share_pct" ||
              yId === "cpi_yoy_pct" || yId === "poverty_change_pp") {
            return v + "%";
          }
          if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
          if (v >= 1000) return (v / 1000).toFixed(0) + "k";
          return v.toString();
        },
      },
      splitLine: { show: true, lineStyle: { color: "#f0f0f0" } },
    },
    yAxis: {
      type: "category",
      data: rows.map((r) => r.name),
      inverse: true,
      axisLine: { lineStyle: { color: "#ccc" } },
      axisTick: { show: false },
      axisLabel: { color: "#444", fontSize: 11 },
    },
    title: {
      text: `${state.year}`,
      left: "center",
      top: "middle",
      textStyle: { fontSize: 64, fontWeight: 700, color: "rgba(0,0,0,0.07)" },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: "rgba(255,255,255,0.97)",
      borderColor: "#ddd",
      textStyle: { color: "#111" },
      formatter: (params) => {
        if (!Array.isArray(params) || !params.length) return "";
        const p = params[0];
        return `<div style="font-weight:600">${escapeHtml(p.name)}</div>` +
          `<div>${escapeHtml(yMeta.name || yId)}: <b>${escapeHtml(formatValue(p.value, yId))}</b></div>` +
          ciTooltipLine(yMeta.name || yId, precByName.get(p.name), yId);
      },
    },
    series: [
      {
        id: "ranks",
        type: "bar",
        data: rows.map((r) => ({
          value: r.value,
          itemStyle: { color: PALETTE[r.island] || "#999", opacity: 0.92 },
        })),
        // No fixed barWidth: let ECharts size bars to fit all 82 areas in
        // the column (a fixed 12px overflowed and clipped the bottom ~37).
        // Per-bar value labels are off (82 would overlap illegibly); the value
        // shows on hover via the axis tooltip, and bar length encodes it.
        label: { show: false },
        emphasis: {
          itemStyle: { opacity: 1 },
          label: {
            show: true,
            position: "right",
            distance: 4,
            color: "#444",
            fontSize: 11,
            formatter: (p) => formatValue(p.value, yId),
          },
        },
      },
    ],
  };
}

// ---------- URL hash state ----------

function parseHash(stories) {
  const h = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(h);
  const storyId = params.get("story") || (stories[0] && stories[0].id);
  const story = stories.find((s) => s.id === storyId) || stories[0];
  const year = parseInt(params.get("year"), 10);
  const cmp = parseInt(params.get("cmp"), 10);
  const logX = params.get("log");
  const sel = (params.get("sel") || "").split(",").filter(Boolean);
  const deflate = params.get("deflate");
  const xParam = params.get("x");
  const yParam = params.get("y");
  const ctParam = (params.get("ct") || "bubbles").toLowerCase();
  const chartType = ["bubbles", "line", "bar", "map"].includes(ctParam) ? ctParam : "bubbles";
  const extrap = params.get("extrap");
  return {
    story,
    xIndicator: xParam || story.x,
    yIndicator: yParam || story.y,
    chartType,
    year: story && story.panel_years.includes(year) ? year : (story && story.default_year),
    compareYear: story && story.panel_years.includes(cmp) ? cmp : null,
    logX: logX === null ? !!(story && story.default_log_x) : logX === "x",
    sel: new Set(sel),
    deflate: deflate === null ? true : deflate === "real",
    extrapolate: extrap === "on",
    hadHash: h.length > 0,
  };
}

function writeHash(state, view) {
  const params = new URLSearchParams();
  params.set("story", state.story.id);
  // Round-trip custom indicator picks only when they diverge from the preset.
  if (view && view.isCustom) {
    params.set("x", view.x);
    params.set("y", view.y);
  }
  if (state.chartType && state.chartType !== "bubbles") {
    params.set("ct", state.chartType);
  }
  if (state.extrapolate) {
    params.set("extrap", "on");
  }
  params.set("year", state.year);
  params.set("log", state.logX ? "x" : "none");
  params.set("deflate", state.deflate ? "real" : "nominal");
  if (state.compareYear) params.set("cmp", state.compareYear);
  if (state.sel.size) params.set("sel", [...state.sel].join(","));
  window.history.replaceState(null, "", "#" + params.toString());
}

// ---------- search ----------

function wireSearch(input, data, state, render) {
  const matches = (q) => {
    const lower = q.trim().toLowerCase();
    if (!lower) return [];
    return Object.entries(data.provinces)
      .filter(([, info]) => info.name.toLowerCase().includes(lower))
      .map(([psgc, info]) => ({ psgc, name: info.name }))
      .slice(0, 6);
  };

  const list = document.getElementById("search-results");

  const updateList = () => {
    const q = input.value;
    const ms = matches(q);
    list.replaceChildren();
    for (const m of ms) {
      const li = document.createElement("li");
      const selected = state.sel.has(m.psgc);
      li.textContent = m.name + (selected ? "  ✓" : "");
      li.dataset.psgc = m.psgc;
      li.tabIndex = 0;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", selected ? "true" : "false");
      const choose = () => {
        toggleSel(m.psgc, state, render);
        input.value = "";
        list.replaceChildren();
        input.focus();
      };
      li.addEventListener("click", choose);
      li.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          choose();
        }
      });
      list.appendChild(li);
    }
  };

  input.addEventListener("input", updateList);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const ms = matches(input.value);
      if (ms.length) {
        toggleSel(ms[0].psgc, state, render);
        input.value = "";
        list.replaceChildren();
      }
    } else if (e.key === "Escape") {
      input.value = "";
      list.replaceChildren();
      input.blur();
    }
  });
}

function toggleSel(psgc, state, render) {
  if (state.sel.has(psgc)) state.sel.delete(psgc);
  else state.sel.add(psgc);
  render();
}

// On phones, move the finding + caveat below the chart so the chart lands at
// the fold instead of a screenful of text first. Desktop keeps them in the
// title strip. The elements are populated by id, so relocating them is safe.
function syncDetailPlacement() {
  const finding = document.getElementById("story-finding");
  const caveat = document.getElementById("story-caveat");
  const slot = document.getElementById("mobile-detail-slot");
  const titleText = document.querySelector(".title-text");
  if (!finding || !caveat || !slot || !titleText) return;
  const wantSlot = window.matchMedia("(max-width: 879px)").matches;
  const inSlot = finding.parentElement === slot;
  if (wantSlot && !inSlot) {
    slot.appendChild(finding);
    slot.appendChild(caveat);
  } else if (!wantSlot && inSlot) {
    titleText.appendChild(finding);
    titleText.appendChild(caveat);
  }
}

function renderSelChips(state, data, render) {
  const root = document.getElementById("selected-chips");
  const hint = document.getElementById("selected-hint");
  root.replaceChildren();
  for (const psgc of state.sel) {
    const info = data.provinces[psgc];
    if (!info) continue;
    const color = PALETTE[info.island_group] || "#999";
    const ink = readableInk(color);
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.style.background = color;
    chip.style.color = ink;
    chip.appendChild(document.createTextNode(info.name + " "));
    const btn = document.createElement("button");
    btn.setAttribute("aria-label", `remove ${info.name}`);
    btn.style.color = ink;
    btn.textContent = "×";
    btn.addEventListener("click", () => {
      state.sel.delete(psgc);
      render();
    });
    chip.appendChild(btn);
    root.appendChild(chip);
  }
  if (hint) hint.style.display = state.sel.size ? "none" : "";
}

// ---------- year + compare controls ----------

function renderYearControls(state, view, render) {
  const display = document.getElementById("year-display");
  if (display) display.textContent = String(state.year);

  // Native range slider: an AT-accessible year control (real slider role +
  // aria-valuetext), mirroring the stepper buttons and arrow-key scrubbing.
  const range = document.getElementById("year-range");
  if (range) {
    const years = view.panel_years;
    const idx = Math.max(0, years.indexOf(state.year));
    range.max = String(Math.max(0, years.length - 1));
    range.value = String(idx);
    range.setAttribute("aria-valuetext", String(state.year));
    range.oninput = () => {
      const y = years[parseInt(range.value, 10)];
      if (y !== undefined && y !== state.year) {
        state.year = y;
        render();
      }
    };
  }

  const select = document.getElementById("compare-select");
  if (!select) return;
  const current = state.compareYear || "";
  select.innerHTML = "";
  const off = document.createElement("option");
  off.value = "";
  off.textContent = "Off";
  select.appendChild(off);
  for (const y of view.panel_years) {
    if (y === state.year) continue;
    const opt = document.createElement("option");
    opt.value = String(y);
    opt.textContent = String(y);
    if (y === state.compareYear) opt.selected = true;
    select.appendChild(opt);
  }
  select.value = current ? String(current) : "";
  select.onchange = () => {
    const v = parseInt(select.value, 10);
    state.compareYear = Number.isFinite(v) ? v : null;
    render();
  };
}

function stepYear(state, delta, render) {
  const years = (state.view && state.view.panel_years) || state.story.panel_years;
  const idx = years.indexOf(state.year);
  const next = years[idx + delta];
  if (next === undefined) return;
  state.year = next;
  render();
}

// Award-based spend indicators: PhilGEPS awards, not disbursement.
const AWARDS_INDICATORS = new Set([
  "dpwh_spend_per_capita",
  "all_spend_per_capita",
  "doh_spend_per_capita",
  "infra_spend_per_capita",
]);

// Axis-level caveats for the indicators actually plotted: the awards/disbursement
// gap, single-snapshot indicators that don't vary by year, and short panels.
// Returned as {tag, text} so the renderer can bold the tag.
function activeCaveats(view, data, state) {
  const ids = state.chartType === "bubbles" ? [view.x, view.y] : [view.y];
  const out = [];
  if (ids.some((id) => AWARDS_INDICATORS.has(id))) {
    const txt = (data.stories.find((s) => s.awards_caveat) || {}).awards_caveat;
    if (txt) out.push({ tag: "Awards, not disbursement", text: txt });
  }
  for (const id of ids) {
    const m = data.indicators[id] || {};
    if (m.static_snapshot && m.snapshot_label) {
      out.push({ tag: m.name, text: m.snapshot_label });
    } else if (m.coverage_label) {
      out.push({ tag: m.name, text: m.coverage_label });
    }
  }
  return out;
}

// ---------- story switcher UI ----------

function renderStorySwitcher(stories, state, view, render) {
  const nav = document.getElementById("story-switcher");
  nav.replaceChildren();
  // A preset tab counts as active only when the user has not deviated from
  // its X/Y picks (i.e. view is not custom AND its story id matches).
  const activeId = view.isCustom ? null : state.story.id;
  for (const s of stories) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("aria-pressed", s.id === activeId ? "true" : "false");
    btn.className = "story-btn" + (s.id === activeId ? " active" : "");
    btn.textContent = s.tab_label || s.headline.split(".")[0];
    btn.addEventListener("click", () => {
      state.story = s;
      state.xIndicator = s.x;
      state.yIndicator = s.y;
      state.year = s.default_year;
      state.logX = !!s.default_log_x;
      render();
    });
    nav.appendChild(btn);
  }
  // Append a "Custom" pill when the user has gone off-preset.
  if (view.isCustom) {
    const tag = document.createElement("span");
    tag.className = "story-btn custom-tag active";
    tag.textContent = "Custom";
    tag.setAttribute("aria-label", "Custom indicator selection");
    nav.appendChild(tag);
  }
}

// ---------- indicator picker (Gapminder X/Y dropdowns) ----------

function renderIndicatorPickers(state, view, data, render) {
  const allIndicators = Object.values(data.indicators);
  const wireOne = (selectId, currentId, otherId, onChange) => {
    const select = document.getElementById(selectId);
    if (!select) return;
    // Build options once. On subsequent renders just mutate enabled/selected,
    // so Playwright (and screen readers) don't see flickering DOM children.
    if (select.options.length === 0) {
      for (const ind of allIndicators) {
        if (ind.national_only) continue; // no per-province variation; not an axis choice
        const opt = document.createElement("option");
        opt.value = ind.id;
        opt.textContent = ind.name;
        select.appendChild(opt);
      }
      select.onchange = () => {
        onChange(select.value);
        render();
      };
    }
    for (const opt of select.options) {
      opt.disabled = opt.value === otherId;
    }
    select.value = currentId;
  };
  wireOne("y-select", view.y, view.x, (val) => {
    state.yIndicator = val;
  });
  wireOne("x-select", view.x, view.y, (val) => {
    state.xIndicator = val;
  });
}

// ---------- sr-only data table ----------

function renderSrTable(story, data, state) {
  const root = document.getElementById("chart-sr-table");
  const rows = [];
  for (const psgc of Object.keys(data.provinces)) {
    const p = pointFor(psgc, state.year, story, data, state);
    if (p) rows.push(p);
  }
  rows.sort((a, b) => a.name.localeCompare(b.name));

  // Concise live summary (the only part announced on year change). Highest/
  // lowest are by the Y indicator the chart foregrounds.
  // Skip the live announcement while autoplay is running: one polite message
  // per ~1.1s frame floods a screen reader. The final/paused frame is announced
  // when the loop stops (stopPlay calls render with the flag cleared).
  const summary = document.getElementById("sr-summary");
  if (summary && !IS_AUTOPLAYING) {
    const yName = data.indicators[story.y] ? data.indicators[story.y].name : story.y;
    if (rows.length) {
      const byY = [...rows].sort((a, b) => b.y - a.y);
      const hi = byY[0];
      const lo = byY[byY.length - 1];
      summary.textContent =
        `${state.year}: ${yName}, ${rows.length} areas. ` +
        `Highest ${hi.name} ${formatValue(hi.y, story.y)}, ` +
        `lowest ${lo.name} ${formatValue(lo.y, story.y)}.`;
    } else {
      summary.textContent = `${state.year}: no data for this combination.`;
    }
  }

  const tbl = document.createElement("table");
  const cap = document.createElement("caption");
  cap.textContent = `${story.headline} Year ${state.year}, ${rows.length} areas.`;
  tbl.appendChild(cap);
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  for (const h of [
    "Province",
    "Island group",
    shortAxisName(story.x, state),
    shortAxisName(story.y, state),
    "Population (2020)",
  ]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = h;
    trh.appendChild(th);
  }
  thead.appendChild(trh);
  tbl.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const r of rows) {
    const tr = document.createElement("tr");
    for (const cell of [
      r.name,
      ISLAND_LABEL[r.island] || r.island,
      formatValue(r.x, story.x),
      formatValue(r.y, story.y),
      COUNT.format(r.pop),
    ]) {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  tbl.appendChild(tbody);
  root.replaceChildren(tbl);
}

// ---------- CSV download ----------

function csvEscape(v) {
  const s = String(v);
  if (/[",\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function downloadCsv(story, data, state) {
  const headers = [
    "psgc",
    "province",
    "island_group",
    "year",
    "x_indicator",
    "x_value",
    "x_unit",
    "y_indicator",
    "y_value",
    "y_unit",
    "population_2020",
    "interpolated",
    "extrapolated",
  ];
  const rows = [headers.join(",")];
  for (const psgc of Object.keys(data.provinces)) {
    const p = pointFor(psgc, state.year, story, data, state);
    if (!p) continue;
    rows.push(
      [
        p.psgc,
        p.name,
        ISLAND_LABEL[p.island] || p.island,
        p.year,
        story.x,
        p.x,
        unitFor(story.x, state),
        story.y,
        p.y,
        unitFor(story.y, state),
        p.pop,
        p.interp ? "true" : "false",
        p.extrap ? "true" : "false",
      ]
        .map(csvEscape)
        .join(","),
    );
  }
  const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `dataviz-ph-${story.id}-${state.year}.csv`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    a.remove();
    URL.revokeObjectURL(url);
  }, 0);
}

// ---------- axis pickers (Gapminder-style: click axis label, pick indicator) ----------

function attachAxisInfoButtons(chart, view, data, chartType) {
  // Overlay a clickable picker pill on each axis. The pill shows the current
  // indicator name + a "▼" affordance; clicking opens an indicator panel.
  // A small "i" badge alongside still opens the definition popover.
  const root = document.getElementById("chart");
  if (!root) return;
  let layer = document.getElementById("axis-info-layer");
  if (!layer) {
    layer = document.createElement("div");
    layer.id = "axis-info-layer";
    root.appendChild(layer);
  }
  layer.replaceChildren();

  const place = (kind, indicatorId, otherId, position) => {
    const info = data.indicators && data.indicators[indicatorId];
    if (!info) return;
    const group = document.createElement("div");
    group.className = `axis-picker axis-picker-${kind}`;
    group.style.position = "absolute";
    Object.assign(group.style, position);

    const pick = document.createElement("button");
    pick.type = "button";
    pick.id = `axis-pick-${kind}`;
    pick.className = "axis-pick-btn";
    pick.setAttribute("aria-haspopup", "listbox");
    pick.setAttribute("aria-label", `${kind === "x" ? "X" : "Y"} axis indicator: ${info.name}. Click to change.`);
    pick.innerHTML = `<span class="axis-pick-name"></span><span class="axis-pick-caret">▾</span>`;
    pick.querySelector(".axis-pick-name").textContent = info.name;
    pick.addEventListener("click", (e) => {
      e.stopPropagation();
      showIndicatorPanel(pick, kind, indicatorId, otherId, data);
    });

    const inf = document.createElement("button");
    inf.type = "button";
    inf.id = `axis-info-${kind}`;
    inf.className = "axis-info-btn";
    inf.setAttribute("aria-label", `${info.name}: definition and source`);
    inf.textContent = "i";
    inf.addEventListener("click", (e) => {
      e.stopPropagation();
      showAxisPopover(inf, info);
    });

    group.appendChild(pick);
    group.appendChild(inf);
    layer.appendChild(group);
  };

  if (chartType === "bubbles") {
    // X picker: bottom-center, below ticks, above the italic caption.
    place("x", view.x, view.y, {
      left: "50%",
      bottom: "126px",
      transform: "translateX(-50%)",
    });
    // Y picker: top-left, horizontal text.
    place("y", view.y, view.x, { left: "12px", top: "8px" });
  } else if (chartType === "line") {
    // Line: X is year (locked). Only Y is choosable, top-left.
    place("y", view.y, view.x, { left: "12px", top: "8px" });
  } else if (chartType === "bar") {
    // Bar (ranks): only Y is choosable. Top-left (like line/map) so it never
    // collides with the X-axis caption at the bottom on narrow screens.
    place("y", view.y, view.x, { left: "12px", top: "8px" });
  } else if (chartType === "map") {
    // Map colours a single indicator (the Y) across provinces. Picker top-left.
    place("y", view.y, view.x, { left: "12px", top: "8px" });
  }
}

// Indicator selection panel: list of all indicators, search, click to pick.
let _indicatorPanelOpenFor = null;
let _indicatorPanelTrigger = null;

function showIndicatorPanel(anchorBtn, kind, currentId, otherId, data) {
  closeIndicatorPanel();
  _indicatorPanelOpenFor = kind;
  _indicatorPanelTrigger = anchorBtn;
  const root = document.getElementById("chart");
  if (!root) return;
  const panel = document.createElement("div");
  panel.id = "indicator-panel";
  panel.className = `indicator-panel indicator-panel-${kind}`;
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", `${kind === "x" ? "X" : "Y"} axis indicator picker`);

  const header = document.createElement("div");
  header.className = "ipanel-head";
  const title = document.createElement("strong");
  title.textContent = kind === "x" ? "X axis" : "Y axis";
  header.appendChild(title);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "ipanel-close";
  close.setAttribute("aria-label", "close");
  close.textContent = "×";
  close.addEventListener("click", closeIndicatorPanel);
  header.appendChild(close);
  panel.appendChild(header);

  const search = document.createElement("input");
  search.type = "search";
  search.className = "ipanel-search";
  search.placeholder = "Filter indicators";
  search.setAttribute("aria-label", "filter indicators by name");
  panel.appendChild(search);

  const list = document.createElement("ul");
  list.className = "ipanel-list";
  list.setAttribute("role", "listbox");
  panel.appendChild(list);

  const allIndicators = Object.values(data.indicators);
  function renderList(filter = "") {
    list.replaceChildren();
    const q = filter.trim().toLowerCase();
    const filtered = allIndicators.filter(
      (i) =>
        !i.national_only &&
        (!q || i.name.toLowerCase().includes(q) || (i.unit || "").toLowerCase().includes(q)),
    );
    for (const ind of filtered) {
      const li = document.createElement("li");
      li.className = "ipanel-item";
      li.setAttribute("role", "option");
      const sameAsOther = ind.id === otherId;
      const isCurrent = ind.id === currentId;
      if (isCurrent) li.classList.add("active");
      if (sameAsOther) li.classList.add("disabled");
      li.setAttribute("aria-selected", isCurrent ? "true" : "false");
      const name = document.createElement("div");
      name.className = "ipanel-name";
      name.textContent = ind.name;
      const meta = document.createElement("div");
      meta.className = "ipanel-meta";
      const unit = ind.unit ? ind.unit : "";
      const flag = ind.national_only ? " · national only" : "";
      meta.textContent = unit + flag;
      li.appendChild(name);
      if (unit || flag) li.appendChild(meta);
      if (!sameAsOther) {
        li.tabIndex = 0;
        li.addEventListener("click", () => {
          _indicatorPickHandler(kind, ind.id);
          closeIndicatorPanel();
        });
        li.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            _indicatorPickHandler(kind, ind.id);
            closeIndicatorPanel();
          }
        });
      }
      list.appendChild(li);
    }
  }
  renderList();
  search.addEventListener("input", () => renderList(search.value));

  // Position the panel near the anchor, clamped to chart bounds.
  const aRect = anchorBtn.getBoundingClientRect();
  const rRect = root.getBoundingClientRect();
  panel.style.position = "absolute";
  panel.style.left = `${Math.max(12, Math.min(aRect.left - rRect.left, rRect.width - 340))}px`;
  if (kind === "x") {
    // anchor below the X picker (above the timeline) - place ABOVE the pill
    panel.style.bottom = `${rRect.bottom - aRect.top + 6}px`;
  } else {
    // Y picker is rotated; place panel to the right of where the un-rotated text
    // would sit, near the top-left of the chart
    panel.style.top = `${Math.max(12, aRect.top - rRect.top)}px`;
  }
  panel.style.maxHeight = `${Math.min(rRect.height - 24, 520)}px`;

  root.appendChild(panel);
  setTimeout(() => search.focus(), 0);
  setTimeout(() => {
    document.addEventListener("click", _outsideIndicatorClick, { capture: true });
    document.addEventListener("keydown", _escIndicatorClose);
  }, 0);
}

function _outsideIndicatorClick(e) {
  const panel = document.getElementById("indicator-panel");
  if (panel && !panel.contains(e.target)) closeIndicatorPanel();
}

function _escIndicatorClose(e) {
  if (e.key === "Escape") closeIndicatorPanel();
}

function closeIndicatorPanel() {
  const panel = document.getElementById("indicator-panel");
  if (panel) panel.remove();
  _indicatorPanelOpenFor = null;
  document.removeEventListener("click", _outsideIndicatorClick, { capture: true });
  document.removeEventListener("keydown", _escIndicatorClose);
  // Restore focus to the picker pill that opened the panel (WCAG 2.4.3).
  // No-op if it was detached by a re-render after a selection.
  if (_indicatorPanelTrigger && document.contains(_indicatorPanelTrigger)) {
    _indicatorPanelTrigger.focus();
  }
  _indicatorPanelTrigger = null;
}

// Bound from main() so the panel has access to render() + state mutation.
let _indicatorPickHandler = () => {};

// Restore focus here when the definition popover closes (WCAG 2.4.3).
let _axisPopoverTrigger = null;

function showAxisPopover(anchorBtn, info) {
  closeAxisPopover();
  _axisPopoverTrigger = anchorBtn;
  const root = document.getElementById("chart");
  if (!root) return;
  const pop = document.createElement("div");
  pop.id = "axis-popover";
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-modal", "false");
  const title = document.createElement("h3");
  title.textContent = info.name;
  pop.appendChild(title);
  const body = document.createElement("p");
  body.textContent = info.definition || info.vintage || "";
  pop.appendChild(body);
  if (info.source) {
    const meta = document.createElement("p");
    meta.className = "axis-popover-meta";
    meta.textContent = `Source: ${info.source}`;
    pop.appendChild(meta);
  }
  if (info.source_url) {
    const link = document.createElement("a");
    link.href = info.source_url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Open source";
    pop.appendChild(link);
  }
  const close = document.createElement("button");
  close.type = "button";
  close.className = "axis-popover-close";
  close.setAttribute("aria-label", "close definition");
  close.textContent = "×";
  close.addEventListener("click", closeAxisPopover);
  pop.appendChild(close);

  // Position next to the anchor button; clamp inside the chart bounds.
  const aRect = anchorBtn.getBoundingClientRect();
  const rRect = root.getBoundingClientRect();
  pop.style.position = "absolute";
  pop.style.left = `${Math.max(12, aRect.left - rRect.left)}px`;
  pop.style.top = `${Math.max(12, aRect.top - rRect.top + aRect.height + 6)}px`;
  pop.style.maxWidth = `${Math.min(360, rRect.width - 24)}px`;

  root.appendChild(pop);

  // Dismiss on outside click or Escape
  setTimeout(() => {
    document.addEventListener("click", _outsideAxisClick, { capture: true });
    document.addEventListener("keydown", _escAxisClose);
  }, 0);
}

function _outsideAxisClick(e) {
  const pop = document.getElementById("axis-popover");
  if (pop && !pop.contains(e.target)) closeAxisPopover();
}

function _escAxisClose(e) {
  if (e.key === "Escape") closeAxisPopover();
}

function closeAxisPopover() {
  const pop = document.getElementById("axis-popover");
  if (pop) pop.remove();
  document.removeEventListener("click", _outsideAxisClick, { capture: true });
  document.removeEventListener("keydown", _escAxisClose);
  if (_axisPopoverTrigger && document.contains(_axisPopoverTrigger)) {
    _axisPopoverTrigger.focus();
  }
  _axisPopoverTrigger = null;
}

// ---------- last-tapped panel (mobile) ----------

function renderLastTapPanel(seriesPoint, state) {
  const panel = document.getElementById("last-tap-panel");
  if (!panel) return;
  if (!seriesPoint || !seriesPoint.value || seriesPoint.value.length < 8) {
    panel.replaceChildren();
    return;
  }
  const [x, y, pop, name, year, interp, island, extrap] = seriesPoint.value;
  const color = PALETTE[island] || "#999";
  const v = state.view || state.story;
  const xLabel = shortAxisName(v.x, state);
  const yLabel = shortAxisName(v.y, state);
  let noteText = "";
  if (extrap || interp) {
    const yKey = v.y;
    const friendly = yKey === "poverty" ? "Poverty" : "Y value";
    noteText = extrap
      ? `${friendly} held constant from nearest PSA anchor`
      : `${friendly} linearly interpolated between PSA anchors`;
  }
  panel.replaceChildren();
  const head = document.createElement("div");
  head.className = "ltp-head";
  const dot = document.createElement("span");
  dot.className = "ltp-dot";
  dot.style.background = color;
  head.appendChild(dot);
  const headText = document.createElement("strong");
  headText.textContent = `${name} · ${year}`;
  head.appendChild(headText);
  const island_lbl = document.createElement("span");
  island_lbl.className = "ltp-island";
  island_lbl.textContent = ISLAND_LABEL[island] || island;
  head.appendChild(island_lbl);
  panel.appendChild(head);
  const rows = [
    [xLabel, formatValue(x, v.x)],
    [yLabel, formatValue(y, v.y)],
    ["Population (2020)", COUNT.format(pop)],
  ];
  for (const [k, v] of rows) {
    const row = document.createElement("div");
    row.className = "ltp-row";
    const ks = document.createElement("span");
    ks.className = "ltp-k";
    ks.textContent = k;
    const vs = document.createElement("span");
    vs.className = "ltp-v";
    vs.textContent = v;
    row.appendChild(ks);
    row.appendChild(vs);
    panel.appendChild(row);
  }
  if (noteText) {
    const note = document.createElement("div");
    note.className = "ltp-note";
    note.textContent = noteText;
    panel.appendChild(note);
  }
}

// ---------- boot ----------

// Peso figure at a readable scale (trillions / billions / millions).
function formatPesoScale(v) {
  if (v >= 1e12) return `PHP ${(v / 1e12).toFixed(2)} trillion`;
  if (v >= 1e9) return `PHP ${Math.round(v / 1e9)} billion`;
  if (v >= 1e6) return `PHP ${Math.round(v / 1e6)} million`;
  return `PHP ${COUNT.format(Math.round(v))}`;
}

// National DPWH award trajectory, rebuilt from per-capita x 2020 population (the
// exact figures the chart plots), so the methodology line can never drift.
function computeDpwhSurge(data) {
  const rows = data.indicatorRows && data.indicatorRows.dpwh_spend_per_capita;
  const prov = data.provinces;
  if (!rows || !prov) return null;
  const yearTot = {};
  let total = 0;
  for (const key in rows) {
    const r = rows[key];
    const p = prov[r.psgc];
    if (!p || r.value == null) continue;
    const award = r.value * p.population_2020;
    yearTot[r.year] = (yearTot[r.year] || 0) + award;
    total += award;
  }
  const years = Object.keys(yearTot).map(Number);
  if (!years.length) return null;
  const avg = (yy) => {
    const vals = yy.map((y) => yearTot[y]).filter((v) => v != null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  };
  let peakYr = years[0];
  for (const y of years) if (yearTot[y] > yearTot[peakYr]) peakYr = y;
  const early = avg([2014, 2015, 2016]);
  const late = avg([2022, 2023, 2024]);
  return { total, early, late, ratio: early ? late / early : null, peakYr, peakVal: yearTot[peakYr] };
}

// Fill the methodology "scale and trend" line from the computed trajectory.
function renderScaleContext(data) {
  const el = document.getElementById("methodology-scale");
  const s = computeDpwhSurge(data);
  if (!el || !s || s.early == null || s.ratio == null) return;
  const set = (k, v) => {
    const n = el.querySelector(`[data-ctx="${k}"]`);
    if (n) n.textContent = v;
  };
  set("total", formatPesoScale(s.total));
  set("early", formatPesoScale(s.early));
  set("peak", formatPesoScale(s.peakVal));
  set("peakyr", String(s.peakYr));
  set("ratio", `${s.ratio.toFixed(1)} times`);
  el.hidden = false;
}

async function main() {
  const root = document.getElementById("chart");
  const loading = document.getElementById("loading");
  const data = await loadData();
  if (loading) loading.remove();

  const initial = parseHash(data.stories);
  const state = {
    story: initial.story,
    xIndicator: initial.xIndicator,
    yIndicator: initial.yIndicator,
    chartType: initial.chartType,
    year: initial.year,
    compareYear: initial.compareYear,
    logX: initial.logX,
    sel: initial.sel,
    deflate: initial.deflate,
    extrapolate: initial.extrapolate,
    view: null,
    howtoDismissed: readHowtoDismissed(),
  };

  const chart = echarts.init(root, null, { renderer: "canvas" });
  renderFreshness(data.manifest);
  renderScaleContext(data);

  // Wire the axis-picker panel selection back into state.
  _indicatorPickHandler = (kind, id) => {
    if (kind === "x") state.xIndicator = id;
    else state.yIndicator = id;
    render();
  };

  let rendering = false;
  function render() {
    if (rendering) return;
    rendering = true;
    try {
      // Compute the active view (preset overlaid with any custom indicator picks).
      // Clamp year to the view's effective panel before any sub-render uses it.
      const view = makeView(state, data);
      state.view = view;
      if (!view.panel_years.includes(state.year)) {
        state.year = view.default_year;
      }
      if (state.compareYear && !view.panel_years.includes(state.compareYear)) {
        state.compareYear = null;
      }

      const xIndicator = data.indicators[view.x];
      // Headline + tagline update with view (preset or custom)
      document.getElementById("story-headline").textContent = view.headline;
      document.getElementById("story-tagline").textContent = view.tagline;
      // Per-story why + source link: hide on custom views (preset copy doesn't apply)
      const whyEl = document.getElementById("story-why");
      if (whyEl) {
        whyEl.textContent = view.isCustom ? "" : view.why || "";
        whyEl.hidden = view.isCustom || !view.why;
      }
      const srcEl = document.getElementById("story-source");
      if (srcEl) {
        if (!view.isCustom && view.source_url) {
          srcEl.href = view.source_url;
          srcEl.hidden = false;
        } else {
          srcEl.hidden = true;
        }
      }
      // Computed finding: the data-grounded answer to the story's question.
      // Preset views only (custom picks have no precomputed correlation).
      const findingEl = document.getElementById("story-finding");
      if (findingEl) {
        const f = !view.isCustom ? view.finding : null;
        if (f && f.available && f.sentence) {
          // On the DPWH-vs-poverty story, set the non-result against the one
          // pairing that does track poverty: per-capita GDP. The contrast is the
          // point, so pull the GDP story's own computed rho (no hardcoded number).
          let contrast = "";
          if (state.story && state.story.id === "spend-vs-poverty") {
            const g = (data.stories || []).find((s) => s.id === "gdp-vs-poverty");
            const gs = g && g.finding && typeof g.finding.spearman === "number" ? g.finding.spearman : null;
            if (gs !== null) {
              const r = (Math.sign(gs) * Math.round(Math.abs(gs) * 100)) / 100;
              contrast = ` Per-capita GDP, by contrast, does track lower poverty (rho = ${r >= 0 ? "+" : ""}${r.toFixed(2)}).`;
            }
          }
          findingEl.textContent = `What the data shows. ${f.sentence}${contrast} ${f.caveat || ""}`;
          findingEl.hidden = false;
        } else {
          findingEl.textContent = "";
          findingEl.hidden = true;
        }
      }
      // Axis caveats: awards-not-disbursement, single-snapshot, short-panel.
      const caveatEl = document.getElementById("story-caveat");
      if (caveatEl) {
        const cavs = activeCaveats(view, data, state);
        caveatEl.replaceChildren();
        for (const c of cavs) {
          const line = document.createElement("div");
          line.className = "caveat-line";
          const tag = document.createElement("strong");
          tag.textContent = `${c.tag}: `;
          line.appendChild(tag);
          line.appendChild(document.createTextNode(c.text));
          caveatEl.appendChild(line);
        }
        caveatEl.hidden = cavs.length === 0;
      }
      // Deflate toggle: keys off the indicator actually plotted. Bubbles put the
      // spend axis on X; line/bar/map all foreground the Y indicator.
      const deflateIndicator =
        state.chartType === "bubbles" ? xIndicator : data.indicators[view.y];
      const deflateBlock = document.getElementById("deflate-block");
      const deflateBtn = document.getElementById("deflate-toggle");
      if (deflateIndicator && deflateIndicator.can_deflate) {
        deflateBlock.hidden = false;
        deflateBtn.textContent = state.deflate ? "PHP, 2018-real" : "PHP, nominal";
      } else {
        deflateBlock.hidden = true;
      }
      // Log toggle only governs the bubble X axis; hide it elsewhere (it was a
      // dead control in line/bar/map).
      const logBlock = document.getElementById("log-block");
      if (logBlock) logBlock.hidden = state.chartType !== "bubbles";
      document.getElementById("log-toggle").textContent = state.logX
        ? "X: log"
        : "X: linear";
      // Extrap toggle label
      const extBtn = document.getElementById("extrap-toggle");
      if (extBtn) {
        extBtn.textContent = state.extrapolate
          ? "Project past anchors: on"
          : "Project past anchors: off";
        extBtn.setAttribute("aria-pressed", state.extrapolate ? "true" : "false");
        extBtn.classList.toggle("on", state.extrapolate);
      }
      // Indicator pickers (X + Y dropdowns)
      renderIndicatorPickers(state, view, data, render);
      // Story tabs (mark active when view matches preset exactly)
      renderStorySwitcher(data.stories, state, view, render);
      // Year stepper + compare year selector
      renderYearControls(state, view, render);
      // Chart-type strip active state
      document.querySelectorAll(".chart-type-btn").forEach((b) => {
        const active = b.dataset.type === state.chartType;
        b.classList.toggle("active", active);
        b.setAttribute("aria-pressed", active ? "true" : "false");
      });
      // Expose the active chart type so CSS can move the play button clear of the
      // left-hand province labels in the bar view on narrow screens.
      const chartWrap = document.getElementById("chart-wrap");
      if (chartWrap) chartWrap.dataset.ct = state.chartType;
      // Island-group legend only applies to the views coloured by island
      // (bubbles/line/bar). Map is coloured by the value scale, so hide it there
      // to avoid implying the map colours mean island groups.
      const islandLegend = document.getElementById("island-legend");
      if (islandLegend) islandLegend.hidden = state.chartType === "map";
      // In line mode most provinces are faint context, so the saturated island
      // key over-promises. The note tells the reader what bold vs faint means.
      const islandNote = document.getElementById("island-legend-note");
      if (islandNote) islandNote.hidden = state.chartType !== "line";
      // Size key: only bubbles encode the 4th variable (population) as area.
      // Line/bar/map drop it, so the key would be a lie there.
      const sizeLegend = document.getElementById("size-legend");
      if (sizeLegend) {
        const showSize = state.chartType === "bubbles";
        sizeLegend.hidden = !showSize;
        if (showSize) renderSizeLegend();
      }
      // First-read scaffold: one plain-language line above the chart for the
      // novice landing on a 4-D moving scatter. Bubble mode only (the other
      // views are read differently), and stays gone once dismissed.
      const howto = document.getElementById("chart-howto");
      if (howto) {
        const showHowto = state.chartType === "bubbles" && !state.howtoDismissed;
        howto.hidden = !showHowto;
        if (showHowto) {
          const yrs = view.panel_years;
          const span = yrs && yrs.length ? `${yrs[0]}→${yrs[yrs.length - 1]}` : "the years";
          document.getElementById("chart-howto-text").textContent =
            `Each bubble is a province or Metro Manila. Size = population. Press play to watch ${span}.`;
        }
      }
      const selHint = document.getElementById("selected-hint");
      if (selHint) {
        selHint.textContent =
          state.chartType === "map"
            ? "Click a province to keep it labeled."
            : "Click a bubble to keep it labeled.";
      }
      // Big play button: hidden only in line mode (X axis is already year).
      // Bubbles and bar both benefit from year animation.
      const bp = document.getElementById("big-play");
      if (bp) bp.hidden = state.chartType === "line";
      // Chart (notMerge:true so a fresh axis indicator triggers full re-render).
      // Map mode needs the province polygons registered first; lazy-load them
      // on first use and re-render once ready (fall back to bubbles on failure).
      if (state.chartType === "map" && !PROVINCE_GEO) {
        chart.showLoading({
          text: "Loading map...",
          color: "#2b6cb0",
          textColor: "#595959",
          maskColor: "rgba(255,255,255,0.85)",
        });
        ensureProvinceMap(
          () => render(),
          () => {
            state.chartType = "bubbles";
            render();
          },
        );
      } else {
        chart.hideLoading();
        const opt = buildOption(view, data, state);
        // Honor prefers-reduced-motion for manual scrubbing too, not just the
        // initial autoplay: snap to the new frame instead of tweening positions.
        if (REDUCE_MOTION) opt.animation = false;
        chart.setOption(opt, { notMerge: true });
      }
      // Axis pickers: bubbles gets both, line gets Y only, bar gets Y only.
      attachAxisInfoButtons(chart, view, data, state.chartType);
      // Selection chips
      renderSelChips(state, data, render);
      // SR mirror
      renderSrTable(view, data, state);
      // URL hash
      writeHash(state, view);
      // Keep the finding/caveat on the correct side of the chart for this width.
      syncDetailPlacement();
    } finally {
      rendering = false;
    }
  }

  chart.on("timelinechanged", (e) => {
    const panel = (state.view && state.view.panel_years) || state.story.panel_years;
    state.year = panel[e.currentIndex];
    writeHash(state, state.view);
    if (state.view) renderSrTable(state.view, data, state);
  });

  let lastTapPsgc = null;
  let lastTapAt = 0;
  chart.on("click", (params) => {
    if (params.componentType !== "series") return;
    // Map mode: click a province to pin/unpin it (round-trips with bubble select).
    if (params.seriesId === "map") {
      const mpsgc = params.data && params.data.psgc;
      if (mpsgc) toggleSel(mpsgc, state, render);
      return;
    }
    if (params.seriesId !== "bubbles") return;
    const psgc = params.data && params.data.id;
    if (!psgc) return;
    renderLastTapPanel(params.data, state);
    if (IS_TOUCH) {
      // First tap: show tooltip + panel only. Second tap within 4s on the same
      // bubble: toggle selection. This stops touch users from pinning trails
      // accidentally while they are trying to read the tooltip.
      const now = Date.now();
      const isRepeat = psgc === lastTapPsgc && now - lastTapAt < 4000;
      lastTapPsgc = psgc;
      lastTapAt = now;
      if (isRepeat) {
        toggleSel(psgc, state, render);
        lastTapPsgc = null;
      }
      return;
    }
    toggleSel(psgc, state, render);
  });

  document.getElementById("log-toggle").addEventListener("click", () => {
    state.logX = !state.logX;
    render();
  });

  document.getElementById("deflate-toggle").addEventListener("click", () => {
    state.deflate = !state.deflate;
    render();
  });

  document.getElementById("extrap-toggle").addEventListener("click", () => {
    state.extrapolate = !state.extrapolate;
    render();
  });

  document.getElementById("chart-howto-dismiss").addEventListener("click", () => {
    state.howtoDismissed = true;
    persistHowtoDismissed();
    const howto = document.getElementById("chart-howto");
    if (howto) howto.hidden = true;
  });

  document.getElementById("csv").addEventListener("click", () => {
    downloadCsv(state.view || state.story, data, state);
  });

  // Chart-type strip: switch between bubble / line / bar.
  document.querySelectorAll(".chart-type-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const t = btn.dataset.type;
      if (!t || t === state.chartType) return;
      // Switching away from bubbles stops any running play loop.
      if (t !== "bubbles" && typeof window.__datavizph_stopPlay === "function") {
        window.__datavizph_stopPlay();
      }
      state.chartType = t;
      render();
    });
  });

  // Big yellow play button: drives a self-managed setInterval that steps the
  // year. ECharts' built-in timelinePlayChange action is unreliable when
  // autoPlay is false and controls are hidden, so we run our own loop.
  const bigPlay = document.getElementById("big-play");
  let playTimer = null;
  function setPlayingState(playing) {
    if (bigPlay) {
      bigPlay.classList.toggle("playing", playing);
      bigPlay.setAttribute("aria-pressed", playing ? "true" : "false");
      bigPlay.setAttribute("aria-label", playing ? "pause timeline" : "play timeline");
    }
  }
  function stopPlay() {
    const wasPlaying = playTimer !== null;
    if (playTimer) {
      clearInterval(playTimer);
      playTimer = null;
    }
    IS_AUTOPLAYING = false;
    setPlayingState(false);
    // Announce the year we landed on (suppressed during the run). render() is
    // synchronous, so a render() that immediately follows in the same handler
    // supersedes this one before paint, no flicker.
    if (wasPlaying) render();
  }
  function startPlay() {
    if (playTimer) return;
    if (state.chartType === "line") return;
    const panelYears = () =>
      (state.view && state.view.panel_years) || state.story.panel_years;
    // If we're sitting on the last year, rewind to the first so play means
    // "watch the full animation" rather than "do nothing."
    let ys = panelYears();
    if (state.year === ys[ys.length - 1]) {
      state.year = ys[0];
      render();
      ys = panelYears();
    }
    setPlayingState(true);
    IS_AUTOPLAYING = true;
    const interval = ys.length <= 3 ? 1500 : 1100;
    playTimer = setInterval(() => {
      const cur = panelYears();
      const idx = cur.indexOf(state.year);
      if (idx < 0 || idx >= cur.length - 1) {
        stopPlay();
        return;
      }
      state.year = cur[idx + 1];
      render();
    }, interval);
  }
  // Expose for the chart-type strip handler so switching away from bubbles
  // stops the timer.
  window.__datavizph_stopPlay = stopPlay;
  window.__datavizph_startPlay = startPlay;
  if (bigPlay) {
    bigPlay.addEventListener("click", () => {
      if (state.chartType === "line") return;
      if (playTimer) stopPlay();
      else startPlay();
    });
  }

  document.getElementById("png").addEventListener("click", () => {
    const url = chart.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: "#fff",
    });
    const v = state.view || state.story;
    const name = v.isCustom ? `${v.x}-vs-${v.y}` : v.id;
    const a = document.createElement("a");
    a.href = url;
    a.download = `dataviz-ph-${name}-${state.year}.png`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => a.remove(), 0);
  });

  document.getElementById("year-prev").addEventListener("click", () => {
    stepYear(state, -1, render);
  });
  document.getElementById("year-next").addEventListener("click", () => {
    stepYear(state, +1, render);
  });

  document.getElementById("share").addEventListener("click", async () => {
    const status = document.getElementById("share-status");
    try {
      await navigator.clipboard.writeText(window.location.href);
      const btn = document.getElementById("share");
      const prev = btn.textContent;
      btn.textContent = "Copied";
      if (status) status.textContent = "Link copied to clipboard.";
      setTimeout(() => {
        btn.textContent = prev;
        if (status) status.textContent = "";
      }, 1800);
    } catch (e) {
      console.error("clipboard failed", e);
      if (status)
        status.textContent =
          "Could not copy. Long-press the address bar instead.";
    }
  });

  wireSearch(document.getElementById("search"), data, state, render);

  // Keyboard year scrubbing (arrow keys when nothing else has focus).
  window.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    const panel = (state.view && state.view.panel_years) || state.story.panel_years;
    if (e.key === "ArrowRight") {
      stepYear(state, +1, render);
    } else if (e.key === "ArrowLeft") {
      stepYear(state, -1, render);
    } else if (e.key === "Home") {
      state.year = panel[0];
      render();
    } else if (e.key === "End") {
      state.year = panel[panel.length - 1];
      render();
    }
  });

  window.addEventListener("resize", () => chart.resize());
  // Re-place the finding/caveat when crossing the mobile breakpoint (rotate/resize).
  window.matchMedia("(max-width: 879px)").addEventListener("change", syncDetailPlacement);
  window.addEventListener("hashchange", () => {
    const next = parseHash(data.stories);
    state.story = next.story;
    state.xIndicator = next.xIndicator;
    state.yIndicator = next.yIndicator;
    state.year = next.year;
    state.compareYear = next.compareYear;
    state.logX = next.logX;
    state.sel = next.sel;
    state.deflate = next.deflate;
    // If chart type changes via URL nav, stop any running play loop so the
    // year-stepper doesn't keep firing while the chart re-renders as a
    // non-bubble view.
    if (next.chartType !== state.chartType) {
      if (typeof window.__datavizph_stopPlay === "function") {
        window.__datavizph_stopPlay();
      }
      state.chartType = next.chartType;
    }
    render();
  });

  render();

  if (!initial.hadHash) {
    // Start autoplay from a year that has data in the current deflate mode so
    // viewers don't watch 4 empty frames before bubbles appear. With deflate=real
    // we can't render pre-CPI-base years, so start at CPI_BASE_YEAR.
    const panel = state.view.panel_years;
    const safeFirstYear =
      DEFLATABLE_INDICATORS.has(state.view.x) && state.deflate
        ? Math.max(panel[0], CPI_BASE_YEAR)
        : panel[0];
    state.year = panel.includes(safeFirstYear) ? safeFirstYear : panel[0];
    render();
    // Respect prefers-reduced-motion: don't autoplay the bubble animation. The
    // play button stays available for anyone who wants it.
    const reduceMotion =
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!reduceMotion) {
      setTimeout(() => {
        if (typeof window.__datavizph_startPlay === "function") {
          window.__datavizph_startPlay();
        }
      }, 500);
    }
  }
}

main().catch((e) => {
  console.error(e);
  const root = document.getElementById("chart");
  root.replaceChildren();
  const msg = document.createElement("div");
  msg.id = "loading";
  msg.role = "alert";
  msg.textContent =
    "Chart could not load. Refresh the page, or check the browser console for details.";
  root.appendChild(msg);
});
