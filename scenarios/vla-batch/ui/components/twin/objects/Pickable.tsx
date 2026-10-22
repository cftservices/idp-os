"use client";

import { useRef, type ReactNode } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import { AdditiveBlending, type Group, type Mesh } from "three";
import type { Vec3 } from "@/lib/twin/layout";
import type { Visual } from "@/lib/twin/derive";
import { needsPin } from "@/lib/twin/flow";
import { statusColor, useTwin } from "../context";

/** Klik- en hoverhandlers voor alles wat selecteerbaar is, ook bewegende objecten. */
export function usePick(id: string) {
  const { selected, select, hover } = useTwin();
  return {
    onClick(e: ThreeEvent<MouseEvent>) {
      e.stopPropagation();
      select(selected === id ? null : id);
    },
    onPointerOver(e: ThreeEvent<PointerEvent>) {
      e.stopPropagation();
      hover(id);
      document.body.style.cursor = "pointer";
    },
    onPointerOut() {
      hover(null);
      document.body.style.cursor = "";
    },
  };
}

/** Zwevend label "Naam · procesregel · status", alleen binnen het labelbudget. */
export function FloatLabel({ id, name, visual, height }: { id: string; name: string; visual: Visual; height: number }) {
  const { palette, selected, labelled, meta } = useTwin();
  if (!labelled.has(id)) return null;
  const m = meta[id];
  return (
    <Html position={[0, height, 0]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
      <div className="twin-label" data-twin-label={id} data-state={visual.state} data-selected={selected === id ? "" : undefined}>
        {visual.state !== "unknown" && (
          <span className="twin-label-shape" style={{ color: selected === id ? undefined : statusColor(palette, visual.state) }} aria-hidden="true">
            {visual.shape}
          </span>
        )}
        <span className="twin-label-name">{name}</span>
        {m && m !== visual.label ? <span className="twin-label-meta">{m}</span> : null}
        <span className="twin-label-state">{visual.label}</span>
      </div>
    </Html>
  );
}

/** Stuiterende speld boven wat aandacht vraagt; in de statuskleur, met vorm via het label. */
export function Pin({ visual, height }: { visual: Visual; height: number }) {
  const { palette, reducedMotion } = useTwin();
  const g = useRef<Group>(null);
  const color = statusColor(palette, visual.state);
  useFrame(({ clock }) => {
    if (!g.current || reducedMotion) return;
    const t = clock.elapsedTime * (visual.state === "alarm" ? 4 : 2.6);
    g.current.position.y = height + Math.abs(Math.sin(t)) * 0.45;
    g.current.rotation.y = clock.elapsedTime * 1.2;
  });
  return (
    <group ref={g} position={[0, height, 0]}>
      <mesh position={[0, 0.55, 0]}>
        <sphereGeometry args={[0.32, 20, 16]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.55} roughness={0.35} />
      </mesh>
      <mesh position={[0, 0.12, 0]} rotation={[Math.PI, 0, 0]}>
        <coneGeometry args={[0.2, 0.55, 16]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.35} roughness={0.35} />
      </mesh>
    </group>
  );
}

/**
 * Elk klikbaar object hangt hieronder. Eén plek voor hover (optillen en
 * gloeiende ring), selectie (lichtbundel), de statusring op de vloer, de
 * speld en het label. Welke labels zichtbaar zijn, beslist labelBudget.
 */
export function Pickable({
  id,
  name,
  visual,
  position,
  radius = 1.6,
  labelHeight = 4,
  rotationY = 0,
  children,
}: {
  id: string;
  name: string;
  visual: Visual;
  position: Vec3;
  radius?: number;
  labelHeight?: number;
  rotationY?: number;
  children: ReactNode;
}) {
  const { palette, selected, hovered, reducedMotion } = useTwin();
  const handlers = usePick(id);
  const ring = useRef<Mesh>(null);
  const body = useRef<Group>(null);
  const beam = useRef<Mesh>(null);
  const isSel = selected === id;
  const isHover = hovered === id;
  const color = statusColor(palette, visual.state);
  const r = Math.max(radius, 0.9);

  useFrame(({ clock }, dt) => {
    const t = clock.elapsedTime;
    if (ring.current) {
      const s = !reducedMotion && (visual.state === "alarm" || isSel) ? 1 + Math.sin(t * (isSel ? 3 : 5)) * 0.07 : 1;
      ring.current.scale.set(s, s, 1);
    }
    if (body.current) {
      const goal = isHover || isSel ? 0.12 : 0;
      const y = body.current.position.y;
      body.current.position.y = reducedMotion ? goal : y + (goal - y) * Math.min(1, dt * 10);
    }
    if (beam.current) {
      const m = beam.current.material as { opacity: number };
      m.opacity = reducedMotion ? 0.16 : 0.12 + Math.sin(t * 2.4) * 0.05;
    }
  });

  return (
    <group position={position} {...handlers}>
      <group ref={body}>
        <group rotation={[0, rotationY, 0]}>{children}</group>
      </group>
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}>
        <ringGeometry args={[r, r + (isSel ? 0.34 : isHover ? 0.26 : 0.16), 56]} />
        <meshBasicMaterial color={isSel || isHover ? palette.glow : color} transparent opacity={isSel ? 0.95 : isHover ? 0.9 : 0.7} />
      </mesh>
      {(isSel || isHover) && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
          <circleGeometry args={[r + 0.3, 48]} />
          <meshBasicMaterial color={palette.glow} transparent opacity={isSel ? 0.16 : 0.1} depthWrite={false} />
        </mesh>
      )}
      {isSel && (
        <mesh ref={beam} position={[0, labelHeight / 2, 0]}>
          <cylinderGeometry args={[r * 0.9, r + 0.3, labelHeight, 40, 1, true]} />
          <meshBasicMaterial color={palette.glow} transparent opacity={0.14} depthWrite={false} blending={AdditiveBlending} side={2} />
        </mesh>
      )}
      {needsPin(visual) && !isSel && <Pin visual={visual} height={labelHeight + 1.1} />}
      <FloatLabel id={id} name={name} visual={visual} height={labelHeight} />
    </group>
  );
}
