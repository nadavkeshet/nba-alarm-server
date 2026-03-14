import React, { useEffect, useRef } from 'react';
import { Animated, StyleSheet, View } from 'react-native';

// ─── Shared pulsing block ─────────────────────────────────────────────────────

function SkeletonBlock({ style }: { style?: object }) {
  const opacity = useRef(new Animated.Value(0.3)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.75, duration: 750, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.3,  duration: 750, useNativeDriver: true }),
      ]),
    ).start();
  }, []);

  return <Animated.View style={[styles.block, style, { opacity }]} />;
}

// ─── Game card skeleton (matches GameCard dimensions) ─────────────────────────

export function SkeletonGameCard() {
  return (
    <View style={styles.card}>
      {/* Header row */}
      <View style={styles.headerRow}>
        <SkeletonBlock style={styles.badge} />
        <SkeletonBlock style={styles.time} />
      </View>

      {/* Teams row */}
      <View style={styles.teamsRow}>
        <View style={styles.teamCol}>
          <SkeletonBlock style={styles.logo} />
          <SkeletonBlock style={styles.abbr} />
        </View>
        <SkeletonBlock style={styles.vs} />
        <View style={styles.teamCol}>
          <SkeletonBlock style={styles.logo} />
          <SkeletonBlock style={styles.abbr} />
        </View>
      </View>
    </View>
  );
}

// ─── Alarm card skeleton (matches alarmCard in active.tsx) ────────────────────

export function SkeletonAlarmCard() {
  return (
    <View style={styles.alarmCard}>
      <SkeletonBlock style={styles.alarmPill} />
      <SkeletonBlock style={styles.alarmTitle} />
      <SkeletonBlock style={styles.alarmSub} />
    </View>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  block: { backgroundColor: '#30363d', borderRadius: 6 },

  // Game card shell
  card: {
    backgroundColor: '#161b22', borderRadius: 16,
    marginHorizontal: 16, marginBottom: 12,
    padding: 16, borderWidth: 1, borderColor: '#30363d',
  },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 14 },
  badge:     { width: 72, height: 22, borderRadius: 6 },
  time:      { width: 48, height: 22, borderRadius: 6 },

  teamsRow: { flexDirection: 'row', alignItems: 'center' },
  teamCol:  { flex: 1, alignItems: 'center', gap: 10 },
  logo:     { width: 56, height: 56, borderRadius: 28 },
  abbr:     { width: 40, height: 14, borderRadius: 4 },
  vs:       { width: 24, height: 14, borderRadius: 4 },

  // Alarm card shell
  alarmCard: {
    paddingVertical: 12,
    borderTopWidth: 1, borderColor: '#30363d',
    gap: 8,
  },
  alarmPill:  { width: 84, height: 20, borderRadius: 6 },
  alarmTitle: { width: '65%', height: 16, borderRadius: 4 },
  alarmSub:   { width: '45%', height: 12, borderRadius: 4 },
});
