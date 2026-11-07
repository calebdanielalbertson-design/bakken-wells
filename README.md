# Bakken Subsurface: North Dakota well pages

One page for every well record the North Dakota Industrial Commission publishes, built from the
public DMR GIS download and republished automatically. It answers the questions the 3D viewers at
[bakkensubsurface.com](https://bakkensubsurface.com) cannot answer quickly: what is this well, who
operates it, where is it, how do I get there.

| URL | What it is |
|---|---|
| `/` | Fuzzy search over every well (name, API, NDIC file number, operator, county); a township typed as `153-91`, `T153N R91W` or `sec 28 153-91` links to its page; `?q=` prefills |
| `/wells/33-053-04567/` | One well: operator, status, type, spud, depth, legal location, map directions, nearby wells, every published attribute, links into the 3D viewers. Wells without an API use `/wells/file-<fileno>/` |
| `/operators/<slug>/` | Every operator name NDIC publishes, wells grouped by county |
| `/counties/<slug>/` | Operators, most recent spuds and permitted-not-drilled locations in a county |
| `/units/<id>/` | One drilling spacing unit: footprint map with its well heads, acreage, formation, land described, the surveyed wells inside it, the NDIC orders that created or changed it, the latest stated well allowance and headroom, overlapping units; links into the 3D viewer and the cases-and-orders map |
| `/units/`, `/units/by-county/<slug>/` | Units by county (the county of most of a unit's wells) |
| `/orders/<no>/` | One NDIC hearing order: date, case, applicant, pool, and what it did to each unit it touched |
| `/orders/`, `/orders/by-year/<year>/` | Orders by the year they were signed |
| `/townships/<T>n-<R>w/` | One PLSS township (for example `/townships/153n-91w/`): a schematic 6 x 6 section grid with every well head placed in the quarter-quarter its legal location names, the wells and spacing units section by section (`#section-28` anchors), the orders on those units, the operators active there |
| `/townships/` | Townships with wells or units, by county |
| `/sitemap.xml` | Sitemap index: `sitemap-pages.xml`, `sitemap-townships.xml`, `sitemap-wells-N.xml` and `sitemap-units-N.xml` (10,000 URLs each) |
| `/wells/index.json` | The compact search index |
| `/build.json` | Counts, field mapping and warnings for the last build |

Each well page carries a canonical URL, description, Open Graph tags, `schema.org/Place` JSON-LD
with coordinates and a breadcrumb list, so a search for a well name or API number can land on it
directly.

## Why this is its own repository

The main site is published as a static upload with a 250 MiB ceiling, almost all of it viewer
data, so tens of thousands of small pages cannot live there. GitHub Pages has no such limit at
this volume and deploys straight from the workflow. GitHub Pages is only free on public
repositories, so the generator lives here, in the open, and the rest of the project's code stays
in its private repositories. Nothing here is secret: the input is a public download and the output
is a public website.

## How it is built

```
scripts/wells/fetch_dmr.py        downloads the archives in scripts/wells/sources.json (needs network)
scripts/wells/shapefile.py        standard-library reader for the DBF and SHP point layer inside each zip
scripts/wells/build_well_pages.py turns the layer into the static site described above
scripts/wells/units.py            spacing-unit and order pages from the pipeline's data file (--data)
scripts/wells/changed.py          tells a scheduled run whether anything changed since the live build
scripts/wells/fixture.py          synthetic OGD_Wells-shaped archives and a small data file for tests and previews
scripts/wells/codes.json          plain-language readings of NDIC status and well-type codes (edit freely)
scripts/wells/templates/          well.css, search.js (fuzzy search), page.js
.github/workflows/well-pages.yml  tests, weekly build and deploy to GitHub Pages
```

The workflow runs every day at 10:27 UTC (DMR regenerates the wells layer each morning at about
09:15 UTC), on demand from the Actions tab, and whenever the generator changes on `main`. Each
run tests the code, lists the archives linked from the DMR download page, downloads
`OGD_Wells.zip` (and the optional permit-status layer), fetches the spacing-unit data, builds
into `work/wells-site/`, writes a Markdown report to the job summary (record counts, which DBF
columns were recognised, status codes seen, warnings) and deploys. Pull requests build and
report without deploying.

A scheduled run first compares what it downloaded with the live site's `build.json`
(`scripts/wells/changed.py`): the SHA-256 of each DMR archive, the generation time of the unit
data and the generator commit. When all three match, the run stops without building or
deploying, so a day on which DMR published nothing new leaves the site, and its sitemap dates,
untouched. Anything it cannot compare counts as a change. Sitemap `lastmod` is the date DMR
last regenerated the layer (the archive's `Last-Modified`), not the day the pages were built;
`build.json` records both, plus the generator commit.

The November 2026 layer holds 44,048 wells across 1,591 operator names and 53 counties, every one
with coordinates and an API number. Columns and how they are used:

| DMR column | Used as |
|---|---|
| `fileno`, `api`, `api_no` | NDIC file number, 14-digit API (the URL uses the first ten digits), DMR's own `33-053-04567-00-00` formatting for display |
| `well_name`, `operator`, `status`, `well_type`, `county`, `field_name` | the headline facts; status and type codes get a plain-language reading from `codes.json` |
| `spud_date`, `td` | spud date, total depth |
| `latitude`, `longitude` | map links, nearby wells, JSON-LD (the point geometry is the fallback) |
| `qq`, `sec`, `twp`, `rng`, `feet_ns`, `fnsl`, `feet_ew`, `fewl` | legal location (`NWNW Sec 28 T153N R91W`) and footage calls (`250 ft FSL · 250 ft FWL`) |
| `symbol` | not used; shown under "Other published attributes" |

Codes without a reading in `codes.json` are shown as published; add readings there as they are
confirmed against NDIC's documentation. Columns the alias table does not recognise still appear
on each page, so a schema change at DMR degrades gracefully; the job summary lists them under
"Not mapped".

Coordinates come from the DBF latitude and longitude columns when present and plausible for North
Dakota, otherwise from the point geometry when the `.prj` is geographic (NAD83). Nearby wells are
the eight closest published well heads within three miles.

## Spacing units and orders

The unit and order pages are built from one file, `well-index-data.json.gz`, that the project's
private pipeline prepares (`explorer/prepare_well_index_data.py` there) from:

- the DMR drilling-spacing-unit footprints (public GIS download), simplified for a small map;
- which surveyed Bakken-system laterals have at least 660 ft of plan-view length inside each
  footprint (geometric membership, not a legal assignment; a lateral can cross several units);
- the order history of each footprint: order and case numbers, dates, applicants, pools, the land
  described and the well counts allowed, extracted from the operative text of signed NDIC orders by
  the pipeline's order tooling. These facts are machine-extracted and not human-reviewed, and every
  page says so. No order document is copied; each order page points to the NDIC Oil and Gas
  Division by order number.

Nothing from the viewer's estimated depth zones or the saved inventory model is in the file.

The workflow downloads the file from this repository's `well-index-data` release
(`gh release download well-index-data --pattern well-index-data.json.gz`) and passes it to the
builder with `--data`. If the asset is missing the build still succeeds, without unit or order
pages. To publish a new data snapshot from the pipeline checkout:

```
python explorer/prepare_well_index_data.py --out work/well-index-data.json.gz
gh release upload well-index-data work/well-index-data.json.gz --clobber --repo calebdanielalbertson-design/bakken-wells
gh release edit well-index-data --notes "Data generated $(date -u +%F) from the DSU snapshot of ..." --repo calebdanielalbertson-design/bakken-wells
gh workflow run well-pages.yml --repo calebdanielalbertson-design/bakken-wells
```

The first time, create the release instead: `gh release create well-index-data work/well-index-data.json.gz --title "Well index data" --notes "..."`.
The file records its own generation time and the hashes of its inputs; `build.json` repeats them
under `data`, and the unit and order sitemaps use the generation date as `lastmod`.

## Townships

Every well's published legal location ("NWNW Sec 28 T153N R91W") and every spacing unit's land
description ("T153N R91W sections 21, 28") already name a PLSS township, so the index has a page
per township with wells or units, no extra data needed. The page draws the township as a
schematic grid: 36 equal squares in standard PLSS numbering (section 1 in the north-east corner,
snaking west and east), each well head placed in the quarter-quarter its legal location names
(a lot or an unparsed description sits at the section centre and is drawn hollow), and sections
that lie in a spacing unit shaded. Below the grid: the wells and units of each section, the
units whose land description names the township, the orders on those units and the operators
active there. Well pages link their legal location to the section anchor; county pages list
their townships; the search box recognises a township typed in any of the usual spellings.
Township and range are north and west throughout North Dakota, so `153n-91w` is unambiguous.
The grid is not a map: it says nothing about where a lateral goes, only where its surface
location is recorded.

## Production

Each well page carries a "Production" panel: monthly oil, gas and water as an inline chart (one
series shown at a time), the last twelve months as a table, totals over the covered months, the
first and last reported month, and a CSV of every month built in the browser from data embedded
in the page. The source is NDIC's free Monthly Production Reports
(<https://www.dmr.nd.gov/oilgas/mprindex.asp>): one Excel workbook per month since May 2015,
listing every well that reported in that month. The pipeline downloads them with full provenance
(`explorer/fetch_monthly_production.py`) and packs them into `well-production.json.gz`
(`explorer/prepare_free_production.py`), published as a second asset of the same
`well-index-data` release:

```
python explorer/fetch_monthly_production.py --cache local-data/monthly-production
python explorer/prepare_free_production.py --cache local-data/monthly-production --out work/well-production.json.gz
gh release upload well-index-data work/well-production.json.gz --clobber --repo calebdanielalbertson-design/bakken-wells
gh workflow run well-pages.yml --repo calebdanielalbertson-design/bakken-wells
```

Rules the panel keeps: a reported zero is a zero; a month the report left NULL (confidential
wells), a month without a row and a negative cell are unknown and shown as "—", never as zero;
totals count known months only and say when nothing is known; two pools reported in one month are
summed. The values are as first reported: operators amend past months later and the Division does
not update the published workbooks, so a month can differ from the current record, and every
panel says so. Wells matched to the feed by NDIC file number, or by API when the file number is
not in the layer. A well absent from the feed gets a short note, not a chart. Months before
May 2015 are not covered yet (the Division publishes them as PDF only). `build.json` records the
feed's file, generation time, month range and match counts under `production`, and the daily
change check treats a new feed as a reason to rebuild.

## Running locally

```
python scripts/wells/fixture.py --out work/dmr-fixture --wells 1500         # no network needed
python scripts/wells/build_well_pages.py --source work/dmr-fixture --out work/wells-site --base-url http://127.0.0.1:8765
python -m http.server 8765 --directory work/wells-site
```

With network access, replace the first line with `python scripts/wells/fetch_dmr.py --out work/dmr`
and point `--source` at `work/dmr`. `--limit 2000` builds a quick subset. `--inspect work/dmr/OGD_Wells.zip`
prints the layer's columns with sample values and how each was mapped. The fixture folder also
holds a four-unit `well-index-data.json.gz`; pass it (or the real file from the release) with
`--data` to build the unit and order pages too.

Tests: `python -m unittest discover -s scripts/tests` (reader, builder, fixtures, nearby wells,
units and orders) and `node tests/test_well_search.mjs` (fuzzy search). Python 3.12 and Node 22;
no packages.

## Custom domain

1. DNS: add a `CNAME` record `wells` pointing at `calebdanielalbertson-design.github.io`.
2. Repository Settings → Pages → Custom domain `wells.bakkensubsurface.com`, then tick
   *Enforce HTTPS* once the certificate is issued.
3. Repository Settings → Secrets and variables → Actions → Variables: `WELLS_BASE_URL` =
   `https://wells.bakkensubsurface.com`. The next build writes canonical URLs, sitemaps and a
   `CNAME` file for the new host.
4. Submit `https://wells.bakkensubsurface.com/sitemap.xml` in Google Search Console.

## Search Console

The GitHub Pages address is a URL-prefix property in Google Search Console, verified by the HTML
tag method: the Actions variable `GOOGLE_SITE_VERIFICATION` holds the `content` value of the
`google-site-verification` meta tag Search Console issued, and the build (`--site-verification`,
or that environment variable) writes the tag into the head of every page and records it in
`build.json`. Leave the variable empty to publish without the tag. A custom domain under
`bakkensubsurface.com` is covered by that domain's Domain property instead, so the tag is not
needed there.

## Deep links into the viewers

Every well page links to `https://bakkensubsurface.com/viewer/?dataset=full&well=<fileno>`; the
regional viewer selects and centres that well on load. The viewer only holds Bakken-system
horizontals with surveys, so for other wells the link opens the viewer with "No matching mapped
wells". The 3D page has no URL state yet, so its link is generic.

## Known limits and what comes next

- Production covers May 2015 onward, as first reported, from the free monthly reports; the PDF
  months from January 2003 to April 2015 and survey summaries are not on the pages yet.
- Unit membership covers surveyed Bakken-system horizontals only, so vertical wells and wells
  without a survey do not appear inside any unit. Order history covers the orders in the research
  snapshot whose land description could be matched to a published footprint, not every order the
  Commission has signed, and the data file is refreshed by hand from the pipeline, not weekly.
- Status and type glosses cover the common NDIC codes only.
- Operator pages group by the operator string as published, so spelling variants of one company
  are separate pages until a grouping table is added.
- New wells appear within a day of DMR adding them to the wells layer; the unit and order data
  refreshes only when a new snapshot is published from the pipeline.
- About 560 MB of HTML before production and roughly 750 MB with it (a well page with a
  production panel is 15 to 20 KB), within GitHub Pages limits but worth watching.

## Data

North Dakota Industrial Commission, Department of Mineral Resources, Oil and Gas Division, public
GIS downloads at <https://gis.dmr.nd.gov/gisdownload.asp> and the public Monthly Production Reports
at <https://www.dmr.nd.gov/oilgas/mprindex.asp>. The state's accuracy disclaimer applies; every
page states the retrieval date and the production panel states its report months. This is an
independent project, not affiliated with NDIC.
