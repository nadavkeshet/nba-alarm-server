import React, { useEffect, useRef } from 'react';
import { Animated, Easing, Modal, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

interface AlarmOverlayProps {
  visible: boolean;
  title: string;
  body: string;
  onDismiss: () => void;
}

export function AlarmOverlay({ visible, title, body, onDismiss }: AlarmOverlayProps) {
  const pulse = useRef(new Animated.Value(1)).current;
  const opacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      // Fade in
      Animated.timing(opacity, { toValue: 1, duration: 300, useNativeDriver: true }).start();
      // Start pulsing ring
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulse, { toValue: 1.35, duration: 900, easing: Easing.out(Easing.ease), useNativeDriver: true }),
          Animated.timing(pulse, { toValue: 1,    duration: 700, easing: Easing.in(Easing.ease),  useNativeDriver: true }),
        ])
      ).start();
    } else {
      pulse.stopAnimation();
      pulse.setValue(1);
      opacity.setValue(0);
    }
  }, [visible]);

  return (
    <Modal visible={visible} transparent animationType="none" statusBarTranslucent>
      <Animated.View style={[styles.backdrop, { opacity }]}>
        <View style={styles.content}>
          {/* Pulsating ring */}
          <View style={styles.ringContainer}>
            <Animated.View style={[styles.ringOuter, { transform: [{ scale: pulse }] }]} />
            <View style={styles.ringInner}>
              <Text style={styles.alarmIcon}>🚨</Text>
            </View>
          </View>

          <Text style={styles.title}>{title}</Text>
          <Text style={styles.body}>{body}</Text>

          <TouchableOpacity style={styles.dismissBtn} onPress={onDismiss} activeOpacity={0.8}>
            <Text style={styles.dismissText}>DISMISS</Text>
          </TouchableOpacity>
        </View>
      </Animated.View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.92)',
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 32,
  },
  content: {
    width: '100%',
    alignItems: 'center',
  },

  ringContainer: {
    width: 140, height: 140,
    justifyContent: 'center', alignItems: 'center',
    marginBottom: 32,
  },
  ringOuter: {
    position: 'absolute',
    width: 140, height: 140, borderRadius: 70,
    backgroundColor: 'rgba(201,8,42,0.25)',
    borderWidth: 2, borderColor: 'rgba(201,8,42,0.5)',
  },
  ringInner: {
    width: 90, height: 90, borderRadius: 45,
    backgroundColor: '#c9082a',
    justifyContent: 'center', alignItems: 'center',
  },
  alarmIcon: { fontSize: 36 },

  title: {
    fontSize: 26, fontWeight: '900', color: '#e6edf3',
    textAlign: 'center', letterSpacing: 0.5, marginBottom: 12,
  },
  body: {
    fontSize: 16, color: '#8b949e', textAlign: 'center',
    lineHeight: 24, marginBottom: 48,
  },

  dismissBtn: {
    backgroundColor: '#c9082a',
    paddingVertical: 18, paddingHorizontal: 56,
    borderRadius: 18,
  },
  dismissText: {
    color: '#fff', fontWeight: '900', fontSize: 18, letterSpacing: 2,
  },
});
