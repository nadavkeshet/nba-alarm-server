/**
 * Expo Config Plugin – Call-Style Full Screen Intent for NBA Alarm
 *
 * Patches ExpoNotificationBuilder.kt in expo-notifications to inject three
 * native builder calls that are not exposed through the JS API:
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
 * Migration: if the v1 patch is already applied, this plugin strips it and
 * re-applies v2 so a clean npm install is not required before each build.
 */

const { withDangerousMod } = require('@expo/config-plugins');
const fs   = require('fs');
const path = require('path');

const V1_MARKER   = '// [NBA_ALARM_FSI_PATCH_v1]';
const PATCH_MARKER = '// [NBA_ALARM_FSI_PATCH_v2]';

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

      // ── Already on v2 ──────────────────────────────────────────────────────
      if (src.includes(PATCH_MARKER)) {
        console.log('[withFullScreenIntent] v2 already applied – skipping.');
        return config;
      }

      // ── Migrate from v1: strip old patch, restore TARGET_LINE ──────────────
      if (src.includes(V1_MARKER)) {
        console.log('[withFullScreenIntent] v1 patch found – migrating to v2…');
        // The v1 block starts at the marker and ends with `    return builder.build()`
        // Replace the whole v1 block with just the plain TARGET_LINE so the v2
        // replacement below has a clean base to work with.
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

      // ── v2 Kotlin block ────────────────────────────────────────────────────
      //
      // Injected immediately before `return builder.build()`.
      //
      // CATEGORY_CALL  : The key that makes OnePlus (and most OEMs) wake the
      //                  screen. WhatsApp and Instagram both use this category.
      //                  Without it, FSI fires but the screen may stay off on
      //                  aggressive power-saving OEMs.
      //
      // PRIORITY_MAX   : Explicit notification-level priority. Redundant on
      //                  Android 8+ (channel importance governs) but kept for
      //                  completeness and older device compat.
      //
      // setFullScreenIntent(true): Launches MainActivity as a full-screen
      //                  activity when the notification arrives.
      //
      const fsiBlock = `    ${PATCH_MARKER}
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
      android.util.Log.w("expo-notifications", "FSI v2 patch error: \${fsiEx.message}")
    }
    return builder.build()`;
      // ────────────────────────────────────────────────────────────────────────

      src = src.replace(TARGET_LINE, fsiBlock);
      fs.writeFileSync(builderPath, src);

      console.log('[withFullScreenIntent] ✓ v2 patch applied (CATEGORY_CALL + FSI + PRIORITY_MAX).');
      return config;
    },
  ]);
};
