import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import {
  Animated,
  Dimensions,
  Modal,
  Text,
  TouchableOpacity,
  TouchableWithoutFeedback,
  View,
  StyleSheet,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../context/ThemeContext';
import { useLanguage } from '../context/LanguageContext';
import { BorderWidth, FontSize, FontWeight, Spacing } from '../constants/tokens';

const DRAWER_WIDTH = 280;

// ── DrawerController context ──────────────────────────────────────────────────

interface DrawerContextValue {
  isOpen: boolean;
  open: () => void;
  close: () => void;
}

const DrawerContext = createContext<DrawerContextValue>({
  isOpen: false,
  open: () => {},
  close: () => {},
});

export function useDrawer(): DrawerContextValue {
  return useContext(DrawerContext);
}

// ── DrawerProvider ────────────────────────────────────────────────────────────

interface ProviderProps {
  children: React.ReactNode;
  onOpenSettings?: () => void;
  onOpenFavorites?: () => void;
  onNavigateFeed?: () => void;
}

export function DrawerProvider({
  children,
  onOpenSettings,
  onOpenFavorites,
  onNavigateFeed,
}: ProviderProps) {
  const [isOpen, setIsOpen] = useState(false);

  const open  = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);

  return (
    <DrawerContext.Provider value={{ isOpen, open, close }}>
      {children}
      <DrawerOverlay
        isOpen={isOpen}
        onClose={close}
        onOpenSettings={() => { close(); onOpenSettings?.(); }}
        onOpenFavorites={() => { close(); onOpenFavorites?.(); }}
        onNavigateFeed={() => { close(); onNavigateFeed?.(); }}
      />
    </DrawerContext.Provider>
  );
}

// ── Drawer overlay ────────────────────────────────────────────────────────────

interface OverlayProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenSettings: () => void;
  onOpenFavorites: () => void;
  onNavigateFeed: () => void;
}

function DrawerOverlay({ isOpen, onClose, onOpenSettings, onOpenFavorites, onNavigateFeed }: OverlayProps) {
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();

  const translateX = useRef(new Animated.Value(-DRAWER_WIDTH)).current;
  const opacity    = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (isOpen) {
      Animated.parallel([
        Animated.spring(translateX, {
          toValue: 0,
          useNativeDriver: true,
          damping: 20,
          stiffness: 200,
        }),
        Animated.timing(opacity, {
          toValue: 0.55,
          duration: 200,
          useNativeDriver: true,
        }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(translateX, {
          toValue: -DRAWER_WIDTH,
          duration: 220,
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 0,
          duration: 200,
          useNativeDriver: true,
        }),
      ]).start();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const ITEMS: Array<{ icon: keyof typeof Ionicons.glyphMap; label: string; onPress: () => void }> = [
    { icon: 'home-outline',          label: t('drawer.home'),          onPress: onNavigateFeed  },
    { icon: 'notifications-outline', label: t('drawer.alertSettings'), onPress: onOpenFavorites },
    { icon: 'settings-outline',      label: t('drawer.settings'),      onPress: onOpenSettings  },
  ];

  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
      {/* Scrim */}
      <TouchableWithoutFeedback onPress={onClose}>
        <Animated.View style={[StyleSheet.absoluteFill, styles.scrim, { opacity }]} />
      </TouchableWithoutFeedback>

      {/* Drawer panel */}
      <Animated.View
        style={[
          styles.panel,
          {
            width: DRAWER_WIDTH,
            backgroundColor: colors.card,
            borderRightColor: colors.border,
            transform: [{ translateX }],
          },
        ]}
      >
        <View style={styles.safeArea}>
          {/* App name header */}
          <View style={[styles.header, { borderBottomColor: colors.border, paddingTop: Math.max(insets.top, 12) }]}>
            <Text style={[styles.appName, { color: colors.textPrimary }]}>Clutch Alarm</Text>
          </View>

          {/* Menu items */}
          {ITEMS.map(item => (
            <TouchableOpacity key={item.label} style={styles.menuItem} onPress={item.onPress} activeOpacity={0.7}>
              <Ionicons name={item.icon} size={22} color={colors.textMuted} />
              <Text style={[styles.menuLabel, { color: colors.textPrimary }]}>{item.label}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </Animated.View>
    </View>
  );
}

// ── Hamburger button (used in screen headers) ─────────────────────────────────

export function HamburgerButton() {
  const { open } = useDrawer();
  const { colors } = useTheme();
  return (
    <TouchableOpacity onPress={open} style={styles.hamburger} hitSlop={8}>
      <Ionicons name="menu-outline" size={24} color={colors.textPrimary} />
    </TouchableOpacity>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  scrim: {
    backgroundColor: '#000',
  },
  panel: {
    position: 'absolute',
    top: 0,
    left: 0,
    bottom: 0,
    borderRightWidth: BorderWidth.thin,
    shadowColor: '#000',
    shadowOffset: { width: 4, height: 0 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 16,
  },
  safeArea: {
    flex: 1,
  },
  header: {
    paddingHorizontal: Spacing.xl,
    paddingVertical: Spacing.lg,
    borderBottomWidth: StyleSheet.hairlineWidth,
    marginBottom: Spacing.sm,
  },
  appName: {
    fontSize: FontSize.lead,
    fontWeight: FontWeight.heavy,
    letterSpacing: -0.3,
  },
  menuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing[14],
    paddingHorizontal: Spacing.xl,
    paddingVertical: Spacing[14],
  },
  menuLabel: {
    fontSize: FontSize.subLg,
    fontWeight: FontWeight.semibold,
  },
  hamburger: {
    marginLeft: Spacing.lg,
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.8,
    paddingHorizontal: Spacing.xl,
    paddingTop: Spacing.sm,
    paddingBottom: 4,
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    marginHorizontal: Spacing.xl,
    marginVertical: Spacing.sm,
  },
});
