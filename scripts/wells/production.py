"""Production on the well pages, from NDIC's free Monthly Production Reports.

The data is the pipeline's well-production feed (see README): per well, keyed by NDIC file
number, the monthly oil, water, gas, gas sold, flared gas and producing days as first reported
in each month's Monthly Production Report (Excel workbooks the Division publishes from May
2015). A null month is unknown, never zero: confidential wells report NULL, and a well absent
from a month's report has no row. This module attaches the feed to the wells read from the
DMR layer and renders the "Production" panel of a well page: an inline SVG chart (oil, gas and
water, one shown at a time), a twelve-month table, totals over the covered months and a CSV
built in the browser from data embedded in the page.
"""
from __future__ import annotations

import gzip
import html
import json
from pathlib import Path

esc = lambda value: html.escape('' if value is None else str(value), quote=True)

SOURCE_URL = 'https://www.dmr.nd.gov/oilgas/mprindex.asp'
SERIES = {'oil': ('Oil', 'bbl'), 'gas': ('Gas', 'mcf'), 'water': ('Water', 'bbl')}
CSV_SERIES = ('oil', 'water', 'gas', 'gasSold', 'flared', 'days')
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
TABLE_MONTHS = 12


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
    stats = {'feedWells': len(data['wells']), 'wellsWithProduction': 0, 'matchedByApi': 0, 'feedWellsNotInLayer': 0}
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
    return stats


def month_label(key: str, short: bool = False) -> str:
    year, month = key[:4], int(key[5:7])
    return f'{MONTHS[month - 1]} {year[2:] if short else year}'


def next_month(key: str) -> str:
    year, month = int(key[:4]), int(key[5:7])
    return f'{year + (month == 12):04d}-{(month % 12) + 1:02d}'


def span_months(entry) -> list[str]:
    months = [entry['first']]
    while months[-1] < entry['last']:
        months.append(next_month(months[-1]))
    return months


def summarize(entry) -> dict:
    """Totals over the covered span, counting only known months."""
    months = span_months(entry)
    v = entry['v']
    out = {'months': months, 'span': len(months), 'reported': entry.get('reported', 0), 'unknown': sum(1 for x in v['oil'] if x is None)}
    for series in ('oil', 'gas', 'water'):
        known = [x for x in v[series] if x is not None]
        out['total_' + series] = sum(known) if known else None      # nothing known is unknown, not zero
    producing = [i for i, x in enumerate(v['oil']) if x]
    out['lastProducing'] = months[producing[-1]] if producing else None
    peak = max(((x, i) for i, x in enumerate(v['oil']) if x is not None), default=None)
    out['peak'] = {'month': months[peak[1]], 'oil': peak[0]} if peak else None
    recent = [x for x in v['oil'][-12:] if x is not None]
    out['last12_oil'] = sum(recent) if recent else None
    out['last12_known'] = len(recent)
    return out


def number(value) -> str:
    return '—' if value is None else f'{value:,}'


def chart_svg(entry, *, width=640, height=220) -> str:
    """Oil, gas and water as three line groups; CSS shows the one named by the svg's class."""
    months = span_months(entry)
    n = len(months)
    left, right, top, bottom = 52, 10, 10, 22
    plot_w, plot_h = width - left - right, height - top - bottom
    step = plot_w / (n - 1) if n > 1 else 0
    groups = []
    for series, (label, unit) in SERIES.items():
        values = entry['v'][series]
        known = [x for x in values if x is not None]
        vmax = max(known) if known else 0
        scale = plot_h / vmax if vmax else 0
        d, pen_down = [], False
        for i, value in enumerate(values):
            if value is None:
                pen_down = False
                continue
            x = left + i * step if n > 1 else left + plot_w / 2
            y = top + plot_h - value * scale
            d.append(f'{"L" if pen_down else "M"}{x:.0f},{y:.0f}')
            pen_down = True
        ticks = ''
        for fraction in (0, 0.5, 1):
            y = top + plot_h - fraction * plot_h
            ticks += (f'<line x1="{left}" y1="{y:.0f}" x2="{width - right}" y2="{y:.0f}"/>'
                      f'<text x="{left - 6}" y="{y + 4:.0f}">{round(vmax * fraction):,}</text>')
        groups.append(f'<g class="s-{series}"><g class="grid">{ticks}</g><text class="unit" x="{left + 8}" y="{top + 12}">{label}, {unit} per month</text>'
                      f'<path d="{" ".join(d) or f"M{left},{top + plot_h}"}"><title>{label}, {unit} per month</title></path></g>')
    labels = ''
    januaries = [i for i, m in enumerate(months) if m.endswith('-01')]
    if n > 1 and len(januaries) >= 2:
        every = max(1, round(len(januaries) / 8))
        for k, i in enumerate(januaries):
            if k % every == 0:
                labels += f'<text x="{left + i * step:.0f}" y="{height - 6}">{months[i][:4]}</text>'
    else:
        labels += f'<text x="{left}" y="{height - 6}">{month_label(months[0], True)}</text>'
        if n > 1:
            labels += f'<text x="{left + plot_w:.0f}" y="{height - 6}" text-anchor="end">{month_label(months[-1], True)}</text>'
    return (f'<svg class="prodchart show-oil" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Monthly production, {month_label(months[0])} to {month_label(months[-1])}">'
            f'<g class="axis">{labels}</g>{"".join(groups)}</svg>')


def table_html(entry) -> str:
    months = span_months(entry)
    v = entry['v']
    rows = ''
    for i in range(max(0, len(months) - TABLE_MONTHS), len(months)):
        cells = ''.join(f'<td class="num">{number(v[s][i])}</td>' for s in ('oil', 'gas', 'water', 'days'))
        rows += f'<tr><th><time datetime="{months[i]}">{month_label(months[i])}</time></th>{cells}</tr>'
    return (f'<table class="units production"><thead><tr><th>Month</th><th class="num">Oil bbl</th><th class="num">Gas mcf</th>'
            f'<th class="num">Water bbl</th><th class="num">Days</th></tr></thead><tbody>{rows}</tbody></table>')


def csv_payload(entry) -> str:
    """The page's own copy of the series, for the CSV the browser builds."""
    return json.dumps({'start': entry['first'], **{s: entry['v'][s] for s in CSV_SERIES}}, separators=(',', ':'))


def well_production_panel(ctx, well) -> str:
    """The "Production" section of a well page ('' when no feed was given to the build)."""
    feed = ctx.get('production')
    if not feed:
        return ''
    first, last = month_label(feed['firstMonth']), month_label(feed['lastMonth'])
    source = (f'<a href="{SOURCE_URL}" rel="noopener">NDIC Monthly Production Reports</a>')
    entry = well.get('production')
    if not entry:
        return (f'<section class="panel production"><h2>Production</h2>'
                f'<p class="note">This well does not appear in the {source} for {first} to {last}, which list every well that '
                'reported in a month. Earlier production, if any, is not covered by the free reports yet.</p></section>')
    s = summarize(entry)
    kpis = [(number(s['total_oil']), f'bbl oil, {month_label(entry["first"], True)} to {month_label(entry["last"], True)}'),
            (number(s['total_gas']), 'mcf gas'), (number(s['total_water']), 'bbl water'),
            (number(s['last12_oil']), f'bbl oil, last 12 months{"" if s["last12_known"] == 12 else f" ({s['last12_known']} known)"}')]
    kpi_html = ''.join(f'<div class="kpi"><b>{value}</b><span>{esc(label)}</span></div>' for value, label in kpis)
    facts = [('First reported month', month_label(entry['first'])), ('Last reported month', month_label(entry['last']))]
    if s['lastProducing']:
        facts.append(('Last month with oil', month_label(s['lastProducing'])))
    if s['peak']:
        facts.append(('Peak month', f'{month_label(s["peak"]["month"])}, {s["peak"]["oil"]:,} bbl'))
    if s['unknown']:
        facts.append(('Unknown months', f'{s["unknown"]} of {s["span"]} (confidential or not in that month\'s report)'))
    if entry.get('pools'):
        facts.append(('Pools reported', ', '.join(entry['pools'])))
    facts_html = ''.join(f'<dt>{esc(k)}</dt><dd>{esc(v)}</dd>' for k, v in facts)
    toggle = ''.join(f'<button type="button" data-series="{key}"{" class=on" if key == "oil" else ""}>{label}</button>'
                     for key, (label, _) in SERIES.items())
    return (f'<section class="panel production"><h2>Production</h2><div class="kpis">{kpi_html}</div>'
            f'<div class="toggle" role="group" aria-label="Series">{toggle}</div>{chart_svg(entry)}'
            f'<details><summary>Last {TABLE_MONTHS} months as a table</summary>{table_html(entry)}</details>'
            f'<dl class="facts">{facts_html}</dl>'
            f'<p class="actions"><button type="button" class="btn csv" data-name="{esc(well["slug"])}-production.csv">Download all months as CSV'
            f'<small>Oil, water, gas, gas sold, flared and days</small></button></p>'
            f'<script type="application/json" id="production-data">{csv_payload(entry).replace("</", "<\\/")}</script>'
            f'<p class="note">As first reported to NDIC in each month\'s report ({source}, {first} to {last}). Operators amend past months later and the '
            'published reports are not updated, so a month can differ from the current record. A blank or missing month is unknown, not zero; '
            'two pools reported in one month are summed. Months before May 2015 are not covered yet.</p></section>')
