/** WGS84 / ENU / 车体系杆臂（与中控 src/solver 一致） */

const A = 6378137.0;
const F = 1 / 298.257223563;
const E2 = F * (2 - F);

export type Lla = { lat: number; lon: number; alt: number };
export type Enu = { e: number; n: number; u: number };
export type Attitude = { roll: number; pitch: number; heading: number };
export type Vec3 = [number, number, number];

export function wgs84ToEcef(lat: number, lon: number, alt: number): Vec3 {
  const sl = Math.sin((lat * Math.PI) / 180);
  const cl = Math.cos((lat * Math.PI) / 180);
  const sn = Math.sin((lon * Math.PI) / 180);
  const cn = Math.cos((lon * Math.PI) / 180);
  const n = A / Math.sqrt(1 - E2 * sl * sl);
  return [(n + alt) * cl * cn, (n + alt) * cl * sn, (n * (1 - E2) + alt) * sl];
}

export function ecefToWgs84(x: number, y: number, z: number): Lla {
  const lon = Math.atan2(y, x);
  const p = Math.hypot(x, y);
  let lat = Math.atan2(z, p * (1 - E2));
  for (let i = 0; i < 8; i++) {
    const sl = Math.sin(lat);
    const n = A / Math.sqrt(1 - E2 * sl * sl);
    const alt = p / Math.cos(lat) - n;
    const lat2 = Math.atan2(z, p * (1 - (E2 * n) / (n + alt)));
    if (Math.abs(lat2 - lat) < 1e-12) {
      lat = lat2;
      return { lat: (lat * 180) / Math.PI, lon: (lon * 180) / Math.PI, alt };
    }
    lat = lat2;
  }
  const sl = Math.sin(lat);
  const n = A / Math.sqrt(1 - E2 * sl * sl);
  return {
    lat: (lat * 180) / Math.PI,
    lon: (lon * 180) / Math.PI,
    alt: p / Math.cos(lat) - n,
  };
}

/** ECEF 点相对参考 LLA 的 ENU */
export function ecefToEnu(
  x: number,
  y: number,
  z: number,
  refLat: number,
  refLon: number,
  refAlt: number,
): Enu {
  const [x0, y0, z0] = wgs84ToEcef(refLat, refLon, refAlt);
  const dx = x - x0;
  const dy = y - y0;
  const dz = z - z0;
  const sl = Math.sin((refLat * Math.PI) / 180);
  const cl = Math.cos((refLat * Math.PI) / 180);
  const sn = Math.sin((refLon * Math.PI) / 180);
  const cn = Math.cos((refLon * Math.PI) / 180);
  return {
    e: -sn * dx + cn * dy,
    n: -sl * cn * dx - sl * sn * dy + cl * dz,
    u: cl * cn * dx + cl * sn * dy + sl * dz,
  };
}

export function enuToEcef(
  e: number,
  n: number,
  u: number,
  refLat: number,
  refLon: number,
  refAlt: number,
): Vec3 {
  const [x0, y0, z0] = wgs84ToEcef(refLat, refLon, refAlt);
  const sl = Math.sin((refLat * Math.PI) / 180);
  const cl = Math.cos((refLat * Math.PI) / 180);
  const sn = Math.sin((refLon * Math.PI) / 180);
  const cn = Math.cos((refLon * Math.PI) / 180);
  const dx = -sn * e - sl * cn * n + cl * cn * u;
  const dy = cn * e - sl * sn * n + cl * sn * u;
  const dz = cl * n + sl * u;
  return [x0 + dx, y0 + dy, z0 + dz];
}

/** 车体 (X前 Y左 Z上) → ENU；航向北 0 顺时针（NMEA） */
export function bodyToEnuRotation(rollDeg: number, pitchDeg: number, headingDeg: number): number[][] {
  const phi = (rollDeg * Math.PI) / 180;
  const theta = (pitchDeg * Math.PI) / 180;
  const psi = (headingDeg * Math.PI) / 180;
  const cx = Math.cos(phi);
  const sx = Math.sin(phi);
  const cy = Math.cos(theta);
  const sy = Math.sin(theta);
  const cz = Math.cos(psi);
  const sz = Math.sin(psi);
  const Rx = [
    [1, 0, 0],
    [0, cx, -sx],
    [0, sx, cx],
  ];
  const Ry = [
    [cy, 0, -sy],
    [0, 1, 0],
    [sy, 0, cy],
  ];
  // 航向 0：X→北，Y→西
  const Rz = [
    [sz, -cz, 0],
    [cz, sz, 0],
    [0, 0, 1],
  ];
  return matMul(Rz, matMul(Ry, Rx));
}

function matMul(a: number[][], b: number[][]): number[][] {
  const out = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      out[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j];
    }
  }
  return out;
}

function matVec(m: number[][], v: Vec3): Vec3 {
  return [
    m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
  ];
}

function matT(m: number[][]): number[][] {
  return [
    [m[0][0], m[1][0], m[2][0]],
    [m[0][1], m[1][1], m[2][1]],
    [m[0][2], m[1][2], m[2][2]],
  ];
}

export function antennaMinusTireEnu(ant: Lla, tire: Lla): Enu {
  const [ax, ay, az] = wgs84ToEcef(ant.lat, ant.lon, ant.alt);
  return ecefToEnu(ax, ay, az, tire.lat, tire.lon, tire.alt);
}

/** L_arm = Rᵀ · (P_ant − P_tire)，车体系米 */
export function computeLArm(ant: Lla, tire: Lla, att: Attitude): Vec3 {
  const delta = antennaMinusTireEnu(ant, tire);
  const r = bodyToEnuRotation(att.roll, att.pitch, att.heading);
  return matVec(matT(r), [delta.e, delta.n, delta.u]);
}

/** P_tire = P_ant − R · L_arm */
export function projectTire(ant: Lla, arm: Vec3, att: Attitude): Lla {
  const r = bodyToEnuRotation(att.roll, att.pitch, att.heading);
  const offset = matVec(r, arm);
  const [ex, ey, ez] = enuToEcef(-offset[0], -offset[1], -offset[2], ant.lat, ant.lon, ant.alt);
  return ecefToWgs84(ex, ey, ez);
}

export function tireOffsetEnu(pred: Lla, measured: Lla): Enu {
  const [px, py, pz] = wgs84ToEcef(pred.lat, pred.lon, pred.alt);
  return ecefToEnu(px, py, pz, measured.lat, measured.lon, measured.alt);
}

export function hypot2(a: number, b: number): number {
  return Math.hypot(a, b);
}

export function hypot3(a: number, b: number, c: number): number {
  return Math.hypot(a, b, c);
}

export function parseLla(lat: string, lon: string, alt: string): Lla | null {
  const a = parseFloat(lat);
  const o = parseFloat(lon);
  const h = parseFloat(alt);
  if (![a, o, h].every(Number.isFinite)) return null;
  return { lat: a, lon: o, alt: h };
}

export function fmtArm(arm: Vec3, digits = 3): string {
  return `[${arm.map((v) => v.toFixed(digits)).join(', ')}]`;
}

export function armDeltaCm(a: Vec3, b: Vec3): { dx: number; dy: number; dz: number; abs: number } {
  const dx = (a[0] - b[0]) * 100;
  const dy = (a[1] - b[1]) * 100;
  const dz = (a[2] - b[2]) * 100;
  return { dx, dy, dz, abs: Math.hypot(dx, dy, dz) };
}
