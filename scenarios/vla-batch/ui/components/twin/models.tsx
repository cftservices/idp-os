"use client";

/**
 * Echte 3D-modellen (Poly Haven, CC0) als decor in de hal. Elk model wordt
 * genormaliseerd: onderkant op y=0, midden op x/z=0, in meters.
 */
import { Suspense, useMemo } from "react";
import { Clone, useGLTF } from "@react-three/drei";
import { Box3, Vector3, type BufferGeometry, type Material, type Matrix4, type Mesh } from "three";

export type PropName = "blusser" | "doos" | "gereedschapkar" | "kist" | "krat" | "schakelkast" | "vat";
const NAMES: PropName[] = ["blusser", "doos", "gereedschapkar", "kist", "krat", "schakelkast", "vat"];
const path = (n: PropName) => `/models/site/${n}.glb`;

function PropInner({ name, scale = 1 }: { name: PropName; scale?: number }) {
  const { scene } = useGLTF(path(name));
  const offset = useMemo(() => {
    const b = new Box3().setFromObject(scene);
    const c = b.getCenter(new Vector3());
    return [-c.x * scale, -b.min.y * scale, -c.z * scale] as [number, number, number];
  }, [scene, scale]);
  return <Clone object={scene} position={offset} scale={scale} castShadow receiveShadow />;
}

export function Prop({
  name,
  p = [0, 0, 0],
  r = 0,
  scale,
}: {
  name: PropName;
  p?: [number, number, number];
  r?: number;
  scale?: number;
}) {
  return (
    <group position={p} rotation={[0, r, 0]}>
      <Suspense fallback={null}>
        <PropInner name={name} scale={scale} />
      </Suspense>
    </group>
  );
}

export function preloadProps() {
  NAMES.forEach((n) => useGLTF.preload(path(n)));
  OIP_FILES.forEach((n) => useGLTF.preload(oipPath(n)));
}

/**
 * Modellen uit Open Industry Project (MIT, zie public/models/oip/CREDITS.md).
 * Die worden niet als geheel geplaatst maar per onderdeel: een robotarm en een
 * dockleveller hebben scharnieren, en die bouwen we met eigen groepen. De
 * knoop zelf verplaatsen we nooit, want meshopt heeft de dequantisatie in de
 * knooptransformatie gebakken; we nemen hem over als vaste matrix.
 */
export type OipFile = "robot" | "dockdeur" | "leveller" | "trailer";
const OIP_FILES: OipFile[] = ["robot", "dockdeur", "leveller", "trailer"];
const oipPath = (n: OipFile) => `/models/oip/${n}.glb`;

export type OipPart = { geometry: BufferGeometry; material: Material; matrix: Matrix4 };

/** Per glTF-knoop de meshes; een knoop met meerdere primitieven levert er meer dan één. */
export function useOipParts(file: OipFile): Record<string, OipPart[]> {
  const { scene } = useGLTF(oipPath(file));
  return useMemo(() => {
    scene.updateMatrixWorld(true);
    const parts: Record<string, OipPart[]> = {};
    scene.traverse((o) => {
      const m = o as Mesh;
      if (!m.isMesh) return;
      const name = m.parent && m.parent !== scene ? m.parent.name : m.name;
      (parts[name] ??= []).push({ geometry: m.geometry, material: m.material as Material, matrix: m.matrixWorld.clone() });
    });
    return parts;
  }, [scene]);
}

/** Eén knoop als losse meshes met de gebakken matrix, onder een eigen groep die mag bewegen. */
export function OipMesh({ parts, shadow = true }: { parts?: OipPart[]; shadow?: boolean }) {
  if (!parts) return null;
  return (
    <>
      {parts.map((p, i) => (
        <mesh
          key={i}
          geometry={p.geometry}
          material={p.material}
          matrix={p.matrix}
          matrixAutoUpdate={false}
          castShadow={shadow}
          receiveShadow
        />
      ))}
    </>
  );
}
