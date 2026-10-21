// Minecraft block -> WGS84 for the Earthcraft frame: a Transverse Mercator on WGS84
// (configs/world-frame.json). Block X = easting - west, block Z = north - northing.
// Inverse series from Snyder, Map Projections: A Working Manual (USGS PP 1395), pp. 63-64;
// tests/frame.mjs checks it against pyproj to a few millimetres across the plan.
const A=6378137,F=1/298.257223563,E2=F*(2-F),EP2=E2/(1-E2);

function meridian(phi){
  const e4=E2*E2,e6=e4*E2;
  return A*((1-E2/4-3*e4/64-5*e6/256)*phi-(3*E2/8+3*e4/32+45*e6/1024)*Math.sin(2*phi)
    +(15*e4/256+45*e6/1024)*Math.sin(4*phi)-(35*e6/3072)*Math.sin(6*phi));
}

// projection: {lat_0, lon_0, k, west, north} as published in generated_world.projection.
export function frameInverse(projection){
  const {lat_0,lon_0,k=1,west,north}=projection,rad=Math.PI/180;
  const m0=meridian(lat_0*rad),e1=(1-Math.sqrt(1-E2))/(1+Math.sqrt(1-E2));
  const e4=E2*E2,e6=e4*E2,mu0=A*(1-E2/4-3*e4/64-5*e6/256);
  return (x,z)=>{
    const easting=west+x,northing=north-z;
    const mu=(m0+northing/k)/mu0;
    const phi1=mu+(3*e1/2-27*e1**3/32)*Math.sin(2*mu)+(21*e1**2/16-55*e1**4/32)*Math.sin(4*mu)
      +(151*e1**3/96)*Math.sin(6*mu)+(1097*e1**4/512)*Math.sin(8*mu);
    const sin=Math.sin(phi1),cos=Math.cos(phi1),tan=Math.tan(phi1);
    const c1=EP2*cos*cos,t1=tan*tan,n1=A/Math.sqrt(1-E2*sin*sin),r1=A*(1-E2)/Math.pow(1-E2*sin*sin,1.5),d=easting/(n1*k);
    const lat=phi1-(n1*tan/r1)*(d*d/2-(5+3*t1+10*c1-4*c1*c1-9*EP2)*d**4/24+(61+90*t1+298*c1+45*t1*t1-252*EP2-3*c1*c1)*d**6/720);
    const lon=lon_0*rad+(d-(1+2*t1+c1)*d**3/6+(5-2*c1+28*t1-3*c1*c1+8*EP2+24*t1*t1)*d**5/120)/cos;
    return [lon/rad,lat/rad];
  };
}
