'use client';

import { useEffect, useState, useCallback } from 'react';
import { Activity, Zap, AlertTriangle, Layers } from 'lucide-react';
import EquipmentTree from './components/EquipmentTree';
import AlarmPanel, { type Alarm } from './components/AlarmPanel';
import MetricTile from './components/MetricTile';

type TagData = { value: string; time: string };
type PlcData = Record<string, TagData>;
type HistoryData = Record<string, number[]>;

function parseNum(v: string | undefined, d = 1): string {
  if (!v) return '—';
  const n = parseFloat(v);
  return isNaN(n) ? v : n.toFixed(d);
}

function OnlineBadge({ online }: { online: boolean }) {
  return (
    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
      online ? 'bg-green-900/60 text-green-400' : 'bg-red-900/40 text-red-500'
    }`}>
      {online ? '● ONLINE' : '○ OFFLINE'}
    </span>
  );
}

function PlcSection({ title, subtitle, icon, online, children }: {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  online: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`rounded-xl border p-5 mb-4 ${
      online ? 'border-slate-700/50 bg-[#111827]' : 'border-red-900/30 bg-[#111827] opacity-50'
    }`}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="text-blue-500 opacity-70">{icon}</div>
          <div>
            <h2 className="text-white font-bold text-sm tracking-wide">{title}</h2>
            <p className="text-gray-600 text-[10px] uppercase tracking-wider mt-0.5">{subtitle}</p>
          </div>
        </div>
        <OnlineBadge online={online} />
      </div>
      {children}
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<PlcData>({});
  const [history, setHistory] = useState<HistoryData>({});
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [plcRes, histRes] = await Promise.all([
        fetch('/api/plc'),
        fetch('/api/history'),
      ]);
      if (!plcRes.ok) throw new Error('plc fetch failed');
      const [plcData, histData]: [PlcData, HistoryData] = await Promise.all([
        plcRes.json(),
        histRes.json(),
      ]);
      setData(plcData);
      setHistory(histData);
      setLastUpdate(new Date());
      setConnectionError(false);
    } catch {
      setConnectionError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const id = setInterval(loadData, 5000);
    return () => clearInterval(id);
  }, [loadData]);

  const v = (t: string, d = 1) => parseNum(data[t]?.value, d);
  const raw = (t: string) => data[t]?.value ?? '';
  const hist = (t: string) => history[t] ?? [];
  const online = (prefix: string) => Object.keys(data).some(k => k.startsWith(prefix));

  const alarm     = raw('idp/plc01/alarm') === 'true';
  const faultBits = parseInt(raw('idp/plc02/fault_bits') || '0');
  const fault     = faultBits > 0;
  const motor3rpm = parseFloat(raw('idp/plc02/motor3_rpm') || '0');
  const motor3off = motor3rpm < 50 && online('idp/plc02');
  const phase     = parseInt(raw('idp/plc03/phase') || '0');

  const plc01Online = online('idp/plc01');
  const plc02Online = online('idp/plc02');
  const plc03Online = online('idp/plc03');

  const alarms: Alarm[] = [];
  if (alarm)     alarms.push({ severity: 'critical', source: 'PLC_01', message: 'Process alarm active' });
  if (fault)     alarms.push({ severity: 'warning',  source: 'PLC_02', message: `Fault bits: ${faultBits}` });
  if (motor3off) alarms.push({ severity: 'info',     source: 'PLC_02', message: 'Motor 3 stopped (normal cycle)' });

  return (
    <div className="h-screen flex flex-col bg-[#0a0f1e] text-white overflow-hidden">

      {/* ── Top Bar ──────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-slate-800/60 bg-[#0d1424] shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded bg-blue-600 flex items-center justify-center shrink-0">
            <Activity size={14} className="text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight text-white leading-none">
              Industrial Data Platform
            </h1>
            <p className="text-[10px] text-gray-600 mt-0.5">OPC-UA → MonsterMQ → MongoDB</p>
          </div>
        </div>

        <div className="flex items-center gap-4">
          {connectionError && (
            <div className="flex items-center gap-1.5 text-red-400 text-xs">
              <AlertTriangle size={12} />
              <span>MongoDB unreachable</span>
            </div>
          )}
          <div className="text-right">
            <div className="flex items-center gap-1.5 justify-end">
              <span className={`w-2 h-2 rounded-full ${connectionError ? 'bg-red-500' : 'bg-green-500 pulse-dot'}`} />
              <span className="text-[10px] text-gray-400">{connectionError ? 'OFFLINE' : 'LIVE'}</span>
            </div>
            <p className="font-mono text-xs text-gray-500 mt-0.5 tabular-nums">
              {lastUpdate ? lastUpdate.toLocaleTimeString([], { hour12: false }) : '—:—:—'}
            </p>
          </div>
        </div>
      </header>

      {/* ── Body ─────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Sidebar ──────────────────────────────────────────────── */}
        <aside className="w-52 shrink-0 bg-[#0d1424] border-r border-slate-800/60 flex flex-col py-4 gap-6 overflow-y-auto">
          <EquipmentTree
            plc01Online={plc01Online}
            plc02Online={plc02Online}
            plc03Online={plc03Online}
          />
          <div className="border-t border-slate-800/60 pt-4">
            <AlarmPanel alarms={alarms} />
          </div>
          <div className="mt-auto px-3 pb-1">
            <p className="text-[9px] text-gray-700 leading-relaxed">
              IDP v2 · Open source<br />
              MonsterMQ + MongoDB<br />
              techflow24.com
            </p>
          </div>
        </aside>

        {/* ── Main ─────────────────────────────────────────────────── */}
        <main className="flex-1 overflow-y-auto p-5">
          {loading ? (
            <div className="flex items-center justify-center h-full text-gray-600 gap-2">
              <Activity size={16} className="animate-spin" />
              <span className="text-sm">Connecting to MongoDB…</span>
            </div>
          ) : (
            <>
              {/* PLC_01 — Process Control */}
              <PlcSection
                title="PLC_01 — Process Control"
                subtitle="Area: Process Unit · Equipment: Sensors"
                icon={<Activity size={18} />}
                online={plc01Online}
              >
                <div className="flex flex-wrap gap-3">
                  <MetricTile
                    label="Temperature"
                    value={v('idp/plc01/temperature')}
                    unit="°C"
                    history={hist('idp/plc01/temperature')}
                    color="#f59e0b"
                  />
                  <MetricTile
                    label="Pressure"
                    value={v('idp/plc01/pressure', 3)}
                    unit="bar"
                    history={hist('idp/plc01/pressure')}
                  />
                  <MetricTile
                    label="Flow"
                    value={v('idp/plc01/flow')}
                    unit="m³/h"
                    history={hist('idp/plc01/flow')}
                    color="#22d3ee"
                  />
                  <MetricTile
                    label="Alarm"
                    value={alarm ? 'ALARM' : 'Normal'}
                    highlight={alarm ? 'critical' : 'ok'}
                  />
                </div>
              </PlcSection>

              {/* PLC_02 — Drive Control */}
              <PlcSection
                title="PLC_02 — Drive Control"
                subtitle="Area: Process Unit · Equipment: Motors"
                icon={<Zap size={18} />}
                online={plc02Online}
              >
                <div className="flex flex-wrap gap-3">
                  <MetricTile
                    label="Motor 1"
                    value={v('idp/plc02/motor1_rpm', 0)}
                    unit="rpm"
                    history={hist('idp/plc02/motor1_rpm')}
                  />
                  <MetricTile
                    label="Motor 2"
                    value={v('idp/plc02/motor2_rpm', 0)}
                    unit="rpm"
                  />
                  <MetricTile
                    label="Motor 3"
                    value={motor3off ? 'STOPPED' : v('idp/plc02/motor3_rpm', 0)}
                    unit={motor3off ? undefined : 'rpm'}
                    highlight={motor3off ? 'warn' : undefined}
                  />
                  <MetricTile
                    label="Power"
                    value={v('idp/plc02/power_kw', 2)}
                    unit="kW"
                    history={hist('idp/plc02/power_kw')}
                    color="#a78bfa"
                  />
                  <MetricTile
                    label="Faults"
                    value={fault ? `Code ${faultBits}` : 'OK'}
                    highlight={fault ? 'critical' : 'ok'}
                  />
                </div>
              </PlcSection>

              {/* PLC_03 — Batch Process */}
              <PlcSection
                title="PLC_03 — Batch Process"
                subtitle="Area: Process Unit · Equipment: Batch Controller"
                icon={<Layers size={18} />}
                online={plc03Online}
              >
                <div className="flex flex-wrap gap-3 mb-4">
                  <MetricTile
                    label="Batch #"
                    value={v('idp/plc03/batch_counter', 0)}
                  />
                  <MetricTile
                    label="Recipe ID"
                    value={v('idp/plc03/recipe_id', 0)}
                  />
                  <MetricTile
                    label="Rate"
                    value={v('idp/plc03/production_rate')}
                    unit="u/h"
                    history={hist('idp/plc03/production_rate')}
                    color="#22c55e"
                  />
                </div>

                {/* Phase progress indicator */}
                <div className="flex items-center gap-3">
                  <p className="text-gray-500 text-[10px] uppercase tracking-wider w-10 shrink-0">Phase</p>
                  <div className="flex gap-1.5">
                    {[1, 2, 3, 4].map(p => (
                      <div
                        key={p}
                        className={`relative w-10 h-7 rounded flex items-center justify-center text-xs font-bold transition-all ${
                          phase === p
                            ? 'bg-blue-600 text-white shadow-lg shadow-blue-900/50'
                            : phase > p
                            ? 'bg-blue-900/40 text-blue-500'
                            : 'bg-slate-800/60 text-gray-700'
                        }`}
                      >
                        {p}
                        {phase === p && (
                          <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-blue-400 pulse-dot" />
                        )}
                      </div>
                    ))}
                  </div>
                  <p className="text-gray-600 text-xs tabular-nums">{phase > 0 ? `${phase} / 4` : '— / 4'}</p>
                </div>
              </PlcSection>

              <div className="h-4" />
            </>
          )}
        </main>
      </div>
    </div>
  );
}
