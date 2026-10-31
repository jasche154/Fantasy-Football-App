export type Block =
  | { kind: 'paragraph'; text: string }
  | { kind: 'bullets'; items: string[] }
  | { kind: 'numbered'; start: number; items: string[] };

type Line = { kind: 'paragraph' | 'bullets'; text: string } | { kind: 'numbered'; text: string; number: number };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/;

function parseLine(line: string): Line {
  const bullet = BULLET.exec(line);
  if (bullet) return { kind: 'bullets', text: bullet[1] };
  const numbered = NUMBERED.exec(line);
  if (numbered) return { kind: 'numbered', text: numbered[2], number: Number(numbered[1]) };
  return { kind: 'paragraph', text: line };
}

function startBlock(line: Line): Block {
  if (line.kind === 'numbered') return { kind: 'numbered', start: line.number, items: [line.text] };
  return line.kind === 'bullets' ? { kind: 'bullets', items: [line.text] } : { kind: 'paragraph', text: line.text };
}

/** Joins `line` onto `last` when it continues it, or returns null to start a new block. */
function extend(last: Block | undefined, line: Line, afterBlank: boolean): Block | null {
  if (!last) return null;
  if (last.kind === 'paragraph') {
    return line.kind === 'paragraph' && !afterBlank ? { ...last, text: `${last.text}\n${line.text}` } : null;
  }
  // A blank line between list items keeps the same list.
  return last.kind === line.kind ? { ...last, items: [...last.items, line.text] } : null;
}

/** Splits plain text into paragraphs and lists. Blank lines end a paragraph; list lines group. */
export function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let afterBlank = false;
  for (const raw of text.split('\n')) {
    if (!raw.trim()) {
      afterBlank = true;
      continue;
    }
    const line = parseLine(raw);
    const extended = extend(blocks[blocks.length - 1], line, afterBlank);
    if (extended) blocks[blocks.length - 1] = extended;
    else blocks.push(startBlock(line));
    afterBlank = false;
  }
  return blocks;
}

/** Renders the assistant's plain text with real lists. React text nodes only, so no HTML is ever injected. */
export function MessageText({ text }: { text: string }) {
  return (
    <div className="message__text">
      {toBlocks(text).map((block, index) => {
        if (block.kind === 'paragraph') return <p key={index}>{block.text}</p>;
        const items = block.items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>);
        return block.kind === 'bullets'
          ? <ul key={index}>{items}</ul>
          : <ol key={index} start={block.start}>{items}</ol>;
      })}
    </div>
  );
}
