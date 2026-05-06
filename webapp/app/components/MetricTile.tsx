import SparkLine from './SparkLine';

type Props = {
  label: string;
  value: string;
  unit?: string;
  history?: number[];
  color?: string;
  highlight?: 'ok' | 'warn' | 'critical' | 'off';
};

const HIGHLIGHT_STYLES: Record<string, string> = {
  ok:       'text-green-400',
  warn:     'text-amber-400',
  critical: 'text-red-400',
  off:      'text-gray-600',
};

export default function MetricTile({ label, value, unit, history, color = '#3b82f6', highlight }: Props) {
  const sparkColor = highlight === 'critical' ? '#ef4444' : highlight === 'warn' ? '#f59e0b' : color;

  return (
    <div className="bg-navy-700 rounded-lg p-3 flex flex-col gap-1 min-w-[110px]">
      <p className="text-gray-500 text-[10px] uppercase tracking-wider font-medium whitespace-nowrap">{label}</p>
      <p className={`font-mono text-lg font-bold leading-none ${highlight ? HIGHLIGHT_STYLES[highlight] : 'text-white'}`}>
        {value}
        {unit && <span className="text-xs text-gray-600 ml-1 font-normal">{unit}</span>}
      </p>
      {history && history.length > 2 && (
        <SparkLine values={history} width={84} height={24} color={sparkColor} />
      )}
    </div>
  );
}
