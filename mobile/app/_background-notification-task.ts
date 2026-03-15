/**
 * Background notification task.
 *
 * Imported at the root _layout.tsx so TaskManager.defineTask runs in every
 * JS context (foreground + headless background) before registerTaskAsync is called.
 *
 * When a remote push arrives while the app is backgrounded OR killed, Android
 * runs this task in a headless JS context. We cannot update React state here,
 * so we:
 *   1. Write the alarm payload to AsyncStorage — HomeScreen picks it up on the
 *      next AppState 'active' event (covers the "app was already running" case).
 *   2. Schedule an immediate local notification — this goes through our patched
 *      ExpoNotificationBuilder which adds a Full Screen Intent, so Android wakes
 *      the screen and brings the app to the foreground automatically.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Notifications from 'expo-notifications';
import * as TaskManager from 'expo-task-manager';

export const BACKGROUND_NOTIFICATION_TASK = 'NBA_ALARM_BACKGROUND_NOTIFICATION';

TaskManager.defineTask(BACKGROUND_NOTIFICATION_TASK, async ({ data, error }: any) => {
  if (error) {
    console.error('[BGTask] Error in background notification task:', error);
    return;
  }

  const notification = data?.notification as Notifications.Notification | undefined;
  if (!notification) return;

  const content   = notification.request.content;
  const notifData = content.data as any;

  if (notifData?.alert_type !== 'alarm') return;

  const title = content.title ?? 'NBA Alarm';
  const body  = content.body  ?? '';

  // ── 1. Persist to AsyncStorage ─────────────────────────────────────────────
  // HomeScreen reads this on AppState → 'active' and shows the overlay.
  try {
    await AsyncStorage.setItem(
      'pendingAlarm',
      JSON.stringify({ title, body, ts: Date.now() }),
    );
    console.log('[BGTask] Alarm stored for foreground pickup:', title);
  } catch (e) {
    console.error('[BGTask] AsyncStorage write failed:', e);
  }

  // ── 2. Fire an immediate local notification with Full Screen Intent ────────
  // Our withFullScreenIntent config plugin patches ExpoNotificationBuilder to
  // call builder.setFullScreenIntent() on every notification, so this local
  // notification will wake the screen and launch the app to the foreground
  // exactly like an alarm clock, even when the app is completely killed.
  //
  // We use trigger: null to fire instantly (no delay).
  try {
    await Notifications.scheduleNotificationAsync({
      content: {
        title,
        body,
        sound:    'classic_alarm.wav',
        priority: Notifications.AndroidNotificationPriority.MAX,
        data:     { alert_type: 'alarm', fromBackground: true },
        sticky:   false,
      },
      trigger: {
        type:      Notifications.SchedulableTriggerInputTypes.TIME_INTERVAL,
        seconds:   1,        // minimum allowed; fires almost immediately
        channelId: 'classic_alarm',
      },
    });
    console.log('[BGTask] FSI local notification scheduled.');
  } catch (e) {
    console.error('[BGTask] Could not schedule local notification:', e);
  }
});
