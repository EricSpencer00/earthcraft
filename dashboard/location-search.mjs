// Search only named public anchors and user-entered WGS84 coordinates.
// No third-party geocoder receives the user's query.
export function parseLocation(query, places) {
  const name = query.trim().toLowerCase();
  if (Object.hasOwn(places, name)) return places[name];
  const match = name.match(/^([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)$/);
  if (!match) throw new Error('Enter Chicago, Elmhurst, or latitude, longitude.');
  const latitude = Number(match[1]), longitude = Number(match[2]);
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) throw new Error('Latitude must be −90 to 90; longitude must be −180 to 180.');
  return {name: `${latitude}, ${longitude}`, coordinates: [longitude, latitude]};
}
