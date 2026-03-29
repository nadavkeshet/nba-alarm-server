/**
 * Expo Config Plugin – Call-Style Full Screen Intent for NBA Alarm
 *
 * Patches ExpoNotificationBuilder.kt in expo-notifications to inject:
 *
 *   builder.setCategory(Notification.CATEGORY_CALL)
 *     → Tells OnePlus/Samsung/MIUI OEMs this notification is a live call.
 *       These launchers grant it maximum visual priority, bypass DND, and
 *       wake the screen — exactly what WhatsApp/Instagram use.
 *
 *   builder.setFullScreenIntent(pendingIntent, true)
 *     → Brings the app to the foreground even when the phone is locked.
 *
 *   builder.setPriority(NotificationCompat.PRIORITY_MAX)
 *     → Explicit max priority on the notification object (redundant on
 *       Android 8+ where channel importance wins, but reinforces on older).
 *
 *   DISMISS action fallback
 *     → For classic_alarm notifications where Expo's push service did not
 *       forward categoryId into the FCM data payload, directly adds a DISMISS
 *       action button using Expo's own buildButtonAction / response-intent
 *       mechanism so the JS addNotificationResponseReceivedListener fires.
 *
 * Migration: v1 → v2 → v3 patches are detected and upgraded automatically.
 */

const { withDangerousMod } = require('@expo/config-plugins');
const fs   = require('fs');
const path = require('path');

const V1_MARKER    = '// [NBA_ALARM_FSI_PATCH_v1]';
const V2_MARKER    = '// [NBA_ALARM_FSI_PATCH_v2]';
const PATCH_MARKER = '// [NBA_ALARM_FSI_PATCH_v3]';

const BUILDER_PATH = [
  'expo-notifications',
  'android', 'src', 'main', 'java',
  'expo', 'modules', 'notifications',
  'notifications', 'presentation', 'builders',
  'ExpoNotificationBuilder.kt',
].join(path.sep);

// Exact string we inject before — the last line of build() in expo-notifications 0.32.x
const TARGET_LINE = '    return builder.build()';

module.exports = function withFullScreenIntent(config) {
  return withDangerousMod(config, [
    'android',
    (config) => {
      const builderPath = path.join(
        config.modRequest.projectRoot,
        'node_modules',
        BUILDER_PATH,
      );

      if (!fs.existsSync(builderPath)) {
        console.warn(
          '[withFullScreenIntent] ExpoNotificationBuilder.kt not found – FSI patch skipped.\n' +
          '  Expected: ' + builderPath,
        );
        return config;
      }

      let src = fs.readFileSync(builderPath, 'utf8');

      // ── Already on v3 ──────────────────────────────────────────────────────
      if (src.includes(PATCH_MARKER)) {
        console.log('[withFullScreenIntent] v3 already applied – skipping.');
        return config;
      }

      // ── Migrate from v2: strip old patch, restore TARGET_LINE ──────────────
      if (src.includes(V2_MARKER)) {
        console.log('[withFullScreenIntent] v2 patch found – migrating to v3…');
        src = src.replace(
          /    \/\/ \[NBA_ALARM_FSI_PATCH_v2\][\s\S]*?return builder\.build\(\)/,
          TARGET_LINE,
        );
      }

      // ── Migrate from v1: strip old patch, restore TARGET_LINE ──────────────
      if (src.includes(V1_MARKER)) {
        console.log('[withFullScreenIntent] v1 patch found – migrating to v3…');
        src = src.replace(
          /    \/\/ \[NBA_ALARM_FSI_PATCH_v1\][\s\S]*?return builder\.build\(\)/,
          TARGET_LINE,
        );
      }

      if (!src.includes(TARGET_LINE)) {
        console.warn(
          '[withFullScreenIntent] Target line not found in ExpoNotificationBuilder.kt – FSI patch skipped.',
        );
        return config;
      }

      // ── v3 Kotlin block ────────────────────────────────────────────────────
      //
      // Injected immediately before `return builder.build()`.
      //
      // New in v3: DISMISS action fallback.
      //   Expo's push service is supposed to forward `categoryId` into the
      //   FCM data map so ExpoNotificationBuilder can call addActionsToBuilder.
      //   In practice, some SDK/server combinations leave categoryId null for
      //   remote pushes, so the DISMISS button never appears even though the
      //   sound and channel work fine (channelId is handled separately).
      //   The fallback: when the trigger channel is "classic_alarm" AND
      //   notificationContent.categoryId is null (standard path didn't fire),
      //   we directly construct a NotificationAction("dismiss","DISMISS",false)
      //   and call the existing buildButtonAction() helper to create the proper
      //   Expo response-intent.  Tapping DISMISS then fires the JS
      //   addNotificationResponseReceivedListener with actionIdentifier="dismiss".
      //
      const fsiBlock = `    ${PATCH_MARKER}
    // ── DISMISS action fallback ─────────────────────────────────────────────
    // If Expo's push service did not forward categoryId into the FCM data
    // payload, notificationContent.categoryId is null and addActionsToBuilder
    // was never called above (line 113). Detect alarm notifications by their
    // channelId and add DISMISS directly via Expo's own buildButtonAction so
    // the JS response listener fires normally when the user taps DISMISS.
    try {
      val fsiTriggerChannelId = (notification.notificationRequest.trigger
        as? expo.modules.notifications.notifications.model.triggers.FirebaseNotificationTrigger
      )?.channelId
      if (fsiTriggerChannelId == "classic_alarm" && notificationContent.categoryId == null) {
        builder.addAction(
          buildButtonAction(
            expo.modules.notifications.notifications.model.NotificationAction(
              "dismiss", "DISMISS", false
            )
          )
        )
      }
    } catch (fsiDismissEx: Exception) {
      android.util.Log.w("expo-notifications", "NBA DISMISS fallback: \${fsiDismissEx.message}")
    }
    // ── Call-style Full Screen Intent ──────────────────────────────────────
    // Mimics WhatsApp/Instagram call behaviour: screen wakes, lock screen
    // is bypassed, and the app is brought to the foreground immediately.
    try {
      val fsiLaunchIntent = context.packageManager.getLaunchIntentForPackage(context.packageName)
      if (fsiLaunchIntent != null) {
        fsiLaunchIntent.addFlags(
          android.content.Intent.FLAG_ACTIVITY_NEW_TASK or
          android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP or
          android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP
        )
        val fsiPiFlags = android.app.PendingIntent.FLAG_UPDATE_CURRENT or
          (if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.M)
            android.app.PendingIntent.FLAG_IMMUTABLE else 0)
        val fsiPendingIntent = android.app.PendingIntent.getActivity(
          context, 9001, fsiLaunchIntent, fsiPiFlags
        )
        // CATEGORY_CALL is the magic flag: OEM ROMs (OnePlus OxygenOS,
        // Samsung One UI, MIUI) treat this as a live call notification and
        // wake the screen even when battery saver / DND is active.
        builder.setCategory(android.app.Notification.CATEGORY_CALL)
        // Explicit PRIORITY_MAX on the notification object itself.
        builder.setPriority(androidx.core.app.NotificationCompat.PRIORITY_MAX)
        // Full Screen Intent – launches the activity on notification arrival.
        builder.setFullScreenIntent(fsiPendingIntent, true)
      }
    } catch (fsiEx: Exception) {
      android.util.Log.w("expo-notifications", "FSI v3 patch error: \${fsiEx.message}")
    }
    return builder.build()`;
      // ────────────────────────────────────────────────────────────────────────

      src = src.replace(TARGET_LINE, fsiBlock);
      fs.writeFileSync(builderPath, src);

      console.log('[withFullScreenIntent] ✓ v3 patch applied (DISMISS fallback + CATEGORY_CALL + FSI + PRIORITY_MAX).');
      return config;
    },
  ]);
};
