/**
 * Expo Config Plugin – Copy sound files to Android res/raw
 *
 * expo-notifications used to handle this automatically.  Now that we use
 * Notifee for notification display we need to copy our custom sound files
 * to android/app/src/main/res/raw/ ourselves so Notifee (and the Android
 * notification channel) can reference them by name.
 */

const { withDangerousMod } = require('@expo/config-plugins');
const fs   = require('fs');
const path = require('path');

const SOUNDS = [
  'classic_alarm.wav',
  'musical1.wav',
  'musical2.wav',
  'buzzer.wav',
  'dreamscape.wav',
  'morningjoy.wav',
];

module.exports = function withSoundFiles(config) {
  return withDangerousMod(config, [
    'android',
    (config) => {
      const resRawDir = path.join(
        config.modRequest.projectRoot,
        'android', 'app', 'src', 'main', 'res', 'raw',
      );
      fs.mkdirSync(resRawDir, { recursive: true });

      for (const file of SOUNDS) {
        const src = path.join(config.modRequest.projectRoot, 'assets', 'sounds', file);
        const dst = path.join(resRawDir, file);
        if (fs.existsSync(src)) {
          fs.copyFileSync(src, dst);
          console.log(`[withSoundFiles] ✓ ${file} → res/raw`);
        } else {
          console.warn(`[withSoundFiles] ⚠ ${file} not found at ${src}`);
        }
      }

      return config;
    },
  ]);
};
