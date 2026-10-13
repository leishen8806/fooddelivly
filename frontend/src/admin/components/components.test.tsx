import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { DataTable } from './DataTable';
import { LocalizedInput, localizedInputIsValid } from './LocalizedInput';
import { formatMinor, parseMoneyToMinor, sanitizeMoneyInput } from './MoneyInput';

vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined });
vi.stubGlobal('document', { documentElement: { lang: '' } });
const { default: i18n } = await import('../../i18n');

describe('MoneyInput editing helpers', () => {
  it('keeps continuous typing as integer minor units', () => {
    expect(parseMoneyToMinor(sanitizeMoneyInput('52', 'USD'), 'USD')).toBe(5200);
    expect(parseMoneyToMinor(sanitizeMoneyInput('5.5', 'USD'), 'USD')).toBe(550);
    expect(parseMoneyToMinor(sanitizeMoneyInput('0.05', 'USD'), 'USD')).toBe(5);
    expect(formatMinor(5, 'USD')).toBe('0.05');
  });

  it('removes decimals for KHR and truncates pasted input', () => {
    expect(sanitizeMoneyInput('12.345', 'KHR')).toBe('12');
    expect(parseMoneyToMinor('12.345', 'KHR')).toBe(12);
    expect(sanitizeMoneyInput('abc12.345', 'USD')).toBe('12.34');
    expect(parseMoneyToMinor('abc12.345', 'USD')).toBe(1234);
  });

  it('represents an empty input as null', () => {
    expect(sanitizeMoneyInput('', 'USD')).toBe('');
    expect(parseMoneyToMinor('', 'USD')).toBeNull();
    expect(formatMinor(null, 'USD')).toBe('');
  });
});

describe('LocalizedInput', () => {
  it('marks missing language tabs and enforces English when requested', () => {
    const html = renderToStaticMarkup(<I18nextProvider i18n={i18n}><LocalizedInput label="Name" value={{ en: '', 'zh-CN': '中文', km: '' }} onChange={() => undefined} requireEnglish /></I18nextProvider>);
    expect((html.match(/localized-tab-missing/g) || []).length).toBe(2);
    expect(html).toContain('aria-required="true"');
    expect(localizedInputIsValid({ en: '', 'zh-CN': '中文', km: '' }, true)).toBe(false);
    expect(localizedInputIsValid({ en: 'Name', 'zh-CN': '', km: '' }, true)).toBe(true);
  });
});

describe('DataTable states', () => {
  const columns = [{ key: 'name', header: 'Name', render: (row: { id: number; name: string }) => row.name }];

  it('renders loading, error, and empty states separately', () => {
    const loading = renderToStaticMarkup(<DataTable columns={columns} rows={[]} loading empty="Nothing" />);
    const error = renderToStaticMarkup(<DataTable columns={columns} rows={[]} error="Failed" empty="Nothing" />);
    const empty = renderToStaticMarkup(<DataTable columns={columns} rows={[]} empty="Nothing" />);
    expect(loading).toContain('…');
    expect(error).toContain('role="alert"');
    expect(error).toContain('Failed');
    expect(empty).toContain('Nothing');
  });
});

