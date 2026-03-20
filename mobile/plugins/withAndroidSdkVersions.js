/**
 * Expo Config Plugin – Pin Android SDK versions
 *
 * Expo 54 / RN 0.81 internally targets compileSdk 36, but EAS build images
 * and most third-party libraries (Notifee, Firebase) are tested against SDK 35.
 * This plugin overrides the root project ext block so every submodule picks
 * up SDK 35 rather than the RN-default 36.
 *
 * Also pins Gradle wrapper to 8.10.2 for stability — regenerated from this
 * plugin on every EAS prebuild.
 */

const { withProjectBuildGradle, withDangerousMod } = require('@expo/config-plugins');
const fs   = require('fs');
const path = require('path');

const EXT_BLOCK = `
// ── SDK version pins (withAndroidSdkVersions plugin) ──────────────────────
ext {
    compileSdkVersion = 35
    targetSdkVersion  = 35
    minSdkVersion     = 24
    buildToolsVersion = "35.0.0"
    ndkVersion        = "27.1.12297006"
}
// ──────────────────────────────────────────────────────────────────────────
`;

function withSdkExtBlock(config) {
  return withProjectBuildGradle(config, (cfg) => {
    if (cfg.modResults.contents.includes('withAndroidSdkVersions plugin')) {
      return cfg; // already patched
    }
    // Insert the ext block at the very top (before any plugins run so they
    // see our values via rootProject.ext)
    cfg.modResults.contents = EXT_BLOCK + cfg.modResults.contents;
    console.log('[withAndroidSdkVersions] ✓ SDK 35 ext block added to root build.gradle');
    return cfg;
  });
}

function withGradleWrapper(config) {
  return withDangerousMod(config, [
    'android',
    (cfg) => {
      const wrapperPath = path.join(
        cfg.modRequest.projectRoot,
        'android', 'gradle', 'wrapper', 'gradle-wrapper.properties',
      );
      if (!fs.existsSync(wrapperPath)) return cfg;
      let content = fs.readFileSync(wrapperPath, 'utf8');
      content = content.replace(
        /distributionUrl=.+gradle-.+-bin\.zip/,
        'distributionUrl=https\\://services.gradle.org/distributions/gradle-8.10.2-bin.zip',
      );
      fs.writeFileSync(wrapperPath, content);
      console.log('[withAndroidSdkVersions] ✓ Gradle wrapper pinned to 8.10.2');
      return cfg;
    },
  ]);
}

module.exports = function withAndroidSdkVersions(config) {
  config = withSdkExtBlock(config);
  config = withGradleWrapper(config);
  return config;
};
