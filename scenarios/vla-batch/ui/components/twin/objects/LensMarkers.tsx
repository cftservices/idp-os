"use client";

import { Html } from "@react-three/drei";
import type { Vec3 } from "@/lib/twin/layout";
import { QUALITY_WORD, type Quality } from "@/lib/twin/datalayer";

export type LensMarker = {
  id: string;
  name: string;
  pos: Vec3;
  quality: Quality;
  wo: string | null;
  down: boolean;
};

/**
 * De datalaag-lens in 3D: boven elke machine hoe zijn signalen ervoor staan,
 * en of er een werkorder op loopt. Bewust klein; de detailkaart vertelt de rest.
 */
export function LensMarkers({ markers, onSelect }: { markers: LensMarker[]; onSelect: (id: string) => void }) {
  return (
    <>
      {markers.map((m) => (
        <Html key={m.id} position={[m.pos[0], m.pos[1] + 7.6, m.pos[2]]} center zIndexRange={[18, 0]}>
          <button
            type="button"
            className="wt-lens"
            data-q={m.down ? "BAD" : m.quality}
            onClick={() => onSelect(`equipment:${m.id}`)}
            data-twin-lens={m.id}
          >
            <span className="wt-dl-dot" aria-hidden="true" />
            <strong>{m.name}</strong>
            <span className="wt-lens-q">{m.down ? "Down" : QUALITY_WORD[m.quality]}</span>
            {m.wo && <span className="mono"> · {m.wo}</span>}
          </button>
        </Html>
      ))}
    </>
  );
}
