// Node checks for the production panel script (scripts/wells/templates/production.js): data decoding, the
// chart series drawn in the browser, and the CSV. Parity with the Python renderer is checked from
// scripts/tests/test_production.py, which runs this module through node.
import assert from 'node:assert/strict';
import {decode, seriesGroup, buildCsv, CSV_SERIES, GEOMETRY} from '../scripts/wells/templates/production.js';

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

// CSV: one row per month across the year end, blank cells for unknown values, a source comment at the end.
const csv = buildCsv(data).split('\n');
assert.equal(csv[0], 'month,oil_bbl,water_bbl,gas_mcf,gas_sold_mcf,flared_mcf,days');
assert.equal(csv[1], '2024-11,10,1,4,7,,31');
assert.equal(csv[2], '2024-12,,2,,8,,0');
assert.equal(csv[3], '2025-01,30,3,6,9,,31');
assert.ok(csv[4].startsWith('# Source: NDIC Monthly Production Reports'));
assert.equal(csv[5], '');
assert.equal(CSV_SERIES.length, 6);

// A series group: grid with three ticks, unit label, a path broken at the unknown month.
const gas = seriesGroup([100, null, 50, 0, 25], 'gas', 5);
assert.ok(gas.startsWith('<g class="s-gas"><g class="grid"><line x1="52" y1="198" x2="630" y2="198"/><text x="46" y="202">0</text>'));
assert.ok(gas.includes('<text x="46" y="14">100</text>'));                 // top tick is the maximum
assert.ok(gas.includes('<text x="46" y="108">50</text>'));
assert.ok(gas.includes('<text class="unit" x="60" y="22">Gas, mcf per month</text>'));
const path = gas.match(/<path d="([^"]*)"/)[1];
assert.equal((path.match(/M/g) || []).length, 2);                         // one gap, two sub-paths
assert.equal((path.match(/L/g) || []).length, 2);
assert.ok(path.startsWith('M52,10 '));                                      // the maximum sits on the top grid line
assert.ok(gas.endsWith('<title>Gas, mcf per month</title></path></g>'));
assert.ok(seriesGroup([5000, 12345], 'oil').includes('>12,345<'));          // thousands separators as in Python
assert.ok(seriesGroup([null, null], 'water').includes(`d="M${GEOMETRY.left},198"`));   // nothing known: a baseline
assert.ok(seriesGroup([7], 'oil', 1).includes('M341,10'));                   // a single month sits mid-plot

console.log('production: all checks passed');
