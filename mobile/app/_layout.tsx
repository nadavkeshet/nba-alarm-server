import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native';
import notifee, { EventType } from '@notifee/react-native';
import messaging from '@react-native-firebase/messaging';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Updates from 'expo-updates';
import * as SplashScreen from 'expo-splash-screen';
import { useFonts } from 'expo-font';
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
  Inter_800ExtraBold,
} from '@expo-google-fonts/inter';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, I18nManager, NativeModules, Platform, StyleSheet, View } from 'react-native';
import { SERVER_URL } from '../config';
import ConfettiCannon from 'react-native-confetti-cannon';
import 'react-native-reanimated';
import { triggerStop } from '../alarmSoundHandler';
import { useColorScheme } from '../hooks/use-color-scheme';
import { PermissionsOnboarding } from '../components/permissions-onboarding';
import { ErrorBoundary } from '../components/error-boundary';
import { initTelemetry, signal, signalSessionEnd } from '../utils/telemetry';

SplashScreen.preventAutoHideAsync().catch(() => {});

// Force LTR layout regardless of device locale (app content is always LTR)
I18nManager.allowRTL(false);
I18nManager.forceRTL(false);

// Warm up the server the instant the JS bundle evaluates — overlaps with React
// tree initialization so the server is ready before any actual data requests fire.
void fetch(`${SERVER_URL}/health`).catch(() => {});

export const unstable_settings = {
  anchor: '(tabs)',
};

const ONBOARDING_KEY = 'nba_onboarding_done';

export default function RootLayout() {
  const colorScheme     = useColorScheme();
  const [confetti, setConfetti] = useState(false);
  const confettiTimer   = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(false);

  const [fontsLoaded] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
    Inter_800ExtraBold,
  });

  // useCallback so this can be referenced by effects that must come before the
  // conditional `return null` (Rules of Hooks: no hooks after early return).
  const fireConfetti = useCallback(() => {
    if (confettiTimer.current) clearTimeout(confettiTimer.current);
    setConfetti(true);
    confettiTimer.current = setTimeout(() => setConfetti(false), 3500);
  }, []);

  // Reveal UI once fonts are ready
  useEffect(() => {
    if (fontsLoaded) SplashScreen.hideAsync().catch(() => {});
  }, [fontsLoaded]);

  // Init TelemetryDeck + fire App.Launched on cold start
  useEffect(() => {
    messaging().getToken().then(token => {
      initTelemetry(token);
      signal('App.Launched');
    }).catch(() => {
      initTelemetry('anonymous');
      signal('App.Launched');
    });
  }, []);

  // Pre-fetch OTA updates on cold start so they apply on next launch
  useEffect(() => {
    if (__DEV__) return;
    (async () => {
      try {
        const update = await Updates.checkForUpdateAsync();
        if (update.isAvailable) {
          await Updates.fetchUpdateAsync();
          // Intentionally not calling reloadAsync() — applying immediately
          // would leave native modules (AlarmChannel) in an invalid state.
          // The update will be applied on the next cold start automatically.
        }
      } catch {}
    })();
  }, []);

  // Show onboarding once on first launch (Android only)
  useEffect(() => {
    if (Platform.OS !== 'android') return;
    AsyncStorage.getItem(ONBOARDING_KEY).then(val => {
      if (!val) {
        setShowOnboarding(true);
        signal('Onboarding.Shown');
      }
    });
  }, []);

  useEffect(() => {
    // FCM token rotation — fires when the OS issues a new push token.
    const unsubTokenRefresh = messaging().onTokenRefresh(async (newToken) => {
      try {
        const oldToken = await AsyncStorage.getItem('nba_fcm_token') ?? '';
        await fetch(`${SERVER_URL}/register-token`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            push_token:    newToken,
            sound_channel: 'classic_alarm',
            old_token:     oldToken,
          }),
        });
        await AsyncStorage.setItem('nba_fcm_token', newToken);
      } catch {}
    });

    // Killed state: app launched by tapping a notification.
    notifee.getInitialNotification().then(initial => {
      if (initial?.notification?.data?.alert_type === 'alarm') {
        signal('Notification.Tapped');
        NativeModules.AlarmChannel?.stopAlarmSound();
        notifee.cancelAllNotifications().catch(() => {});
        fireConfetti();
      }
    });

    // Foreground + background state notification events (single handler — Notifee allows only one)
    const unsub = notifee.onForegroundEvent(async ({ type, detail }) => {
      if (type === EventType.PRESS && detail.notification?.data?.alert_type === 'alarm') {
        signal('Notification.Tapped');
        NativeModules.AlarmChannel?.stopAlarmSound();
        triggerStop();
        await notifee.cancelAllNotifications();
        fireConfetti();
        return;
      }
      if (type === EventType.DELIVERED && detail.notification?.data?.alert_type === 'alarm') {
        const savedSound = (await AsyncStorage.getItem('nba_alarm_sound').catch(() => null)) ?? 'classic_alarm';
        NativeModules.AlarmChannel?.startAlarmSound(savedSound);
        return;
      }
      if (type === EventType.ACTION_PRESS && detail.pressAction?.id === 'dismiss') {
        NativeModules.AlarmChannel?.stopAlarmSound();
        triggerStop();
        await notifee.cancelAllNotifications();
      }
    });

    // Background tap: onBackgroundEvent sets a flag → fire confetti when app opens
    // Also fires App.SessionEnded with duration on background/close.
    const appStateSub = AppState.addEventListener('change', async state => {
      if (state === 'active') {
        const pending = await AsyncStorage.getItem('nba_confetti_pending');
        if (pending) {
          await AsyncStorage.removeItem('nba_confetti_pending');
          fireConfetti();
        }
      } else if (state === 'background') {
        signalSessionEnd();
      }
    });

    return () => {
      unsubTokenRefresh();
      unsub();
      appStateSub.remove();
      if (confettiTimer.current) clearTimeout(confettiTimer.current);
    };
  }, [fireConfetti]);

  // All hooks above — safe to return early now
  if (!fontsLoaded) return null;

  const handleOnboardingDone = async () => {
    signal('Onboarding.Finished');
    await AsyncStorage.setItem(ONBOARDING_KEY, '1');
    setShowOnboarding(false);
  };

  return (
    <ErrorBoundary>
    <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
      <PermissionsOnboarding visible={showOnboarding} onDone={handleOnboardingDone} />
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="modal" options={{ presentation: 'modal', title: 'Modal' }} />
      </Stack>
      <StatusBar style="auto" />

      {/* Confetti overlay — rendered above all tabs, pointer-events none so it doesn't block UI */}
      {confetti && (
        <View style={StyleSheet.absoluteFill} pointerEvents="none">
          <ConfettiCannon
            count={90}
            origin={{ x: -10, y: 0 }}
            autoStart
            fadeOut
            fallSpeed={3200}
            explosionSpeed={280}
          />
        </View>
      )}
    </ThemeProvider>
    </ErrorBoundary>
  );
}
