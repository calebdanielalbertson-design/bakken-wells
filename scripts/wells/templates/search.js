// Fuzzy well search for the static well pages.
// Pure functions first (covered by site/test_well_search.mjs in Node); DOM glue at the end.
// Well names, operators and counties are data: they are rendered with textContent only.

export const normalize = value => String(value ?? '')
  .normalize('NFKD').replace(/[̀-ͯ]/g, '')
  .toUpperCase().replace(/[^A-Z0-9]+/g, ' ').trim();

export const digits = value => String(value ?? '').replace(/\D/g, '');

export function bigrams(text) {
  const out = new Set(), padded = ` ${text} `;
  for (let i = 0; i < padded.length - 1; i++) out.add(padded.slice(i, i + 2));
  return out;
}

export function dice(a, b) {
  if (!a.size || !b.size) return 0;
  let shared = 0;
  for (const gram of a) if (b.has(gram)) shared++;
  return (2 * shared) / (a.size + b.size);
}

export function formatApi(api) {
  const d = digits(api);
  if (d.length < 10) return d;
  return `${d.slice(0, 2)}-${d.slice(2, 5)}-${d.slice(5, 10)}${d.length > 10 ? '-' + d.slice(10) : ''}`;
}

export const mapLinks = (lat, lon, name = '') => ({
  navigate: `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`,
  view: `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`,
  apple: `https://maps.apple.com/?daddr=${lat},${lon}&q=${encodeURIComponent(name || 'Well')}`,
});

// Column order of index.wells rows, written by scripts/wells/build_well_pages.py. status and
// type are indexes into index.statuses / index.types; slug is null when it equals formatApi(api).
export const FIELDS = ['name', 'api', 'file', 'op', 'county', 'status', 'type', 'lat', 'lon', 'slug'];

export function createSearch(index) {
  const operators = index.operators || [], counties = index.counties || [];
  const statuses = index.statuses || [], types = index.types || [];
  const records = (index.wells || []).map(row => {
    const r = Object.fromEntries(FIELDS.map((key, i) => [key, row[i]]));
    r.operator = operators[r.op] ?? '';
    r.countyName = counties[r.county] ?? '';
    r.status = typeof r.status === 'number' ? (statuses[r.status] ?? '') : (r.status ?? '');
    r.type = typeof r.type === 'number' ? (types[r.type] ?? '') : (r.type ?? '');
    r.slug = r.slug || formatApi(r.api) || `file-${r.file}`;
    r.nameN = normalize(r.name);
    r.tokens = r.nameN ? r.nameN.split(' ') : [];
    r.apiD = digits(r.api);
    r.fileD = digits(r.file);
    r.opN = normalize(r.operator);
    r.countyN = normalize(r.countyName);
    r.grams = null; // built on first fuzzy comparison
    return r;
  });
  const grams = r => r.grams || (r.grams = bigrams(r.nameN));

  function score(r, query) {
    const {qn, tokens, qd, idQuery} = query;
    let s = 0, idHit = false;
    if (idQuery) {
      if (r.fileD && r.fileD === qd) { s += 100; idHit = true; }
      else if (r.fileD && qd.length >= 4 && r.fileD.startsWith(qd)) { s += 30; idHit = true; }
      if (r.apiD && r.apiD.startsWith(qd)) { s += 100; idHit = true; }
      else if (qd.length >= 6 && r.apiD && r.apiD.includes(qd)) { s += 25; idHit = true; }
      if (!tokens.length) return s;
    }
    if (!tokens.length) return s;
    let tokenScore = 0, matchedAll = true;
    for (const qt of tokens) {
      let best = 0;
      const numeric = /^\d+$/.test(qt);
      for (const rt of r.tokens) {
        if (rt === qt) { best = 10; break; }
        if (rt.startsWith(qt)) best = Math.max(best, numeric ? 2 : 7);
      }
      if (best === 0 && qt.length >= 4) {
        const d = dice(bigrams(qt), grams(r));
        if (d >= 0.5) best = d * 6;
      }
      if (best === 0 && qt.length >= 3 && (r.opN.includes(qt) || r.countyN.startsWith(qt))) best = 3;
      if (best === 0) { matchedAll = false; break; }
      tokenScore += best;
    }
    if (matchedAll) return s + tokenScore;
    if (idHit) return s;
    if (qn.length >= 5) {
      const d = dice(bigrams(qn), grams(r));
      if (d >= 0.55) return d * 12;
    }
    return 0;
  }

  function parse(text) {
    const qn = normalize(text), qd = digits(text);
    const compact = qn.replace(/ /g, '');
    const idQuery = qd.length >= 3 && qd.length >= compact.length * 0.6;
    const tokens = !qn ? [] : idQuery ? qn.split(' ').filter(t => digits(t).length < t.length && t.length >= 2) : qn.split(' ');
    return {qn, qd, idQuery, tokens};
  }

  function search(text, limit = 50) {
    const query = parse(text);
    if (!query.qn) return {rows: [], total: 0};
    const hits = [];
    for (const r of records) {
      const s = score(r, query);
      if (s > 0) hits.push([s, r]);
    }
    hits.sort((a, b) => b[0] - a[0] || a[1].tokens.length - b[1].tokens.length || (a[1].nameN < b[1].nameN ? -1 : a[1].nameN > b[1].nameN ? 1 : 0));
    return {rows: hits.slice(0, limit).map(([s, r]) => ({score: s, well: r})), total: hits.length};
  }

  return {search, records, size: records.length};
}

// ---------------------------------------------------------------- DOM glue
const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'text') node.textContent = value;
    else if (key === 'class') node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
};

export function mountSearch({input, results, status, base = '', index, limit = 50}) {
  const engine = createSearch(index);
  const statusOf = index.statusGloss || {}, typeOf = index.typeGloss || {};
  const fmt = new Intl.NumberFormat('en-US');
  let timer = null, lastQuery = null;

  function render(text) {
    lastQuery = text;
    const trimmed = text.trim();
    results.replaceChildren();
    if (!trimmed) {
      status.textContent = `${fmt.format(engine.size)} wells indexed. Type a well name, API number or NDIC file number.`;
      return;
    }
    const {rows, total} = engine.search(trimmed, limit);
    status.textContent = total
      ? `${fmt.format(total)} match${total === 1 ? '' : 'es'}${total > rows.length ? ` · showing the top ${rows.length}` : ''}`
      : 'No matching wells. Try fewer words, part of the name, or the API or file number.';
    for (const {well} of rows) {
      const href = `${base}/wells/${well.slug}/`;
      const bits = [`API ${formatApi(well.api) || '—'}`, `File ${well.file || '—'}`, well.operator,
        well.countyName ? `${well.countyName} County` : '',
        statusOf[well.status] ? `${statusOf[well.status]} (${well.status})` : well.status,
        typeOf[well.type] || well.type].filter(Boolean);
      const actions = [el('a', {href, text: 'Details'})];
      if (Number.isFinite(well.lat) && Number.isFinite(well.lon)) {
        const links = mapLinks(well.lat, well.lon, well.name);
        actions.push(el('a', {href: links.navigate, rel: 'noopener', target: '_blank', text: 'Navigate'}),
                     el('a', {href: links.view, rel: 'noopener', target: '_blank', text: 'Map'}));
      }
      results.append(el('li', {}, [
        el('a', {class: 'r-title', href, text: well.name || `Well file ${well.file}`}),
        el('div', {class: 'r-meta', text: bits.join(' · ')}),
        el('div', {class: 'r-actions'}, actions),
      ]));
    }
  }

  function sync() {
    const text = input.value;
    if (text === lastQuery) return;
    render(text);
    const url = new URL(location.href);
    if (text.trim()) url.searchParams.set('q', text.trim()); else url.searchParams.delete('q');
    history.replaceState(null, '', url);
  }

  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(sync, 60); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      const first = results.querySelector('a.r-title');
      if (first) { event.preventDefault(); location.href = first.href; }
    }
  });
  const initial = new URLSearchParams(location.search).get('q') || input.value;
  input.value = initial;
  render(initial);
  input.disabled = false;
  input.focus({preventScroll: true});
  return engine;
}
