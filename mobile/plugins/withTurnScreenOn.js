/**
 * Expo Config Plugin – Turn Screen On & Show When Locked
 *
 * Adds two Activity-level attributes to MainActivity in AndroidManifest.xml:
 *
 *   android:turnScreenOn="true"
 *     → Wakes the display when this Activity is brought to the foreground.
 *       Takes effect when the user taps the notification while screen is off.
 *
 *   android:showWhenLocked="true"
 *     → Allows the Activity to appear over the lock screen without requiring
 *       the user to unlock first.
 *
 * Both attributes require API 27+ (Android 8.1) — on older devices they are
 * silently ignored. They are the manifest-level equivalent of calling
 *   activity.setTurnScreenOn(true)
 *   activity.setShowWhenLocked(true)
 * programmatically, which is what Expo's own APIs cannot expose via JS.
 *
 * Note: these attributes wake the screen when the Activity is *launched*
 * (i.e. when the user taps the notification). For auto-wake on notification
 * *arrival* (screen stays off, no tap needed) a Full Screen Intent is
 * required instead (see withFullScreenIntent.js).
 */

const { withAndroidManifest } = require('@expo/config-plugins');

module.exports = function withTurnScreenOn(config) {
  return withAndroidManifest(config, (config) => {
    const application = config.modResults.manifest.application?.[0];
    if (!application) {
      console.warn('[withTurnScreenOn] No <application> element found – skipping.');
      return config;
    }

    const activities = application.activity ?? [];
    const mainActivity = activities.find(
      (a) => a.$['android:name'] === '.MainActivity',
    );

    if (!mainActivity) {
      console.warn('[withTurnScreenOn] MainActivity not found in manifest – skipping.');
      return config;
    }

    mainActivity.$['android:turnScreenOn']   = 'true';
    mainActivity.$['android:showWhenLocked'] = 'true';

    console.log('[withTurnScreenOn] ✓ turnScreenOn + showWhenLocked applied to MainActivity.');
    return config;
  });
};
