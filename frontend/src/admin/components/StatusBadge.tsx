export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <span className={`admin-status-badge admin-status-${status.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}>{label || status}</span>;
}

