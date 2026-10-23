import { useId } from 'react';

export interface SelectOption {
  value: string | number;
  label: string;
}

interface SelectFieldProps {
  label: string;
  value: string | number;
  options: readonly SelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  isInline?: boolean;
  className?: string;
}

/** Labelled native <select>; the label is always programmatically associated. */
export function SelectField({ label, value, options, onChange, disabled, isInline = false, className }: SelectFieldProps) {
  const id = useId();
  const fieldClass = ['field', isInline ? 'field--inline' : '', className ?? ''].filter(Boolean).join(' ');
  return (
    <div className={fieldClass}>
      <label className="field__label" htmlFor={id}>{label}</label>
      <select id={id} className="select" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </div>
  );
}
