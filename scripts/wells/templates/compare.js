// Compare a well with the other wells of its spacing unit, or look at all the wells of a unit together.
// Nothing is added to the pages for this: the module reads the unit links a well page already has, fetches the
// unit page (which lists the surveyed wells inside with their feet of lateral in the footprint and the share), then
// fetches each well page and reads the production data it already embeds (#production-data), its name, operator
// and spud date. Lateral length = feet inside / share, from the survey. Everything is computed here, on request.
// Membership is the geometric rule of the index (660 ft of surveyed lateral inside the published footprint);
// overlapping units are not additive; the wells differ in vintage, lateral and completion; the volumes are as
// first reported. The block says all that before it says anything else.
import {decode, derive, compact, fmt, fixed, thousands, niceTicks, tickLabel, monthLabel, addMonths} from './production.js';

export const GEO = {W: 820, LEFT: 58, RIGHT: 20, TOP: 16, PLOT: 240, AXIS: 26};
const MEASURES = {
  rate: {label: 'Oil rate', unit: 'bbl/d', per: 'bbl/d per 1,000 ft', key: 'rateOil', norm: true},
  cum: {label: 'Cumulative oil', unit: 'bbl', per: 'bbl per 1,000 ft', key: 'cumOil', norm: true},
  gor: {label: 'Gas–oil ratio', unit: 'scf/bbl, trailing 3 months', key: 'gor3', norm: false},
  wcut: {label: 'Water cut', unit: '% of liquids, trailing 3 months', key: 'wcut3', norm: false},
};
const CONCURRENCY = 6;
const MAX_WELLS = 80;
const cache = new Map();

// ---------------------------------------------------------------- reading pages
const slugOf = (href) => { const m = String(href || '').match(/\/(wells|units)\/([^/?#]+)\/?/); return m ? m[2] : null; };
const text = (el) => (el ? el.textContent : '').replace(/\s+/g, ' ').trim();
const fact = (doc, label) => { for (const dt of doc.querySelectorAll('dl.facts dt')) if (text(dt) === label) return dt.nextElementSibling; return null; };
const number = (s) => { const m = String(s || '').replace(/,/g, '').match(/-?\d+(\.\d+)?/); return m ? Number(m[0]) : null; };

export function parseWellPage(doc, href) {
  const spudTime = fact(doc, 'Spud date');
  const spud = spudTime ? (spudTime.querySelector('time') || {}).getAttribute?.('datetime') || null : null;
  const raw = doc.getElementById('production-data');
  let data = null;
  if (raw) { try { data = decode(JSON.parse(raw.textContent)); } catch { data = null; } }
  const units = [];
  for (const li of doc.querySelectorAll('ul.nearby li[data-acres]')) {
    const a = li.querySelector('a'); units.push({slug: slugOf(a && a.getAttribute('href')), href: a && a.getAttribute('href'), where: text(a), acres: number(li.dataset.acres)});
  }
  return {slug: slugOf(href), href, name: text(doc.querySelector('h1')) || slugOf(href), operator: text(fact(doc, 'Operator')) || null,
          status: text(fact(doc, 'Status')) || null, spud, spudYear: spud ? Number(spud.slice(0, 4)) : null, data, units};
}

export function parseUnitPage(doc, href) {
  const members = [];
  for (const row of doc.querySelectorAll('table.units tbody tr')) {
    const a = row.querySelector('td a[href*="/wells/"]'); if (!a) continue;
    const cells = row.querySelectorAll('td'); const lateral = cells[cells.length - 1];
    const inside = number(text(lateral).split(' ft')[0]); const share = number(text(lateral.querySelector('small')));
    members.push({slug: slugOf(a.getAttribute('href')), href: a.getAttribute('href'), name: text(a), operator: text(cells[1]) || null, status: text(cells[2]) || null,
                  inside, share, lateral: inside != null && share >= 5 ? Math.round(inside / (share / 100)) : null});
  }
  const overlaps = [];
  for (const h2 of doc.querySelectorAll('section.panel h2')) if (text(h2) === 'Overlapping units') {
    for (const a of h2.parentElement.querySelectorAll('ul.plain li a[href*="/units/"]')) overlaps.push({slug: slugOf(a.getAttribute('href')), href: a.getAttribute('href'), where: text(a), title: text(a.nextElementSibling)});
  }
  return {slug: slugOf(href), href, title: text(doc.querySelector('h1')), where: text(doc.querySelector('p.lede')), acres: number(text(fact(doc, 'Acres'))), members, overlaps};
}

async function fetchDoc(href) {
  if (!cache.has(href)) cache.set(href, (async () => {
    const response = await fetch(href, {credentials: 'same-origin'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return new DOMParser().parseFromString(await response.text(), 'text/html');
  })());
  return cache.get(href);
}
export const loadUnit = async (href) => parseUnitPage(await fetchDoc(href), href);
export const loadWell = async (href) => parseWellPage(await fetchDoc(href), href);

async function loadWells(members, onProgress) {
  const out = new Array(members.length); let next = 0, done = 0, failed = 0;
  const worker = async () => {
    while (next < members.length) {
      const i = next++;
      try { out[i] = {...members[i], ...await loadWell(members[i].href), inside: members[i].inside, share: members[i].share, lateral: members[i].lateral, units: members[i].units}; }
      catch { out[i] = {...members[i], data: null, failed: true}; failed++; }
      done++; if (onProgress) onProgress(done, members.length);
    }
  };
  await Promise.all(Array.from({length: Math.min(CONCURRENCY, members.length)}, worker));
  return {wells: out, failed};
}

// ---------------------------------------------------------------- figures per well
export function wellStats(well) {
  const s = {name: well.name, slug: well.slug, href: well.href, operator: well.operator, spudYear: well.spudYear, lateral: well.lateral, months: null, first: null,
             peak: null, first12: null, first12Known: 0, cum: null, cumPer: null, first12Per: null, last12: null, latest: null, gor12: null, wcut12: null, series: null};
  if (!well.data) return s;
  const d = derive(well.data), n = d.n, v = well.data;
  s.months = n; s.first = v.start;
  const known = (a) => a.filter((x) => x != null);
  const cumKnown = known(v.oil); s.cum = cumKnown.length ? cumKnown.reduce((a, b) => a + b, 0) : null;
  const rates = known(d.rate.oil); s.peak = rates.length ? Math.max(...rates) : null;
  const f12 = known(v.oil.slice(0, 12)); s.first12 = f12.length ? f12.reduce((a, b) => a + b, 0) : null; s.first12Known = f12.length;
  const l12 = known(v.oil.slice(Math.max(0, n - 12))); s.last12 = l12.length ? l12.reduce((a, b) => a + b, 0) : null;
  s.latest = [...d.rate.oil].reverse().find((x) => x != null) ?? null;
  const ratio = (num, den, from, scale) => { let a = 0, b = 0, seen = false; for (let i = from; i < n; i++) if (num[i] != null && den[i] != null) { a += num[i]; b += den[i]; seen = true; } return seen && b > 0 ? a * scale / b : null; };
  const gas = v.gas || [], water = v.water || [];
  s.gor12 = ratio(gas, v.oil, Math.max(0, n - 12), 1000);
  const liquids = v.oil.map((o, i) => o == null || water[i] == null ? null : o + water[i]);
  s.wcut12 = ratio(water, liquids, Math.max(0, n - 12), 100);
  if (s.lateral) { s.cumPer = s.cum == null ? null : s.cum * 1000 / s.lateral; s.first12Per = s.first12 == null ? null : s.first12 * 1000 / s.lateral; }
  let seen = false; const cumOil = d.cum.oil.map((c, i) => { if (v.oil[i] != null) seen = true; return seen ? c : null; });   // nothing known yet: no cumulative, not zero
  s.series = {rateOil: d.rate.oil, cumOil, gor3: d.gor3, wcut3: d.wcut3};
  return s;
}

export const median = (values) => { const a = values.filter((x) => x != null).sort((x, y) => x - y); if (a.length < 3) return null; const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };

// ---------------------------------------------------------------- the chart
// series: [{key, label, values (by x index), me, href}]; x axis: months on production (0..) or calendar months from `start`.
export function linesSvg(series, opts) {
  const {W, LEFT, RIGHT, TOP, PLOT, AXIS} = GEO; const plotW = W - LEFT - RIGHT;
  const n = opts.n, log = !!opts.log;
  const x = (i) => n > 1 ? LEFT + i * plotW / (n - 1) : LEFT + plotW / 2;
  const all = []; for (const s of series) for (const v of s.values) if (v != null && (!log || v > 0)) all.push(v);
  const vmax = all.length ? Math.max(...all) : 0;
  let ticks, y;
  if (log) {
    const min = Math.min(...all.filter((v) => v > 0)); let hi = Math.ceil(Math.log10(vmax || 10)), lo = Math.floor(Math.log10(Math.max(min || 1, (vmax || 10) / 1e4)));
    if (hi - lo > 4) lo = hi - 4; if (hi <= lo) hi = lo + 1; ticks = []; for (let e = lo; e <= hi; e++) ticks.push(10 ** e);
    y = (v) => TOP + PLOT - (Math.log10(Math.max(v, ticks[0])) - Math.log10(ticks[0])) / (Math.log10(ticks[ticks.length - 1]) - Math.log10(ticks[0])) * PLOT;
  } else {
    ticks = niceTicks(opts.pct ? 100 : vmax); const tmax = ticks[ticks.length - 1];
    y = (v) => TOP + PLOT - v / tmax * PLOT;
  }
  let out = `<svg class="prodchart cmp" viewBox="0 0 ${W} ${TOP + PLOT + AXIS}" role="img" aria-label="${opts.ariaLabel || ''}"><g class="grid">`;
  for (const t of ticks) out += `<line x1="${LEFT}" x2="${W - RIGHT}" y1="${fmt(y(t))}" y2="${fmt(y(t))}"/><text x="${LEFT - 8}" y="${fmt(y(t) + 4)}" text-anchor="end">${log ? compact(t) : tickLabel(t, ticks)}</text>`;
  out += '</g>';
  const path = (values) => { const d = []; let pen = false; for (let i = 0; i < n; i++) { const v = values[i]; if (v == null || (log && v <= 0)) { pen = false; continue; } d.push(`${pen ? 'L' : 'M'}${fmt(x(i))},${fmt(y(v))}`); pen = true; } return d.join(' '); };
  const others = series.filter((s) => !s.me && !s.median), me = series.filter((s) => s.me), med = series.filter((s) => s.median);
  for (const s of [...others, ...med, ...me]) { const d = path(s.values); if (d) out += `<path class="w${s.me ? ' me' : ''}${s.median ? ' med' : ''}" data-key="${s.key}" d="${d}"/>`; }
  out += `<g class="axis"><line x1="${LEFT}" x2="${W - RIGHT}" y1="${fmt(TOP + PLOT + 0.5)}" y2="${fmt(TOP + PLOT + 0.5)}"/>`;
  const pxPerMonth = plotW / Math.max(1, n - 1); const want = Math.ceil(64 / pxPerMonth); const every = [1, 2, 3, 6, 12, 24, 48, 60, 120, 240].find((e) => e >= want) || 240;
  for (let i = 0; i < n; i++) {
    if (opts.start) { const key = addMonths(opts.start, i); const year = Number(key.slice(0, 4)), month = Number(key.slice(5, 7));
      if (every >= 12 ? (month === 1 && year % (every / 12) === 0) : (i % every === 0)) out += `<text x="${fmt(x(i))}" y="${TOP + PLOT + 18}" text-anchor="middle">${every >= 12 ? year : monthLabel(key, true)}</text>`; }
    else if (i % Math.max(every, 6) === 0) out += `<text x="${fmt(x(i))}" y="${TOP + PLOT + 18}" text-anchor="middle">${i}</text>`;
  }
  out += `</g><line class="cross" x1="0" x2="0" y1="${TOP}" y2="${TOP + PLOT}"/><circle class="pt" r="4" cx="0" cy="0"/><rect class="hit" x="${LEFT}" y="${TOP}" width="${plotW}" height="${PLOT}"/></svg>`;
  return {svg: out, x, y, n};
}

// ---------------------------------------------------------------- the block
const CONTROLS = (unitOptions, overlapCount) => `<div class="controls" role="group" aria-label="Comparison options">
${unitOptions ? `<div class="switch"><span>Unit</span><select class="unit-pick">${unitOptions}</select></div>` : ''}
<div class="switch"><span>Show</span><div class="seg" data-opt="measure"><button type="button" data-v="rate" aria-pressed="true">Oil rate</button><button type="button" data-v="cum" aria-pressed="false">Cumulative oil</button><button type="button" data-v="gor" aria-pressed="false">Gas–oil ratio</button><button type="button" data-v="wcut" aria-pressed="false">Water cut</button></div></div>
<div class="switch"><span>Per</span><div class="seg" data-opt="norm"><button type="button" data-v="well" aria-pressed="true">Well</button><button type="button" data-v="kft" aria-pressed="false">1,000 ft of lateral</button></div></div>
<div class="switch"><span>Time</span><div class="seg" data-opt="time"><button type="button" data-v="age" aria-pressed="true">Months on production</button><button type="button" data-v="cal" aria-pressed="false">Calendar</button></div></div>
<div class="switch"><span>Scale</span><div class="seg" data-opt="scale"><button type="button" data-v="lin" aria-pressed="true">Linear</button><button type="button" data-v="log" aria-pressed="false">Log</button></div></div>
<label class="chk"><input type="checkbox" class="overlap-toggle"> Include overlapping units${overlapCount ? ` (${overlapCount})` : ''}</label></div>`;

const PREFACE = 'A neighbourhood comparison, not a type curve. A well belongs to a unit here when at least 660 ft of its surveyed lateral lies inside the published footprint, so this is geometry, not a legal assignment, and a lateral can sit in several units; overlapping units are not additive. The wells differ in vintage, lateral length, completion design and target bench, none of which the public record states fully. Volumes are as first reported to NDIC; a month a report leaves blank is unknown, not zero. Lateral length is the surveyed plan-view length, taken as the feet inside the unit divided by the share inside.';

export function mount(doc) {
  const path = (doc.location && doc.location.pathname) || '';
  const isUnit = /\/units\/[^/]+\/?$/.test(path), isWell = /\/wells\/[^/]+\/?$/.test(path);
  if (!isUnit && !isWell) return;
  const section = doc.createElement('section'); section.className = 'panel compare';
  let anchor = null, mySlug = null, unitLinks = [];
  if (isWell) {
    mySlug = slugOf(path);
    const items = [...doc.querySelectorAll('ul.nearby li[data-acres]')];
    if (!items.length) return;
    unitLinks = items.map((li) => { const a = li.querySelector('a'); return {slug: slugOf(a.getAttribute('href')), href: a.getAttribute('href'), where: text(a), acres: number(li.dataset.acres)}; })
      .sort((a, b) => (a.acres || 1e9) - (b.acres || 1e9));
    anchor = doc.querySelector('section.production') || items[0].closest('section');
    section.innerHTML = `<h2>Compare with the wells in this spacing unit</h2><p class="sub">The other surveyed wells whose laterals share a unit with this one, drawn from their own pages when you ask.</p>
<p class="actions"><button type="button" class="btn load">Compare with the wells in ${unitLinks[0].where}<small>${unitLinks.length > 1 ? `This well sits in ${unitLinks.length} units; pick another after loading` : 'Loads each well’s page and reads its production'}</small></button></p>`;
  } else {
    const rows = [...doc.querySelectorAll('table.units tbody tr td a[href*="/wells/"]')];
    if (!rows.length) return;
    unitLinks = [{slug: slugOf(path), href: path, where: text(doc.querySelector('p.lede')), acres: number(text(fact(doc, 'Acres')))}];
    anchor = rows[0].closest('section');
    section.innerHTML = `<h2>Production of the wells in this unit</h2><p class="sub">Oil, gas and water of the ${rows.length} surveyed wells inside, read from their own pages when you ask.</p>
<p class="actions"><button type="button" class="btn load">Load the production of these ${rows.length} wells<small>One page per well; nothing is loaded until you click</small></button></p>`;
  }
  anchor.insertAdjacentElement('afterend', section);
  const state = {measure: 'rate', norm: 'well', time: 'age', scale: 'lin', overlaps: false, unit: unitLinks[0].slug, sort: 'first12', dir: -1, hover: null};
  let units = new Map(), wells = [], chart = null;

  const status = doc.createElement('p'); status.className = 'status';
  const body = doc.createElement('div'); body.className = 'cmpbody';

  async function load() {
    section.querySelector('.actions').remove(); section.append(status, body);
    status.textContent = 'Loading the unit…';
    try {
      for (const u of unitLinks) if (!units.has(u.slug)) units.set(u.slug, await loadUnit(u.href));
      await loadMembers();
    } catch (err) { status.textContent = `Could not load the unit page (${err.message}).`; return; }
    render();
  }
  function memberSet() {
    const main = units.get(state.unit); if (!main) return [];
    const picked = [main]; if (state.overlaps) for (const o of main.overlaps) if (units.has(o.slug)) picked.push(units.get(o.slug));
    const seen = new Map();
    for (const u of picked) for (const m of u.members) {
      if (!seen.has(m.slug)) seen.set(m.slug, {...m, units: []});
      seen.get(m.slug).units.push(u.where);
    }
    return [...seen.values()].slice(0, MAX_WELLS);
  }
  async function loadMembers() {
    const members = memberSet();
    const missing = members.filter((m) => !wells.some((w) => w.slug === m.slug));
    if (missing.length) {
      const {wells: loaded, failed} = await loadWells(missing, (done, total) => { status.textContent = `Loading ${total} well page${total === 1 ? '' : 's'}… ${done} of ${total}`; });
      wells = wells.concat(loaded);
      if (failed) status.dataset.failed = String(Number(status.dataset.failed || 0) + failed);
    }
    for (const m of members) { const w = wells.find((x) => x.slug === m.slug); if (w) { w.units = m.units; w.inside = m.inside; w.share = m.share; w.lateral = m.lateral; } }
  }
  async function loadOverlaps() {
    const main = units.get(state.unit);
    for (const o of main.overlaps) if (!units.has(o.slug)) { status.textContent = `Loading overlapping unit ${o.where}…`; try { units.set(o.slug, await loadUnit(o.href)); } catch { /* skip a unit that fails */ } }
  }

  function render() {
    const main = units.get(state.unit); const members = memberSet();
    const list = members.map((m) => wells.find((w) => w.slug === m.slug)).filter(Boolean);
    const stats = list.map(wellStats);
    const withProd = stats.filter((s) => s.series);
    const failed = Number(status.dataset.failed || 0);
    status.textContent = `${list.length} wells in ${state.overlaps ? `${main.where} and its overlapping units` : main.where}, ${withProd.length} with production in the reports` + (failed ? `; ${failed} page${failed === 1 ? '' : 's'} could not be loaded` : '') + (members.length >= MAX_WELLS ? `; the first ${MAX_WELLS} shown` : '') + '.';
    if (!body.querySelector('.controls')) {
      const options = unitLinks.length > 1 ? unitLinks.map((u) => `<option value="${u.slug}"${u.slug === state.unit ? ' selected' : ''}>${u.where}${u.acres ? ` · ${thousands(u.acres)} acres` : ''}</option>`).join('') : '';
      body.innerHTML = `${CONTROLS(options, main.overlaps.length)}<ul class="legend"></ul><div class="chartwrap" tabindex="0"></div><div class="scroll"></div><p class="note">${PREFACE}</p>`;
      body.querySelector('.chartwrap').setAttribute('aria-label', 'Comparison chart. Hover or use the arrow keys to read month by month.');
      body.addEventListener('click', onClick); body.querySelector('.overlap-toggle').addEventListener('change', onOverlap);
      const pick = body.querySelector('.unit-pick'); if (pick) pick.addEventListener('change', async () => { state.unit = pick.value; status.textContent = 'Loading…'; if (state.overlaps) await loadOverlaps(); await loadMembers(); render(); });
    }
    body.querySelector('.overlap-toggle').nextSibling.textContent = ` Include overlapping units${main.overlaps.length ? ` (${main.overlaps.length})` : ''}`;
    body.querySelector('.overlap-toggle').disabled = !main.overlaps.length;
    for (const b of body.querySelectorAll('[data-opt=norm] button')) b.disabled = !MEASURES[state.measure].norm;
    renderChart(stats); renderTable(stats);
  }
  function seriesFor(stats) {
    const M = MEASURES[state.measure]; const perKft = state.norm === 'kft' && M.norm;
    const usable = stats.filter((s) => s.series && (!perKft || s.lateral));
    let start = null, n = 0;
    if (state.time === 'cal') { const firsts = usable.map((s) => s.first).sort(); start = firsts[0] || null; for (const s of usable) { const off = monthsBetween(start, s.first); n = Math.max(n, off + s.months); } }
    else n = Math.max(0, ...usable.map((s) => s.months));
    const series = usable.map((s) => {
      const raw = s.series[M.key]; const values = Array(n).fill(null); const off = state.time === 'cal' ? monthsBetween(start, s.first) : 0;
      for (let i = 0; i < raw.length; i++) values[off + i] = raw[i] == null ? null : (perKft ? raw[i] * 1000 / s.lateral : raw[i]);
      return {key: s.slug, label: s.name, href: s.href, values, me: s.slug === mySlug, stat: s};
    });
    if (state.time === 'age' && series.length >= 3) {
      // the median is drawn only while at least half of the wells (and at least three) still report, so it does not
      // jump when a young cohort ends and only the old wells remain
      const need = Math.max(3, Math.ceil(series.length / 2)); const med = Array(n).fill(null);
      for (let i = 0; i < n; i++) { const col = series.map((s) => s.values[i]); med[i] = col.filter((v) => v != null).length >= need ? median(col) : null; }
      series.push({key: 'median', label: `Median of ${series.length} wells`, values: med, median: true});
    }
    return {series, start, n, perKft, M, left: stats.length - usable.length};
  }
  const monthsBetween = (a, b) => (Number(b.slice(0, 4)) - Number(a.slice(0, 4))) * 12 + Number(b.slice(5, 7)) - Number(a.slice(5, 7));
  function renderChart(stats) {
    const {series, start, n, perKft, M, left} = seriesFor(stats); const wrap = body.querySelector('.chartwrap'); const legend = body.querySelector('.legend');
    legend.textContent = '';
    const li = (cls, label) => { const el = doc.createElement('li'); el.className = cls; el.innerHTML = '<i></i>'; el.append(label); legend.append(el); };
    if (mySlug && series.some((s) => s.me)) li('me', 'This well');
    li('other', `Other wells in the unit${state.overlaps ? ' and its overlapping units' : ''} (${series.filter((s) => !s.me && !s.median).length})`);
    if (series.some((s) => s.median)) li('median', 'Median while at least half of the wells report');
    if (left) { const el = doc.createElement('li'); el.className = 'left'; el.append(`${left} well${left === 1 ? '' : 's'} not drawn: no production${perKft ? ' or no surveyed lateral length' : ''}`); legend.append(el); }
    if (!n) { wrap.innerHTML = '<p class="note">Nothing to draw: no well in this unit has production in the reports.</p>'; chart = null; return; }
    const unit = perKft ? M.per : M.unit;
    chart = linesSvg(series, {n, start, log: state.scale === 'log', pct: state.measure === 'wcut', ariaLabel: `${M.label}, ${unit}, ${series.length} wells`});
    wrap.innerHTML = `<p class="t"><b>${M.label}</b> ${unit} · ${state.time === 'cal' ? 'by calendar month' : 'by months on production, each well from its first reported month'}</p>${chart.svg}<div class="tip"></div>`;
    chart.series = series; chart.unit = unit;
    const hit = wrap.querySelector('.hit'); hit.addEventListener('pointermove', onHover); hit.addEventListener('pointerleave', hideHover);
  }
  function onHover(ev) {
    if (!chart) return; const svg = ev.currentTarget.ownerSVGElement; const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY; const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    const i = Math.min(chart.n - 1, Math.max(0, Math.round((p.x - GEO.LEFT) / (GEO.W - GEO.LEFT - GEO.RIGHT) * (chart.n - 1))));
    let best = null, dist = Infinity;
    for (const s of chart.series) { const v = s.values[i]; if (v == null || (state.scale === 'log' && v <= 0)) continue; const d = Math.abs(chart.y(v) - p.y); if (d < dist) { dist = d; best = s; } }
    showAt(i, best);
  }
  function showAt(i, s) {
    const wrap = body.querySelector('.chartwrap'); const svg = wrap.querySelector('svg'); const tip = wrap.querySelector('.tip');
    for (const el of svg.querySelectorAll('path.w')) el.classList.toggle('hl', !!s && el.dataset.key === s.key);
    const px = chart.x(i); const cross = svg.querySelector('.cross'); cross.setAttribute('x1', px); cross.setAttribute('x2', px); cross.style.opacity = 1;
    const dot = svg.querySelector('.pt');
    if (!s) { dot.style.opacity = 0; tip.style.display = 'none'; return; }
    dot.setAttribute('cx', px); dot.setAttribute('cy', chart.y(s.values[i])); dot.style.opacity = 1;
    tip.textContent = ''; const b = doc.createElement('b'); b.textContent = s.label; tip.append(b);
    const row = (val, lab) => { const div = doc.createElement('div'); const a = doc.createElement('span'); a.textContent = val; const l = doc.createElement('span'); l.textContent = lab; div.append(a, l); tip.append(div); };
    const v = s.values[i]; row(state.measure === 'wcut' ? fixed(v, 0) + '%' : thousands(fmt(v)), chart.unit);
    row(state.time === 'cal' ? monthLabel(addMonths(seriesStart(), i)) : `month ${i + 1}`, state.time === 'cal' ? 'month' : 'on production');
    if (s.stat) { if (s.stat.spudYear) row(String(s.stat.spudYear), 'spud'); if (s.stat.lateral) row(thousands(s.stat.lateral) + ' ft', 'lateral'); if (s.stat.operator) row(s.stat.operator, ''); }
    const rect = wrap.getBoundingClientRect(); const frac = (px - GEO.LEFT) / (GEO.W - GEO.LEFT - GEO.RIGHT); const left = rect.width * px / GEO.W;
    tip.style.display = 'block'; tip.style.top = '28px';
    if (frac > 0.55) { tip.style.left = 'auto'; tip.style.right = `${rect.width - left + 14}px`; } else { tip.style.right = 'auto'; tip.style.left = `${left + 14}px`; }
    state.hover = {i, key: s.key};
  }
  const seriesStart = () => { const firsts = chart.series.filter((s) => s.stat).map((s) => s.stat.first).sort(); return firsts[0]; };
  function hideHover() { const wrap = body.querySelector('.chartwrap'); for (const el of wrap.querySelectorAll('.cross,.pt')) el.style.opacity = 0; for (const el of wrap.querySelectorAll('path.w.hl')) el.classList.remove('hl'); const tip = wrap.querySelector('.tip'); if (tip) tip.style.display = 'none'; state.hover = null; }

  const COLUMNS = [['name', 'Well'], ['spudYear', 'Spud'], ['lateral', 'Lateral ft'], ['first', 'First month'], ['months', 'Months'], ['peak', 'Peak bbl/d'], ['first12', 'First 12 mo bbl'], ['first12Per', 'per 1,000 ft'],
                   ['cum', 'Cum oil bbl'], ['cumPer', 'per 1,000 ft'], ['last12', 'Last 12 mo bbl'], ['latest', 'Latest bbl/d'], ['gor12', 'GOR 12 mo'], ['wcut12', 'Water cut 12 mo']];
  function renderTable(stats) {
    const rows = [...stats].sort((a, b) => { const x = a[state.sort], y = b[state.sort]; if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return (x < y ? -1 : x > y ? 1 : 0) * state.dir; });
    const cell = (s, k) => { const v = s[k]; if (v == null) return '—'; if (k === 'name') return `<a href="${s.href}">${esc(v)}</a>`; if (k === 'first') return monthLabel(v); if (k === 'spudYear') return String(v); if (k === 'wcut12') return fixed(v, 0) + '%'; if (k === 'first12' && s.first12Known && s.first12Known < 12) return `${thousands(fmt(v))}<small>${s.first12Known} mo</small>`; return typeof v === 'number' ? thousands(fmt(v)) : esc(v); };
    const head = COLUMNS.map(([k, label]) => `<th${k === 'name' ? '' : ' class="num"'}><button type="button" data-sort="${k}" aria-sort="${state.sort === k ? (state.dir > 0 ? 'ascending' : 'descending') : 'none'}">${label}${state.sort === k ? (state.dir > 0 ? ' ▲' : ' ▼') : ''}</button></th>`).join('');
    const tbody = rows.map((s) => `<tr${s.slug === mySlug ? ' class="me"' : ''}>${COLUMNS.map(([k]) => `<td${k === 'name' ? '' : ' class="num"'}>${cell(s, k)}</td>`).join('')}</tr>`).join('');
    body.querySelector('.scroll').innerHTML = `<table class="units cmp"><thead><tr>${head}</tr></thead><tbody>${tbody}</tbody></table><p class="note">Click a heading to sort. First 12 months and cumulative count known months only; a well younger than 12 months says how many. Per 1,000 ft uses the surveyed lateral length.</p>`;
  }
  function onClick(ev) {
    const sort = ev.target.closest('button[data-sort]');
    if (sort) { const k = sort.dataset.sort; if (state.sort === k) state.dir = -state.dir; else { state.sort = k; state.dir = k === 'name' || k === 'first' ? 1 : -1; } render(); return; }
    const b = ev.target.closest('.seg button'); if (!b || b.disabled) return;
    const seg = b.closest('.seg'); for (const o of seg.querySelectorAll('button')) o.setAttribute('aria-pressed', o === b);
    state[seg.dataset.opt] = b.dataset.v; render();
  }
  async function onOverlap(ev) { state.overlaps = ev.target.checked; if (state.overlaps) { await loadOverlaps(); } await loadMembers(); render(); }
  section.querySelector('button.load').addEventListener('click', load);
  section.addEventListener('keydown', (ev) => {
    if (!chart || (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight')) return; ev.preventDefault();
    const i = state.hover == null ? chart.n - 1 : Math.min(chart.n - 1, Math.max(0, state.hover.i + (ev.key === 'ArrowRight' ? 1 : -1)));
    const key = state.hover ? state.hover.key : (chart.series.find((s) => s.me) || chart.series[0]).key;
    let s = chart.series.find((x) => x.key === key); if (!s || s.values[i] == null) s = chart.series.find((x) => x.values[i] != null) || null;
    showAt(i, s);
  });
}
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

if (typeof document !== 'undefined') mount(document);
