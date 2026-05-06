'use client';

import { useId } from 'react';

type Props = {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
  className?: string;
};

export default function SparkLine({ values, width = 80, height = 28, color = '#3b82f6', className = '' }: Props) {
  const uid = useId();
  const gradId = `spark-grad-${uid.replace(/:/g, '')}`;

  if (values.length < 2) {
    return <div style={{ width, height }} className={`opacity-20 ${className}`} />;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 2;

  const points = values.map((v, i) => ({
    x: pad + (i / (values.length - 1)) * (width - pad * 2),
    y: pad + (1 - (v - min) / range) * (height - pad * 2),
  }));

  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const fill = `${d} L${points[points.length - 1].x.toFixed(1)},${height} L${points[0].x.toFixed(1)},${height} Z`;

  const lastVal = values[values.length - 1];
  const prevVal = values[values.length - 2];
  const trend = lastVal > prevVal * 1.001 ? '↑' : lastVal < prevVal * 0.999 ? '↓' : '→';
  const trendColor = trend === '↑' ? '#22c55e' : trend === '↓' ? '#ef4444' : '#64748b';

  return (
    <div className={`flex items-end gap-1 ${className}`}>
      <svg width={width} height={height} className="opacity-70">
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.3" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={fill} fill={`url(#${gradId})`} />
        <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={points[points.length - 1].x} cy={points[points.length - 1].y} r="2" fill={color} />
      </svg>
      <span className="text-xs font-mono leading-none pb-0.5" style={{ color: trendColor }}>{trend}</span>
    </div>
  );
}
