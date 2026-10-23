import { useId, useState, type FormEvent } from 'react';
import { exchangeYahooCode, getYahooAuthUrl } from '../../api/actions';
import { errorMessage } from '../../api/client';
import { Icon } from '../../components/Icon';
import { useAction } from '../../hooks/useAction';
import { extractYahooCode, isSafeHttpUrl } from '../../lib/yahoo';
import { YahooLeagues } from './YahooLeagues';

export const CODE_NOT_FOUND_MESSAGE =
  'Couldn’t find a code. Paste the full address you were redirected to (it contains “?code=”) or just the code itself.';

async function fetchSafeAuthUrl(): Promise<string> {
  const { url } = await getYahooAuthUrl();
  if (!isSafeHttpUrl(url)) throw new Error('The backend returned an invalid Yahoo authorization URL.');
  return url;
}

function AuthorizeStep() {
  const authorize = useAction(fetchSafeAuthUrl);
  const handleClick = async () => {
    const url = await authorize.run();
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  };
  return (
    <li className="step">
      <p className="step__title">1. Authorize this app on Yahoo</p>
      <button type="button" className="btn btn--primary" onClick={() => void handleClick()} disabled={authorize.status === 'pending'}>
        <Icon name="external" size={16} /> {authorize.status === 'pending' ? 'Getting link…' : 'Open Yahoo authorization'}
      </button>
      <div aria-live="polite">
        {authorize.status === 'success' && (
          <p className="field__hint">
            A new tab should have opened. If not,{' '}
            <a href={authorize.data} target="_blank" rel="noopener noreferrer">open Yahoo authorization</a>.
            After approving, Yahoo redirects to a page that may not load — that’s expected. Copy its address.
          </p>
        )}
        {authorize.status === 'error' && <p className="error-text" role="alert">{errorMessage(authorize.error)}</p>}
      </div>
    </li>
  );
}

function CodeStep({ onConnected }: { onConnected: () => void }) {
  const id = useId();
  const [input, setInput] = useState('');
  const [validation, setValidation] = useState<string | null>(null);
  const exchange = useAction(exchangeYahooCode);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const code = extractYahooCode(input);
    if (!code) {
      setValidation(CODE_NOT_FOUND_MESSAGE);
      return;
    }
    setValidation(null);
    const result = await exchange.run(code);
    if (result?.connected) onConnected();
  };

  const message = validation ?? (exchange.status === 'error' ? `Connection failed: ${errorMessage(exchange.error)}` : null);
  return (
    <li className="step">
      <form onSubmit={(event) => void handleSubmit(event)} className="code-form">
        <label className="step__title" htmlFor={id}>2. Paste the redirected URL or the code</label>
        <div className="inline-form">
          <input
            id={id}
            className="input"
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="https://localhost/callback?code=…"
            value={input}
            aria-invalid={message !== null}
            aria-describedby={message ? `${id}-error` : undefined}
            onChange={(event) => setInput(event.target.value)}
          />
          <button type="submit" className="btn btn--primary" disabled={exchange.status === 'pending' || !input.trim()}>
            {exchange.status === 'pending' ? 'Connecting…' : 'Connect'}
          </button>
        </div>
        {message && <p id={`${id}-error`} className="error-text" role="alert">{message}</p>}
        {exchange.status === 'success' && <p className="success-text" role="status">Yahoo connected.</p>}
      </form>
    </li>
  );
}

interface YahooConnectProps {
  isConnected: boolean;
  onConnected: () => void;
}

export function YahooConnect({ isConnected, onConnected }: YahooConnectProps) {
  const [isReconnecting, setIsReconnecting] = useState(false);
  if (isConnected && !isReconnecting) {
    return (
      <div className="yahoo">
        <YahooLeagues />
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setIsReconnecting(true)}>
          Reconnect Yahoo
        </button>
      </div>
    );
  }
  return (
    <ol className="steps">
      <AuthorizeStep />
      <CodeStep
        onConnected={() => {
          setIsReconnecting(false);
          onConnected();
        }}
      />
    </ol>
  );
}
