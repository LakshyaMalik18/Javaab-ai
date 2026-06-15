"use client";
// Code-built animated hero: a luminous golden DATA WAVE in Three.js, viewed
// FRONT-ON. A wide, dense sheet of glowing points forms a flowing current whose
// crests travel horizontally across the hero (like the front of an ocean swell),
// with depth front-to-back. Motion is driven by a clamped delta-accumulator so it
// glides continuously and never teleports after an off-screen pause. Faint
// flow-aligned streak lines (no triangulated web) add soft trailing; crests bloom
// gold via additive blending while troughs fall into the black. The left fades to
// black behind the headline. Capped point count + rAF + paused off-screen for
// 60fps; prefers-reduced-motion freezes it to a still frame.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { usePrefersReducedMotion, useIsMobile } from "@/lib/useReducedMotion";

// compact 2D simplex noise (Stefan Gustavson, public domain — seeded + inlined)
function makeNoise2D(seed = 1) {
  const p = new Uint8Array(256);
  for (let i = 0; i < 256; i++) p[i] = i;
  let s = (seed * 2147483647) | 0 || 1;
  for (let i = 255; i > 0; i--) {
    s = (s * 16807) % 2147483647;
    const j = Math.abs(s) % (i + 1);
    const t = p[i];
    p[i] = p[j];
    p[j] = t;
  }
  const perm = new Uint8Array(512);
  for (let i = 0; i < 512; i++) perm[i] = p[i & 255];
  const grad = [
    [1, 1], [-1, 1], [1, -1], [-1, -1],
    [1, 0], [-1, 0], [0, 1], [0, -1],
  ];
  const F2 = 0.5 * (Math.sqrt(3) - 1);
  const G2 = (3 - Math.sqrt(3)) / 6;
  return function (xin: number, yin: number) {
    const sk = (xin + yin) * F2;
    const i = Math.floor(xin + sk);
    const j = Math.floor(yin + sk);
    const t = (i + j) * G2;
    const x0 = xin - (i - t);
    const y0 = yin - (j - t);
    const i1 = x0 > y0 ? 1 : 0;
    const j1 = x0 > y0 ? 0 : 1;
    const x1 = x0 - i1 + G2;
    const y1 = y0 - j1 + G2;
    const x2 = x0 - 1 + 2 * G2;
    const y2 = y0 - 1 + 2 * G2;
    const ii = i & 255;
    const jj = j & 255;
    let n0 = 0, n1 = 0, n2 = 0;
    let t0 = 0.5 - x0 * x0 - y0 * y0;
    if (t0 >= 0) {
      const g = grad[perm[ii + perm[jj]] & 7];
      t0 *= t0;
      n0 = t0 * t0 * (g[0] * x0 + g[1] * y0);
    }
    let t1 = 0.5 - x1 * x1 - y1 * y1;
    if (t1 >= 0) {
      const g = grad[perm[ii + i1 + perm[jj + j1]] & 7];
      t1 *= t1;
      n1 = t1 * t1 * (g[0] * x1 + g[1] * y1);
    }
    let t2 = 0.5 - x2 * x2 - y2 * y2;
    if (t2 >= 0) {
      const g = grad[perm[ii + 1 + perm[jj + 1]] & 7];
      t2 *= t2;
      n2 = t2 * t2 * (g[0] * x2 + g[1] * y2);
    }
    return 70 * (n0 + n1 + n2); // ~[-1, 1]
  };
}

const GOLD = new THREE.Color("#F0C04A");
const GOLD_HOT = new THREE.Color("#FFD884");
const DIM = new THREE.Color("#1c1a14"); // warm near-black for troughs

// soft round glow sprite so additive points bloom into luminous gold
function makeGlowTexture() {
  const s = 64;
  const c = document.createElement("canvas");
  c.width = c.height = s;
  const ctx = c.getContext("2d")!;
  const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.3, "rgba(255,216,132,0.85)");
  g.addColorStop(0.6, "rgba(240,192,74,0.35)");
  g.addColorStop(1, "rgba(240,192,74,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, s, s);
  const tex = new THREE.CanvasTexture(c);
  tex.needsUpdate = true;
  return tex;
}

export default function NodeNetwork() {
  const mountRef = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();
  const mobile = useIsMobile();

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    // ── grid: wide & dense, COLS = horizontal flow direction (capped for 60fps) ──
    const COLS = mobile ? 130 : 208; // across the screen (the current's length)
    const ROWS = mobile ? 34 : 56; // depth, front-to-back (more rows → rises higher)
    const SX = 0.29; // tight x spacing → richer continuous band, reaches further left/right
    const SZ = 0.46; // z spacing (depth)
    const AMP = 1.6; // wave height (vertical undulation, seen front-on)
    const NF = 0.2; // noise spatial frequency
    const SPEED = 0.16; // noise phase units / sec — slow, silky horizontal drift
    const Z_NEAR = 5;
    const W = () => mount.clientWidth;
    const H = () => mount.clientHeight;
    const N = COLS * ROWS;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, W() / H(), 0.1, 160);
    // front-on, just above the sheet → see the wave's face with depth behind it.
    // look a touch higher so the current rises up and wraps around the headline.
    camera.position.set(0, 3.1, 15);
    camera.lookAt(0, 0.95, -3);

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    renderer.setSize(W(), H());
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mount.appendChild(renderer.domElement);

    const group = new THREE.Group();
    // mostly front-on, a hair of tilt so the depth reads; nudged up so the swell
    // reaches higher around the type
    group.position.set(0, -0.3, 0);
    group.rotation.x = -0.1;
    scene.add(group);

    const noise = makeNoise2D(7);
    const glow = makeGlowTexture();

    // base grid coords (x, z) — y is animated
    const baseX = new Float32Array(N);
    const baseZ = new Float32Array(N);
    const depthFade = new Float32Array(N);
    for (let j = 0; j < ROWS; j++) {
      for (let i = 0; i < COLS; i++) {
        const k = j * COLS + i;
        baseX[k] = (i - (COLS - 1) / 2) * SX;
        baseZ[k] = Z_NEAR - j * SZ;
        depthFade[k] = 1 - j / (ROWS - 1); // 1 near → 0 far
      }
    }

    // ── points ──
    const pos = new Float32Array(N * 3);
    const colr = new Float32Array(N * 3);
    const pGeo = new THREE.BufferGeometry();
    pGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    pGeo.setAttribute("color", new THREE.BufferAttribute(colr, 3));
    const pMat = new THREE.PointsMaterial({
      size: mobile ? 0.38 : 0.3,
      map: glow,
      vertexColors: true,
      transparent: true,
      opacity: 1,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: true,
    });
    const points = new THREE.Points(pGeo, pMat);
    group.add(points);

    // ── streak lines: ONLY along the flow (horizontal) → soft trailing current,
    //    not a triangulated overhead web ──
    const pairs: number[] = [];
    for (let j = 0; j < ROWS; j++) {
      for (let i = 0; i < COLS - 1; i++) {
        const k = j * COLS + i;
        pairs.push(k, k + 1);
      }
    }
    const segCount = pairs.length / 2;
    const lPos = new Float32Array(segCount * 2 * 3);
    const lCol = new Float32Array(segCount * 2 * 3);
    const lGeo = new THREE.BufferGeometry();
    lGeo.setAttribute("position", new THREE.BufferAttribute(lPos, 3));
    lGeo.setAttribute("color", new THREE.BufferAttribute(lCol, 3));
    const lMat = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.4,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const lines = new THREE.LineSegments(lGeo, lMat);
    group.add(lines);

    const pPos = pGeo.getAttribute("position") as THREE.BufferAttribute;
    const pCol = pGeo.getAttribute("color") as THREE.BufferAttribute;
    const lPosA = lGeo.getAttribute("position") as THREE.BufferAttribute;
    const lColA = lGeo.getAttribute("color") as THREE.BufferAttribute;

    const tmp = new THREE.Color();
    const heights = new Float32Array(N);

    function computeFrame(elapsed: number) {
      // crests travel along X → the swell sweeps horizontally across the hero.
      // `elapsed` is the clamped accumulator, so the phase advances in tiny,
      // even per-frame steps → silky, never steppy.
      const phase = elapsed * SPEED;
      const drift = elapsed * SPEED * 0.35; // gentle depth drift for life
      for (let k = 0; k < N; k++) {
        const x = baseX[k];
        const z = baseZ[k];
        const nx = x * NF - phase;
        const nz = z * NF - drift;
        const h =
          noise(nx, nz) * AMP +
          noise(nx * 2.1 - phase * 0.5, nz * 2.1 + 9) * AMP * 0.35;
        heights[k] = h;
        const yN = (h / (AMP * 1.4) + 1) * 0.5; // ~0..1
        const crest = Math.max(0, (yN - 0.5) / 0.5); // sparse gold on crests
        const fade = 0.3 + depthFade[k] * 0.7;
        tmp.copy(DIM).lerp(GOLD, crest);
        if (crest > 0.7) tmp.lerp(GOLD_HOT, (crest - 0.7) / 0.3);
        tmp.multiplyScalar(fade * (0.5 + crest * 1.7)); // bright crests
        pPos.setXYZ(k, x, h, z);
        pCol.setXYZ(k, tmp.r, tmp.g, tmp.b);
      }
      pPos.needsUpdate = true;
      pCol.needsUpdate = true;

      for (let sIdx = 0; sIdx < segCount; sIdx++) {
        const a = pairs[sIdx * 2];
        const b = pairs[sIdx * 2 + 1];
        const o = sIdx * 6;
        lPos[o] = baseX[a];
        lPos[o + 1] = heights[a];
        lPos[o + 2] = baseZ[a];
        lPos[o + 3] = baseX[b];
        lPos[o + 4] = heights[b];
        lPos[o + 5] = baseZ[b];
        for (const [idx, off] of [[a, o], [b, o + 3]] as const) {
          lCol[off] = pCol.getX(idx) * 0.45;
          lCol[off + 1] = pCol.getY(idx) * 0.45;
          lCol[off + 2] = pCol.getZ(idx) * 0.45;
        }
      }
      lPosA.needsUpdate = true;
      lColA.needsUpdate = true;
    }

    // ── understated mouse parallax ──
    const targetM = { x: 0, y: 0 };
    const curM = { x: 0, y: 0 };
    const onMove = (e: PointerEvent) => {
      const r = mount.getBoundingClientRect();
      targetM.x = ((e.clientX - r.left) / r.width - 0.5) * 2;
      targetM.y = ((e.clientY - r.top) / r.height - 0.5) * 2;
    };
    window.addEventListener("pointermove", onMove);

    let raf = 0;
    let running = true;
    let elapsed = 0; // accumulates ONLY while visible → no jump after a pause
    let last = performance.now();

    function render() {
      curM.x += (targetM.x - curM.x) * 0.03;
      curM.y += (targetM.y - curM.y) * 0.03;
      group.rotation.y = curM.x * 0.08;
      group.rotation.x = -0.1 - curM.y * 0.03;
      renderer.render(scene, camera);
    }

    function loop(now: number) {
      raf = requestAnimationFrame(loop);
      const dt = Math.min((now - last) / 1000, 0.05); // clamp hitches
      last = now;
      if (!running) return;
      elapsed += dt; // tiny, even increments → smooth glide
      computeFrame(elapsed);
      render();
    }

    const onResize = () => {
      camera.aspect = W() / H();
      camera.updateProjectionMatrix();
      renderer.setSize(W(), H());
    };
    window.addEventListener("resize", onResize);

    const io = new IntersectionObserver(
      ([entry]) => {
        running = entry.isIntersecting && !document.hidden;
      },
      { threshold: 0.01 },
    );
    io.observe(mount);
    const onVis = () => {
      running = !document.hidden;
    };
    document.addEventListener("visibilitychange", onVis);

    if (reduced) {
      computeFrame(0); // a single composed still — no animation loop
      render();
    } else {
      raf = requestAnimationFrame(loop);
    }

    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVis);
      renderer.dispose();
      pGeo.dispose();
      lGeo.dispose();
      pMat.dispose();
      lMat.dispose();
      glow.dispose();
      if (renderer.domElement.parentNode === mount)
        mount.removeChild(renderer.domElement);
    };
  }, [reduced, mobile]);

  return (
    <div
      ref={mountRef}
      className="absolute inset-0 h-full w-full"
      aria-hidden
      style={{ contain: "strict" }}
    />
  );
}
