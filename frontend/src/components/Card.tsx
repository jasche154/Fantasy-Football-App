import { useId, type ReactNode } from 'react';

interface CardProps {
  title: ReactNode;
  /** Small label shown above the title, e.g. data provenance ("Model", "Sleeper"). */
  eyebrow?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  headingLevel?: 2 | 3;
}

export function Card({ title, eyebrow, actions, children, className, headingLevel = 2 }: CardProps) {
  const headingId = useId();
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <section className={className ? `card ${className}` : 'card'} aria-labelledby={headingId}>
      <header className="card__header">
        <div>
          {eyebrow && <p className="card__eyebrow">{eyebrow}</p>}
          <Heading id={headingId} className="card__title">
            {title}
          </Heading>
        </div>
        {actions && <div className="card__actions">{actions}</div>}
      </header>
      <div className="card__body">{children}</div>
    </section>
  );
}

interface PageHeaderProps {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        <h1 className="page-header__title" tabIndex={-1} data-page-heading>
          {title}
        </h1>
        {subtitle && <p className="page-header__subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}
