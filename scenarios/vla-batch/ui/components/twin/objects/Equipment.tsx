"use client";

import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { RoundedBox } from "@react-three/drei";
import { DoubleSide, type Group, type Mesh, type MeshStandardMaterial } from "three";
import type { TwinEquipment } from "@/lib/twin/useTwinData";
import type { VisualState } from "@/lib/twin/derive";
import { statusColor, useTwin } from "../context";
import { Pickable } from "./Pickable";

/**
 * De vijf procesdelen van lijn L1, procedureel opgebouwd uit herkenbare
 * onderdelen (bolle bodems, mangaten, platenpakketten, kartons). Beweging
 * volgt de echte toestand: een roerwerk draait alleen als het procesdeel
 * draait, de vuller transporteert alleen tijdens FILLING. Een stilstaande
 * machine die toch beweegt zou precies de leugen zijn die een twin niet mag
 * vertellen.
 */

const RVS = { metalness: 0.85, roughness: 0.22 } as const;
const GEBORSTELD = { metalness: 0.75, roughness: 0.38 } as const;

function Legs({ r, h, color }: { r: number; h: number; color: string }) {
  const pts = [0, 1, 2, 3].map((i) => {
    const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
    return [Math.cos(a) * r, Math.sin(a) * r] as const;
  });
  return (
    <>
      {pts.map(([x, z], i) => (
        <group key={i} position={[x, 0, z]}>
          <mesh position={[0, h / 2, 0]} castShadow>
            <cylinderGeometry args={[0.08, 0.08, h, 12]} />
            <meshStandardMaterial color={color} {...GEBORSTELD} />
          </mesh>
          {/* stelvoet */}
          <mesh position={[0, 0.03, 0]}>
            <cylinderGeometry args={[0.15, 0.17, 0.06, 14]} />
            <meshStandardMaterial color={color} {...GEBORSTELD} />
          </mesh>
        </group>
      ))}
      {/* ringverband tussen de poten */}
      <mesh position={[0, h * 0.35, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[r, 0.035, 6, 32]} />
        <meshStandardMaterial color={color} {...GEBORSTELD} />
      </mesh>
    </>
  );
}

/** Statusbaken bovenop elk procesdeel: knippert bij alarm, gloeit bij bedrijf. */
function Beacon({ y, state }: { y: number; state: VisualState }) {
  const { palette, reducedMotion } = useTwin();
  const mat = useRef<MeshStandardMaterial>(null);
  const color = statusColor(palette, state);
  useFrame(({ clock }) => {
    if (!mat.current) return;
    const base = state === "run" ? 1.4 : state === "idle" || state === "unknown" ? 0.2 : 1.8;
    const pulse = state === "alarm" && !reducedMotion ? (Math.sin(clock.elapsedTime * 8) > 0 ? 1 : 0.15) : 1;
    mat.current.emissiveIntensity = base * pulse;
  });
  return (
    <group position={[0, y, 0]}>
      <mesh>
        <cylinderGeometry args={[0.08, 0.08, 0.3, 8]} />
        <meshStandardMaterial color={palette["steel-dark"]} />
      </mesh>
      <mesh position={[0, 0.3, 0]}>
        <sphereGeometry args={[0.2, 16, 12]} />
        <meshStandardMaterial ref={mat} color={color} emissive={color} emissiveIntensity={1} />
      </mesh>
    </group>
  );
}

/** Kooiladder langs een tank, met beugels. */
function Ladder({ r, h, a }: { r: number; h: number; a: number }) {
  const { palette } = useTwin();
  const x = Math.cos(a) * (r + 0.25);
  const z = Math.sin(a) * (r + 0.25);
  const rungs = Math.floor(h / 0.3);
  return (
    <group position={[x, 0, z]} rotation={[0, -a + Math.PI / 2, 0]}>
      {[-0.2, 0.2].map((s) => (
        <mesh key={s} position={[s, h / 2, 0]}>
          <cylinderGeometry args={[0.025, 0.025, h, 6]} />
          <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
        </mesh>
      ))}
      {Array.from({ length: rungs }, (_, i) => (
        <mesh key={i} position={[0, 0.25 + i * 0.3, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.018, 0.018, 0.4, 6]} />
          <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
        </mesh>
      ))}
    </group>
  );
}

/** Handbediende vlinderklep met hendel. */
function Valve({ p, r = 0 }: { p: [number, number, number]; r?: number }) {
  const { palette } = useTwin();
  return (
    <group position={p} rotation={[0, r, 0]}>
      <mesh rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.13, 0.13, 0.12, 14]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      <mesh position={[0, 0.14, 0]}>
        <cylinderGeometry args={[0.03, 0.03, 0.16, 6]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      <mesh position={[0.1, 0.22, 0]}>
        <boxGeometry args={[0.28, 0.03, 0.05]} />
        <meshStandardMaterial color={palette.accent} roughness={0.5} />
      </mesh>
    </group>
  );
}

function VesselBody({ r, h, y }: { r: number; h: number; y: number }) {
  const { palette } = useTwin();
  return (
    <group position={[0, y, 0]}>
      <mesh position={[0, h / 2, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[r, r, h, 40]} />
        <meshStandardMaterial color={palette.tank} {...RVS} />
      </mesh>
      {/* bolle bodems (torisferisch), boven en onder */}
      <mesh position={[0, h, 0]} scale={[1, 0.32, 1]} castShadow>
        <sphereGeometry args={[r, 40, 12, 0, Math.PI * 2, 0, Math.PI / 2]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      <mesh position={[0, 0, 0]} scale={[1, 0.38, 1]} castShadow>
        <sphereGeometry args={[r, 40, 12, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2]} />
        <meshStandardMaterial color={palette.steel} {...RVS} side={DoubleSide} />
      </mesh>
      {/* lasnaden */}
      {[0.02, h * 0.36, h - 0.02].map((yy) => (
        <mesh key={yy} position={[0, yy, 0]}>
          <cylinderGeometry args={[r + 0.012, r + 0.012, 0.035, 40, 1, true]} />
          <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
        </mesh>
      ))}
      {/* gekleurde band: puur decoratief, de status zit in ring, baken en label */}
      <mesh position={[0, h * 0.72, 0]}>
        <cylinderGeometry args={[r + 0.02, r + 0.02, 0.18, 40]} />
        <meshStandardMaterial color={palette.accent} emissive={palette.accent} emissiveIntensity={0.25} />
      </mesh>
      {/* mangat met klemmen op de bovenbodem */}
      <group position={[r * 0.45, h + r * 0.24, 0]}>
        <mesh>
          <cylinderGeometry args={[0.28, 0.28, 0.16, 24]} />
          <meshStandardMaterial color={palette.steel} {...RVS} />
        </mesh>
        <mesh position={[0, 0.1, 0]}>
          <cylinderGeometry args={[0.31, 0.31, 0.05, 24]} />
          <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
        </mesh>
        {[0, 1, 2, 3].map((i) => (
          <mesh key={i} position={[Math.cos(i * 1.57) * 0.32, 0.1, Math.sin(i * 1.57) * 0.32]}>
            <boxGeometry args={[0.06, 0.1, 0.06]} />
            <meshStandardMaterial color={palette["steel-dark"]} />
          </mesh>
        ))}
      </group>
      {/* CIP-sproeibol-aansluiting en ontluchting */}
      <mesh position={[-r * 0.4, h + r * 0.26, r * 0.2]}>
        <cylinderGeometry args={[0.07, 0.07, 0.3, 10]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      {/* temperatuur- en niveausensor aan de mantel */}
      <mesh position={[0, h * 0.4, r + 0.06]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.05, 0.05, 0.14, 8]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      <mesh position={[0, h * 0.4, r + 0.16]}>
        <boxGeometry args={[0.14, 0.14, 0.08]} />
        <meshStandardMaterial color={palette["steel-dark"]} roughness={0.6} />
      </mesh>
      {/* uitloop met klep onder de bodem */}
      <mesh position={[0, -r * 0.38 - 0.12, 0]}>
        <cylinderGeometry args={[0.07, 0.07, 0.3, 10]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      <Valve p={[0, -r * 0.38 - 0.3, 0]} />
      <Ladder r={r} h={h + 0.2} a={Math.PI * 0.75} />
    </group>
  );
}

function ReceivingTank({ eq }: { eq: TwinEquipment }) {
  const { palette } = useTwin();
  return (
    <>
      <Legs r={0.9} h={1.0} color={palette["steel-dark"]} />
      <VesselBody r={1.3} h={2.6} y={1.25} />
      <Beacon y={4.5} state={eq.visual.state} />
    </>
  );
}

function Mixer({ eq }: { eq: TwinEquipment }) {
  const { palette, reducedMotion } = useTwin();
  const rotor = useRef<Group>(null);
  const running = eq.visual.state === "run" || eq.health?.state === "Running";
  useFrame((_, dt) => {
    if (rotor.current && running && !reducedMotion) rotor.current.rotation.y += dt * 3;
  });
  return (
    <>
      <Legs r={0.9} h={1.0} color={palette["steel-dark"]} />
      <VesselBody r={1.25} h={2.4} y={1.25} />
      {/* aandrijving: brug, tandwielkast en motor met koelribben */}
      <mesh position={[0, 4.12, 0]} castShadow>
        <boxGeometry args={[1.9, 0.12, 0.3]} />
        <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
      </mesh>
      <mesh position={[0, 4.38, 0]} castShadow>
        <cylinderGeometry args={[0.32, 0.36, 0.4, 20]} />
        <meshStandardMaterial color={palette["steel-dark"]} metalness={0.4} roughness={0.5} />
      </mesh>
      <mesh position={[0, 4.85, 0]} castShadow>
        <cylinderGeometry args={[0.25, 0.25, 0.55, 20]} />
        <meshStandardMaterial color={palette.pipe} metalness={0.3} roughness={0.45} />
      </mesh>
      {Array.from({ length: 6 }, (_, i) => (
        <mesh key={i} position={[0, 4.62 + i * 0.08, 0]}>
          <cylinderGeometry args={[0.28, 0.28, 0.025, 20]} />
          <meshStandardMaterial color={palette.pipe} metalness={0.3} roughness={0.5} />
        </mesh>
      ))}
      <mesh position={[0.3, 4.85, 0]}>
        <boxGeometry args={[0.14, 0.2, 0.2]} />
        <meshStandardMaterial color={palette["steel-dark"]} />
      </mesh>
      {/* ventilatorkap: draait mee met de as, dus alleen als de motor loopt */}
      <group ref={rotor} position={[0, 5.18, 0]}>
        <mesh>
          <cylinderGeometry args={[0.22, 0.25, 0.12, 20]} />
          <meshStandardMaterial color={palette["steel-dark"]} />
        </mesh>
        {[0, 1, 2].map((i) => (
          <mesh key={i} rotation={[0, (i / 3) * Math.PI * 2, 0]} position={[0, 0.07, 0]}>
            <boxGeometry args={[0.42, 0.025, 0.07]} />
            <meshStandardMaterial color={palette.accent} emissive={palette.accent} emissiveIntensity={0.4} />
          </mesh>
        ))}
      </group>
      <Beacon y={5.3} state={eq.visual.state} />
    </>
  );
}

function CookUnit({ eq }: { eq: TwinEquipment }) {
  const { palette, reducedMotion } = useTwin();
  const coil = useRef<MeshStandardMaterial>(null);
  const hot = eq.health?.state === "Running";
  useFrame(({ clock }) => {
    if (!coil.current) return;
    coil.current.emissiveIntensity = hot ? (reducedMotion ? 1.2 : 0.9 + Math.sin(clock.elapsedTime * 3) * 0.5) : 0.05;
  });
  return (
    <>
      {/* RVS skid met afvoerbak */}
      <mesh position={[0, 0.1, 0]} receiveShadow castShadow>
        <boxGeometry args={[2.9, 0.2, 2.3]} />
        <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
      </mesh>
      {/* platenwarmtewisselaar: vaste plaat, platenpakket, drukplaat, trekstangen */}
      <group position={[-0.5, 0.2, -0.3]}>
        {[-0.75, 0.75].map((x) => (
          <mesh key={x} position={[x, 0.95, 0]} castShadow>
            <boxGeometry args={[0.14, 1.9, 0.9]} />
            <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
          </mesh>
        ))}
        {Array.from({ length: 18 }, (_, i) => (
          <mesh key={i} position={[-0.6 + i * 0.07, 0.95, 0]}>
            <boxGeometry args={[0.04, 1.6, 0.78]} />
            <meshStandardMaterial color={i % 2 ? palette.steel : palette.tank} {...RVS} />
          </mesh>
        ))}
        {[0.25, 1.75].map((y) =>
          [-0.38, 0.38].map((z) => (
            <mesh key={`${y}${z}`} position={[0, y, z]} rotation={[0, 0, Math.PI / 2]}>
              <cylinderGeometry args={[0.025, 0.025, 1.8, 6]} />
              <meshStandardMaterial color={palette["steel-dark"]} metalness={0.8} roughness={0.3} />
            </mesh>
          )),
        )}
        {[0.5, 1.4].map((y) => (
          <mesh key={y} position={[-0.9, y, 0.2]} rotation={[0, 0, Math.PI / 2]}>
            <cylinderGeometry args={[0.07, 0.07, 0.3, 10]} />
            <meshStandardMaterial color={palette.steel} {...RVS} />
          </mesh>
        ))}
      </group>
      {/* heethoudbuis: gloeit tijdens verhitten */}
      {[0.6, 0.95, 1.3].map((y) => (
        <mesh key={y} position={[0, y, 0.85]} rotation={[0, 0, Math.PI / 2]}>
          <capsuleGeometry args={[0.07, 2.1, 4, 12]} />
          <meshStandardMaterial
            ref={y === 0.95 ? coil : undefined}
            color={palette.vla}
            emissive={palette.vla}
            emissiveIntensity={0.1}
            metalness={0.6}
            roughness={0.3}
          />
        </mesh>
      ))}
      {[-1.15, 1.15].map((x) => (
        <mesh key={x} position={[x, 0.95, 0.85]}>
          <torusGeometry args={[0.175, 0.07, 8, 16, Math.PI]} />
          <meshStandardMaterial color={palette.steel} {...RVS} />
        </mesh>
      ))}
      {/* stoomregelklep */}
      <mesh position={[0.9, 1.2, -0.6]} castShadow>
        <cylinderGeometry args={[0.18, 0.18, 0.5, 14]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      <mesh position={[0.9, 1.6, -0.6]}>
        <cylinderGeometry args={[0.24, 0.24, 0.12, 18]} />
        <meshStandardMaterial color={palette["steel-dark"]} />
      </mesh>
      {/* besturingskast met HMI */}
      <RoundedBox args={[0.9, 1.6, 0.4]} radius={0.04} position={[0.95, 1.0, 0.1]} castShadow>
        <meshStandardMaterial color={palette.steel} metalness={0.5} roughness={0.35} />
      </RoundedBox>
      <mesh position={[0.95, 1.35, 0.31]}>
        <planeGeometry args={[0.6, 0.4]} />
        <meshStandardMaterial color={palette.cold} emissive={palette.cold} emissiveIntensity={0.6} />
      </mesh>
      <mesh position={[0.75, 0.85, 0.31]}>
        <circleGeometry args={[0.05, 16]} />
        <meshStandardMaterial color={palette["status-alarm"]} emissive={palette["status-alarm"]} emissiveIntensity={0.3} />
      </mesh>
      <Beacon y={2.65} state={eq.visual.state} />
    </>
  );
}

function Cooler({ eq }: { eq: TwinEquipment }) {
  const { palette } = useTwin();
  return (
    <>
      <mesh position={[0, 0.15, 0]} receiveShadow>
        <boxGeometry args={[3, 0.3, 1.4]} />
        <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
      </mesh>
      {/* vaste kopplaat, drukplaat en steunkolom */}
      {[-1.4, 1.4].map((x) => (
        <mesh key={x} position={[x, 1.35, 0]} castShadow>
          <boxGeometry args={[0.18, 2.3, 1.25]} />
          <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
        </mesh>
      ))}
      {Array.from({ length: 28 }, (_, i) => (
        <mesh key={i} position={[-1.2 + i * 0.088, 1.35, 0]} castShadow>
          <boxGeometry args={[0.05, 2.0, 1.05]} />
          <meshStandardMaterial color={i % 2 ? palette.cold : palette.steel} {...RVS} />
        </mesh>
      ))}
      {/* draag- en geleidestang */}
      <mesh position={[0, 2.55, 0]}>
        <boxGeometry args={[3, 0.14, 0.2]} />
        <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
      </mesh>
      {[0.55, 2.15].map((y) =>
        [-0.55, 0.55].map((z) => (
          <group key={`${y}${z}`}>
            <mesh position={[0, y, z]} rotation={[0, 0, Math.PI / 2]}>
              <cylinderGeometry args={[0.03, 0.03, 3.1, 8]} />
              <meshStandardMaterial color={palette["steel-dark"]} metalness={0.85} roughness={0.25} />
            </mesh>
            {[-1.56, 1.56].map((x) => (
              <mesh key={x} position={[x, y, z]} rotation={[0, 0, Math.PI / 2]}>
                <cylinderGeometry args={[0.06, 0.06, 0.08, 6]} />
                <meshStandardMaterial color={palette["steel-dark"]} metalness={0.85} roughness={0.25} />
              </mesh>
            ))}
          </group>
        )),
      )}
      {/* ijswater in/uit */}
      {[0.6, 2.0].map((y) => (
        <mesh key={y} position={[-1.6, y, 0.35]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.08, 0.08, 0.3, 12]} />
          <meshStandardMaterial color={palette.cold} metalness={0.5} roughness={0.3} />
        </mesh>
      ))}
      <Beacon y={2.7} state={eq.visual.state} />
    </>
  );
}

/** Gable-top melkpak van 1 liter: blok met nokdak en vouwrand. */
function Carton({ color, roof }: { color: string; roof: string }) {
  return (
    <group>
      <mesh position={[0, 0.13, 0]} castShadow>
        <boxGeometry args={[0.14, 0.26, 0.14]} />
        <meshStandardMaterial color={color} roughness={0.55} />
      </mesh>
      <mesh position={[0, 0.3, 0]} rotation={[0, Math.PI / 4, 0]}>
        <coneGeometry args={[0.1, 0.08, 4]} />
        <meshStandardMaterial color={roof} roughness={0.55} />
      </mesh>
      <mesh position={[0, 0.35, 0]}>
        <boxGeometry args={[0.14, 0.03, 0.012]} />
        <meshStandardMaterial color={color} roughness={0.55} />
      </mesh>
    </group>
  );
}

function Filler({ eq, filling }: { eq: TwinEquipment; filling: boolean }) {
  const { palette, reducedMotion } = useTwin();
  const carousel = useRef<Group>(null);
  const packs = useRef<Group>(null);
  useFrame((_, dt) => {
    if (!filling || reducedMotion) return;
    if (carousel.current) carousel.current.rotation.y += dt * 1.6;
    if (packs.current) {
      for (const c of packs.current.children) {
        c.position.x += dt * 1.1;
        if (c.position.x > 2.6) c.position.x -= 3.2;
      }
    }
  });
  return (
    <>
      <mesh position={[0, 0.6, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[1.1, 1.2, 1.2, 32]} />
        <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
      </mesh>
      <group ref={carousel} position={[0, 1.3, 0]}>
        <mesh>
          <cylinderGeometry args={[1, 1, 0.12, 32]} />
          <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
        </mesh>
        {Array.from({ length: 10 }, (_, i) => {
          const a = (i / 10) * Math.PI * 2;
          return (
            <group key={i} position={[Math.cos(a) * 0.8, 0.06, Math.sin(a) * 0.8]}>
              <mesh position={[0, 0.3, 0]}>
                <cylinderGeometry args={[0.035, 0.05, 0.6, 8]} />
                <meshStandardMaterial color={palette.steel} {...RVS} />
              </mesh>
              <Carton color={palette.pack} roof={palette.milk} />
            </group>
          );
        })}
      </group>
      {/* producttank boven de vulkoppen */}
      <mesh position={[0, 2.4, 0]} castShadow>
        <cylinderGeometry args={[0.6, 0.4, 0.8, 24]} />
        <meshStandardMaterial color={palette.tank} {...RVS} />
      </mesh>
      <mesh position={[0, 2.85, 0]} scale={[1, 0.3, 1]}>
        <sphereGeometry args={[0.6, 24, 8, 0, Math.PI * 2, 0, Math.PI / 2]} />
        <meshStandardMaterial color={palette.steel} {...RVS} />
      </mesh>
      {/* afscherming: RVS frame met polycarbonaat panelen */}
      {[0, 1, 2, 3].map((i) => {
        const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
        return (
          <mesh key={i} position={[Math.cos(a) * 1.35, 1.25, Math.sin(a) * 1.35]}>
            <boxGeometry args={[0.06, 2.5, 0.06]} />
            <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
          </mesh>
        );
      })}
      {[0, 1, 2].map((i) => {
        const a = (i / 4) * Math.PI * 2;
        return (
          <mesh key={`p${i}`} position={[Math.cos(a + Math.PI) * 0.955, 1.3, Math.sin(a + Math.PI) * 0.955]} rotation={[0, -a + Math.PI / 2, 0]}>
            <planeGeometry args={[1.9, 1.3]} />
            <meshPhysicalMaterial color={palette.cold} transparent opacity={0.18} roughness={0.05} side={DoubleSide} depthWrite={false} />
          </mesh>
        );
      })}
      {/* transportband naar de palletiser: rollen, zijgeleiders, poten */}
      <mesh position={[1.6, 0.7, 0]} receiveShadow>
        <boxGeometry args={[3.4, 0.08, 0.5]} />
        <meshStandardMaterial color={palette.road} roughness={0.8} />
      </mesh>
      {[-0.28, 0.28].map((z) => (
        <mesh key={z} position={[1.6, 0.76, z]}>
          <boxGeometry args={[3.4, 0.14, 0.03]} />
          <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
        </mesh>
      ))}
      {[0.2, 1.6, 3.0].map((x) =>
        [-0.22, 0.22].map((z) => (
          <mesh key={`${x}${z}`} position={[x, 0.33, z]}>
            <boxGeometry args={[0.05, 0.66, 0.05]} />
            <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
          </mesh>
        )),
      )}
      <group ref={packs} position={[0, 0.74, 0]}>
        {Array.from({ length: 6 }, (_, i) => (
          <group key={i} position={[-0.4 + i * 0.55, 0, 0]} visible={filling}>
            <Carton color={palette.pack} roof={palette.milk} />
          </group>
        ))}
      </group>
      <Beacon y={3} state={eq.visual.state} />
    </>
  );
}

export function EquipmentObject({ eq, batchState }: { eq: TwinEquipment; batchState: string | null }) {
  const label = { tank: 5.5, mixer: 6.2, cook: 4, cooler: 3.8, filler: 4 }[eq.kind];
  return (
    <Pickable id={`equipment:${eq.id}`} name={eq.name} visual={eq.visual} position={eq.pos} radius={1.75} labelHeight={label}>
      {eq.kind === "tank" && <ReceivingTank eq={eq} />}
      {eq.kind === "mixer" && <Mixer eq={eq} />}
      {eq.kind === "cook" && <CookUnit eq={eq} />}
      {eq.kind === "cooler" && <Cooler eq={eq} />}
      {eq.kind === "filler" && <Filler eq={eq} filling={batchState === "FILLING"} />}
    </Pickable>
  );
}

/** Leiding tussen de procesdelen. Vloeit zichtbaar als er een batch loopt. */
export function ProcessPipe({ from, to, flowing }: { from: number; to: number; flowing: boolean }) {
  const { palette, reducedMotion } = useTwin();
  const pulse = useRef<Mesh>(null);
  const len = to - from;
  const clamps = Math.max(2, Math.round(len / 1.2));
  useFrame(({ clock }) => {
    if (!pulse.current) return;
    pulse.current.visible = flowing;
    if (!reducedMotion) pulse.current.position.x = from + ((clock.elapsedTime * 2) % len);
  });
  return (
    <group>
      <mesh position={[from + len / 2, 2.2, -0.9]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.1, 0.1, len, 16]} />
        <meshStandardMaterial color={palette.pipe} {...RVS} />
      </mesh>
      {/* tri-clamp koppelingen */}
      {Array.from({ length: clamps + 1 }, (_, i) => (
        <mesh key={i} position={[from + (i / clamps) * len, 2.2, -0.9]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.135, 0.135, 0.06, 16]} />
          <meshStandardMaterial color={palette.steel} {...GEBORSTELD} />
        </mesh>
      ))}
      {/* leidingsteun op de vloer */}
      <mesh position={[from + len / 2, 1.05, -0.9]}>
        <boxGeometry args={[0.06, 2.1, 0.06]} />
        <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
      </mesh>
      <mesh position={[from + len / 2, 2.07, -0.9]}>
        <boxGeometry args={[0.08, 0.04, 0.3]} />
        <meshStandardMaterial color={palette["steel-dark"]} {...GEBORSTELD} />
      </mesh>
      <mesh ref={pulse} position={[from, 2.2, -0.9]}>
        <sphereGeometry args={[0.16, 10, 8]} />
        <meshStandardMaterial color={palette.glow} emissive={palette.glow} emissiveIntensity={1.5} />
      </mesh>
    </group>
  );
}
