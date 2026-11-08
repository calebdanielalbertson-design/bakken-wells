"""Spacing-unit and order pages for the well index.

The data comes from the pipeline's well-index data file (see README): DMR spacing-unit
footprints, which surveyed wells sit inside each unit, and the order history extracted
from signed NDIC orders (machine-extracted, not human-reviewed). This module attaches that
data to the wells read from the DMR layer and renders:

    /units/<id>/                 one page per spacing unit
    /units/                      counties with unit counts
    /units/by-county/<slug>/     every unit in a county
    /orders/<no>/                one page per order
    /orders/                     years with order counts
    /orders/by-year/<year>/      every order signed that year

and the "Spacing units" panel of a well page.
"""
from __future__ import annotations

import gzip
import html
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

esc = lambda value: html.escape('' if value is None else str(value), quote=True)
slugify = lambda value: re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-')[:80] or 'unknown'

MAIN_SITE = 'https://bakkensubsurface.com'
NDIC_ORDERS = 'https://www.dmr.nd.gov/oilgas/'
EVENT_WORDS = {
    ('spacing', 'established'): 'Established the spacing unit', ('spacing', 'designated'): 'Designated the spacing unit',
    ('spacing', 'affirmed'): 'Affirmed the spacing unit', ('spacing', 'created'): 'Created the spacing unit',
    ('spacing', 'redefined'): 'Redefined the spacing unit', ('spacing', 'amended'): 'Amended the spacing unit',
    ('spacing', 'enlarged'): 'Enlarged the spacing unit', ('spacing', 'reduced'): 'Reduced the spacing unit',
    ('spacing', 'extended'): 'Extended the spacing unit', ('pooling', 'pooled'): 'Pooled all interests in the unit',
    ('termination', None): 'Terminated the spacing unit', ('density', None): 'Set the well density',
    ('zone', None): 'Listed the unit in a pool zone table',
}
TYPE_WORDS = {'SPC': 'Spacing unit', 'DRL': 'Drilling unit'}
UNITS_PER_SITEMAP = 10000
MAP_REACH_MILES = 2.5   # well heads farther than this from the footprint are left off the inline map


def load_data(path):
    """The well-index data file, gzip or plain JSON."""
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as handle:
        data = json.load(handle)
    if data.get('version') != 1 or 'units' not in data or 'orders' not in data:
        raise ValueError(f'{path} is not a version 1 well-index data file')
    return data


def format_date(iso):
    if not iso:
        return None
    try:
        from datetime import datetime
        parsed = datetime.strptime(iso, '%Y-%m-%d')
        return f'{parsed:%b} {parsed.day}, {parsed.year}'
    except ValueError:
        return iso


def event_text(event):
    """One line in plain English for an order event on a unit."""
    words = EVENT_WORDS.get((event.get('event'), event.get('verb'))) or EVENT_WORDS.get((event.get('event'), None))
    if not words:
        words = f"{(event.get('verb') or event.get('event') or 'Acted on').capitalize()} the unit"
    wells = event.get('wells')
    if wells is not None:
        count = f'{wells:g}' if isinstance(wells, (int, float)) else str(wells)
        qualifier = f' ({event["qualifier"]})' if event.get('qualifier') and event['qualifier'] != 'stated' else ''
        words += f', {count} well{"" if count == "1" else "s"} allowed{qualifier}'
    return words


def termination_text(unit):
    """'Terminated by order 35122 (May 12, 2026)' from the unit's events, or None when not terminated."""
    if not (unit.get('allowance') or {}).get('terminated'):
        return None
    event = next((e for e in reversed(unit.get('events') or []) if e.get('event') == 'termination'), None)
    if not event:
        return 'Terminated by a later order'
    when = format_date(event.get('date'))
    return f'Terminated by order {event["order"]}' + (f' ({when})' if when else '')


def unit_title(unit):
    acres = f'{unit["acres"]:,}-acre ' if unit.get('acres') else ''
    kind = TYPE_WORDS.get(unit.get('type'), 'Spacing unit').lower()
    if unit.get('lands'):
        where = unit['lands'][0]
    elif unit.get('dmrOrder'):
        where = f'Footprint from DMR order {unit["dmrOrder"]}'
    else:
        where = f'Footprint {unit["id"].removeprefix("dsu-")[:8]}'
    title = f'{acres}{unit.get("formation") or ""} {kind}'.strip()
    return title[:1].upper() + title[1:], where


def cases_map_link(unit):
    """Deep link into the main site's cases-and-orders map, when the unit's land is a single township."""
    if not unit.get('lands') or len(unit['lands']) != 1 or ';' in unit['lands'][0]:
        return None
    text = unit['lands'][0]
    m = re.fullmatch(r'(T\d+[NS] R\d+[EW]) (sections?) (.+)', text)
    if not m:
        return None
    from urllib.parse import quote_plus
    key = f'{m.group(1)} · {m.group(2)} {m.group(3)}'
    order = (unit.get('allowance') or {}).get('order') or (unit['events'][-1]['order'] if unit.get('events') else None)
    params = (f'order={quote_plus(order)}&' if order else '') + f'unit={quote_plus(key)}&zoom=unit'
    return f'{MAIN_SITE}/cases-and-orders/?{params}#unit-map'


# ----------------------------------------------------------------------------- attach


def attach(wells, data):
    """Link units to well records and wells to units. Returns (units, orders, stats)."""
    by_file = {w['file']: w for w in wells}
    units = []
    stats = Counter()
    for unit in data['units']:
        members = []
        for member in unit.get('wells', []):
            well = by_file.get(member['file'])
            if well is None:
                stats['membersNotInLayer'] += 1
                continue
            members.append({'well': well, 'insideFt': member['insideFt'], 'fraction': member['fraction']})
            well.setdefault('units', []).append(unit)
        unit['members'] = members
        counties = Counter(m['well']['county'] for m in members if m['well']['county'])
        unit['county'] = counties.most_common(1)[0][0] if counties else None
        unit['county_slug'] = slugify(unit['county'] or 'unknown county')
        operators = Counter(m['well']['operator'] for m in members if m['well']['operator'])
        unit['operators'] = operators.most_common(3)
        unit['slug'] = unit['id']
        title, where = unit_title(unit)
        unit['title'] = title
        unit['where'] = where
        units.append(unit)
        stats['units'] += 1
        stats['unitsWithMembers'] += bool(members)
    for well in wells:
        if 'units' in well:
            well['units'].sort(key=lambda u: (-(u.get('acres') or 0), u['id']))
    by_id = {u['id']: u for u in units}
    for unit in units:
        unit['overlap_refs'] = [by_id[o] for o in unit.get('overlaps') or [] if o in by_id]
    orders = data['orders']
    for order in orders:
        order['slug'] = order['order']
        for row in order['units']:
            row['ref'] = by_id.get(row['unit'])
    stats['orders'] = len(orders)
    return units, orders, stats


# ----------------------------------------------------------------------------- map


def unit_svg(unit, base='', width=360, height=260):
    """Inline SVG: the unit outline, the surface locations of its wells, a one-mile bar."""
    rings = unit.get('rings') or []
    if not rings:
        return ''
    ring_lons = [x for ring in rings for x, _ in ring]
    ring_lats = [y for ring in rings for _, y in ring]
    reach = MAP_REACH_MILES / 69.0
    heads = [(m['well']['lon'], m['well']['lat'], m['well']) for m in unit['members'] if m['well'].get('lat') is not None
             and min(ring_lats) - reach <= m['well']['lat'] <= max(ring_lats) + reach
             and min(ring_lons) - reach * 1.5 <= m['well']['lon'] <= max(ring_lons) + reach * 1.5]
    lons = ring_lons + [h[0] for h in heads]
    lats = ring_lats + [h[1] for h in heads]
    lat0 = (min(lats) + max(lats)) / 2
    kx = math.cos(math.radians(lat0))
    span_x = (max(lons) - min(lons)) * kx or 1e-6
    span_y = (max(lats) - min(lats)) or 1e-6
    pad = 18
    scale = min((width - 2 * pad) / span_x, (height - 2 * pad - 14) / span_y)
    ox = pad + ((width - 2 * pad) - span_x * scale) / 2
    oy = pad + ((height - 2 * pad - 14) - span_y * scale) / 2

    def pt(lon, lat):
        return ox + (lon - min(lons)) * kx * scale, oy + (max(lats) - lat) * scale

    paths = ''.join('<path d="M' + ' L'.join(f'{x:.1f} {y:.1f}' for x, y in (pt(*p) for p in ring)) + ' Z"/>' for ring in rings)
    dots = ''
    for lon, lat, well in heads[:40]:
        x, y = pt(lon, lat)
        label = esc(well['display_name'])
        dots += (f'<a href="{base}/wells/{esc(well["slug"])}/"><circle cx="{x:.1f}" cy="{y:.1f}" r="4"><title>{label}</title></circle></a>')
    mile = scale * (1 / 69.0)   # one mile of latitude in px
    bar = f'<g class="bar"><line x1="{pad}" y1="{height - 8}" x2="{pad + mile:.1f}" y2="{height - 8}"/><text x="{pad + mile / 2:.1f}" y="{height - 12}" text-anchor="middle">1 mi</text></g>' if 20 < mile < width else ''
    return (f'<svg class="unitmap" viewBox="0 0 {width} {height}" role="img" aria-label="Outline of the spacing unit with the surface locations of its wells">'
            f'<g class="outline">{paths}</g><g class="heads">{dots}</g>{bar}</svg>')


# ----------------------------------------------------------------------------- pages


def facts_list(pairs):
    return '<dl class="facts">' + ''.join(f'<dt>{label}</dt><dd>{value}</dd>' for label, value in pairs if value not in (None, '')) + '</dl>'


def unit_page(ctx, unit, *, shell, status_text):
    base = ctx['base_path']
    path = f'/units/{unit["slug"]}/'
    county_label = f'{unit["county"]} County' if unit.get('county') else 'North Dakota'
    allowance = unit.get('allowance') or {}
    head = unit.get('headroom') or {}
    events = unit.get('events') or []
    title = f'{unit["title"]} · {unit["where"]}'
    description = (f'{unit["title"]} in {county_label}, North Dakota ({unit["where"]}): {len(unit["members"])} surveyed wells inside, '
                   + (f'{allowance["wells"]:g} wells allowed by NDIC order {allowance["order"]}, ' if allowance.get('wells') is not None else '')
                   + f'{len(events)} order events. Map, wells and order history.')

    badges = [f'<span class="badge neutral">{esc(TYPE_WORDS.get(unit.get("type"), unit.get("type") or "Unit"))}</span>']
    if allowance.get('terminated'):
        badges.append(f'<span class="badge off">{esc(termination_text(unit))}</span>')
    elif allowance.get('wells') is not None:
        badges.append(f'<span class="badge ok">{allowance["wells"]:g} wells allowed · order {esc(allowance["order"])}</span>')
    if allowance.get('mixed'):
        badges.append('<span class="badge off">Orders disagree on the allowance</span>')
    if unit.get('pools'):
        badges.append(f'<span class="badge neutral">{esc(unit["pools"][0])}</span>')

    facts = [('Formation', esc(unit.get('formation'))), ('Acres', f'{unit["acres"]:,}' if unit.get('acres') else None),
             ('Type', esc(TYPE_WORDS.get(unit.get('type'), unit.get('type')))),
             ('Land described', '<br>'.join(esc(l) for l in unit.get('lands', [])) or None),
             ('Pool', ', '.join(esc(p) for p in unit.get('pools', [])) or None),
             ('Zone', ', '.join(esc(z) for z in unit.get('zones', [])) or None),
             ('County', f'<a href="{base}/counties/{unit["county_slug"]}/">{esc(county_label)}</a>' if unit.get('county') else None),
             ('Surveyed wells inside', f'{len(unit["members"]):,}'),
             ('Most wells operated by', esc(head.get('holder')) if head.get('holder') else (esc(unit['operators'][0][0]) if unit.get('operators') else None)),
             ('DMR case / order on the footprint', f'{esc(unit.get("dmrCase"))} / {esc(unit.get("dmrOrder"))}' if unit.get('dmrCase') or unit.get('dmrOrder') else None),
             ('Unit id', f'<code>{esc(unit["id"])}</code>')]
    if head:
        room = head.get('balance')
        facts += [('Wells allowed by the latest linked order', f'{head["allowed"]:g}' if head.get('allowed') is not None else None),
                  ('Wells assigned to this unit', f'{head.get("assignedWells"):,}' if head.get('assignedWells') is not None else None),
                  ('Pending permits inside', f'{head.get("pending"):,}' if head.get('pending') is not None else None),
                  ('Balance under the allowance', (f'{room:+g}' if room else '0') if room is not None else None),
                  ('First linked order', esc(format_date(head.get('firstOrderDate'))) if head.get('firstOrderDate') else None)]
        if head.get('review'):
            facts.append(('Review note', esc(head['review'])))

    wells_rows = ''.join(
        f'<tr><td><a href="{base}/wells/{esc(m["well"]["slug"])}/">{esc(m["well"]["display_name"])}</a></td>'
        f'<td>{esc(m["well"]["operator"] or "")}</td><td>{esc(status_text(m["well"]["status"]))}</td>'
        f'<td class="num">{m["insideFt"]:,} ft<small>{m["fraction"] * 100:.0f}%</small></td></tr>'
        for m in sorted(unit['members'], key=lambda m: (-m['insideFt'], m['well']['display_name'])))
    wells_html = (f'<section class="panel"><h2>Wells inside the unit</h2><table class="units"><thead><tr><th>Well</th><th>Operator</th><th>Status</th>'
                  f'<th class="num">Lateral inside</th></tr></thead><tbody>{wells_rows}</tbody></table>'
                  '<p class="note">Surveyed Bakken-system laterals with at least 660 ft of plan-view length inside the published footprint. '
                  'Geometric membership, not a legal assignment; a lateral can cross several units.</p></section>') if unit['members'] else \
        '<section class="panel"><h2>Wells inside the unit</h2><p class="note">No surveyed lateral has 660 ft or more inside this footprint.</p></section>'

    if events:
        rows = ''.join(
            f'<li><time datetime="{esc(e["date"] or "")}">{esc(format_date(e["date"]) or "undated")}</time>'
            f'<a href="{base}/orders/{esc(e["order"])}/">Order {esc(e["order"])}</a>'
            + (f'<span class="case">Case {esc(e["case"])}</span>' if e.get('case') else '')
            + f'<span class="what">{esc(event_text(e))}</span>'
            + (f'<span class="who">{esc(e["applicant"])}</span>' if e.get('applicant') else '') + '</li>'
            for e in events)
        history_html = (f'<section class="panel"><h2>Order history</h2><ol class="timeline">{rows}</ol>'
                        '<p class="note">Facts extracted from signed NDIC orders whose land description covers this footprint; machine-extracted and not human-reviewed. '
                        'A stated allowance is what that order allowed, not proof that it governs today.</p></section>')
    else:
        history_html = '<section class="panel"><h2>Order history</h2><p class="note">No order in the research snapshot has been matched to this footprint yet.</p></section>'

    overlaps = unit.get('overlap_refs') or []
    overlap_html = ''
    if overlaps:
        items = ''.join(f'<li><a href="{base}/units/{esc(o["slug"])}/">{esc(o["where"])}</a> <small>{esc(o["title"])}</small></li>' for o in overlaps[:30])
        count = f'{len(overlaps)} other units share' if len(overlaps) != 1 else 'One other unit shares'
        overlap_html = (f'<section class="panel"><h2>Overlapping units</h2><p class="note">{count} a surveyed well with this one; '
                        f'overlapping units are not additive.</p><ul class="plain">{items}</ul></section>')

    links = [f'<a class="btn" href="{MAIN_SITE}/viewer/?dataset=full&amp;dsu={esc(unit["id"])}" rel="noopener">Open in the regional 3D viewer<small>Footprint, wells and production playback</small></a>']
    cases = cases_map_link(unit)
    if cases:
        links.append(f'<a class="btn" href="{esc(cases)}" rel="noopener">See on the cases and orders map<small>Historical spacing units by order</small></a>')
    map_html = unit_svg(unit, base)
    body = (
        f'<p class="eyebrow">Spacing unit · {esc(county_label)}</p><h1>{esc(unit["title"])}</h1>'
        f'<p class="lede">{esc(unit["where"])}</p><div class="badges">{"".join(badges)}</div>'
        f'<div class="grid"><section class="panel"><h2>Unit facts</h2>{facts_list(facts)}</section>'
        f'<section class="panel"><h2>Map</h2>{map_html}<p class="note">DMR footprint (NAD83) with the published surface locations of its wells. Laterals run under the unit; pads often sit on a neighbouring section.</p>'
        f'<div class="actions">{"".join(links)}</div></section></div>'
        f'{wells_html}{history_html}{overlap_html}'
        # the comparison block (production of the wells inside, read from their pages on request) needs a feed and members
        + (f'<script type="module" src="{base}/assets/compare.js"></script>' if ctx.get('production') and unit['members'] else '')
    )
    centroid =((unit['bbox'][1] + unit['bbox'][3]) / 2, (unit['bbox'][0] + unit['bbox'][2]) / 2) if unit.get('bbox') else None
    jsonld = [{'@context': 'https://schema.org', '@type': 'Place', 'name': f'{unit["title"]}, {unit["where"]}', 'url': ctx['base_url'] + path,
               'identifier': {'@type': 'PropertyValue', 'propertyID': 'Bakken Subsurface unit id', 'value': unit['id']},
               'address': {'@type': 'PostalAddress', 'addressLocality': county_label, 'addressRegion': 'ND', 'addressCountry': 'US'}}]
    if centroid:
        jsonld[0]['geo'] = {'@type': 'GeoCoordinates', 'latitude': round(centroid[0], 5), 'longitude': round(centroid[1], 5)}
    crumbs = [('Wells', f'{base}/'), ('Spacing units', f'{base}/units/'), (county_label, f'{base}/units/by-county/{unit["county_slug"]}/'), (unit['where'], None)]
    return shell(ctx, title=title, description=description, path=path, body=body, crumbs=crumbs, jsonld=jsonld, current='units')


def order_page(ctx, order, *, shell):
    base = ctx['base_path']
    path = f'/orders/{order["slug"]}/'
    when = format_date(order.get('date'))
    title = f'NDIC order {order["order"]}' + (f' · {when}' if when else '')
    unit_count = len(order['units'])
    description = (f'North Dakota Industrial Commission order {order["order"]}' + (f', signed {when}' if when else '')
                   + (f', applicant {order["applicant"]}' if order.get('applicant') else '')
                   + f': what it did to {unit_count} spacing unit{"s" if unit_count != 1 else ""}' + (f' in the {", ".join(order["pools"])} pool' if order.get('pools') else '') + '.')
    facts = [('Signed', f'<time datetime="{esc(order.get("date") or "")}">{esc(when)}</time>' if when else None),
             ('Case', ', '.join(esc(c) for c in order.get('cases', [])) or None),
             ('Applicant', esc(order.get('applicant')) if order.get('applicant') else None),
             ('Pool', ', '.join(esc(p) for p in order.get('pools', [])) or None),
             ('Units touched', f'{unit_count:,}')]
    rows = ''
    for row in order['units']:
        ref = row.get('ref')
        name = f'<a href="{base}/units/{esc(row["unit"])}/">{esc(ref["title"] if ref else row.get("label") or row["unit"])}</a>'
        land = esc(row.get('land') or (ref['where'] if ref else ''))
        rows += f'<li>{name}<span class="land">{land}</span><span class="what">{esc(event_text(row))}</span></li>'
    body = (
        f'<p class="eyebrow">NDIC hearing order</p><h1>Order {esc(order["order"])}</h1>'
        f'<p class="lede">{esc(order.get("applicant") or "North Dakota Industrial Commission")}' + (f' · signed {esc(when)}' if when else '') + '</p>'
        f'<div class="grid"><section class="panel"><h2>Order facts</h2>{facts_list(facts)}'
        f'<p class="note">The signed document is on file with the <a href="{NDIC_ORDERS}" rel="noopener">NDIC Oil and Gas Division</a> under this order number '
        '(the Division\'s order index is part of its subscription service). This page shows facts extracted from the order\'s operative text by the project\'s order pipeline; '
        'they are machine-extracted and not human-reviewed.</p></section>'
        f'<section class="panel"><h2>What it did</h2><ol class="timeline orders">{rows}</ol></section></div>'
    )
    year = (order.get('date') or '')[:4] or 'undated'
    crumbs = [('Wells', f'{base}/'), ('Orders', f'{base}/orders/'), (year.capitalize(), f'{base}/orders/by-year/{year}/'), (f'Order {order["order"]}', None)]
    return shell(ctx, title=title, description=description, path=path, body=body, crumbs=crumbs, current='orders')


def units_index_pages(ctx, units, counts, *, shell):
    """Returns {path: html} for /units/ and /units/by-county/<slug>/."""
    base = ctx['base_path']
    by_county = defaultdict(list)
    for unit in units:
        by_county[(unit['county'] or 'Unknown county', unit['county_slug'])].append(unit)
    pages = {}
    rows = ''.join(f'<li><a href="{base}/units/by-county/{slug}/">{esc(name)}</a> <small>{len(group):,}</small></li>'
                   for (name, slug), group in sorted(by_county.items(), key=lambda item: (-len(item[1]), item[0])))
    body = (f'<h1>Spacing units</h1><p class="lede">{counts["units"]:,} drilling spacing units published by NDIC, {counts["unitsWithOrders"]:,} of them matched to the hearing orders that created or changed them, '
            f'{counts["unitsWithAllowance"]:,} with a stated well allowance. Each unit has a page with its footprint, wells and order history.</p>'
            f'<ul class="plain">{rows}</ul><p class="note">A unit\'s county is the county of most of its wells; units without surveyed wells are listed under "Unknown county".</p>')
    pages['/units/'] = shell(ctx, title='Spacing units · North Dakota', description=f'{counts["units"]:,} North Dakota drilling spacing units by county, each with footprint, wells inside and NDIC order history.',
                             path='/units/', body=body, crumbs=[('Wells', f'{base}/'), ('Spacing units', None)], current='units')
    for (name, slug), group in by_county.items():
        items = ''.join(
            f'<li><a href="{base}/units/{esc(u["slug"])}/">{esc(u["where"])}</a> <small>{esc(u["title"])}'
            + (f' · {u["allowance"]["wells"]:g} allowed' if (u.get('allowance') or {}).get('wells') is not None else '')
            + f' · {len(u["members"])} wells</small></li>'
            for u in sorted(group, key=lambda u: (u['where'], u['id'])))
        body = (f'<p class="eyebrow">County</p><h1>Spacing units in {esc(name)}</h1><p class="lede">{len(group):,} units.</p><ul class="plain long">{items}</ul>')
        pages[f'/units/by-county/{slug}/'] = shell(ctx, title=f'Spacing units in {name} · North Dakota', description=f'{len(group):,} drilling spacing units in {name}, North Dakota, with wells inside and NDIC order history.',
                                                    path=f'/units/by-county/{slug}/', body=body,
                                                    crumbs=[('Wells', f'{base}/'), ('Spacing units', f'{base}/units/'), (name, None)], current='units')
    return pages


def orders_index_pages(ctx, orders, *, shell):
    base = ctx['base_path']
    by_year = defaultdict(list)
    for order in orders:
        by_year[(order.get('date') or '')[:4] or 'undated'].append(order)
    pages = {}
    rows = ''.join(f'<li><a href="{base}/orders/by-year/{year}/">{esc(year.capitalize())}</a> <small>{len(group):,}</small></li>'
                   for year, group in sorted(by_year.items(), reverse=True))
    body = (f'<h1>NDIC orders</h1><p class="lede">{len(orders):,} hearing orders matched to spacing units, by the year they were signed. '
            f'Each order has a page listing the units it established, designated, pooled, re-sized or terminated.</p><ul class="plain">{rows}</ul>'
            '<p class="note">Only orders whose land description could be matched to a published DMR footprint are listed; the research snapshot does not cover every order the Commission has signed.</p>')
    pages['/orders/'] = shell(ctx, title='NDIC orders by year · North Dakota spacing units', description=f'{len(orders):,} North Dakota Industrial Commission hearing orders matched to spacing units, by year.',
                              path='/orders/', body=body, crumbs=[('Wells', f'{base}/'), ('Orders', None)], current='orders')
    for year, group in by_year.items():
        items = ''.join(
            f'<li><a href="{base}/orders/{esc(o["slug"])}/">Order {esc(o["order"])}</a> <small>{esc(format_date(o.get("date")) or "")}'
            + (f' · {esc(o["applicant"])}' if o.get('applicant') else '') + f' · {len(o["units"])} unit{"s" if len(o["units"]) != 1 else ""}</small></li>'
            for o in sorted(group, key=lambda o: (o.get('date') or '', int(o['order']) if o['order'].isdigit() else 0), reverse=True))
        heading = f'Orders signed in {year}' if year != 'undated' else 'Orders without a signing date'
        body = f'<p class="eyebrow">Orders</p><h1>{esc(heading)}</h1><p class="lede">{len(group):,} orders.</p><ul class="plain long">{items}</ul>'
        pages[f'/orders/by-year/{year}/'] = shell(ctx, title=f'NDIC {heading[0].lower() + heading[1:]}', description=f'{len(group):,} North Dakota Industrial Commission orders ({heading[0].lower() + heading[1:]}), each with the spacing units it affected.',
                                                   path=f'/orders/by-year/{year}/', body=body, crumbs=[('Wells', f'{base}/'), ('Orders', f'{base}/orders/'), (year.capitalize(), None)], current='orders')
    return pages


def well_units_panel(ctx, well):
    """The "Spacing units" section of a well page, or '' when the well sits in none."""
    units = well.get('units') or []
    if not units:
        return ''
    base = ctx['base_path']
    items = ''
    for unit in units:
        member = next((m for m in unit['members'] if m['well'] is well), None)
        allowance = unit.get('allowance') or {}
        bits = [esc(unit['title'])]
        if allowance.get('terminated'):
            bits.append(esc(termination_text(unit)[0].lower() + termination_text(unit)[1:]))
        elif allowance.get('wells') is not None:
            bits.append(f'{allowance["wells"]:g} wells allowed by order {esc(allowance["order"])}')
        bits.append(f'{len(unit["members"])} wells inside')
        if member:
            bits.append(f'this lateral {member["insideFt"]:,} ft inside ({member["fraction"] * 100:.0f}%)')
        # data-acres lets templates/compare.js pick the smallest unit as the default comparison set
        items += (f'<li data-acres="{unit.get("acres") or ""}"><a href="{base}/units/{esc(unit["slug"])}/">{esc(unit["where"])}</a>'
                  f'<small>{" · ".join(bits)}</small></li>')
    return (f'<section class="panel"><h2>Spacing units</h2><ul class="nearby">{items}</ul>'
            '<p class="note">Units whose published footprint holds at least 660 ft of this well\'s surveyed lateral. Allowances are facts extracted from signed NDIC orders, machine-extracted and not human-reviewed.</p></section>')
