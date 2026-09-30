import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import locales from '../../docs/ui/locales.json';

// Initialize i18next
i18n
  .use(initReactI18next)
  .init({
    resources: {
      'zh-CN': { translation: locales['zh-CN'] },
      en: { translation: locales['en'] },
      km: { translation: locales['km'] }
    },
    lng: localStorage.getItem('teacafe.language') || 'en',
    fallbackLng: 'en',
    keySeparator: false,
    interpolation: {
      escapeValue: false // react already safes from xss
    }
  });

export default i18n;
