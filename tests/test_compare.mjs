// Node checks for the comparison module (scripts/wells/templates/compare.js): per-well figures, the median,
// and the multi-line chart. Page parsing and the block itself need a browser; they are checked by hand on a build.
import assert from 'node:assert/strict';
import {wellStats, median, linesSvg, GEO} from '../scripts/wells/templates/compare.js';

const well = (slug, oil, {lateral = 10000, days = 30, start = '2020-01'} = {}) => ({
  slug, name: slug.toUpperCase(), href: `/wells/${slug}/`, operator: 'OP', spudYear: 2019, lateral,
  data: {start, oil, water: oil.map((x) => (x == null ? null : x / 4)), gas: oil.map((x) => (x == null ? null : x * 3)), gasSold: null, flared: null,
         days: oil.map((x) => (x == null ? null : days))},
});

// Figures: cumulative and first-12 count known months only, rates need days, ratios need both sides, per 1,000 ft uses the lateral.
const s = wellStats(well('a', [3000, 2000, null, 1500, 1200]));
assert.equal(s.cum, 7700);
assert.equal(s.first12, 7700);
assert.equal(s.first12Known, 4);                      // four known of five months; fewer than twelve, and the table says so
assert.equal(s.peak, 100);
assert.equal(s.latest, 40);
assert.equal(s.last12, 7700);
assert.equal(s.gor12, 3000);
assert.equal(+s.wcut12.toFixed(2), 20);
assert.equal(s.cumPer, 770);
assert.equal(s.first12Per, 770);
assert.deepEqual(s.series.cumOil, [3000, 5000, 5000, 6500, 7700]);   // an unknown month holds the cumulative flat (a lower bound)
assert.deepEqual(s.series.rateOil, [100, 200 / 3, null, 50, 40]);
assert.equal(s.months, 5);
assert.equal(s.first, '2020-01');
const blank = wellStats(well('b', [null, null]));
assert.equal(blank.cum, null);
assert.equal(blank.peak, null);
assert.deepEqual(blank.series.cumOil, [null, null]);  // nothing known: no cumulative, not zero
const noLateral = wellStats(well('c', [10], {lateral: null}));
assert.equal(noLateral.cumPer, null);
const noData = wellStats({slug: 'd', name: 'D', href: '/wells/d/', data: null});
assert.equal(noData.series, null);
assert.equal(noData.cum, null);

// Median: at least three known values, else nothing.
assert.equal(median([1, 5, 3]), 3);
assert.equal(median([4, 1, 3, 2]), 2.5);
assert.equal(median([1, null, 3]), null);
assert.equal(median([]), null);

// The chart: siblings first, the median dashed, this well on top; a months-on-production axis or a calendar axis.
const series = [{key: 'a', values: [10, 8, null, 5], me: true}, {key: 'b', values: [6, 5, 4, 3]}, {key: 'median', values: [8, 6.5, 4, 4], median: true}];
const r = linesSvg(series, {n: 4, start: null, log: false, ariaLabel: 'test'});
assert.ok(r.svg.startsWith(`<svg class="prodchart cmp" viewBox="0 0 ${GEO.W} ${GEO.TOP + GEO.PLOT + GEO.AXIS}" role="img" aria-label="test">`));
const order = ['data-key="b"', 'data-key="median"', 'data-key="a"'].map((k) => r.svg.indexOf(k));
assert.ok(order[0] < order[1] && order[1] < order[2]);                 // draw order: others, median, this well
assert.ok(r.svg.includes('<path class="w me" data-key="a" d="M58,16 L305,'));   // the maximum sits on the top gridline; x steps of 742/3
assert.ok(r.svg.includes('class="w med"'));
assert.ok(r.svg.includes('<text x="58" y="274" text-anchor="middle">0</text>'));   // month numbers along the axis (TOP + PLOT + 18)
assert.equal((r.svg.match(/<path class="w me"[^>]*d="([^"]*)"/)[1].match(/M/g) || []).length, 2);   // broken at the unknown month
assert.ok(r.svg.includes('<text x="50" y="20" text-anchor="end">10</text>'));
const cal = linesSvg([{key: 'a', values: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]}], {n: 13, start: '2023-11', log: false});
assert.ok(cal.svg.includes('>Nov ’23<') && cal.svg.includes('>Nov ’24<') && !cal.svg.includes('months on production'));
const log = linesSvg([{key: 'a', values: [1000, 100, 0, 10]}], {n: 4, start: null, log: true});
assert.ok(log.svg.includes('>1,000<') && log.svg.includes('>10<'));
assert.equal((log.svg.match(/<path class="w"[^>]*d="([^"]*)"/)[1].match(/M/g) || []).length, 2);   // the zero month is left out on a log scale
const empty = linesSvg([{key: 'a', values: [null, null]}], {n: 2, start: null, log: false});
assert.ok(!empty.svg.includes('<path class="w"'));                      // nothing known: no line

console.log('compare: all checks passed');
