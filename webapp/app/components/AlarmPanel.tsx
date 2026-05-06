import { CheckCircle2 } from 'lucide-react';

export type Alarm = {
  severity: 'critical' | 'warning' | 'info';
  source: string;
  message: string;
};

type Props = { alarms: Alarm[] };

const SEVERITY_STYLES = {
  critical: { border: 'border-red-800',   bg: 'bg-red-950/40',   text: 'text-red-400',   dot: 'bg-red-500' },
  warning:  { border: 'border-amber-800', bg: 'bg-amber-950/40', text: 'text-amber-400', dot: 'bg-amber-500' },
  info:     { border: 'border-blue-800',  bg: 'bg-blue-950/40',  text: 'text-blue-400',  dot: 'bg-blue-500' },
};

export default function AlarmPanel({ alarms }: Props) {
  return (
    <div>
      <p className="text-gray-600 uppercase tracking-widest text-[10px] px-2 mb-2 font-semibold">
        Active Alarms
        {alarms.length > 0 && (
          <span className="ml-2 bg-red-500 text-white text-[9px] rounded-full px-1.5 py-0.5 font-bold">
            {alarms.length}
          </span>
        )}
      </p>

      {alarms.length === 0 ? (
        <div className="flex items-center gap-2 px-2 py-1.5 text-xs text-green-500">
          <CheckCircle2 size={12} />
          <span>No active alarms</span>
        </div>
      ) : (
        <div className="space-y-1 px-1">
          {alarms.map((alarm, i) => {
            const s = SEVERITY_STYLES[alarm.severity];
            return (
              <div key={i} className={`border ${s.border} ${s.bg} rounded px-2 py-1.5`}>
                <div className="flex items-center gap-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${s.dot} pulse-dot flex-shrink-0`} />
                  <span className={`text-[10px] font-semibold ${s.text}`}>{alarm.source}</span>
                </div>
                <p className="text-gray-400 text-[10px] mt-0.5 leading-tight">{alarm.message}</p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
