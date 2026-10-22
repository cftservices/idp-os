"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode, type RefObject } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import { Box3, CanvasTexture, Matrix4, Quaternion, RepeatWrapping, SRGBColorSpace, TextureLoader, Vector3, type Group, type Texture } from "three";
import {
  COLDSTORE,
  MAIN_AISLE_Z,
  PALLETISER,
  PALLETISER_PICK,
  RACK,
  RACK_SLOTS,
  ROAD_X,
  ROAD_Z,
  dockPosition,
  rackLevelY,
  rackSlotPosition,
  type Vec3,
} from "@/lib/twin/layout";
import {
  departureProgress,
  type Departure,
  type DockSlot,
  type Hu,
  type Logistics,
  type Move,
  type Visual,
} from "@/lib/twin/derive";
import {
  CARRY_OFFSET,
  FORK_REST,
  PARK_LOADER,
  PARK_PUTAWAY,
  loadRoute,
  putawayRoute,
  type Pose,
  type Route,
} from "@/lib/twin/forklift";
import {
  departPose,
  forkliftVisual,
  inboundName,
  inboundVisual,
  needsPin,
  shipmentVisual,
  tankerPose,
  type SimForklift,
  type SimInbound,
  type SimShipment,
} from "@/lib/twin/flow";
import type { TwinSilo } from "@/lib/twin/useTwinData";
import { statusColor, useTwin } from "../context";
import { FloatLabel, Pickable, Pin, usePick } from "./Pickable";
import { Robot } from "./Robot";
import { OipMesh, useOipParts, type OipPart } from "../models";

export function Silo({ silo }: { silo: TwinSilo }) {
  const { palette } = useTwin();
  const h = 4.2;
  const fill = silo.fill ?? 0;
  return (
    <Pickable
      id={`silo:${silo.material.material_id}`}
      name={silo.material.name}
      visual={silo.visual}
      position={silo.pos}
      radius={1.35}
      labelHeight={h + 3}
    >
      {[0, 1, 2, 3].map((i) => {
        const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
        return (
          <mesh key={i} position={[Math.cos(a) * 0.8, 0.7, Math.sin(a) * 0.8]} castShadow>
            <cylinderGeometry args={[0.08, 0.08, 1.4, 6]} />
            <meshStandardMaterial color={palette["steel-dark"]} />
          </mesh>
        );
      })}
      <mesh position={[0, 1.6, 0]} rotation={[Math.PI, 0, 0]} castShadow>
        <coneGeometry args={[1.05, 0.9, 24]} />
        <meshStandardMaterial color={palette.silo} metalness={0.4} roughness={0.4} />
      </mesh>
      <mesh position={[0, 2.05 + h / 2, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[1.05, 1.05, h, 24]} />
        <meshStandardMaterial color={palette.silo} metalness={0.4} roughness={0.4} />
      </mesh>
      <mesh position={[0, 2.05 + h + 0.3, 0]} castShadow>
        <coneGeometry args={[1.05, 0.6, 32]} />
        <meshStandardMaterial color={palette.steel} metalness={0.7} roughness={0.3} />
      </mesh>
      {/* lasnaden, kruisverbanden, kooiladder, dakreling en vulleiding */}
      {[0.9, 1.9, 2.9].map((y) => (
        <mesh key={y} position={[0, 2.05 + y, 0]}>
          <cylinderGeometry args={[1.065, 1.065, 0.04, 32, 1, true]} />
          <meshStandardMaterial color={palette["steel-dark"]} metalness={0.6} roughness={0.4} />
        </mesh>
      ))}
      {[0, 1].map((i) => (
        <mesh key={`x${i}`} position={[0, 0.7, i ? 0.566 : -0.566]} rotation={[0, 0, i ? 0.95 : -0.95]}>
          <boxGeometry args={[0.04, 1.75, 0.04]} />
          <meshStandardMaterial color={palette["steel-dark"]} />
        </mesh>
      ))}
      <group position={[1.3, 0, 0]}>
        {[-0.2, 0.2].map((z) => (
          <mesh key={z} position={[0, (2.05 + h + 0.9) / 2, z]}>
            <boxGeometry args={[0.04, 2.05 + h + 0.9, 0.04]} />
            <meshStandardMaterial color={palette.steel} metalness={0.6} roughness={0.4} />
          </mesh>
        ))}
        {Array.from({ length: Math.floor((2.05 + h) / 0.32) }, (_, i) => (
          <mesh key={i} position={[0, 0.3 + i * 0.32, 0]} rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[0.015, 0.015, 0.4, 5]} />
            <meshStandardMaterial color={palette.steel} />
          </mesh>
        ))}
        {/* rugbeschermingskooi vanaf 2,2 m */}
        {Array.from({ length: Math.floor((h - 0.2) / 0.7) }, (_, i) => (
          <mesh key={`k${i}`} position={[0.22, 2.3 + i * 0.7, 0]} rotation={[0, 0, Math.PI / 2]}>
            <torusGeometry args={[0.3, 0.015, 4, 12, Math.PI]} />
            <meshStandardMaterial color={palette.steel} />
          </mesh>
        ))}
      </group>
      <mesh position={[0, 2.05 + h + 0.65, 0]}>
        <torusGeometry args={[0.85, 0.02, 4, 24]} />
        <meshStandardMaterial color={palette.accent} />
      </mesh>
      <mesh position={[-0.5, 2.05 + h / 2 + 0.6, -0.95]}>
        <cylinderGeometry args={[0.06, 0.06, h + 1.4, 8]} />
        <meshStandardMaterial color={palette.steel} metalness={0.7} roughness={0.3} />
      </mesh>
      {/* peilglas: een donkere goot met een vulling die de voorraad volgt */}
      <mesh position={[0, 2.05 + h / 2, 1.07]}>
        <boxGeometry args={[0.24, h, 0.04]} />
        <meshStandardMaterial color={palette.road} />
      </mesh>
      {silo.fill != null && (
        <mesh position={[0, 2.05 + (h * fill) / 2, 1.1]}>
          <boxGeometry args={[0.18, Math.max(0.02, h * fill), 0.04]} />
          <meshStandardMaterial
            color={silo.visual.state === "warn" ? palette["status-warn"] : palette.accent}
            emissive={silo.visual.state === "warn" ? palette["status-warn"] : palette.accent}
            emissiveIntensity={0.6}
          />
        </mesh>
      )}
    </Pickable>
  );
}

/** Packs per laag op een pallet: 12 per tray, 8 trays per laag (zie PACKS_PER_PALLET in de engine). */
const PACKS_PER_LAYER = 80;
const LAYER_H = 0.12;
const MAX_LAYERS = 6;

/** Zijaanzicht van één laag: trays van karton met de kartons erop, als herhalende textuur. */
const layerTextures = new Map<string, CanvasTexture>();
function layerTexture(milk: string, vla: string, tray: string, seam: string, layers: number): CanvasTexture | null {
  if (typeof document === "undefined") return null;
  const key = `${milk}|${vla}|${tray}|${seam}|${layers}`;
  const hit = layerTextures.get(key);
  if (hit) return hit;
  const c = document.createElement("canvas");
  c.width = 64;
  c.height = 32;
  const g = c.getContext("2d")!;
  g.fillStyle = milk;
  g.fillRect(0, 0, 64, 32);
  g.fillStyle = vla; // bedrukte band op de kartons
  g.fillRect(0, 6, 64, 7);
  g.fillStyle = seam; // naden tussen de kartons
  g.globalAlpha = 0.18;
  for (let x = 0; x < 64; x += 8) g.fillRect(x, 0, 1, 24);
  g.globalAlpha = 1;
  g.fillStyle = tray;
  g.fillRect(0, 22, 64, 10);
  g.fillStyle = seam;
  g.globalAlpha = 0.25;
  g.fillRect(0, 22, 64, 1);
  g.globalAlpha = 1;
  const tex = new CanvasTexture(c);
  tex.wrapS = tex.wrapT = RepeatWrapping;
  tex.repeat.set(3, layers);
  tex.colorSpace = SRGBColorSpace;
  layerTextures.set(key, tex);
  return tex;
}

export /** Houttextuur van de europallet (Open Industry Project), één keer geladen en gedeeld door alle pallets. */
let woodCache: { map: Texture; normalMap: Texture; roughnessMap: Texture } | null = null;
function palletWood() {
  if (woodCache || typeof window === "undefined") return woodCache;
  const loader = new TextureLoader();
  const load = (name: string, srgb = false) => {
    const t = loader.load(`/models/oip/tex/pallet-${name}.webp`);
    t.wrapS = t.wrapT = RepeatWrapping;
    if (srgb) t.colorSpace = SRGBColorSpace;
    return t;
  };
  woodCache = { map: load("kleur", true), normalMap: load("normaal"), roughnessMap: load("ruwheid") };
  return woodCache;
}

function PalletModel({ packs = 1, wrapped = false }: { packs?: number; wrapped?: boolean }) {
  const { palette } = useTwin();
  const n = Math.max(1, Number(packs) || 1);
  const full = Math.min(MAX_LAYERS, Math.floor(n / PACKS_PER_LAYER));
  const rest = full < MAX_LAYERS ? (n % PACKS_PER_LAYER) / PACKS_PER_LAYER : 0;
  const tex = layerTexture(palette.milk, palette.vla, palette.pallet, palette.seam, Math.max(1, full));
  const loadH = full * LAYER_H + (rest > 0 ? LAYER_H : 0);
  const wood = palletWood();
  const woodColor = wood ? palette.white : palette.pallet;
  return (
    <group>
      {/* europallet: drie onderlatten, klossen als één blok, dekplanken */}
      {[-0.45, 0, 0.45].map((z) => (
        <mesh key={z} position={[0, 0.011, z]} receiveShadow>
          <boxGeometry args={[1.2, 0.022, 0.1]} />
          <meshStandardMaterial color={woodColor} {...wood} roughness={0.95} />
        </mesh>
      ))}
      <mesh position={[0, 0.06, 0]} castShadow receiveShadow>
        <boxGeometry args={[1.16, 0.076, 0.96]} />
        <meshStandardMaterial color={woodColor} {...wood} roughness={0.95} />
      </mesh>
      <mesh position={[0, 0.11, 0]} castShadow receiveShadow>
        <boxGeometry args={[1.2, 0.024, 1]} />
        <meshStandardMaterial color={woodColor} {...wood} roughness={0.9} />
      </mesh>
      {full > 0 && (
        <mesh position={[0, 0.122 + (full * LAYER_H) / 2, 0]} castShadow receiveShadow>
          <boxGeometry args={[1.16, full * LAYER_H, 0.96]} />
          <meshStandardMaterial map={tex ?? undefined} color={tex ? palette.white : palette.pack} roughness={0.8} />
        </mesh>
      )}
      {rest > 0 && (
        <mesh position={[-0.58 + (1.16 * rest) / 2, 0.122 + full * LAYER_H + LAYER_H / 2, 0]} castShadow>
          <boxGeometry args={[1.16 * rest, LAYER_H, 0.96]} />
          <meshStandardMaterial color={palette.milk} roughness={0.8} />
        </mesh>
      )}
      {wrapped ? (
        /* krimpfolie: glanzend en doorschijnend, net iets groter dan de lading */
        <mesh position={[0, 0.122 + loadH / 2, 0]}>
          <boxGeometry args={[1.19, loadH + 0.02, 0.99]} />
          <meshStandardMaterial color={palette.film} transparent opacity={0.22} roughness={0.05} metalness={0.1} depthWrite={false} />
        </mesh>
      ) : null}
    </group>
  );
}

export function Palletiser({ logistics, visual }: { logistics: Logistics; visual: Visual }) {
  const { palette } = useTwin();
  const busy = logistics.palletiser.length > 0;
  return (
    <Pickable id="palletiser" name="Palletiser" visual={visual} position={PALLETISER} radius={1.4} labelHeight={4}>
      <mesh position={[0, 0.08, 0]} receiveShadow>
        <cylinderGeometry args={[1.1, 1.1, 0.16, 24]} />
        <meshStandardMaterial color={palette["steel-dark"]} />
      </mesh>
      <Suspense fallback={null}>
        <Robot busy={busy} position={[-1.4, 0, 0]} />
      </Suspense>
      {busy && (
        <group position={[0, 0.16, 0]}>
          <PalletModel packs={logistics.palletiser[0].packs_count} wrapped />
        </group>
      )}
      {logistics.palletiser.slice(1, 4).map((hu, i) => (
        <group key={hu.hu_id} position={[0, 0, 1.4 + i * 1.2]}>
          <PalletModel packs={hu.packs_count} wrapped />
        </group>
      ))}
    </Pickable>
  );
}

/** HU's die een heftruck onderweg heeft: die staan al in de data in het vak, maar nog niet in beeld. */
export type Transit = MutableRefObject<Set<string>>;

function RackPallet({ hu, slot, transit, origin }: { hu: Hu; slot: number; transit: Transit; origin: [number, number] }) {
  const ref = useRef<Group>(null);
  const [x, y, z] = rackSlotPosition(slot);
  useFrame(() => {
    if (ref.current) ref.current.visible = !transit.current.has(hu.hu_id);
  });
  return (
    <group ref={ref} position={[x - origin[0], y, z - origin[1]]} visible={!transit.current.has(hu.hu_id)}>
      <PalletModel packs={hu.packs_count} wrapped />
    </group>
  );
}

/** Eén enkelzijdige stellingrij: staanders voor en achter, per niveau twee liggers. */
function RackRow({ z, origin }: { z: number; origin: [number, number] }) {
  const { palette } = useTwin();
  const len = RACK.slotsPerRow * RACK.pitch;
  const x0 = RACK.xStart - RACK.pitch / 2 - origin[0];
  const hgt = RACK.levels * RACK.levelHeight + 0.3;
  const half = RACK.depth / 2;
  return (
    <group position={[x0, 0, z - origin[1]]}>
      {Array.from({ length: RACK.slotsPerRow + 1 }, (_, i) =>
        [-half, half].map((dz) => (
          <mesh key={`${i}${dz}`} position={[i * RACK.pitch, hgt / 2, dz]} castShadow>
            <boxGeometry args={[0.08, hgt, 0.08]} />
            <meshStandardMaterial color={palette["steel-dark"]} metalness={0.5} roughness={0.5} />
          </mesh>
        )),
      )}
      {Array.from({ length: RACK.levels }, (_, l) =>
        [-half, half].map((dz) => (
          <mesh key={`${l}${dz}`} position={[len / 2, rackLevelY(l) - 0.05, dz]}>
            <boxGeometry args={[len, 0.1, 0.06]} />
            <meshStandardMaterial color={palette.rack} metalness={0.3} roughness={0.5} />
          </mesh>
        )),
      )}
      {/* diagonaalverband in elk staanderframe, kolombeschermers onderaan */}
      {Array.from({ length: RACK.slotsPerRow + 1 }, (_, i) => (
        <group key={`d${i}`} position={[i * RACK.pitch, 0, 0]}>
          {Array.from({ length: Math.max(1, Math.floor(hgt / 1)) }, (_, k) => (
            <mesh key={k} position={[0, 0.5 + k, 0]} rotation={[(k % 2 ? 1 : -1) * Math.atan2(RACK.depth, 1), 0, 0]}>
              <boxGeometry args={[0.03, Math.hypot(1, RACK.depth), 0.03]} />
              <meshStandardMaterial color={palette["steel-dark"]} metalness={0.5} roughness={0.5} />
            </mesh>
          ))}
          <mesh position={[0, 0.2, half + 0.06]}>
            <boxGeometry args={[0.16, 0.4, 0.06]} />
            <meshStandardMaterial color={palette.accent} roughness={0.6} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

export function ColdStore({ logistics, visual, transit }: { logistics: Logistics; visual: Visual; transit: Transit }) {
  const { palette } = useTwin();
  const [x0, z0] = COLDSTORE.min;
  const [x1, z1] = COLDSTORE.max;
  const [door0, door1] = COLDSTORE.door;
  const cx = (x0 + x1) / 2;
  const cz = (z0 + z1) / 2;
  const w = x1 - x0;
  const d = z1 - z0;
  const h = COLDSTORE.wall;
  const doorH = 3.2;
  const origin: [number, number] = [cx - 1, cz - 1];
  const westD = door0 - z0;
  return (
    <group>
      <mesh position={[cx, 0.05, cz]} receiveShadow>
        <boxGeometry args={[w, 0.1, d]} />
        <meshStandardMaterial color={palette["ground-line"]} />
      </mesh>
      {/* gele looplijnen langs het hoofdpad */}
      {[MAIN_AISLE_Z - 0.85, MAIN_AISLE_Z + 0.85].map((z) => (
        <mesh key={z} position={[cx, 0.11, z]} rotation={[-Math.PI / 2, 0, 0]}>
          <planeGeometry args={[w - 0.4, 0.08]} />
          <meshBasicMaterial color={palette["road-mark"]} />
        </mesh>
      ))}
      {/* achter- en oostwand; de westwand heeft een opening voor de heftruck */}
      <mesh position={[cx, h / 2, z0]} castShadow receiveShadow>
        <boxGeometry args={[w, h, 0.3]} />
        <meshStandardMaterial color={palette.cold} />
      </mesh>
      <mesh position={[x1, h / 2, cz]} castShadow receiveShadow>
        <boxGeometry args={[0.3, h, d]} />
        <meshStandardMaterial color={palette.cold} />
      </mesh>
      <mesh position={[x0, h / 2, z0 + westD / 2]} castShadow receiveShadow>
        <boxGeometry args={[0.3, h, westD]} />
        <meshStandardMaterial color={palette.cold} />
      </mesh>
      <mesh position={[x0, (doorH + h) / 2, (door0 + door1) / 2]} castShadow>
        <boxGeometry args={[0.3, h - doorH, door1 - door0]} />
        <meshStandardMaterial color={palette.cold} />
      </mesh>
      {/* strokengordijn in de deuropening */}
      <mesh position={[x0, doorH / 2, (door0 + door1) / 2]}>
        <boxGeometry args={[0.04, doorH, door1 - door0]} />
        <meshStandardMaterial color={palette.curtain} transparent opacity={0.18} depthWrite={false} />
      </mesh>
      <mesh position={[cx, h + 0.1, z0 + 0.2]}>
        <boxGeometry args={[w + 0.4, 0.2, 0.6]} />
        <meshStandardMaterial color={palette["cold-roof"]} emissive={palette["cold-roof"]} emissiveIntensity={0.15} />
      </mesh>
      <Pickable
        id="coldstore"
        name="Koelmagazijn"
        visual={visual}
        position={[origin[0], 0, origin[1]]}
        radius={0.01}
        labelHeight={h + 2}
      >
        {RACK.rows.map((z) => (
          <RackRow key={z} z={z} origin={origin} />
        ))}
        {logistics.rack.map(({ slot, hu }) => (
          <RackPallet key={hu.hu_id} hu={hu} slot={slot} transit={transit} origin={origin} />
        ))}
        <Html position={[x1 - origin[0], h + 0.4, z1 - 1.5 - origin[1]]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
          <div className="twin-chip" data-twin-occupancy={logistics.rack.length}>
            {logistics.rack.length}/{RACK_SLOTS} vakken · {logistics.totals.packsStored.toLocaleString("nl-NL")} packs
          </div>
        </Html>
      </Pickable>
    </group>
  );
}

/** Schaal van de OIP-trailer: een 40ft-chassis ingekort tot een 20ft-container, zodat hij tussen dock en weg past. */
const TRAILER_SCALE: Vec3 = [0.9, 0.85, 0.45];

/** Laadruimte van de trailer in truckassen, gemeten uit het OIP-interieur (vloerhoogte, breedte, kop en achterkant). */
function useTrailerHold() {
  const p = useOipParts("trailer");
  return useMemo(() => {
    const box = new Box3();
    for (const part of p.Interior ?? []) {
      if (!part.geometry.boundingBox) part.geometry.computeBoundingBox();
      box.union(part.geometry.boundingBox!.clone().applyMatrix4(part.matrix));
    }
    if (box.isEmpty()) return { floor: 1.1, width: 2.0, front: 2.5, back: -2.5 };
    const [sx, sy, sz] = TRAILER_SCALE;
    return { floor: box.min.y * sy, width: (box.max.x - box.min.x) * sx, front: box.max.z * sz, back: box.min.z * sz };
  }, [p.Interior]);
}

/**
 * De geladen HU als ingepakte pallets in de trailer, twee naast elkaar en vanaf de kop
 * naar de deuren gevuld, zoals een heftruck een trailer werkelijk laadt.
 */
function TrailerCargo({ hus }: { hus: Hu[] }) {
  const hold = useTrailerHold();
  const rows = Math.max(1, Math.floor((hold.front - hold.back - 0.1) / 1.22));
  const across = hold.width >= 2.0 ? 2 : 1;
  const shown = hus.slice(0, rows * across);
  return (
    <group>
      {shown.map((h, i) => {
        const row = Math.floor(i / across);
        const x = across === 2 ? (i % 2 === 0 ? -0.51 : 0.51) : 0;
        const z = hold.front - 0.05 - 0.61 - row * 1.22;
        return (
          <group key={h.hu_id ?? i} position={[x, hold.floor + 0.01, z]} rotation={[0, Math.PI / 2, 0]}>
            <PalletModel packs={Number(h.packs_count) || 1} wrapped />
          </group>
        );
      })}
    </group>
  );
}

/** Container-trailer uit Open Industry Project; de achterdeuren klappen open zodra hij aan een dock staat. */
function OipTrailer({ doorsOpen, xray = false }: { doorsOpen: boolean; xray?: boolean }) {
  const p = useOipParts("trailer");
  // doorkijk aan het dock: dezelfde container met een glazige kopie van zijn materiaal, zodat de lading zichtbaar is
  const glass = useMemo(
    () =>
      p.Container?.map((part) => {
        const m = (Array.isArray(part.material) ? part.material[0] : part.material).clone();
        m.transparent = true;
        m.opacity = 0.22;
        m.depthWrite = false;
        return { ...part, material: m };
      }),
    [p.Container],
  );
  // gesloten liggen beide deuren dwars (π), open klappen ze tegen de zijwand
  const left = doorsOpen ? -Math.PI / 2 : Math.PI;
  const right = doorsOpen ? Math.PI / 2 : Math.PI;
  const hinge = (parts: OipPart[] | undefined) => parts?.[0]?.matrix.elements;
  const hl = hinge(p.Door_Left);
  const hr = hinge(p.Door_Right);
  return (
    <group scale={TRAILER_SCALE}>
      <OipMesh parts={xray ? glass : p.Container} shadow={!xray} />
      <OipMesh parts={p.Interior} shadow={false} />
      <OipMesh parts={p.Chassis} />
      {hl && (
        <group position={[hl[12], hl[13], hl[14]]} rotation={[0, left, 0]}>
          <OipDoor parts={p.Door_Left} />
        </group>
      )}
      {hr && (
        <group position={[hr[12], hr[13], hr[14]]} rotation={[0, right, 0]}>
          <OipDoor parts={p.Door_Right} />
        </group>
      )}
    </group>
  );
}

/** Een deur zonder zijn eigen scharnierstand: positie en draaiing uit het model eruit, de rest (meshopt-schaal) erin. */
function OipDoor({ parts }: { parts?: OipPart[] }) {
  const stripped = useMemo(
    () =>
      parts?.map((d) => {
        const pos = new Vector3();
        const rot = new Quaternion();
        const scl = new Vector3();
        d.matrix.decompose(pos, rot, scl);
        return { ...d, matrix: new Matrix4().compose(new Vector3(), new Quaternion(), scl) };
      }),
    [parts],
  );
  return <OipMesh parts={stripped} />;
}

/** Procedurele trailer, als terugval zolang het model laadt. */
function BoxTrailer() {
  const { palette } = useTwin();
  return (
    <mesh position={[0, 1.55, 0]} castShadow receiveShadow>
      <boxGeometry args={[2.2, 2.3, 5.4]} />
      <meshStandardMaterial color={palette["truck-box"]} />
    </mesh>
  );
}

function TruckModel({ load = 0, docked = false, cargo }: { load?: number; docked?: boolean; cargo?: Hu[] }) {
  const { palette } = useTwin();
  return (
    <group>
      {/* trailer (achterkant aan -z, cabine aan +z) */}
      <Suspense fallback={<BoxTrailer />}>
        <OipTrailer doorsOpen={docked} xray={cargo !== undefined} />
        {cargo && <TrailerCargo hus={cargo} />}
      </Suspense>
      {/* laadstreep op de zijkant: hoe vol de truck is */}
      <mesh position={[1.12, 1.45, -2.6 + (5.2 * load) / 2]} rotation={[0, Math.PI / 2, 0]}>
        <planeGeometry args={[Math.max(0.01, 5.2 * load), 0.22]} />
        <meshBasicMaterial color={palette.accent} />
      </mesh>
      {/* trekker: cabine iets hoger geschaald, zodat hij bij de container past */}
      <mesh position={[0, 0.42, 3.4]}>
        <boxGeometry args={[1.4, 0.18, 1.8]} />
        <meshStandardMaterial color={palette.road} metalness={0.4} roughness={0.6} />
      </mesh>
      <group scale={[1, 1.45, 1]}>
        <mesh position={[0, 1.15, 3.4]} castShadow>
          <boxGeometry args={[2.1, 1.7, 1.3]} />
          <meshStandardMaterial color={palette["truck-cab"]} metalness={0.3} roughness={0.4} />
        </mesh>
        <mesh position={[0, 1.55, 4.06]}>
          <planeGeometry args={[1.7, 0.6]} />
          <meshStandardMaterial color={palette.sky} metalness={0.8} roughness={0.1} />
        </mesh>
        {/* grille, koplampen, spiegels, dakspoiler */}
        <mesh position={[0, 0.85, 4.06]}>
          <planeGeometry args={[1.3, 0.5]} />
          <meshStandardMaterial color={palette["steel-dark"]} metalness={0.6} roughness={0.4} />
        </mesh>
        {[-0.82, 0.82].map((x) => (
          <mesh key={`k${x}`} position={[x, 0.6, 4.07]}>
            <boxGeometry args={[0.3, 0.1, 0.02]} />
            <meshStandardMaterial color={palette.glow} emissive={palette.glow} emissiveIntensity={0.6} />
          </mesh>
        ))}
        {[-1, 1].map((s) => (
          <mesh key={`m${s}`} position={[s * 1.2, 1.6, 3.9]}>
            <boxGeometry args={[0.05, 0.35, 0.12]} />
            <meshStandardMaterial color={palette.road} />
          </mesh>
        ))}
        <mesh position={[0, 2.15, 3.45]} rotation={[0.25, 0, 0]}>
          <boxGeometry args={[2.0, 0.08, 1.1]} />
          <meshStandardMaterial color={palette["truck-cab"]} metalness={0.3} roughness={0.4} />
        </mesh>
      </group>
      <mesh position={[0, 0.42, 4.12]}>
        <boxGeometry args={[2.1, 0.25, 0.12]} />
        <meshStandardMaterial color={palette.road} roughness={0.6} />
      </mesh>
      {[-1, 1].map((s) => (
        <group key={s} position={[s * 1.05, 0.38, 3.5]} rotation={[0, 0, Math.PI / 2]}>
          <mesh castShadow>
            <cylinderGeometry args={[0.38, 0.38, 0.3, 20]} />
            <meshStandardMaterial color={palette.road} roughness={0.85} />
          </mesh>
          <mesh position={[0, s * 0.155, 0]}>
            <cylinderGeometry args={[0.22, 0.22, 0.02, 16]} />
            <meshStandardMaterial color={palette.steel} metalness={0.8} roughness={0.3} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

/** OIP-docks zijn ontworpen op 3,5 m; onze docks staan 3,4 m uit elkaar. */
const DOCK_SCALE = 0.85;
/** Scharnier van de leveller (aan de binnenkant), en hoe ver lip en plaat draaien. */
const LEVELER_HINGE_Z = 2.4;
const LIP_STOWED = -85 * (Math.PI / 180);
const MAIN_DEPLOYED = 3 * (Math.PI / 180);
const DOOR_H = 3.11;

/**
 * Eén dock: roldeur en dockleveller uit Open Industry Project. Het model is in
 * OIP-assen gebouwd (buitengevel op z=0, gebouw aan +z), dus de groep draait
 * een halve slag: binnen is bij ons -z. Met een truck ervoor rolt de deur op
 * en gaat de leveller omhoog met de lip uitgeklapt.
 */
function DockBay({ index, occupied }: { index: number; occupied: boolean }) {
  const p = useOipParts("leveller");
  const d = useOipParts("dockdeur");
  const { palette, reducedMotion } = useTwin();
  const door = useRef<Group>(null);
  const main = useRef<Group>(null);
  const lip = useRef<Group>(null);
  const [x, , z] = dockPosition(index);
  const shift = useMemo(() => new Matrix4().makeTranslation(0, 0, -LEVELER_HINGE_Z), []);
  const mainParts = useMemo(
    () => p.Lift_Main?.map((m) => ({ ...m, matrix: shift.clone().multiply(m.matrix) })),
    [p.Lift_Main, shift],
  );
  const bollards = useMemo(
    () =>
      [-1, 1].map((sx) =>
        p.Bollard?.map((m) => ({ ...m, matrix: new Matrix4().makeTranslation(sx * 1.75, 0, 0.25).multiply(m.matrix) })),
      ),
    [p.Bollard],
  );
  useFrame((_, dt) => {
    const k = reducedMotion ? 1 : Math.min(1, dt * 3);
    const open = occupied ? 1 : 0;
    if (door.current) {
      // roldeur: het paneel rolt op tot een smalle band onder de trommel
      const s = door.current.scale.y + ((occupied ? 0.05 : 1) - door.current.scale.y) * k;
      door.current.scale.y = s;
      door.current.position.y = DOOR_H * (1 - s);
    }
    if (main.current) main.current.rotation.x += (MAIN_DEPLOYED * open - main.current.rotation.x) * k;
    if (lip.current) lip.current.rotation.x += (LIP_STOWED * (1 - open) - lip.current.rotation.x) * k;
  });
  return (
    <group position={[x, 0.1, z]} rotation={[0, Math.PI, 0]} scale={DOCK_SCALE}>
      <OipMesh parts={p.Surrounds} />
      {bollards.map((b, i) => (
        <OipMesh key={i} parts={b} />
      ))}
      <group ref={main} position={[0, 0, LEVELER_HINGE_Z]}>
        <OipMesh parts={mainParts} />
        <group ref={lip} position={[0, 0, -LEVELER_HINGE_Z]} rotation={[occupied ? 0 : LIP_STOWED, 0, 0]}>
          <OipMesh parts={p.Lift_Lip} />
        </group>
      </group>
      <group ref={door} scale={[1, occupied ? 0.05 : 1, 1]} position={[0, occupied ? DOOR_H * 0.95 : 0, 0]}>
        <OipMesh parts={d.Door} />
      </group>
      {/* trommelkast boven de deur en de stijlen waar het paneel in loopt */}
      <mesh position={[0, DOOR_H + 0.18, 0.15]} castShadow>
        <boxGeometry args={[3.4, 0.36, 0.4]} />
        <meshStandardMaterial color={palette.dock} metalness={0.3} roughness={0.6} />
      </mesh>
      {[-1, 1].map((s) => (
        <mesh key={s} position={[s * 1.62, DOOR_H / 2, 0.15]} castShadow>
          <boxGeometry args={[0.14, DOOR_H, 0.3]} />
          <meshStandardMaterial color={palette.dock} metalness={0.3} roughness={0.6} />
        </mesh>
      ))}
    </group>
  );
}

/** Terugval zolang de OIP-modellen laden: het oude dockblok. */
function DockBlock({ index }: { index: number }) {
  const { palette } = useTwin();
  const [x, , z] = dockPosition(index);
  return (
    <mesh position={[x, 0.6, z + 0.25]} receiveShadow>
      <boxGeometry args={[2.8, 1.2, 0.5]} />
      <meshStandardMaterial color={palette.dock} />
    </mesh>
  );
}

export function DockTruck({ slot, visual }: { slot: DockSlot; visual: Visual }) {
  const [x, , z] = dockPosition(slot.dock);
  return (
    <group>
      <Suspense fallback={<DockBlock index={slot.dock} />}>
        <DockBay index={slot.dock} occupied />
      </Suspense>
      <Pickable
        id={`dock:${slot.batch_id}`}
        name={`Dock ${slot.dock + 1}`}
        visual={visual}
        position={[x, 0, z + 3.6]}
        radius={0.01}
        labelHeight={4.6}
      >
        <TruckModel load={slot.progress} docked cargo={slot.loaded} />
        <DockChip id={`dock:${slot.batch_id}`} dock={slot.dock} loaded={slot.loaded.length} total={slot.loaded.length + slot.waiting.length} />
      </Pickable>
    </group>
  );
}

/** Compacte HU-teller boven het dock; wijkt voor het volledige label bij hover of selectie. */
function DockChip({ id, dock, loaded, total }: { id: string; dock: number; loaded: number; total: number }) {
  const { labelled } = useTwin();
  if (labelled.has(id)) return null;
  return (
    <Html position={[0, 4, 0]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
      <div className="twin-chip" data-twin-dock={dock + 1}>
        D{dock + 1} · {loaded}/{total} HU
      </div>
    </Html>
  );
}

export function EmptyDock({ index }: { index: number }) {
  const { palette } = useTwin();
  const [x, , z] = dockPosition(index);
  return (
    <group>
      <Suspense fallback={<DockBlock index={index} />}>
        <DockBay index={index} occupied={false} />
      </Suspense>
      {[-1, 1].map((s) => (
        <mesh key={s} position={[x + s * 1.3, 0.02, z + 3.5]} rotation={[-Math.PI / 2, 0, 0]}>
          <planeGeometry args={[0.12, 6]} />
          <meshBasicMaterial color={palette["road-mark"]} />
        </mesh>
      ))}
    </group>
  );
}
/** Vertrekkende truck: rijdt in het vertrekvenster van de expeditie naar de rand van de kaart. */
export function DepartingTruck({ dep, index, visual }: { dep: Departure; index: number; visual: Visual }) {
  const startX = 24;
  const endX = ROAD_X[1] - 2;
  const p = departureProgress(dep.ageMs);
  const x = startX + (endX - startX) * p;
  return (
    <Pickable
      id={`truck:${dep.batch_id}`}
      name="Vertrokken truck"
      visual={visual}
      position={[x - index * 7, 0, ROAD_Z + 1.4]}
      radius={0.01}
      labelHeight={4}
      rotationY={Math.PI / 2}
    >
      <TruckModel load={1} />
    </Pickable>
  );
}

/** Rijdend object: pose per frame uit een pure functie, label en ring rijden mee. */
function Driving({
  id,
  name,
  visual,
  pose,
  labelHeight,
  children,
}: {
  id: string;
  name: string;
  visual: Visual;
  pose: (now: number) => { x: number; z: number; rot: number; visible: boolean };
  labelHeight: number;
  children: ReactNode;
}) {
  const { palette, selected, hovered, reducedMotion } = useTwin();
  const g = useRef<Group>(null);
  const body = useRef<Group>(null);
  const handlers = usePick(id);
  const on = selected === id || hovered === id;
  useFrame(() => {
    if (!g.current) return;
    const p = pose(Date.now());
    g.current.visible = p.visible;
    g.current.position.set(p.x, 0, p.z);
    if (body.current) body.current.rotation.y = p.rot;
  });
  const first = pose(Date.now());
  return (
    <group ref={g} position={[first.x, 0, first.z]} visible={first.visible} {...handlers}>
      <group ref={body} rotation={[0, first.rot, 0]}>
        {children}
      </group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}>
        <ringGeometry args={[3.4, on ? 3.75 : 3.55, 48]} />
        <meshBasicMaterial color={on ? palette.glow : statusColor(palette, visual.state)} transparent opacity={on ? 0.95 : 0.55} />
      </mesh>
      {needsPin(visual) && selected !== id && !reducedMotion && <Pin visual={visual} height={labelHeight + 1.1} />}
      <FloatLabel id={id} name={name} visual={visual} height={labelHeight} />
    </group>
  );
}

/** Vertrekkende zending uit de demo-simulatie: rijdt het eerste stuk van de rit in beeld weg. */
export function SimDepartingTruck({ s, lane }: { s: SimShipment; lane: number }) {
  const pose = useCallback((now: number) => departPose(s, now, lane, ROAD_Z), [s, lane]);
  return (
    <Driving id={`shipment:${s.shipment_id}`} name={`${s.truck_id} · ${s.customer}`} visual={shipmentVisual(s)} pose={pose} labelHeight={4.2}>
      <TruckModel load={1} />
    </Driving>
  );
}

/** Tankwagen; `bulk` maakt er een gelakte poederbulkwagen van (suiker, zetmeel) in plaats van RVS voor melk. */
function TankerModel({ bulk = false }: { bulk?: boolean }) {
  const { palette } = useTwin();
  return (
    <group>
      {/* tank op een chassis, cabine aan +z (zelfde conventie als TruckModel) */}
      <mesh position={[0, 1.75, -0.3]} rotation={[Math.PI / 2, 0, 0]} castShadow receiveShadow>
        <capsuleGeometry args={[1.05, 4.0, 8, 24]} />
        <meshStandardMaterial
          color={bulk ? palette.silo : palette.steel}
          metalness={bulk ? 0.35 : 0.85}
          roughness={bulk ? 0.45 : 0.22}
        />
      </mesh>
      {bulk &&
        [-1.2, 0.6].map((z) => (
          // losconussen onder de bulktank
          <mesh key={`c${z}`} position={[0, 0.78, -0.3 + z]} rotation={[Math.PI, 0, 0]}>
            <coneGeometry args={[0.45, 0.5, 16]} />
            <meshStandardMaterial color={palette["steel-dark"]} metalness={0.6} roughness={0.4} />
          </mesh>
        ))}
      {[-1.6, 0, 1.6].map((z) => (
        <mesh key={z} position={[0, 1.75, -0.3 + z]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[1.07, 0.04, 8, 32]} />
          <meshStandardMaterial color={palette["steel-dark"]} metalness={0.7} roughness={0.35} />
        </mesh>
      ))}
      <mesh position={[0, 2.85, -0.3]}>
        <boxGeometry args={[0.5, 0.12, 3.6]} />
        <meshStandardMaterial color={palette["steel-dark"]} metalness={0.6} roughness={0.4} />
      </mesh>
      <mesh position={[0, 1.75, -2.95]}>
        <circleGeometry args={[0.32, 20]} />
        <meshStandardMaterial color={palette.accent} />
      </mesh>
      <mesh position={[0, 1.15, 3.35]} castShadow>
        <boxGeometry args={[2.1, 1.7, 1.3]} />
        <meshStandardMaterial color={palette["truck-cab"]} metalness={0.3} roughness={0.4} />
      </mesh>
      <mesh position={[0, 1.55, 4.01]}>
        <planeGeometry args={[1.7, 0.6]} />
        <meshStandardMaterial color={palette.sky} metalness={0.8} roughness={0.1} />
      </mesh>
      {[-0.82, 0.82].map((x) => (
        <mesh key={`k${x}`} position={[x, 0.75, 4.02]}>
          <boxGeometry args={[0.3, 0.14, 0.02]} />
          <meshStandardMaterial color={palette.glow} emissive={palette.glow} emissiveIntensity={0.6} />
        </mesh>
      ))}
      <mesh position={[0, 0.42, 0.3]}>
        <boxGeometry args={[1.4, 0.18, 6.6]} />
        <meshStandardMaterial color={palette.road} metalness={0.4} roughness={0.6} />
      </mesh>
      {[-2.0, -0.8, 3.3].map((z) =>
        [-1, 1].map((sd) => (
          <mesh key={`${z}${sd}`} position={[sd * 1.05, 0.38, z]} rotation={[0, 0, Math.PI / 2]} castShadow>
            <cylinderGeometry args={[0.38, 0.38, 0.3, 20]} />
            <meshStandardMaterial color={palette.road} roughness={0.85} />
          </mesh>
        )),
      )}
    </group>
  );
}

const INBOUND_PALLETS = 12;

/**
 * Leverancier van één grondstof: komt aanrijden, lost op zijn eigen plaats bij de silo's, en rijdt weer weg.
 * Melk komt per RVS-tankwagen, suiker en zetmeel per bulkwagen, cacao in zakken op pallets.
 */
export function Tanker({ inbound }: { inbound: SimInbound }) {
  const pose = useCallback((now: number) => tankerPose(inbound, now), [inbound]);
  const vehicle = inbound.vehicle ?? "tanker";
  // de palletwagen loopt zichtbaar leeg naarmate er gelost wordt
  const left = inbound.stage === "gone" ? 0 : Math.round(INBOUND_PALLETS * (1 - (inbound.unloaded_pct || 0) / 100));
  const cargo = useMemo(
    () => Array.from({ length: left }, (_, k) => ({ hu_id: `${inbound.id}-${k}`, packs_count: 4 }) as unknown as Hu),
    [inbound.id, left],
  );
  return (
    <Driving
      id={`inbound:${inbound.material_id ?? "milk"}`}
      name={inboundName(inbound)}
      visual={inboundVisual(inbound)}
      pose={pose}
      labelHeight={4.2}
    >
      {vehicle === "pallet" ? (
        <TruckModel load={left / INBOUND_PALLETS} docked={inbound.stage === "unloading"} cargo={cargo} />
      ) : (
        <TankerModel bulk={vehicle === "bulk"} />
      )}
    </Driving>
  );
}

/** Heftruck als model; pose wordt per frame op de refs gezet, zonder React-render. */
type Rig = {
  root: RefObject<Group | null>;
  inner: RefObject<Group | null>;
  carriage: RefObject<Group | null>;
  load: RefObject<Group | null>;
};

function applyPose(rig: Rig, p: Pose) {
  if (rig.root.current) {
    rig.root.current.position.set(p.x, 0, p.z);
    rig.root.current.rotation.y = p.rot;
  }
  if (rig.carriage.current) rig.carriage.current.position.y = p.fork;
  // telescoopmast: de binnenmast schuift pas uit als de vork boven de buitenmast komt
  if (rig.inner.current) rig.inner.current.position.y = Math.max(0, p.fork + 1.3 - 2.3);
  if (rig.load.current) rig.load.current.visible = p.carrying;
}

function ForkliftModel({ rig, color, packs, children }: { rig: Rig; color: string; packs: number; children?: ReactNode }) {
  const { palette } = useTwin();
  const steel = palette["steel-dark"];
  return (
    <group ref={rig.root}>
      {children}
      {/* chassis en contragewicht; de vorken wijzen naar lokaal +z */}
      <mesh position={[0, 0.42, -0.1]} castShadow>
        <boxGeometry args={[1.0, 0.55, 1.4]} />
        <meshStandardMaterial color={color} roughness={0.5} />
      </mesh>
      <mesh position={[0, 0.55, -0.85]} castShadow>
        <boxGeometry args={[1.0, 0.8, 0.35]} />
        <meshStandardMaterial color={palette.road} roughness={0.7} />
      </mesh>
      <mesh position={[0, 0.55, -1.02]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.4, 0.4, 1.0, 16, 1, false, 0, Math.PI]} />
        <meshStandardMaterial color={palette.road} roughness={0.7} />
      </mesh>
      {/* stoel met rugleuning, stuurkolom en stuurwiel */}
      <mesh position={[0, 0.78, -0.35]}>
        <boxGeometry args={[0.45, 0.1, 0.4]} />
        <meshStandardMaterial color={palette.road} roughness={0.9} />
      </mesh>
      <mesh position={[0, 1.05, -0.55]} rotation={[-0.15, 0, 0]}>
        <boxGeometry args={[0.45, 0.5, 0.08]} />
        <meshStandardMaterial color={palette.road} roughness={0.9} />
      </mesh>
      <mesh position={[0, 1.0, 0.12]} rotation={[0.5, 0, 0]}>
        <cylinderGeometry args={[0.025, 0.025, 0.45, 6]} />
        <meshStandardMaterial color={steel} />
      </mesh>
      <mesh position={[0, 1.2, 0.02]} rotation={[-1.07, 0, 0]}>
        <torusGeometry args={[0.14, 0.015, 6, 16]} />
        <meshStandardMaterial color={palette.road} />
      </mesh>
      {/* veiligheidskooi */}
      {[-0.45, 0.45].map((x) =>
        [-0.65, 0.35].map((z) => (
          <mesh key={`${x}${z}`} position={[x, 1.45, z]}>
            <boxGeometry args={[0.05, 1.5, 0.05]} />
            <meshStandardMaterial color={steel} />
          </mesh>
        )),
      )}
      {[-0.6, -0.4, -0.2, 0, 0.2, 0.3].map((z) => (
        <mesh key={`s${z}`} position={[0, 2.2, z]} castShadow>
          <boxGeometry args={[0.98, 0.04, 0.06]} />
          <meshStandardMaterial color={steel} metalness={0.4} roughness={0.5} />
        </mesh>
      ))}
      {[-0.47, 0.47].map((x) => (
        <mesh key={`sr${x}`} position={[x, 2.2, -0.15]}>
          <boxGeometry args={[0.05, 0.05, 1.1]} />
          <meshStandardMaterial color={steel} />
        </mesh>
      ))}
      {/* zwaailamp */}
      <mesh position={[0, 2.3, -0.5]}>
        <cylinderGeometry args={[0.06, 0.06, 0.12, 8]} />
        <meshStandardMaterial color={palette.dock} emissive={palette.dock} emissiveIntensity={0.9} />
      </mesh>
      {[-0.45, 0.45].map((x) =>
        [-0.55, 0.4].map((z) => (
          <group key={`w${x}${z}`} position={[x * 1.05, 0.2, z]} rotation={[0, 0, Math.PI / 2]}>
            <mesh castShadow>
              <cylinderGeometry args={[0.2, 0.2, 0.18, 18]} />
              <meshStandardMaterial color={palette.road} roughness={0.85} />
            </mesh>
            <mesh position={[0, x > 0 ? 0.095 : -0.095, 0]}>
              <cylinderGeometry args={[0.11, 0.11, 0.01, 12]} />
              <meshStandardMaterial color={palette.accent} metalness={0.4} roughness={0.5} />
            </mesh>
          </group>
        )),
      )}
      {/* buitenmast */}
      {[-0.32, 0.32].map((x) => (
        <mesh key={`o${x}`} position={[x, 1.2, 0.62]} castShadow>
          <boxGeometry args={[0.08, 2.3, 0.1]} />
          <meshStandardMaterial color={steel} metalness={0.5} />
        </mesh>
      ))}
      <group ref={rig.inner}>
        {[-0.24, 0.24].map((x) => (
          <mesh key={`i${x}`} position={[x, 1.25, 0.66]}>
            <boxGeometry args={[0.07, 2.3, 0.08]} />
            <meshStandardMaterial color={palette.steel} metalness={0.5} />
          </mesh>
        ))}
      </group>
      <group ref={rig.carriage} position={[0, FORK_REST, 0]}>
        <mesh position={[0, 0.35, 0.72]}>
          <boxGeometry args={[0.8, 0.7, 0.05]} />
          <meshStandardMaterial color={steel} />
        </mesh>
        {[-0.25, 0.25].map((x) => (
          <mesh key={`f${x}`} position={[x, -0.03, 1.3]}>
            <boxGeometry args={[0.1, 0.04, 1.15]} />
            <meshStandardMaterial color={palette.steel} metalness={0.6} roughness={0.3} />
          </mesh>
        ))}
        <group ref={rig.load} position={[0, 0, CARRY_OFFSET]} visible={false}>
          <PalletModel packs={packs} wrapped />
        </group>
      </group>
    </group>
  );
}

function PickableForklift({ rig, color, packs, pick }: { rig: Rig; color: string; packs: number; pick: { id: string; name: string; visual: Visual } }) {
  const { palette, selected, hovered } = useTwin();
  const handlers = usePick(pick.id);
  const on = selected === pick.id || hovered === pick.id;
  return (
    <group {...handlers}>
      <ForkliftModel rig={rig} color={color} packs={packs}>
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}>
          <ringGeometry args={[1.25, on ? 1.5 : 1.38, 40]} />
          <meshBasicMaterial color={on ? palette.glow : statusColor(palette, pick.visual.state)} transparent opacity={on ? 0.95 : 0.6} />
        </mesh>
        {needsPin(pick.visual) && selected !== pick.id && <Pin visual={pick.visual} height={3.3} />}
        <FloatLabel id={pick.id} name={pick.name} visual={pick.visual} height={2.9} />
      </ForkliftModel>
    </group>
  );
}

type Active = { route: Route; move: Move; t0: number };

/**
 * Heftruck die echte boekingen naspeelt. Elke nieuwe `move` (een putaway of
 * een laadactie uit de HU-data) wordt een rit; komt er een nieuwe binnen
 * tijdens een rit, dan start die vanaf waar de truck dan staat. Een oudere
 * wachtende rit vervalt; die pallet staat dan gewoon meteen op zijn plek.
 * De eerste waarde na het laden wordt niet gespeeld: dat is geschiedenis.
 */
function RouteForklift({
  move,
  park,
  plan,
  color,
  transit,
  hideUntilDrop,
  pickup,
  name,
  pick,
}: {
  /** Selecteerbaar als heftruck uit de demo-simulatie; zonder sim alleen decor. */
  pick?: { id: string; name: string; visual: Visual } | null;
  move: Move | null;
  park: Pose;
  plan: (from: Pose, m: Move) => Route;
  color: string;
  transit: Transit;
  /** Putaway: de pallet staat al in het vak in de data, toon hem pas als de truck hem neerzet. */
  hideUntilDrop: boolean;
  /** Waar de pallet staat voordat de vork hem pakt; null = daar staat al iets in beeld. */
  pickup: (m: Move) => Vec3 | null;
  name: string;
}) {
  const { reducedMotion } = useTwin();
  const rig: Rig = { root: useRef<Group>(null), inner: useRef<Group>(null), carriage: useRef<Group>(null), load: useRef<Group>(null) };
  const ghost = useRef<Group>(null);
  const seen = useRef<string | null | undefined>(undefined);
  const pending = useRef<Move | null>(null);
  const active = useRef<Active | null>(null);
  const pose = useRef<Pose>(park);
  const [cargo, setCargo] = useState<{ packs: number; at: Vec3 | null }>({ packs: 480, at: null });

  const id = move?.hu.hu_id ?? null;
  useEffect(() => {
    if (seen.current === undefined) {
      seen.current = id;
      return;
    }
    if (!id || id === seen.current || !move) return;
    seen.current = id;
    if (reducedMotion) return;
    const old = pending.current;
    if (old && hideUntilDrop) transit.current.delete(old.hu.hu_id);
    pending.current = move;
    if (hideUntilDrop) transit.current.add(id);
  }, [id, move, reducedMotion, hideUntilDrop, transit]);

  useEffect(
    () => () => {
      // bij unmount niets onzichtbaar achterlaten
      if (pending.current) transit.current.delete(pending.current.hu.hu_id);
      if (active.current) transit.current.delete(active.current.move.hu.hu_id);
    },
    [transit],
  );

  useFrame(({ clock }) => {
    const now = clock.elapsedTime;
    let a = active.current;
    if (a && now - a.t0 >= a.route.duration) {
      transit.current.delete(a.move.hu.hu_id);
      pose.current = a.route.end;
      active.current = a = null;
    }
    if (!a && pending.current) {
      const m = pending.current;
      pending.current = null;
      a = active.current = { route: plan(pose.current, m), move: m, t0: now };
      setCargo({ packs: Number(m.hu.packs_count) || 480, at: pickup(m) });
    }
    let p = pose.current;
    if (a) {
      const k = now - a.t0;
      p = a.route.poseAt(k);
      if (hideUntilDrop && a.route.dropAt != null && k >= a.route.dropAt) transit.current.delete(a.move.hu.hu_id);
      if (ghost.current) ghost.current.visible = a.route.grabAt != null && k < a.route.grabAt;
    } else if (ghost.current) ghost.current.visible = false;
    applyPose(rig, p);
  });

  return (
    <group name={name}>
      {pick ? (
        <PickableForklift rig={rig} color={color} packs={cargo.packs} pick={pick} />
      ) : (
        <ForkliftModel rig={rig} color={color} packs={cargo.packs} />
      )}
      {cargo.at && (
        <group ref={ghost} position={cargo.at} visible={false}>
          <PalletModel packs={cargo.packs} wrapped />
        </group>
      )}
    </group>
  );
}

function forkPick(f: SimForklift | null | undefined) {
  return f ? { id: `forklift:${f.id}`, name: `Heftruck ${f.id}`, visual: forkliftVisual(f) } : null;
}

const putawayPlan = (from: Pose, m: Move) => putawayRoute(from, m.slot);
const loadPlan = (from: Pose, m: Move) => loadRoute(from, m.slot, m.dock ?? 0);
const slotPickup = (m: Move): Vec3 => rackSlotPosition(m.slot);

/** Heftruck palletiser → stelling: speelt elke putaway uit de HU-data na. */
export function PutawayForklift({ logistics, transit, sim }: { logistics: Logistics; transit: Transit; sim?: SimForklift | null }) {
  const { palette } = useTwin();
  const turntableBusy = logistics.palletiser.length > 0;
  const pickup = useCallback(
    (): Vec3 | null => (turntableBusy ? null : [PALLETISER_PICK[0], 0.16, PALLETISER_PICK[1]]),
    [turntableBusy],
  );
  return (
    <RouteForklift
      name="forklift-putaway"
      pick={forkPick(sim)}
      move={logistics.lastPutaway}
      park={PARK_PUTAWAY}
      plan={putawayPlan}
      color={palette.dock}
      transit={transit}
      hideUntilDrop
      pickup={pickup}
    />
  );
}

/** Heftruck stelling → truck: speelt elke laadactie (ship) na, van het vak naar de dock. */
export function LoaderForklift({ logistics, transit, sim }: { logistics: Logistics; transit: Transit; sim?: SimForklift | null }) {
  const { palette } = useTwin();
  return (
    <RouteForklift
      name="forklift-loader"
      pick={forkPick(sim)}
      move={logistics.lastLoad}
      park={PARK_LOADER}
      plan={loadPlan}
      color={palette["truck-cab"]}
      transit={transit}
      hideUntilDrop={false}
      pickup={slotPickup}
    />
  );
}
