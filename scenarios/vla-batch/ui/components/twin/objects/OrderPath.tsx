"use client";

import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Html, Line } from "@react-three/drei";
import type { Vec3 } from "@/lib/twin/layout";
import { orderPathPoints, orderStops, type OrderLike } from "@/lib/twin/orderPath";
import type { TwinModel } from "@/lib/twin/useTwinData";
import { useTwin } from "../context";

const Y = 0.35;

/**
 * De weg van een order door de fabriek als bewegende stippellijn, met een
 * genummerde marker per halte. De getallen per halte staan in de orderkaart,
 * zodat ze niet met de objectlabels botsen.
 */
export function OrderPath({ order, model }: { order: OrderLike; model: TwinModel }) {
  const { palette, reducedMotion } = useTwin();
  const stops = orderStops(order, model);
  const line = useRef<{ material: { dashOffset: number } } | null>(null);
  const dockX = stops.find((s) => s.key === "dock")?.at[0] ?? 0;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const points = useMemo(() => orderPathPoints(stops).map(([x, , z]) => [x, Y, z] as Vec3), [dockX]);

  useFrame((_, dt) => {
    if (reducedMotion || !line.current) return;
    line.current.material.dashOffset -= dt * 1.6;
  });

  return (
    <group>
      <Line
        ref={line as never}
        points={points}
        color={palette.glow}
        lineWidth={3}
        dashed
        dashSize={0.9}
        gapSize={0.5}
        transparent
        opacity={0.9}
        depthTest={false}
        renderOrder={10}
      />
      {stops.map((s, i) => (
        <Html key={s.key} position={[s.at[0], 0.6, s.at[2]]} center zIndexRange={[15, 0]} style={{ pointerEvents: "none" }}>
          <span className="wt-path-dot" data-twin-path-stop={s.key} title={`${s.label}: ${s.value}`}>
            {i + 1}
          </span>
        </Html>
      ))}
    </group>
  );
}
