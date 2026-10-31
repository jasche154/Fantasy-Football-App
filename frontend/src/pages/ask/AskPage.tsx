import { useEffect, useRef, useState } from 'react';
import { getMyLeagues } from '../../api/data';
import type { MyLeague } from '../../api/types';
import { PageHeader } from '../../components/Card';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { useMeta } from '../../state/MetaContext';
import { ChatError } from './ChatError';
import { ChatMessages } from './ChatMessages';
import { Composer } from './Composer';
import { LeaguePicker } from './LeaguePicker';
import { PromptChips } from './PromptChips';
import { buildStarterPrompts } from './starterPrompts';
import { LLM_SETUP_MESSAGE } from './toolCalls';
import { useChat } from './useChat';

const NO_LEAGUES: readonly MyLeague[] = [];

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

export function AskPage() {
  useDocumentTitle('Ask AI');
  const chat = useChat();
  const meta = useMeta();
  const leaguesState = useAsync((signal) => getMyLeagues(signal), []);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isLlmMissing = meta.data?.integrations.llm === false;
  const leagues = leaguesState.data?.leagues ?? NO_LEAGUES;
  const { leagueId, setLeagueId } = chat;

  // A saved focus on a league that's no longer imported would make every request fail.
  useEffect(() => {
    if (leaguesState.status === 'success' && leagueId !== null && !leagues.some((league) => league.id === leagueId)) {
      setLeagueId(null);
    }
  }, [leaguesState.status, leagues, leagueId, setLeagueId]);

  const handleSend = async (text: string) => {
    if (!text.trim() || chat.isPending) return;
    setDraft('');
    const outcome = await chat.send(text);
    if (outcome === 'failed') setDraft((current) => current || text);
    if (outcome === 'ok') inputRef.current?.focus();
  };

  const latest = chat.entries[chat.entries.length - 1];
  const followUps = !chat.isPending && latest?.role === 'assistant' ? latest.suggestions ?? [] : [];
  const retryText = chat.lastFailed ?? draft;

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
        <LeaguePicker leagues={leagues} value={leagueId} isDisabled={chat.isPending} onChange={setLeagueId} />
        <MemoryNote memory={chat.memory} />
        <ChatMessages entries={chat.entries} isPending={chat.isPending} />
        {chat.entries.length === 0 && !chat.isPending && (
          <PromptChips label="Try one of these" prompts={buildStarterPrompts(leagues, leagueId)}
            isDisabled={chat.isPending} onPick={(prompt) => void handleSend(prompt)} />
        )}
        <PromptChips label="Ask next" prompts={followUps} isDisabled={chat.isPending}
          onPick={(prompt) => void handleSend(prompt)} />
        {chat.error && (
          <ChatError error={chat.error} onRetry={retryText.trim() ? () => void handleSend(retryText) : undefined} />
        )}
        <Composer draft={draft} isPending={chat.isPending} inputRef={inputRef} onDraft={setDraft}
          onSend={(text) => void handleSend(text)} />
      </div>
    </div>
  );
}
