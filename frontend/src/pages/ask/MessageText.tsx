export type Block =
  | { kind: 'paragraph'; text: string }
  | { kind: 'bullets' | 'numbered'; items: string[] };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

function lineKind(line: string): { kind: Block['kind']; text: string } {
  const bullet = BULLET.exec(line);
  if (bullet) return { kind: 'bullets', text: bullet[1] };
  const numbered = NUMBERED.exec(line);
  if (numbered) return { kind: 'numbered', text: numbered[1] };
  return { kind: 'paragraph', text: line };
}

/** Splits plain text into paragraphs and lists. Blank lines end a paragraph; list lines group. */
export function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) {
      blocks.push({ kind: 'paragraph', text: '' });
      continue;
    }
    const { kind, text: content } = lineKind(line);
    const last = blocks[blocks.length - 1];
    if (last && last.kind === kind && kind !== 'paragraph' && 'items' in last) {
      blocks[blocks.length - 1] = { kind, items: [...last.items, content] };
    } else if (last && last.kind === 'paragraph' && kind === 'paragraph' && last.text) {
      blocks[blocks.length - 1] = { kind, text: `${last.text}\n${content}` };
    } else {
      blocks.push(kind === 'paragraph' ? { kind, text: content } : { kind, items: [content] });
    }
  }
  return blocks.filter((block) => block.kind !== 'paragraph' || block.text);
}

/** Renders the assistant's plain text with real lists. React text nodes only, so no HTML is ever injected. */
export function MessageText({ text }: { text: string }) {
  return (
    <div className="message__text">
      {toBlocks(text).map((block, index) => {
        if (block.kind === 'paragraph') return <p key={index}>{block.text}</p>;
        const items = block.items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>);
        return block.kind === 'bullets' ? <ul key={index}>{items}</ul> : <ol key={index}>{items}</ol>;
      })}
    </div>
  );
}
