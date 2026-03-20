// Must be the very first import — registers FCM background handler + Notifee
// background event handler before any React component mounts.
import '../firebaseMessaging';

import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native';
import notifee, { EventType } from '@notifee/react-native';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import React, { useEffect, useRef, useState } from 'react';
import { I18nManager, NativeModules, StyleSheet, View } from 'react-native';
import ConfettiCannon from 'react-native-confetti-cannon';
import 'react-native-reanimated';

import { triggerStop } from '../alarmSoundHandler';
import { useColorScheme } from '../hooks/use-color-scheme';

// Force LTR layout regardless of device locale (app content is always LTR)
I18nManager.allowRTL(false);
I18nManager.forceRTL(false);

export const unstable_settings = {
  anchor: '(tabs)',
};

export default function RootLayout() {
  const colorScheme     = useColorScheme();
  const [confetti, setConfetti] = useState(false);
  const confettiTimer   = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fireConfetti = () => {
    if (confettiTimer.current) clearTimeout(confettiTimer.current);
    setConfetti(true);
    confettiTimer.current = setTimeout(() => setConfetti(false), 3500);
  };

  useEffect(() => {
    // Killed state: app launched by tapping a notification
    notifee.getInitialNotification().then(initial => {
      if (initial?.notification?.data?.alert_type === 'alarm') {
        fireConfetti();
      }
    });

    // Foreground + background state notification events (single handler — Notifee allows only one)
    const unsub = notifee.onForegroundEvent(async ({ type, detail }) => {
      // User tapped the notification body → celebrate
      if (type === EventType.PRESS && detail.notification?.data?.alert_type === 'alarm') {
        fireConfetti();
        return;
      }
      // Trigger notification delivered while app is in foreground → start alarm sound
      if (type === EventType.DELIVERED && detail.notification?.data?.alert_type === 'alarm') {
        NativeModules.AlarmChannel?.startAlarmSound();
        return;
      }
      // User tapped DISMISS → stop sound + cancel notification
      if (type === EventType.ACTION_PRESS && detail.pressAction?.id === 'dismiss') {
        triggerStop();
        await notifee.cancelAllNotifications();
      }
    });

    return () => {
      unsub();
      if (confettiTimer.current) clearTimeout(confettiTimer.current);
    };
  }, []);

  return (
    <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="modal" options={{ presentation: 'modal', title: 'Modal' }} />
      </Stack>
      <StatusBar style="auto" />

      {/* Confetti overlay — rendered above all tabs, pointer-events none so it doesn't block UI */}
      {confetti && (
        <View style={StyleSheet.absoluteFill} pointerEvents="none">
          <ConfettiCannon
            count={200}
            origin={{ x: -10, y: 0 }}
            autoStart
            fadeOut
            fallSpeed={2500}
            explosionSpeed={350}
          />
        </View>
      )}
    </ThemeProvider>
  );
}
