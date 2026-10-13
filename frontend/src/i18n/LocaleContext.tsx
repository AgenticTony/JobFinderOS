'use client';

// Console-wide locale. Defaults to 'en' (the static export prerenders
// English); the console resolves the real locale after mount via
// consoleLocale() and can switch it live with setLocale (persisted).

import { createContext, useContext } from 'react';
import { dicts, type Locale } from './dict';

export const LocaleContext = createContext<{
  locale: Locale;
  setLocale: (l: Locale) => void;
}>({ locale: 'en', setLocale: () => {} });

export function useLocale() {
  return useContext(LocaleContext);
}

export function useT() {
  return dicts[useContext(LocaleContext).locale];
}
