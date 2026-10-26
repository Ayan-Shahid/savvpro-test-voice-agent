"use client";
import { useEffect, useRef } from "react";

const VERT = `attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;
// Procedural spiral galaxy + star field. Cheap: one full-screen pass at reduced resolution.
const FRAG = `
precision mediump float;
uniform vec2 r; uniform float t;
float h(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float n(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);
  return mix(mix(h(i),h(i+vec2(1,0)),f.x), mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x), f.y); }
float fbm(vec2 p){ float v=0.0, a=0.5; for(int i=0;i<5;i++){ v+=a*n(p); p*=2.03; a*=0.5; } return v; }
void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5*r) / min(r.x, r.y);
  uv -= vec2(0.35, 0.12);
  float rad = length(uv), ang = atan(uv.y, uv.x);
  float arms = sin(2.0*ang - 6.5*log(rad + 0.05) + t*0.03);
  float disk = exp(-rad*2.4) * (0.55 + 0.45*arms) * fbm(uv*3.0 + t*0.01);
  float core = exp(-rad*14.0);
  vec3 col = vec3(0.02, 0.025, 0.06);
  col += disk * mix(vec3(0.35, 0.28, 0.85), vec3(0.95, 0.55, 0.85), clamp(rad*1.6, 0.0, 1.0)) * 0.9;
  col += core * vec3(1.0, 0.85, 0.7);
  vec2 g = gl_FragCoord.xy / 1.7;
  float s = h(floor(g)); float tw = 0.6 + 0.4*sin(t*1.5 + s*40.0);
  col += step(0.9975, s) * tw * vec3(0.9, 0.92, 1.0);
  col += fbm(uv*1.2 - t*0.004) * vec3(0.03, 0.05, 0.1);
  gl_FragColor = vec4(col, 1.0);
}`;

export function GalaxyBackground() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const gl = canvas?.getContext("webgl", { antialias: false, powerPreference: "low-power" });
    if (!canvas || !gl) return;
    const sh = (type: number, src: string) => { const s = gl.createShader(type)!; gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const prog = gl.createProgram()!;
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog); gl.useProgram(prog);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const uR = gl.getUniformLocation(prog, "r"), uT = gl.getUniformLocation(prog, "t");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motion === "off";
    const scale = 0.5;
    const resize = () => {
      canvas.width = Math.floor(innerWidth * scale); canvas.height = Math.floor(innerHeight * scale);
      gl.viewport(0, 0, canvas.width, canvas.height);
    };
    resize(); addEventListener("resize", resize);
    let raf = 0; const t0 = performance.now();
    const draw = () => {
      gl.uniform2f(uR, canvas.width, canvas.height); gl.uniform1f(uT, (performance.now() - t0) / 1000);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (!reduce && !document.hidden) raf = requestAnimationFrame(draw);
    };
    draw();
    const vis = () => { if (!document.hidden && !reduce) { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); } };
    document.addEventListener("visibilitychange", vis);
    return () => { cancelAnimationFrame(raf); removeEventListener("resize", resize); document.removeEventListener("visibilitychange", vis); };
  }, []);
  return <canvas ref={ref} aria-hidden className="fixed inset-0 -z-10 h-full w-full" />;
}
