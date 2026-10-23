import { useId, useState } from 'react';
import { Card } from '../../components/Card';
import { SelectField } from '../../components/SelectField';
import {
  SCORING_OPTIONS,
  TE_PREMIUM_MAX,
  TE_PREMIUM_MIN,
  TEAMS_MAX,
  TEAMS_MIN,
  isValidTePremium,
} from '../../state/leagueSettings';
import { useLeagueSettings } from '../../state/SettingsContext';
import { useTheme } from '../../state/ThemeContext';
import { isThemePreference } from '../../state/theme';

const TEAM_OPTIONS = Array.from({ length: TEAMS_MAX - TEAMS_MIN + 1 }, (_, index) => ({
  value: TEAMS_MIN + index,
  label: `${TEAMS_MIN + index} teams`,
}));

const THEME_OPTIONS = [
  { value: 'system', label: 'Match system' },
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
];

export function parseTePremium(raw: string): number | null {
  if (!raw.trim()) return null;
  const value = Number(raw);
  return isValidTePremium(value) ? value : null;
}

function ScoringFieldset() {
  const { settings, updateSettings } = useLeagueSettings();
  const name = useId();
  return (
    <fieldset className="fieldset">
      <legend className="field__label">Scoring</legend>
      <div className="radio-cards">
        {SCORING_OPTIONS.map((option) => (
          <label key={option.value} className="radio-card">
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={settings.scoring === option.value}
              onChange={() => updateSettings({ scoring: option.value })}
            />
            <span className="radio-card__title">{option.short}</span>
            <span className="radio-card__text">{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** Keeps a local draft so partially typed values ("0.") don't fight the stored setting. */
function TePremiumField() {
  const id = useId();
  const { settings, updateSettings } = useLeagueSettings();
  const [draft, setDraft] = useState(String(settings.te_premium));
  const isInvalid = parseTePremium(draft) === null;
  const handleChange = (raw: string) => {
    setDraft(raw);
    const value = parseTePremium(raw);
    if (value !== null) updateSettings({ te_premium: value });
  };
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>TE premium (extra points per TE reception)</label>
      <input
        id={id}
        className="input"
        type="number"
        inputMode="decimal"
        min={TE_PREMIUM_MIN}
        max={TE_PREMIUM_MAX}
        step={0.25}
        value={draft}
        aria-invalid={isInvalid}
        aria-describedby={`${id}-help`}
        onChange={(event) => handleChange(event.target.value)}
      />
      <p id={`${id}-help`} className={isInvalid ? 'field__error' : 'field__hint'}>
        {isInvalid ? `Enter a number from ${TE_PREMIUM_MIN} to ${TE_PREMIUM_MAX}.` : `Between ${TE_PREMIUM_MIN} and ${TE_PREMIUM_MAX}, e.g. 0.5.`}
      </p>
    </div>
  );
}

export function LeagueSettingsForm() {
  const { settings, updateSettings, resetSettings, persisted } = useLeagueSettings();
  const [resetCount, setResetCount] = useState(0);
  const handleReset = () => {
    resetSettings();
    setResetCount((count) => count + 1);
  };
  return (
    <Card title="League settings" eyebrow="Used by rankings, trade values and signals">
      <form className="settings-form" onSubmit={(event) => event.preventDefault()}>
        <ScoringFieldset />
        <SelectField label="Teams" value={settings.teams} options={TEAM_OPTIONS} onChange={(value) => updateSettings({ teams: Number(value) })} />
        <label className="checkbox">
          <input type="checkbox" checked={settings.superflex} onChange={(event) => updateSettings({ superflex: event.target.checked })} />
          <span>
            Superflex
            <span className="checkbox__hint">Adds one QB/RB/WR/TE starting slot, which raises QB value.</span>
          </span>
        </label>
        <TePremiumField key={resetCount} />
        <div className="settings-form__footer">
          <button type="button" className="btn btn--ghost" onClick={handleReset}>Reset to defaults</button>
          <p className="field__hint" aria-live="polite">
            {persisted ? 'Changes apply instantly and are saved in this browser.' : 'Your browser blocked storage, so these settings last for this visit only.'}
          </p>
        </div>
      </form>
    </Card>
  );
}

export function AppearanceForm() {
  const { preference, setPreference } = useTheme();
  return (
    <Card title="Appearance" eyebrow="Theme">
      <SelectField
        label="Theme"
        value={preference}
        options={THEME_OPTIONS}
        onChange={(value) => {
          if (isThemePreference(value)) setPreference(value);
        }}
      />
    </Card>
  );
}
