import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { useColorScheme } from 'react-native';

export type ThemeMode = 'dark' | 'light';

const THEME_KEY      = 'nba_alarm_theme';
const NO_SPOILER_KEY = 'nba_no_spoiler';

export const darkColors = {
  bg:          '#0B1623',
  card:        '#0d1f35',
  cardAlt:     '#111d2e',
  border:      '#1a3451',
  borderMuted: '#1e2a3d',
  blue:        '#1d428a',
  red:         '#c9082a',
  textPrimary: '#e6edf3',
  textMuted:   '#6b8aad',
  liveGreen:   '#3fb950',
  accentBlue:  '#4d8cf5',
  gold:        '#f0a500',
  orange:      '#FF6B00',
  orangeDim:   'rgba(255,107,0,0.15)',
};

export const lightColors = {
  bg:          '#F5F6F9',
  card:        '#FFFFFF',
  cardAlt:     '#F2F3F5',
  border:      '#E0E3E8',
  borderMuted: '#E5E7EA',
  blue:        '#1d428a',
  red:         '#C9082A',
  textPrimary: '#1C1F26',
  textMuted:   '#6B7687',
  liveGreen:   '#3FB950',
  accentBlue:  '#4D8CF5',
  gold:        '#F0A500',
  orange:      '#FF6B00',
  orangeDim:   'rgba(255,107,0,0.15)',
};

export type AppColors = typeof darkColors;

interface ThemeContextValue {
  mode:            ThemeMode;
  colors:          AppColors;
  toggleTheme:     () => void;
  noSpoiler:       boolean;
  toggleNoSpoiler: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  mode:            'dark',
  colors:          darkColors,
  toggleTheme:     () => {},
  noSpoiler:       false,
  toggleNoSpoiler: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const systemScheme = useColorScheme();
  const [mode, setMode]           = useState<ThemeMode>(systemScheme === 'light' ? 'light' : 'dark');
  const [noSpoiler, setNoSpoiler] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(THEME_KEY).then(val => {
      if (val === 'light' || val === 'dark') setMode(val);
    });
    AsyncStorage.getItem(NO_SPOILER_KEY).then(val => {
      setNoSpoiler(val === '1');
    });
  }, []);

  const toggleTheme = useCallback(() => {
    setMode(prev => {
      const next = prev === 'dark' ? 'light' : 'dark';
      AsyncStorage.setItem(THEME_KEY, next);
      return next;
    });
  }, []);

  const toggleNoSpoiler = useCallback(() => {
    setNoSpoiler(prev => {
      const next = !prev;
      AsyncStorage.setItem(NO_SPOILER_KEY, next ? '1' : '0');
      return next;
    });
  }, []);

  return (
    <ThemeContext.Provider value={{ mode, colors: mode === 'dark' ? darkColors : lightColors, toggleTheme, noSpoiler, toggleNoSpoiler }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
