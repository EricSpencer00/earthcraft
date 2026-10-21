// Block -> WGS84 inverse (dashboard/frame.mjs) against pyproj for the Earthcraft frame,
// from the Water Tower out to 120 km.
import assert from 'node:assert/strict';
import {frameInverse} from '../dashboard/frame.mjs';

// [block x, block z, lon, lat] from pyproj with configs/world-frame.json.
const REFERENCE=[[32, 33, -87.62443, 41.8972], [0, 0, -87.62481561839934, 41.897497105830666], [-67072, 38400, -88.42871029316771, 41.54895005423546], [-56320, -79872, -88.3112387482775, 42.61449896642799], [90000, -90000, -86.52637164908018, 42.70246652272788], [-120000, 60000, -89.0586445712872, 41.34834070231564], [1000, 120000, -87.61295638056826, 40.81700752458575]];
const inverse=frameInverse({lat_0:41.8972,lon_0:-87.62443,k:1,west:-32,north:33});
for(const [x,z,lon,lat] of REFERENCE){
  const [l,a]=inverse(x,z);
  const metres=Math.hypot((l-lon)*111320*Math.cos(lat*Math.PI/180),(a-lat)*110574);
  assert.ok(metres<0.01,`block ${x},${z} is ${metres.toFixed(4)} m from pyproj`);
}
console.log('Frame inverse matches pyproj within 1 cm out to 120 km passed');
