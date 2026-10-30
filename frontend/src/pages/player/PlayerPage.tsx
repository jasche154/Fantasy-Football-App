import { ApiError } from '../../api/client';
import { getPlayer, getPlayerNews } from '../../api/data';
import type { PlayerProfile } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { NewsList } from '../../components/NewsList';
import { PositionChip } from '../../components/PlayerLink';
import { SelectField } from '../../components/SelectField';
import { EmptyState, ErrorState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { paths, parsePositiveInt } from '../../router/routes';
import { navigate } from '../../router/useHashLocation';
import { scoringLabel } from '../../state/leagueSettings';
import { useMeta } from '../../state/MetaContext';
import { useLeagueSettings } from '../../state/SettingsContext';
import { GameLogTable } from './GameLogTable';
import { AdvancedStats, ProjectionPanel, SignalsPanel } from './PlayerInsights';
import { StatTiles } from './StatTiles';
import { WeeklyChart } from './WeeklyChart';

interface SeasonSelectProps {
  playerId: number;
  season: number;
}

function SeasonSelect({ playerId, season }: SeasonSelectProps) {
  const meta = useMeta();
  const seasons = meta.data ? [...meta.data.seasons].sort((a, b) => b - a) : [];
  const values = seasons.includes(season) ? seasons : [season, ...seasons];
  return (
    <SelectField
      label="Season"
      isInline
      value={season}
      options={values.map((value) => ({ value, label: String(value) }))}
      onChange={(value) => navigate(paths.player(playerId), { season: value }, { replace: true })}
    />
  );
}

function PlayerHeader({ profile, selectedSeason }: { profile: PlayerProfile; selectedSeason: number | undefined }) {
  const { player } = profile;
  return (
    <header className="page-header player-header">
      <div className="page-header__text">
        <p className="player-header__eyebrow">
          <PositionChip position={player.position} size="md" />
          <span>{player.team ?? 'Free agent'}</span>
          <span aria-hidden="true">·</span>
          <span>{scoringLabel(profile.scoring)} scoring</span>
        </p>
        <h1 className="page-header__title" tabIndex={-1} data-page-heading>{player.name}</h1>
      </div>
      <div className="page-header__actions">
        <SeasonSelect playerId={player.id} season={selectedSeason ?? profile.season} />
      </div>
    </header>
  );
}

function PlayerNews({ playerId }: { playerId: number }) {
  const news = useAsync((signal) => getPlayerNews(playerId, signal), [playerId]);
  if (news.error || (news.data && news.data.items.length === 0)) return null;      // news is extra: hide when missing
  return (
    <Card title="Latest news" eyebrow="Via Sleeper">
      <AsyncView state={news} loading={<Skeleton lines={3} label="Loading news…" />}>
        {(d) => <NewsList items={d.items.slice(0, 5)} />}
      </AsyncView>
    </Card>
  );
}

function PlayerBody({ profile, selectedSeason }: { profile: PlayerProfile; selectedSeason: number | undefined }) {
  return (
    <>
      <PlayerHeader profile={profile} selectedSeason={selectedSeason} />
      <StatTiles summary={profile.summary} />
      <div className="player-grid">
        <Card title="Weekly points" eyebrow="Actual vs expected" className="player-grid__chart">
          {profile.game_log.length === 0 ? (
            <EmptyState title={`No games logged in ${profile.season}.`} />
          ) : (
            <WeeklyChart rows={profile.game_log} playerName={profile.player.name} season={profile.season} />
          )}
        </Card>
        <div className="player-grid__side">
          <ProjectionPanel profile={profile} />
          <SignalsPanel signals={profile.signals} />
        </div>
      </div>
      <PlayerNews playerId={profile.player.id} />
      <Card title="Advanced stats" eyebrow="Next Gen Stats & usage">
        <AdvancedStats advanced={profile.advanced} />
      </Card>
      <Card title="Game log" eyebrow={`${profile.season} · ${scoringLabel(profile.scoring)}`}>
        {profile.game_log.length === 0 ? (
          <EmptyState title="No games logged for this season." />
        ) : (
          <GameLogTable rows={profile.game_log} />
        )}
      </Card>
    </>
  );
}

interface PlayerPageProps {
  playerId: number;
  query: Readonly<Record<string, string>>;
}

export function PlayerPage({ playerId, query }: PlayerPageProps) {
  const { settings } = useLeagueSettings();
  const season = parsePositiveInt(query.season) ?? undefined;
  const profile = useAsync(
    (signal) => getPlayer(playerId, settings, season, signal),
    [playerId, season, settings],
  );
  useDocumentTitle(profile.data?.player.name ?? 'Player');
  return (
    <AsyncView
      state={profile}
      loading={<Skeleton lines={8} label="Loading player…" />}
      renderError={(error, retry) => (
        <>
          <h1 className="page-header__title" tabIndex={-1} data-page-heading>Player</h1>
          {error instanceof ApiError && error.status === 404 ? (
            <ErrorState error={error} title="Player not found" message={`No player with id ${playerId} exists.`} />
          ) : (
            <ErrorState error={error} title="Couldn't load this player" onRetry={retry} />
          )}
        </>
      )}
    >
      {(data) => <PlayerBody profile={data} selectedSeason={season} />}
    </AsyncView>
  );
}
