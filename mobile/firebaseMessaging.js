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
    // Save to Trophy Room
    try {
      const hit = { title: data.title ?? 'NBA Alarm', body: data.body ?? '', receivedAt: new Date().toISOString() };
      const raw  = await AsyncStorage.getItem(TROPHY_KEY);
      const prev = raw ? JSON.parse(raw) : [];
      await AsyncStorage.setItem(TROPHY_KEY, JSON.stringify([hit, ...prev].slice(0, 10)));
    } catch {}

    await notifee.displayNotification({
      title: data.title ?? 'NBA Alarm',
      body:  data.body  ?? '',
      data:  { alert_type: 'alarm' },
      android: {
        channelId:   data.channel_id ?? 'classic_alarm',
        sound:       'classic_alarm',
        importance:  AndroidImportance.HIGH,
        category:    AndroidCategory.ALARM,
        visibility:  AndroidVisibility.PUBLIC,
        pressAction: { id: 'default' },
        actions:     [{ title: 'DISMISS', pressAction: { id: 'dismiss' } }],
      },
    });
  } else {
    // Silent mode — regular notification, no sound, no DISMISS button
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
  }
});

const CONFETTI_KEY = 'nba_confetti_pending';

// ── Background Notifee event handler ─────────────────────────────────────────
// Handles notification events when the app is in background or killed.
notifee.onBackgroundEvent(async ({ type, detail }) => {
  // Notification delivered while app is in background → start alarm sound
  if (type === EventType.DELIVERED && detail.notification?.data?.alert_type === 'alarm') {
    NativeModules.AlarmChannel?.startAlarmSound();
    return;
  }
  // User tapped notification body → stop sound, set confetti flag, cancel notification
  if (type === EventType.PRESS && detail.notification?.data?.alert_type === 'alarm') {
    NativeModules.AlarmChannel?.stopAlarmSound();
    await AsyncStorage.setItem(CONFETTI_KEY, '1');
    await notifee.cancelAllNotifications();
    return;
  }
  // User tapped DISMISS → stop sound + cancel notification
  if (type === EventType.ACTION_PRESS && detail.pressAction?.id === 'dismiss') {
    NativeModules.AlarmChannel?.stopAlarmSound();
    await notifee.cancelAllNotifications();
  }
});
