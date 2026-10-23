import { Icon } from '../components/Icon';
import { useTheme } from '../state/ThemeContext';

export function ThemeToggle() {
  const { resolved, toggle } = useTheme();
  const next = resolved === 'dark' ? 'light' : 'dark';
  return (
    <button
      type="button"
      className="icon-btn theme-toggle"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      <Icon name={resolved === 'dark' ? 'sun' : 'moon'} />
    </button>
  );
}
