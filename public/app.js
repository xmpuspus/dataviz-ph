// plot.ph — story switcher, inflation toggle, CSV download, a11y mirror table,
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

async function loadData() {
  const [provinces, poverty, spend, allSpend, gdp, indicators, stories, manifest] =
    await Promise.all([
      fetchJson("data/provinces.json"),
      fetchJson("data/poverty.json"),
      fetchJson("data/dpwh_spend_per_capita.json"),
      fetchJson("data/all_spend_per_capita.json"),
      fetchJson("data/gdp_per_capita.json"),
      fetchJson("data/indicators.json"),
      fetchJson("data/stories.json"),
      fetchJson("data/manifest.json").catch(() => null),
    ]);
  return {
    provinces,
    indicatorRows: {
      poverty: indexRows(poverty),
      dpwh_spend_per_capita: indexRows(spend),
      all_spend_per_capita: indexRows(allSpend),
      gdp_per_capita: indexRows(gdp),
    },
    indicators: Object.fromEntries(indicators.map((i) => [i.id, i])),
    stories,
    manifest,
  };
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

// Build per-province point for a year in the current story.
function pointFor(psgc, year, story, data, state) {
  const info = data.provinces[psgc];
  if (!info) return null;
  const xRow = data.indicatorRows[story.x][`${psgc}-${year}`];
  const yRow = data.indicatorRows[story.y][`${psgc}-${year}`];
  if (!xRow || !yRow) return null;
  const xVal = indicatorValue(xRow, story.x, state);
  const yVal = indicatorValue(yRow, story.y, state);
  if (xVal === null || yVal === null) return null;
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
    return {
      id: p.psgc,
      name: p.name,
      value: [p.x, p.y, p.pop, p.name, p.year, p.interp, p.island, p.extrap],
      symbolSize: sizeFor(p.pop),
      itemStyle: {
        color,
        opacity: isAnchor ? 0.88 : 0.55,
        borderColor: isAnchor ? "#fff" : color,
        borderWidth: isAnchor ? 0.6 : 1.5,
      },
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

function buildTrails(year, story, data, state) {
  const trails = [];
  for (const psgc of state.sel) {
    const pts = [];
    for (const y of story.panel_years) {
      if (y > year) break;
      const p = pointFor(psgc, y, story, data, state);
      if (p) pts.push([p.x, p.y]);
    }
    if (pts.length < 2) continue;
    const info = data.provinces[psgc];
    const color = PALETTE[info.island_group] || "#999";
    trails.push({
      type: "line",
      name: `trail_${psgc}`,
      data: pts,
      symbol: "none",
      smooth: true,
      lineStyle: { color, width: 1.5, opacity: 0.45 },
      tooltip: { show: false },
      animationDurationUpdate: 600,
      z: 1,
    });
  }
  return trails;
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
  if (indicatorId === "poverty") return `${PCT.format(v)}%`;
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
  if (indicatorId === "gdp_per_capita") return "Per capita GDP, PHP (constant 2018)";
  if (indicatorId === "poverty") return "Poverty incidence among families (%)";
  return indicatorId;
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
    grid: { left: 70, right: 28, top: 44, bottom: 110 },
    xAxis: {
      type: logX ? "log" : "value",
      name: shortAxisName(xIndicator, state) + (logX ? " (log scale)" : " (linear)"),
      nameLocation: "middle",
      nameGap: 36,
      nameTextStyle: { fontSize: 12, color: "#595959" },
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
      name: shortAxisName(yIndicator, state),
      nameLocation: "middle",
      nameGap: 42,
      nameTextStyle: { fontSize: 12, color: "#595959" },
      min: 0,
      max: yIndicator === "poverty" ? 80 : undefined,
      axisLine: { lineStyle: { color: "#ccc" } },
      axisTick: { show: false },
      splitLine: { show: true, lineStyle: { color: "#f0f0f0" } },
      axisLabel: {
        color: "#595959",
        formatter: (v) => {
          if (yIndicator === "poverty") return v + "%";
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
        return (
          `<div style="font-weight:600;margin-bottom:4px">${swatch}${escapeHtml(name)} · ${year}</div>` +
          `<div style="color:#595959;font-size:11px;margin-bottom:6px">${escapeHtml(ISLAND_LABEL[island] || island)}</div>` +
          `<div>${escapeHtml(shortAxisName(xIndicator, state))}: <b>${escapeHtml(formatValue(x, xIndicator))}</b></div>` +
          `<div>${escapeHtml(shortAxisName(yIndicator, state))}: <b>${escapeHtml(formatValue(y, yIndicator))}</b></div>` +
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

function buildOption(story, data, state) {
  const years = story.panel_years;
  const trailIds = [...state.sel];
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
    const titleBlocks = [
      {
        text: compareSeries ? `${year} vs ${state.compareYear}` : `${year}`,
        left: "center",
        top: 10,
        textStyle: { fontSize: 48, fontWeight: 700, color: "rgba(0,0,0,0.06)" },
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
        left: 70,
        right: 28,
        symbol: "none",
        lineStyle: { color: "#ccc" },
        checkpointStyle: { color: "#111", borderColor: "#fff", borderWidth: 2 },
        controlStyle: {
          showNextBtn: false,
          showPrevBtn: false,
          color: "#111",
          borderColor: "#111",
          itemSize: 18,
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
  return {
    story,
    year: story && story.panel_years.includes(year) ? year : (story && story.default_year),
    compareYear: story && story.panel_years.includes(cmp) ? cmp : null,
    logX: logX === null ? !!(story && story.default_log_x) : logX === "x",
    sel: new Set(sel),
    deflate: deflate === null ? true : deflate === "real",
    hadHash: h.length > 0,
  };
}

function writeHash(state) {
  const params = new URLSearchParams();
  params.set("story", state.story.id);
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
      li.textContent = m.name + (state.sel.has(m.psgc) ? "  ✓" : "");
      li.dataset.psgc = m.psgc;
      li.tabIndex = 0;
      li.addEventListener("click", () => {
        toggleSel(m.psgc, state, render);
        input.value = "";
        list.replaceChildren();
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

function renderSelChips(state, data, render) {
  const root = document.getElementById("selected-chips");
  const hint = document.getElementById("selected-hint");
  root.replaceChildren();
  for (const psgc of state.sel) {
    const info = data.provinces[psgc];
    if (!info) continue;
    const color = PALETTE[info.island_group] || "#999";
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.style.background = color;
    chip.appendChild(document.createTextNode(info.name + " "));
    const btn = document.createElement("button");
    btn.setAttribute("aria-label", `remove ${info.name}`);
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

function renderYearControls(state, render) {
  const display = document.getElementById("year-display");
  if (display) display.textContent = String(state.year);

  const select = document.getElementById("compare-select");
  if (!select) return;
  const current = state.compareYear || "";
  select.innerHTML = "";
  const off = document.createElement("option");
  off.value = "";
  off.textContent = "Off";
  select.appendChild(off);
  for (const y of state.story.panel_years) {
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
  const years = state.story.panel_years;
  const idx = years.indexOf(state.year);
  const next = years[idx + delta];
  if (next === undefined) return;
  state.year = next;
  render();
}

// ---------- story switcher UI ----------

function renderStorySwitcher(stories, state, render) {
  const nav = document.getElementById("story-switcher");
  nav.replaceChildren();
  for (const s of stories) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.role = "tab";
    btn.setAttribute("aria-selected", s.id === state.story.id ? "true" : "false");
    btn.className = "story-btn" + (s.id === state.story.id ? " active" : "");
    btn.textContent = s.tab_label || s.headline.split(".")[0];
    btn.addEventListener("click", () => {
      if (s.id === state.story.id) return;
      state.story = s;
      state.year = s.default_year;
      state.logX = !!s.default_log_x;
      render();
    });
    nav.appendChild(btn);
  }
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

  const tbl = document.createElement("table");
  const cap = document.createElement("caption");
  cap.textContent = `${story.headline} Year ${state.year}, ${rows.length} provinces.`;
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
  a.download = `plot-ph-${story.id}-${state.year}.csv`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    a.remove();
    URL.revokeObjectURL(url);
  }, 0);
}

// ---------- axis definition popover ----------

function attachAxisInfoButtons(chart, story, data) {
  // Mount two absolute-positioned info buttons over the X and Y axis label area.
  // ECharts paints axis labels into the canvas, so we overlay our own buttons that
  // open a popover with the indicator's definition + source link.
  const root = document.getElementById("chart");
  if (!root) return;
  let layer = document.getElementById("axis-info-layer");
  if (!layer) {
    layer = document.createElement("div");
    layer.id = "axis-info-layer";
    root.appendChild(layer);
  }
  layer.replaceChildren();

  const place = (id, indicatorId, position) => {
    const info = data.indicators && data.indicators[indicatorId];
    if (!info) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = id;
    btn.className = "axis-info-btn";
    btn.setAttribute("aria-label", `${info.name}: what does this measure?`);
    btn.textContent = "i";
    btn.style.position = "absolute";
    Object.assign(btn.style, position);
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      showAxisPopover(btn, info);
    });
    btn.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        showAxisPopover(btn, info);
      }
    });
    layer.appendChild(btn);
  };

  // X-axis label sits centered, ~24px above the timeline (story.x).
  place("axis-info-x", story.x, { left: "50%", bottom: "76px", transform: "translateX(-50%)" });
  // Y-axis label rotates 90deg, vertical text on the left.
  place("axis-info-y", story.y, { left: "16px", top: "50%", transform: "translateY(-50%)" });
}

function showAxisPopover(anchorBtn, info) {
  closeAxisPopover();
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
  const xLabel = shortAxisName(state.story.x, state);
  const yLabel = shortAxisName(state.story.y, state);
  let noteText = "";
  if (extrap || interp) {
    const yKey = state.story.y;
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
    [xLabel, formatValue(x, state.story.x)],
    [yLabel, formatValue(y, state.story.y)],
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

async function main() {
  const root = document.getElementById("chart");
  const loading = document.getElementById("loading");
  const data = await loadData();
  if (loading) loading.remove();

  const initial = parseHash(data.stories);
  const state = {
    story: initial.story,
    year: initial.year,
    compareYear: initial.compareYear,
    logX: initial.logX,
    sel: initial.sel,
    deflate: initial.deflate,
  };

  const chart = echarts.init(root, null, { renderer: "canvas" });
  renderFreshness(data.manifest);

  let rendering = false;
  function render() {
    if (rendering) return;
    rendering = true;
    try {
      const xIndicator = data.indicators[state.story.x];
      // Headline + tagline update with story
      document.getElementById("story-headline").textContent = state.story.headline;
      document.getElementById("story-tagline").textContent = state.story.tagline;
      // Per-story why + source link (both optional)
      const whyEl = document.getElementById("story-why");
      if (whyEl) {
        whyEl.textContent = state.story.why || "";
        whyEl.hidden = !state.story.why;
      }
      const srcEl = document.getElementById("story-source");
      if (srcEl) {
        if (state.story.source_url) {
          srcEl.href = state.story.source_url;
          srcEl.hidden = false;
        } else {
          srcEl.hidden = true;
        }
      }
      // Deflate toggle visibility + label
      const deflateBlock = document.getElementById("deflate-block");
      const deflateBtn = document.getElementById("deflate-toggle");
      if (xIndicator && xIndicator.can_deflate) {
        deflateBlock.hidden = false;
        deflateBtn.textContent = state.deflate ? "PHP, 2018-real" : "PHP, nominal";
      } else {
        deflateBlock.hidden = true;
      }
      // Log toggle label
      document.getElementById("log-toggle").textContent = state.logX
        ? "X: log"
        : "X: linear";
      // Story tabs
      renderStorySwitcher(data.stories, state, render);
      // Year stepper + compare year selector
      renderYearControls(state, render);
      // Chart
      chart.setOption(buildOption(state.story, data, state), { notMerge: true });
      // Axis-label info buttons (overlay)
      attachAxisInfoButtons(chart, state.story, data);
      // Selection chips
      renderSelChips(state, data, render);
      // SR mirror
      renderSrTable(state.story, data, state);
      // URL hash
      writeHash(state);
    } finally {
      rendering = false;
    }
  }

  chart.on("timelinechanged", (e) => {
    state.year = state.story.panel_years[e.currentIndex];
    writeHash(state);
    renderSrTable(state.story, data, state);
  });

  let lastTapPsgc = null;
  let lastTapAt = 0;
  chart.on("click", (params) => {
    if (params.componentType !== "series" || params.seriesId !== "bubbles") return;
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

  document.getElementById("csv").addEventListener("click", () => {
    downloadCsv(state.story, data, state);
  });

  document.getElementById("png").addEventListener("click", () => {
    const url = chart.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: "#fff",
    });
    const a = document.createElement("a");
    a.href = url;
    a.download = `plot-ph-${state.story.id}-${state.year}.png`;
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
    if (e.key === "ArrowRight") {
      stepYear(state, +1, render);
    } else if (e.key === "ArrowLeft") {
      stepYear(state, -1, render);
    } else if (e.key === "Home") {
      state.year = state.story.panel_years[0];
      render();
    } else if (e.key === "End") {
      state.year = state.story.panel_years[state.story.panel_years.length - 1];
      render();
    }
  });

  window.addEventListener("resize", () => chart.resize());
  window.addEventListener("hashchange", () => {
    const next = parseHash(data.stories);
    state.story = next.story;
    state.year = next.year;
    state.compareYear = next.compareYear;
    state.logX = next.logX;
    state.sel = next.sel;
    state.deflate = next.deflate;
    render();
  });

  render();

  if (!initial.hadHash) {
    // Start autoplay from a year that has data in the current deflate mode so
    // viewers don't watch 4 empty frames before bubbles appear. With deflate=real
    // we can't render pre-CPI-base years, so start at CPI_BASE_YEAR.
    const safeFirstYear =
      DEFLATABLE_INDICATORS.has(state.story.x) && state.deflate
        ? Math.max(state.story.panel_years[0], CPI_BASE_YEAR)
        : state.story.panel_years[0];
    state.year = state.story.panel_years.includes(safeFirstYear)
      ? safeFirstYear
      : state.story.panel_years[0];
    render();
    setTimeout(() => {
      chart.dispatchAction({ type: "timelinePlayChange", playState: true });
    }, 500);
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
