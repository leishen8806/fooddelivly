import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import {
  CustomerOrderIssueBadge,
  CustomerOrderPaymentProofAction,
  CustomerOrderPaymentSummary,
  CustomerOrderShortageBlock,
  type CustomerOrderDetails,
} from './App';

vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined });
vi.stubGlobal('document', { documentElement: { lang: '' } });
const { default: i18n } = await import('./i18n');

const order = (overrides: Partial<CustomerOrderDetails> = {}): CustomerOrderDetails => ({
  public_code: 'ORD-1', room_number: '101', order_status: 'PREPARING', payment_status: 'PARTIALLY_REFUNDED',
  currency: 'USD', total_minor: 700, paid_confirmed_minor: 1000,
  refunded_done_minor: 200, refund_pending_minor: 100, wallet_paid_minor: 400, external_due_minor: 600,
  created_at: '2026-01-01T00:00:00Z', items: [], proof_status: null, issues: [], ...overrides,
});

const render = (node: ReactNode) => renderToStaticMarkup(<I18nextProvider i18n={i18n}>{node}</I18nextProvider>);

describe('customer order payment and shortage states', () => {
  it('renders receipt totals instead of ABA due for confirmed payment', () => {
    const html = render(<CustomerOrderPaymentSummary order={order()} />);
    expect(html).toContain('Paid');
    expect(html).toContain('Refunded');
    expect(html).toContain('Refund pending');
    expect(html).not.toContain('ABA amount due');
  });

  it('keeps cancellation out of the shortage block and hides replacement for paid orders', () => {
    const html = render(<CustomerOrderShortageBlock order={order({ issues: [{ id: 1, issue_type: 'OUT_OF_STOCK', status: 'OPEN', replacement_name: 'Another tea' }] })} onResolve={() => undefined} />);
    expect(html).not.toContain('<button type="button">Cancel order</button>');
    expect(html).not.toContain('Another tea');
    expect(html).toContain('You can also cancel the order below.');
  });

  it('disables payment proof while an issue is open and shows the reason', () => {
    const html = render(<CustomerOrderPaymentProofAction order={order({ bot_deeplink: 'https://t.me/example', issues: [{ id: 1, issue_type: 'OUT_OF_STOCK', status: 'OPEN' }] })} onOpen={vi.fn()} />);
    expect(html).toContain('disabled=""');
    expect(html).toContain('Please resolve the unavailable item before sending payment proof.');
  });

  it('shows a text-and-icon shortage badge in the order list', () => {
    const html = render(<CustomerOrderIssueBadge count={1} />);
    expect(html).toContain('⚠');
    expect(html).toContain('Shortage needs action');
  });
});
