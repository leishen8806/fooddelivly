import { useEffect, useId, useRef, useState } from 'react';

const ZERO_DECIMAL_CURRENCIES = new Set(['KHR', 'JPY', 'VND']);

export function currencyFractionDigits(currency: string) {
  return ZERO_DECIMAL_CURRENCIES.has(currency.toUpperCase()) ? 0 : 2;
}

/** Keep the edit buffer textual. Decimal digits are truncated, never rounded. */
export function sanitizeMoneyInput(input: string, currency: string) {
  const digits = currencyFractionDigits(currency);
  const filtered = input.replace(/[^0-9.]/g, '');
  if (digits === 0) return filtered.split('.')[0] || (filtered.includes('.') ? '' : filtered);
  const firstDot = filtered.indexOf('.');
  if (firstDot < 0) return filtered;
  const whole = filtered.slice(0, firstDot);
  const fraction = filtered.slice(firstDot + 1).replace(/\./g, '').slice(0, digits);
  return `${whole}.${fraction}`;
}

export function parseMoneyToMinor(input: string, currency: string): number | null {
  const sanitized = sanitizeMoneyInput(input, currency);
  if (!sanitized || sanitized === '.') return null;
  const digits = currencyFractionDigits(currency);
  const [whole = '0', fraction = ''] = sanitized.split('.');
  const scale = 10 ** digits;
  return (Number(whole || 0) * scale) + Number(fraction.padEnd(digits, '0') || 0);
}

export function formatMinor(valueMinor: number | null, currency: string) {
  if (valueMinor === null || !Number.isFinite(valueMinor)) return '';
  const digits = currencyFractionDigits(currency);
  const sign = valueMinor < 0 ? '-' : '';
  const integer = String(Math.abs(Math.trunc(valueMinor))).padStart(digits + 1, '0');
  if (digits === 0) return `${sign}${integer}`;
  return `${sign}${integer.slice(0, -digits)}.${integer.slice(-digits)}`;
}

export function MoneyInput({ valueMinor, currency, onChange, label, required = false }: {
  valueMinor: number | null;
  currency: string;
  onChange: (valueMinor: number | null) => void;
  label: string;
  required?: boolean;
}) {
  const id = useId();
  const [draft, setDraft] = useState(() => formatMinor(valueMinor, currency));
  const focused = useRef(false);

  useEffect(() => {
    if (!focused.current) setDraft(formatMinor(valueMinor, currency));
  }, [currency, valueMinor]);

  const updateDraft = (next: string) => {
    const sanitized = sanitizeMoneyInput(next, currency);
    setDraft(sanitized);
    onChange(parseMoneyToMinor(sanitized, currency));
  };

  return <label htmlFor={id} className="money-input"><span>{label}</span><div><input id={id} inputMode="decimal" value={draft} required={required} onFocus={() => { focused.current = true; }} onChange={(event) => updateDraft(event.target.value)} onBlur={() => { focused.current = false; const parsed = parseMoneyToMinor(draft, currency); setDraft(formatMinor(parsed, currency)); onChange(parsed); }} /><span className="money-input-currency">{currency}</span></div></label>;
}
