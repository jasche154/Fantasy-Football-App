import { getProjectionExplanation } from '../../api/data';
import { AsyncView } from '../../components/AsyncView';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useLeagueSettings } from '../../state/SettingsContext';

interface ExplainPanelProps {
  playerId: number;
  week: number;
}

/** "Why this projection?": every step from recent production to the final number. */
export function ExplainPanel({ playerId, week }: ExplainPanelProps) {
  const { settings } = useLeagueSettings();
  const explanation = useAsync((signal) => getProjectionExplanation(playerId, settings, week, signal), [playerId, week, settings]);
  return (
    <AsyncView state={explanation} loading={<Skeleton lines={4} label="Explaining…" />} errorTitle="Couldn't explain this projection">
      {(data) => (
        <ol className="explain-steps" aria-label={`Why ${data.player.name} projects ${data.projection}`}>
          {data.steps.map((step) => (
            <li key={step.label} className="explain-step">
              <span className="explain-step__label">{step.label}</span>
              <strong className="explain-step__value">{step.value}</strong>
              <span className="explain-step__detail">{step.detail}</span>
            </li>
          ))}
        </ol>
      )}
    </AsyncView>
  );
}
