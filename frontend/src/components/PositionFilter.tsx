import { POSITION_FILTERS, positionFilterLabel, type PositionFilterValue } from '../lib/positions';

interface PositionFilterProps {
  value: PositionFilterValue;
  onChange: (value: PositionFilterValue) => void;
  label?: string;
}

/** Segmented position filter (All / QB / RB / WR / TE) built from toggle buttons. */
export function PositionFilter({ value, onChange, label = 'Filter by position' }: PositionFilterProps) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {POSITION_FILTERS.map((option) => {
        const isActive = option === value;
        const tone = option === 'ALL' ? 'all' : option.toLowerCase();
        return (
          <button
            key={option}
            type="button"
            className={`segmented__option segmented__option--${tone}${isActive ? ' is-active' : ''}`}
            aria-pressed={isActive}
            onClick={() => onChange(option)}
          >
            {positionFilterLabel(option)}
          </button>
        );
      })}
    </div>
  );
}
