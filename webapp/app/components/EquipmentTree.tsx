import { Factory, Layers, Cpu, Activity } from 'lucide-react';

type Props = {
  plc01Online: boolean;
  plc02Online: boolean;
  plc03Online: boolean;
};

type NodeProps = {
  label: string;
  level: number;
  online?: boolean;
  icon?: React.ReactNode;
  children?: React.ReactNode;
};

function TreeNode({ label, level, online, icon, children }: NodeProps) {
  return (
    <div>
      <div
        className={`flex items-center gap-1.5 py-1 rounded text-xs cursor-default ${
          online === true ? 'text-green-400' : online === false ? 'text-gray-600' : 'text-gray-400'
        }`}
        style={{ paddingLeft: `${8 + level * 12}px` }}
      >
        {level > 0 && (
          <span className="text-gray-700 select-none">{children ? '▾' : '└'}</span>
        )}
        {icon && <span className="opacity-60">{icon}</span>}
        <span className="truncate">{label}</span>
        {online === true && <span className="ml-auto mr-2 w-1.5 h-1.5 rounded-full bg-green-400 pulse-dot flex-shrink-0" />}
        {online === false && <span className="ml-auto mr-2 w-1.5 h-1.5 rounded-full bg-gray-700 flex-shrink-0" />}
      </div>
      {children && <div>{children}</div>}
    </div>
  );
}

export default function EquipmentTree({ plc01Online, plc02Online, plc03Online }: Props) {
  return (
    <div className="text-xs">
      <p className="text-gray-600 uppercase tracking-widest text-[10px] px-2 mb-2 font-semibold">
        ISA-95 Hierarchy
      </p>
      <TreeNode label="Demo Enterprise" level={0} icon={<Factory size={11} />}>
        <TreeNode label="Demo Plant" level={1} icon={<Layers size={11} />}>
          <TreeNode label="Process Area" level={2} icon={<Activity size={11} />}>
            <TreeNode label="PLC_01 — Process" level={3} online={plc01Online} icon={<Cpu size={11} />} />
            <TreeNode label="PLC_02 — Drives"  level={3} online={plc02Online} icon={<Cpu size={11} />} />
            <TreeNode label="PLC_03 — Batch"   level={3} online={plc03Online} icon={<Cpu size={11} />} />
          </TreeNode>
        </TreeNode>
      </TreeNode>
    </div>
  );
}
