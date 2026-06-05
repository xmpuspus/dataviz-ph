// Fills the two live elements on the methodology page without booting the chart:
//   - #methodology-scale: the DPWH award trajectory (total / early-avg / peak /
//     ratio), computed from the same per-province data the chart plots, so the
//     numbers can never drift from a hand-typed prose value.
//   - #data-freshness: the build date + poverty-anchor vintage from manifest.json.
// Mirrors the compute in app.js (computeDpwhSurge / formatPesoScale / renderFreshness),
// kept self-contained so this static page pulls in no chart engine.

const COUNT = new Intl.NumberFormat("en-PH");

async function fetchJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function formatPesoScale(v) {
  if (v >= 1e12) return `PHP ${(v / 1e12).toFixed(2)} trillion`;
  if (v >= 1e9) return `PHP ${Math.round(v / 1e9)} billion`;
  if (v >= 1e6) return `PHP ${Math.round(v / 1e6)} million`;
  return `PHP ${COUNT.format(Math.round(v))}`;
}

// dpwhRows: [{psgc, year, value}], provinces: { psgc: { population_2020 } }.
function computeDpwhSurge(dpwhRows, provinces) {
  const yearTot = {};
  let total = 0;
  for (const r of dpwhRows) {
    const p = provinces[r.psgc];
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
  return { total, early, ratio: early ? late / early : null, peakYr, peakVal: yearTot[peakYr] };
}

function fillScale(dpwhRows, provinces) {
  const el = document.getElementById("methodology-scale");
  if (!el) return;
  const s = computeDpwhSurge(dpwhRows, provinces);
  if (!s || s.early == null || s.ratio == null) return;
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

function fillFreshness(manifest) {
  const target = document.getElementById("data-freshness");
  if (!target || !manifest || !manifest.built_at) return;
  let dateText = manifest.built_at;
  try {
    dateText = new Date(manifest.built_at).toLocaleDateString("en-PH", {
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

(async () => {
  try {
    const [provinces, dpwh, manifest] = await Promise.all([
      fetchJson("/data/provinces.json"),
      fetchJson("/data/dpwh_spend_per_capita.json"),
      fetchJson("/data/manifest.json").catch(() => null),
    ]);
    fillScale(dpwh, provinces);
    fillFreshness(manifest);
  } catch (e) {
    // Leave the scale paragraph hidden and the freshness line empty; the static
    // methodology prose stands on its own.
    console.error("methodology context failed", e);
  }
})();
