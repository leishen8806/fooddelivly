import type { ReactNode } from 'react';

export function Toolbar({ children, actions }: { children?: ReactNode; actions?: ReactNode }) {
  return <div className="admin-toolbar"><div className="admin-toolbar-filters">{children}</div>{actions && <div className="admin-toolbar-actions">{actions}</div>}</div>;
}

