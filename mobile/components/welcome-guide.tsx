import { Ionicons } from '@expo/vector-icons';
import React, { useRef, useState } from 'react';
import {
  Animated,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLanguage, type TFunc } from '../context/LanguageContext';

// ─── Theme ────────────────────────────────────────────────────────────────────

const C = {
  bg:      '#0B1623',
  card:    '#0d1f35',
  border:  '#1a3451',
  text:    '#e6edf3',
  muted:   '#6b8aad',
  orange:  '#FF6B00',
  gold:    '#f0a500',
};

// ─── Slide definitions ────────────────────────────────────────────────────────

interface Slide {
  icon:     React.ComponentProps<typeof Ionicons>['name'];
  color:    string;
  bg:       string;
  title:    string;
  body?:    string;
  bullets?: string[];
  where?:   string;
}

function makeSlides(t: TFunc): Slide[] {
  return [
    {
      // 1 — What the app is
      icon:  'notifications' as const,
      color: C.orange, bg: 'rgba(255,107,0,0.12)',
      title: t('guide.slide1.title'),
      body:  t('guide.slide1.body'),
    },
    {
      // 2 — Drama Engine
      icon:  'bar-chart' as const,
      color: C.orange, bg: 'rgba(255,107,0,0.12)',
      title: t('guide.slide2.title'),
      body:  t('guide.slide2.body'),
      where: t('guide.slide2.where'),
    },
    {
      // 3 — 7 Football criteria
      icon:    'football' as const,
      color:   '#22c55e', bg: 'rgba(34,197,94,0.12)',
      title:   t('guide.slide3.title'),
      bullets: [t('guide.slide3.b1'), t('guide.slide3.b2'), t('guide.slide3.b3'), t('guide.slide3.b4'), t('guide.slide3.b5'), t('guide.slide3.b6'), t('guide.slide3.b7')],
    },
    {
      // 4 — Follow teams & leagues
      icon:  'heart' as const,
      color: C.orange, bg: 'rgba(255,107,0,0.12)',
      title: t('guide.slide4.title'),
      body:  t('guide.slide4.body'),
      where: t('guide.slide4.where'),
    },
    {
      // 5 — Per-match alarms
      icon:  'alarm' as const,
      color: C.orange, bg: 'rgba(255,107,0,0.12)',
      title: t('guide.slide5.title'),
      body:  t('guide.slide5.body'),
      where: t('guide.slide5.where'),
    },
    {
      // 6 — Basketball
      icon:    'basketball' as const,
      color:   '#4d8cf5', bg: 'rgba(77,140,245,0.12)',
      title:   t('guide.slide6.title'),
      bullets: [t('guide.slide6.b1'), t('guide.slide6.b2'), t('guide.slide6.b3'), t('guide.slide6.b4'), t('guide.slide6.b5')],
    },
    {
      // 7 — Notifications
      icon:    'notifications-circle' as const,
      color:   C.gold, bg: 'rgba(240,165,0,0.12)',
      title:   t('guide.slide7.title'),
      bullets: [t('guide.slide7.b1'), t('guide.slide7.b2'), t('guide.slide7.b3'), t('guide.slide7.b4'), t('guide.slide7.b5')],
    },
    {
      // 8 — Good to know
      icon:    'information-circle' as const,
      color:   C.muted, bg: 'rgba(107,138,173,0.12)',
      title:   t('guide.slide8.title'),
      bullets: [t('guide.slide8.b1'), t('guide.slide8.b2'), t('guide.slide8.b3'), t('guide.slide8.b4')],
    },
  ];
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  visible:     boolean;
  onClose:     () => void;
  closeLabel?: string;   // "Get Started" on first launch, "Close" from settings
}

// ─── Component ────────────────────────────────────────────────────────────────

export function WelcomeGuide({ visible, onClose, closeLabel }: Props) {
  const { t } = useLanguage();
  const slides = makeSlides(t);
  const [index, setIndex] = useState(0);
  const fade              = useRef(new Animated.Value(1)).current;

  const goTo = (next: number) => {
    Animated.timing(fade, { toValue: 0, duration: 130, useNativeDriver: true }).start(() => {
      setIndex(next);
      Animated.timing(fade, { toValue: 1, duration: 180, useNativeDriver: true }).start();
    });
  };

  const handleClose = () => {
    setIndex(0); // reset for next open
    onClose();
  };

  const slide      = slides[index];
  const isLast     = index === slides.length - 1;
  const isFirst    = index === 0;

  return (
    <Modal visible={visible} animationType="fade" statusBarTranslucent>
      <SafeAreaView style={styles.container}>

        {/* ── Skip (top right, only on non-last slides) ──────────────── */}
        <View style={styles.topBar}>
          <View style={{ flex: 1 }} />
          {!isLast && (
            <TouchableOpacity onPress={handleClose} hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}>
              <Text style={styles.skipText}>{t('guide.skip')}</Text>
            </TouchableOpacity>
          )}
        </View>

        {/* ── Slide content ──────────────────────────────────────────── */}
        <Animated.View style={[styles.slideWrap, { opacity: fade }]}>
          <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.slideScroll}>

          {/* Icon */}
          <View style={[styles.iconCircle, { backgroundColor: slide.bg }]}>
            <Ionicons name={slide.icon} size={52} color={slide.color} />
          </View>

          {/* Text */}
          <Text style={styles.slideTitle}>{slide.title}</Text>

          {slide.bullets ? (
            <View style={styles.bulletList}>
              {slide.bullets.map((b, i) => (
                <View key={i} style={styles.bulletRow}>
                  <View style={styles.bulletDot} />
                  <Text style={styles.bulletText}>{b}</Text>
                </View>
              ))}
            </View>
          ) : (
            <Text style={styles.slideBody}>{slide.body}</Text>
          )}

          {/* WHERE tag — only when provided */}
          {slide.where && (
            <View style={styles.whereTag}>
              <Ionicons name="compass-outline" size={12} color={C.muted} style={{ marginRight: 5 }} />
              <Text style={styles.whereText}>{slide.where}</Text>
            </View>
          )}

          </ScrollView>
        </Animated.View>

        {/* ── Dot indicators ─────────────────────────────────────────── */}
        <View style={styles.dots}>
          {slides.map((_, i) => (
            <TouchableOpacity key={i} onPress={() => goTo(i)}>
              <View style={[styles.dot, i === index && styles.dotActive]} />
            </TouchableOpacity>
          ))}
        </View>

        {/* ── Navigation buttons ─────────────────────────────────────── */}
        <View style={styles.navRow}>
          {/* Back */}
          <TouchableOpacity
            style={[styles.navBtn, styles.navBtnSecondary, isFirst && styles.navBtnHidden]}
            onPress={() => goTo(index - 1)}
            disabled={isFirst}
          >
            <Ionicons name="arrow-back" size={18} color={C.muted} />
            <Text style={styles.navBtnSecondaryText}>{t('guide.back')}</Text>
          </TouchableOpacity>

          {/* Next / Get Started */}
          {isLast ? (
            <TouchableOpacity style={[styles.navBtn, styles.navBtnPrimary]} onPress={handleClose}>
              <Text style={styles.navBtnPrimaryText}>{closeLabel ?? t('guide.getStarted')}</Text>
              <Ionicons name="checkmark-circle" size={18} color="#fff" />
            </TouchableOpacity>
          ) : (
            <TouchableOpacity style={[styles.navBtn, styles.navBtnPrimary]} onPress={() => goTo(index + 1)}>
              <Text style={styles.navBtnPrimaryText}>{t('guide.next')}</Text>
              <Ionicons name="arrow-forward" size={18} color="#fff" />
            </TouchableOpacity>
          )}
        </View>

        {/* ── Slide counter ──────────────────────────────────────────── */}
        <Text style={styles.counter}>{t('guide.counter', index + 1, slides.length)}</Text>

      </SafeAreaView>
    </Modal>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1, backgroundColor: C.bg,
    paddingHorizontal: 28,
    direction: 'ltr',
  },

  topBar: {
    flexDirection: 'row', alignItems: 'center',
    paddingTop: 8, paddingBottom: 4,
    minHeight: 44,
  },
  skipText: {
    color: C.muted, fontSize: 14, fontWeight: '600',
  },

  // ── Slide ──────────────────────────────────────────────────────────
  slideWrap: {
    flex: 1,
    paddingBottom: 24,
  },
  slideScroll: {
    alignItems: 'center', justifyContent: 'center',
    flexGrow: 1, paddingBottom: 8,
  },

  iconCircle: {
    width: 108, height: 108, borderRadius: 54,
    alignItems: 'center', justifyContent: 'center',
    marginBottom: 32,
    borderWidth: 1.5, borderColor: 'rgba(255,107,0,0.2)',
  },

  slideTitle: {
    fontSize: 28, fontWeight: '900', color: C.text,
    textAlign: 'center', letterSpacing: 0.3,
    marginBottom: 16,
  },
  slideBody: {
    fontSize: 15, color: C.muted, textAlign: 'center',
    lineHeight: 24, marginBottom: 28,
  },

  whereTag: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: C.card,
    borderRadius: 20, borderWidth: 1, borderColor: C.border,
    paddingHorizontal: 14, paddingVertical: 7,
  },
  whereText: {
    fontSize: 12, color: C.muted, fontWeight: '700', letterSpacing: 0.5,
  },

  // ── Dots ───────────────────────────────────────────────────────────
  dots: {
    flexDirection: 'row', justifyContent: 'center',
    gap: 8, marginBottom: 24,
  },
  dot: {
    width: 7, height: 7, borderRadius: 3.5,
    backgroundColor: C.border,
  },
  dotActive: {
    width: 22, borderRadius: 4,
    backgroundColor: C.orange,
  },

  // ── Nav ────────────────────────────────────────────────────────────
  navRow: {
    flexDirection: 'row', gap: 12,
    marginBottom: 12,
  },
  navBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center',
    justifyContent: 'center', gap: 8,
    paddingVertical: 15, borderRadius: 14,
  },
  navBtnHidden: { opacity: 0 },

  navBtnSecondary: {
    backgroundColor: C.card,
    borderWidth: 1, borderColor: C.border,
  },
  navBtnSecondaryText: {
    color: C.muted, fontWeight: '700', fontSize: 15,
  },

  navBtnPrimary: {
    backgroundColor: C.orange,
  },
  navBtnPrimaryText: {
    color: '#fff', fontWeight: '800', fontSize: 15,
  },

  // ── Bullet list ────────────────────────────────────────────────────
  bulletList: {
    width: '100%', gap: 12, marginBottom: 20,
  },
  bulletRow: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
  },
  bulletDot: {
    width: 6, height: 6, borderRadius: 3,
    backgroundColor: C.orange, marginTop: 7, flexShrink: 0,
  },
  bulletText: {
    flex: 1, fontSize: 13, color: C.muted, lineHeight: 20,
  },

  counter: {
    textAlign: 'center', color: C.border,
    fontSize: 12, fontWeight: '700',
    marginBottom: 8,
  },
});
