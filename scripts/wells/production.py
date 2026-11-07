"""Production on the well pages, from NDIC's free Monthly Production Reports.

The data is the pipeline's well-production feed (see README): per well, keyed by NDIC file
number, the monthly oil, water, gas, gas sold, flared gas and producing days as first reported
in each month's Monthly Production Report (PDF reports from January 2003, Excel workbooks from
May 2015). A null month is unknown, never zero: confidential wells report NULL, and a well absent
from a month's report has no row. The PDF years print no flared gas, and a well in a unitized
pool appears in them only inside its unit's total; the feed flags such wells (unitPool) and the
panel says so. This module attaches the feed to the wells read from the
DMR layer and renders the "Production" panel of a well page: an inline SVG chart of oil (the browser
draws gas or water from the embedded data when asked), a twelve-month table, totals over the covered
months and a CSV built in the browser from the same data.
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


GEOMETRY = {'width': 640, 'height': 220, 'left': 52, 'right': 10, 'top': 10, 'bottom': 22}   # mirrored in templates/production.js


def series_group(values, series, n=None) -> str:
    """One series of the chart as an SVG group: grid with three ticks, unit label and the line, broken at unknown months.

    templates/production.js builds the same markup in the browser for the series the page does not carry
    (the arithmetic and rounding are mirrored there; test_production checks the two agree)."""
    label, unit = SERIES[series]
    g = GEOMETRY
    n = len(values) if n is None else n
    left, top = g['left'], g['top']
    plot_w, plot_h = g['width'] - left - g['right'], g['height'] - top - g['bottom']
    step = plot_w / (n - 1) if n > 1 else 0
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
        ticks += (f'<line x1="{left}" y1="{y:.0f}" x2="{g["width"] - g["right"]}" y2="{y:.0f}"/>'
                  f'<text x="{left - 6}" y="{y + 4:.0f}">{int(vmax * fraction):,}</text>')
    return (f'<g class="s-{series}"><g class="grid">{ticks}</g><text class="unit" x="{left + 8}" y="{top + 12}">{label}, {unit} per month</text>'
            f'<path d="{" ".join(d) or f"M{left},{top + plot_h}"}"><title>{label}, {unit} per month</title></path></g>')


def chart_svg(entry, *, series=('oil',)) -> str:
    """The chart with the year axis and the given series drawn; the page carries oil only, and the browser adds
    gas or water from the embedded data when the visitor asks for them (CSS shows the series named by the svg's class)."""
    months = span_months(entry)
    n = len(months)
    g = GEOMETRY
    left, plot_w = g['left'], g['width'] - g['left'] - g['right']
    step = plot_w / (n - 1) if n > 1 else 0
    labels = ''
    januaries = [i for i, m in enumerate(months) if m.endswith('-01')]
    if n > 1 and len(januaries) >= 2:
        every = max(1, round(len(januaries) / 8))
        for k, i in enumerate(januaries):
            if k % every == 0:
                labels += f'<text x="{left + i * step:.0f}" y="{g["height"] - 6}">{months[i][:4]}</text>'
    else:
        labels += f'<text x="{left}" y="{g["height"] - 6}">{month_label(months[0], True)}</text>'
        if n > 1:
            labels += f'<text x="{left + plot_w:.0f}" y="{g["height"] - 6}" text-anchor="end">{month_label(months[-1], True)}</text>'
    groups = ''.join(series_group(entry['v'][key], key, n) for key in series)
    return (f'<svg class="prodchart show-oil" viewBox="0 0 {g["width"]} {g["height"]}" role="img" '
            f'aria-label="Monthly production, {month_label(months[0])} to {month_label(months[-1])}">'
            f'<g class="axis">{labels}</g>{groups}</svg>')


def table_html(entry) -> str:
    months = span_months(entry)
    v = entry['v']
    rows = ''
    for i in range(max(0, len(months) - TABLE_MONTHS), len(months)):
        cells = ''.join(f'<td>{number(v[s][i])}</td>' for s in ('oil', 'gas', 'water', 'days'))
        rows += f'<tr><th><time datetime="{months[i]}">{month_label(months[i])}</time></th>{cells}</tr>'
    return (f'<table class="units production"><thead><tr><th>Month</th><th>Oil bbl</th><th>Gas mcf</th>'
            f'<th>Water bbl</th><th>Days</th></tr></thead><tbody>{rows}</tbody></table>')


def csv_payload(entry) -> str:
    """The page's own copy of the series for the browser (chart and CSV): the first month and, per series, one
    comma-joined string with an empty field for an unknown month (a fifth the size of a JSON array of nulls)."""
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
            f'two pools reported in one month are summed.{pdf_text}{unit_note(feed, well, entry)}</p></section>')
