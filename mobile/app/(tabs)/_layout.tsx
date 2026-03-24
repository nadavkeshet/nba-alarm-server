import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router';
import React, { useEffect, useState } from 'react';

import { HapticTab } from '../../components/haptic-tab';
import { IconSymbol } from '../../components/ui/icon-symbol';
import { WelcomeGuide } from '../../components/welcome-guide';
import { Colors } from '../../constants/theme';
import { useColorScheme } from '../../hooks/use-color-scheme';

const ONBOARDED_KEY = 'nba_alarm_onboarded';

export default function TabLayout() {
  const colorScheme = useColorScheme();
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
          tabBarInactiveTintColor: '#6b8aad',
          tabBarStyle: {
            backgroundColor: '#0B1623',
            borderTopColor: '#1a3451',
            borderTopWidth: 1,
          },
          headerShown: false,
          tabBarButton: HapticTab,
          animation: 'none',
        }}>
        <Tabs.Screen
          name="index"
          options={{
            title: 'Home',
            tabBarIcon: ({ color }) => <IconSymbol size={28} name="house.fill" color={color} />,
          }}
        />
        <Tabs.Screen
          name="active"
          options={{
            title: 'Active',
            tabBarIcon: ({ color }) => <Ionicons name="pulse" size={26} color={color} />,
          }}
        />
        <Tabs.Screen
          name="settings"
          options={{
            title: 'Settings',
            tabBarIcon: ({ color }) => <Ionicons name="settings-sharp" size={26} color={color} />,
          }}
        />
      </Tabs>

      <WelcomeGuide
        visible={showGuide}
        onClose={handleGuideClose}
        closeLabel="Get Started"
      />
    </>
  );
}
