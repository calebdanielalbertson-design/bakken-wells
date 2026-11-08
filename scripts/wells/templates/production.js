// Production panel of a well page. The page carries the stat tiles, the facts, the table and one strip (oil in
// barrels per producing day); this module builds the rest from the data embedded in the page: the controls, the
// gas and water strips, volume and cumulative views, log scale, the gas-oil ratio, water cut and uptime strips,
// the hover readout, an Arps decline fit and the CSV.
// The page embeds its data as {start: "YYYY-MM", oil: "a,b,,c", water: ..., gas: ..., gasSold: ..., flared: ..., days: ...}:
// one comma-joined string per series from the first month, an empty field for an unknown month.
// stripSvg mirrors strip_svg in scripts/wells/production.py (same geometry, arithmetic, rounding and text), so the
// oil strip the page carries and the one drawn here are identical; scripts/tests/test_production.py checks that.

export const SERIES = {oil: ['Oil', 'bbl'], gas: ['Gas', 'mcf'], water: ['Water', 'bbl']};
export const CSV_SERIES = ['oil', 'water', 'gas', 'gasSold', 'flared', 'days'];
export const GEO = {main: {W: 820, LEFT: 58, RIGHT: 74, STRIP: 112}, diag: {W: 280, LEFT: 44, RIGHT: 44, STRIP: 84}};
export const GAP = 26, TOP = 22, AXIS = 26;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// ---------------------------------------------------------------- months and numbers (mirrored from production.py)
export const addMonths = (first, i) => { let [y, m] = first.split('-').map(Number); m += i; y += Math.floor((m - 1) / 12); m = ((m - 1) % 12) + 1; return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}`; };
export const monthLabel = (key, short = false) => { const m = Number(key.slice(5, 7)); return short ? `${MONTHS[m - 1]} ’${key.slice(2, 4)}` : `${MONTHS[m - 1]} ${key.slice(0, 4)}`; };
const daysIn = (key) => new Date(Number(key.slice(0, 4)), Number(key.slice(5, 7)), 0).getDate();
export const thousands = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
// Python's f'{x:.0f}': nearest integer, ties to even.
export const fmt = (v) => { const f = Math.floor(v); if (v - f === 0.5) return String(f % 2 === 0 ? f : f + 1); return String(Math.round(v)); };
// Python's f'{x:.{d}f}' for the values the chart prints: ties to even at the last digit.
export const fixed = (v, d) => { const p = 10 ** d; const s = v * p; const f = Math.floor(s); const n = (s - f === 0.5) ? (f % 2 === 0 ? f : f + 1) : Math.round(s); return (n / p).toFixed(d); };
const trim0 = (s) => s.includes('.') ? s.slice(0, -1).replace(/0+$/, '').replace(/\.$/, '') + s.slice(-1) : s;
export const compact = (value) => {
  if (value == null) return '—';
  const v = Math.abs(value);
  if (v >= 1e6) return trim0(fixed(value / 1e6, v >= 1e7 ? 1 : 2) + 'M');
  if (v >= 1e4) return trim0(fixed(value / 1e3, v >= 1e5 ? 0 : 1) + 'K');
  return thousands(fmt(value));
};
export const niceTicks = (vmax) => {
  if (!(vmax > 0)) return [0, 1];
  const raw = vmax / 3, mag = 10 ** Math.floor(Math.log10(raw)), norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const count = Math.ceil(vmax / step - 1e-9);
  const ticks = []; for (let k = 0; k <= count; k++) ticks.push(+(k * step).toFixed(10));
  return ticks;
};
const logTicks = (min, max) => {
  let hi = Math.ceil(Math.log10(max)), lo = Math.floor(Math.log10(Math.max(min, max / 1e4)));
  if (hi - lo > 4) lo = hi - 4; if (hi <= lo) hi = lo + 1;
  const t = []; for (let e = lo; e <= hi; e++) t.push(10 ** e); return t;
};
export const tickLabel = (value, ticks) => {
  const top = ticks[ticks.length - 1];
  if (ticks.every(Number.isInteger)) return top >= 10000 ? compact(value) : thousands(value);
  const decimals = ticks[1] > 0 && ticks[1] < 0.1 ? 2 : 1;
  const [int, frac] = fixed(value, decimals).split('.');
  return `${thousands(int)}.${frac}`;
};
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

// ---------------------------------------------------------------- data
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

// Derived series: rates per producing day, cumulatives, monthly and trailing-three-month ratios, uptime.
export const derive = (data) => {
  const n = data.oil.length, days = data.days || [];
  const rate = {}, cum = {};
  for (const k of ['oil', 'gas', 'water']) {
    const s = data[k] || [];
    rate[k] = Array.from({length: n}, (_, i) => (s[i] == null || days[i] == null || days[i] <= 0) ? null : s[i] / days[i]);
    let c = 0; cum[k] = Array.from({length: n}, (_, i) => { if (s[i] != null) c += s[i]; return c; });
  }
  const gas = data.gas || [], water = data.water || [];
  const gor = data.oil.map((o, i) => (o && gas[i] != null) ? gas[i] * 1000 / o : null);
  const wcut = data.oil.map((o, i) => (o != null && water[i] != null && o + water[i] > 0) ? 100 * water[i] / (o + water[i]) : null);
  const uptime = Array.from({length: n}, (_, i) => days[i] == null ? null : 100 * days[i] / daysIn(addMonths(data.start, i)));
  // trailing three months: steadier than one month, where a few barrels of oil make a wild ratio
  const roll = (num, den, f) => data.oil.map((_, i) => { let a = 0, b = 0, k = 0; for (let j = Math.max(0, i - 2); j <= i; j++) if (num[j] != null && den[j] != null) { a += num[j]; b += den[j]; k++; } return k ? f(a, b) : null; });
  const gor3 = roll(gas, data.oil, (g, o) => o > 0 ? g * 1000 / o : null);
  const wcut3 = roll(water, data.oil, (wa, o) => wa + o > 0 ? 100 * wa / (wa + o) : null);
  return {n, rate, cum, gor, wcut, uptime, gor3, wcut3};
};

// ---------------------------------------------------------------- Arps modified hyperbolic
// The conventions of the operator prioritisation study (v02_dca_economics.py): fit from the peak of the first six
// months (or a given month), months with fewer than 20 producing days left out, soft-L1 loss in log rate, b in
// [0.05, 1.5], terminal decline 8 %/yr, 30-year life from first production, 5 bbl/d floor, the model scaled to
// reproduce the actual cumulative over the fitted months (0.75 to 1.05) so downtime is carried forward.
export const DMIN = 0.08 / 12, HORIZON = 360, QLIM = 150;
export const mh = (t, qi, di, b) => {
  if (di <= DMIN) return qi * Math.exp(-di * t);
  const tsw = (di / DMIN - 1) / (b * di);
  if (t <= tsw) return qi / Math.pow(1 + b * di * t, 1 / b);
  return qi / Math.pow(1 + b * di * tsw, 1 / b) * Math.exp(-DMIN * (t - tsw));
};
const nelderMead = (f, x0, step, iters = 400) => {
  const n = x0.length; let simplex = [x0.slice()];
  for (let i = 0; i < n; i++) { const p = x0.slice(); p[i] += step[i]; simplex.push(p); }
  let vals = simplex.map(f);
  for (let it = 0; it < iters; it++) {
    const order = vals.map((v, i) => i).sort((a, b) => vals[a] - vals[b]);
    simplex = order.map((i) => simplex[i]); vals = order.map((i) => vals[i]);
    const best = simplex[0], worst = simplex[n];
    const centroid = Array(n).fill(0); for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) centroid[j] += simplex[i][j] / n;
    const refl = centroid.map((c, j) => 2 * c - worst[j]); const fr = f(refl);
    if (fr < vals[0]) { const exp = centroid.map((c, j) => 3 * c - 2 * worst[j]); const fe = f(exp); if (fe < fr) { simplex[n] = exp; vals[n] = fe; } else { simplex[n] = refl; vals[n] = fr; } }
    else if (fr < vals[n - 1]) { simplex[n] = refl; vals[n] = fr; }
    else { const con = centroid.map((c, j) => (c + worst[j]) / 2); const fc = f(con);
      if (fc < vals[n]) { simplex[n] = con; vals[n] = fc; }
      else { for (let i = 1; i <= n; i++) { simplex[i] = simplex[i].map((x, j) => (x + best[j]) / 2); vals[i] = f(simplex[i]); } } }
    if (Math.abs(vals[n] - vals[0]) < 1e-9) break;
  }
  return {x: simplex[0], f: vals[0]};
};
export const fitArps = (oil, days, from = null) => {
  const n = oil.length; if (n < 15) return null;
  let p = 0; for (let i = 1; i < Math.min(6, n); i++) if ((oil[i] ?? -1) > (oil[p] ?? -1)) p = i;
  if (from != null) p = Math.max(0, Math.min(from, n - 12));
  const q = oil.slice(p), d = days.slice(p), m = q.length;
  const ok = q.map((x, i) => x != null && x > 0 && d[i] != null && d[i] >= 20); if (from == null) ok[0] = q[0] != null && q[0] > 0;
  const nFit = ok.filter(Boolean).length; if (nFit < 12) return null;
  const qpk = from == null ? q[0] : Math.max(...q.filter((x, i) => ok[i])), F = 0.3;
  const lo = [Math.log(0.3 * qpk), Math.log(0.005), 0.05], hi = [Math.log(3 * qpk), Math.log(2), 1.5];
  const clamp = (th) => [Math.exp(Math.min(Math.max(th[0], lo[0]), hi[0])), Math.exp(Math.min(Math.max(th[1], lo[1]), hi[1])), Math.min(Math.max(th[2], lo[2]), hi[2])];
  const cost = (th) => {
    let pen = 0; for (let j = 0; j < 3; j++) { if (th[j] < lo[j]) pen += 1e3 * (lo[j] - th[j]) ** 2; if (th[j] > hi[j]) pen += 1e3 * (th[j] - hi[j]) ** 2; }
    const [qi, di, b] = clamp(th);
    let c = 0; for (let i = 0; i < m; i++) { if (!ok[i]) continue; const r = Math.log(mh(i, qi, di, b)) - Math.log(q[i]); c += F * F * (Math.sqrt(1 + (r / F) ** 2) - 1); }  // scipy soft_l1
    return c + pen;
  };
  let best = null;
  for (const b0 of [0.6, 1.0, 1.3]) { const r = nelderMead(cost, [Math.log(qpk), Math.log(0.25), b0], [0.3, 0.7, 0.25]); if (!best || r.f < best.f) best = r; }
  const [qi, di, b] = clamp(best.x);
  const hist = q.map((_, i) => mh(i, qi, di, b));
  let sq = 0, sh = 0; for (let i = 0; i < m; i++) { sq += q[i] ?? 0; sh += hist[i]; }
  const scale = Math.min(1.05, Math.max(0.75, sq / sh));
  const forecast = []; for (let t = m; t < HORIZON - p; t++) { const f = mh(t, qi, di, b) * scale; if (f < QLIM) break; forecast.push(f); }
  let se = 0; for (let i = 0; i < m; i++) if (ok[i]) se += (Math.log(hist[i]) - Math.log(q[i])) ** 2;
  const deAnnual = di <= DMIN ? 1 - Math.exp(-12 * di) : 1 - Math.pow(1 + b * di * 12, -1 / b);
  return {qi, di, b, p, scale, hist, forecast, nFit, rmseLog: Math.sqrt(se / nFit), deAnnual};
};

// ---------------------------------------------------------------- one chart strip (mirrored from production.py)
export const xFor = (geo, idx0, idx1) => { const N = idx1 - idx0, pw = geo.W - geo.LEFT - geo.RIGHT; return (i) => N > 1 ? geo.LEFT + (i - idx0) * pw / (N - 1) : geo.LEFT + pw / 2; };

export const stripSvg = (geo, y0, title, unit, cls, values, idx0, idx1, showAxis, first, n, opts = {}) => {
  const {W, LEFT, RIGHT, STRIP: plotH} = geo, top = y0, N = idx1 - idx0, plotW = W - LEFT - RIGHT;
  const x = xFor(geo, idx0, idx1);
  const log = !!opts.log && cls !== 'pct' && cls !== 'ink';
  const extra = opts.extra || null;
  const vis = []; for (let i = idx0; i < Math.min(idx1, n); i++) if (values[i] != null) vis.push(values[i]);
  if (extra) for (let i = idx0; i < idx1; i++) { if (extra.fit && extra.fit[i] != null) vis.push(extra.fit[i]); if (extra.hist && extra.hist[i] != null) vis.push(extra.hist[i]); }
  const vmax = vis.length ? Math.max(...vis) : 0;
  const positives = vis.filter((v) => v > 0);
  const ticks = log ? logTicks(positives.length ? Math.min(...positives) : 1, vmax || 10) : niceTicks(cls === 'pct' ? 100 : vmax);
  const tmin = ticks[0], tmax = ticks[ticks.length - 1];
  const y = (v) => log ? top + plotH - (Math.log10(Math.max(v, tmin)) - Math.log10(tmin)) / (Math.log10(tmax) - Math.log10(tmin)) * plotH
                       : top + plotH - (v - tmin) / (tmax - tmin) * plotH;
  let out = '<g class="grid">';
  for (const t of ticks) out += `<line x1="${LEFT}" x2="${W - RIGHT}" y1="${fmt(y(t))}" y2="${fmt(y(t))}"/><text x="${LEFT - 8}" y="${fmt(y(t) + 4)}" text-anchor="end">${log ? compact(t) : tickLabel(t, ticks)}</text>`;
  out += '</g>';
  const half = N > 1 ? (x(idx0 + 1) - x(idx0)) / 2 : 6;
  for (let i = idx0; i < Math.min(idx1, n); i++) if (values[i] == null) out += `<rect class="band" x="${fmt(x(i) - half)}" y="${top}" width="${fmt(2 * half)}" height="${plotH}"/>`;
  out += `<text class="t" x="${LEFT}" y="${top - 8}">${esc(title)}<tspan> ${esc(unit)}</tspan></text>`;
  const path = (arr, from, to) => { const d = []; let pen = false; for (let i = from; i < to; i++) { const v = arr[i]; if (v == null || (log && v <= 0)) { pen = false; continue; } d.push(`${pen ? 'L' : 'M'}${fmt(x(i))},${fmt(y(v))}`); pen = true; } return d.join(' '); };
  if (extra && extra.hist) out += `<path class="hist ${cls}" d="${path(extra.hist, idx0, Math.min(idx1, n))}"/>`;
  if (extra && extra.fit) out += `<path class="fit ${cls}" d="${path(extra.fit, Math.max(idx0, (extra.forecastFrom || 0) - 1), idx1)}"/>`;
  out += `<path class="s ${cls}" d="${path(values, idx0, Math.min(idx1, n)) || `M${LEFT},${top + plotH}`}"/>`;
  let last = -1; for (let i = Math.min(idx1, n) - 1; i >= idx0; i--) if (values[i] != null) { last = i; break; }
  if (last >= 0 && !(log && values[last] <= 0)) {
    out += `<circle class="end ${cls}" cx="${fmt(x(last))}" cy="${fmt(y(values[last]))}" r="4"/>`;
    if (!extra || !extra.fit) out += `<text class="end" x="${fmt(x(last) + 9)}" y="${fmt(y(values[last]) + 4)}">${cls === 'pct' ? fixed(values[last], 0) + '%' : compact(Number(fmt(values[last])))}</text>`;
  }
  out += `<circle class="pt ${cls}" data-strip="${cls}" r="4" cx="0" cy="0"/>`;
  if (showAxis) {
    out += `<g class="axis"><line x1="${LEFT}" x2="${W - RIGHT}" y1="${fmt(top + plotH + 0.5)}" y2="${fmt(top + plotH + 0.5)}"/>`;
    const pxPerMonth = plotW / Math.max(1, N - 1);
    const want = Math.ceil(64 / pxPerMonth); const every = [1, 2, 3, 6, 12, 24, 48, 60, 120, 240].find((e) => e >= want) || 240;
    for (let i = idx0; i < idx1; i++) {
      const key = addMonths(first, i); const year = Number(key.slice(0, 4)), month = Number(key.slice(5, 7));
      if (every >= 12 ? (month === 1 && year % (every / 12) === 0) : ((i - idx0) % every === 0)) out += `<text x="${fmt(x(i))}" y="${top + plotH + 18}" text-anchor="middle">${every >= 12 ? year : monthLabel(key, true)}</text>`;
    }
    out += '</g>';
  }
  return {svg: out, y};
};

// ---------------------------------------------------------------- CSV: one row per month, blank = unknown
export const buildCsv = (data) => {
  const d = derive(data);
  const lines = ['month,oil_bbl,water_bbl,gas_mcf,gas_sold_mcf,flared_mcf,days,oil_bbl_per_day,gor_scf_per_bbl,water_cut_pct'];
  for (let i = 0; i < d.n; i++) {
    const cells = CSV_SERIES.map((k) => (data[k] && data[k][i] != null ? data[k][i] : ''));
    const c = (x, dp) => x == null ? '' : x.toFixed(dp);
    lines.push(`${addMonths(data.start, i)},${cells.join(',')},${c(d.rate.oil[i], 1)},${c(d.gor[i], 0)},${c(d.wcut[i], 1)}`);
  }
  lines.push('# Source: NDIC Monthly Production Reports (https://www.dmr.nd.gov/oilgas/mprindex.asp), values as first reported; blank = unknown');
  return lines.join('\n') + '\n';
};

// ---------------------------------------------------------------- the panel in the browser
const CONTROLS = `<div class="controls" role="group" aria-label="Chart options">
<div class="switch"><span>Show</span><div class="seg" data-opt="measure"><button type="button" data-v="rate" aria-pressed="true">Rate per producing day</button><button type="button" data-v="volume" aria-pressed="false">Volume per month</button><button type="button" data-v="cum" aria-pressed="false">Cumulative</button></div></div>
<div class="switch"><span>Scale</span><div class="seg" data-opt="scale"><button type="button" data-v="lin" aria-pressed="true">Linear</button><button type="button" data-v="log" aria-pressed="false">Log</button></div></div>
<div class="switch"><span>Months</span><div class="seg" data-opt="range"><button type="button" data-v="all" aria-pressed="true">All</button><button type="button" data-v="first36" aria-pressed="false">First 36</button><button type="button" data-v="last60" aria-pressed="false">Last 60</button></div></div>
<label class="chk"><input type="checkbox" class="fit-toggle"> Decline fit (Arps)</label></div><ul class="legend"></ul>`;

export function mount(doc) {
  const panel = doc.querySelector('section.production');
  const raw = panel && doc.getElementById('production-data');
  const chartwrap = panel && panel.querySelector('.chartwrap');
  if (!panel || !raw || !chartwrap) return;
  let data;
  try { data = decode(JSON.parse(raw.textContent)); } catch { return; }
  const d = derive(data), n = d.n, first = data.start;
  const state = {measure: 'rate', scale: 'lin', range: 'all', fit: false, fitFrom: 'peak', hover: null};
  const fits = {};
  let cur = null;
  const diag = panel.querySelector('.diag') || chartwrap.insertAdjacentElement('afterend', doc.createElement('div'));
  diag.className = 'diag';
  chartwrap.insertAdjacentHTML('beforebegin', CONTROLS);
  const legend = panel.querySelector('.legend');
  const fitBox = doc.createElement('div'); fitBox.className = 'fit'; fitBox.hidden = true; diag.insertAdjacentElement('afterend', fitBox);
  chartwrap.tabIndex = 0; chartwrap.setAttribute('aria-label', 'Production chart. Use the left and right arrow keys to read month by month.');

  const unitOf = (k) => state.measure === 'rate' ? `${SERIES[k][1]}/d` : state.measure === 'cum' ? `${SERIES[k][1]}, cumulative` : `${SERIES[k][1]} per month`;
  const measureOf = (k) => state.measure === 'rate' ? d.rate[k] : state.measure === 'cum' ? d.cum[k] : (data[k] || []);
  const fitNow = () => { if (!state.fit) return null; const key = state.fitFrom; if (!(key in fits)) fits[key] = fitArps(data.oil, data.days || [], key === 'last60' ? Math.max(0, n - 60) : null); return fits[key]; };

  const li = (cls, text, keyClass = '') => { const el = doc.createElement('li'); if (cls) el.className = cls; const i = doc.createElement('i'); if (keyClass) i.className = keyClass; el.append(i, text); return el; };

  function render() {
    const fit = fitNow();
    let idx0 = 0, idx1 = n;
    if (state.range === 'first36') idx1 = Math.min(36, n);
    if (state.range === 'last60') idx0 = Math.max(0, n - 60);
    let extra = null;
    if (fit && state.range === 'all') {
      const histVol = Array(n).fill(null); for (let i = 0; i < fit.hist.length; i++) histVol[fit.p + i] = fit.hist[i] * fit.scale;
      const fitVol = Array(n + fit.forecast.length).fill(null); fitVol[n - 1] = histVol[n - 1]; for (let i = 0; i < fit.forecast.length; i++) fitVol[n + i] = fit.forecast[i];
      const conv = (arr) => state.measure === 'rate' ? arr.map((v) => v == null ? null : v / 30.4375)
        : state.measure === 'cum' ? (() => { let c = d.cum.oil[n - 1]; return arr.map((v, i) => i < n ? (i === n - 1 ? c : null) : (c += v)); })() : arr;
      extra = {hist: state.measure === 'cum' ? null : conv(histVol), fit: conv(fitVol), forecastFrom: n};
      idx1 = n + fit.forecast.length;
    }
    // legend
    legend.textContent = '';
    for (const k of ['oil', 'gas', 'water']) legend.append(li(k, `${SERIES[k][0]}, ${unitOf(k)}`));
    if (extra) legend.append(li('oil', 'Arps fit and forecast to a 5 bbl/d floor', 'dash'));
    if (data.oil.some((v) => v == null)) legend.append(li('', 'Unknown month (confidential or not in that report)', 'band'));
    // the three strips over one time axis
    const G = GEO.main, xm = xFor(G, idx0, idx1), H = TOP + 3 * G.STRIP + 2 * GAP + AXIS, ys = {};
    let svg = `<svg class="prodchart main" viewBox="0 0 ${G.W} ${H}" role="img" aria-label="Oil, gas and water, ${monthLabel(first)} to ${monthLabel(addMonths(first, n - 1))}">`;
    ['oil', 'gas', 'water'].forEach((k, j) => {
      const r = stripSvg(G, TOP + j * (G.STRIP + GAP), SERIES[k][0], unitOf(k), k, measureOf(k), idx0, idx1, j === 2, first, n, {log: state.scale === 'log', extra: k === 'oil' ? extra : null});
      svg += r.svg; ys[k] = r.y;
    });
    svg += `<line class="cross" x1="0" x2="0" y1="${TOP - 4}" y2="${TOP + 3 * G.STRIP + 2 * GAP}"/><rect class="hit" x="${G.LEFT}" y="${TOP - 10}" width="${G.W - G.LEFT - G.RIGHT}" height="${3 * G.STRIP + 2 * GAP + 10}"/></svg><div class="tip"></div>`;
    chartwrap.innerHTML = svg;
    cur = {fit, charts: [{svg: chartwrap.querySelector('svg'), wrap: chartwrap, geo: G, idx0, idx1, N: idx1 - idx0, x: xm, ys}]};
    // diagnostics over the same months, never the forecast
    diag.textContent = '';
    const D = GEO.diag, d1 = Math.min(idx1, n), xd = xFor(D, idx0, d1), dH = TOP + D.STRIP + AXIS;
    for (const [t, u, cls, key, arr] of [['Gas–oil ratio', 'scf/bbl, trailing 3 months', 'gas', 'gor', d.gor3], ['Water cut', '% of liquids, trailing 3 months', 'pct', 'wcut', d.wcut3], ['Uptime', '% of days producing', 'ink', 'uptime', d.uptime]]) {
      const r = stripSvg(D, TOP, t, u, cls, arr, idx0, d1, true, first, n, {log: state.scale === 'log'});
      const div = doc.createElement('div'); div.className = 'chartwrap';
      div.innerHTML = `<svg class="prodchart" viewBox="0 0 ${D.W} ${dH}" role="img" aria-label="${esc(t)}">${r.svg}<line class="cross" x1="0" x2="0" y1="${TOP - 4}" y2="${TOP + D.STRIP}"/><rect class="hit" x="${D.LEFT}" y="${TOP - 10}" width="${D.W - D.LEFT - D.RIGHT}" height="${D.STRIP + 10}"/></svg><div class="tip"></div>`;
      diag.append(div);
      cur.charts.push({svg: div.querySelector('svg'), wrap: div, geo: D, idx0, idx1: d1, N: d1 - idx0, x: xd, ys: {[key]: r.y}});
    }
    renderFit(fit);
    for (const el of panel.querySelectorAll('.hit')) { el.addEventListener('pointermove', onHover); el.addEventListener('pointerleave', hideHover); }
  }

  function renderFit(fit) {
    fitBox.hidden = !state.fit;
    if (!state.fit) return;
    const seg = `<div class="switch" style="margin:4px 0 8px"><span>Fit</span><div class="seg" data-opt="fitFrom"><button type="button" data-v="peak" aria-pressed="${state.fitFrom === 'peak'}">From the peak</button><button type="button" data-v="last60" aria-pressed="${state.fitFrom === 'last60'}">Last 60 months</button></div></div>`;
    if (!fit) { fitBox.innerHTML = `<div><h3>No decline fit</h3>${seg}<p>Fewer than 12 full producing months in that window, so no curve is fitted. Unknown is not zero.</p></div>`; return; }
    const cum = d.cum.oil[n - 1], rem = fit.forecast.reduce((s, v) => s + v, 0), yrs = fit.forecast.length / 12;
    const regime = fit.b >= 1 ? 'A b at or above 1 is the long, flat tail typical of fracture-dominated flow in tight rock; the 8 %/yr terminal decline keeps the forecast from running away.'
      : fit.b >= 0.5 ? 'A mid-range b: hyperbolic decline already bending toward the terminal rate.' : 'A low b: close to exponential decline, as for a well in boundary-dominated flow.';
    const fromText = state.fitFrom === 'last60' ? `from ${monthLabel(addMonths(first, fit.p))} (the last 60 months)` : `from the peak month (${monthLabel(addMonths(first, fit.p))})`;
    fitBox.innerHTML = `<div><h3>Arps decline fit, oil</h3>${seg}
<p>Modified hyperbolic fitted to the monthly volumes ${fromText}, ${fit.nFit} full producing months used, months with fewer than 20 producing days left out. Terminal decline 8 %/yr, forecast to a 5 bbl/d floor within a 30-year life, scaled by ${fit.scale.toFixed(2)} to reproduce the actual cumulative over the fitted months. ${regime}</p>
<p>Orientation only: a single-well fit to public monthly volumes as first reported, not a reserves estimate. Fit error ±${fit.rmseLog.toFixed(2)} in log rate.</p></div>
<dl><dt>q<sub>i</sub></dt><dd>${thousands(Math.round(fit.qi / 30.4375))} bbl/d</dd><dt>Initial decline</dt><dd>${(fit.deAnnual * 100).toFixed(0)} %/yr effective</dd><dt>b</dt><dd>${fit.b.toFixed(2)}</dd>
<dt>Produced to date</dt><dd>${compact(cum)} bbl</dd><dt>Remaining to floor</dt><dd>${compact(rem)} bbl over ${yrs.toFixed(1)} yr</dd><dt>EUR, this fit</dt><dd>${compact(cum + rem)} bbl</dd></dl>`;
  }

  function onHover(ev) {
    const svg = ev.currentTarget.ownerSVGElement; const chart = cur.charts.find((c) => c.svg === svg); if (!chart) return;
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    const i = Math.round(chart.idx0 + (p.x - chart.geo.LEFT) / (chart.geo.W - chart.geo.LEFT - chart.geo.RIGHT) * (chart.N - 1));
    showAt(Math.min(chart.idx1 - 1, Math.max(chart.idx0, i)), chart);
  }
  function hideHover() { for (const el of panel.querySelectorAll('.cross,.pt')) el.style.opacity = 0; for (const t of panel.querySelectorAll('.tip')) t.style.display = 'none'; state.hover = null; }
  function showAt(i, active = cur.charts[0]) {
    state.hover = i; const fit = cur.fit;
    const vals = {}; for (const k of ['oil', 'gas', 'water']) vals[k] = i < n ? measureOf(k)[i] : null;
    const pts = {...vals, gor: i < n ? d.gor3[i] : null, wcut: i < n ? d.wcut3[i] : null, uptime: i < n ? d.uptime[i] : null};
    for (const c of cur.charts) {
      const inRange = i >= c.idx0 && i < c.idx1; const cx = c.x(Math.min(c.idx1 - 1, Math.max(c.idx0, i)));
      for (const el of c.svg.querySelectorAll('.cross')) { el.setAttribute('x1', cx); el.setAttribute('x2', cx); el.style.opacity = inRange ? 1 : 0; }
      for (const el of c.svg.querySelectorAll('circle.pt')) {
        const k = el.dataset.strip; const key = k === 'pct' ? 'wcut' : k === 'ink' ? 'uptime' : (c.ys[k] ? k : 'gor'); const v = pts[key];
        if (!inRange || v == null || !c.ys[key] || (state.scale === 'log' && v <= 0 && key !== 'wcut' && key !== 'uptime')) { el.style.opacity = 0; continue; }
        el.setAttribute('cx', cx); el.setAttribute('cy', c.ys[key](v)); el.style.opacity = 1;
      }
    }
    for (const t of panel.querySelectorAll('.tip')) t.style.display = 'none';
    const tip = active.wrap.querySelector('.tip'); tip.textContent = '';
    const b = doc.createElement('b'); b.textContent = i < n ? monthLabel(addMonths(first, i)) : `${monthLabel(addMonths(first, i))} · forecast`; tip.append(b);
    const row = (val, lab, cls) => { const div = doc.createElement('div'); const a = doc.createElement('span'); a.textContent = val; const s = doc.createElement('span'); if (cls) { const ii = doc.createElement('i'); ii.className = cls; s.append(ii); } s.append(lab); div.append(a, s); tip.append(div); };
    if (i < n) {
      const unit = (k) => state.measure === 'rate' ? `${SERIES[k][1]}/d` : SERIES[k][1];
      for (const k of ['oil', 'gas', 'water']) row(vals[k] == null ? 'unknown' : thousands(Math.round(vals[k])), `${SERIES[k][0].toLowerCase()}, ${unit(k)}${state.measure === 'cum' ? ' cum' : ''}`, k);
      const days = (data.days || [])[i]; row(days == null ? 'unknown' : `${days} d`, 'producing days');
      if (d.gor[i] != null) row(thousands(Math.round(d.gor[i])), 'GOR, scf/bbl');
      if (d.wcut[i] != null) row(d.wcut[i].toFixed(0) + '%', 'water cut');
      if (fit && i >= fit.p && fit.hist[i - fit.p] != null) row(thousands(Math.round(fit.hist[i - fit.p] * fit.scale / (state.measure === 'rate' ? 30.4375 : 1))), state.measure === 'rate' ? 'fit, bbl/d' : 'fit, bbl', 'oil');
    } else if (fit) {
      const f = fit.forecast[i - n]; row(thousands(Math.round(state.measure === 'rate' ? f / 30.4375 : f)), state.measure === 'rate' ? 'forecast oil, bbl/d' : 'forecast oil, bbl', 'oil');
    }
    const {W, LEFT, RIGHT} = active.geo; const px = active.x(Math.min(active.idx1 - 1, Math.max(active.idx0, i)));
    const rect = active.wrap.getBoundingClientRect(); const frac = (px - LEFT) / (W - LEFT - RIGHT); const left = rect.width * px / W;
    tip.style.display = 'block'; tip.style.top = '8px';
    if (frac > 0.55) { tip.style.left = 'auto'; tip.style.right = `${rect.width - left + 14}px`; } else { tip.style.right = 'auto'; tip.style.left = `${left + 14}px`; }
  }

  panel.addEventListener('click', (ev) => {
    const b = ev.target.closest('.seg button'); if (!b) return;
    const seg = b.closest('.seg'); for (const o of seg.querySelectorAll('button')) o.setAttribute('aria-pressed', o === b);
    state[seg.dataset.opt] = b.dataset.v; render();
  });
  panel.querySelector('.fit-toggle').addEventListener('change', (ev) => { state.fit = ev.target.checked; render(); });
  chartwrap.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return; ev.preventDefault();
    const c = cur.charts[0]; const i = state.hover == null ? n - 1 : state.hover + (ev.key === 'ArrowRight' ? 1 : -1); showAt(Math.min(c.idx1 - 1, Math.max(c.idx0, i)), c);
  });
  chartwrap.addEventListener('blur', hideHover);
  const csv = panel.querySelector('button.csv');
  if (csv) csv.addEventListener('click', () => {
    const blob = new Blob([buildCsv(data)], {type: 'text/csv'});
    const link = doc.createElement('a'); link.href = URL.createObjectURL(blob); link.download = csv.dataset.name || 'production.csv';
    doc.body.appendChild(link); link.click(); setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
  });
  render();
}

if (typeof document !== 'undefined') {
  mount(document);
  window.bakkenProductionCsv = (raw) => buildCsv(decode(raw));
}
