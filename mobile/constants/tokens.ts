// ── Design Tokens ─────────────────────────────────────────────────────────────
// Single source of truth for all non-color design values.
// Mirrors the iOS token set — iOS pt values translate 1:1 to RN dp/sp.
// Adaptive colors (light/dark) live in ThemeContext.tsx.

// ── Spacing ───────────────────────────────────────────────────────────────────
// Based on iOS VStack/HStack and padding usage counts.
// Named aliases for the dominant values; intermediates via bracket notation.

export const Spacing = {
  none:  0,
  xs:    4,
  sm:    8,   // most common stack gap (97×)
  md:   12,   // standard padding (71×)
  lg:   16,   // dominant horizontal padding (81×)
  xl:   20,
  // Intermediate values — access as Spacing[6], Spacing[14], etc.
   1:  1,
   2:  2,
   3:  3,
   5:  5,
   6:  6,
   7:  7,
  10: 10,
  14: 14,
  18: 18,
} as const;

export type SpacingValue = typeof Spacing[keyof typeof Spacing];

// ── Corner Radii ──────────────────────────────────────────────────────────────
// Occurrence counts from iOS codebase:
// md(10): 45×  xl(14): 29×  lg(12): 25×  xs(4): 4×  sm(6): 6×

export const Radii = {
  xs:   4,
  sm:   6,
  md:   10,  // most common
  lg:   12,  // alt card
  xl:   14,  // standard card
  pill: 22,
  badge: 38,
  full: 999, // fully circular
} as const;

// ── Font Sizes ────────────────────────────────────────────────────────────────
// iOS pt → RN fontSize: 1:1 (both device-independent units).
// Occurrence counts shown inline for the busy range.

export const FontSize = {
  // Micro labels: badges, section title caps, status dots
  badge:    7,
  tiny:     8,
  xs:       9,    // section-title caps (heavy, letterSpacing 2), live pills

  // Core body scale — the 10–14 range accounts for ~500 text styles in the iOS codebase
  sm:      10,
  base:    11,
  md:      12,    // most common overall (124×)
  lg:      13,
  xl:      14,

  // Sub-heading scale
  sub:     15,
  subLg:   16,
  nav:     17,    // nav bar titles
  lead:    18,

  // Heading scale
  h4:      20,
  h3:      22,
  h2:      24,
  h1:      26,
  score:   28,    // scoreline display
  scoreLg: 30,

  // Display / hero
  display:  32,
  hero:     36,
  heroLg:   40,
  title:    44,
  titleLg:  48,
  jumbo:    56,
} as const;

// ── Font Weights ──────────────────────────────────────────────────────────────
// iOS → RN mapping:
// .light → '300'  .regular → '400'  .medium → '500'
// .semibold → '600'  .bold → '700'  .heavy → '800'  .black → '900'

export const FontWeight = {
  light:    '300',
  regular:  '400',
  medium:   '500',
  semibold: '600',
  bold:     '700',
  heavy:    '800',
  black:    '900',
} as const;

export type FontWeightValue = typeof FontWeight[keyof typeof FontWeight];

// ── Shadows ───────────────────────────────────────────────────────────────────
// Spread into StyleSheet objects: `{ ...Shadow.card, backgroundColor: ... }`
// `text` and `badge` entries are for Text nodes only (textShadow* props).
// Dynamic shadows (drawer animated, team-color button glow) are computed
// in-component and not representable as static tokens.

export const Shadow = {
  // black 0.15, radius 8, y 4 — default card
  card: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.15,
    shadowRadius: 8,
    elevation: 4,
  },
  // black 0.15, radius 4, y 2 — compact card / row
  cardSm: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.15,
    shadowRadius: 4,
    elevation: 2,
  },
  // black 0.20, radius 12, y 6 — prominent card
  elevated: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.20,
    shadowRadius: 12,
    elevation: 6,
  },
  // black 0.30, radius 20, y 10 — modal / bottom sheet
  modal: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.30,
    shadowRadius: 20,
    elevation: 10,
  },
  // black 0.30, radius 1, y 1 — text shadow (Text nodes only)
  text: {
    textShadowColor: 'rgba(0,0,0,0.30)',
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 1,
  },
  // black 0.50, radius 2, y 1 — badge/abbr text (Text nodes only)
  badge: {
    textShadowColor: 'rgba(0,0,0,0.50)',
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 2,
  },
};

// ── Border Widths ─────────────────────────────────────────────────────────────

export const BorderWidth = {
  hairline: 0.5,
  thin:     1,    // default (46×)
  medium:   1.5,
  thick:    2,
} as const;

// ── Opacity ───────────────────────────────────────────────────────────────────
// Keyed by percentage integer: Opacity[12] = 0.12, Opacity[40] = 0.40, etc.
// Most common in iOS codebase: 12, 15, 50, 40, 30.

export const Opacity = {
   4: 0.04,
   5: 0.05,
   6: 0.06,
   7: 0.07,
   8: 0.08,
  10: 0.10,
  12: 0.12,
  13: 0.13,
  15: 0.15,
  18: 0.18,
  20: 0.20,
  25: 0.25,
  28: 0.28,
  30: 0.30,
  35: 0.35,
  40: 0.40,
  45: 0.45,
  50: 0.50,
  55: 0.55,
  60: 0.60,
  65: 0.65,
  70: 0.70,
  80: 0.80,
  85: 0.85,
  90: 0.90,
  95: 0.95,
} as const;

export type OpacityValue = typeof Opacity[keyof typeof Opacity];

// ── Composed style presets ────────────────────────────────────────────────────
// Ready-to-spread style objects for recurring cross-screen patterns.

// Shared tab-screen header title: used by feed, sports, and active.
export const tabHeaderTitle = {
  fontSize:   FontSize.nav,    // 17
  fontWeight: FontWeight.bold, // '700'
};
