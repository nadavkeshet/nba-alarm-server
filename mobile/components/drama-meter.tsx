import React, { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { useTheme } from '../context/ThemeContext';
import { useLanguage } from '../context/LanguageContext';
import type { SoccerDramaScore, DramaBand } from '../types/soccer';

interface Props {
  score: SoccerDramaScore;
  expandable?: boolean;
  initialExpanded?: boolean;
}

export function DramaMeter({ score, expandable = false, initialExpanded = false }: Props) {
  const [expanded, setExpanded] = useState(initialExpanded);
  const { colors } = useTheme();
  const { t } = useLanguage();

  const band = resolveBand(score);
  const bandColor = dramaBandColor(band, colors);
  const fillPct = Math.min(1, score.total / 10);

  if (!expanded) {
    return (
      <TouchableOpacity
        style={styles.compact}
        onPress={expandable ? () => setExpanded(true) : undefined}
        activeOpacity={expandable ? 0.7 : 1}
        disabled={!expandable}
      >
        <Text style={[styles.compactLabel, { color: colors.textMuted }]}>
          {t('drama.label')}
        </Text>
        <View style={[styles.compactBar, { backgroundColor: colors.border }]}>
          <View
            style={[
              styles.compactFill,
              { width: `${fillPct * 100}%`, backgroundColor: bandColor },
            ]}
          />
        </View>
        <Text style={[styles.compactNumber, { color: bandColor }]}>
          {score.total.toFixed(1)}
        </Text>
      </TouchableOpacity>
    );
  }

  // Expanded mode
  return (
    <TouchableOpacity
      style={[styles.expanded, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}
      onPress={expandable ? () => setExpanded(false) : undefined}
      activeOpacity={expandable ? 0.85 : 1}
      disabled={!expandable}
    >
      {/* Big number + band chip */}
      <View style={styles.expandedHeader}>
        <Text style={[styles.bigNumber, { color: bandColor }]}>
          {score.total.toFixed(1)}
        </Text>
        <View style={[styles.bandChip, { backgroundColor: bandColor + '22', borderColor: bandColor }]}>
          <Text style={[styles.bandChipText, { color: bandColor }]}>
            {t(`drama.band.${band}`)}
          </Text>
        </View>
      </View>

      {/* Gradient bar */}
      <View style={[styles.expandedBar, { backgroundColor: colors.border }]}>
        <View
          style={[
            styles.expandedFill,
            { width: `${fillPct * 100}%`, backgroundColor: bandColor },
          ]}
        />
      </View>

      {/* Component chips */}
      <View style={styles.chips}>
        <ComponentChip
          label={t('drama.comp.state')}
          value={score.state}
          max={7}
          color={colors.accentBlue}
          colors={colors}
        />
        <ComponentChip
          label={t('drama.comp.momentum')}
          value={score.momentum}
          max={4}
          color={score.momentum >= 0 ? colors.liveGreen : colors.textMuted}
          colors={colors}
        />
        <ComponentChip
          label={t('drama.comp.events')}
          value={score.events}
          max={4}
          color={colors.orange}
          colors={colors}
        />
        <ComponentChip
          label={t('drama.comp.stakes')}
          value={score.stakes}
          max={2}
          color={colors.gold}
          colors={colors}
          isMultiplier
        />
      </View>
    </TouchableOpacity>
  );
}

function ComponentChip({
  label,
  value,
  max,
  color,
  colors,
  isMultiplier = false,
}: {
  label: string;
  value: number;
  max: number;
  color: string;
  colors: ReturnType<typeof useTheme>['colors'];
  isMultiplier?: boolean;
}) {
  return (
    <View style={[styles.chip, { backgroundColor: colors.border }]}>
      <Text style={[styles.chipValue, { color }]}>
        {isMultiplier ? `×${value.toFixed(1)}` : value.toFixed(1)}
      </Text>
      <Text style={[styles.chipLabel, { color: colors.textMuted }]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

export function resolveBand(score: SoccerDramaScore): DramaBand {
  if (score.band === 'mustWatch' || score.band === 'worthLooking' || score.band === 'quiet') {
    return score.band;
  }
  if (score.total >= 8) return 'mustWatch';
  if (score.total >= 5) return 'worthLooking';
  return 'quiet';
}

export function dramaBandColor(
  band: DramaBand,
  colors: { orange: string; accentBlue: string; textMuted: string }
): string {
  switch (band) {
    case 'mustWatch':    return colors.orange;
    case 'worthLooking': return colors.accentBlue;
    case 'quiet':        return colors.textMuted;
  }
}

const styles = StyleSheet.create({
  // Compact
  compact: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  compactLabel: {
    fontSize: 9,
    fontWeight: '700',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  compactBar: {
    flex: 1,
    height: 4,
    borderRadius: 2,
    overflow: 'hidden',
  },
  compactFill: {
    height: '100%',
    borderRadius: 2,
  },
  compactNumber: {
    fontSize: 12,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
    minWidth: 28,
    textAlign: 'right',
  },

  // Expanded
  expanded: {
    borderRadius: 12,
    borderWidth: 1,
    padding: 12,
    gap: 10,
  },
  expandedHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  bigNumber: {
    fontSize: 32,
    fontWeight: '800',
    fontVariant: ['tabular-nums'],
  },
  bandChip: {
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  bandChipText: {
    fontSize: 11,
    fontWeight: '700',
  },
  expandedBar: {
    height: 6,
    borderRadius: 3,
    overflow: 'hidden',
  },
  expandedFill: {
    height: '100%',
    borderRadius: 3,
  },
  chips: {
    flexDirection: 'row',
    gap: 6,
  },
  chip: {
    flex: 1,
    borderRadius: 8,
    padding: 6,
    alignItems: 'center',
    gap: 2,
  },
  chipValue: {
    fontSize: 13,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },
  chipLabel: {
    fontSize: 9,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
});
