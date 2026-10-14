import type { ReactNode } from 'react';

export type DataTableColumn<T> = {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  className?: string;
};

export function DataTable<T extends { id?: number | string }>({
  columns,
  rows,
  empty,
  error,
  loading = false,
  rowKey = (row, index) => row.id ?? index,
  onRowClick,
  sortKey,
  sortDirection,
  onSort,
  getRowClassName,
}: {
  columns: DataTableColumn<T>[];
  rows: T[];
  empty: ReactNode;
  error?: ReactNode;
  loading?: boolean;
  rowKey?: (row: T, index: number) => number | string;
  onRowClick?: (row: T) => void;
  sortKey?: string;
  sortDirection?: 'asc' | 'desc';
  onSort?: (key: string) => void;
  getRowClassName?: (row: T) => string | undefined;
}) {
  return (
    <div className="admin-table-wrap" aria-busy={loading}>
      <table className="admin-table">
        <thead><tr>{columns.map((column) => <th className={column.className} key={column.key}>{onSort ? <button type="button" className="admin-table-sort" onClick={() => onSort(column.key)}>{column.header}{sortKey === column.key ? (sortDirection === 'asc' ? ' ↑' : ' ↓') : ''}</button> : column.header}</th>)}</tr></thead>
        <tbody>
          {error ? <tr><td colSpan={columns.length}><div className="admin-table-empty" role="alert">{error}</div></td></tr> : rows.map((row, index) => <tr className={getRowClassName?.(row)} key={rowKey(row, index)} onClick={onRowClick ? () => onRowClick(row) : undefined} onKeyDown={onRowClick ? (event) => { if (event.key === 'Enter' || event.key === ' ') onRowClick(row); } : undefined} tabIndex={onRowClick ? 0 : undefined}>{columns.map((column) => <td className={column.className} key={column.key}>{column.render(row)}</td>)}</tr>)}
          {!error && !rows.length && <tr><td colSpan={columns.length}><div className="admin-table-empty">{loading ? '…' : empty}</div></td></tr>}
        </tbody>
      </table>
    </div>
  );
}

