// Generates the primary-button background: a soft mesh gradient with film grain, baked into a PNG.
// No CSS gradients or shaders at runtime: the button just shows this image. Deterministic (seeded noise).
import { deflateSync } from "node:zlib";
import { writeFileSync, mkdirSync } from "node:fs";

function crc32(buf) {
  let c, crc = 0xffffffff;
  for (let n = 0; n < buf.length; n++) {
    c = (crc ^ buf[n]) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(w, h, rgba) {
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w * 4 + 1)] = 0; rgba.copy(raw, y * (w * 4 + 1) + 1, y * w * 4, (y + 1) * w * 4); }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })), chunk("IEND", Buffer.alloc(0))]);
}
function rng(seed) { return () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296); }

function mesh(name, w, h, points, grain, seed) {
  const r = rng(seed), out = Buffer.alloc(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let tw = 0, cr = 0, cg = 0, cb = 0;
    for (const p of points) {
      const dx = x / w - p.x, dy = (y / h - p.y) * (h / w) * 1.6;
      const wgt = 1 / Math.pow(dx * dx + dy * dy + 0.004, 1.6);
      tw += wgt; cr += p.c[0] * wgt; cg += p.c[1] * wgt; cb += p.c[2] * wgt;
    }
    const n = (r() - 0.5) * grain;
    const i = (y * w + x) * 4;
    out[i] = Math.max(0, Math.min(255, cr / tw + n));
    out[i + 1] = Math.max(0, Math.min(255, cg / tw + n));
    out[i + 2] = Math.max(0, Math.min(255, cb / tw + n));
    out[i + 3] = 255;
  }
  mkdirSync("public/textures", { recursive: true });
  writeFileSync(`public/textures/${name}.png`, png(w, h, out));
  console.log(`public/textures/${name}.png`);
}

const primary = [
  { x: 0.05, y: 0.2, c: [124, 92, 255] },  // violet
  { x: 0.45, y: 0.95, c: [64, 120, 255] }, // blue
  { x: 0.7, y: 0.1, c: [236, 112, 214] },  // magenta
  { x: 1.0, y: 0.8, c: [255, 166, 120] },  // warm peach
  { x: 0.3, y: 0.4, c: [92, 72, 230] },
];
mesh("mesh-primary", 640, 160, primary, 26, 7);
mesh("mesh-primary-hover", 640, 160, primary.map((p) => ({ ...p, c: p.c.map((v) => Math.min(255, v + 18)) })), 26, 11);
