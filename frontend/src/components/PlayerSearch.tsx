import { useId, useMemo, useState, type Ref } from 'react';
import { errorMessage } from '../api/client';
import { searchPlayers } from '../api/data';
import type { PlayerSummary } from '../api/types';
import { useAsync } from '../hooks/useAsync';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { Icon } from './Icon';
import { PositionChip } from './PlayerLink';
import { useComboboxNavigation } from './useComboboxNavigation';

export const SEARCH_DEBOUNCE_MS = 250;
const SEARCH_LIMIT = 8;
const NO_IDS: readonly number[] = [];
const STATIC_INPUT_PROPS = {
  className: 'input combobox__input',
  type: 'text',
  role: 'combobox',
  autoComplete: 'off',
  spellCheck: false,
  'aria-autocomplete': 'list',
} as const;

interface SearchStatus {
  results: PlayerSummary[];
  message: string;
  isError: boolean;
}

function useSearchResults(query: string, excludeIds: readonly number[]): SearchStatus {
  const term = query.trim();
  const debounced = useDebouncedValue(term, SEARCH_DEBOUNCE_MS);
  const search = useAsync(
    debounced ? (signal) => searchPlayers(debounced, { limit: SEARCH_LIMIT }, signal) : null,
    [debounced],
  );
  const results = useMemo(
    () => (search.data?.results ?? []).filter((player) => !excludeIds.includes(player.id)),
    [search.data, excludeIds],
  );
  if (!term) return { results: [], message: '', isError: false };
  if (search.status === 'error') {
    return { results: [], message: errorMessage(search.error), isError: true };
  }
  const isPending = term !== debounced || search.status === 'loading';
  if (isPending && results.length === 0) return { results, message: 'Searching…', isError: false };
  if (results.length === 0) return { results, message: `No players match “${term}”.`, isError: false };
  const noun = results.length === 1 ? 'player' : 'players';
  return {
    results,
    message: `${results.length} ${noun} found. Use up and down arrows to choose.`,
    isError: false,
  };
}

interface ListboxProps {
  id: string;
  label: string;
  results: PlayerSummary[];
  active: number;
  isExpanded: boolean;
  optionId: (index: number) => string;
  onHover: (index: number) => void;
  onChoose: (player: PlayerSummary) => void;
}

function SearchListbox({ id, label, results, active, isExpanded, optionId, onHover, onChoose }: ListboxProps) {
  return (
    <ul id={id} role="listbox" aria-label={label} className="combobox__listbox" hidden={!isExpanded}>
      {results.map((player, index) => (
        <li
          key={player.id}
          id={optionId(index)}
          role="option"
          aria-selected={index === active}
          className={index === active ? 'combobox__option is-active' : 'combobox__option'}
          onMouseDown={(event) => event.preventDefault()}
          onMouseEnter={() => onHover(index)}
          onClick={() => onChoose(player)}
        >
          <span className="combobox__name">{player.name}</span>
          <PositionChip position={player.position} />
          {player.team && <span className="combobox__team">{player.team}</span>}
        </li>
      ))}
    </ul>
  );
}

function SearchStatusLine({ id, isError, message }: { id: string; isError: boolean; message: string }) {
  return (
    <p id={id} className={isError ? 'combobox__status is-error' : 'combobox__status'} aria-live="polite">
      {message}
    </p>
  );
}

interface PlayerSearchProps {
  label: string;
  onSelect: (player: PlayerSummary) => void;
  excludeIds?: readonly number[];
  placeholder?: string;
  inputRef?: Ref<HTMLInputElement>;
}

/** ARIA 1.2 combobox with a listbox popup; debounced search against `/api/players/search`. */
export function PlayerSearch({ label, onSelect, excludeIds = NO_IDS, placeholder = 'Search players…', inputRef }: PlayerSearchProps) {
  const baseId = useId();
  const [query, setQuery] = useState('');
  const { results, message, isError } = useSearchResults(query, excludeIds);
  const optionId = (index: number) => `${baseId}-option-${index}`;
  const nav = useComboboxNavigation(
    results,
    (player) => {
      onSelect(player);
      setQuery('');
    },
    () => setQuery(''),
    optionId,
  );

  return (
    <div className="combobox">
      <label className="field__label" htmlFor={`${baseId}-input`}>{label}</label>
      <div className="combobox__control">
        <Icon name="search" size={16} className="combobox__icon" />
        <input
          {...STATIC_INPUT_PROPS}
          ref={inputRef}
          id={`${baseId}-input`}
          placeholder={placeholder}
          aria-expanded={nav.isExpanded}
          aria-controls={`${baseId}-listbox`}
          aria-activedescendant={nav.isExpanded && nav.active >= 0 ? optionId(nav.active) : undefined}
          aria-describedby={`${baseId}-status`}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            nav.reopen();
          }}
          onFocus={nav.open}
          onBlur={nav.close}
          onKeyDown={nav.handleKeyDown}
        />
        <SearchListbox
          id={`${baseId}-listbox`}
          label={label}
          results={results}
          active={nav.active}
          isExpanded={nav.isExpanded}
          optionId={optionId}
          onHover={nav.setActive}
          onChoose={nav.choose}
        />
      </div>
      <SearchStatusLine id={`${baseId}-status`} isError={isError} message={nav.isOpen || isError ? message : ''} />
    </div>
  );
}
