// Segmented control — ported from design_handoff_ims_2_0/shell/shell.jsx → Seg

interface SegProps<T extends string> {
  value: T;
  onChange: (v: T) => void;
  options: readonly T[];
  className?: string;
}

export function Seg<T extends string>({ value, onChange, options, className }: SegProps<T>) {
  return (
    <div className={'seg' + (className ? ' ' + className : '')}>
      {options.map((o) => (
        <button
          key={o}
          type="button"
          className={value === o ? 'on' : ''}
          onClick={() => onChange(o)}
        >
          {o}
        </button>
      ))}
    </div>
  );
}
