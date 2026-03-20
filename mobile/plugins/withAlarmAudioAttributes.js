/**
 * Expo Config Plugin – Alarm Channel Audio Attributes
 *
 * Writes AlarmChannelModule.kt + AlarmChannelPackage.kt into the Android
 * project and registers the package in MainApplication.kt so JS can call
 * NativeModules.AlarmChannel.patchChannel(channelId, soundFile) to
 * recreate a Notifee channel with AudioAttributes.USAGE_ALARM, making
 * notification sounds play even in vibrate / ringer-off mode.
 */

const { withDangerousMod, withMainApplication } = require('@expo/config-plugins');
const fs   = require('fs');
const path = require('path');

const PACKAGE_PATH = 'com/nadav/nbaalarmapp';

const MODULE_KT = `\
package com.nadav.nbaalarmapp

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.media.AudioAttributes
import android.net.Uri
import android.os.Build
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

class AlarmChannelModule(reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName() = "AlarmChannel"

    @ReactMethod
    fun patchChannel(channelId: String, soundFileName: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val ctx = reactApplicationContext
        val nm  = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        nm.deleteNotificationChannel(channelId)

        val soundUri = Uri.parse(
            "android.resource://\${ctx.packageName}/raw/\$soundFileName"
        )

        val audioAttr = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()

        val channel = NotificationChannel(
            channelId,
            "Classic Alarm",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            setBypassDnd(true)
            setShowBadge(true)
            enableVibration(true)
            vibrationPattern = longArrayOf(100, 300, 200, 400)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            setSound(soundUri, audioAttr)
        }

        nm.createNotificationChannel(channel)
    }
}
`;

const PACKAGE_KT = `\
package com.nadav.nbaalarmapp

import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.uimanager.ViewManager

class AlarmChannelPackage : ReactPackage {
    override fun createNativeModules(ctx: ReactApplicationContext): List<NativeModule> =
        listOf(AlarmChannelModule(ctx))

    override fun createViewManagers(ctx: ReactApplicationContext): List<ViewManager<*, *>> =
        emptyList()
}
`;

function withAlarmKotlinFiles(config) {
  return withDangerousMod(config, [
    'android',
    (cfg) => {
      const javaDir = path.join(
        cfg.modRequest.projectRoot,
        'android', 'app', 'src', 'main', 'java',
        ...PACKAGE_PATH.split('/'),
      );
      fs.mkdirSync(javaDir, { recursive: true });
      fs.writeFileSync(path.join(javaDir, 'AlarmChannelModule.kt'), MODULE_KT);
      fs.writeFileSync(path.join(javaDir, 'AlarmChannelPackage.kt'), PACKAGE_KT);
      console.log('[withAlarmAudioAttributes] ✓ Kotlin files written');
      return cfg;
    },
  ]);
}

function withAlarmPackageRegistration(config) {
  return withMainApplication(config, (cfg) => {
    let src = cfg.modResults.contents;
    if (src.includes('AlarmChannelPackage')) return cfg; // already patched
    src = src.replace(
      /PackageList\(this\)\.packages\.apply \{[\s\S]*?\}/,
      match => match.replace(
        /(\s*\/\/ Packages that cannot be autolinked.*\n)?(\s*)}\s*$/,
        '\n              add(AlarmChannelPackage())\n            }',
      ),
    );
    cfg.modResults.contents = src;
    console.log('[withAlarmAudioAttributes] ✓ AlarmChannelPackage registered');
    return cfg;
  });
}

module.exports = function withAlarmAudioAttributes(config) {
  config = withAlarmKotlinFiles(config);
  config = withAlarmPackageRegistration(config);
  return config;
};
