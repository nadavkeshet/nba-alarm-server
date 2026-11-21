import React from 'react';
import { Pressable, Text, ScrollView, StyleSheet } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withTiming,
  withSpring,
} from 'react-native-reanimated';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../context/ThemeContext';
import { useLanguage } from '../context/LanguageContext';

export type SportFilter = 'all' | 'football' | 'basketball';

interface Props {
  value: SportFilter;
  onChange: (v: SportFilter) => void;
}

type IoniconName = React.ComponentProps<typeof Ionicons>['name'];

const OPTIONS: Array<{ value: SportFilter; icon?: IoniconName; i18nKey: string }> = [
  { value: 'all',        icon: undefined,            i18nKey: 'sport.all' },
  { value: 'football',   icon: 'football-outline',   i18nKey: 'sport.football' },
  { value: 'basketball', icon: 'basketball-outline', i18nKey: 'sport.basketball' },
];

const PillButton = React.memo(function PillButton({ opt, selected, onChange }: {
  opt: (typeof OPTIONS)[number];
  selected: boolean;
  onChange: (v: SportFilter) => void;
}) {
  const { colors } = useTheme();
  const { t }      = useLanguage();
  const scale      = useSharedValue(1);

  const animStyle = useAnimatedStyle(() => ({
    transform: [{ scale: scale.value }],
  }));

  const iconColor = selected ? colors.bg : colors.textMuted;

  return (
    <Pressable
      onPressIn={() => { scale.value = withTiming(0.93, { duration: 70 }); }}
      onPressOut={() => { scale.value = withSpring(1, { damping: 14, stiffness: 220 }); }}
      onPress={() => onChange(opt.value)}
    >
      <Animated.View style={[
        styles.pill,
        {
          backgroundColor: selected ? colors.textPrimary : colors.card,
          borderColor: selected ? colors.textPrimary : colors.border,
        },
        animStyle,
      ]}>
        {opt.icon ? <Ionicons name={opt.icon} size={14} color={iconColor} /> : null}
        <Text style={[styles.label, { color: iconColor }]}>
          {t(opt.i18nKey)}
        </Text>
      </Animated.View>
    </Pressable>
  );
});

export function SportFilterPills({ value, onChange }: Props) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={styles.scroll}
      contentContainerStyle={styles.container}
    >
      {OPTIONS.map(opt => (
        <PillButton
          key={opt.value}
          opt={opt}
          selected={opt.value === value}
          onChange={onChange}
        />
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: {
    height: 52,
    flexGrow: 0,
    flexShrink: 0,
    backgroundColor: 'transparent',
  },
  container: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    gap: 8,
    flexDirection: 'row',
    alignItems: 'center',
  },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    borderRadius: 20,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  label: {
    fontSize: 13,
    fontWeight: '600',
  },
});
