import { Ionicons } from '@expo/vector-icons';
import { Tabs, useRouter } from 'expo-router';
import React, { useEffect, useRef } from 'react';
import { Dimensions, Text, TouchableOpacity, View } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withSpring,
  withSequence,
  withTiming,
  Easing,
} from 'react-native-reanimated';

export const unstable_settings = { initialRouteName: 'feed' };

import { ErrorBoundary } from '../../components/error-boundary';
import { ThemeProvider, useTheme } from '../../context/ThemeContext';
import { LanguageProvider, useLanguage } from '../../context/LanguageContext';
import { DrawerProvider } from '../../components/drawer-menu';

type IoniconName = React.ComponentProps<typeof Ionicons>['name'];

const SCREEN_WIDTH = Dimensions.get('window').width;
const TAB_BAR_H  = 68;
const TAB_BAR_MX = 12;   // marginHorizontal
const N_TABS     = 3;
const PILL_PAD   = 5;    // gap between indicator pill and tab bar edges

const VISIBLE_TABS: Array<{
  name: string;
  focusedIcon: IoniconName;
  unfocusedIcon: IoniconName;
  labelKey: string;
}> = [
  { name: 'feed',   focusedIcon: 'star',      unfocusedIcon: 'star-outline',      labelKey: 'tab.feed'   },
  { name: 'sports', focusedIcon: 'apps',       unfocusedIcon: 'apps-outline',       labelKey: 'tab.sports' },
  { name: 'active', focusedIcon: 'bar-chart',  unfocusedIcon: 'bar-chart-outline',  labelKey: 'tab.active' },
];

// ── Per-icon bounce animation (scale + float) ──────────────────────────────

function TabIcon({ name, color, focused }: { name: IoniconName; color: string; focused: boolean }) {
  const scale     = useSharedValue(1);
  const translateY = useSharedValue(0);
  const mounted   = useRef(false);

  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return; }
    if (focused) {
      scale.value      = withSequence(withTiming(1.15, { duration: 0 }), withSpring(1, { damping: 14, stiffness: 200 }));
      translateY.value = withSequence(withTiming(-3,  { duration: 0 }), withSpring(0, { damping: 14, stiffness: 200 }));
    }
  }, [focused]);

  const animStyle = useAnimatedStyle(() => ({
    transform: [{ scale: scale.value }, { translateY: translateY.value }],
  }));

  return (
    <Animated.View style={animStyle}>
      <Ionicons name={name} size={24} color={color} />
    </Animated.View>
  );
}

// ── Custom tab bar with sliding pill indicator ─────────────────────────────

const HIDDEN_ON_ROUTES = new Set(['favorites', 'settings']);

function CustomTabBar({ state, navigation, insets }: any) {
  const { colors } = useTheme();
  const { t }      = useLanguage();

  // Hide the tab bar entirely on Alert Settings and Settings screens
  const currentRouteName = state.routes[state.index]?.name ?? '';
  if (HIDDEN_ON_ROUTES.has(currentRouteName)) return null;

  const tabWidth = (SCREEN_WIDTH - TAB_BAR_MX * 2) / N_TABS;

  // Map route name → visible-tab index; fall back to last valid one
  const activeNameIdx = VISIBLE_TABS.findIndex(td => td.name === state.routes[state.index]?.name);
  const lastValidRef  = useRef(0);
  if (activeNameIdx >= 0) lastValidRef.current = activeNameIdx;
  const indicatorIdx  = lastValidRef.current;

  const indicatorX = useSharedValue(indicatorIdx * tabWidth);
  useEffect(() => {
    indicatorX.value = withTiming(indicatorIdx * tabWidth, { duration: 200, easing: Easing.out(Easing.quad) });
  }, [indicatorIdx]);

  const indicatorStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: indicatorX.value }],
  }));

  return (
    <View style={{
      flexDirection: 'row',
      backgroundColor: colors.card,
      borderRadius: 28,
      marginHorizontal: TAB_BAR_MX,
      marginBottom: insets.bottom + 10,
      height: TAB_BAR_H,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.13,
      shadowRadius: 14,
      elevation: 10,
    }}>
      {/* Sliding background pill */}
      <Animated.View style={[{
        position: 'absolute',
        top: PILL_PAD,
        bottom: PILL_PAD,
        width: tabWidth - PILL_PAD * 2,
        left: PILL_PAD,
        backgroundColor: colors.bg,
        borderRadius: 22,
      }, indicatorStyle]} />

      {VISIBLE_TABS.map((tab, index) => {
        const route   = state.routes.find((r: any) => r.name === tab.name);
        if (!route) return null;
        const focused = index === indicatorIdx;
        const color   = focused ? colors.orange : colors.textMuted;

        return (
          <TouchableOpacity
            key={tab.name}
            style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}
            onPress={() => {
const event = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
              if (!focused && !event.defaultPrevented) navigation.navigate(tab.name);
            }}
            activeOpacity={1}
          >
            <TabIcon name={focused ? tab.focusedIcon : tab.unfocusedIcon} color={color} focused={focused} />
            <Text style={{ fontSize: 11, fontWeight: '600', color, marginTop: 2 }}>
              {t(tab.labelKey)}
            </Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

// ── Layout ─────────────────────────────────────────────────────────────────

function TabLayoutInner() {
  const router = useRouter();

  return (
    <DrawerProvider
      onOpenSettings={() => router.navigate('/settings' as any)}
      onOpenFavorites={() => router.navigate('/favorites' as any)}
      onNavigateFeed={() => router.navigate('/feed' as any)}
    >
      <Tabs
        initialRouteName="feed"
        tabBar={props => <CustomTabBar {...props} />}
        screenOptions={{ headerShown: false, animation: 'none' }}
      >
        <Tabs.Screen name="feed"   />
        <Tabs.Screen name="sports" />
        <Tabs.Screen name="active" />

        {/* Hidden tabs — accessible via drawer / navigation */}
        <Tabs.Screen name="index"     options={{ href: null }} />
        <Tabs.Screen name="favorites" options={{ href: null }} />
        <Tabs.Screen name="settings"  options={{ href: null }} />
      </Tabs>
    </DrawerProvider>
  );
}

export default function TabLayout() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <LanguageProvider>
          <TabLayoutInner />
        </LanguageProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}
