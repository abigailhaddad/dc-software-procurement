// Small page behaviour shared by every page. A plain script, not a module, so
// it works on pages with no other JavaScript.
//
// 1. Click a column heading in any table to sort by it; click again to reverse.
//    DataTables tables sort themselves and are skipped. Tables drawn after the
//    page loads work too, because the click is caught on the document.
// 2. The nav's drop-down menus close when you click anywhere else.

// A cell can carry data-sort to sort by something other than its text, such
// as an ISO date-time behind "2:00 PM"; data-sort="" sorts as blank.
function cellText(row, index) {
  const cell = row.children[index];
  if (!cell) return "";
  if (cell.dataset.sort !== undefined) return cell.dataset.sort;
  return cell.textContent.trim();
}

// "$1.2M", "3,462", "28%", "-$5" -> a number; anything else -> null.
function cellNumber(text) {
  const m = text.replace(/[,\s]/g, "").match(/^(-?)\$?(-?\d+(?:\.\d+)?)([KMB]?)%?$/i);
  if (!m) return null;
  const scale = {"": 1, k: 1e3, m: 1e6, b: 1e9}[m[3].toLowerCase()];
  const value = Number(m[2]) * scale;
  return m[1] === "-" ? -value : value;
}

function isBlank(text) {
  return text === "" || text === "–" || text === "—" || text === "-";
}

function sortTable(th) {
  const table = th.closest("table");
  const body = table.tBodies[0];
  if (!body) return;
  const index = Array.from(th.parentNode.children).indexOf(th);
  const rows = Array.from(body.rows);
  // Rows that span columns (group headings, expanded details) would scramble; leave those tables alone.
  if (rows.some((r) => Array.from(r.cells).some((c) => c.rowSpan > 1 || c.colSpan > 1))) return;

  const texts = rows.map((r) => cellText(r, index));
  const numeric = texts.every((t) => isBlank(t) || cellNumber(t) !== null);
  const descending = th.getAttribute("aria-sort") === "ascending";

  rows.sort((a, b) => {
    const x = cellText(a, index);
    const y = cellText(b, index);
    // Blanks always go last, whichever way the column is sorted.
    if (isBlank(x) && isBlank(y)) return 0;
    if (isBlank(x)) return 1;
    if (isBlank(y)) return -1;
    const order = numeric ? cellNumber(x) - cellNumber(y) : x.localeCompare(y, undefined, {numeric: true, sensitivity: "base"});
    return descending ? -order : order;
  });
  for (const r of rows) body.appendChild(r);

  for (const other of th.parentNode.children) other.removeAttribute("aria-sort");
  th.setAttribute("aria-sort", descending ? "descending" : "ascending");
}

document.addEventListener("click", (event) => {
  const th = event.target.closest("thead th");
  if (th && !th.closest("table.dataTable") && !event.target.closest("a, button, input, select")) {
    sortTable(th);
  }
  for (const menu of document.querySelectorAll("nav.pages details[open]")) {
    if (!menu.contains(event.target)) menu.open = false;
  }
});
