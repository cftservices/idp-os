'use client';

import { useEffect, useState, useCallback } from 'react';

type TagData = { value: string; time: string };
type PlcData = Record<string, TagData>;

function parseNum(v: string | undefined, decimals = 1): string {
  if (!v) return '—';
  const n = parseFloat(v);
  return isNaN(n) ? v : n.toFixed(decimals);
}

function Metric({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-gray-800 last:border-0">
      <span className="text-gray-400 text-sm">{label}</span>
      <span className="font-mono text-base font-semibold text-white">
        {value}
        {unit && <span className="text-gray-500 text-xs ml-1">{unit}</span>}
      </span>
    </div>
  );
}

function StatusBadge({ ok, okText, badText }: { ok: boolean; okText: string; badText: string }) {
  return (
    <span className={`text-sm font-semibold ${ok ? 'text-green-400' : 'text-red-400'}`}>
      {ok ? `✓ ${okText}` : `⚠ ${badText}`}
    </span>
  );
}

function PlcCard({ title, subtitle, online, children }: {
  title: string; subtitle: string; online: boolean; children: React.ReactNode;
}) {
  return (
    <div className={`bg-gray-900 rounded-xl border p-5 ${online ? 'border-gray-700' : 'border-red-900 opacity-60'}`}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-white font-bold">{title}</h2>
          <p className="text-gray-500 text-xs mt-0.5">{subtitle}</p>
        </div>
        <span className={`text-xs px-2 py-0.5 rounded-full font-mono ${
          online ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
        }`}>
          {online ? 'ONLINE' : 'OFFLINE'}
        </span>
      </div>
      {children}
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<PlcData>({});
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const res = await fetch('/api/plc');
      if (!res.ok) throw new Error('fetch failed');
      setData(await res.json());
      setLastUpdate(new Date());
      setError(false);
    } catch {
      setError(true);
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
  const raw = (t: string) => data[t]?.value;
  const online = (prefix: string) => Object.keys(data).some(k => k.startsWith(prefix));

  const alarm = raw('idp/plc01/alarm') === 'true';
  const fault = parseInt(raw('idp/plc02/fault_bits') || '0') > 0;
  const motor3rpm = parseFloat(raw('idp/plc02/motor3_rpm') || '0');
  const phase = parseInt(raw('idp/plc03/phase') || '0');

  return (
    <main className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-5xl mx-auto">

        {/* Header */}
        <div className="flex items-start justify-between mb-8">
          <div>
            <h1 className="text-xl font-bold tracking-tight">Industrial Data Platform</h1>
            <p className="text-gray-500 text-sm mt-1">
              OPC-UA Simulator → MonsterMQ → MongoDB · 3 PLCs live
            </p>
          </div>
          <div className="text-right">
            {error && <p className="text-red-400 text-xs mb-1">MongoDB unreachable</p>}
            <p className="text-gray-600 text-xs">Updated</p>
            <p className="text-gray-300 text-sm font-mono">
              {lastUpdate ? lastUpdate.toLocaleTimeString() : '—'}
            </p>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-64 text-gray-500">
            Connecting to MongoDB...
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">

            {/* PLC_01 — Process Control */}
            <PlcCard title="PLC_01" subtitle="Process Control" online={online('idp/plc01')}>
              <Metric label="Temperature" value={v('idp/plc01/temperature')} unit="°C" />
              <Metric label="Pressure"    value={v('idp/plc01/pressure', 3)} unit="bar" />
              <Metric label="Flow"        value={v('idp/plc01/flow')} unit="m³/h" />
              <div className="flex items-center justify-between pt-2">
                <span className="text-gray-400 text-sm">Alarm</span>
                <StatusBadge ok={!alarm} okText="Normal" badText="ALARM" />
              </div>
            </PlcCard>

            {/* PLC_02 — Drive Control */}
            <PlcCard title="PLC_02" subtitle="Drive Control" online={online('idp/plc02')}>
              <Metric label="Motor 1" value={v('idp/plc02/motor1_rpm')} unit="RPM" />
              <Metric label="Motor 2" value={v('idp/plc02/motor2_rpm')} unit="RPM" />
              <div className="flex items-center justify-between py-2 border-b border-gray-800">
                <span className="text-gray-400 text-sm">Motor 3</span>
                <span className={`font-mono text-base font-semibold ${motor3rpm < 50 ? 'text-yellow-500' : 'text-white'}`}>
                  {motor3rpm < 50 ? 'STOPPED' : `${v('idp/plc02/motor3_rpm')} RPM`}
                </span>
              </div>
              <Metric label="Power" value={v('idp/plc02/power_kw', 2)} unit="kW" />
              <div className="flex items-center justify-between pt-2">
                <span className="text-gray-400 text-sm">Fault</span>
                <StatusBadge ok={!fault} okText="OK" badText={`Code ${raw('idp/plc02/fault_bits')}`} />
              </div>
            </PlcCard>

            {/* PLC_03 — Batch Process */}
            <PlcCard title="PLC_03" subtitle="Batch Process" online={online('idp/plc03')}>
              <Metric label="Batch #"  value={v('idp/plc03/batch_counter', 0)} />
              <Metric label="Recipe"   value={v('idp/plc03/recipe_id', 0)} />
              <div className="flex items-center justify-between py-2 border-b border-gray-800">
                <span className="text-gray-400 text-sm">Phase</span>
                <div className="flex gap-1">
                  {[1, 2, 3, 4].map(p => (
                    <span key={p} className={`w-6 h-6 rounded text-xs flex items-center justify-center font-bold ${
                      phase === p ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-500'
                    }`}>{p}</span>
                  ))}
                </div>
              </div>
              <Metric label="Rate" value={v('idp/plc03/production_rate')} unit="units/h" />
            </PlcCard>

          </div>
        )}

        {/* Footer */}
        <div className="mt-8 pt-4 border-t border-gray-800 flex justify-between text-gray-700 text-xs">
          <span>IDP v2 · techflow24.com · open source</span>
          <span>MonsterMQ + MongoDB + Next.js</span>
        </div>

      </div>
    </main>
  );
}
