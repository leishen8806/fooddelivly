// 金额格式化工具。金额一律是「最小货币单位」整数（USD → cents），
// 与后端 orders.total_minor / wallet 里所有 *_minor 字段同单位。
// 这里和 App.tsx 里的同名前缀逻辑保持一致（App.tsx 里的两个 helper 是模块内私有的，
// 新组件复用这份，避免再复制一遍小数位推导）。

export const formatAmount = (minor: number, currency: string, locale = 'en') => {
  try {
    const digits = new Intl.NumberFormat(locale, { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2;
    return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(minor / (10 ** digits));
  } catch {
    return `${minor} ${currency}`;
  }
};

export const toMinorUnits = (value: number, currency: string) => {
  try {
    return Math.round(value * (10 ** (new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2)));
  } catch {
    return Math.round(value * 100);
  }
};

export const toMajorUnits = (minor: number, currency: string) => {
  try {
    const digits = new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2;
    return minor / (10 ** digits);
  } catch {
    return minor / 100;
  }
};

export const formatDateTime = (value: string | null | undefined, locale = 'en') => {
  if (!value) return '—';
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
  } catch {
    return value;
  }
};
