import { useId, type KeyboardEvent, type Ref } from 'react';
import { Icon } from '../../components/Icon';

/** The server accepts questions up to this length. */
export const MAX_QUESTION_CHARS = 4000;
const SHOW_COUNT_AFTER = 3500;

interface ComposerProps {
  draft: string;
  isPending: boolean;
  inputRef?: Ref<HTMLTextAreaElement>;
  onDraft: (value: string) => void;
  onSend: (text: string) => void;
}

export function Composer({ draft, isPending, inputRef, onDraft, onSend }: ComposerProps) {
  const id = useId();
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    onSend(draft);
  };
  return (
    <form className="composer" onSubmit={(event) => { event.preventDefault(); onSend(draft); }}>
      <label htmlFor={id} className="sr-only">Ask a fantasy football question</label>
      <textarea
        id={id}
        ref={inputRef}
        className="composer__input"
        rows={2}
        maxLength={MAX_QUESTION_CHARS}
        placeholder="Ask about players, trades or matchups…"
        value={draft}
        aria-describedby={`${id}-hint`}
        onChange={(event) => onDraft(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <div className="composer__footer">
        <span id={`${id}-hint`} className="composer__hint">
          Enter to send · Shift+Enter for a new line
          {draft.length > SHOW_COUNT_AFTER && ` · ${draft.length} / ${MAX_QUESTION_CHARS}`}
        </span>
        {/* aria-disabled (not disabled) keeps focus on the button while a reply is pending. */}
        <button type="submit" className="btn btn--primary" aria-disabled={isPending || !draft.trim()}>
          <Icon name="send" size={16} /> Send
        </button>
      </div>
    </form>
  );
}
