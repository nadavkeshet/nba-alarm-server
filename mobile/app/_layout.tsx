import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native';
import notifee, { EventType } from '@notifee/react-native';
import messaging from '@react-native-firebase/messaging';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Updates from 'expo-updates';
import React, { useEffect, useRef, useState } from 'react';
import { AppState, ErrorUtils, I18nManager, NativeModules, Platform, StyleSheet, View } from 'react-native';

import { SERVER_URL } from '../config';
import ConfettiCannon from 'react-native-confetti-cannon';
import 'react-native-reanimated';

import { triggerStop } from '../alarmSoundHandler';
import { useColorScheme } from '../hooks/use-color-scheme';
import { PermissionsOnboarding } from '../components/permissions-onboarding';
import { ErrorBoundary, reportGlobalError } from '../components/error-boundary';

// Force LTR layout regardless of device locale (app content is always LTR)
I18nManager.allowRTL(false);
I18nManager.forceRTL(false);

export const unstable_settings = {
  anchor: '(tabs)',
};

const ONBOARDING_KEY = 'nba_onboarding_done';

export default function RootLayout() {
  const colorScheme     = useColorScheme();
  const [confetti, setConfetti] = useState(false);
  const confettiTimer   = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(false);

  const fireConfetti = () => {
    if (confettiTimer.current) clearTimeout(confettiTimer.current);
    setConfetti(true);
    confettiTimer.current = setTimeout(() => setConfetti(false), 3500);
  };

  // Global JS error handler — routes unhandled errors to the ErrorBoundary debug screen
  useEffect(() => {
    const prev = ErrorUtils.getGlobalHandler();
    ErrorUtils.setGlobalHandler((error, isFatal) => {
      reportGlobalError(error);
      prev?.(error, isFatal);
    });
    return () => { ErrorUtils.setGlobalHandler(prev); };
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
      if (!val) setShowOnboarding(true);
    });
  }, []);

  const handleOnboardingDone = async () => {
    await AsyncStorage.setItem(ONBOARDING_KEY, '1');
    setShowOnboarding(false);
  };

  useEffect(() => {
    // FCM token rotation — fires when the OS issues a new push token.
    // Read the old token from AsyncStorage so the backend can migrate all
    // user data (favorites, preferences, alarms) to the new token before
    // the old one is invalidated.
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
    // This is the ONLY event fired for a killed-state notification tap —
    // neither onForegroundEvent PRESS nor onBackgroundEvent PRESS fires here.
    notifee.getInitialNotification().then(initial => {
      if (initial?.notification?.data?.alert_type === 'alarm') {
        NativeModules.AlarmChannel?.stopAlarmSound();
        notifee.cancelAllNotifications().catch(() => {});
        fireConfetti();
      }
    });

    // Foreground + background state notification events (single handler — Notifee allows only one)
    const unsub = notifee.onForegroundEvent(async ({ type, detail }) => {
      // User tapped the notification body → celebrate + stop alarm.
      // Call stopAlarmSound directly (NativeModules always available in foreground)
      // in addition to triggerStop() to cover the race where index.tsx hasn't
      // registered its stop handler yet (e.g. app just opened via notification tap).
      if (type === EventType.PRESS && detail.notification?.data?.alert_type === 'alarm') {
        NativeModules.AlarmChannel?.stopAlarmSound();
        triggerStop();
        await notifee.cancelAllNotifications();
        fireConfetti();
        return;
      }
      // Trigger notification delivered while app is in foreground → start alarm sound
      if (type === EventType.DELIVERED && detail.notification?.data?.alert_type === 'alarm') {
        const savedSound = (await AsyncStorage.getItem('nba_alarm_sound').catch(() => null)) ?? 'classic_alarm';
        NativeModules.AlarmChannel?.startAlarmSound(savedSound);
        return;
      }
      // User tapped DISMISS → stop sound + cancel notification
      if (type === EventType.ACTION_PRESS && detail.pressAction?.id === 'dismiss') {
        NativeModules.AlarmChannel?.stopAlarmSound();
        triggerStop();
        await notifee.cancelAllNotifications();
      }
    });

    // Background tap: onBackgroundEvent sets a flag → fire confetti when app opens
    const appStateSub = AppState.addEventListener('change', async state => {
      if (state === 'active') {
        const pending = await AsyncStorage.getItem('nba_confetti_pending');
        if (pending) {
          await AsyncStorage.removeItem('nba_confetti_pending');
          fireConfetti();
        }
      }
    });

    return () => {
      unsubTokenRefresh();
      unsub();
      appStateSub.remove();
      if (confettiTimer.current) clearTimeout(confettiTimer.current);
    };
  }, []);

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
