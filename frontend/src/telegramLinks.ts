type TelegramWebApp = {
  openTelegramLink?: (url: string) => void;
  initDataUnsafe?: { start_param?: string };
};

type TelegramWindow = Window & {
  Telegram?: { WebApp?: TelegramWebApp };
};

/** Open a bot deep link in Telegram when the Mini App bridge is available. */
export function openTelegramLink(url: string) {
  const webApp = (window as TelegramWindow).Telegram?.WebApp;
  if (webApp?.openTelegramLink) {
    try {
      webApp.openTelegramLink(url);
      return;
    } catch {
      // Fall back to the normal browser navigation below.
    }
  }
  window.location.assign(url);
}

/** Read the Mini App deep-link parameter from Telegram or its browser fallback. */
export function getTelegramStartParam(): string | null {
  const webAppParam = (window as TelegramWindow).Telegram?.WebApp?.initDataUnsafe?.start_param;
  if (webAppParam) return webAppParam;
  const read = (value: string) => {
    try { return new URLSearchParams(value.replace(/^#/, '').replace(/^\?/, '')).get('tgWebAppStartParam'); }
    catch { return null; }
  };
  return read(window.location.search) || read(window.location.hash);
}
