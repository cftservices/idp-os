"use client";

/**
 * Echte 3D-modellen (Poly Haven, CC0) als decor in de hal. Elk model wordt
 * genormaliseerd: onderkant op y=0, midden op x/z=0, in meters.
 */
import { Suspense, useMemo } from "react";
import { Clone, useGLTF } from "@react-three/drei";
import { Box3, Vector3 } from "three";

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
}
