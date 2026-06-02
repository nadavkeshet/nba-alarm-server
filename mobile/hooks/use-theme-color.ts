import { useTheme, type AppColors } from '@/context/ThemeContext';

// Maps the old Expo scaffold color key names to ThemeContext keys.
const KEY_MAP: Partial<Record<string, keyof AppColors>> = {
  text:            'textPrimary',
  background:      'bg',
  tint:            'orange',
  icon:            'textMuted',
  tabIconDefault:  'textMuted',
  tabIconSelected: 'orange',
};

export function useThemeColor(
  props: { light?: string; dark?: string },
  colorName: string,
): string {
  const { colors, mode } = useTheme();
  const fromProps = props[mode as 'light' | 'dark'];
  if (fromProps) return fromProps;
  const key = KEY_MAP[colorName];
  return key ? colors[key] : colors.textPrimary;
}
