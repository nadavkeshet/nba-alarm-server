package com.nadav.nbaalarmapp

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

/**
 * Native module with two responsibilities:
 *
 * 1. patchChannel()     – recreates a Notifee channel with USAGE_ALARM so the
 *                         one-shot notification sound bypasses vibrate/silent mode.
 *
 * 2. startAlarmSound()  – starts a looping MediaPlayer on STREAM_ALARM.
 *    stopAlarmSound()   – stops and releases it.
 *
 * The MediaPlayer is held in a companion-object singleton so it can be stopped
 * from ANY JS context (foreground or background headless task).
 */
class AlarmChannelModule(reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName() = "AlarmChannel"

    // ── Singleton player ────────────────────────────────────────────────────────

    companion object {
        @Volatile private var player: MediaPlayer? = null
        @Volatile private var wakeLock: PowerManager.WakeLock? = null

        @Synchronized
        fun stopPlayer() {
            player?.apply {
                try { if (isPlaying) stop() } catch (_: Exception) {}
                try { release() }             catch (_: Exception) {}
            }
            player = null
            wakeLock?.apply {
                try { if (isHeld) release() } catch (_: Exception) {}
            }
            wakeLock = null
        }
    }

    // ── React methods ───────────────────────────────────────────────────────────

    @ReactMethod
    fun patchChannel(channelId: String, soundFileName: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val ctx = reactApplicationContext
        val nm  = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

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

    @ReactMethod
    fun startAlarmSound() {
        stopPlayer() // release any previous instance first
        val ctx = reactApplicationContext

        // Wake the screen (shows lock screen with notification, without opening the app)
        try {
            val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
            @Suppress("DEPRECATION")
            wakeLock = pm.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
                "NBAAlarmApp:AlarmWakeLock"
            ).also { it.acquire(60_000L) } // auto-release after 60 s
        } catch (_: Exception) {}

        val soundUri = Uri.parse(
            "android.resource://${ctx.packageName}/raw/classic_alarm"
        )
        val audioAttr = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()
        try {
            player = MediaPlayer().apply {
                setAudioAttributes(audioAttr)
                setDataSource(ctx, soundUri)
                isLooping = true
                prepare()
                start()
            }
        } catch (e: Exception) {
            player = null
        }
    }

    @ReactMethod
    fun stopAlarmSound() {
        stopPlayer()
    }
}
