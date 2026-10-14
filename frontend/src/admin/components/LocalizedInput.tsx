import type { ChangeEvent } from 'react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

const LANGUAGES = [{ key: 'en', labelKey: 'admin.localized.language.en' }, { key: 'zh-CN', labelKey: 'admin.localized.language.zh' }, { key: 'km', labelKey: 'admin.localized.language.km' }] as const;
type LocalizedLanguage = typeof LANGUAGES[number]['key'];

export function localizedInputIsValid(value: Record<string, string>, requireEnglish: boolean) {
  return !requireEnglish || Boolean(value.en?.trim());
}

export function LocalizedInput({ value, onChange, label, multiline = false, requireEnglish = false }: {
  value: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
  label: string;
  multiline?: boolean;
  requireEnglish?: boolean;
}) {
  const { t } = useTranslation();
  const [language, setLanguage] = useState<LocalizedLanguage>('en');
  const selected = LANGUAGES.find((item) => item.key === language) || LANGUAGES[0];
  const handleChange = (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange({ ...value, [selected.key]: event.target.value });
  return <fieldset className="localized-input"><legend>{label}</legend><div className="localized-tabs" role="tablist" aria-label={label}>{LANGUAGES.map((item) => <button type="button" role="tab" aria-selected={item.key === language} className={item.key === language ? 'active' : ''} key={item.key} onClick={() => setLanguage(item.key)}><span>{t(item.labelKey)}</span>{!value[item.key]?.trim() && <i className="localized-tab-missing" aria-label={t('admin.localized.missing')} />}</button>)}</div><label><span>{t(selected.labelKey)}</span>{multiline ? <textarea aria-required={requireEnglish && selected.key === 'en'} required={requireEnglish && selected.key === 'en'} value={value[selected.key] || ''} onChange={handleChange} /> : <input aria-required={requireEnglish && selected.key === 'en'} required={requireEnglish && selected.key === 'en'} value={value[selected.key] || ''} onChange={handleChange} />}</label>{requireEnglish && !localizedInputIsValid(value, true) && <small className="localized-input-error">{t('admin.localized.englishRequired')}</small>}</fieldset>;
}
