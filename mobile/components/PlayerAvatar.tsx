import React from 'react';
import { View, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

interface Props {
  size?: number;
}

export function PlayerAvatar({ size = 40 }: Props) {
  return (
    <View style={[styles.circle, { width: size, height: size, borderRadius: size / 2 }]}>
      <Ionicons name="person" size={size * 0.55} color="#6b8aad" />
    </View>
  );
}

const styles = StyleSheet.create({
  circle: {
    backgroundColor: '#0d1f35',
    borderWidth: 1,
    borderColor: '#1a3451',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
});
