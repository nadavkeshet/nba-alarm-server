/**
 * Expo Config Plugin – Pin Gradle wrapper version
 *
 * RN 0.81 / AGP 8.11 requires Gradle 8.13 minimum.
 * This plugin ensures the gradle-wrapper.properties always uses 8.13
 * on EAS cloud builds (where the file is regenerated from scratch).
 */

const { withDangerousMod } = require('@expo/config-plugins');
const fs   = require('fs');
const path = require('path');

module.exports = function withAndroidSdkVersions(config) {
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
        'distributionUrl=https\\://services.gradle.org/distributions/gradle-8.13-bin.zip',
      );
      fs.writeFileSync(wrapperPath, content);
      console.log('[withAndroidSdkVersions] ✓ Gradle wrapper pinned to 8.13');
      return cfg;
    },
  ]);
};
