import React, { useEffect, useState } from 'react';
import WebApp from '@twa-dev/sdk';
import axios from 'axios';
import { useAuthStore } from '../store/authStore';
import { useTranslation } from 'react-i18next';

export const TelegramProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { setCustomerAuth } = useAuthStore();
  const { i18n, t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    WebApp.ready();
    
    // Set language based on Telegram user if available
    const tgUser = WebApp.initDataUnsafe?.user;
    if (tgUser && tgUser.language_code) {
      const code = tgUser.language_code;
      if (code.startsWith('zh')) {
        i18n.changeLanguage('zh-CN');
      } else if (code === 'km') {
        i18n.changeLanguage('km');
      } else {
        i18n.changeLanguage('en');
      }
    }

    const authenticate = async () => {
      try {
        const initData = WebApp.initData;
        
        if (!initData) {
          // If no initData, we might be in a regular browser (could be Admin)
          // We won't block rendering, but customer routes will require auth
          setLoading(false);
          return;
        }

        const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
        const res = await axios.post(`${API_URL}/api/v1/auth/telegram`, { initData }, {
          withCredentials: true // send cookies
        });

        if (res.data.customer_id) {
          setCustomerAuth(res.data.customer_id);
        }
      } catch (err) {
        console.error('Telegram auth failed', err);
        setError(t('auth.sessionExpired'));
      } finally {
        setLoading(false);
      }
    };

    authenticate();
  }, [setCustomerAuth, i18n, t]);

  if (error) {
    return <div style={{ padding: 20, textAlign: 'center', color: 'red' }}>{error}</div>;
  }

  if (loading) {
    return <div style={{ padding: 20, textAlign: 'center' }}>{t('common.loading')}</div>;
  }

  return <>{children}</>;
};
