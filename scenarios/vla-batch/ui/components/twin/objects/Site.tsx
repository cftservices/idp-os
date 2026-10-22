"use client";

import { Html } from "@react-three/drei";
import { Vector2 } from "three";
import { HALL, HALL2_Z, PREP_ROOM, ROAD_X, ROAD_Z } from "@/lib/twin/layout";
import { useTwin } from "../context";
import { usePbr } from "../materials";
import { Prop } from "../models";

const FLOOR_NS = new Vector2(0.6, 0.6);
const WALL_NS = new Vector2(1, 1);

/** Staalconstructie: kolommen langs de achterwand en een ligger erover. */
function Frame({ x0, x1, z0 }: { x0: number; x1: number; z0: number }) {
  const { palette } = useTwin();
  const h = HALL.wall * 2.2;
  const cols = Array.from({ length: Math.floor((x1 - x0) / 4.5) + 1 }, (_, i) => x0 + 0.4 + i * 4.5);
  return (
    <group>
      {cols.map((x) => (
        <group key={x} position={[x, 0, z0 + 0.3]}>
          <mesh position={[0, h / 2, 0]} castShadow>
            <boxGeometry args={[0.22, h, 0.22]} />
            <meshStandardMaterial color={palette["steel-dark"]} metalness={0.6} roughness={0.45} />
          </mesh>
          <mesh position={[0, 0.06, 0]}>
            <boxGeometry args={[0.4, 0.12, 0.4]} />
            <meshStandardMaterial color={palette["hall-roof"]} roughness={0.9} />
          </mesh>
          {/* gele aanrijdbeveiliging */}
          <mesh position={[0, 0.45, 0.16]}>
            <boxGeometry args={[0.3, 0.9, 0.06]} />
            <meshStandardMaterial color={palette.accent} roughness={0.5} />
          </mesh>
        </group>
      ))}
      <mesh position={[(x0 + x1) / 2, h - 0.15, z0 + 0.3]} castShadow>
        <boxGeometry args={[x1 - x0, 0.3, 0.2]} />
        <meshStandardMaterial color={palette["steel-dark"]} metalness={0.6} roughness={0.45} />
      </mesh>
      {/* kabelgoot langs de wand */}
      <mesh position={[(x0 + x1) / 2, h - 0.6, z0 + 0.55]}>
        <boxGeometry args={[x1 - x0 - 0.6, 0.08, 0.3]} />
        <meshStandardMaterial color={palette.steel} metalness={0.7} roughness={0.35} />
      </mesh>
    </group>
  );
}

/** CC0-decor: brandblussers, schakelkasten, karren, vaten en kratten. */
function HallProps({ x0, x1, z0, z1 }: { x0: number; x1: number; z0: number; z1: number }) {
  const back = z0 + 0.55;
  return (
    <group>
      {[-12.6, -3.5, 6.2].map((x) => (
        <Prop key={`b${x}`} name="blusser" p={[x, 0.1, back]} />
      ))}
      <Prop name="schakelkast" p={[-8.6, 0.1, back + 0.1]} />
      <Prop name="schakelkast" p={[1.8, 0.1, back + 0.1]} />
      <Prop name="schakelkast" p={[11.2, 0.1, back + 0.1]} />
      <Prop name="gereedschapkar" p={[-3.6, 0.1, -3.1]} r={0.4} />
      <Prop name="gereedschapkar" p={[6.6, 0.1, -3.2]} r={-0.3} />
      {/* grondstoffen bij de ontvangst: vaten cacao/suikerstroop */}
      <Prop name="vat" p={[x0 + 0.8, 0.1, -3.6]} />
      <Prop name="vat" p={[x0 + 1.6, 0.1, -3.9]} r={1} />
      <Prop name="vat" p={[x0 + 0.9, 0.1, -2.7]} r={2} />
      <Prop name="kist" p={[x0 + 1.2, 0.1, 3.8]} r={0.2} />
      {/* lege kratten en dozen bij de vuller */}
      <Prop name="krat" p={[x1 - 2.6, 0.1, -3.4]} />
      <Prop name="krat" p={[x1 - 1.8, 0.1, -3.6]} r={0.5} />
      <Prop name="doos" p={[x1 - 2.2, 0.1, z1 - 1.2]} r={0.3} />
    </group>
  );
}

/**
 * Voorbereidingsruimte west van de hallen, op het oude siloplein: vloer,
 * lage wanden en bord. De
 * stations zelf (klikbaar, met status) staan in Prep.tsx.
 */
function PrepRoomShell() {
  const { palette } = useTwin();
  const [x0, z0] = PREP_ROOM.min;
  const [x1, z1] = PREP_ROOM.max;
  const floor = usePbr("beton", [(x1 - x0) / 4, (z1 - z0) / 4]);
  return (
    <group>
      <mesh position={[(x0 + x1) / 2, 0.05, (z0 + z1) / 2]} receiveShadow>
        <boxGeometry args={[x1 - x0, 0.1, z1 - z0]} />
        <meshStandardMaterial
          key={floor.map ? "t" : "f"}
          color={palette["ground-line"]}
          map={floor.map}
          normalMap={floor.normalMap}
          normalScale={FLOOR_NS}
          roughnessMap={floor.roughnessMap}
          roughness={0.85}
        />
      </mesh>
      {/* lage wanden west, noord en zuid; oost open naar de hallen voor de premixleidingen */}
      <mesh position={[x0, PREP_ROOM.wall / 2, (z0 + z1) / 2]} castShadow receiveShadow>
        <boxGeometry args={[0.25, PREP_ROOM.wall, z1 - z0]} />
        <meshStandardMaterial color={palette["prep-room"]} metalness={0.3} roughness={0.6} />
      </mesh>
      {[z0, z1].map((z) => (
        <mesh key={z} position={[(x0 + x1) / 2, PREP_ROOM.wall / 2, z]} castShadow receiveShadow>
          <boxGeometry args={[x1 - x0, PREP_ROOM.wall, 0.25]} />
          <meshStandardMaterial color={palette["prep-room"]} metalness={0.3} roughness={0.6} />
        </mesh>
      ))}
      {/* hygiënezone: lijn op de vloer */}
      <mesh position={[(x0 + x1) / 2, 0.105, (z0 + z1) / 2]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[x1 - x0 - 0.6, 0.08]} />
        <meshBasicMaterial color={palette.accent} />
      </mesh>
      <Html position={[(x0 + x1) / 2, PREP_ROOM.wall + 1.6, z0]} center transform distanceFactor={18} style={{ pointerEvents: "none" }}>
        <div className="twin-sign">Voorbereiding · afwegen + premix</div>
      </Html>
    </group>
  );
}

/**
 * Eén productiehal: vloer, lage wanden, staalframe, decor en bord. Getekend
 * in de coördinaten van hal 1 en met `dz` naar het noorden geschoven voor
 * hal 2, zodat beide hallen exact dezelfde opbouw hebben.
 */
function Hall({ dz = 0, sign }: { dz?: number; sign: string }) {
  const { palette } = useTwin();
  const [x0, z0] = HALL.min;
  const [x1, z1] = HALL.max;
  const floor = usePbr("beton", [(x1 - x0) / 4, (z1 - z0) / 4]);
  const wallBack = usePbr("golfplaat", [(x1 - x0) / 2.5, 1.2]);
  const wallSide = usePbr("golfplaat", [(z1 - z0) / 2.5, 0.6]);
  return (
    <group position={[0, 0, dz]}>
      {/* productiehal: vloer en lage wanden, een open diorama */}
      <mesh position={[(x0 + x1) / 2, 0.05, (z0 + z1) / 2]} receiveShadow>
        <boxGeometry args={[x1 - x0, 0.1, z1 - z0]} />
        <meshStandardMaterial
          key={floor.map ? "t" : "f"}
          color={palette["ground-line"]}
          map={floor.map}
          normalMap={floor.normalMap}
          normalScale={FLOOR_NS}
          roughnessMap={floor.roughnessMap}
          roughness={0.85}
        />
      </mesh>
      <mesh position={[(x0 + x1) / 2, (HALL.wall * 2.2) / 2, z0]} castShadow receiveShadow>
        <boxGeometry args={[x1 - x0, HALL.wall * 2.2, 0.3]} />
        <meshStandardMaterial
          key={wallBack.map ? "t" : "f"}
          color={palette.hall}
          map={wallBack.map}
          normalMap={wallBack.normalMap}
          normalScale={WALL_NS}
          roughnessMap={wallBack.roughnessMap}
          metalness={0.35}
          roughness={0.6}
        />
      </mesh>
      <mesh position={[x0, HALL.wall / 2, (z0 + z1) / 2]} castShadow receiveShadow>
        <boxGeometry args={[0.3, HALL.wall, z1 - z0]} />
        <meshStandardMaterial
          key={wallSide.map ? "t" : "f"}
          color={palette.hall}
          map={wallSide.map}
          normalMap={wallSide.normalMap}
          normalScale={WALL_NS}
          roughnessMap={wallSide.roughnessMap}
          metalness={0.35}
          roughness={0.6}
        />
      </mesh>
      <Frame x0={x0} x1={x1} z0={z0} />
      <HallProps x0={x0} x1={x1} z0={z0} z1={z1} />
      {/* gele vakmarkering rond de proceslijn */}
      {[-1.9, 1.9].map((z) => (
        <mesh key={z} position={[(x0 + x1) / 2 - 1, 0.105, z]} rotation={[-Math.PI / 2, 0, 0]}>
          <planeGeometry args={[x1 - x0 - 4, 0.1]} />
          <meshBasicMaterial color={palette.accent} />
        </mesh>
      ))}
      <mesh position={[(x0 + x1) / 2, HALL.wall * 1.65, z0 + 0.3]}>
        <boxGeometry args={[x1 - x0 + 0.4, 0.2, 0.8]} />
        <meshStandardMaterial color={palette["roof-edge"]} roughness={0.7} />
      </mesh>
      {/* looplijn op de vloer */}
      <mesh position={[(x0 + x1) / 2, 0.11, 2.4]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[x1 - x0 - 1, 0.12]} />
        <meshBasicMaterial color={palette["road-mark"]} />
      </mesh>

      <Html position={[-6, HALL.wall * 2.4, z0]} center transform distanceFactor={18} style={{ pointerEvents: "none" }}>
        <div className="twin-sign">{sign}</div>
      </Html>
    </group>
  );
}

/** Het decor: grond, weg, beide hallen, bomen en de borden. Niets hiervan is klikbaar. */
export function SiteGround() {
  const { palette } = useTwin();
  const roadLen = ROAD_X[1] - ROAD_X[0];
  const road = usePbr("beton", [roadLen / 6, 1]);
  // Bomen ten noorden van hal 2; de strook tussen de hallen is voor de voorbereiding.
  const trees: Array<[number, number, number]> = [
    [-28, -9, 1.2], [-25, -12, 0.9], [-30, 4, 1.1], [-27, 8, 0.8], [-8, -26, 1], [2, -27, 1.2],
    [10, -26, 0.9], [34, -10, 1.1], [37, -4, 0.9], [36, 6, 1.2], [-18, 9, 1], [-4, 9.5, 0.8],
    [6, 9.6, 1], [40, 18, 1.1], [-20, 18, 1.2], [0, 18.5, 0.9], [18, 19, 1],
    [-25, -20, 1], [34, -20, 1.1],
  ];
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[140, 90]} />
        <meshStandardMaterial color={palette.ground} />
      </mesh>
      {/* stoeprand langs de weg: witte band zoals in de WareTrack-look */}
      {[-1.1, 3.9].map((dz) => (
        <mesh key={dz} position={[(ROAD_X[0] + ROAD_X[1]) / 2, 0.06, ROAD_Z + dz]} receiveShadow>
          <boxGeometry args={[roadLen, 0.12, 0.25]} />
          <meshStandardMaterial color={palette["roof-edge"]} roughness={0.8} />
        </mesh>
      ))}

      {/* weg met middenstreep */}
      <mesh position={[(ROAD_X[0] + ROAD_X[1]) / 2, 0.02, ROAD_Z + 1.4]} receiveShadow>
        <boxGeometry args={[roadLen, 0.04, 5]} />
        <meshStandardMaterial
          key={road.normalMap ? "t" : "f"}
          color={palette.road}
          normalMap={road.normalMap}
          normalScale={FLOOR_NS}
          roughnessMap={road.roughnessMap}
          roughness={0.95}
        />
      </mesh>
      {Array.from({ length: Math.floor(roadLen / 4) }, (_, i) => (
        <mesh key={i} position={[ROAD_X[0] + 2 + i * 4, 0.05, ROAD_Z + 1.4]} rotation={[-Math.PI / 2, 0, 0]}>
          <planeGeometry args={[2, 0.15]} />
          <meshBasicMaterial color={palette["road-mark"]} />
        </mesh>
      ))}
      {/* oprit naar de docks */}
      <mesh position={[23, 0.02, 8.5]} receiveShadow>
        <boxGeometry args={[15, 0.04, 8]} />
        <meshStandardMaterial color={palette.road} />
      </mesh>

      <Hall sign="Hal 1 · L1 chocolade" />
      <Hall dz={HALL2_Z} sign="Hal 2 · L2 smaken" />
      <PrepRoomShell />

      {trees.map(([x, z, s], i) => (
        <group key={i} position={[x, 0, z]} scale={s}>
          <mesh position={[0, 0.5, 0]} castShadow>
            <cylinderGeometry args={[0.13, 0.17, 1, 8]} />
            <meshStandardMaterial color={palette["tree-trunk"]} roughness={0.9} />
          </mesh>
          {/* ronde speelgoedboom: twee bollen, zacht belicht */}
          <mesh position={[0, 1.65, 0]} castShadow>
            <sphereGeometry args={[0.85, 18, 14]} />
            <meshStandardMaterial color={palette.tree} roughness={0.75} />
          </mesh>
          <mesh position={[0.35, 2.15, 0.2]} castShadow>
            <sphereGeometry args={[0.55, 16, 12]} />
            <meshStandardMaterial color={palette.tree} roughness={0.75} />
          </mesh>
        </group>
      ))}

    </group>
  );
}
