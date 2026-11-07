"""Township pages for the well index: one page per PLSS township with wells or spacing units.

    /townships/                    townships by county, with well and unit counts
    /townships/<T>n-<R>w/          one township: a schematic 6 x 6 section grid with every well
                                   head placed by its published legal location, the wells and
                                   spacing units section by section, the orders that touched
                                   those units, and the operators active there

Everything comes from data the index already has: the legal location of every well in the DMR
wells layer ("NWNW Sec 28 T153N R91W") and the land description of every spacing unit in the
well-index data file ("T153N R91W sections 21, 28"). The grid is schematic: sections are drawn
as equal squares in the standard PLSS numbering, and a well sits in the quarter-quarter its
legal location names, not at its surveyed coordinates. Every North Dakota township is north of
the baseline and every range west of the fifth principal meridian, so "153n-91w" is unambiguous.
"""
from __future__ import annotations

import html
import re
from collections import Counter, defaultdict

esc = lambda value: html.escape('' if value is None else str(value), quote=True)
slugify = lambda value: re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-')[:80] or 'unknown'

LEGAL = re.compile(r'^(?:(?P<qq>[A-Z0-9 ]+?) )?Sec (?P<sec>\d{1,2}) T(?P<t>\d{3})N R(?P<r>\d{2,3})W$')
LAND = re.compile(r'T(?P<t>\d{3})N R(?P<r>\d{2,3})W sections? (?P<secs>[\d, ]+)')
QUARTER_X = {'W': 0.0, 'E': 0.5}
QUARTER_Y = {'N': 0.0, 'S': 0.5}
# Section numbers in a standard township, row by row from the north, west to east.
GRID = [[6, 5, 4, 3, 2, 1], [7, 8, 9, 10, 11, 12], [18, 17, 16, 15, 14, 13],
        [19, 20, 21, 22, 23, 24], [30, 29, 28, 27, 26, 25], [31, 32, 33, 34, 35, 36]]
CELL = {sec: (col, row) for row, sections in enumerate(GRID) for col, sec in enumerate(sections)}
ORDERS_SHOWN = 30


def parse_legal(text):
    """{'qq', 'section', 'township', 'range'} from a legal location string, or None."""
    match = LEGAL.match((text or '').strip())
    if not match:
        return None
    section = int(match.group('sec'))
    if not 1 <= section <= 36:
        return None
    return {'qq': (match.group('qq') or '').strip(), 'section': section, 'township': int(match.group('t')), 'range': int(match.group('r'))}


def parse_lands(lands):
    """[(township, range, [sections]), ...] from a unit's land description strings."""
    out = []
    for text in lands or []:
        for match in LAND.finditer(text or ''):
            sections = sorted({int(s) for s in re.findall(r'\d+', match.group('secs')) if 1 <= int(s) <= 36})
            if sections:
                out.append((int(match.group('t')), int(match.group('r')), sections))
    return out


def slug_of(t, r) -> str:
    return f'{t}n-{r}w'


def short_name(t, r) -> str:
    return f'T{t}N R{r}W'


def long_name(t, r) -> str:
    return f'Township {t} North, Range {r} West'


def qq_position(qq):
    """(x, y) in the unit square for a quarter-quarter code; approximate placements say so."""
    code = (qq or '').replace(' ', '').upper()
    if len(code) == 4 and code[0] in 'NS' and code[1] in 'EW' and code[2] in 'NS' and code[3] in 'EW':
        return (QUARTER_X[code[3]] + QUARTER_X[code[1]] / 2 + 0.125, QUARTER_Y[code[2]] + QUARTER_Y[code[0]] / 2 + 0.125, False)
    if len(code) == 3 and code[0] == 'C' and code[1] in 'NS' and code[2] in 'EW':
        return (QUARTER_X[code[2]] + 0.25, QUARTER_Y[code[1]] + 0.25, False)
    if len(code) == 4 and code[1] == '2' and code[0] in 'NSEW' and code[2] in 'NS' and code[3] in 'EW':
        x, y = QUARTER_X[code[3]] + 0.25, QUARTER_Y[code[2]] + 0.25
        if code[0] == 'E':
            x += 0.125
        elif code[0] == 'W':
            x -= 0.125
        elif code[0] == 'S':
            y += 0.125
        else:
            y -= 0.125
        return (x, y, False)
    return (0.5, 0.5, True)


def new_township(t, r):
    return {'t': t, 'r': r, 'slug': slug_of(t, r), 'short': short_name(t, r), 'title': long_name(t, r), 'wells': [],
            'sections': defaultdict(lambda: {'wells': [], 'units': []}), 'units': {}, 'operators': Counter(), 'county': None, 'county_slug': None}


def attach(wells, units):
    """Group wells (by legal location) and units (by land description) into townships. Returns slug -> township."""
    townships = {}

    def township(t, r):
        slug = slug_of(t, r)
        if slug not in townships:
            townships[slug] = new_township(t, r)
        return townships[slug]

    for well in wells:
        legal = parse_legal(well.get('legal'))
        if not legal:
            continue
        tw = township(legal['township'], legal['range'])
        well['township'] = tw
        well['section'] = legal['section']
        well['qq'] = legal['qq']
        tw['wells'].append(well)
        tw['sections'][legal['section']]['wells'].append(well)
        if well.get('operator'):
            tw['operators'][well['operator']] += 1
    for unit in units or []:
        for t, r, sections in parse_lands(unit.get('lands')):
            tw = township(t, r)
            tw['units'][unit['id']] = unit
            for section in sections:
                if unit not in tw['sections'][section]['units']:
                    tw['sections'][section]['units'].append(unit)
            unit.setdefault('townships', []).append({'township': tw, 'sections': sections})
    for tw in townships.values():
        counties = Counter(w['county'] for w in tw['wells'] if w.get('county'))
        tw['county'] = counties.most_common(1)[0][0] if counties else None
        tw['county_slug'] = slugify(tw['county'] or 'unknown county')
        tw['wells'].sort(key=lambda w: (w.get('section') or 0, w['display_name'], w['file']))
    return dict(sorted(townships.items(), key=lambda item: (item[1]['t'], item[1]['r'])))


def grid_svg(tw, base='', cell=58, pad=4):
    """The township as a 6 x 6 grid; wells as dots in their quarter-quarter, unit sections shaded."""
    size = cell * 6 + pad * 2
    parts = [f'<svg class="twpgrid" viewBox="0 0 {size} {size}" role="img" aria-label="Sections of {esc(tw["short"])} with well locations">']
    for sec, (col, row) in CELL.items():
        x, y = pad + col * cell, pad + row * cell
        data = tw['sections'].get(sec)
        classes = ['sec']
        if data and data['units']:
            classes.append('unit')
        if data and data['wells']:
            classes.append('wells')
        rect = f'<rect class="{" ".join(classes)}" x="{x}" y="{y}" width="{cell}" height="{cell}"/><text x="{x + 4}" y="{y + 12}">{sec}</text>'
        if data and data['wells']:
            parts.append(f'<a href="#section-{sec}"><title>Section {sec}: {len(data["wells"])} wells</title>{rect}</a>')
        else:
            parts.append(rect)
    dots = []
    for well in tw['wells']:
        col, row = CELL[well['section']]
        fx, fy, approx = qq_position(well.get('qq'))
        cx, cy = pad + col * cell + fx * cell, pad + row * cell + fy * cell
        dots.append(f'<a href="{base}/wells/{esc(well["slug"])}/"><circle class="{"approx" if approx else "head"} s-{esc(well["status"] or "none").lower()}" cx="{cx:.1f}" cy="{cy:.1f}" r="3.2">'
                    f'<title>{esc(well["display_name"])} · {esc(well["operator"] or "unknown operator")} · {esc(well["legal"])}</title></circle></a>')
    parts.append('<g class="heads">' + ''.join(dots) + '</g></svg>')
    return ''.join(parts)


def unit_line(ctx, unit, *, sections=None):
    base = ctx['base_path']
    allowance = unit.get('allowance') or {}
    bits = [esc(unit.get('label') or unit['id'])]
    if sections:
        bits.append('sections ' + ', '.join(str(s) for s in sections) if len(sections) > 1 else f'section {sections[0]}')
    if allowance.get('terminated'):
        bits.append('terminated')
    elif allowance.get('wells') is not None:
        bits.append(f'{allowance["wells"]:g} wells allowed')
    members = unit.get('members')
    if members is not None:
        bits.append(f'{len(members)} surveyed wells inside')
    where = unit.get('where') or unit.get('title') or unit['id']
    return f'<li><a href="{base}/units/{esc(unit["id"])}/">{esc(where)}</a><small>{" · ".join(bits)}</small></li>'


def township_orders(tw):
    """(date, order, verb, unit) for every order event on the township's units, newest first."""
    seen = {}
    for unit in tw['units'].values():
        for event in unit.get('events') or []:
            order = event.get('order')
            if not order:
                continue
            key = (order, unit['id'])
            if key not in seen:
                seen[key] = (event.get('date') or '', order, event.get('verb') or event.get('event') or '', unit)
    return sorted(seen.values(), key=lambda row: (row[0], row[1]), reverse=True)


def township_page(ctx, tw, *, shell, status_text, well_list, kpis, format_date):
    base = ctx['base_path']
    path = f'/townships/{tw["slug"]}/'
    wells = tw['wells']
    active = sum(1 for w in wells if w['status'] == 'A')
    permitted = sum(1 for w in wells if w['status'] == 'LOC')
    county_label = f'{tw["county"]} County' if tw['county'] else 'North Dakota'
    county_link = f'<a href="{base}/counties/{tw["county_slug"]}/">{esc(county_label)}</a>' if tw['county'] else esc(county_label)
    sections_html = ''
    for sec in sorted(tw['sections']):
        data = tw['sections'][sec]
        if not data['wells'] and not data['units']:
            continue
        block = f'<h3 id="section-{sec}">Section {sec}</h3>'
        if data['wells']:
            block += well_list(ctx, data['wells'], with_operator=True)
        if data['units']:
            block += '<ul class="plain">' + ''.join(unit_line(ctx, u) for u in sorted(data['units'], key=lambda u: -(u.get('acres') or 0))) + '</ul>'
        sections_html += block
    units_html = ''
    if tw['units']:
        rows = []
        for unit in sorted(tw['units'].values(), key=lambda u: (-(u.get('acres') or 0), u['id'])):
            here = next((t['sections'] for t in unit.get('townships', []) if t['township'] is tw), None)
            rows.append(unit_line(ctx, unit, sections=here))
        units_html = f'<section class="panel"><h2>Spacing units</h2><ul class="nearby">{"".join(rows)}</ul><p class="note">Units whose published land description names this township. A unit can extend into a neighbouring township; overlapping units are not additive.</p></section>'
    orders = township_orders(tw)
    orders_html = ''
    if orders:
        items = ''.join(f'<li><time datetime="{esc(date)}">{esc(format_date(date) if date else "undated")}</time> <a href="{base}/orders/{esc(order)}/">Order {esc(order)}</a> '
                        f'<span class="what">{esc(verb)}</span> <a class="land" href="{base}/units/{esc(unit["id"])}/">{esc(unit.get("where") or unit.get("label") or unit["id"])}</a></li>'
                        for date, order, verb, unit in orders[:ORDERS_SHOWN])
        more = f'<p class="note">{len(orders) - ORDERS_SHOWN:,} earlier order events are on the unit pages.</p>' if len(orders) > ORDERS_SHOWN else ''
        orders_html = f'<section class="panel"><h2>Orders</h2><ol class="timeline orders">{items}</ol>{more}<p class="note">Facts extracted from signed NDIC orders by the pipeline, machine-extracted and not human-reviewed.</p></section>'
    ops_html = ''.join(f'<li><a href="{base}/operators/{slugify(name)}/">{esc(name)}</a> <small>{count:,}</small></li>' for name, count in tw['operators'].most_common())
    body = (f'<p class="eyebrow">Township · {county_link}</p><h1>{esc(tw["title"])}</h1>'
            f'<p class="lede">{esc(tw["short"])}: {len(wells):,} well records, {len(tw["units"]):,} spacing units, {len(tw["operators"]):,} operators.</p>'
            + kpis([(f'{len(wells):,}', 'wells'), (f'{active:,}', 'active'), (f'{permitted:,}', 'permitted, not drilled'), (f'{len(tw["units"]):,}', 'spacing units')])
            + f'<div class="grid"><section class="panel"><h2>Sections</h2>{grid_svg(tw, base)}'
            '<p class="note">Schematic: sections drawn as equal squares in standard PLSS numbering; each dot is a well head placed in the quarter-quarter its published legal location names (a lot or an unparsed description sits at the section centre). Shaded sections lie in a spacing unit. Click a section or a dot.</p></section>'
            f'<section class="panel"><h2>Operators</h2><ul class="plain">{ops_html or "<li>No wells recorded</li>"}</ul></section></div>'
            f'<section class="panel"><h2>Wells and units by section</h2>{sections_html or "<p class=note>No wells or units recorded.</p>"}</section>'
            f'{units_html}{orders_html}'
            f'<p class="note">Legal locations as published by NDIC in the wells layer; a horizontal well\'s lateral can run into other sections. Township and range are north and west throughout North Dakota.</p>')
    description = (f'{tw["title"]} ({tw["short"]}), {county_label}, North Dakota: {len(wells):,} well records, {len(tw["units"]):,} spacing units, '
                   f'{len(tw["operators"]):,} operators, with every well placed in its section.')
    jsonld = [{'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'name': 'Wells', 'item': ctx['base_url'] + '/'},
        {'@type': 'ListItem', 'position': 2, 'name': 'Townships', 'item': ctx['base_url'] + '/townships/'},
        {'@type': 'ListItem', 'position': 3, 'name': tw['short'], 'item': ctx['base_url'] + path}]}]
    return shell(ctx, title=f'{tw["short"]} · {tw["title"]} · {county_label}, ND', description=description, path=path, body=body,
                 crumbs=[('Wells', f'{base}/'), ('Townships', f'{base}/townships/'), (tw['short'], None)], jsonld=jsonld, current='townships')


def townships_index_page(ctx, townships, *, shell):
    base = ctx['base_path']
    by_county = defaultdict(list)
    for tw in townships.values():
        by_county[tw['county'] or 'County not recorded'].append(tw)
    sections = ''
    for county in sorted(by_county, key=lambda c: (c == 'County not recorded', c)):
        items = ''.join(f'<li><a href="{base}/townships/{tw["slug"]}/">{esc(tw["short"])}</a> <small>{len(tw["wells"]):,} wells'
                        + (f' · {len(tw["units"]):,} units' if tw['units'] else '') + '</small></li>' for tw in by_county[county])
        slug = slugify(county)
        sections += f'<section class="panel" id="{slug}"><h2>{esc(county)}{" County" if county != "County not recorded" else ""}</h2><ul class="plain long">{items}</ul></section>'
    total_wells = sum(len(tw['wells']) for tw in townships.values())
    body = (f'<h1>Townships</h1><p class="lede">{len(townships):,} townships with well records or spacing units, by county. '
            'Each page places every well in its section and lists the spacing units and orders on that land.</p>' + sections)
    return shell(ctx, title='Townships · North Dakota wells', description=f'{len(townships):,} North Dakota townships with {total_wells:,} well records, each with a section grid, its wells, spacing units and orders.',
                 path='/townships/', body=body, crumbs=[('Wells', f'{base}/'), ('Townships', None)], current='townships')


def county_townships_panel(ctx, county_slug, townships):
    base = ctx['base_path']
    mine = [tw for tw in townships.values() if tw['county_slug'] == county_slug]
    if not mine:
        return ''
    items = ''.join(f'<li><a href="{base}/townships/{tw["slug"]}/">{esc(tw["short"])}</a> <small>{len(tw["wells"]):,}</small></li>' for tw in mine)
    return f'<section class="panel"><h2>Townships</h2><ul class="plain">{items}</ul></section>'


def legal_link(ctx, well):
    """The well's legal location, linked to its section on the township page when parsed."""
    tw = well.get('township')
    if not tw:
        return esc(well.get('legal'))
    return f'<a href="{ctx["base_path"]}/townships/{tw["slug"]}/#section-{well["section"]}">{esc(well["legal"])}</a>'
