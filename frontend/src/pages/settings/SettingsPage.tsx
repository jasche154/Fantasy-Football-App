import type { ReactNode } from 'react';
import { getIntegrationsStatus } from '../../api/actions';
import type { IntegrationsStatus } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card, PageHeader } from '../../components/Card';
import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { AppearanceForm, LeagueSettingsForm } from './LeagueSettingsForm';
import { YahooConnect } from './YahooConnect';

interface IntegrationRowProps {
  name: string;
  isOk: boolean;
  okLabel: string;
  missingLabel: string;
  children: ReactNode;
}

function IntegrationRow({ name, isOk, okLabel, missingLabel, children }: IntegrationRowProps) {
  return (
    <li className="integration">
      <div className="integration__head">
        <p className="integration__name">{name}</p>
        <span className={isOk ? 'status-badge status-badge--ok' : 'status-badge status-badge--missing'}>
          <Icon name={isOk ? 'check' : 'alert'} size={14} />
          {isOk ? okLabel : missingLabel}
        </span>
      </div>
      <div className="integration__body">{children}</div>
    </li>
  );
}

function IntegrationList({ status, onChanged }: { status: IntegrationsStatus; onChanged: () => void }) {
  return (
    <ul className="integration-list">
      <IntegrationRow name="FantasyPros" isOk={status.fantasypros} okLabel="Configured" missingLabel="Not configured">
        {status.fantasypros ? (
          <p>Expert consensus rankings come from the FantasyPros API.</p>
        ) : (
          <p>
            Add <code>FANTASYPROS_API_KEY</code> to <code>backend/.env</code> and restart the backend. Until then, free
            weekly ECR from nflverse is used.
          </p>
        )}
      </IntegrationRow>
      <IntegrationRow name="Vegas player props (The Odds API)" isOk={status.odds_api} okLabel="Configured" missingLabel="Not configured">
        {status.odds_api ? (
          <p>Week X rankings include Vegas player props, the most heavily weighted source.</p>
        ) : (
          <p>
            Add <code>ODDS_API_KEY</code> (free at the-odds-api.com, 500 credits/month) to <code>backend/.env</code> and
            restart the backend. Until then, Week X rankings blend the other sources.
          </p>
        )}
      </IntegrationRow>
      <IntegrationRow name="AI assistant (Groq)" isOk={status.llm} okLabel="Configured" missingLabel="Not configured">
        {status.llm ? (
          <p>Ask AI is ready.</p>
        ) : (
          <p>Add <code>GROQ_API_KEY</code> to <code>backend/.env</code> and restart the backend.</p>
        )}
      </IntegrationRow>
      <IntegrationRow
        name="Yahoo Fantasy"
        isOk={status.yahoo_connected}
        okLabel="Connected"
        missingLabel={status.yahoo_configured ? 'Not connected' : 'Not configured'}
      >
        {status.yahoo_configured ? (
          <YahooConnect isConnected={status.yahoo_connected} onConnected={onChanged} />
        ) : (
          <p>
            Add <code>YAHOO_CLIENT_ID</code> and <code>YAHOO_CLIENT_SECRET</code> to <code>backend/.env</code> to enable
            league import.
          </p>
        )}
      </IntegrationRow>
    </ul>
  );
}

function IntegrationsPanel() {
  const status = useAsync((signal) => getIntegrationsStatus(signal), []);
  return (
    <Card title="Integrations" eyebrow="Data sources & accounts">
      <AsyncView state={status} loading={<Skeleton lines={4} label="Checking integrations…" />} errorTitle="Couldn't check integrations">
        {(data) => <IntegrationList status={data} onChanged={status.reload} />}
      </AsyncView>
    </Card>
  );
}

export function SettingsPage() {
  useDocumentTitle('Settings');
  return (
    <>
      <PageHeader title="Settings" subtitle="League format, appearance and connected services." />
      <div className="settings-grid">
        <LeagueSettingsForm />
        <div className="settings-grid__side">
          <AppearanceForm />
          <IntegrationsPanel />
        </div>
      </div>
    </>
  );
}
