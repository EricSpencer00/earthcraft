// Search named places from ./places.json (OpenStreetMap, built by scripts/build_places.py) and
// user-entered WGS84 coordinates. Matching runs in the browser: no third-party geocoder receives
// the user's query.
export function parseCoordinates(query) {
  const match = query.trim().match(/^([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)$/);
  if (!match) return null;
  const latitude = Number(match[1]), longitude = Number(match[2]);
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) throw new Error('Latitude must be −90 to 90; longitude must be −180 to 180.');
  return {name: `${latitude}, ${longitude}`, coordinates: [longitude, latitude]};
}

export const fold = text => text.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

// Index the [name, lon, lat, kind] rows once; kinds come ranked from the build (city first).
export function placeIndex(data) {
  const kinds = [...new Set(data.places.map(p => p[3]))];
  return data.places.map(([name, lon, lat, kind]) => ({name, key: fold(name), coordinates: [lon, lat], kind, rank: kinds.indexOf(kind)}));
}

// Best matches first: exact name, then prefix, then a word prefix, then anywhere; ties by kind, then shorter name.
export function searchPlaces(query, index, limit = 6) {
  const q = fold(query);
  if (q.length < 2) return [];
  const scored = [];
  for (const place of index) {
    const at = place.key.indexOf(q);
    if (at < 0) continue;
    const score = place.key === q ? 0 : at === 0 ? 1 : place.key[at - 1] === ' ' ? 2 : 3;
    scored.push([score, place.rank, place.key.length, place]);
  }
  scored.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
  return scored.slice(0, limit).map(entry => entry[3]);
}
