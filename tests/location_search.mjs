import assert from 'node:assert/strict';
import {parseCoordinates,placeIndex,searchPlaces} from '../dashboard/location-search.mjs';
assert.deepEqual(parseCoordinates('41.89, -87.62').coordinates,[-87.62,41.89]);
assert.equal(parseCoordinates('Paris'),null);
for(const query of ['91, 0','0, -181'])assert.throws(()=>parseCoordinates(query));
const index=placeIndex({places:[
  ['Chicago',-87.62,41.88,'city'],['Loyola Park',-87.66,42.01,'park'],['Loyola',-87.661,42.001,'station'],
  ['Loyola University Chicago',-87.658,41.999,'university'],['Lake Shore Park',-87.62,41.89,'park'],['Café Brauer',-87.63,41.92,'landmark']]});
assert.equal(searchPlaces('loyola university chicago',index)[0].name,'Loyola University Chicago');
assert.equal(searchPlaces('Loyola',index)[0].name,'Loyola');                    // exact name first
assert.equal(searchPlaces('loyola u',index)[0].name,'Loyola University Chicago');
assert.equal(searchPlaces('shore',index)[0].name,'Lake Shore Park');            // word prefix
assert.equal(searchPlaces('cafe brauer',index)[0].name,'Café Brauer');           // accents fold
assert.deepEqual(searchPlaces('x',index),[]);
assert.deepEqual(searchPlaces('constructor',index),[]);
console.log('location search passed');
