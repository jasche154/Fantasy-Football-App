import type { MyLeague } from '../../api/types';
import { EXAMPLE_PROMPTS } from './toolCalls';

const MAX_NAME_LENGTH = 40;

/** League names come from Yahoo: keep them to one short printable line before they become a question. */
export function cleanLeagueName(name: string): string {
  const flat = name.replace(/[\p{Cc}\p{Cf}]/gu, ' ').replace(/\s+/g, ' ').trim();
  return flat.length > MAX_NAME_LENGTH ? `${flat.slice(0, MAX_NAME_LENGTH - 1)}…` : flat;
}

function weekText(week: number | null): string {
  return week ? `week ${week}` : 'this week';
}

function leaguePrompts(league: MyLeague): string[] {
  const name = cleanLeagueName(league.name);
  const week = weekText(league.week);
  return [
    `Who should I start in ${week} in ${name}?`,
    `Best waiver pickup and who to drop in ${name}?`,
    `Am I favored in my ${week} matchup in ${name}?`,
    `What are my playoff odds in ${name}?`,
  ];
}

/**
 * One-click questions for an empty chat. One league (or the league the chat is focused on)
 * gets its four core questions; several leagues get start/sit and matchup for each, plus one
 * general question. No leagues: the generic examples.
 */
export function buildStarterPrompts(leagues: readonly MyLeague[], focusId: number | null = null): string[] {
  const focused = leagues.find((league) => league.id === focusId);
  if (focused) return leaguePrompts(focused);
  if (leagues.length === 0) return [...EXAMPLE_PROMPTS];
  if (leagues.length === 1) return leaguePrompts(leagues[0]);
  const perLeague = leagues.flatMap((league) => {
    const [startSit, , matchup] = leaguePrompts(league);
    return [startSit, matchup];
  });
  return [...perLeague, EXAMPLE_PROMPTS[0]];
}
