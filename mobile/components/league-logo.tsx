import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { LeagueCatalog } from '../services/league-catalog';
import { useTheme } from '../context/ThemeContext';

interface Props {
  leagueId: string;
  size?: number;
}

export function LeagueLogo({ leagueId, size = 28 }: Props) {
  const { colors } = useTheme();
  const info = LeagueCatalog.info(leagueId);

  const label = info.label
    .split(/[\s']+/)
    .map(w => w[0])
    .join('')
    .slice(0, 3)
    .toUpperCase();

  const fontSize = Math.round(size * 0.3);

  return (
    <View
      style={[
        styles.circle,
        {
          width: size,
          height: size,
          borderRadius: size / 2,
          backgroundColor: info.color,
        },
      ]}
    >
      <Text style={[styles.label, { fontSize, color: '#fff' }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  circle: {
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  label: {
    fontWeight: '900',
    letterSpacing: 0.3,
  },
});
