import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { en } from '../i18n/en';
import { he } from '../i18n/he';

export type Lang = 'en' | 'he';

const LANG_KEY = 'nba_alarm_lang';

const TRANSLATIONS = { en, he } as const;

export type TFunc = (key: string, ...args: (string | number)[]) => string;

function makeT(lang: Lang): TFunc {
  return (key: string, ...args: (string | number)[]): string => {
    let str: string =
      (TRANSLATIONS[lang] as Record<string, string>)[key] ??
      (TRANSLATIONS.en    as Record<string, string>)[key] ??
      key;
    args.forEach((arg, i) => {
      str = str.replace(`{${i}}`, String(arg));
    });
    return str;
  };
}

interface LanguageContextValue {
  lang:           Lang;
  t:              TFunc;
  toggleLanguage: () => void;
  isHebrew:       boolean;
}

const LanguageContext = createContext<LanguageContextValue>({
  lang:           'en',
  t:              makeT('en'),
  toggleLanguage: () => {},
  isHebrew:       false,
});

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLang] = useState<Lang>('en');

  useEffect(() => {
    AsyncStorage.getItem(LANG_KEY).then(val => {
      if (val === 'en' || val === 'he') setLang(val);
    });
  }, []);

  const toggleLanguage = useCallback(() => {
    setLang(prev => {
      const next = prev === 'en' ? 'he' : 'en';
      AsyncStorage.setItem(LANG_KEY, next);
      return next;
    });
  }, []);

  const t = useMemo(() => makeT(lang), [lang]);

  const value = useMemo(
    () => ({ lang, t, toggleLanguage, isHebrew: lang === 'he' }),
    [lang, t, toggleLanguage],
  );

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage(): LanguageContextValue {
  return useContext(LanguageContext);
}
