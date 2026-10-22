/**
 * Deterministic Fruchterman–Reingold layout (pure function, no DOM / no randomness source
 * other than a seeded PRNG derived from the node ids).
 */

export const MAX_LAYOUT_NODES = 500;

export interface LayoutNode {
  id: string;
}

export interface LayoutEdge {
  source: string;
  target: string;
}

export interface LayoutOptions {
  iterations?: number;
  /** Extra salt so "re-layout" can produce a different (still deterministic) arrangement. */
  seed?: number;
}

export type LayoutResult = Record<string, { x: number; y: number }>;

function hashString(value: string): number {
  let h = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function layoutSize(count: number): number {
  return Math.max(600, Math.round(Math.sqrt(Math.max(count, 1)) * 140));
}

export function computeForceLayout(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  options: LayoutOptions = {},
): LayoutResult {
  const ids = Array.from(new Set(nodes.map((n) => n.id))).sort();
  const result: LayoutResult = {};
  if (ids.length === 0) return result;

  const size = layoutSize(ids.length);
  const center = size / 2;
  const laidOut = ids.slice(0, MAX_LAYOUT_NODES);
  const overflow = ids.slice(MAX_LAYOUT_NODES);
  const n = laidOut.length;
  const index = new Map(laidOut.map((id, i) => [id, i] as const));

  if (n === 1) {
    result[laidOut[0]] = { x: center, y: center };
  } else {
    const rand = mulberry32(hashString(`${n}:${laidOut.join("|")}`) ^ ((options.seed ?? 0) >>> 0));
    const xs = new Float64Array(n);
    const ys = new Float64Array(n);
    for (let i = 0; i < n; i += 1) {
      const angle = (2 * Math.PI * i) / n + rand() * 0.5;
      const radius = size * 0.3 * (0.5 + rand() * 0.5);
      xs[i] = center + radius * Math.cos(angle);
      ys[i] = center + radius * Math.sin(angle);
    }

    const pairs: Array<[number, number]> = [];
    const seen = new Set<string>();
    for (const edge of edges) {
      const a = index.get(edge.source);
      const b = index.get(edge.target);
      if (a === undefined || b === undefined || a === b) continue;
      const key = a < b ? `${a}:${b}` : `${b}:${a}`;
      if (seen.has(key)) continue;
      seen.add(key);
      pairs.push([a, b]);
    }

    const iterations = options.iterations ?? (n > 200 ? 120 : 250);
    const k = Math.sqrt((size * size) / n) * 0.8;
    const dx = new Float64Array(n);
    const dy = new Float64Array(n);
    let temperature = size / 8;
    const cooling = temperature / (iterations + 1);

    for (let step = 0; step < iterations; step += 1) {
      dx.fill(0);
      dy.fill(0);
      for (let i = 0; i < n; i += 1) {
        for (let j = i + 1; j < n; j += 1) {
          let ex = xs[i] - xs[j];
          let ey = ys[i] - ys[j];
          let dist = Math.hypot(ex, ey);
          if (dist < 0.01) {
            // Coincident nodes: separate along a fixed, index-derived direction.
            ex = ((i + 1) % 7) - 3 || 1;
            ey = ((j + 1) % 5) - 2 || 1;
            dist = Math.hypot(ex, ey);
          }
          const force = (k * k) / dist;
          const fx = (ex / dist) * force;
          const fy = (ey / dist) * force;
          dx[i] += fx;
          dy[i] += fy;
          dx[j] -= fx;
          dy[j] -= fy;
        }
      }
      for (const [a, b] of pairs) {
        const ex = xs[a] - xs[b];
        const ey = ys[a] - ys[b];
        const dist = Math.max(Math.hypot(ex, ey), 0.01);
        const force = (dist * dist) / k;
        const fx = (ex / dist) * force;
        const fy = (ey / dist) * force;
        dx[a] -= fx;
        dy[a] -= fy;
        dx[b] += fx;
        dy[b] += fy;
      }
      for (let i = 0; i < n; i += 1) {
        // Gentle gravity keeps disconnected components on the canvas.
        dx[i] -= (xs[i] - center) * 0.02 * k;
        dy[i] -= (ys[i] - center) * 0.02 * k;
        const disp = Math.hypot(dx[i], dy[i]);
        if (disp > 0) {
          const limited = Math.min(disp, temperature);
          xs[i] += (dx[i] / disp) * limited;
          ys[i] += (dy[i] / disp) * limited;
        }
        xs[i] = Math.min(size, Math.max(0, xs[i]));
        ys[i] = Math.min(size, Math.max(0, ys[i]));
      }
      temperature -= cooling;
    }

    for (let i = 0; i < n; i += 1) {
      result[laidOut[i]] = { x: round(xs[i]), y: round(ys[i]) };
    }
  }

  // Anything above the cap is parked on an outer ring instead of being simulated.
  overflow.forEach((id, i) => {
    const angle = (2 * Math.PI * i) / Math.max(overflow.length, 1);
    result[id] = {
      x: round(center + size * 0.6 * Math.cos(angle)),
      y: round(center + size * 0.6 * Math.sin(angle)),
    };
  });
  return result;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
