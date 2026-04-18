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
  // ── Euroleague ────────────────────────────────────────────────────────
  EFE: ['#D73430', '#00A4D2'], MON: ['#C20E1A', '#FFFFFF'],
  LYN: ['#000000', '#FFFFFF'], BAR: ['#004D98', '#A50044'],
  BAY: ['#0066B2', '#DC052D'], BAS: ['#CF152D', '#0B2240'],
  CZV: ['#EB1926', '#FFFFFF'], DUB: ['#000000', '#D4AF37'],
  FEN: ['#00417F', '#FFED00'], MLN: ['#E2231A', '#FFFFFF'],
  OLY: ['#D0061F', '#FFFFFF'], PAN: ['#007841', '#FFFFFF'],
  PAR: ['#000000', '#FF0000'], PAT: ['#000000', '#FFFFFF'],
  RMA: ['#00529F', '#FFFFFF'], VAL: ['#FF6C0C', '#5B6670'],
  VIR: ['#000000', '#FFFFFF'], ZAL: ['#146734', '#FFFFFF'],
  HTA: ['#E3001B', '#FFFFFF'], MTA: ['#2371B5', '#FFF100'],
  // ── EuroCup ──────────────────────────────────────────────────────────
  BOU: ['#D52127', '#FFFFFF'], BES: ['#000000', '#FFFFFF'],
  BHK: ['#FFCC00', '#000000'], CLJ: ['#E2231A', '#000000'],
  BUD: ['#0033A0', '#FFFFFF'], CED: ['#F5811F', '#000000'],
  MAN: ['#E2231A', '#000000'], RVN: ['#7E1A1F', '#FFFFFF'],
  TUR: ['#003DA5', '#FFD100'], ULM: ['#F47B20', '#000000'],
  // ── FIBA BCL ─────────────────────────────────────────────────────────
  NYM: ['#FFD100', '#000000'], AEK: ['#F1C40F', '#000000'],
  PRO: ['#FFD500', '#000000'], ALB: ['#005CA9', '#FFFFFF'],
  CHO: ['#E2231A', '#FFFFFF'], GLT: ['#A90432', '#FFB81C'],
  GRA: ['#005CA9', '#FFD100'], JOV: ['#1B7C3E', '#000000'],
  TEN: ['#000000', '#FFFFFF'], TOF: ['#0072CE', '#FFFFFF'],
  UNI: ['#006F45', '#5BC2E7'], HEI: ['#E2231A', '#000000'],
  WUR: ['#E2231A', '#FFFFFF'],
  // ── Winner League (Israel) ────────────────────────────────────────────
  JER: ['#E31837', '#000000'], HOL: ['#4B2163', '#FFC72C'],
  HER: ['#0033A0', '#FFFFFF'], NES: ['#F37021', '#002B5C'],
  RMG: ['#2371B5', '#FFFFFF'], GAL: ['#E2001A', '#FFFFFF'],
  ATA: ['#0055A5', '#FFFFFF'], BSV: ['#D21245', '#FFFFFF'],
  NET: ['#FFD700', '#000000'], EMK: ['#E31837', '#FFFFFF'],
  RSH: ['#F37021', '#FFFFFF'], RNA: ['#0055A5', '#FFFFFF'],
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
