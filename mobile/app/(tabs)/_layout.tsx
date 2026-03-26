import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router';
import React, { useEffect, useState } from 'react';

import { HapticTab } from '../../components/haptic-tab';
import { IconSymbol } from '../../components/ui/icon-symbol';
import { WelcomeGuide } from '../../components/welcome-guide';
import { ErrorBoundary } from '../../components/error-boundary';
import { ThemeProvider, useTheme } from '../../context/ThemeContext';
import { LanguageProvider, useLanguage } from '../../context/LanguageContext';

const ONBOARDED_KEY = 'nba_alarm_onboarded';

function TabLayoutInner() {
  const { colors } = useTheme();
  const { t } = useLanguage();
  const [showGuide, setShowGuide] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(ONBOARDED_KEY).then(val => {
      if (!val) setShowGuide(true);
    });
  }, []);

  const handleGuideClose = async () => {
    await AsyncStorage.setItem(ONBOARDED_KEY, 'true');
    setShowGuide(false);
  };

  return (
    <>
      <Tabs
        screenOptions={{
          tabBarActiveTintColor: '#FF6B00',
          tabBarInactiveTintColor: colors.textMuted,
          tabBarStyle: {
            backgroundColor: colors.bg,
            borderTopColor: colors.border,
            borderTopWidth: 1,
          },
          headerShown: false,
          tabBarButton: HapticTab,
          animation: 'none',
        }}>
        <Tabs.Screen
          name="index"
          options={{
            title: t('tab.home'),
            tabBarIcon: ({ color }) => <IconSymbol size={28} name="house.fill" color={color} />,
          }}
        />
        <Tabs.Screen
          name="active"
          options={{
            title: t('tab.active'),
            tabBarIcon: ({ color }) => <Ionicons name="pulse" size={26} color={color} />,
          }}
        />
        <Tabs.Screen
          name="settings"
          options={{
            title: t('tab.settings'),
            tabBarIcon: ({ color }) => <Ionicons name="settings-sharp" size={26} color={color} />,
          }}
        />
      </Tabs>

      <WelcomeGuide
        visible={showGuide}
        onClose={handleGuideClose}
        closeLabel={t('guide.getStarted')}
      />
    </>
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
