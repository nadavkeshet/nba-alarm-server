// Custom entry point — must register Firebase background handlers BEFORE
// Expo Router loads any React components.  In killed-state / headless JS,
// only this file and its direct imports are guaranteed to run.
// firebaseMessaging.js registers:
//   - messaging().setBackgroundMessageHandler(...)
//   - notifee.onBackgroundEvent(...)
import './firebaseMessaging';

// Apply Inter font mapping before any screen module is loaded.
// Metro transforms imports to sequential require() calls, so this runs before
// expo-router/entry triggers the require chain for all screens.
import './utils/patchFonts';

// Hand off to Expo Router's default entry (loads app/_layout.tsx etc.)
import 'expo-router/entry';
