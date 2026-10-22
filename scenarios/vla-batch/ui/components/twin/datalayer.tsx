"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { del, post } from "@/lib/client";
import { num } from "@/lib/format";
import {
  QUALITY_WORD,
  WO_WORD,
  useCause,
  useDataOps,
  useEquipmentContext,
  useEquipmentLosses,
  useLayerMap,
  useReliability,
  useResources,
  useWorkOrders,
  type LayerNode,
  type WorkOrder,
} from "@/lib/twin/datalayer";

/**
 * De datalaag in de twin. Eén paneel met vier lenzen op dezelfde laag:
 * waar de data vandaan komt, wat onderhoud ermee doet, wat het kost, en of
 * je de laag zelf kunt vertrouwen.
 */

export type DlTab = "bronnen" | "onderhoud" | "euros" | "dataops";
export const DL_TABS: Array<[DlTab, string]> = [
  ["bronnen", "Bronnen"],
  ["onderhoud", "Onderhoud"],
  ["euros", "Euro's"],
  ["dataops", "DataOps"],
];

const STATUS_WORD: Record<string, string> = {
  ok: "verbonden",
  warn: "verouderd",
  down: "weg",
  off: "uit",
  unknown: "onbekend",
};

const eur = (v: number | null | undefined) => (v == null ? "—" : `€ ${num(v, 0)}`);
const ago = (s: number | null | undefined) => (s == null ? "—" : s < 60 ? `${num(s, 0)} s` : `${num(s / 60, 0)} min`);

function short(eq: string) {
  return eq.replace(/-0(\d)$/, " $1").replace(/-/g, " ");
}

export function DatalayerPanel({
  tab,
  onTab,
  onSelect,
  onClose,
}: {
  tab: DlTab;
  onTab: (t: DlTab) => void;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <section className="wt-panel wt-dl wt-glass" data-open="" aria-label="De datalaag" data-twin-datalayer="">
      <header className="wt-dl-head">
        <div>
          <p className="wt-eyebrow">Grip via de datalaag</p>
          <h2 className="wt-detail-title">De datalaag</h2>
        </div>
        <button type="button" className="wt-btn" onClick={onClose} aria-label="Datalaag sluiten">
          ✕
        </button>
      </header>
      <div className="wt-tabs" role="tablist" aria-label="Datalaag-lenzen">
        {DL_TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            className="wt-tab"
            aria-selected={tab === id}
            onClick={() => onTab(id)}
            data-twin-dl-tab={id}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="wt-dl-body">
        {tab === "bronnen" && <Sources onSelect={onSelect} />}
        {tab === "onderhoud" && <Maintenance onSelect={onSelect} />}
        {tab === "euros" && <Euros onSelect={onSelect} />}
        {tab === "dataops" && <DataOpsTab />}
      </div>
    </section>
  );
}

function Node({ n }: { n: LayerNode }) {
  const counts = n.topics != null;
  return (
    <li className="wt-dl-node" data-status={n.status}>
      <span className="wt-dl-dot" aria-hidden="true" />
      <div>
        <strong>{n.name}</strong>
        <small>
          {n.protocol ? `${n.protocol} · ` : ""}
          {STATUS_WORD[n.status] ?? n.status}
          {counts && ` · ${n.topics} topics`}
          {n.last_age_s != null && ` · ${ago(n.last_age_s)} geleden`}
        </small>
        {n.role && <small>{n.role}</small>}
      </div>
      {counts && (
        <span className="wt-dl-q num" aria-label={`${n.good} goed, ${n.stale} verouderd, ${n.bad} slecht`}>
          <span data-q="GOOD">{n.good}</span>
          <span data-q="STALE">{n.stale}</span>
          <span data-q="BAD">{n.bad}</span>
        </span>
      )}
    </li>
  );
}

function Sources({ onSelect }: { onSelect: (id: string) => void }) {
  const map = useLayerMap();
  if (map.isError) return <p className="wt-empty">De datalaag is niet bereikbaar.</p>;
  if (!map.data) return <p className="wt-empty">Datalaag laden…</p>;
  const d = map.data;
  const stores = d.services.filter((s) => s.kind === "store");
  const consumers = d.services.filter((s) => s.kind !== "store");
  return (
    <div className="wt-dl-flow">
      <p className="wt-eyebrow">Bronnen</p>
      <ul className="wt-dl-nodes">
        {d.sources.map((n) => (
          <Node key={n.id} n={n} />
        ))}
      </ul>
      <div className="wt-dl-core" data-twin-dl-core="">
        <strong>{d.datalayer.name}</strong>
        <small>
          {d.datalayer.topics} topics in ISA-95-context · {d.datalayer.good} goed · {d.datalayer.stale} verouderd · {d.datalayer.bad} slecht
        </small>
        <code className="mono">{d.datalayer.convention}</code>
      </div>
      <p className="wt-eyebrow">Opslag</p>
      <ul className="wt-dl-nodes">
        {stores.map((n) => (
          <Node key={n.id} n={n} />
        ))}
      </ul>
      <p className="wt-eyebrow">Afnemers</p>
      <ul className="wt-dl-nodes">
        {consumers.map((n) => (
          <Node key={n.id} n={n} />
        ))}
      </ul>
      <DemoActions onSelect={onSelect} />
      <p className="wt-note">Verouderd = geen nieuwe waarde binnen {d.stale_threshold_s} s. Elke afnemer leest dezelfde laag.</p>
    </div>
  );
}

const DEMO_EQ = [
  "cook-unit-01",
  "cooler-01",
  "filler-01",
  "process-tank-01",
  "receiving-tank-01",
  "cook-unit-02",
  "cooler-02",
  "filler-02",
  "process-tank-02",
  "receiving-tank-02",
];

/** Twee knoppen voor een opname: een storing melden, en een signaal laten wegvallen. */
function DemoActions({ onSelect }: { onSelect: (id: string) => void }) {
  const qc = useQueryClient();
  const [eq, setEq] = useState(DEMO_EQ[0]);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try {
      await fn();
      setMsg(ok);
      onSelect(`equipment:${eq}`);
      qc.invalidateQueries({ queryKey: ["dl"] });
      qc.invalidateQueries({ queryKey: ["cmms"] });
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Mislukt");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="wt-dl-demo" data-twin-dl-demo="">
      <p className="wt-eyebrow">Demo</p>
      <label className="wt-dl-field">
        <span>Machine</span>
        <select value={eq} onChange={(e) => setEq(e.target.value)}>
          {DEMO_EQ.map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
      </label>
      <div className="wt-dl-buttons">
        <button
          type="button"
          className="wt-chip-btn"
          disabled={busy}
          onClick={() => run(() => post(`/equipment/${eq}/breakdown`, {}), `Storing gemeld op ${eq}; werkorder aangemaakt.`)}
        >
          Storing melden
        </button>
        <button
          type="button"
          className="wt-chip-btn"
          disabled={busy}
          onClick={() => run(() => post(`/datalayer/dropout/${eq}?seconds=90`), `Signalen van ${eq} vallen 90 s weg.`)}
        >
          Signaal laten wegvallen
        </button>
        <button
          type="button"
          className="wt-chip-btn"
          disabled={busy}
          onClick={() => run(() => del("/datalayer/dropout/all"), "Alle signalen hersteld.")}
        >
          Signalen herstellen
        </button>
      </div>
      {msg && (
        <p className="wt-note" role="status">
          {msg}
        </p>
      )}
    </div>
  );
}

function WoRow({ w, onSelect }: { w: WorkOrder; onSelect: (id: string) => void }) {
  return (
    <li>
      <button type="button" className="wt-row" onClick={() => onSelect(`equipment:${w.equipment_id}`)} data-twin-wo={w.wo_id}>
        <span className="wt-row-name">
          <span className="mono">{w.wo_id}</span> · {w.equipment_id}
        </span>
        <span className="wt-dl-wo" data-wo={w.status}>
          {WO_WORD[w.status] ?? w.status}
        </span>
        <span className="wt-row-meta">
          {w.kind === "cbm" ? "CBM" : "storing"} · {w.cause ?? "oorzaak onbekend"}
          {w.technician ? ` · ${w.technician.name}` : ""}
        </span>
      </button>
    </li>
  );
}

function Maintenance({ onSelect }: { onSelect: (id: string) => void }) {
  const wo = useWorkOrders();
  const rel = useReliability();
  const list = wo.data?.work_orders ?? [];
  const open = list.filter((w) => w.status !== "done");
  const done = list.filter((w) => w.status === "done").slice(0, 5);
  const rows = (rel.data?.equipment ?? []).filter((r) => r.failures > 0 || r.down).slice(0, 8);
  return (
    <div className="wt-dl-flow">
      <p className="wt-eyebrow">Open werkorders · {open.length}</p>
      {open.length === 0 ? (
        <p className="wt-empty">Geen open werkorders.</p>
      ) : (
        <ul className="wt-list wt-dl-list">
          {open.map((w) => (
            <WoRow key={w.wo_id} w={w} onSelect={onSelect} />
          ))}
        </ul>
      )}
      {done.length > 0 && (
        <>
          <p className="wt-eyebrow">Recent gereed</p>
          <ul className="wt-list wt-dl-list">
            {done.map((w) => (
              <WoRow key={w.wo_id} w={w} onSelect={onSelect} />
            ))}
          </ul>
        </>
      )}
      <p className="wt-eyebrow">Betrouwbaarheid · {rel.data?.window_h ?? 24} uur</p>
      {rows.length === 0 ? (
        <p className="wt-empty">Nog geen storingen in dit venster.</p>
      ) : (
        <table className="wt-dl-table">
          <thead>
            <tr>
              <th scope="col">Machine</th>
              <th scope="col">Besch.</th>
              <th scope="col">MTBF</th>
              <th scope="col">MTTR</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.equipment_id} data-down={r.down ? "" : undefined}>
                <th scope="row">
                  <button type="button" className="wt-dl-link" onClick={() => onSelect(`equipment:${r.equipment_id}`)}>
                    {short(r.equipment_id)}
                  </button>
                </th>
                <td className="num">{r.availability == null ? "—" : `${num(r.availability * 100, 1)}%`}</td>
                <td className="num">{r.mtbf_h == null ? "—" : `${num(r.mtbf_h, 1)} u`}</td>
                <td className="num">{r.mttr_min == null ? "—" : `${num(r.mttr_min, 0)} min`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="wt-note">
        Werkorders komen uit het onderhoudssysteem; de status gaat terug de datalaag in als context bij de machine.
      </p>
    </div>
  );
}

function Euros({ onSelect }: { onSelect: (id: string) => void }) {
  const l = useEquipmentLosses();
  if (l.isError) return <p className="wt-empty">Verliezen niet beschikbaar.</p>;
  if (!l.data) return <p className="wt-empty">Laden…</p>;
  const d = l.data;
  const rows = d.equipment.filter((r) => r.total_causal > 0 || r.total_resultant > 0);
  const max = Math.max(1, ...rows.map((r) => r.total_causal));
  return (
    <div className="wt-dl-flow">
      <p className="wt-eyebrow">Verlies per machine · 24 uur</p>
      {d.total_causal != null && <p className="wt-dl-big num">{eur(d.total_causal)}</p>}
      {rows.length === 0 ? (
        <p className="wt-empty">{d.equipment.length ? "Geen verlies in dit venster." : "Geen kostenmodel ingesteld."}</p>
      ) : (
        <ul className="wt-list wt-dl-list">
          {rows.map((r) => (
            <li key={r.equipment_id}>
              <button type="button" className="wt-row" onClick={() => onSelect(`equipment:${r.equipment_id}`)}>
                <span className="wt-row-name">{r.equipment_id}</span>
                <span className="num">{eur(r.total_causal)}</span>
                <span className="wt-row-meta">
                  {r.items
                    .filter((i) => i.kind === "causal")
                    .map((i) => `${i.label} ${eur(i.amount)}`)
                    .join(" · ")}
                </span>
                <span className="wt-row-bar">
                  <span style={{ width: `${(r.total_causal / max) * 100}%` }} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {d.omitted.length > 0 && (
        <>
          <p className="wt-eyebrow">Niet meegeteld</p>
          <ul className="wt-reasons">
            {d.omitted.map((o) => (
              <li key={o.category}>
                {o.category === "*" ? "alles" : o.category}: {o.reason}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="wt-note">Alleen oorzakelijk verlies telt op; gevolgverlies staat erbij maar niet dubbel. Wat ontbreekt staat er eerlijk bij.</p>
    </div>
  );
}

function DataOpsTab() {
  const d = useDataOps();
  const r = useResources();
  return (
    <div className="wt-dl-flow">
      <p className="wt-eyebrow">Datakwaliteit</p>
      {d.data ? (
        <ul className="wt-dl-rules">
          {d.data.rules.map((x) => (
            <li key={x.id} data-ok={x.ok ? "" : undefined}>
              <span className="wt-dl-dot" aria-hidden="true" />
              <div>
                <strong>
                  <span className="mono">{x.id}</span> {x.rule}
                </strong>
                <small>{x.detail}</small>
              </div>
              <span>{x.ok ? "ok" : "fout"}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="wt-empty">Laden…</p>
      )}
      <p className="wt-eyebrow">Build</p>
      <dl className="wt-rows">
        <div>
          <dt>Versie</dt>
          <dd className="mono">{d.data?.build.git_sha?.slice(0, 7) ?? "lokaal"}</dd>
        </div>
        <div>
          <dt>Tests geslaagd</dt>
          <dd className="num">{d.data?.build.tests_passed ?? "—"}</dd>
        </div>
        <div>
          <dt>Pijplijn</dt>
          <dd>{d.data?.build.pipeline ?? "—"}</dd>
        </div>
      </dl>
      <p className="wt-eyebrow">Wat deze fabriek kost aan server</p>
      {r.data?.available ? (
        <>
          <p className="wt-dl-big num">
            {num((r.data.stack?.mem_mib ?? 0) / 1024, 1)} GB · {num(r.data.stack?.cpu_pct ?? 0, 0)}% cpu
          </p>
          <ul className="wt-dl-meter">
            {r.data.containers
              .filter((c) => c.stack)
              .slice(0, 8)
              .map((c) => (
                <li key={c.name}>
                  <span className="mono">{c.name}</span>
                  <span className="num">{num(c.mem_mib ?? 0, 0)} MB</span>
                </li>
              ))}
          </ul>
          {r.data.stale && <p className="wt-note">Meting is ouder dan drie minuten.</p>}
        </>
      ) : (
        <p className="wt-empty">{r.data?.reason ?? "Geen meting beschikbaar."}</p>
      )}
      <p className="wt-note">Eén VPS, open source, geen licentie per tag of per gebruiker.</p>
    </div>
  );
}

/* ---------------- detailkaart: context per machine ---------------- */

export function EquipmentDatalayer({ eq, onSelect }: { eq: string; onSelect?: (id: string) => void }) {
  const c = useEquipmentContext(eq);
  const [raw, setRaw] = useState(false);
  const [cause, setCause] = useState<string | null>(null);
  if (c.isError) return <p className="wt-note">Geen datalaag-context voor deze machine.</p>;
  if (!c.data) return <p className="wt-note">Context laden…</p>;
  const d = c.data;
  const wo = d.open_work_orders[0];
  return (
    <div className="wt-dl-ctx" data-twin-dl-context={eq}>
      <p className="wt-eyebrow">In de datalaag</p>
      <ol className="wt-dl-path" aria-label="ISA-95-pad">
        {d.isa95_path.map((p) => (
          <li key={p.level} title={p.level}>
            {p.name}
          </li>
        ))}
      </ol>
      <div className="wt-dl-badges">
        <span className="wt-dl-qbadge" data-q={d.quality}>
          signalen {QUALITY_WORD[d.quality] ?? d.quality}
          {d.dropout_simulated ? " (demo)" : ""}
        </span>
        {d.down && (
          <span className="wt-dl-qbadge" data-q="BAD">
            Down · {d.down.cause}
          </span>
        )}
        {wo && (
          <span className="wt-dl-wo" data-wo={wo.status}>
            {wo.wo_id} · {WO_WORD[wo.status] ?? wo.status}
            {wo.technician ? ` · ${wo.technician.name}` : ""}
          </span>
        )}
      </div>
      <div className="wt-dl-switch" role="group" aria-label="Weergave">
        <button type="button" className="wt-chip-btn" aria-pressed={!raw} onClick={() => setRaw(false)}>
          Met context
        </button>
        <button type="button" className="wt-chip-btn" aria-pressed={raw} onClick={() => setRaw(true)}>
          Ruw uit de PLC
        </button>
      </div>
      <table className="wt-dl-table wt-dl-topics">
        <tbody>
          {d.topics.slice(0, 12).map((t) => (
            <tr key={t.topic} data-q={t.quality ?? undefined}>
              <th scope="row" className="mono">
                {raw ? (t.raw ? t.raw.node_id : "— (MES)") : t.tag}
              </th>
              <td className="num">
                {t.value == null ? "—" : typeof t.value === "number" ? num(t.value) : String(t.value)}
                {!raw && t.unit ? ` ${t.unit}` : ""}
              </td>
              <td className="num">{ago(t.age_s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="wt-note mono">{raw ? d.opcua_endpoint : `${d.topic_prefix}/…`}</p>
      {d.open_cbm_alerts.length > 0 && (
        <div className="wt-dl-buttons">
          {d.open_cbm_alerts.map((a) => (
            <button
              key={a.alert_id}
              type="button"
              className="wt-chip-btn"
              aria-pressed={cause === a.alert_id}
              onClick={() => setCause((x) => (x === a.alert_id ? null : a.alert_id))}
              data-twin-cause-btn={a.alert_id}
            >
              Oorzaak: {a.message ?? a.alert_id}
            </button>
          ))}
        </div>
      )}
      {cause && <CausePanel id={cause} onSelect={onSelect} />}
    </div>
  );
}

export function CausePanel({ id, onSelect }: { id: string; onSelect?: (id: string) => void }) {
  const c = useCause(id);
  if (c.isError) return <p className="wt-note">Oorzaak niet te bepalen.</p>;
  if (!c.data) return <p className="wt-note">Signalen doorzoeken…</p>;
  const d = c.data;
  return (
    <div className="wt-dl-cause" data-twin-cause={id}>
      <p className="wt-eyebrow">Van symptoom naar oorzaak</p>
      <ol className="wt-dl-chain">
        {d.chain.map((s) => (
          <li key={s.step}>
            <button type="button" className="wt-dl-link" onClick={() => onSelect?.(`equipment:${s.equipment_id}`)}>
              <strong>{s.label}</strong>
            </button>
            <small>{s.evidence}</small>
          </li>
        ))}
      </ol>
      <p className="wt-dl-verdict">{d.verdict}</p>
      {d.ai?.text && (
        <blockquote className="wt-dl-ai">
          {d.ai.text}
          <small>lokaal model · {d.ai.model}</small>
        </blockquote>
      )}
      {onSelect && d.focus_equipment && (
        <button type="button" className="wt-chip-btn" onClick={() => onSelect(`equipment:${d.focus_equipment}`)}>
          ◎ Toon {d.focus_equipment}
        </button>
      )}
    </div>
  );
}
