import { useId } from 'react';
import type { MyLeague } from '../../api/types';
import { cleanLeagueName } from './starterPrompts';

interface LeaguePickerProps {
  leagues: readonly MyLeague[];
  value: number | null;
  isDisabled: boolean;
  onChange: (id: number | null) => void;
}

/** With two or more imported leagues, lets the chat focus on one so "my team" is never a guess. */
export function LeaguePicker({ leagues, value, isDisabled, onChange }: LeaguePickerProps) {
  const id = useId();
  if (leagues.length < 2) return null;
  return (
    <div className="league-picker">
      <label htmlFor={id} className="league-picker__label">League for this chat</label>
      <select
        id={id}
        className="select league-picker__select"
        value={value ?? ''}
        disabled={isDisabled}
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      >
        <option value="">All my leagues</option>
        {leagues.map((league) => (
          <option key={league.id} value={league.id}>{cleanLeagueName(league.name)}</option>
        ))}
      </select>
    </div>
  );
}
