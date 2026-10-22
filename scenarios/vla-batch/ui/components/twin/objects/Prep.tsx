"use client";

import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import type { Group, Mesh } from "three";
import type { Vec3 } from "@/lib/twin/layout";
import type { TwinPrepStation } from "@/lib/twin/useTwinData";
import { FLAVOUR_WORD, PREP_STATE_WORD, flavourKey, prepColorKey } from "@/lib/twin/lines";
import { useTwin } from "../context";
import { Carton } from "./Equipment";
import { Pickable } from "./Pickable";

/**
 * Voorbereiding: twee weegstations en twee premixtanks, gedeeld door L1 en
 * L2. Kleur van het display en de band volgt de state van de set (gepland,
 * afwegen, premix, gereed); de inhoud draagt de smaak van het recept.
 */

const RVS = { metalness: 0.85, roughness: 0.22 } as const;
const GEBORSTELD = { metalness: 0.75, roughness: 0.38 } as const;

function WeighStation({ st }: { st: TwinPrepStation }) {
  const { palette } = useTwin();
  const state = palette[prepColorKey(st.view.state)];
  const fill = st.view.flavour ? palette[flavourKey(st.view.flavour)] : palette.steel;
  return (
    <>
      {/* vloerweegschaal */}
      <mesh position={[0, 0.08, 0]} receiveShadow castShadow>
        <boxGeometry args={[1.3, 0.12, 1.3]} />
        <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
      </mesh>
      {/* afweegbak met de componenten in de kleur van het recept */}
      <mesh position={[0, 0.55, 0]} castShadow>
        <boxGeometry args={[0.9, 0.8, 0.9]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      {st.view.set && st.view.state !== "verbruikt" && (
        <mesh position={[0, 0.96, 0]}>
          <boxGeometry args={[0.8, 0.04, 0.8]} />
          <meshStandardMaterial color={fill} roughness={0.7} />
        </mesh>
      )}
      {/* weegindicator op een paal */}
      <mesh position={[0.75, 0.75, -0.55]}>
        <boxGeometry args={[0.06, 1.5, 0.06]} />
        <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
      </mesh>
      <mesh position={[0.75, 1.55, -0.55]}>
        <boxGeometry args={[0.42, 0.3, 0.1]} />
        <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
      </mesh>
      <mesh position={[0.75, 1.55, -0.49]}>
        <planeGeometry args={[0.34, 0.2]} />
        <meshStandardMaterial color={state} emissive={state} emissiveIntensity={0.9} />
      </mesh>
    </>
  );
}

function PremixTank({ st }: { st: TwinPrepStation }) {
  const { palette, reducedMotion } = useTwin();
  const state = palette[prepColorKey(st.view.state)];
  const fill = st.view.flavour ? palette[flavourKey(st.view.flavour)] : palette.tank;
  const stirrer = useRef<Group>(null);
  const mixing = st.view.state === "premix";
  useFrame((_, dt) => {
    if (mixing && !reducedMotion && stirrer.current) stirrer.current.rotation.y += dt * 2.4;
  });
  return (
    <>
      {[0, 1, 2, 3].map((i) => {
        const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
        return (
          <mesh key={i} position={[Math.cos(a) * 0.55, 0.4, Math.sin(a) * 0.55]}>
            <cylinderGeometry args={[0.05, 0.05, 0.8, 8]} />
            <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
          </mesh>
        );
      })}
      <mesh position={[0, 1.45, 0]} castShadow>
        <cylinderGeometry args={[0.75, 0.75, 1.3, 28]} />
        <meshStandardMaterial color={palette.tank} {...RVS} />
      </mesh>
      <mesh position={[0, 0.8, 0]} scale={[1, 0.4, 1]}>
        <sphereGeometry args={[0.75, 24, 10, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2]} />
        <meshStandardMaterial color={palette.tank} {...RVS} />
      </mesh>
      {/* statusband rond de mantel */}
      <mesh position={[0, 1.95, 0]}>
        <cylinderGeometry args={[0.77, 0.77, 0.12, 28]} />
        <meshStandardMaterial color={state} emissive={state} emissiveIntensity={0.5} />
      </mesh>
      {/* kijkglas met de premix in de kleur van de smaak */}
      {st.view.set && st.view.state !== "verbruikt" && (
        <mesh position={[0, 1.3, 0.74]}>
          <boxGeometry args={[0.22, 0.6, 0.04]} />
          <meshStandardMaterial color={fill} emissive={fill} emissiveIntensity={0.25} />
        </mesh>
      )}
      <mesh position={[0, 2.15, 0]}>
        <cylinderGeometry args={[0.75, 0.75, 0.08, 28]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      <group ref={stirrer} position={[0, 2.45, 0]}>
        <mesh>
          <cylinderGeometry args={[0.2, 0.2, 0.5, 16]} />
          <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
        </mesh>
        <mesh position={[0.22, 0, 0]}>
          <boxGeometry args={[0.18, 0.18, 0.18]} />
          <meshStandardMaterial color={palette.accent} roughness={0.5} />
        </mesh>
      </group>
    </>
  );
}

/** Altijd zichtbaar chipje: state · product · lijn. */
function PrepChip({ st, height }: { st: TwinPrepStation; height: number }) {
  const set = st.view.set;
  const text = set
    ? `${PREP_STATE_WORD[st.view.state]} · ${st.view.flavour ? FLAVOUR_WORD[st.view.flavour] : set.recipe_id} · ${set.line}`
    : st.visual.label;
  const dot = `var(--twin-${prepColorKey(st.view.state)})`;
  return (
    <Html position={[0, height, 0]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
      <div className="twin-chip" data-prep={st.view.state} data-twin-prep={st.id} style={{ ["--prep-dot" as string]: dot }}>
        {text}
      </div>
    </Html>
  );
}

export function PrepStationObject({ st }: { st: TwinPrepStation }) {
  const label = st.kind === "weigh" ? 2.4 : 3.2;
  // De stations staan 2-3 m uit elkaar; om-en-om hoger zodat de chips elkaar niet bedekken.
  const stagger = st.id.endsWith("02") ? 1.1 : 0;
  return (
    <Pickable id={`prep:${st.id}`} name={st.name} visual={st.visual} position={st.pos} radius={1} labelHeight={label}>
      {st.kind === "weigh" ? <WeighStation st={st} /> : <PremixTank st={st} />}
      <PrepChip st={st} height={label - 0.55 + stagger} />
    </Pickable>
  );
}

/* ---------------- leidingen en banden ---------------- */

function segments(points: Vec3[]) {
  const out: Array<{ a: Vec3; b: Vec3; len: number; mid: Vec3; axis: "x" | "y" | "z" }> = [];
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const len = Math.hypot(d[0], d[1], d[2]);
    if (len < 1e-3) continue;
    const axis = Math.abs(d[0]) >= Math.abs(d[1]) && Math.abs(d[0]) >= Math.abs(d[2]) ? "x" : Math.abs(d[1]) >= Math.abs(d[2]) ? "y" : "z";
    out.push({ a, b, len, mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2], axis });
  }
  return out;
}

function pointAt(segs: ReturnType<typeof segments>, dist: number): Vec3 {
  let d = dist;
  for (const s of segs) {
    if (d <= s.len) {
      const t = d / s.len;
      return [s.a[0] + (s.b[0] - s.a[0]) * t, s.a[1] + (s.b[1] - s.a[1]) * t, s.a[2] + (s.b[2] - s.a[2]) * t];
    }
    d -= s.len;
  }
  const last = segs[segs.length - 1];
  return last ? last.b : [0, 0, 0];
}

const ROT: Record<"x" | "y" | "z", Vec3> = { x: [0, 0, Math.PI / 2], y: [0, 0, 0], z: [Math.PI / 2, 0, 0] };

/** Orthogonale RVS-leiding langs punten; een gloeiende puls loopt mee als er product stroomt. */
export function PolyPipe({ points, flowing }: { points: Vec3[]; flowing: boolean }) {
  const { palette, reducedMotion } = useTwin();
  const segs = useMemo(() => segments(points), [points]);
  const total = segs.reduce((n, s) => n + s.len, 0);
  const pulse = useRef<Mesh>(null);
  useFrame(({ clock }) => {
    if (!pulse.current) return;
    pulse.current.visible = flowing;
    if (!reducedMotion && total > 0) pulse.current.position.set(...pointAt(segs, (clock.elapsedTime * 2.5) % total));
  });
  return (
    <group>
      {segs.map((s, i) => (
        <mesh key={i} position={s.mid} rotation={ROT[s.axis]} castShadow>
          <cylinderGeometry args={[0.08, 0.08, s.len + 0.08, 12]} />
          <meshStandardMaterial color={palette.pipe} {...RVS} />
        </mesh>
      ))}
      {points.slice(1, -1).map((p, i) => (
        <mesh key={`b${i}`} position={p}>
          <sphereGeometry args={[0.1, 12, 8]} />
          <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
        </mesh>
      ))}
      <mesh ref={pulse} position={points[0]}>
        <sphereGeometry args={[0.14, 10, 8]} />
        <meshStandardMaterial color={palette.glow} emissive={palette.glow} emissiveIntensity={1.5} />
      </mesh>
    </group>
  );
}

/** Transportband langs punten (op bandhoogte), met pakken in de smaakkleur die alleen bewegen als de lijn vult. */
export function Conveyor({ points, color, running, packs = 10 }: { points: Vec3[]; color: string; running: boolean; packs?: number }) {
  const { palette, reducedMotion } = useTwin();
  const segs = useMemo(() => segments(points), [points]);
  const total = segs.reduce((n, s) => n + s.len, 0);
  const group = useRef<Group>(null);
  const offset = useRef(0);
  useFrame((_, dt) => {
    if (!group.current || total <= 0) return;
    if (running && !reducedMotion) offset.current = (offset.current + dt * 1.1) % total;
    group.current.children.forEach((c, i) => {
      const p = pointAt(segs, (offset.current + (i / packs) * total) % total);
      c.position.set(p[0], p[1] + 0.04, p[2]);
    });
  });
  return (
    <group>
      {segs.map((s, i) => (
        <group key={i}>
          <mesh position={s.mid} receiveShadow>
            <boxGeometry args={s.axis === "x" ? [s.len + 0.5, 0.08, 0.5] : [0.5, 0.08, s.len + 0.5]} />
            <meshStandardMaterial color={palette.road} roughness={0.8} />
          </mesh>
          {/* poten om de 1,6 m */}
          {Array.from({ length: Math.max(2, Math.round(s.len / 1.6)) },           (_, k) => k).map((k, _i, arr) => {
                      const t = k / (arr.length - 1);
            const x = s.a[0] + (s.b[0] - s.a[0]) * t;
            const z = s.a[2] + (s.b[2] - s.a[2]) * t;
            return (
              <mesh key={k} position={[x, s.a[1] / 2, z]}>
                <boxGeometry args={[0.05, s.a[1], 0.05]} />
                <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
              </mesh>
            );
          })}
        </group>
      ))}
      <group ref={group}>
        {Array.from({ length: packs }, (_, i) => (
          <group key={i} visible={running}>
            <Carton color={color} roof={palette.milk} />
          </group>
        ))}
      </group>
    </group>
  );
}
