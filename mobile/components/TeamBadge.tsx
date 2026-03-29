import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

const TEAM_COLORS: Record<string, [string, string]> = {
  ATL: ['#C8102E', '#FDB927'], BKN: ['#002A60', '#CD1041'],
  BOS: ['#007A33', '#BA9653'], CHA: ['#1D1160', '#00788C'],
  CHI: ['#CE1141', '#E03A3E'], CLE: ['#860038', '#FDBB30'],
  DAL: ['#00538C', '#002B5E'], DEN: ['#0E2240', '#FEC524'],
  DET: ['#C8102E', '#1D42BA'], GS:  ['#1D428A', '#FFC72C'],
  GSW: ['#1D428A', '#FFC72C'], HOU: ['#CE1141', '#C4CED4'],
  IND: ['#002D62', '#FDBB30'], LAC: ['#C8102E', '#1D428A'],
  LAL: ['#552583', '#FDB927'], MEM: ['#5D76A9', '#12173F'],
  MIA: ['#98002E', '#F9A01B'], MIL: ['#00471B', '#EEE1C6'],
  MIN: ['#0C2340', '#236192'], NO:  ['#0C2340', '#C8102E'],
  NOP: ['#0C2340', '#C8102E'], NY:  ['#006BB6', '#F58426'],
  NYK: ['#006BB6', '#F58426'], OKC: ['#007AC1', '#EF3B24'],
  ORL: ['#0077C0', '#C4CED4'], PHI: ['#006BB6', '#ED174C'],
  PHX: ['#1D1160', '#E56020'], POR: ['#E03A3E', '#007A33'],
  SA:  ['#C4CED4', '#8A8D8F'], SAS: ['#C4CED4', '#8A8D8F'],
  SAC: ['#5A2D81', '#63727A'], TOR: ['#CE1141', '#A1A1A4'],
  UTAH: ['#002B5C', '#00471B'], UTA: ['#002B5C', '#00471B'],
  WSH: ['#002B5C', '#E31837'], WAS: ['#002B5C', '#E31837'],
};

const DEFAULT_COLORS: [string, string] = ['#1a3451', '#0B1623'];

interface Props {
  abbreviation: string;
  size?: number;
}

export function TeamBadge({ abbreviation, size = 36 }: Props) {
  const abbr = (abbreviation || '?').toUpperCase();
  const colors = TEAM_COLORS[abbr] ?? DEFAULT_COLORS;
  const fontSize = Math.round(size * 0.26);
  const glowSize = Math.round(size * 0.04);

  return (
    <LinearGradient
      colors={[colors[0], colors[1]]}
      start={{ x: 0.3, y: 0 }}
      end={{ x: 0.7, y: 1 }}
      style={[
        styles.circle,
        {
          width: size, height: size, borderRadius: size / 2,
          borderWidth: glowSize,
          shadowColor: colors[0],
          shadowOpacity: 0.4,
          shadowRadius: 4,
          shadowOffset: { width: 0, height: 2 },
          elevation: 4,
        },
      ]}
    >
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
    </LinearGradient>
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
