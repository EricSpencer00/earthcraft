import assert from 'node:assert/strict';
import {parseLocation} from '../dashboard/location-search.mjs';
const places={chicago:{name:'Chicago',coordinates:[-87.62443,41.8972]}};
assert.equal(parseLocation(' CHICAGO ',places),places.chicago);
assert.deepEqual(parseLocation('41.89, -87.62',places).coordinates,[-87.62,41.89]);
for(const query of ['91, 0','0, -181','NaN, 0','Paris','constructor'])assert.throws(()=>parseLocation(query,places));
console.log('Search parsing: 7 checks passed');
