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

  if (data.alert_type !== 'alarm') {
    // Silent push (radar/favorites in silent mode) — no visible notification
    return;
  }

  // Save to Trophy Room (background/killed state — foreground is handled in active.tsx)
  try {
    const hit = { title: data.title ?? 'NBA Alarm', body: data.body ?? '', receivedAt: new Date().toISOString() };
    const raw  = await AsyncStorage.getItem(TROPHY_KEY);
    const prev = raw ? JSON.parse(raw) : [];
    await AsyncStorage.setItem(TROPHY_KEY, JSON.stringify([hit, ...prev].slice(0, 10)));
  } catch {}

  await notifee.displayNotification({
    title: data.title ?? 'NBA Alarm',
    body:  data.body  ?? '',
    data:  { alert_type: 'alarm' },   // required for checkAlarmOnForeground in index.tsx
    android: {
      channelId:        data.channel_id ?? 'classic_alarm',
      sound:            'classic_alarm',
      importance:       AndroidImportance.HIGH,
      category:         AndroidCategory.ALARM,
      visibility:       AndroidVisibility.PUBLIC,
      // DISMISS button — inline, no SharedPreferences lookup required
      actions: [{ title: 'DISMISS', pressAction: { id: 'dismiss' } }],
      // Full Screen Intent — wakes screen on notification arrival.
      // withTurnScreenOn plugin ensures turnScreenOn + showWhenLocked on MainActivity.
      fullScreenAction: { id: 'default' },
    },
  });
});

// ── Background Notifee event handler ─────────────────────────────────────────
// Handles action button taps (DISMISS) when the app is in background or killed.
notifee.onBackgroundEvent(async ({ type, detail }) => {
  if (type === EventType.ACTION_PRESS && detail.pressAction?.id === 'dismiss') {
    NativeModules.AlarmChannel?.stopAlarmSound();
    await notifee.cancelAllNotifications();
  }
});
