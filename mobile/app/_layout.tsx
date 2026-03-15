import AsyncStorage from '@react-native-async-storage/async-storage';
import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native';
import * as Notifications from 'expo-notifications';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import 'react-native-reanimated';

// Must be imported before any navigation so defineTask runs in all JS contexts
import './_background-notification-task';

import { useColorScheme } from '../hooks/use-color-scheme';

const ALARM_STATUS_KEY = 'ALARM_STATUS';

/**
 * Handles a DISMISS_ALARM notification response from any app state.
 *
 * Sets ALARM_STATUS='dismissed' in AsyncStorage — the 1-second polling loop
 * inside triggerAlarm() will detect this and stop the sound + hide the overlay.
 * Also clears pendingAlarm so the overlay never appears on a fresh app open.
 */
async function handleDismissAction(): Promise<void> {
  await AsyncStorage.setItem(ALARM_STATUS_KEY, 'dismissed').catch(() => {});
  await AsyncStorage.removeItem('pendingAlarm').catch(() => {});
  console.log('[RootLayout] DISMISS_ALARM handled — ALARM_STATUS set to dismissed.');
}

export const unstable_settings = {
  anchor: '(tabs)',
};

export default function RootLayout() {
  const colorScheme = useColorScheme();

  useEffect(() => {
    // ── Cold-start: app was launched because user tapped a notification action ──
    Notifications.getLastNotificationResponseAsync().then(async (response) => {
      if (!response) return;
      const data = response.notification.request.content.data as any;
      if (data?.alert_type !== 'alarm') return;

      if (response.actionIdentifier === 'DISMISS_ALARM') {
        await handleDismissAction();
      }
    });

    // ── Root-level response listener ───────────────────────────────────────────
    // Always active regardless of which tab is visible or whether HomeScreen is mounted.
    const sub = Notifications.addNotificationResponseReceivedListener(
      async (response) => {
        const data = response.notification.request.content.data as any;
        if (data?.alert_type !== 'alarm') return;

        console.log('[RootLayout] Response received. actionIdentifier:', response.actionIdentifier);

        if (response.actionIdentifier === 'DISMISS_ALARM') {
          await handleDismissAction();
        }
        // Default action (tapped notification body): HomeScreen's AppState listener
        // will call checkPendingAlarm() when the app foregrounds, which picks up
        // pendingAlarm from AsyncStorage and shows the overlay.
      },
    );

    return () => sub.remove();
  }, []);

  return (
    <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="modal" options={{ presentation: 'modal', title: 'Modal' }} />
      </Stack>
      <StatusBar style="auto" />
    </ThemeProvider>
  );
}
