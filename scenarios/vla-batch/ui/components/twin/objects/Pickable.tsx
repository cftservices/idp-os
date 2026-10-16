"use client";

import { useRef, type ReactNode } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import type { Mesh } from "three";
import type { Vec3 } from "@/lib/twin/layout";
import type { Visual } from "@/lib/twin/derive";
import { statusColor, useTwin } from "../context";

/**
 * Elk klikbaar object hangt hieronder. Eén plek voor hover, selectie, de
 * statusring op de vloer en het label. Het label verschijnt altijd bij een
 * afwijkende status (alarm, aandacht, verouderd), en verder alleen bij hover
 * of selectie: een diorama vol tekstballonnen is geen overzicht meer.
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
  const { palette, selected, hovered, select, hover, reducedMotion } = useTwin();
  const ring = useRef<Mesh>(null);
  const isSel = selected === id;
  const isHover = hovered === id;
  const loud = visual.state === "alarm" || visual.state === "warn" || visual.state === "stale";
  const color = statusColor(palette, visual.state);

  useFrame(({ clock }) => {
    if (!ring.current || reducedMotion) return;
    const s = visual.state === "alarm" ? 1 + Math.sin(clock.elapsedTime * 5) * 0.08 : 1;
    ring.current.scale.set(s, s, 1);
  });

  function onClick(e: ThreeEvent<MouseEvent>) {
    e.stopPropagation();
    select(isSel ? null : id);
  }
  function onOver(e: ThreeEvent<PointerEvent>) {
    e.stopPropagation();
    hover(id);
    document.body.style.cursor = "pointer";
  }
  function onOut() {
    hover(null);
    document.body.style.cursor = "";
  }

  return (
    <group position={position} onClick={onClick} onPointerOver={onOver} onPointerOut={onOut}>
      <group rotation={[0, rotationY, 0]}>{children}</group>
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}>
        <ringGeometry args={[radius, radius + (isSel ? 0.32 : 0.18), 48]} />
        <meshBasicMaterial color={isSel || isHover ? palette.glow : color} transparent opacity={isSel ? 0.95 : 0.75} />
      </mesh>
      {(loud || isSel || isHover) && (
        <Html position={[0, labelHeight, 0]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
          <div className="twin-label" data-twin-label={id} data-state={visual.state}>
            <span className="twin-label-shape" style={{ color }} aria-hidden="true">
              {visual.state === "unknown" ? "" : shapeOf(visual)}
            </span>
            <span className="twin-label-name">{name}</span>
            <span className="twin-label-state">{visual.label}</span>
          </div>
        </Html>
      )}
    </group>
  );
}

function shapeOf(v: Visual) {
  return v.shape;
}
