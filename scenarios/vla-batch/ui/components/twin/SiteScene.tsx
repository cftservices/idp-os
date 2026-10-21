"use client";

import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Environment, Lightformer, OrbitControls } from "@react-three/drei";
import { Vector3 } from "three";
import { CAMERA_HOME, CAMERA_TARGET, LINE_EQUIPMENT, MAX_DOCKS, type Vec3 } from "@/lib/twin/layout";
import type { TwinModel } from "@/lib/twin/useTwinData";
import { TwinContext, type TwinCtx } from "./context";
import { EquipmentObject, ProcessPipe } from "./objects/Equipment";
import {
  ColdStore,
  DepartingTruck,
  DockTruck,
  EmptyDock,
  LoaderForklift,
  Palletiser,
  PutawayForklift,
  SimDepartingTruck,
  Silo,
  Tanker,
} from "./objects/Logistics";
import { SiteGround } from "./objects/Site";
import { preloadProps } from "./models";

if (typeof window !== "undefined") preloadProps();
import { positionOf } from "./selection";

const ACTIVE_STATES = new Set(["DOSING", "COOKING", "COOLING", "FILLING"]);

/**
 * Camera: zachtjes naar een geselecteerd object, terug naar huis bij een
 * reset. Alleen tijdens die overgang; daarna heeft de gebruiker de camera.
 */
// Thuispositie gekalibreerd op een breed beeld; een smaller (staand) beeld
// schuift de camera verder terug zodat de hele site in beeld blijft.
const HOME_ASPECT = 1.9;
function homePosition(aspect: number): Vector3 {
  const t = new Vector3(...CAMERA_TARGET);
  const k = Math.min(2.2, Math.max(1, HOME_ASPECT / Math.max(aspect, 0.1)));
  return new Vector3(...CAMERA_HOME).sub(t).multiplyScalar(k).add(t);
}

function CameraRig({ focus, resetSignal, zoom }: { focus: Vec3 | null; resetSignal: number; zoom: { n: number; dir: 1 | -1 } }) {
  const controls = useThree((s) => s.controls) as unknown as { target: Vector3; update: () => void } | null;
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const goal = useRef<{ target: Vector3; pos: Vector3 | null; until: number } | null>(null);
  const placed = useRef(false);

  useEffect(() => {
    if (placed.current || size.width === 0 || size.height === 0) return;
    placed.current = true;
    camera.position.copy(homePosition(size.width / size.height));
    controls?.update();
  }, [size, camera, controls]);

  useEffect(() => {
    if (!focus) return;
    goal.current = { target: new Vector3(...focus), pos: null, until: performance.now() + 900 };
  }, [focus]);

  useEffect(() => {
    if (resetSignal === 0) return;
    goal.current = {
      target: new Vector3(...CAMERA_TARGET),
      pos: homePosition(size.width / Math.max(size.height, 1)),
      until: performance.now() + 1100,
    };
  }, [resetSignal]);

  useEffect(() => {
    if (zoom.n === 0 || !controls) return;
    const t = controls.target.clone();
    const off = camera.position.clone().sub(t);
    const len = Math.min(200, Math.max(18, off.length() * (zoom.dir > 0 ? 0.78 : 1.28)));
    goal.current = { target: t, pos: t.clone().add(off.setLength(len)), until: performance.now() + 700 };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoom.n]);

  useFrame(() => {
    const g = goal.current;
    if (!g || !controls) return;
    controls.target.lerp(g.target, 0.12);
    if (g.pos) camera.position.lerp(g.pos, 0.12);
    controls.update();
    if (performance.now() > g.until) goal.current = null;
  });
  return null;
}

export default function SiteScene({
  model,
  ctx,
  presenting,
  resetSignal,
  zoom,
}: {
  model: TwinModel;
  ctx: TwinCtx;
  presenting: boolean;
  resetSignal: number;
  zoom: { n: number; dir: 1 | -1 };
}) {
  const { palette, reducedMotion } = ctx;
  const flowing = model.batch ? ACTIVE_STATES.has(model.batch.state) : false;
  // Alleen bij een nieuwe selectie: anders trekt elke poll de camera terug.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const focus = useMemo(() => (ctx.selected ? positionOf(ctx.selected, model) : null), [ctx.selected]);
  const xs = LINE_EQUIPMENT.map((e) => e.pos[0]);
  const lv = model.logisticsVisual;
  const transit = useRef(new Set<string>());

  return (
    <Canvas
      shadows
      dpr={[1, 2]}
      frameloop={reducedMotion && !presenting ? "demand" : "always"}
      camera={{ position: CAMERA_HOME, fov: 32, near: 1, far: 400 }}
      onPointerMissed={() => ctx.select(null)}
      gl={{ antialias: true, preserveDrawingBuffer: true }}
      aria-label="3D-weergave van de site"
      data-twin-canvas=""
    >
      <TwinContext.Provider value={ctx}>
        <color attach="background" args={[palette.sky]} />
        <fog attach="fog" args={[palette.sky, 120, 320]} />
        <hemisphereLight args={[palette["roof-edge"], palette.ground, 1.1]} />
        <ambientLight intensity={0.45} />
        {/* Studio-reflecties voor RVS en lak: alleen lichtvlakken, geen HDR-download. */}
        <Environment resolution={256} environmentIntensity={0.8}>
          <Lightformer form="rect" intensity={2.5} position={[0, 12, 0]} rotation-x={Math.PI / 2} scale={[40, 12, 1]} />
          <Lightformer form="rect" intensity={1.2} position={[-30, 6, 0]} rotation-y={Math.PI / 2} scale={[30, 6, 1]} />
          <Lightformer form="rect" intensity={1.2} position={[30, 6, 0]} rotation-y={-Math.PI / 2} scale={[30, 6, 1]} />
        </Environment>
        <directionalLight
          position={[-22, 42, 28]}
          intensity={2.4}
          castShadow
          shadow-mapSize={[2048, 2048]}
          shadow-bias={-0.0004}
          shadow-radius={6}
          shadow-camera-left={-50}
          shadow-camera-right={50}
          shadow-camera-top={34}
          shadow-camera-bottom={-34}
        />

        <SiteGround />

        {model.silos.map((s) => (
          <Silo key={s.material.material_id} silo={s} />
        ))}

        {model.equipment.map((eq) => (
          <EquipmentObject key={eq.id} eq={eq} batchState={model.batch?.state ?? null} />
        ))}
        {xs.slice(0, -1).map((x, i) => (
          <ProcessPipe key={x} from={x + 1.2} to={xs[i + 1] - 1.2} flowing={flowing} />
        ))}

        <Palletiser logistics={model.logistics} visual={lv.palletiser} />
        <PutawayForklift logistics={model.logistics} transit={transit} sim={model.sim?.forklifts.find((f) => f.role === "putaway")} />
        <LoaderForklift logistics={model.logistics} transit={transit} sim={model.sim?.forklifts.find((f) => f.role === "loader")} />
        <ColdStore logistics={model.logistics} visual={lv.coldstore} transit={transit} />

        {Array.from({ length: MAX_DOCKS }, (_, i) => {
          const slot = model.logistics.docks.find((d) => d.dock === i);
          return slot ? <DockTruck key={slot.batch_id} slot={slot} visual={lv.dock} /> : <EmptyDock key={`e${i}`} index={i} />;
        })}
        {model.sim
          ? model.sim.shipments
              .filter((s) => s.stage === "en_route")
              .slice(0, 3)
              .map((s, i) => <SimDepartingTruck key={s.shipment_id} s={s} lane={i} />)
          : model.logistics.departures.slice(0, 3).map((d, i) => (
              <DepartingTruck key={d.batch_id} dep={d} index={i} visual={lv.truck} />
            ))}
        {model.sim?.inbounds.map((i) => <Tanker key={i.material_id ?? "milk"} inbound={i} />)}

        <OrbitControls
          makeDefault
          target={CAMERA_TARGET}
          enableDamping
          minDistance={18}
          maxDistance={200}
          maxPolarAngle={1.3}
          autoRotate={presenting && !reducedMotion}
          autoRotateSpeed={0.6}
        />
        <CameraRig focus={focus} resetSignal={resetSignal} zoom={zoom} />
      </TwinContext.Provider>
    </Canvas>
  );
}
