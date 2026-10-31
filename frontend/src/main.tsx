import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles/tokens.css';
import './styles/base.css';
import './styles/layout.css';
import './styles/components.css';
import './styles/tables.css';
import './styles/weekly.css';
import './styles/charts.css';
import './styles/dashboard.css';
import './styles/trade.css';
import './styles/player.css';
import './styles/chat.css';
import './styles/settings.css';
import './styles/team.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Missing #root element in index.html.');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
