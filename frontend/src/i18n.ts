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

const syncDocumentLanguage = (language: string) => {
  document.documentElement.lang = language;
};

syncDocumentLanguage(i18n.resolvedLanguage || i18n.language || 'en');
i18n.on('languageChanged', syncDocumentLanguage);

export default i18n;
