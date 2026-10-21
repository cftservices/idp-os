"use client";

/**
 * Zesassige robotarm van de palletiser, gebouwd uit de rig van Open Industry
 * Project (SixAxisRobot, MIT). Elke as is een eigen groep; de meshes hangen er
 * met de vaste matrix uit het model onder. Zolang er pallets in de palletiser
 * staan pakt de arm dozen van de toevoerband en zet ze op de pallet; zonder
 * werk, of met verminderde beweging, staat hij stil in zijn rustpositie.
 *
 * Rigmaten en de assen komen uit de rig.json van het onderdeel
 * (oip.six-axis-robot); de standen zijn hier zelf berekend voor een grijppunt
 * op 1,4 m afstand en ongeveer 1 m hoogte, met de zuignap recht naar beneden.
 */
import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Quaternion, Vector3, type Group } from "three";
import { useTwin } from "../context";
import { OipMesh, useOipParts } from "../models";

const q = (x: number, y: number, z: number, w: number) => new Quaternion(x, y, z, w).normalize();
const Q_BASE = q(0, -0.7071068, 0, 0.70710677);
const Q_L2 = q(-0.5, -0.5, -0.5, 0.5);
const Q_L34 = q(0.5, 0.5, 0.5, -0.5);
const Q_L5 = q(0.5, -0.5, 0.5, 0.5);
const Q_SUCTION = q(0.7071068, 0, 0, 0.70710677);
const Q_HYD_BASE = q(0.05364445, -0.70634111, 0.0538397, 0.70377966);
const Q_HYD_ROD = q(-0.32043198, 0.62891006, 0.31849341, 0.63273804);

/** Standen in graden: [J1, J2, J3, J4, J5, J6]. J1 = -90 kijkt naar de band, -180 naar de pallet. */
type Pose = [number, number, number, number, number, number];
const HOME: Pose = [-135, -30, 100, 0, 60, 0];
const PICK_UP: Pose = [-90, -26, 110, 0, 88, 0];
const PICK: Pose = [-90, -24, 132, 0, 68, 0];
const PLACE_UP: Pose = [-180, -26, 110, 0, 88, 90];
const PLACE: Pose = [-180, -24, 132, 0, 68, 90];

/** Eén cyclus: [tijd in s, stand]. Tussen 0,6 en 3,0 s hangt er een doos aan de zuignap. */
const CYCLE: [number, Pose][] = [
  [0, PICK_UP],
  [0.6, PICK],
  [1.2, PICK_UP],
  [2.4, PLACE_UP],
  [3.0, PLACE],
  [3.6, PLACE_UP],
  [4.8, PICK_UP],
];
const PERIOD = CYCLE[CYCLE.length - 1][0];
const GRIP: [number, number] = [0.6, 3.0];

const ease = (t: number) => t * t * (3 - 2 * t);
const DEG = Math.PI / 180;

export function poseAt(t: number): Pose {
  const s = ((t % PERIOD) + PERIOD) % PERIOD;
  for (let i = 1; i < CYCLE.length; i++) {
    const [t1, b] = CYCLE[i];
    if (s <= t1) {
      const [t0, a] = CYCLE[i - 1];
      const k = ease((s - t0) / (t1 - t0));
      return a.map((v, j) => v + (b[j] - v) * k) as Pose;
    }
  }
  return CYCLE[0][1];
}

const CASE: [number, number, number] = [0.3, 0.15, 0.4];
const _v = new Vector3();

export function Robot({ busy, position }: { busy: boolean; position: [number, number, number] }) {
  const { palette, reducedMotion } = useTwin();
  const p = useOipParts("robot");
  const j = [useRef<Group>(null), useRef<Group>(null), useRef<Group>(null), useRef<Group>(null), useRef<Group>(null), useRef<Group>(null)];
  const hydBase = useRef<Group>(null);
  const hydRod = useRef<Group>(null);
  const carried = useRef<Group>(null);
  const waiting = useRef<Group>(null);

  useFrame(({ clock }) => {
    const moving = busy && !reducedMotion;
    const t = clock.elapsedTime;
    const pose = moving ? poseAt(t) : HOME;
    const axes = ["y", "z", "z", "y", "z", "y"] as const;
    j.forEach((r, i) => {
      if (r.current) r.current.rotation[axes[i]] = pose[i] * DEG;
    });
    const s = ((t % PERIOD) + PERIOD) % PERIOD;
    const holding = moving && s >= GRIP[0] && s < GRIP[1];
    if (carried.current) carried.current.visible = holding;
    if (waiting.current) waiting.current.visible = busy && !holding;
    // de hydrauliekcilinders wijzen naar elkaar, zodat ze bij elke stand aansluiten
    if (hydBase.current && hydRod.current) {
      hydBase.current.parent?.updateWorldMatrix(true, true);
      hydBase.current.lookAt(hydRod.current.getWorldPosition(_v));
      hydRod.current.lookAt(hydBase.current.getWorldPosition(_v));
    }
  });

  return (
    <group position={position}>
      <group quaternion={Q_BASE}>
        <OipMesh parts={p["Six-Axis_Base"]} />
      </group>
      <group ref={j[0]}>
        <group position={[0, 0.23, 0]} quaternion={Q_BASE}>
          <OipMesh parts={p["Six-Axis_Linkage_01"]} />
        </group>
        <group ref={hydBase} position={[0.31569114, 0.5505995, -0.22799234]} quaternion={Q_HYD_BASE}>
          <OipMesh parts={p["Six-Axis_Hydraulic_01"]} />
        </group>
        <group position={[0, 0.9, 0]}>
          <group ref={j[1]} position={[-0.72657037, -0.34717786, -0.00242546]}>
            <group quaternion={Q_L2}>
              <OipMesh parts={p["Six-Axis_Linkage_02"]} />
            </group>
            <group ref={hydRod} position={[0.19884515, 0.00020599, -0.228842]} quaternion={Q_HYD_ROD}>
              <OipMesh parts={p["Six-Axis_Hydraulic_02"]} />
            </group>
            <group position={[0.01472515, 1.3262625, -0.00687705]}>
              <group ref={j[2]} position={[-0.01825523, 0.01825523, -0.09700517]}>
                <group quaternion={Q_L34}>
                  <OipMesh parts={p["Six-Axis_Linkage_03"]} />
                </group>
                <group ref={j[3]} position={[0.11355525, 0.87549424, 0.09818761]}>
                  <group quaternion={Q_L34}>
                    <OipMesh parts={p["Six-Axis_Linkage_04"]} />
                  </group>
                  <group ref={j[4]} position={[0, 0.33123374, 0]}>
                    <group quaternion={Q_L5}>
                      <OipMesh parts={p["Six-Axis_Linkage_05"]} />
                    </group>
                    <group ref={j[5]} position={[0, 0.21365428, 0]}>
                      <group position={[0, 0.02553463, 0]} quaternion={Q_SUCTION}>
                        <OipMesh parts={p["Six-Axis_Tool_Suction"]} />
                      </group>
                      {/* vacuümplaat met de doos die hij vasthoudt */}
                      <mesh position={[0, 0.1, 0]} castShadow>
                        <boxGeometry args={[0.34, 0.04, 0.44]} />
                        <meshStandardMaterial color={palette["steel-dark"]} metalness={0.6} roughness={0.4} />
                      </mesh>
                      <mesh ref={carried} position={[0, 0.12 + CASE[1] / 2, 0]} castShadow visible={false}>
                        <boxGeometry args={CASE} />
                        <meshStandardMaterial color={palette.pallet} roughness={0.9} />
                      </mesh>
                    </group>
                  </group>
                </group>
              </group>
            </group>
          </group>
        </group>
      </group>
      {/* toevoerband: dozen komen van de vuller en wachten aan het eind op de grijper */}
      <group position={[0, 0, -1.4]}>
        <mesh position={[0, 0.82, -0.55]} castShadow receiveShadow>
          <boxGeometry args={[0.6, 0.06, 1.9]} />
          <meshStandardMaterial color={palette["steel-dark"]} metalness={0.4} roughness={0.5} />
        </mesh>
        {[-1.35, 0.25].map((z) =>
          [-0.25, 0.25].map((x) => (
            <mesh key={`${z}${x}`} position={[x, 0.4, z]}>
              <boxGeometry args={[0.05, 0.8, 0.05]} />
              <meshStandardMaterial color={palette.steel} metalness={0.5} roughness={0.5} />
            </mesh>
          )),
        )}
        <mesh ref={waiting} position={[0, 0.85 + CASE[1] / 2, 0]} castShadow visible={busy}>
          <boxGeometry args={CASE} />
          <meshStandardMaterial color={palette.pallet} roughness={0.9} />
        </mesh>
        {busy &&
          [-0.6, -1.15].map((z) => (
            <mesh key={z} position={[0, 0.85 + CASE[1] / 2, z]} castShadow>
              <boxGeometry args={CASE} />
              <meshStandardMaterial color={palette.pallet} roughness={0.9} />
            </mesh>
          ))}
      </group>
    </group>
  );
}
