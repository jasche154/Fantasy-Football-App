import { PageHeader } from '../components/Card';
import { EmptyState } from '../components/StatusViews';
import { useDocumentTitle } from '../hooks/useDocumentTitle';
import { buildHash, paths } from '../router/routes';

export function NotFoundPage({ path }: { path: string }) {
  useDocumentTitle('Page not found');
  return (
    <>
      <PageHeader title="Page not found" />
      <EmptyState title={`Nothing lives at “${path}”.`}>
        <a href={buildHash(paths.dashboard)}>Back to the dashboard</a>
      </EmptyState>
    </>
  );
}
