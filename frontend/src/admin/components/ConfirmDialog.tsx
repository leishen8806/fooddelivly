import type { ReactNode } from 'react';
import { useState } from 'react';

export function ConfirmDialog({ open, title, description, confirmLabel, requireReason = false, onConfirm, onClose }: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel: ReactNode;
  requireReason?: boolean;
  onConfirm: (reason?: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState('');
  if (!open) return null;
  const canConfirm = !requireReason || reason.trim().length > 0;
  return <div className="admin-dialog-backdrop" role="presentation"><section className="admin-dialog" role="dialog" aria-modal="true" aria-labelledby="admin-confirm-title"><h2 id="admin-confirm-title">{title}</h2>{description && <p>{description}</p>}{requireReason && <label>Reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} required /></label>}<div className="admin-dialog-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" type="button" disabled={!canConfirm} onClick={() => onConfirm(requireReason ? reason.trim() : undefined)}>{confirmLabel}</button></div></section></div>;
}

