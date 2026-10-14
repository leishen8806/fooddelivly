import type { ReactNode } from 'react';
import { useEffect } from 'react';

export function Drawer({ open, title, children, onClose }: { open: boolean; title: ReactNode; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose, open]);

  if (!open) return null;
  return <div className="admin-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><aside className="admin-drawer" role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined}><header><h2>{title}</h2><button type="button" onClick={onClose} aria-label="Close">×</button></header><div className="admin-drawer-body">{children}</div></aside></div>;
}

