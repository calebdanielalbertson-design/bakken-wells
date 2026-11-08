"""Production on the well pages, from NDIC's free Monthly Production Reports.

The data is the pipeline's well-production feed (see README): per well, keyed by NDIC file
number, the monthly oil, water, gas, gas sold, flared gas and producing days as first reported
in each month's Monthly Production Report (PDF reports from January 2003, Excel workbooks from
May 2015). A null month is unknown, never zero: confidential wells report NULL, and a well absent
from a month's report has no row. The PDF years print no flared gas, and a well in a unitized
pool appears in them only inside its unit's total; the feed flags such wells (unitPool) and the
panel says so.

This module attaches the feed to the wells read from the DMR layer and renders the "Production"
panel of a well page. The page itself carries what an engineer reads first and what a search
engine can index: six stat tiles, the early-time and current facts, a twelve-month table and one
chart strip, oil in barrels per producing day. templates/production.js then builds the rest from
the data embedded in the page (gas and water strips, volume and cumulative views, log scale,
gas-oil ratio, water cut and uptime, the hover readout, an Arps decline fit and the CSV), so the
page weight stays flat. strip_svg here and stripSvg there draw identical markup for the same
input: scripts/tests/test_production.py checks it, and the page never jumps when the script
takes over.
"""
from __future__ import annotations

import calendar
import gzip
import html
import json
import math
from pathlib import Path

esc = lambda value: html.escape('' if value is None else str(value), quote=True)

SOURCE_URL = 'https://www.dmr.nd.gov/oilgas/mprindex.asp'
SERIES = {'oil': ('Oil', 'bbl'), 'gas': ('Gas', 'mcf'), 'water': ('Water', 'bbl')}
CSV_SERIES = ('oil', 'water', 'gas', 'gasSold', 'flared', 'days')
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
TABLE_MONTHS = 12

# Chart geometry, mirrored in templates/production.js (GEO, GAP, TOP, AXIS). A strip is one series with its own
# value axis; the browser stacks three of them over one time axis and draws three smaller diagnostic strips.
GEO = {'main': {'W': 820, 'LEFT': 58, 'RIGHT': 74, 'STRIP': 112}, 'diag': {'W': 280, 'LEFT': 44, 'RIGHT': 44, 'STRIP': 84}}
GAP, TOP, AXIS = 26, 22, 26


def load_production(path):
    """The well-production feed, gzip or plain JSON (version 1)."""
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as handle:
        data = json.load(handle)
    if data.get('version') != 1 or data.get('kind') != 'well-production' or 'months' not in data or 'wells' not in data:
        raise ValueError(f'{path} is not a version 1 well-production feed')
    return data


def attach(wells, data):
    """Give each well its feed entry (by file number, else by API). Returns stats."""
    stats = {'feedWells': len(data['wells']), 'wellsWithProduction': 0, 'matchedByApi': 0, 'feedWellsNotInLayer': 0, 'unitPoolWells': 0}
    by_file = {w['file']: w for w in wells}
    by_api = {}
    for w in wells:
        api = (w.get('api_full') or w.get('api') or '')
        if api:
            by_api.setdefault(api[:10], w)
    for file_no, entry in data['wells'].items():
        well = by_file.get(file_no)
        if well is None and entry.get('api'):
            well = by_api.get(entry['api'][:10])
            if well is not None and 'production' not in well:
                stats['matchedByApi'] += 1
        if well is None or 'production' in well:
            stats['feedWellsNotInLayer'] += well is None
            continue
        well['production'] = entry
        stats['wellsWithProduction'] += 1
        stats['unitPoolWells'] += bool(entry.get('unitPool'))
    return stats


def pdf_span(feed) -> tuple[str, str] | None:
    """(first, last) month read from the PDF reports, when the feed has any."""
    pdf = (feed or {}).get('pdf') or {}
    return (pdf['firstMonth'], pdf['lastMonth']) if pdf.get('firstMonth') and pdf.get('lastMonth') else None


def unit_note(feed, well, entry) -> str:
    """A sentence for a well whose pool was reported only as a unit total in the PDF years, if that applies to it."""
    span = pdf_span(feed)
    if not span or not entry.get('unitPool'):
        return ''
    spud = (well.get('extras') or {}).get('spud') or ''
    if spud and spud[:7] > span[1]:
        return ''                      # drilled after the PDF years: nothing of it could have been in a unit total
    return (f' Before {month_label(next_month(span[1]))} this well\'s pool was reported only as a unit total, so the reports hold no '
            f'figures for the well itself in those years; its own figures start in {month_label(entry["first"])}.')


# ---------------------------------------------------------------- months and numbers (mirrored in production.js)

def month_label(key: str, short: bool = False) -> str:
    year, month = key[:4], int(key[5:7])
    return f'{MONTHS[month - 1]} ’{year[2:]}' if short else f'{MONTHS[month - 1]} {year}'


def next_month(key: str) -> str:
    year, month = int(key[:4]), int(key[5:7])
    return f'{year + (month == 12):04d}-{(month % 12) + 1:02d}'


def span_months(entry) -> list[str]:
    months = [entry['first']]
    while months[-1] < entry['last']:
        months.append(next_month(months[-1]))
    return months


def days_in(key: str) -> int:
    return calendar.monthrange(int(key[:4]), int(key[5:7]))[1]


def number(value) -> str:
    return '—' if value is None else f'{value:,}'


def fmt(x) -> str:
    """Chart coordinates: nearest integer, ties to even (what production.js's fmt does)."""
    return f'{x:.0f}'


def fixed(x, decimals: int) -> str:
    return f'{x:.{decimals}f}'


def trim0(text: str) -> str:
    """'1.50M' -> '1.5M', '12.0K' -> '12K'."""
    if '.' not in text:
        return text
    head, unit = text[:-1], text[-1]
    return head.rstrip('0').rstrip('.') + unit


def compact(value) -> str:
    """1,284 / 12.9K / 1.84M, as the stat tiles and the chart print them."""
    if value is None:
        return '—'
    v = abs(value)
    if v >= 1e6:
        return trim0(fixed(value / 1e6, 1 if v >= 1e7 else 2) + 'M')
    if v >= 1e4:
        return trim0(fixed(value / 1e3, 0 if v >= 1e5 else 1) + 'K')
    return f'{round(value):,}'


def nice_ticks(vmax) -> list[float]:
    """Gridline values from 0 to just above the maximum, three to four of them on round steps."""
    if not vmax or vmax <= 0:
        return [0, 1]
    raw = vmax / 3
    mag = 10 ** math.floor(math.log10(raw))
    norm = raw / mag
    step = (1 if norm <= 1 else 2 if norm <= 2 else 2.5 if norm <= 2.5 else 5 if norm <= 5 else 10) * mag
    count = math.ceil(vmax / step - 1e-9)
    return [round(k * step, 10) for k in range(count + 1)]


def tick_label(value, ticks) -> str:
    """Axis tick text: in full under 10,000, compact above; decimals only when the steps need them."""
    top = ticks[-1]
    if all(float(t).is_integer() for t in ticks):
        return compact(int(value)) if top >= 10000 else f'{int(value):,}'
    decimals = 2 if 0 < ticks[1] < 0.1 else 1
    return f'{value:,.{decimals}f}'


# ---------------------------------------------------------------- derived series and figures

def rate(volume, days):
    """Barrels (or mcf) per producing day; unknown when either side is unknown or no days were reported."""
    return None if volume is None or not days or days <= 0 else volume / days


def derive(entry) -> dict:
    """Per-month derived series the page prints: oil rate, gas-oil ratio and water cut."""
    v = entry['v']
    n = len(v['oil'])
    oil_rate = [rate(v['oil'][i], v['days'][i]) for i in range(n)]
    gor = [v['gas'][i] * 1000 / v['oil'][i] if v['oil'][i] and v['gas'][i] is not None else None for i in range(n)]
    wcut = [100 * v['water'][i] / (v['oil'][i] + v['water'][i]) if v['oil'][i] is not None and v['water'][i] is not None and v['oil'][i] + v['water'][i] > 0 else None
            for i in range(n)]
    return {'n': n, 'oilRate': oil_rate, 'gor': gor, 'wcut': wcut}


def _known(values):
    return [x for x in values if x is not None]


def _ratio(num, den, indices, scale=1.0):
    """Sum of num over sum of den across the given months where both are known; None when nothing is known or den is zero."""
    a = b = 0
    seen = False
    for i in indices:
        if num[i] is not None and den[i] is not None:
            a += num[i]
            b += den[i]
            seen = True
    return a * scale / b if seen and b > 0 else None


def summarize(entry) -> dict:
    """Totals over the covered span, counting only known months."""
    months = span_months(entry)
    v = entry['v']
    out = {'months': months, 'span': len(months), 'reported': entry.get('reported', 0), 'unknown': sum(1 for x in v['oil'] if x is None)}
    for series in ('oil', 'gas', 'water'):
        known = _known(v[series])
        out['total_' + series] = sum(known) if known else None      # nothing known is unknown, not zero
    producing = [i for i, x in enumerate(v['oil']) if x]
    out['lastProducing'] = months[producing[-1]] if producing else None
    peak = max(((x, i) for i, x in enumerate(v['oil']) if x is not None), default=None)
    out['peak'] = {'month': months[peak[1]], 'oil': peak[0]} if peak else None
    recent = _known(v['oil'][-12:])
    out['last12_oil'] = sum(recent) if recent else None
    out['last12_known'] = len(recent)
    return out


def metrics(entry) -> dict:
    """Everything the stat tiles and the two fact lists print. Unknown stays None; a partial figure says how many months it rests on."""
    v = entry['v']
    d = derive(entry)
    n = d['n']
    months = span_months(entry)
    s = summarize(entry)
    last12 = range(max(0, n - 12), n)
    prior12 = range(max(0, n - 24), max(0, n - 12))
    first12 = range(0, min(12, n))
    m = {'n': n, 'months': months, 'first': entry['first'], 'last': entry['last'], 'knownMonths': n - s['unknown'], 'unknown': s['unknown'],
         'cumOil': s['total_oil'], 'cumGas': s['total_gas'], 'cumWater': s['total_water'], 'lastProducing': s['lastProducing'], 'pools': entry.get('pools') or []}
    # peaks
    peak_rate = max(((x, i) for i, x in enumerate(d['oilRate']) if x is not None), default=None)
    m['peakRate'] = {'index': peak_rate[1], 'month': months[peak_rate[1]], 'rate': peak_rate[0], 'oil': v['oil'][peak_rate[1]], 'days': v['days'][peak_rate[1]]} if peak_rate else None
    m['peakMonth'] = {'index': months.index(s['peak']['month']), 'month': s['peak']['month'], 'oil': s['peak']['oil']} if s['peak'] else None
    # last twelve months against the twelve before
    l12, p12 = _known([v['oil'][i] for i in last12]), _known([v['oil'][i] for i in prior12])
    m['last12Oil'], m['last12Known'] = (sum(l12) if l12 else None), len(l12)
    m['yoy'] = (sum(l12) - sum(p12)) / sum(p12) * 100 if len(l12) == 12 and len(p12) == 12 and sum(p12) > 0 else None
    # ratios
    m['gor12'] = _ratio(v['gas'], v['oil'], last12, 1000)
    m['gorYear1'] = _ratio(v['gas'], v['oil'], first12, 1000)
    liquids = [None if v['oil'][i] is None or v['water'][i] is None else v['oil'][i] + v['water'][i] for i in range(n)]
    m['wcut12'] = _ratio(v['water'], liquids, last12, 100)
    m['wcutYear1'] = _ratio(v['water'], liquids, first12, 100)
    m['sold12'] = _ratio(v['gasSold'], v['gas'], last12, 100)
    flared = _known([v['flared'][i] for i in last12])
    m['flared12'] = sum(flared) if flared else None
    # early time
    m['monthsToPeak'] = m['peakMonth']['index'] + 1 if m['peakMonth'] else None
    m['firstMonths'] = {}
    for k in (3, 6, 12, 24):
        if n >= k:
            known = _known(v['oil'][:k])
            m['firstMonths'][k] = {'oil': sum(known) if known else None, 'known': len(known)}
    year1 = _known(v['oil'][:12])
    m['year1Share'] = 100 * sum(year1) / m['cumOil'] if n >= 12 and m['cumOil'] else None
    # today
    latest = next((x for x in reversed(d['oilRate']) if x is not None), None)
    m['latestRate'] = latest
    m['shareOfPeak'] = 100 * latest / m['peakRate']['rate'] if latest is not None and m['peakRate'] and m['peakRate']['rate'] else None
    known_days = [(v['days'][i], days_in(months[i])) for i in last12 if v['days'][i] is not None]
    m['uptime12'] = 100 * sum(a for a, _ in known_days) / sum(b for _, b in known_days) if known_days else None
    return m


# ---------------------------------------------------------------- the chart strip (mirrored in production.js)

def strip_svg(geo, y0, title, unit, cls, values, idx0, idx1, show_axis, first, n) -> str:
    """One series as SVG: gridlines with round ticks, bands over unknown months, the title, the line broken at
    unknown months, an end marker with the last value, and the time axis when asked. Linear scale; production.js
    adds log scale and the fit overlay on top of the same markup."""
    W, LEFT, RIGHT, plot_h = geo['W'], geo['LEFT'], geo['RIGHT'], geo['STRIP']
    top = y0
    N = idx1 - idx0
    plot_w = W - LEFT - RIGHT

    def x(i):
        return LEFT + (i - idx0) * plot_w / (N - 1) if N > 1 else LEFT + plot_w / 2

    vis = [values[i] for i in range(idx0, min(idx1, n)) if values[i] is not None]
    vmax = max(vis) if vis else 0
    ticks = nice_ticks(100 if cls == 'pct' else vmax)
    tmin, tmax = ticks[0], ticks[-1]

    def y(v):
        return top + plot_h - (v - tmin) / (tmax - tmin) * plot_h

    out = '<g class="grid">'
    for t in ticks:
        out += (f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{fmt(y(t))}" y2="{fmt(y(t))}"/>'
                f'<text x="{LEFT - 8}" y="{fmt(y(t) + 4)}" text-anchor="end">{tick_label(t, ticks)}</text>')
    out += '</g>'
    half = (x(idx0 + 1) - x(idx0)) / 2 if N > 1 else 6
    for i in range(idx0, min(idx1, n)):
        if values[i] is None:
            out += f'<rect class="band" x="{fmt(x(i) - half)}" y="{top}" width="{fmt(2 * half)}" height="{plot_h}"/>'
    out += f'<text class="t" x="{LEFT}" y="{top - 8}">{esc(title)}<tspan> {esc(unit)}</tspan></text>'
    d, pen = [], False
    for i in range(idx0, min(idx1, n)):
        if values[i] is None:
            pen = False
            continue
        d.append(f'{"L" if pen else "M"}{fmt(x(i))},{fmt(y(values[i]))}')
        pen = True
    out += f'<path class="s {cls}" d="{" ".join(d) or f"M{LEFT},{top + plot_h}"}"/>'
    last = next((i for i in range(min(idx1, n) - 1, idx0 - 1, -1) if values[i] is not None), None)
    if last is not None:
        label = f'{fixed(values[last], 0)}%' if cls == 'pct' else compact(round(values[last]))
        out += (f'<circle class="end {cls}" cx="{fmt(x(last))}" cy="{fmt(y(values[last]))}" r="4"/>'
                f'<text class="end" x="{fmt(x(last) + 9)}" y="{fmt(y(values[last]) + 4)}">{label}</text>')
    out += f'<circle class="pt {cls}" data-strip="{cls}" r="4" cx="0" cy="0"/>'
    if show_axis:
        out += f'<g class="axis"><line x1="{LEFT}" x2="{W - RIGHT}" y1="{fmt(top + plot_h + 0.5)}" y2="{fmt(top + plot_h + 0.5)}"/>'
        px_per_month = plot_w / max(1, N - 1)
        want = math.ceil(64 / px_per_month)
        every = next((e for e in (1, 2, 3, 6, 12, 24, 48, 60, 120, 240) if e >= want), 240)
        for i in range(idx0, idx1):
            key = add_months(first, i)
            year, month = int(key[:4]), int(key[5:7])
            if (month == 1 and year % (every // 12) == 0) if every >= 12 else ((i - idx0) % every == 0):
                out += f'<text x="{fmt(x(i))}" y="{top + plot_h + 18}" text-anchor="middle">{year if every >= 12 else month_label(key, True)}</text>'
        out += '</g>'
    return out


def add_months(first: str, i: int) -> str:
    year, month = int(first[:4]), int(first[5:7]) + i
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f'{year:04d}-{month:02d}'


def chart_svg(entry) -> str:
    """The oil strip the page carries: barrels per producing day over every month, the default view of the panel."""
    d = derive(entry)
    n = d['n']
    g = GEO['main']
    height = TOP + g['STRIP'] + AXIS
    return (f'<svg class="prodchart main" viewBox="0 0 {g["W"]} {height}" role="img" '
            f'aria-label="Oil, barrels per producing day, {month_label(entry["first"])} to {month_label(entry["last"])}">'
            f'{strip_svg(g, TOP, "Oil", "bbl/d", "oil", d["oilRate"], 0, n, True, entry["first"], n)}</svg>')


# ---------------------------------------------------------------- the panel

def kpi_tiles(m) -> str:
    def tile(label, value, unit, detail):
        return f'<div class="kpi"><span class="l">{esc(label)}</span><b>{esc(value)}<small>{esc(unit)}</small></b><span class="d">{detail}</span></div>'

    tiles = [tile('Cumulative oil', compact(m['cumOil']), 'bbl',
                  f'{esc(month_label(m["first"], True))} to {esc(month_label(m["last"], True))} · <strong>{m["knownMonths"]:,}</strong> of {m["n"]:,} months known')]
    pr = m['peakRate']
    tiles.append(tile('Peak oil rate', f'{round(pr["rate"]):,}' if pr else '—', 'bbl/d',
                      f'{esc(month_label(pr["month"]))} · {pr["oil"]:,} bbl in {pr["days"]} days' if pr else 'no month with producing days'))
    if m['yoy'] is not None:
        detail = f'<strong>{m["yoy"]:+.0f}%</strong> against the 12 before'
    else:
        detail = f'{m["last12Known"]} of {min(12, m["n"])} months known'
    tiles.append(tile('Oil, last 12 months', compact(m['last12Oil']), 'bbl', detail))
    tiles.append(tile('Gas–oil ratio, last 12 months', f'{round(m["gor12"]):,}' if m['gor12'] is not None else '—', 'scf/bbl',
                      f'first year <strong>{round(m["gorYear1"]):,}</strong> scf/bbl' if m['gorYear1'] is not None else 'gas over oil, produced'))
    tiles.append(tile('Water cut, last 12 months', f'{m["wcut12"]:.0f}' if m['wcut12'] is not None else '—', '%',
                      (f'first year <strong>{m["wcutYear1"]:.0f}%</strong> · ' if m['wcutYear1'] is not None else '') + f'{compact(m["cumWater"])} bbl water in all'))
    tiles.append(tile('Gas sold, last 12 months', f'{m["sold12"]:.0f}' if m['sold12'] is not None else '—', '%',
                      'of gas produced · ' + (f'<strong>{compact(m["flared12"])}</strong> mcf reported flared' if m['flared12'] is not None else f'{compact(m["cumGas"])} mcf in all')))
    return '<div class="kpis">' + ''.join(tiles) + '</div>'


def fact_lists(m) -> str:
    def partial(item):
        if item['known'] == 0:
            return '—'
        return f'{item["oil"]:,} bbl'

    early = [('First reported month', month_label(m['first']))]
    if m['peakMonth']:
        early.append(('Months to peak', f'{m["monthsToPeak"]} ({month_label(m["peakMonth"]["month"])})'))
    if m['peakRate']:
        early.append(('Peak month rate', f'{round(m["peakRate"]["rate"]):,} bbl/d on {m["peakRate"]["days"]} days'))
    for k, item in m['firstMonths'].items():
        text = partial(item) + ('' if item['known'] in (0, k) else f' ({item["known"]} of {k} known)')
        early.append((f'First {k} months, oil', text))
    if m['year1Share'] is not None:
        early.append(('Share of cumulative in year 1', f'{m["year1Share"]:.0f}%'))
    today = [('Last reported month', month_label(m['last']))]
    if m['lastProducing']:
        today.append(('Last month with oil', month_label(m['lastProducing'])))
    if m['latestRate'] is not None:
        today.append(('Latest oil rate', f'{round(m["latestRate"]):,} bbl/d'))
    if m['shareOfPeak'] is not None:
        today.append(('Latest rate as share of peak', f'{m["shareOfPeak"]:.1f}%'))
    if m['uptime12'] is not None:
        today.append(('Uptime, last 12 months', f'{m["uptime12"]:.0f}% of days'))
    today.append(('Cumulative gas', f'{compact(m["cumGas"])} mcf' if m['cumGas'] is not None else '—'))
    today.append(('Cumulative water', f'{compact(m["cumWater"])} bbl' if m['cumWater'] is not None else '—'))
    if m['unknown']:
        today.append(('Unknown months', f'{m["unknown"]} of {m["n"]} (confidential or not in that month\'s report)'))
    if m['pools']:
        today.append(('Pools reported', ', '.join(m['pools'])))

    def dl(rows):
        return '<dl class="facts">' + ''.join(f'<dt>{esc(k)}</dt><dd>{esc(v)}</dd>' for k, v in rows) + '</dl>'
    return (f'<div class="cols"><div><h3 class="block">Early time</h3>{dl(early)}</div>'
            f'<div><h3 class="block">Today</h3>{dl(today)}</div></div>')


def table_html(entry) -> str:
    months = span_months(entry)
    v = entry['v']
    d = derive(entry)
    rows = ''
    for i in range(max(0, len(months) - TABLE_MONTHS), len(months)):
        cells = ''.join(f'<td>{number(v[s][i])}</td>' for s in ('oil', 'gas', 'water', 'days'))
        oil_rate, gor, wcut = d['oilRate'][i], d['gor'][i], d['wcut'][i]
        cells += f'<td>{number(None if oil_rate is None else round(oil_rate))}</td><td>{number(None if gor is None else round(gor))}</td>'
        cells += '<td>' + ('—' if wcut is None else f'{wcut:.0f}%') + '</td>'
        rows += f'<tr><th><time datetime="{months[i]}">{month_label(months[i])}</time></th>{cells}</tr>'
    return (f'<table class="units production"><thead><tr><th>Month</th><th>Oil bbl</th><th>Gas mcf</th><th>Water bbl</th><th>Days</th>'
            f'<th>Oil bbl/d</th><th>GOR scf/bbl</th><th>Water cut</th></tr></thead><tbody>{rows}</tbody></table>')


def csv_payload(entry) -> str:
    """The page's own copy of the series for the browser (chart, readout, fit and CSV): the first month and, per series,
    one comma-joined string with an empty field for an unknown month (a fifth the size of a JSON array of nulls)."""
    return json.dumps({'start': entry['first'], **{s: ','.join('' if x is None else str(x) for x in entry['v'][s]) for s in CSV_SERIES}},
                      separators=(',', ':'))


def well_production_panel(ctx, well) -> str:
    """The "Production" section of a well page ('' when no feed was given to the build)."""
    feed = ctx.get('production')
    if not feed:
        return ''
    first, last = month_label(feed['firstMonth']), month_label(feed['lastMonth'])
    source = (f'<a href="{SOURCE_URL}" rel="noopener">NDIC Monthly Production Reports</a>')
    entry = well.get('production')
    span = pdf_span(feed)
    pdf_text = ''
    if span:
        pdf_text = (f' Months from {month_label(span[0])} to {month_label(span[1])} are read from the Division\'s PDF reports, which print no '
                    'flared gas and list a well in a unitized pool only inside its unit\'s total.')
    if not entry:
        unit_text = (f' Before {month_label(next_month(span[1]))} a well in a unitized pool was listed only inside its unit\'s total, '
                     'so such a well does not appear on its own in those years either.') if span else ''
        return (f'<section class="panel production"><h2>Production</h2>'
                f'<p class="note">This well does not appear on its own in the {source} for {first} to {last}, which list every well that '
                f'reported in a month.{unit_text}</p></section>')
    m = metrics(entry)
    pools = m['pools']
    sub = (f'{m["n"]:,} months reported, {month_label(m["first"])} to {month_label(m["last"])}'
           + (f', {esc(" and ".join(pools))} pool{"" if len(pools) == 1 else "s"}' if pools else '') + '.')
    return (f'<section class="panel production"><h2>Production</h2><p class="sub">{sub}</p>'
            f'{kpi_tiles(m)}'
            f'<div class="chartwrap">{chart_svg(entry)}</div><div class="diag"></div>'
            f'{fact_lists(m)}'
            f'<details><summary>Last {TABLE_MONTHS} months as a table</summary><div class="scroll">{table_html(entry)}</div></details>'
            f'<p class="actions"><button type="button" class="btn csv" data-name="{esc(well["slug"])}-production.csv">Download all months as CSV'
            f'<small>Oil, water, gas, gas sold, flared, days, and the rate and ratios</small></button></p>'
            f'<script type="application/json" id="production-data">{csv_payload(entry).replace("</", "<\\/")}</script>'
            f'<p class="note">As first reported to NDIC in each month\'s report ({source}, {first} to {last}). Operators amend past months later and the '
            'published reports are not updated, so a month can differ from the current record. A blank or missing month is unknown, not zero, and a '
            'cumulative over such months is a lower bound; two pools reported in one month are summed. Rates per producing day divide the month\'s '
            'volume by the days reported, so a month reported without producing days has no rate. Gas–oil ratio is gas produced over oil produced; '
            f'water cut is water over oil plus water.{pdf_text}{unit_note(feed, well, entry)}</p></section>')
