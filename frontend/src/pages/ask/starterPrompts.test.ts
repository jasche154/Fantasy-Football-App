import { describe, expect, it } from 'vitest';
import { league } from './askFixtures';
import { buildStarterPrompts, cleanLeagueName } from './starterPrompts';
import { EXAMPLE_PROMPTS } from './toolCalls';

describe('buildStarterPrompts', () => {
  it('falls back to the generic examples without leagues', () => {
    expect(buildStarterPrompts([])).toEqual(EXAMPLE_PROMPTS);
  });

  it('asks the core questions for a single league, naming it and the week', () => {
    const prompts = buildStarterPrompts([league(1, 'Chen Family League')]);
    expect(prompts).toHaveLength(4);
    expect(prompts[0]).toBe('Who should I start in week 9 in Chen Family League?');
    expect(prompts.every((prompt) => prompt.includes('Chen Family League'))).toBe(true);
  });

  it('says "this week" when the week is unknown', () => {
    expect(buildStarterPrompts([league(1, 'Tintin', { week: null })])[0]).toBe('Who should I start in this week in Tintin?');
  });

  it('covers each league when there are several, and only the focused one when set', () => {
    const leagues = [league(1, 'Chen'), league(2, 'Tintin')];
    const all = buildStarterPrompts(leagues);
    expect(all.filter((prompt) => prompt.includes('Chen'))).toHaveLength(2);
    expect(all.filter((prompt) => prompt.includes('Tintin'))).toHaveLength(2);
    expect(all).toContain(EXAMPLE_PROMPTS[0]);
    expect(buildStarterPrompts(leagues, 2).every((prompt) => prompt.includes('Tintin'))).toBe(true);
  });

  it('flattens and shortens league names from Yahoo', () => {
    expect(cleanLeagueName('Bad\nName​  here')).toBe('Bad Name here');
    expect(cleanLeagueName('x'.repeat(60))).toHaveLength(40);
  });
});
