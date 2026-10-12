type TelegramWebApp = {
  openTelegramLink?: (url: string) => void;
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
