import { Ionicons } from '@expo/vector-icons';
import notifee, { AuthorizationStatus } from '@notifee/react-native';
import * as IntentLauncher from 'expo-intent-launcher';
import React, { useEffect, useState } from 'react';
import {
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { signal } from '../utils/telemetry';

// ─── Theme ────────────────────────────────────────────────────────────────────

const C = {
  bg:     '#0B1623',
  card:   '#0d1f35',
  border: '#1a3451',
  text:   '#e6edf3',
  muted:  '#6b8aad',
  orange: '#FF6B00',
  green:  '#3fb950',
  red:    '#c9082a',
};

const APP_PACKAGE = 'com.nadav.nbaalarmapp';

// ─── Step definitions ─────────────────────────────────────────────────────────

interface Step {
  id:         string;
  icon:       React.ComponentProps<typeof Ionicons>['name'];
  title:      string;
  desc:       string;
  btnLabel:   string;
  onPress:    () => Promise<void>;
  androidMin?: number; // only show on this API level+
}

// ─── Component ────────────────────────────────────────────────────────────────

interface Props {
  visible:  boolean;
  onDone:   () => void;
}

export function PermissionsOnboarding({ visible, onDone }: Props) {
  const [done, setDone] = useState<Record<string, boolean>>({});

  // Auto-request notifications on mount (shows system dialog)
  useEffect(() => {
    if (!visible) return;
    notifee.requestPermission().then(settings => {
      const granted = settings.authorizationStatus >= AuthorizationStatus.AUTHORIZED;
      signal('NotificationPermission.Decided', { granted: String(granted) });
      if (granted) setDone(prev => ({ ...prev, notifications: true }));
    }).catch(() => {});
  }, [visible]);

  const markDone = (id: string) =>
    setDone(prev => ({ ...prev, [id]: true }));

  const steps: Step[] = [
    {
      id:       'notifications',
      icon:     'notifications',
      title:    'Notifications',
      desc:     'Required to receive game and player alerts.',
      btnLabel: 'Allow',
      onPress:  async () => {
        const settings = await notifee.requestPermission();
        if (settings.authorizationStatus >= AuthorizationStatus.AUTHORIZED) {
          markDone('notifications');
        }
      },
    },
    {
      id:       'battery',
      icon:     'battery-charging',
      title:    'Unrestricted Battery',
      desc:     'Prevents Android from killing the app in the background. Go to Battery → set to Unrestricted.',
      btnLabel: 'Open Settings',
      onPress:  async () => {
        await IntentLauncher.startActivityAsync(
          'android.settings.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS',
          { data: `package:${APP_PACKAGE}` },
        );
        markDone('battery');
      },
    },
    {
      id:         'fsi',
      icon:       'phone-portrait',
      title:      'Full Screen Alarms',
      desc:       'Allows alarm notifications to appear on your lock screen even when the phone is locked.',
      btnLabel:   'Open Settings',
      androidMin: 34, // Android 14+
      onPress:    async () => {
        await IntentLauncher.startActivityAsync(
          'android.settings.MANAGE_APP_USE_FULL_SCREEN_INTENT',
          { data: `package:${APP_PACKAGE}` },
        );
        markDone('fsi');
      },
    },
    {
      id:       'dnd',
      icon:     'moon',
      title:    'Do Not Disturb Access',
      desc:     'Allows alarm-type notifications to break through Do Not Disturb mode.',
      btnLabel: 'Open Settings',
      onPress:  async () => {
        await IntentLauncher.startActivityAsync(
          'android.settings.NOTIFICATION_POLICY_ACCESS_SETTINGS',
        );
        markDone('dnd');
      },
    },
  ];

  // Filter by platform and API level
  const { Version } = Platform;
  const apiLevel = typeof Version === 'number' ? Version : 0;
  const visibleSteps = steps.filter(s =>
    !s.androidMin || apiLevel >= s.androidMin,
  );

  return (
    <Modal visible={visible} animationType="fade" statusBarTranslucent>
      <SafeAreaView style={styles.container}>

        {/* Header */}
        <View style={styles.header}>
          <View style={styles.iconCircle}>
            <Ionicons name="shield-checkmark" size={40} color={C.orange} />
          </View>
          <Text style={styles.title}>One-Time Setup</Text>
          <Text style={styles.subtitle}>
            Grant these permissions so alarms work reliably — even when your phone is locked or in Do Not Disturb mode.
          </Text>
        </View>

        {/* Steps */}
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={styles.steps}
          showsVerticalScrollIndicator={false}
        >
          {visibleSteps.map(step => {
            const isDone = done[step.id];
            return (
              <View key={step.id} style={styles.stepCard}>
                <View style={[styles.stepIconWrap, isDone && styles.stepIconDone]}>
                  <Ionicons
                    name={isDone ? 'checkmark' : step.icon}
                    size={22}
                    color={isDone ? C.green : C.orange}
                  />
                </View>
                <View style={styles.stepBody}>
                  <Text style={styles.stepTitle}>{step.title}</Text>
                  <Text style={styles.stepDesc}>{step.desc}</Text>
                </View>
                {!isDone && (
                  <TouchableOpacity style={styles.stepBtn} onPress={step.onPress}>
                    <Text style={styles.stepBtnText}>{step.btnLabel}</Text>
                  </TouchableOpacity>
                )}
              </View>
            );
          })}

          {/* Samsung note */}
          <View style={styles.noteCard}>
            <Ionicons name="information-circle-outline" size={15} color={C.muted} style={{ marginTop: 1 }} />
            <Text style={styles.noteText}>
              On Samsung devices, also go to Settings → Apps → Clutch Alarm → Battery → Unrestricted, and make sure the app is not listed under Sleeping Apps.
            </Text>
          </View>
        </ScrollView>

        {/* Done button */}
        <TouchableOpacity style={styles.doneBtn} onPress={onDone}>
          <Text style={styles.doneBtnText}>I'm All Set — Get Started</Text>
          <Ionicons name="arrow-forward-circle" size={20} color="#fff" />
        </TouchableOpacity>

      </SafeAreaView>
    </Modal>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1, backgroundColor: C.bg,
    paddingHorizontal: 24,
  },

  header: {
    alignItems: 'center',
    paddingTop: 20, paddingBottom: 24,
  },
  iconCircle: {
    width: 80, height: 80, borderRadius: 40,
    backgroundColor: 'rgba(255,107,0,0.12)',
    borderWidth: 1.5, borderColor: 'rgba(255,107,0,0.3)',
    alignItems: 'center', justifyContent: 'center',
    marginBottom: 20,
  },
  title: {
    fontSize: 26, fontWeight: '900', color: C.text,
    letterSpacing: 0.3, marginBottom: 10,
  },
  subtitle: {
    fontSize: 14, color: C.muted, textAlign: 'center',
    lineHeight: 21,
  },

  steps: {
    gap: 10, paddingBottom: 16,
  },

  stepCard: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: C.card,
    borderRadius: 14, borderWidth: 1, borderColor: C.border,
    padding: 14, gap: 12,
  },
  stepIconWrap: {
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: 'rgba(255,107,0,0.1)',
    borderWidth: 1, borderColor: 'rgba(255,107,0,0.2)',
    alignItems: 'center', justifyContent: 'center',
    flexShrink: 0,
  },
  stepIconDone: {
    backgroundColor: 'rgba(63,185,80,0.1)',
    borderColor: 'rgba(63,185,80,0.3)',
  },
  stepBody: {
    flex: 1,
  },
  stepTitle: {
    fontSize: 14, fontWeight: '800', color: C.text, marginBottom: 3,
  },
  stepDesc: {
    fontSize: 12, color: C.muted, lineHeight: 17,
  },
  stepBtn: {
    backgroundColor: 'rgba(255,107,0,0.15)',
    borderWidth: 1, borderColor: 'rgba(255,107,0,0.4)',
    borderRadius: 8, paddingHorizontal: 12, paddingVertical: 7,
    flexShrink: 0,
  },
  stepBtnText: {
    color: C.orange, fontSize: 12, fontWeight: '700',
  },

  noteCard: {
    flexDirection: 'row', gap: 8, alignItems: 'flex-start',
    backgroundColor: 'rgba(107,138,173,0.08)',
    borderRadius: 10, borderWidth: 1, borderColor: C.border,
    padding: 12, marginTop: 4,
  },
  noteText: {
    flex: 1, fontSize: 12, color: C.muted, lineHeight: 18,
  },

  doneBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 10, backgroundColor: C.orange,
    borderRadius: 14, paddingVertical: 16,
    marginBottom: 12, marginTop: 8,
  },
  doneBtnText: {
    color: '#fff', fontSize: 15, fontWeight: '800',
  },
});
