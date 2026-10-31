interface PromptChipsProps {
  label: string;
  prompts: readonly string[];
  isDisabled: boolean;
  onPick: (text: string) => void;
}

/** Starter questions for an empty chat, and follow-ups under the latest reply. */
export function PromptChips({ label, prompts, isDisabled, onPick }: PromptChipsProps) {
  const unique = [...new Set(prompts)];
  if (unique.length === 0) return null;
  return (
    <section className="examples" aria-label={label}>
      <p className="examples__label">{label}</p>
      <ul className="examples__list">
        {unique.map((prompt) => (
          <li key={prompt}>
            <button type="button" className="chip-btn" disabled={isDisabled} onClick={() => onPick(prompt)}>{prompt}</button>
          </li>
        ))}
      </ul>
    </section>
  );
}
