/**
 * firebaseMessaging.js
 *
 * Must be imported ONCE at the very top of app/_layout.tsx (before any React
 * code runs) so that the background/killed-state handlers are registered as
 * early as possible in the JS bundle.
 *
 * Flow:
 *   FCM data-only message arrives
 *     ├─ App FOREGROUND  → messaging().onMessage() in index.tsx handles it
 *     ├─ App BACKGROUND  → setBackgroundMessageHandler below → Notifee displays it
 *     └─ App KILLED      → setBackgroundMessageHandler below → Notifee displays it (headless JS)
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import messaging from '@react-native-firebase/messaging';
import notifee, { AndroidImportance, AndroidCategory, AndroidVisibility, EventType } from '@notifee/react-native';
import { NativeModules } from 'react-native';

const TROPHY_KEY = 'nba_alarm_recent_hits';

// ── Background / killed-state FCM handler ────────────────────────────────────
// Called by Firebase when a data-only FCM message arrives and the app is NOT
// in the foreground.  We display the notification ourselves via Notifee so we
// have full control (actions, channel, FSI, sound).
messaging().setBackgroundMessageHandler(async remoteMessage => {
  const data = remoteMessage.data ?? {};

  if (data.alert_type === 'alarm') {
    // Ensure the alarm channel exists — defensive guard for edge cases where
    // the channel was not yet created (e.g., very first install, race condition).
    // createChannel is idempotent: Android ignores updates to existing channels.
    try {
      await notifee.createChannel({
        id:         'classic_alarm',
        name:       'Classic Alarm',
        sound:      'classic_alarm',
        importance: AndroidImportance.HIGH,
        bypassDnd:  true,
        visibility: AndroidVisibility.PUBLIC,
        vibration:  true,
      });
    } catch {}

    // Display notification FIRST — Android gives Headless JS a narrow time
    // window in killed state. Nothing must block this call.
    try {
      await notifee.displayNotification({
        title: data.title ?? 'NBA Alarm',
        body:  data.body  ?? '',
        data:  { alert_type: 'alarm' },
        android: {
          channelId:        data.channel_id ?? 'classic_alarm',
          sound:            'classic_alarm',
          importance:       AndroidImportance.HIGH,
          category:         AndroidCategory.ALARM,
          visibility:       AndroidVisibility.PUBLIC,
          pressAction:      { id: 'default' },
          // fullScreenAction wakes the screen and shows over the lock screen
          // when the phone is locked — critical for the night-time use case.
          // NOTE: withFullScreenIntent.js patches ExpoNotificationBuilder.kt
          // (expo-notifications) which is NOT used here. Notifee needs this
          // fullScreenAction field to call setFullScreenIntent() on its own builder.
          fullScreenAction: { id: 'default' },
          actions:          [{ title: 'DISMISS', pressAction: { id: 'dismiss' } }],
        },
      });
    } catch (e) {
      // Log but don't rethrow — a crash here silently swallows the notification.
      console.error('[FCM background] displayNotification failed:', e);
    }

    // Save to Trophy Room after notification is already displayed
    try {
      const hit = { title: data.title ?? 'NBA Alarm', body: data.body ?? '', receivedAt: new Date().toISOString() };
      const raw  = await AsyncStorage.getItem(TROPHY_KEY);
      const prev = raw ? JSON.parse(raw) : [];
      await AsyncStorage.setItem(TROPHY_KEY, JSON.stringify([hit, ...prev].slice(0, 10)));
    } catch {}
  } else {
    // Silent mode — regular notification, no sound, no DISMISS button
    try {
      await notifee.createChannel({ id: 'silent', name: 'Silent Updates', importance: AndroidImportance.DEFAULT });
      await notifee.displayNotification({
        title: data.title ?? 'NBA Update',
        body:  data.body  ?? '',
        data:  { alert_type: 'silent' },
        android: {
          channelId:   'silent',
          importance:  AndroidImportance.DEFAULT,
          pressAction: { id: 'default' },
        },
      });
    } catch (e) {
      console.error('[FCM background] silent displayNotification failed:', e);
    }
  }
});

const CONFETTI_KEY = 'nba_confetti_pending';

// ── Background Notifee event handler ─────────────────────────────────────────
// Handles notification events when the app is in background or killed.
notifee.onBackgroundEvent(async ({ type, detail }) => {
  // Notification delivered while app is in background/killed → start alarm sound.
  // NativeModules may not be fully available in killed state — always guard with try-catch.
  if (type === EventType.DELIVERED && detail.notification?.data?.alert_type === 'alarm') {
    try { NativeModules.AlarmChannel?.startAlarmSound(); } catch {}
    return;
  }
  // User tapped notification body → stop sound, set confetti flag, cancel notification
  if (type === EventType.PRESS && detail.notification?.data?.alert_type === 'alarm') {
    try { NativeModules.AlarmChannel?.stopAlarmSound(); } catch {}
    try { await AsyncStorage.setItem(CONFETTI_KEY, '1'); } catch {}
    try { await notifee.cancelAllNotifications(); } catch {}
    return;
  }
  // User tapped DISMISS → stop sound + cancel notification
  if (type === EventType.ACTION_PRESS && detail.pressAction?.id === 'dismiss') {
    try { NativeModules.AlarmChannel?.stopAlarmSound(); } catch {}
    try { await notifee.cancelAllNotifications(); } catch {}
  }
});
