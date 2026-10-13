// The site's whole data layer: DuckDB-WASM over the archive's Parquet tables.
//
// Modelled on usajobs_historical/web/shared/wasm-api.js, and keeping what that
// file measured:
//   - each table is fetched once and queried from memory, which beat range
//     reads on a cold load (3.8s against 9.4s there);
//   - COUNT comes back as BigInt and is turned into a Number at the boundary;
//   - DATE comes back as epoch milliseconds, so dates are cast to VARCHAR in SQL.
//
// Plain functions, one module-level connection, no client object.

import * as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm";

const HF = "https://huggingface.co/datasets/abigailhaddad/dc-procurement-raw/resolve/main/archive/";

// Served from this machine (make serve), the pages read the copy `make
// export` writes, so a build can be checked before it is published; ?data=hf
// reads the published copy instead. Anywhere else they read the published copy
// (and ?data=local forces the local one). Pages live in site/, the export in
// data/raw/.
const DATA_PARAM = new URLSearchParams(location.search).get("data");
const ON_THIS_MACHINE = ["127.0.0.1", "localhost"].includes(location.hostname);
export const LOCAL = DATA_PARAM === "local" || (ON_THIS_MACHINE && DATA_PARAM !== "hf");
export const BASE = LOCAL ? "../data/raw/" : HF;
export const PARQUET_DIR = BASE + "parquet/";
export const DATASET_PAGE = "https://huggingface.co/datasets/abigailhaddad/dc-procurement-raw/tree/main/archive";

let conn = null;
let database = null;

export async function boot(tables) {
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerUrl = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker}");`], {type: "text/javascript"}));
  database = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), new Worker(workerUrl));
  await database.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);
  conn = await database.connect();
  for (const table of tables) {
    const url = new URL(PARQUET_DIR + table + ".parquet", location.href).href;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`could not load ${table}: ${response.status} ${response.statusText}`);
    await database.registerFileBuffer(table + ".parquet", new Uint8Array(await response.arrayBuffer()));
    await conn.query(`CREATE VIEW ${table} AS SELECT * FROM '${table}.parquet'`);
  }
}

function plain(value) {
  if (typeof value === "bigint") return Number(value);
  if (value && typeof value === "object" && typeof value.toArray === "function") return value.toArray().map(plain);
  return value;
}

/** Run SQL with positional binds; rows come back as plain objects. */
export async function q(sql, binds = []) {
  if (!conn) throw new Error("boot() has not finished");
  let result;
  if (binds.length) {
    const stmt = await conn.prepare(sql);
    try {
      result = await stmt.query(...binds);
    } finally {
      await stmt.close();
    }
  } else {
    result = await conn.query(sql);
  }
  return result.toArray().map((row) => {
    const obj = row.toJSON();
    for (const k of Object.keys(obj)) obj[k] = plain(obj[k]);
    return obj;
  });
}

/** Write a query's full result to CSV inside DuckDB and hand it to the browser. */
export async function downloadCsv(sql, binds, filename) {
  // COPY cannot take bind parameters, so the filtered rows go through a temp
  // table first.
  await q("DROP TABLE IF EXISTS _export");
  await q(`CREATE TEMP TABLE _export AS ${sql}`, binds);
  // A fresh file name each time: DuckDB-WASM hands back the first buffer it
  // wrote under a name, so reusing 'export.csv' repeated the first download.
  const file = `export-${Date.now()}-${Math.random().toString(36).slice(2)}.csv`;
  await conn.query(`COPY _export TO '${file}' (HEADER, DELIMITER ',')`);
  const buffer = await database.copyFileToBuffer(file);
  await database.dropFile(file);
  await q("DROP TABLE _export");
  const url = URL.createObjectURL(new Blob([buffer], {type: "text/csv"}));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function manifest() {
  const response = await fetch(new URL(BASE + "manifest.json", location.href).href);
  if (!response.ok) throw new Error(`could not load the manifest: ${response.status}`);
  return response.json();
}
