import React, { useEffect, useRef } from 'react';
import { Animated, StyleSheet, Text, TouchableOpacity } from 'react-native';

interface Props {
  value: boolean;
  onValueChange: (v: boolean) => void;
  disabled?: boolean;
}

const TRACK_W  = 56;
const TRACK_H  = 30;
const THUMB_SZ = 24;
const PADDING  = 3;
const TRAVEL   = TRACK_W - THUMB_SZ - PADDING * 2;  // 26

export function BasketballToggle({ value, onValueChange, disabled }: Props) {
  const anim = useRef(new Animated.Value(value ? 1 : 0)).current;

  useEffect(() => {
    Animated.spring(anim, {
      toValue: value ? 1 : 0,
      useNativeDriver: false,
      bounciness: 10,
      speed: 14,
    }).start();
  }, [value, anim]);

  const translateX = anim.interpolate({
    inputRange:  [0, 1],
    outputRange: [PADDING, PADDING + TRAVEL],
  });

  const trackBg = anim.interpolate({
    inputRange:  [0, 1],
    outputRange: ['#162035', 'rgba(255,107,0,0.28)'],
  });

  const trackBorder = anim.interpolate({
    inputRange:  [0, 1],
    outputRange: ['#1a3451', 'rgba(255,107,0,0.6)'],
  });

  const ballOpacity = anim.interpolate({
    inputRange:  [0, 1],
    outputRange: [0.45, 1],
  });

  const ballScale = anim.interpolate({
    inputRange:  [0, 1],
    outputRange: [0.85, 1],
  });

  return (
    <TouchableOpacity
      activeOpacity={0.8}
      onPress={() => !disabled && onValueChange(!value)}
      accessibilityRole="switch"
      accessibilityState={{ checked: value, disabled }}
    >
      <Animated.View style={[
        styles.track,
        { backgroundColor: trackBg, borderColor: trackBorder },
      ]}>
        <Animated.View style={[
          styles.thumb,
          { transform: [{ translateX }, { scale: ballScale }], opacity: ballOpacity },
        ]}>
          <Text style={styles.ball}>🏀</Text>
        </Animated.View>
      </Animated.View>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  track: {
    width: TRACK_W, height: TRACK_H,
    borderRadius: TRACK_H / 2,
    borderWidth: 1.5,
    justifyContent: 'center',
  },
  thumb: {
    position: 'absolute',
    width: THUMB_SZ, height: THUMB_SZ,
    borderRadius: THUMB_SZ / 2,
    alignItems: 'center', justifyContent: 'center',
  },
  ball: {
    fontSize: 17,
    lineHeight: 20,
  },
});
