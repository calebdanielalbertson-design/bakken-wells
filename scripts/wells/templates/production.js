// Production panel of a well page: the chart's gas and water lines (built here from the page's own data when
// the visitor asks for them; the page carries the oil line), the series toggle, and the CSV download.
// The page embeds its data as {start: "YYYY-MM", oil: "a,b,,c", water: ..., gas: ..., gasSold: ..., flared: ..., days: ...}:
// one comma-joined string per series from the first month, an empty field for an unknown month.
// seriesGroup mirrors series_group in scripts/wells/production.py (same geometry, arithmetic and rounding);
// scripts/tests/test_production.py checks that the two produce identical markup.

export const SERIES = {oil: ['Oil', 'bbl'], gas: ['Gas', 'mcf'], water: ['Water', 'bbl']};
export const CSV_SERIES = ['oil', 'water', 'gas', 'gasSold', 'flared', 'days'];
export const GEOMETRY = {width: 640, height: 220, left: 52, right: 10, top: 10, bottom: 22};

// Python's f'{x:.0f}': nearest integer, ties to even.
const fmt = (v) => {
  const f = Math.floor(v);
  if (v - f === 0.5) return String(f % 2 === 0 ? f : f + 1);
  return String(Math.round(v));
};
const thousands = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

export const decode = (raw) => {
  const data = {start: raw.start};
  for (const key of CSV_SERIES) {
    const v = raw[key];
    if (v == null) data[key] = null;
    else if (Array.isArray(v)) data[key] = v;
    else data[key] = String(v).split(',').map((t) => (t === '' ? null : Number(t)));
  }
  return data;
};

export const seriesGroup = (values, series, n = values.length) => {
  const [label, unit] = SERIES[series];
  const g = GEOMETRY;
  const plotW = g.width - g.left - g.right;
  const plotH = g.height - g.top - g.bottom;
  const step = n > 1 ? plotW / (n - 1) : 0;
  const known = values.filter((x) => x != null);
  const vmax = known.length ? Math.max(...known) : 0;
  const scale = vmax ? plotH / vmax : 0;
  const d = [];
  let penDown = false;
  values.forEach((value, i) => {
    if (value == null) { penDown = false; return; }
    const x = n > 1 ? g.left + i * step : g.left + plotW / 2;
    const y = g.top + plotH - value * scale;
    d.push(`${penDown ? 'L' : 'M'}${fmt(x)},${fmt(y)}`);
    penDown = true;
  });
  let ticks = '';
  for (const fraction of [0, 0.5, 1]) {
    const y = g.top + plotH - fraction * plotH;
    ticks += `<line x1="${g.left}" y1="${fmt(y)}" x2="${g.width - g.right}" y2="${fmt(y)}"/>`
      + `<text x="${g.left - 6}" y="${fmt(y + 4)}">${thousands(Math.floor(vmax * fraction))}</text>`;
  }
  const path = d.join(' ') || `M${g.left},${g.top + plotH}`;
  return `<g class="s-${series}"><g class="grid">${ticks}</g><text class="unit" x="${g.left + 8}" y="${g.top + 12}">${label}, ${unit} per month</text>`
    + `<path d="${path}"><title>${label}, ${unit} per month</title></path></g>`;
};

// One row per month from the first reported month; an unknown month is an empty cell, never 0.
export const buildCsv = (data) => {
  const lines = ['month,oil_bbl,water_bbl,gas_mcf,gas_sold_mcf,flared_mcf,days'];
  let [year, month] = data.start.split('-').map(Number);
  for (let i = 0; i < data.oil.length; i++) {
    const cells = CSV_SERIES.map((k) => (data[k] && data[k][i] != null ? data[k][i] : ''));
    lines.push(`${year}-${String(month).padStart(2, '0')},${cells.join(',')}`);
    month += 1;
    if (month === 13) { month = 1; year += 1; }
  }
  lines.push('# Source: NDIC Monthly Production Reports (https://www.dmr.nd.gov/oilgas/mprindex.asp), values as first reported; blank = unknown');
  return lines.join('\n') + '\n';
};

const SVG_NS = 'http://www.w3.org/2000/svg';

export function mount(doc) {
  const panel = doc.querySelector('section.production');
  if (!panel) return;
  const chart = panel.querySelector('svg.prodchart');
  const raw = doc.getElementById('production-data');
  let data = null;
  const load = () => {
    if (data === null && raw) {
      try { data = decode(JSON.parse(raw.textContent)); } catch { data = false; }
    }
    return data || null;
  };
  const ensureSeries = (key) => {
    if (!chart || chart.querySelector(`.s-${key}`)) return;
    const d = load();
    if (!d || !d[key]) return;
    const parsed = new DOMParser().parseFromString(`<svg xmlns="${SVG_NS}">${seriesGroup(d[key], key, d.oil.length)}</svg>`, 'image/svg+xml');
    const group = parsed.documentElement.firstChild;
    if (group) chart.appendChild(doc.importNode(group, true));
  };
  for (const button of panel.querySelectorAll('.toggle button')) {
    button.addEventListener('click', () => {
      const key = button.dataset.series;
      for (const other of panel.querySelectorAll('.toggle button')) other.classList.toggle('on', other === button);
      ensureSeries(key);
      if (chart) chart.setAttribute('class', `prodchart show-${key}`);
    });
  }
  const csv = panel.querySelector('button.csv');
  if (!csv) return;
  csv.addEventListener('click', () => {
    const d = load();
    if (!d) return;
    const blob = new Blob([buildCsv(d)], {type: 'text/csv'});
    const link = doc.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = csv.dataset.name || 'production.csv';
    doc.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
  });
}

if (typeof document !== 'undefined') {
  mount(document);
  window.bakkenProductionCsv = (raw) => buildCsv(decode(raw));
}
