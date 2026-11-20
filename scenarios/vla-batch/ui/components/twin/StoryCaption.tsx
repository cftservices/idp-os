"use client";

import type { StoryStop } from "@/lib/twin/story";
import type { TwinModel } from "@/lib/twin/useTwinData";

/**
 * Caption of story mode: what the camera is looking at, what happens there and the live status.
 * A normal DOM card, re-rendered with each poll; not inside the 3D scene, so it costs nothing
 * per frame (see the bench test notes in broadcast/README.md).
 */
export function StoryCaption({
  stop,
  index,
  total,
  model,
}: {
  stop: StoryStop;
  index: number;
  total: number;
  model: TwinModel;
}) {
  const lines = stop.live(model);
  return (
    <aside className="wt-story wt-glass" data-twin-story={stop.id} aria-live="polite" key={stop.id}>
      <p className="wt-story-step">
        {index + 1} / {total}
      </p>
      <h2 className="wt-story-title">{stop.title}</h2>
      <p className="wt-story-text">{stop.text}</p>
      {lines.length > 0 && (
        <dl className="wt-story-live">
          {lines.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </aside>
  );
}
