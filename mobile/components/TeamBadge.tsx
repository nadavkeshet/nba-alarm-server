import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

const TEAM_COLORS: Record<string, [string, string]> = {
  // Eastern Conference
  ATL: ['#E03A3E', '#26282A'], BOS: ['#007A33', '#FFFFFF'],
  BKN: ['#000000', '#FFFFFF'], CHA: ['#00788C', '#1D1160'],
  CHI: ['#CE1141', '#000000'], CLE: ['#6F263D', '#FFB81C'],
  DET: ['#1D428A', '#C8102E'], IND: ['#002D62', '#FDBB30'],
  MIA: ['#98002E', '#000000'], MIL: ['#00471B', '#EEE1C6'],
  NY:  ['#006BB6', '#F58426'], NYK: ['#006BB6', '#F58426'],
  ORL: ['#0077C0', '#000000'], PHI: ['#006BB6', '#ED174C'],
  TOR: ['#CE1141', '#000000'], WSH: ['#002B5C', '#E31837'],
  WAS: ['#002B5C', '#E31837'],
  // Western Conference
  DAL: ['#0053BC', '#BBC4CA'], DEN: ['#0E2240', '#FEC524'],
  GS:  ['#1D428A', '#FFC72C'], GSW: ['#1D428A', '#FFC72C'],
  HOU: ['#CE1141', '#000000'], LAC: ['#1D428A', '#C8102E'],
  LAL: ['#552583', '#FDB927'], MEM: ['#5D76A9', '#12173F'],
  MIN: ['#0C2340', '#78BE20'], NO:  ['#0C2340', '#C8102E'],
  NOP: ['#0C2340', '#C8102E'], OKC: ['#007AC1', '#EF3B24'],
  PHX: ['#1D1160', '#E56020'], POR: ['#E03A3E', '#000000'],
  SAC: ['#5A2D81', '#000000'], SA:  ['#C4CED4', '#000000'],
  SAS: ['#C4CED4', '#000000'], UTAH: ['#000000', '#FFF215'],
  UTA: ['#000000', '#FFF215'],
};

const DEFAULT_COLORS: [string, string] = ['#1a3451', '#0B1623'];

interface Props {
  abbreviation: string;
  size?: number;
}

// Pure JS gradient simulation: primary color circle with secondary color
// overlay in the bottom-right quadrant, clipped to the circle shape.
export function TeamBadge({ abbreviation, size = 36 }: Props) {
  const abbr = (abbreviation || '?').toUpperCase();
  const [primary, secondary] = TEAM_COLORS[abbr] ?? DEFAULT_COLORS;
  const fontSize = Math.round(size * 0.26);
  const borderWidth = Math.max(1, Math.round(size * 0.04));

  return (
    <View
      style={[
        styles.circle,
        {
          width: size, height: size, borderRadius: size / 2,
          backgroundColor: primary,
          borderWidth,
          shadowColor: primary,
          shadowOpacity: 0.4,
          shadowRadius: 4,
          shadowOffset: { width: 0, height: 2 },
          elevation: 4,
        },
      ]}
    >
      {/* Secondary color overlay — bottom half, simulates gradient */}
      <View
        style={{
          position: 'absolute',
          bottom: 0, left: 0, right: 0,
          height: size * 0.45,
          backgroundColor: secondary,
          opacity: 0.45,
        }}
      />
      <Text
        style={[
          styles.text,
          {
            fontSize,
            textShadowColor: 'rgba(0,0,0,0.5)',
            textShadowOffset: { width: 0, height: 1 },
            textShadowRadius: 2,
          },
        ]}
        numberOfLines={1}
        adjustsFontSizeToFit
      >
        {abbr}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  circle: {
    alignItems: 'center',
    justifyContent: 'center',
    borderColor: 'rgba(255,255,255,0.12)',
    overflow: 'hidden',
  },
  text: {
    color: '#fff',
    fontWeight: '900',
    letterSpacing: 0.5,
  },
});
