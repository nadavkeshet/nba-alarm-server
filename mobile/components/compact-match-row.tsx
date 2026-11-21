import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import { useTheme } from '../context/ThemeContext';

export interface CompactMatchRowProps {
  // Status column
  statusLabel: string;       // "78'" | "HT" | "FT" | "21:00"
  isLive: boolean;

  // Teams
  homeName: string;
  homeCrest?: string | null;
  homeScore?: number | null;
  homePensScore?: number | null;
  awayName: string;
  awayCrest?: string | null;
  awayScore?: number | null;
  awayPensScore?: number | null;

  // Interactions
  hasAlarm: boolean;
  onBellTap: () => void;
  onRowTap: () => void;
  hideBell?: boolean;
  isFinished?: boolean;
  watchScore?: number | null;

  // Optional: competition label shown above team row
  competitionLabel?: string;
}

export const CompactMatchRow = React.memo(function CompactMatchRow({
  statusLabel,
  isLive,
  homeName,
  homeCrest,
  homeScore,
  homePensScore,
  awayName,
  awayCrest,
  awayScore,
  awayPensScore,
  hasAlarm,
  onBellTap,
  onRowTap,
  hideBell = false,
  competitionLabel,
  isFinished = false,
  watchScore,
}: CompactMatchRowProps) {
  const { colors, noSpoiler } = useTheme();
  const showScore = homeScore != null && awayScore != null;
  const showPens = homePensScore != null && awayPensScore != null;
  const maskScore = noSpoiler && isFinished;
  const wsColor = watchScore == null ? colors.textMuted
    : watchScore >= 8 ? '#22c55e'
    : watchScore >= 5 ? '#FF6B00'
    : '#6b8aad';

  return (
    <TouchableOpacity
      style={[styles.row, { borderBottomColor: colors.borderMuted }]}
      onPress={onRowTap}
      activeOpacity={0.7}
    >
      {/* Status column */}
      <View style={styles.statusCol}>
        {isLive && <View style={[styles.liveDot, { backgroundColor: colors.liveGreen }]} />}
        <Text
          style={[
            styles.statusText,
            { color: isLive ? colors.liveGreen : colors.textMuted },
          ]}
          numberOfLines={1}
        >
          {statusLabel}
        </Text>
        {isFinished && watchScore != null && (
          <View style={[styles.wsBadge, { backgroundColor: `${wsColor}22`, borderColor: `${wsColor}99` }]}>
            <Text style={[styles.wsText, { color: wsColor }]}>{watchScore.toFixed(1)}</Text>
          </View>
        )}
      </View>

      {/* Match body */}
      <View style={styles.body}>
        {/* Home row */}
        <View style={styles.teamRow}>
          <TeamCrest uri={homeCrest} name={homeName} size={20} />
          <Text style={[styles.teamName, { color: colors.textPrimary }]} numberOfLines={1}>
            {homeName}
          </Text>
          <View style={styles.scoreBlock}>
            <Text style={[styles.score, { color: showScore ? colors.textPrimary : colors.textMuted }]}>
              {showScore ? (maskScore ? '–' : homeScore) : ''}
            </Text>
            {showPens && !maskScore && (
              <Text style={[styles.pensScore, { color: colors.textMuted }]}>
                ({homePensScore})
              </Text>
            )}
          </View>
        </View>

        {/* Away row */}
        <View style={styles.teamRow}>
          <TeamCrest uri={awayCrest} name={awayName} size={20} />
          <Text style={[styles.teamName, { color: colors.textPrimary }]} numberOfLines={1}>
            {awayName}
          </Text>
          <View style={styles.scoreBlock}>
            <Text style={[styles.score, { color: showScore ? colors.textPrimary : colors.textMuted }]}>
              {showScore ? (maskScore ? '–' : awayScore) : ''}
            </Text>
            {showPens && !maskScore && (
              <Text style={[styles.pensScore, { color: colors.textMuted }]}>
                ({awayPensScore})
              </Text>
            )}
          </View>
        </View>
      </View>

      {/* Bell */}
      {!hideBell && (
        <TouchableOpacity onPress={onBellTap} style={styles.bell} hitSlop={8}>
          <Ionicons
            name={hasAlarm ? 'notifications' : 'notifications-outline'}
            size={18}
            color={hasAlarm ? colors.orange : colors.textMuted}
          />
        </TouchableOpacity>
      )}
    </TouchableOpacity>
  );
});

function TeamCrest({ uri, name, size }: { uri?: string | null; name: string; size: number }) {
  const { colors } = useTheme();
  if (uri) {
    return (
      <Image
        source={{ uri }}
        style={{ width: size, height: size, borderRadius: size / 2, marginRight: 6 }}
        contentFit="contain"
      />
    );
  }
  // Fallback: colored initial circle
  const initials = (name ?? '')
    .split(' ')
    .map(w => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
  return (
    <View
      style={[
        {
          width: size, height: size, borderRadius: size / 2,
          backgroundColor: colors.border,
          alignItems: 'center', justifyContent: 'center',
          marginRight: 6,
        },
      ]}
    >
      <Text style={{ color: colors.textMuted, fontSize: size * 0.38, fontWeight: '700' }}>
        {initials}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    minHeight: 56,
  },

  statusCol: {
    width: 48,
    alignItems: 'center',
    gap: 3,
  },
  liveDot: {
    width: 6, height: 6, borderRadius: 3,
  },
  statusText: {
    fontSize: 10,
    fontWeight: '600',
    textAlign: 'center',
  },

  body: {
    flex: 1,
    gap: 4,
    marginHorizontal: 8,
  },
  teamRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  teamName: {
    flex: 1,
    fontSize: 13,
    fontWeight: '500',
  },
  scoreBlock: {
    alignItems: 'flex-end',
  },
  score: {
    fontSize: 13,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
    minWidth: 20,
    textAlign: 'right',
  },
  pensScore: {
    fontSize: 10,
    fontWeight: '500',
    fontVariant: ['tabular-nums'],
    textAlign: 'right',
  },

  bell: {
    padding: 4,
  },
  wsBadge: {
    paddingHorizontal: 4,
    paddingVertical: 2,
    borderRadius: 6,
    borderWidth: 1,
    alignItems: 'center',
  },
  wsText: {
    fontSize: 10,
    fontWeight: '800',
    fontVariant: ['tabular-nums'],
  },
});
