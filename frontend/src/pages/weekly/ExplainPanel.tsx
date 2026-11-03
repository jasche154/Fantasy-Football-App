import { getProjectionExplanation } from '../../api/data';
import type { ThresholdOdds } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useLeagueSettings } from '../../state/SettingsContext';
import { oddsLabel } from './thresholdOdds';

interface ExplainPanelProps {
  playerId: number;
  week: number;
}

/** "10+ pts: 72%", "15+ pts: 48%", ... from the same range as the floor and ceiling. */
function OddsLadder({ name, odds }: { name: string; odds: readonly ThresholdOdds[] | undefined }) {
  if (!odds?.length) return null;
  return (
    <div className="threshold-ladder">
      <span className="explain-step__label">Chance to score</span>
      <ul aria-label={`${name}'s chance to score`}>
        {odds.map((item) => (
          <li key={item.points}>{oddsLabel(item)}</li>
        ))}
      </ul>
      <span className="explain-step__detail">From the same range as the floor and ceiling, including the chance he sits.</span>
    </div>
  );
}

/** "Why this projection?": every step from recent production to the final number. */
export function ExplainPanel({ playerId, week }: ExplainPanelProps) {
  const { settings } = useLeagueSettings();
  const explanation = useAsync((signal) => getProjectionExplanation(playerId, settings, week, signal), [playerId, week, settings]);
  return (
    <AsyncView state={explanation} loading={<Skeleton lines={4} label="Explaining…" />} errorTitle="Couldn't explain this projection">
      {(data) => (
        <>
          <ol className="explain-steps" aria-label={`Why ${data.player.name} projects ${data.projection}`}>
            {data.steps.map((step) => (
              <li key={step.label} className="explain-step">
                <span className="explain-step__label">{step.label}</span>
                <strong className="explain-step__value">{step.value}</strong>
                <span className="explain-step__detail">{step.detail}</span>
              </li>
            ))}
          </ol>
          <OddsLadder name={data.player.name} odds={data.threshold_odds} />
        </>
      )}
    </AsyncView>
  );
}
