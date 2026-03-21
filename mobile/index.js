// Custom entry point — must register Firebase background handlers BEFORE
// Expo Router loads any React components.  In killed-state / headless JS,
// only this file and its direct imports are guaranteed to run.
// firebaseMessaging.js registers:
//   - messaging().setBackgroundMessageHandler(...)
//   - notifee.onBackgroundEvent(...)
import './firebaseMessaging';

// Hand off to Expo Router's default entry (loads app/_layout.tsx etc.)
import 'expo-router/entry';
