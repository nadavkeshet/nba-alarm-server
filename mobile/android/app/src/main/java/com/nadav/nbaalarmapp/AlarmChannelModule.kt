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

/**
 * Recreates a Notifee-created notification channel with AudioAttributes.USAGE_ALARM
 * so the sound routes through STREAM_ALARM and plays even in vibrate/ringer-off mode.
 *
 * Called from index.tsx after setupNotifee() creates the base channel.
 */
class AlarmChannelModule(reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName() = "AlarmChannel"

    @ReactMethod
    fun patchChannel(channelId: String, soundFileName: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val ctx = reactApplicationContext
        val nm  = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        // Delete so Android picks up the new audio attributes (cached otherwise)
        nm.deleteNotificationChannel(channelId)

        val soundUri = Uri.parse(
            "android.resource://${ctx.packageName}/raw/$soundFileName"
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
