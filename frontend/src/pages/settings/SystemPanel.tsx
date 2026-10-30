import { useId, useState } from 'react';
import { backupNow, getSystemStatus } from '../../api/actions';
import { getApiToken, setApiToken } from '../../api/client';
import type { SystemStatus } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useMeta } from '../../state/MetaContext';

function when(iso: string | undefined | null): string {
  if (!iso) return 'never';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/** The backend's optional API_TOKEN. Shown when the backend asks for one, or once a token is saved. */
export function ApiTokenForm() {
  const meta = useMeta();
  const id = useId();
  const [draft, setDraft] = useState('');
  const hasToken = Boolean(getApiToken());
  if (!meta.data?.auth_required && !hasToken) return null;
  const save = (token: string | null) => {
    setApiToken(token);
    window.location.reload();                 // every page re-fetches with the new header
  };
  return (
    <Card title="API access" eyebrow="Token">
      <p className="hint">
        The backend has <code>API_TOKEN</code> set, so each request needs it. It's saved in this browser only.
      </p>
      <form className="token-form" onSubmit={(event) => { event.preventDefault(); if (draft.trim()) save(draft.trim()); }}>
        <label htmlFor={id}>API token</label>
        <input id={id} type="password" autoComplete="off" value={draft} placeholder={hasToken ? 'Saved' : ''}
          onChange={(event) => setDraft(event.target.value)} />
        <button type="submit" className="btn btn--primary btn--sm" disabled={!draft.trim()}>Save</button>
        {hasToken && <button type="button" className="btn btn--ghost btn--sm" onClick={() => save(null)}>Forget</button>}
      </form>
    </Card>
  );
}

function StatusRows({ data, onBackup, backupMessage }: { data: SystemStatus; onBackup: () => void; backupMessage: string | null }) {
  const update = data.auto_update;
  const backup = data.backup;
  return (
    <dl className="status-list">
      <dt>Auto-update</dt>
      <dd>
        {!update.enabled ? 'Off (AUTO_UPDATE=false)' : update.at ? (
          <>
            Last check {when(update.at)}{update.ok === false ? `: failed (${update.error})` : ''}.
            {update.loaded_week ? ` Loaded week ${update.loaded_week} stats.` : ''}
            {update.snapshot_week ? ` Saved week ${update.snapshot_week} projections.` : ''}
            {update.synced_leagues ? ` Synced ${update.synced_leagues} Yahoo leagues.` : ''}
            {update.next_check_hours ? ` Runs every ${update.next_check_hours} hours.` : ''}
          </>
        ) : 'Not run yet (first check about 90 seconds after the backend starts).'}
      </dd>
      <dt>Data</dt>
      <dd>
        {data.data.season ?? '—'} through week {data.data.latest_week ?? '—'}; last load {when(data.data.last_load)};
        weights learned through week {data.data.weights_learned_through_week ?? '—'}.
      </dd>
      <dt>Odds API credits</dt>
      <dd>{data.odds_credits ? `${data.odds_credits.remaining} left (as of ${when(data.odds_credits.updated_at)})` : 'Unknown until the next props purchase.'}</dd>
      <dt>Backups</dt>
      <dd>
        {!backup.enabled ? 'Off (BACKUPS=false). ' : ''}
        {backup.at ? `Last ${when(backup.at)}${backup.ok === false ? ` failed (${backup.error})` : ` (${backup.file}, ${backup.size_mb} MB, ${backup.count} kept of ${backup.keep})`}.` : 'None yet.'}
        {' '}<button type="button" className="btn btn--ghost btn--sm" onClick={onBackup}>Back up now</button>
        {backupMessage && <span className="hint" role="status"> {backupMessage}</span>}
      </dd>
    </dl>
  );
}

export function SystemPanel() {
  const status = useAsync((signal) => getSystemStatus(signal), []);
  const [backupMessage, setBackupMessage] = useState<string | null>(null);
  const runBackup = async () => {
    setBackupMessage('Backing up…');
    try {
      const result = await backupNow();
      setBackupMessage(`Saved ${result.file}.`);
      status.reload();
    } catch {
      setBackupMessage('Backup failed. Check the backend log.');
    }
  };
  return (
    <Card title="System" eyebrow="Background jobs & backups">
      <AsyncView state={status} loading={<Skeleton lines={4} label="Checking the backend…" />} errorTitle="Couldn't read the status">
        {(data) => <StatusRows data={data} onBackup={() => void runBackup()} backupMessage={backupMessage} />}
      </AsyncView>
    </Card>
  );
}
