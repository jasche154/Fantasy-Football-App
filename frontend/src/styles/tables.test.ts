import { describe, expect, it } from 'vitest';

// The test config turns CSS imports off (even ?raw), and the app has no Node types: read the file through Node directly.
type Fs = { readFileSync(path: string, encoding: 'utf8'): string };
declare const process: { cwd(): string };

describe('tables.css', () => {
  it('makes the table scroller the containing block, so screen-reader-only text inside a table never widens the page', async () => {
    const { readFileSync } = (await import(/* @vite-ignore */ `node:${'fs'}`)) as Fs;
    const css = readFileSync(`${process.cwd()}/src/styles/tables.css`, 'utf8');
    // .sr-only is position: absolute; without a positioned scroller it lays out against the page and escapes the clip.
    const rule = css.match(/\.table-wrap\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(rule).toMatch(/position:\s*relative/);
  });
});
