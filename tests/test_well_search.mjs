// Node checks for the fuzzy well search shipped with the well pages (scripts/wells/templates/search.js).
import assert from 'node:assert/strict';
import {createSearch, normalize, digits, formatApi, dice, bigrams, townshipQuery, FIELDS} from '../scripts/wells/templates/search.js';

const index = {
  operators: ['CONTINENTAL RESOURCES, INC.', 'HESS BAKKEN INVESTMENTS II, LLC', 'KRAKEN OPERATING, LLC'],
  counties: ['McKenzie', 'Williams', 'Mountrail'],
  statuses: ['A', 'PA', 'LOC'],
  types: ['OG', 'SWD'],
  statusGloss: {A: 'Active', PA: 'Plugged and abandoned', LOC: 'Permitted location, not yet drilled'},
  typeGloss: {OG: 'Oil and gas'},
  // name, api, file, op, county, status, type, lat, lon, slug
  wells: [
    ['HAWKINSON 1-21H', '3305304567', 21994, 0, 0, 0, 0, 47.81, -103.21, null],
    ['HAWKINSON 2-21H', '3305304568', 21995, 0, 0, 0, 0, 47.81, -103.21, null],
    ['HANSON 1-2H', '3310501234', 31234, 1, 1, 1, 0, 48.1, -103.6, null],
    ['OWAN 3-14H', '3305309999', 29999, 0, 0, 0, 0, 47.9, -103.1, null],
    ['SORENSON FEDERAL 44-7TFH', '3306100001', 40001, 2, 2, 2, 0, null, null, null],
    ['', '', 50000, 2, 2, 0, 1, 48.0, -102.0, 'file-50000'],
    ['ROLFSRUD 12-1H', '3305300002', 33333, 0, 0, 0, 0, 47.7, -103.3, '33-053-00002-33333'],
  ],
};
assert.equal(FIELDS.length, index.wells[0].length);
const engine = createSearch(index);
const names = q => engine.search(q).rows.map(r => r.well.name || `file-${r.well.file}`);

assert.equal(normalize('  Hawkinson  1-21H '), 'HAWKINSON 1 21H');
assert.equal(digits('33-053-04567'), '3305304567');
assert.equal(formatApi('33053045670000'), '33-053-04567-0000');
assert.equal(formatApi('3305304567'), '33-053-04567');
assert.ok(dice(bigrams('HAWKINSON'), bigrams('HAWKENSON')) > 0.6);

// Exact name first, sibling second.
assert.deepEqual(names('hawkinson 1-21h').slice(0, 2), ['HAWKINSON 1-21H', 'HAWKINSON 2-21H']);
// Typo still finds it and ranks the real Hawkinsons above Hanson.
assert.deepEqual(names('HAWKENSON').slice(0, 2).sort(), ['HAWKINSON 1-21H', 'HAWKINSON 2-21H']);
// File number and API in any punctuation.
assert.deepEqual(names('21994'), ['HAWKINSON 1-21H']);
assert.deepEqual(names('33-053-04567'), ['HAWKINSON 1-21H']);
assert.deepEqual(names('3305304567'), ['HAWKINSON 1-21H']);
assert.equal(names('33-053').length, 4);                 // API prefix matches every McKenzie well with an API
// Operator and county words narrow the list; partial lease names work.
assert.deepEqual(names('kraken').sort(), ['SORENSON FEDERAL 44-7TFH', 'file-50000']);
assert.deepEqual(names('sorenson federal'), ['SORENSON FEDERAL 44-7TFH']);
assert.deepEqual(names('owan'), ['OWAN 3-14H']);
assert.deepEqual(names('mountrail').sort(), ['SORENSON FEDERAL 44-7TFH', 'file-50000']);
// Nothing matches gibberish; empty queries return nothing.
assert.equal(engine.search('zzqx').total, 0);
assert.equal(engine.search('   ').total, 0);
// Slugs: derived from the API when null, kept when explicit.
const bySlug = Object.fromEntries(engine.records.map(r => [r.file, r.slug]));
assert.equal(bySlug[21994], '33-053-04567');
assert.equal(bySlug[50000], 'file-50000');
assert.equal(bySlug[33333], '33-053-00002-33333');
// Status and type codes resolve through the index arrays.
const hanson = engine.records.find(r => r.file === 31234);
assert.equal(hanson.status, 'PA');
assert.equal(hanson.type, 'OG');
assert.equal(hanson.countyName, 'Williams');

// Township shortcuts: several spellings, optional section, never from plain digits or unknown townships.
const townships = ['153-91', '152-104'];
assert.deepEqual(townshipQuery('153-91', townships), {township: 153, range: 91, section: null, slug: '153n-91w'});
assert.deepEqual(townshipQuery('T153N R91W', townships), {township: 153, range: 91, section: null, slug: '153n-91w'});
assert.deepEqual(townshipQuery('153n 91w', townships), {township: 153, range: 91, section: null, slug: '153n-91w'});
assert.deepEqual(townshipQuery('sec 28 t153n r91w', townships), {township: 153, range: 91, section: 28, slug: '153n-91w'});
assert.deepEqual(townshipQuery('Section 10, 152-104', townships), {township: 152, range: 104, section: 10, slug: '152n-104w'});
assert.equal(townshipQuery('15391', townships), null);          // a file number, not a township
assert.equal(townshipQuery('33-053-04567', townships), null);   // an API
assert.equal(townshipQuery('154-96', townships), null);         // not in the index
assert.equal(townshipQuery('hawkinson 1-21h', townships), null);
assert.equal(townshipQuery('153-91 sec 40', townships).section, null);

console.log('well search: all checks passed');
