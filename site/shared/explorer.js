// One explorer page: filters on top, then stat cards, charts, a breakdown table
// and the rows, all reading the same filters. Each page passes a plain config
// (see contracts.html for the shape); everything here is plain functions.
//
// State: FILTERS is the only filter state, owned by this module. The URL is
// written from it and read into it once at start; nothing else holds a copy.
//
// Every figure is a SQL aggregate over the rows the filters select. Field
// values are the District's, as published.

import {boot, q, downloadCsv, manifest, LOCAL, PARQUET_DIR, DATASET_PAGE} from "./db.js";
export {q, downloadCsv};

let CFG = null;
let FILTERS = {};      // key -> {values: []} | {text} | {from, to} | {min, max}
let TABLE = null;      // the DataTable
const CHARTS = {};     // chart id -> Chart instance
let NIGP = {};         // class -> description

// ---------- formatting ----------
export function n(x) { return x === null || x === undefined ? "–" : Number(x).toLocaleString("en-US"); }
export function money(x) {
  if (x === null || x === undefined) return "–";
  const v = Number(x);
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return v.toLocaleString("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0});
}
function moneyExact(x) {
  return x === null || x === undefined ? "–"
    : Number(x).toLocaleString("en-US", {style: "currency", currency: "USD", minimumFractionDigits: 2});
}
export function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}

// ---------- filters -> SQL ----------
function whereFrom(filters, skip = null) {
  const parts = [];
  const binds = [];
  for (const [key, f] of Object.entries(filters)) {
    if (key === skip) continue;
    const field = CFG.fields[key];
    if (!field) throw new Error(`unknown filter field ${key}`);
    if (field.filter === "multi" && f.values?.length) {
      const marks = f.values.map(() => "?").join(", ");
      parts.push(field.listExpr ? `list_has_any(${field.listExpr}, [${marks}])` : `${field.expr} IN (${marks})`);
      binds.push(...f.values);
    } else if (field.filter === "text" && f.text) {
      // Identifiers match exactly: CW91684 must not also find CW916840.
      parts.push(field.exact ? `${field.expr} = ?` : `${field.expr} ILIKE ?`);
      binds.push(field.exact ? f.text.trim() : `%${f.text}%`);
    } else if (field.filter === "date") {
      if (f.from) { parts.push(`${field.expr} >= CAST(? AS DATE)`); binds.push(f.from); }
      if (f.to) { parts.push(`${field.expr} <= CAST(? AS DATE)`); binds.push(f.to); }
    } else if (field.filter === "number") {
      if (f.min !== undefined && f.min !== "") { parts.push(`${field.expr} >= ?`); binds.push(Number(f.min)); }
      if (f.max !== undefined && f.max !== "") { parts.push(`${field.expr} <= ?`); binds.push(Number(f.max)); }
    }
  }
  return {sql: parts.length ? "WHERE " + parts.join("\n  AND ") : "", binds};
}

// ---------- the URL ----------
// Repeated parameters, never comma-joined: vendor and agency names contain
// commas ("COMPUTER AID, INC."), so splitting on them breaks the filter.
function writeUrl() {
  const p = new URLSearchParams();
  const data = new URLSearchParams(location.search).get("data");
  if (data) p.set("data", data);
  for (const [key, f] of Object.entries(FILTERS)) {
    for (const v of f.values || []) p.append(key, v);
    if (f.text) p.set(key, f.text);
    if (f.from) p.set(key + "_from", f.from);
    if (f.to) p.set(key + "_to", f.to);
    if (f.min !== undefined && f.min !== "") p.set(key + "_min", f.min);
    if (f.max !== undefined && f.max !== "") p.set(key + "_max", f.max);
  }
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

function readUrl() {
  const p = new URLSearchParams(location.search);
  for (const [key, field] of Object.entries(CFG.fields)) {
    if (field.filter === "multi" && p.getAll(key).length) FILTERS[key] = {values: p.getAll(key)};
    if (field.filter === "text" && p.get(key)) FILTERS[key] = {text: p.get(key)};
    if (field.filter === "date" && (p.get(key + "_from") || p.get(key + "_to"))) {
      FILTERS[key] = {from: p.get(key + "_from") || "", to: p.get(key + "_to") || ""};
    }
    if (field.filter === "number" && (p.get(key + "_min") || p.get(key + "_max"))) {
      FILTERS[key] = {min: p.get(key + "_min") || "", max: p.get(key + "_max") || ""};
    }
  }
}

// ---------- filter chips and the picker ----------
function chipText(key, f) {
  const label = CFG.fields[key].label;
  if (f.values) return `${label}: ${f.values.length > 2 ? f.values.slice(0, 2).join(", ") + ` +${f.values.length - 2}` : f.values.join(", ")}`;
  // A label that already says "contains" ("Vendor name contains") is not repeated.
  if (f.text) return CFG.fields[key].exact ? `${label} is ${f.text}`
    : / contains$/.test(label) ? `${label} "${f.text}"` : `${label} contains "${f.text}"`;
  if ("from" in f) return `${label}: ${f.from || "…"} to ${f.to || "…"}`;
  return `${label}: ${f.min || "…"} to ${f.max || "…"}`;
}

function renderChips() {
  const box = document.getElementById("chips");
  const keys = Object.keys(FILTERS);
  box.innerHTML = keys.length
    ? keys.map((k) => `<span class="chip"><button class="chip-edit" data-key="${k}">${esc(chipText(k, FILTERS[k]))}</button>` +
        `<button class="chip-x" data-key="${k}" title="Remove">×</button></span>`).join("")
    : `<span class="muted">No filters: everything in the table.</span>`;
  document.getElementById("clear").hidden = keys.length === 0;
}

function setFilter(key, value) {
  if (value === null) delete FILTERS[key];
  else FILTERS[key] = value;
  refresh();
}

async function optionsFor(key) {
  // Values with their row counts under every other active filter, so the
  // picker shows what choosing one would leave.
  const field = CFG.fields[key];
  const {sql, binds} = whereFrom(FILTERS, key);
  const valueExpr = field.optionExpr || field.expr;
  const rows = await q(`SELECT v, count(*) AS n FROM (SELECT ${valueExpr} AS v FROM ${CFG.table} ${sql}) GROUP BY v ORDER BY n DESC, v LIMIT 2000`, binds);
  return rows.filter((r) => r.v !== null && r.v !== "");
}

function openDialog(html) {
  const dlg = document.getElementById("dialog");
  dlg.innerHTML = html;
  dlg.showModal();
  return dlg;
}

function pickField() {
  const keys = Object.keys(CFG.fields).filter((k) => CFG.fields[k].filter);
  const dlg = openDialog(`<form method="dialog"><h3>Filter by</h3><div class="pick-list">` +
    keys.map((k) => `<button value="${k}" class="pick">${esc(CFG.fields[k].label)}<span class="muted"> ${esc(CFG.fields[k].from || "")}</span></button>`).join("") +
    `</div><div class="dlg-bar"><button value="">Cancel</button></div></form>`);
  dlg.onclose = () => { if (dlg.returnValue) editFilter(dlg.returnValue); };
}

async function editFilter(key) {
  const field = CFG.fields[key];
  const current = FILTERS[key] || {};
  if (field.filter === "multi") {
    const dlg = openDialog(`<p class="muted">Loading values…</p>`);
    const options = await optionsFor(key);
    const chosen = new Set(current.values || []);
    dlg.innerHTML = `<form method="dialog"><h3>${esc(field.label)}</h3>
      <p class="muted">${esc(field.from || "")} Counts are rows under your other filters.</p>
      <input type="search" class="pick-search" placeholder="Search ${options.length.toLocaleString()} values">
      <div class="pick-values">${options.map((o) => `<label class="pick-value"><input type="checkbox" value="${esc(o.v)}"${chosen.has(String(o.v)) ? " checked" : ""}>` +
        `<span>${esc(field.describe ? field.describe(o.v) : o.v)}</span><span class="muted">${n(o.n)}</span></label>`).join("")}</div>
      <div class="dlg-bar"><button value="">Cancel</button><button value="apply" class="primary">Apply</button></div></form>`;
    dlg.querySelector(".pick-search").addEventListener("input", (e) => {
      const needle = e.target.value.toLowerCase();
      for (const el of dlg.querySelectorAll(".pick-value")) el.hidden = !el.textContent.toLowerCase().includes(needle);
    });
    dlg.onclose = () => {
      if (dlg.returnValue !== "apply") return;
      const values = [...dlg.querySelectorAll(".pick-value input:checked")].map((i) => i.value);
      setFilter(key, values.length ? {values} : null);
    };
    return;
  }
  const inputs = field.filter === "text"
    ? `<input name="text" type="search" value="${esc(current.text || "")}" placeholder="${field.exact ? "Exactly…" : "Contains…"}">`
    : field.filter === "date"
      ? `<label>From <input name="from" type="date" value="${esc(current.from || "")}"></label><label>To <input name="to" type="date" value="${esc(current.to || "")}"></label>`
      : `<label>Min <input name="min" type="number" value="${esc(current.min ?? "")}"></label><label>Max <input name="max" type="number" value="${esc(current.max ?? "")}"></label>`;
  const dlg = openDialog(`<form method="dialog"><h3>${esc(field.label)}</h3><p class="muted">${esc(field.from || "")}</p>
    <div class="pick-inputs">${inputs}</div>
    <div class="dlg-bar"><button value="">Cancel</button><button value="apply" class="primary">Apply</button></div></form>`);
  dlg.onclose = () => {
    if (dlg.returnValue !== "apply") return;
    const form = Object.fromEntries(new FormData(dlg.querySelector("form")));
    const empty = Object.values(form).every((v) => v === "");
    setFilter(key, empty ? null : form);
  };
}

// ---------- stats ----------
async function renderStats(where) {
  const sql = `SELECT ${CFG.stats.map((s, i) => `${s.expr} AS s${i}`).join(",\n       ")}\nFROM ${CFG.table}\n${where.sql}`;
  const [row] = await q(sql, where.binds);
  document.getElementById("stats").innerHTML = CFG.stats.map((s, i) =>
    `<div class="stat"><div class="stat-label">${esc(s.label)}</div><div class="stat-value">${s.money ? money(row["s" + i]) : n(row["s" + i])}</div>` +
    `<div class="stat-what">${esc(s.what || "")}</div></div>`).join("");
}

// ---------- charts ----------
const MIN_BAR_SHARE = 0.01;   // see renderChart
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

// Which number sizes the bars: the dollar measure, or the count. One value,
// set from cfg.sizeDefault at start and then by the page's switch.
let SIZE_BY = "measure";
function sizing() { return SIZE_BY === "measure" ? CFG.measure : CFG.count; }
// Pages whose measure is not money (documents) format it as a count.
function measureFmt(x, exact = false) {
  if (CFG.measure.money === false) return n(x);
  return exact ? moneyExact(x) : money(x);
}

async function renderChart(spec, where) {
  const field = CFG.fields[spec.field];
  const size = sizing();
  const ordered = spec.byKey ? "k" : `s DESC NULLS LAST`;
  const sql = `SELECT ${field.expr} AS k, ${CFG.count.expr} AS c, ${CFG.measure.expr} AS m, ${size.expr} AS s\nFROM ${CFG.table}\n${where.sql}\nGROUP BY k\nORDER BY ${ordered}`;
  const rows = await q(sql, where.binds);
  const fmt = SIZE_BY === "measure" ? (x) => measureFmt(x) : n;
  // A bar under 1% of the largest is a hairline: it reads as blank, and a run
  // of them buries the few that matter. Those are left off and counted in the
  // note instead; "Break it down" lists every value. Year charts keep every
  // year, since a gap there means something.
  const largest = rows.length ? Math.max(...rows.map((r) => r.s ?? 0)) : 0;
  const shown = spec.byKey ? rows
    : rows.filter((r) => (r.s ?? 0) > 0 && (r.s ?? 0) >= largest * MIN_BAR_SHARE).slice(0, spec.limit || 12);
  const left = rows.filter((r) => !shown.includes(r));
  const leftTotal = left.reduce((t, r) => t + (r.s ?? 0), 0);
  const box = document.getElementById("chart-" + spec.id);
  box.querySelector(".chart-note").textContent = spec.byKey
    ? `${size.label} by ${field.label.toLowerCase()}. Hover for both numbers.`
    : `${shown.length < rows.length ? `Top ${shown.length} of ${n(rows.length)} values` : `All ${n(rows.length)} values`}, ` +
      `by ${size.label.toLowerCase()}. Hover for both numbers. Click a bar to filter to it.` +
      (left.length ? ` Not shown: ${n(left.length)} more, together ${fmt(leftTotal)}. Every value is in "Break it down" below.` : "");
  const labels = shown.map((r) => r.k === null ? "(blank)" : (field.describe ? field.describe(r.k) : String(r.k)));
  if (CHARTS[spec.id]) CHARTS[spec.id].destroy();
  CHARTS[spec.id] = new Chart(box.querySelector("canvas"), {
    type: "bar",
    data: {labels, datasets: [{data: shown.map((r) => r.s ?? 0), backgroundColor: cssVar("--bar"), hoverBackgroundColor: cssVar("--bar-hover")}]},
    options: {
      indexAxis: spec.byKey ? "x" : "y",
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: {display: false},
        tooltip: {callbacks: {
          title: (items) => labels[items[0].dataIndex],
          label: (c) => CFG.measure.money === false ? `${n(shown[c.dataIndex].c)} ${CFG.count.noun}`
            : `${moneyExact(shown[c.dataIndex].m)} · ${n(shown[c.dataIndex].c)} ${CFG.count.noun}`,
        }},
      },
      // The value axis shows dollars; the category axis shows the District's
      // values, shortened for space (hover shows the whole thing).
      scales: spec.byKey ? {
        x: {ticks: {color: cssVar("--muted")}, grid: {display: false}},
        y: {ticks: {color: cssVar("--muted"), callback: (v) => fmt(v)}, grid: {color: cssVar("--rule")}},
      } : {
        x: {ticks: {color: cssVar("--muted"), callback: (v) => fmt(v)}, grid: {color: cssVar("--rule")}},
        y: {ticks: {color: cssVar("--muted"), autoSkip: false, callback: function (v) {
          const t = this.getLabelForValue(v);
          return t.length > 38 ? t.slice(0, 36) + "…" : t;
        }}, grid: {display: false}},
      },
      onClick: (event, elements) => {
        if (!elements.length || field.filter !== "multi" || field.listExpr) return;
        const value = shown[elements[0].index].k;
        if (value === null) return;
        const values = new Set(FILTERS[spec.field]?.values || []);
        values.add(String(value));
        setFilter(spec.field, {values: [...values]});
      },
    },
  });
  box.style.setProperty("--h", spec.byKey ? "240px" : Math.max(160, shown.length * 24 + 40) + "px");
}

// ---------- breakdown ----------
async function renderBreakdown(where) {
  const by1 = document.getElementById("by1").value;
  const by2 = document.getElementById("by2").value;
  const keys = [by1, by2].filter(Boolean);
  const out = document.getElementById("breakdown");
  if (!keys.length) { out.innerHTML = ""; return; }
  const groups = keys.map((k, i) => `${CFG.fields[k].expr} AS g${i}`);
  const extra = CFG.distinct ? `,\n       ${CFG.distinct.expr} AS d` : "";
  const sql = `SELECT ${groups.join(", ")},\n       count(*) AS n${extra},\n       ${CFG.measure.expr} AS m\nFROM ${CFG.table}\n${where.sql}\n` +
    `GROUP BY ${keys.map((_, i) => "g" + i).join(", ")}\nORDER BY ${keys.map((k, i) => CFG.fields[k].ordered ? `g${i}` : null)
      .concat(keys.length > 1 && !CFG.fields[keys[0]].ordered ? ["g0"] : []).filter(Boolean).concat(["m DESC NULLS LAST"]).join(", ")}`;
  const rows = await q(sql + "\nLIMIT 501", where.binds);
  const heads = [...keys.map((k) => CFG.fields[k].label), CFG.rowLabel, ...(CFG.distinct ? [CFG.distinct.label] : []), CFG.measure.label];
  out.innerHTML = `<div class="tablewrap"><table class="plain"><thead><tr>${heads.map((h, i) => `<th${i >= keys.length ? ' class="num"' : ""}>${esc(h)}</th>`).join("")}</tr></thead><tbody>` +
    rows.slice(0, 500).map((r) => "<tr>" + keys.map((_, i) => `<td>${esc(r["g" + i] ?? "(blank)")}</td>`).join("") +
      `<td class="num">${n(r.n)}</td>${CFG.distinct ? `<td class="num">${n(r.d)}</td>` : ""}<td class="num">${measureFmt(r.m, true)}</td></tr>`).join("") +
    `</tbody></table></div>${rows.length > 500 ? '<p class="muted">First 500 groups shown; the CSV has them all.</p>' : ""}`;
  document.getElementById("breakdown-csv").onclick = () =>
    downloadCsv(sql, where.binds, `${CFG.table}-by-${keys.join("-")}.csv`);
}

// ---------- the rows ----------
function buildTable() {
  const cols = CFG.columns;
  document.getElementById("rows").innerHTML = `<table class="display compact" style="width:100%"><thead><tr>` +
    cols.map((c) => `<th${c.title ? ` title="${esc(c.title)}"` : ""}>${esc(c.label)}</th>`).join("") + "</tr></thead></table>";
  TABLE = $("#rows table").DataTable({
    serverSide: true,
    processing: true,
    searching: true,
    pageLength: 25,
    order: CFG.defaultOrder,
    scrollX: true,
    // DataTables otherwise sizes columns from the longest markup a cell renders,
    // tooltip text included, which made short columns very wide.
    autoWidth: false,
    language: {search: `Search ${CFG.searchLabel}:`},
    columns: cols.map((c) => ({
      data: c.key,
      className: c.cls || "",
      orderable: c.sortable !== false,
      render: (value, type, row) => (c.render ? c.render(value, row) : esc(Array.isArray(value) ? value.join("; ") : value)),
    })),
    ajax: (request, callback) => {
      tableQuery(request).then(callback, (err) => {
        // DataTables would otherwise sit on "Processing…" with no sign of why.
        document.getElementById("rows-error").textContent = String(err.message || err);
        throw err;
      });
    },
  });
}

async function tableQuery(request) {
  const where = whereFrom(FILTERS);
  const parts = where.sql ? [where.sql.replace(/^WHERE /, "")] : [];
  const binds = [...where.binds];
  const search = request.search?.value?.trim();
  if (search) {
    parts.push("(" + CFG.search.map((c) => `CAST(${c} AS VARCHAR) ILIKE ?`).join(" OR ") + ")");
    for (const _ of CFG.search) binds.push(`%${search}%`);
  }
  const whereSql = parts.length ? "WHERE " + parts.join("\n  AND ") : "";
  const order = (request.order || []).map((o) => `${CFG.columns[o.column].key} ${o.dir === "desc" ? "DESC" : "ASC"} NULLS LAST`);
  const select = CFG.select.join(", ");
  const sql = `SELECT ${select}\nFROM ${CFG.table}\n${whereSql}\nORDER BY ${order.join(", ") || CFG.columns[0].key}`;
  const [{total}] = await q(`SELECT count(*) AS total FROM ${CFG.table} ${whereSql}`, binds);
  const rows = await q(`${sql}\nLIMIT ${Number(request.length)} OFFSET ${Number(request.start)}`, binds);
  return {draw: request.draw, recordsTotal: total, recordsFiltered: total, data: rows};
}

// ---------- refresh everything from FILTERS ----------
let refreshing = 0;
async function refresh() {
  const ticket = ++refreshing;
  writeUrl();
  renderChips();
  const where = whereFrom(FILTERS);
  await Promise.all([
    renderStats(where),
    ...CFG.charts.map((c) => renderChart(c, where)),
    renderBreakdown(where),
  ]);
  if (ticket !== refreshing) return;   // a newer refresh has started
  if (TABLE) TABLE.ajax.reload(null, true);
}

// ---------- what this covers ----------
// Every page says up front what its table is and is not, with figures from
// the table itself and from summary.json (which the same export writes), so
// the statement stays true as the data moves.
async function renderCoverage(cfg) {
  const [row] = await q(cfg.coverage.sql);
  const response = await fetch(new URL(PARQUET_DIR + "summary.json", location.href).href);
  if (!response.ok) throw new Error(`could not load summary.json: ${response.status}`);
  const summary = await response.json();
  const about = "about.html" + (new URLSearchParams(location.search).get("data") ? "?data=" + new URLSearchParams(location.search).get("data") : "");
  document.getElementById("coverage").innerHTML = `<h2>What this covers</h2>${cfg.coverage.text(row, summary)}` +
    `<p><a href="${about}">More on what the data does and does not include →</a></p>`;
}

// ---------- start ----------
export async function start(cfg) {
  CFG = cfg;
  readUrl();
  const status = document.getElementById("status");
  try {
    status.textContent = "Loading the data into your browser…";
    await boot(cfg.tables || [cfg.table, "nigp_classes"]);
    for (const r of await q("SELECT nigp_class, description FROM nigp_classes")) NIGP[r.nigp_class] = r.description;
    const m = await manifest();
    const stamps = m.snapshots.map((s) => (s.pulled_at || "").slice(0, 10)).filter(Boolean).sort();
    document.getElementById("asof").innerHTML =
      `Pulled from contracts.ocp.dc.gov ${esc(stamps[0])}${stamps.at(-1) !== stamps[0] ? " to " + esc(stamps.at(-1)) : ""}` +
      ` · <a href="${DATASET_PAGE}">full dataset</a> · <a href="${new URL(PARQUET_DIR + cfg.table + ".parquet", location.href).href}">this table (Parquet)</a>` +
      (LOCAL ? " · <strong>reading the local build</strong>" : "");
    // Charts sit in a <details> that the HTML marks open. On a phone they make
    // the page very long, so start it closed there. A chart drawn while closed
    // has no size, so redraw each one at its real size when it is opened.
    const chartsBox = document.getElementById("charts-box");
    if (matchMedia("(max-width: 639.98px)").matches) chartsBox.open = false;
    chartsBox.addEventListener("toggle", () => {
      if (!chartsBox.open) return;
      for (const chart of Object.values(CHARTS)) chart.resize();
    });
    document.getElementById("charts").innerHTML = cfg.charts.map((c) =>
      `<div class="card chart${c.byKey ? " wide" : ""}" id="chart-${c.id}"><h3>${esc(c.title)}</h3><p class="chart-note muted"></p><div class="canvas"><canvas></canvas></div></div>`).join("");
    const opts = `<option value="">—</option>` + cfg.breakdown.map((k) => `<option value="${k}">${esc(cfg.fields[k].label)}</option>`).join("");
    document.getElementById("by1").innerHTML = opts;
    document.getElementById("by2").innerHTML = opts;
    document.getElementById("by1").value = cfg.breakdownDefault?.[0] || "";
    document.getElementById("by2").value = cfg.breakdownDefault?.[1] || "";
    for (const id of ["by1", "by2"]) document.getElementById(id).addEventListener("change", refresh);
    const sizeDefault = cfg.sizeDefault || "measure";
    if (sizeDefault !== "measure" && sizeDefault !== "count") throw new Error(`sizeDefault must be "measure" or "count", not ${sizeDefault}`);
    SIZE_BY = sizeDefault;
    document.getElementById("size-by").innerHTML =
      `<option value="measure">${esc(cfg.measure.label)}</option><option value="count">${esc(cfg.count.label)}</option>`;
    document.getElementById("size-by").value = SIZE_BY;
    document.getElementById("size-by").addEventListener("change", (e) => { SIZE_BY = e.target.value; refresh(); });
    document.getElementById("add-filter").addEventListener("click", pickField);
    document.getElementById("clear").addEventListener("click", () => { FILTERS = {}; refresh(); });
    document.getElementById("chips").addEventListener("click", (e) => {
      const edit = e.target.closest(".chip-edit");
      const x = e.target.closest(".chip-x");
      if (x) setFilter(x.dataset.key, null);
      else if (edit) editFilter(edit.dataset.key);
    });
    document.getElementById("copy-link").addEventListener("click", async () => {
      await navigator.clipboard.writeText(location.href);
      document.getElementById("copy-link").textContent = "Copied";
      setTimeout(() => { document.getElementById("copy-link").textContent = "Copy link"; }, 1500);
    });
    document.getElementById("download").addEventListener("click", async () => {
      const where = whereFrom(FILTERS);
      const [{total}] = await q(`SELECT count(*) AS total FROM ${cfg.table} ${where.sql}`, where.binds);
      if (total > 300000 && !confirm(`That is ${n(total)} rows. The Parquet file in the full dataset may be easier. Download as CSV anyway?`)) return;
      await downloadCsv(`SELECT * FROM ${cfg.table} ${where.sql}`, where.binds, `${cfg.table}.csv`);
    });
    if (cfg.coverage) await renderCoverage(cfg);
    buildTable();
    status.textContent = "";
    await refresh();
  } catch (err) {
    status.innerHTML = `<span class="error">${esc(err.message || err)}</span>`;
    throw err;
  }
}

// For configs: describe an NIGP class with DC's own description.
export function nigpLabel(cls) { return NIGP[cls] ? `${cls} ${NIGP[cls]}` : String(cls); }
