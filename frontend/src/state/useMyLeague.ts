import { useEffect, useState } from 'react';
import { getMyLeagues } from '../api/data';
import type { MyLeague, MyLeaguesResponse } from '../api/types';
import { useAsync, type AsyncResult } from '../hooks/useAsync';

const STORAGE_KEY = 'gridiron.myLeagueId';

function readStored(): number | null {
  try {
    const value = Number(window.localStorage.getItem(STORAGE_KEY));
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch {
    return null;                       // storage blocked (private mode): just don't remember
  }
}

function store(id: number): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(id));
  } catch {
    // not remembered; harmless
  }
}

export interface MyLeagueState {
  leagues: AsyncResult<MyLeaguesResponse>;
  /** The chosen league (remembered across pages), or the first one; null when none are imported. */
  league: MyLeague | null;
  choose: (id: number) => void;
}

/** The user's imported Yahoo leagues and which one the My Team / Waivers pages show. */
export function useMyLeague(): MyLeagueState {
  const leagues = useAsync((signal) => getMyLeagues(signal), []);
  const [chosen, setChosen] = useState<number | null>(readStored);
  const list = leagues.data?.leagues ?? [];
  const league = list.find((l) => l.id === chosen) ?? list[0] ?? null;
  useEffect(() => {
    if (league && league.id !== chosen) setChosen(league.id);
  }, [league, chosen]);
  return {
    leagues,
    league,
    choose: (id: number) => {
      store(id);
      setChosen(id);
    },
  };
}
