# Bakken Subsurface: North Dakota well pages

One page for every well record the North Dakota Industrial Commission publishes, built from the
public DMR GIS download and republished automatically. It answers the questions the 3D viewers at
[bakkensubsurface.com](https://bakkensubsurface.com) cannot answer quickly: what is this well, who
operates it, where is it, how do I get there.

| URL | What it is |
|---|---|
| `/` | Fuzzy search over every well (name, API, NDIC file number, operator, county); `?q=` prefills |
| `/wells/33-053-04567/` | One well: operator, status, type, spud, depth, legal location, map directions, nearby wells, every published attribute, links into the 3D viewers. Wells without an API use `/wells/file-<fileno>/` |
| `/operators/<slug>/` | Every operator name NDIC publishes, wells grouped by county |
| `/counties/<slug>/` | Operators, most recent spuds and permitted-not-drilled locations in a county |
| `/sitemap.xml` | Sitemap index: `sitemap-pages.xml` and `sitemap-wells-N.xml` (10,000 URLs each) |
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
scripts/wells/fixture.py          synthetic OGD_Wells-shaped archives for tests and previews
scripts/wells/codes.json          plain-language readings of NDIC status and well-type codes (edit freely)
scripts/wells/templates/          well.css, search.js (fuzzy search), page.js
.github/workflows/well-pages.yml  tests, weekly build and deploy to GitHub Pages
```

The workflow runs every Tuesday, on demand from the Actions tab, and whenever the generator
changes on `main`. Each run tests the code, lists the archives linked from the DMR download page,
downloads `OGD_Wells.zip` (and the optional permit-status layer), builds into `work/wells-site/`,
writes a Markdown report to the job summary (record counts, which DBF columns were recognised,
status codes seen, warnings) and deploys. Pull requests build and report without deploying.

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

## Running locally

```
python scripts/wells/fixture.py --out work/dmr-fixture --wells 1500         # no network needed
python scripts/wells/build_well_pages.py --source work/dmr-fixture --out work/wells-site --base-url http://127.0.0.1:8765
python -m http.server 8765 --directory work/wells-site
```

With network access, replace the first line with `python scripts/wells/fetch_dmr.py --out work/dmr`
and point `--source` at `work/dmr`. `--limit 2000` builds a quick subset. `--inspect work/dmr/OGD_Wells.zip`
prints the layer's columns with sample values and how each was mapped.

Tests: `python -m unittest discover -s scripts/tests` (reader, builder, fixtures, nearby wells)
and `node tests/test_well_search.mjs` (fuzzy search). Python 3.12 and Node 22; no packages.

## Custom domain

1. DNS: add a `CNAME` record `wells` pointing at `calebdanielalbertson-design.github.io`.
2. Repository Settings → Pages → Custom domain `wells.bakkensubsurface.com`, then tick
   *Enforce HTTPS* once the certificate is issued.
3. Repository Settings → Secrets and variables → Actions → Variables: `WELLS_BASE_URL` =
   `https://wells.bakkensubsurface.com`. The next build writes canonical URLs, sitemaps and a
   `CNAME` file for the new host.
4. Submit `https://wells.bakkensubsurface.com/sitemap.xml` in Google Search Console.

## Deep links into the viewers

Every well page links to `https://bakkensubsurface.com/viewer/?dataset=full&well=<fileno>`; the
regional viewer selects and centres that well on load. The viewer only holds Bakken-system
horizontals with surveys, so for other wells the link opens the viewer with "No matching mapped
wells". The 3D page has no URL state yet, so its link is generic.

## Known limits and what comes next

- Production, spacing units, hearing orders and survey summaries are not on the pages yet. They
  are the next step: the project's prepared datasets carry monthly production for 22,000 wells,
  spacing-unit membership and order history, and the workflow can add them once it is given a
  read-only key to the data bucket.
- Status and type glosses cover the common NDIC codes only.
- Operator pages group by the operator string as published, so spelling variants of one company
  are separate pages until a grouping table is added.
- The workflow republishes weekly even when nothing changed.
- About 400 MB of HTML for 44,000 wells (8 to 9 KB a page, about 30 MB compressed in the Pages
  artifact), within GitHub Pages limits.

## Data

North Dakota Industrial Commission, Department of Mineral Resources, Oil and Gas Division, public
GIS downloads at <https://gis.dmr.nd.gov/gisdownload.asp>. The state's accuracy disclaimer applies;
every page states the retrieval date. This is an independent project, not affiliated with NDIC.
