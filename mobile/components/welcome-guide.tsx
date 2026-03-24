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

const SLIDES: Slide[] = [
  {
    icon:    'notifications' as const,
    color:   C.orange,
    bg:      'rgba(255,107,0,0.12)',
    title:   'Game & Player Alarms',
    body:    'Tap any live or upcoming game to set a one-shot alert. Choose a score gap threshold for the game itself, or pick a specific player and configure independent conditions — Points Goal, Early Scorer, or Triple-Double Watch.',
    where:   'Home Tab → tap any game',
  },
  {
    icon:    'person-circle' as const,
    color:   C.orange,
    bg:      'rgba(255,107,0,0.12)',
    title:   'Favorite Players',
    body:    'Track players across every game, automatically. Configure Early Points thresholds (e.g. 15 pts by Q1), scoring milestones, and Triple-Double watches — each condition fires independently.',
    where:   'Settings → Favorite Players',
  },
  {
    icon:    'shield' as const,
    color:   C.orange,
    bg:      'rgba(255,107,0,0.12)',
    title:   'Favorite Teams',
    body:    'Add your squad and we\'ll monitor every game they play. Set your own score gap — never miss a nail-biter from your team again.',
    where:   'Settings → Favorite Teams',
  },
  {
    icon:    'radio' as const,
    color:   C.orange,
    bg:      'rgba(255,107,0,0.12)',
    title:   'Global Radar',
    body:    'Sit back while the app scans the entire league automatically. You set your own score gap and starting quarter — any game that meets your criteria will trigger an alert, no matter who\'s playing.',
    where:   'Settings → Global Radar',
  },
  {
    icon:    'time' as const,
    color:   C.gold,
    bg:      'rgba(240,165,0,0.12)',
    title:   'Historical Mode',
    body:    'Get alerted for once-in-a-generation stat lines: 40+ points before halftime, 12+ three-pointers made in a game (with 3+ minutes remaining), or a Quad-Double threat quietly developing.',
    where:   'Settings → Historical Mode',
  },
  {
    icon:    'notifications-circle' as const,
    color:   '#4d8cf5',
    bg:      'rgba(77,140,245,0.12)',
    title:   'How Notifications Work',
    bullets: [
      '🔔  Alarm mode — a looping sound plays even if your phone is on silent or vibrate. The screen wakes up and shows the notification on your lock screen. Tap the notification to open the app and stop the music, or hit DISMISS to silence it without opening the app.',
      '🔕  Silent mode — a regular notification appears with the game or player details, no sound. Tap it to open the app.',
      '📵  Do Not Disturb? No problem — Alarm mode routes audio through the system alarm channel, which bypasses DND and the ringer switch.',
      '⚠️  IMPORTANT: The sound uses your Alarm volume — not media or ringtone volume. If you hear nothing, go to your phone\'s sound settings and turn up the Alarm volume.',
      '⚠️  IMPORTANT: Android may stop background apps to save battery, which can block notifications. To prevent this, go to Settings → Apps → Nba Smart Alarm → Battery → set to Unrestricted. On Samsung, also make sure the app is not listed under Sleeping Apps.',
    ],
  },
  {
    icon:    'copy' as const,
    color:   '#4d8cf5',
    bg:      'rgba(77,140,245,0.12)',
    title:   'No Double Alerts',
    bullets: [
      'Set a Game Alarm on a match where your Favorite Team is playing? No problem — the app detects the overlap and lets your manual alarm run. The Favorite Team monitor steps aside for that game.',
      'Same goes for players: if you set a Player Alarm on someone who is also in your Favorite Players list, the manual alarm takes over and the favorite watch pauses for that game.',
      'In short — manual alarms always take priority. You\'ll never get two alerts for the same event.',
    ],
  },
  {
    icon:    'information-circle' as const,
    color:   C.muted,
    bg:      'rgba(107,138,173,0.12)',
    title:   'Good to Know',
    bullets: [
      'Each alert fires once per game, per night — the same event won\'t wake you up twice.',
      'Early Points has a deadline: the alert only fires while the game is still in or before your chosen quarter. Once the game passes it, the condition expires silently — even if the player ends up with 40 points.',
      'Game Alarms only start watching from the quarter you pick — anything that happens before that quarter is ignored.',
      'You can revisit this guide at any time from the bottom of the Settings tab.',
      'Questions, requests, or suggestions? Reach us at nbasmartalarm@gmail.com',
    ],
  },
];

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  visible:     boolean;
  onClose:     () => void;
  closeLabel?: string;   // "Get Started" on first launch, "Close" from settings
}

// ─── Component ────────────────────────────────────────────────────────────────

export function WelcomeGuide({ visible, onClose, closeLabel = 'Get Started' }: Props) {
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

  const slide      = SLIDES[index];
  const isLast     = index === SLIDES.length - 1;
  const isFirst    = index === 0;

  return (
    <Modal visible={visible} animationType="fade" statusBarTranslucent>
      <SafeAreaView style={styles.container}>

        {/* ── Skip (top right, only on non-last slides) ──────────────── */}
        <View style={styles.topBar}>
          <View style={{ flex: 1 }} />
          {!isLast && (
            <TouchableOpacity onPress={handleClose} hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}>
              <Text style={styles.skipText}>Skip</Text>
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
          {SLIDES.map((_, i) => (
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
            <Text style={styles.navBtnSecondaryText}>Back</Text>
          </TouchableOpacity>

          {/* Next / Get Started */}
          {isLast ? (
            <TouchableOpacity style={[styles.navBtn, styles.navBtnPrimary]} onPress={handleClose}>
              <Text style={styles.navBtnPrimaryText}>{closeLabel}</Text>
              <Ionicons name="checkmark-circle" size={18} color="#fff" />
            </TouchableOpacity>
          ) : (
            <TouchableOpacity style={[styles.navBtn, styles.navBtnPrimary]} onPress={() => goTo(index + 1)}>
              <Text style={styles.navBtnPrimaryText}>Next</Text>
              <Ionicons name="arrow-forward" size={18} color="#fff" />
            </TouchableOpacity>
          )}
        </View>

        {/* ── Slide counter ──────────────────────────────────────────── */}
        <Text style={styles.counter}>{index + 1} / {SLIDES.length}</Text>

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
