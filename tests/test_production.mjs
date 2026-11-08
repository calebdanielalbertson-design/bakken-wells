// Node checks for the production panel script (scripts/wells/templates/production.js): data decoding, derived
// series, the chart strip, the Arps fit and the CSV. Parity with the Python renderer (strip_svg, the number
// formats) is checked from scripts/tests/test_production.py, which runs this module through node.
import assert from 'node:assert/strict';
import {decode, derive, stripSvg, fitArps, mh, buildCsv, niceTicks, compact, fixed, fmt, tickLabel, CSV_SERIES, GEO, TOP, DMIN} from '../scripts/wells/templates/production.js';

// Decoding: comma-joined strings with empty fields for unknown months; arrays pass through; absent series are null.
const raw = {start: '2024-11', oil: '10,,30', water: '1,2,3', gas: [4, null, 6], gasSold: '7,8,9', flared: ',,', days: '31,0,31'};
const data = decode(raw);
assert.deepEqual(data.oil, [10, null, 30]);
assert.deepEqual(data.gas, [4, null, 6]);
assert.deepEqual(data.flared, [null, null, null]);
assert.equal(data.start, '2024-11');
assert.equal(decode({start: '2024-01', oil: '5'}).water, null);
assert.deepEqual(decode({start: '2024-01', oil: '0'}).oil, [0]);          // a reported zero is a zero
assert.deepEqual(decode({start: '2024-01', oil: ''}).oil, [null]);        // a single unknown month

// Derived series: rates need producing days, ratios need both sides, cumulatives count known months only.
const d = derive(data);
assert.deepEqual(d.rate.oil.map((v) => v && +v.toFixed(3)), [0.323, null, 0.968]);
assert.deepEqual(d.rate.water, [1 / 31, null, 3 / 31]);                   // December has no producing days: no rate
assert.deepEqual(d.cum.oil, [10, 10, 40]);
assert.deepEqual(d.gor, [400, null, 200]);
assert.deepEqual(d.wcut.map((v) => v && +v.toFixed(2)), [9.09, null, 9.09]);   // December's oil is unknown: no water cut
assert.deepEqual(d.uptime.map((v) => v && +v.toFixed(1)), [103.3, 0, 100]);   // 31 reported days in a 30-day November: as reported
assert.deepEqual(d.gor3, [400, 400, 250]);                                   // trailing three months: (4+6)*1000/(10+30)
assert.equal(derive({start: '2024-01', oil: [1, 2]}).gor[0], null);          // no gas series at all

// CSV: one row per month across the year end, blank cells for unknown values, derived columns, a source comment.
const csv = buildCsv(data).split('\n');
assert.equal(csv[0], 'month,oil_bbl,water_bbl,gas_mcf,gas_sold_mcf,flared_mcf,days,oil_bbl_per_day,gor_scf_per_bbl,water_cut_pct');
assert.equal(csv[1], '2024-11,10,1,4,7,,31,0.3,400,9.1');
assert.equal(csv[2], '2024-12,,2,,8,,0,,,');
assert.equal(csv[3], '2025-01,30,3,6,9,,31,1.0,200,9.1');
assert.ok(csv[4].startsWith('# Source: NDIC Monthly Production Reports'));
assert.equal(csv[5], '');
assert.equal(CSV_SERIES.length, 6);

// Numbers: Python's formats (ties to even), compact figures, round ticks.
assert.deepEqual([2.5, 3.5, 4.4999].map(fmt), ['2', '4', '4']);
assert.deepEqual([[12.25, 1], [0.125, 2], [1.005, 2]].map(([a, b]) => fixed(a, b)), ['12.2', '0.12', '1.00']);
assert.deepEqual([null, 0, 9999, 12250, 123456, 1500000, 1840000].map(compact), ['—', '0', '9,999', '12.2K', '123K', '1.5M', '1.84M']);
assert.deepEqual(niceTicks(1005), [0, 500, 1000, 1500]);
assert.deepEqual(niceTicks(7), [0, 2.5, 5, 7.5]);
assert.deepEqual(niceTicks(0), [0, 1]);
assert.equal(tickLabel(15000, [0, 5000, 10000, 15000]), '15K');
assert.equal(tickLabel(2.5, [0, 2.5, 5, 7.5]), '2.5');

// A strip: grid with round ticks, a band over the unknown month, the title, a path broken at the gap, an end marker.
const gas = stripSvg(GEO.main, TOP, 'Gas', 'mcf/d', 'gas', [100, null, 50, 0, 25], 0, 5, true, '2025-11', 5).svg;
assert.ok(gas.startsWith('<g class="grid"><line x1="58" x2="746" y1="134" y2="134"/><text x="50" y="138" text-anchor="end">0</text>'));
assert.ok(gas.includes('<text x="50" y="26" text-anchor="end">100</text>'));             // top tick at the plot top (22 + 4)
assert.equal((gas.match(/<rect class="band"/g) || []).length, 1);
assert.ok(gas.includes('<text class="t" x="58" y="14">Gas<tspan> mcf/d</tspan></text>'));
const path = gas.match(/<path class="s gas" d="([^"]*)"/)[1];
assert.equal((path.match(/M/g) || []).length, 2);                                        // one gap, two sub-paths
assert.ok(path.startsWith('M58,22 '));                                                    // the maximum sits on the top gridline
assert.ok(gas.includes('<circle class="end gas" cx="746" cy="106" r="4"/><text class="end" x="755" y="110">25</text>'));
assert.ok(gas.includes('<g class="axis">'));
assert.ok(gas.includes('>Nov ’25<') && gas.includes('>Mar ’26<'));
assert.ok(stripSvg(GEO.main, TOP, 'Oil', 'bbl/d', 'oil', [null, null], 0, 2, false, '2024-01', 2).svg.includes('d="M58,134"'));   // nothing known: a baseline
assert.ok(!stripSvg(GEO.main, TOP, 'Oil', 'bbl/d', 'oil', [1], 0, 1, false, '2024-01', 1).svg.includes('<g class="axis">'));
// log scale: decade ticks, zero months left out of the line
const log = stripSvg(GEO.main, TOP, 'Oil', 'bbl/d', 'oil', [1000, 100, 0, 10], 0, 4, false, '2024-01', 4, {log: true}).svg;
assert.ok(log.includes('>1,000<') && log.includes('>100<') && log.includes('>10<') && !log.includes('>1<'));   // decades from the smallest positive value
assert.equal((log.match(/<path class="s oil" d="([^"]*)"/)[1].match(/M/g) || []).length, 2);
// fit overlay: a dashed forecast beyond the last month and a dotted model over the history
const withFit = stripSvg(GEO.main, TOP, 'Oil', 'bbl/d', 'oil', [100, 80, 60], 0, 5, true, '2024-01', 3, {extra: {hist: [95, 78, 62], fit: [null, null, 62, 50, 40], forecastFrom: 3}}).svg;
assert.ok(withFit.includes('<path class="hist oil" d="M58,'));
assert.ok(withFit.includes('<path class="fit oil" d="M402,'));
assert.ok(!withFit.includes('<text class="end"'));                                        // the end label yields to the forecast
assert.ok(withFit.includes('>May ’24<'));                                             // the axis runs on through the forecast

// Arps: an exact exponential decline is recovered (b at its floor, Di within a hair), a short history is refused.
const months = 60, qi = 9000, di = 0.05;
const oil = Array.from({length: months}, (_, t) => qi * Math.exp(-di * t));
const days = Array(months).fill(30);
const fit = fitArps(oil, days);
assert.ok(fit && fit.nFit === 60 && fit.p === 0);
assert.ok(Math.abs(fit.di - di) < 0.01, `di ${fit.di}`);       // b is floored at 0.05, so a pure exponential comes back a shade steeper
assert.ok(Math.abs(fit.qi - qi) / qi < 0.05, `qi ${fit.qi}`);
assert.ok(fit.b < 0.15, `b ${fit.b}`);
assert.ok(Math.abs(fit.scale - 1) < 0.02);
assert.ok(fit.rmseLog < 0.05, `rmse ${fit.rmseLog}`);
assert.ok(fit.forecast.length > 0 && fit.forecast[0] < oil[months - 1] && fit.forecast[fit.forecast.length - 1] >= 150);
assert.ok(Math.abs(fit.deAnnual - (1 - Math.exp(-12 * di))) < 0.03);
const hyper = Array.from({length: 84}, (_, t) => 12000 / (1 + 1.0 * 0.15 * t));         // b = 1, Di = 0.15 per month
const hfit = fitArps(hyper, Array(84).fill(30));
assert.ok(hfit && hfit.b > 0.7 && Math.abs(hfit.di - 0.15) < 0.04, `b ${hfit.b} di ${hfit.di}`);
assert.equal(fitArps(oil.slice(0, 10), days.slice(0, 10)), null);
assert.equal(fitArps(oil.slice(0, 30), Array(30).fill(10)), null);                       // no full producing months: no fit
const late = fitArps(oil, days, 40);                                                      // a window starting at month 40
assert.ok(late && late.p === 40 && late.nFit === 20);
assert.ok(mh(0, 100, 0.1, 0.5) === 100 && mh(1000, 100, 0.1, 0.5) < 1 && mh(5, 100, DMIN / 2, 0.5) < 100);

console.log('production: all checks passed');
