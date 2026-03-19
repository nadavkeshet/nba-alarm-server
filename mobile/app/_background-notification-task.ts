/**
 * DEPRECATED — this file is no longer used.
 *
 * Background / killed-state notification handling is now done entirely by
 * @react-native-firebase/messaging + Notifee via firebaseMessaging.js:
 *
 *   messaging().setBackgroundMessageHandler()  — receives the FCM data message
 *   notifee.displayNotification()              — builds and shows the notification
 *   notifee.onBackgroundEvent()                — handles DISMISS button tap
 *
 * This TaskManager-based task was the old expo-notifications approach and
 * is safe to delete once confirmed working.
 */
