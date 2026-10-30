import { useId, useState, type KeyboardEvent } from 'react';
import { ApiError } from '../../api/client';
import { PageHeader } from '../../components/Card';
import { Icon } from '../../components/Icon';
import { ErrorState } from '../../components/StatusViews';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { useMeta } from '../../state/MetaContext';
import { ChatMessages } from './ChatMessages';
import { EXAMPLE_PROMPTS, LLM_SETUP_MESSAGE } from './toolCalls';
import { useChat } from './useChat';

interface ComposerProps {
  draft: string;
  isPending: boolean;
  onDraft: (value: string) => void;
  onSend: (text: string) => void;
}

function Composer({ draft, isPending, onDraft, onSend }: ComposerProps) {
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
        className="composer__input"
        rows={2}
        placeholder="Ask about players, trades or matchups…"
        value={draft}
        aria-describedby={`${id}-hint`}
        onChange={(event) => onDraft(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <div className="composer__footer">
        <span id={`${id}-hint`} className="composer__hint">Enter to send · Shift+Enter for a new line</span>
        {/* aria-disabled (not disabled) keeps focus on the button while a reply is pending. */}
        <button type="submit" className="btn btn--primary" aria-disabled={isPending || !draft.trim()}>
          <Icon name="send" size={16} /> Send
        </button>
      </div>
    </form>
  );
}

function ExamplePrompts({ onPick, isDisabled }: { onPick: (text: string) => void; isDisabled: boolean }) {
  return (
    <div className="examples">
      <p className="examples__label">Try one of these</p>
      <ul className="examples__list">
        {EXAMPLE_PROMPTS.map((prompt) => (
          <li key={prompt}>
            <button type="button" className="chip-btn" disabled={isDisabled} onClick={() => onPick(prompt)}>{prompt}</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MemoryNote({ memory }: { memory: readonly string[] }) {
  if (memory.length === 0) return null;
  return (
    <section className="chat-memory" aria-label="Keeping in mind">
      <p className="chat-memory__label">Keeping in mind for this chat</p>
      <ul className="chat-memory__list">
        {memory.map((fact) => <li key={fact} className="chip">{fact}</li>)}
      </ul>
    </section>
  );
}

function chatErrorMessage(error: Error): string | undefined {
  return error instanceof ApiError && error.status === 503 ? LLM_SETUP_MESSAGE : undefined;
}

export function AskPage() {
  useDocumentTitle('Ask AI');
  const chat = useChat();
  const meta = useMeta();
  const [draft, setDraft] = useState('');
  const isLlmMissing = meta.data?.integrations.llm === false;

  const handleSend = async (text: string) => {
    if (!text.trim() || chat.isPending) return;
    setDraft('');
    const outcome = await chat.send(text);
    if (outcome === 'failed') setDraft((current) => current || text);
  };

  return (
    <div className="ask">
      <PageHeader
        title="Ask AI"
        subtitle="Answers come from tool calls against the same data as the rest of the app — the model explains, it doesn’t invent numbers."
        actions={chat.entries.length > 0 && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={chat.reset}>New chat</button>
        )}
      />
      {isLlmMissing && <p className="notice" role="note">{LLM_SETUP_MESSAGE}</p>}
      <div className="chat">
        <MemoryNote memory={chat.memory} />
        <ChatMessages entries={chat.entries} isPending={chat.isPending} />
        {chat.entries.length === 0 && !chat.isPending && (
          <ExamplePrompts onPick={(prompt) => void handleSend(prompt)} isDisabled={chat.isPending} />
        )}
        {chat.error && (
          <ErrorState
            error={chat.error}
            title={chat.error instanceof ApiError && chat.error.status === 503 ? 'AI assistant not configured' : 'The assistant couldn’t answer'}
            message={chatErrorMessage(chat.error)}
            onRetry={draft.trim() ? () => void handleSend(draft) : undefined}
          />
        )}
        <Composer draft={draft} isPending={chat.isPending} onDraft={setDraft} onSend={(text) => void handleSend(text)} />
      </div>
    </div>
  );
}
