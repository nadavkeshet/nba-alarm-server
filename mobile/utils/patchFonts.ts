/**
 * Applied at app startup (index.js) BEFORE expo-router/entry loads any screen.
 *
 * Approach — two hooks:
 *
 * 1. StyleSheet.create patch — intercepts every StyleSheet.create call in the
 *    entire app (and most library code) and maps fontWeight → the matching Inter
 *    family name.  Running before expo-router/entry guarantees all screen modules
 *    are transformed on their first require().
 *
 * 2. Text.defaultProps.style — covers <Text> nodes that use inline styles or no
 *    style at all, where StyleSheet.create never ran.
 *
 * Android does NOT synthesise bold from a base family — each weight must be its
 * own fontFamily string.  This patch ensures every weight resolves correctly on
 * both platforms without touching individual components.
 */

import { StyleSheet, Text } from 'react-native';

// fontWeight → loaded Inter variant name
const INTER: Record<string, string> = {
  '100': 'Inter_400Regular',   // Thin not loaded; use Regular
  '200': 'Inter_400Regular',   // ExtraLight not loaded
  '300': 'Inter_400Regular',   // Light not loaded
  '400': 'Inter_400Regular',
  '500': 'Inter_500Medium',
  '600': 'Inter_600SemiBold',
  '700': 'Inter_700Bold',
  '800': 'Inter_800ExtraBold',
  '900': 'Inter_800ExtraBold', // Black not loaded; use ExtraBold
  'normal': 'Inter_400Regular',
  'bold':   'Inter_700Bold',
};

// ── 1. StyleSheet.create ──────────────────────────────────────────────────────
const _origCreate = StyleSheet.create.bind(StyleSheet);
(StyleSheet as any).create = function patchedCreate<T extends Record<string, any>>(styles: T): T {
  const out: Record<string, any> = {};
  for (const [key, style] of Object.entries(styles)) {
    if (style && typeof style === 'object' && !Array.isArray(style) && !(style as any).fontFamily) {
      const weight = (style as any).fontWeight;
      const family = weight ? (INTER[String(weight)] ?? 'Inter_400Regular') : 'Inter_400Regular';
      out[key] = { ...style, fontFamily: family, fontWeight: undefined };
    } else {
      out[key] = style;
    }
  }
  return _origCreate(out as T);
};

// ── 2. Text.defaultProps ──────────────────────────────────────────────────────
(Text as any).defaultProps = {
  ...((Text as any).defaultProps ?? {}),
  style: { fontFamily: 'Inter_400Regular' },
};
