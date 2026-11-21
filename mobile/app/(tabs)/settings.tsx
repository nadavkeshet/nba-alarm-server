import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import messaging from '@react-native-firebase/messaging';
import AsyncStorage from '@react-native-async-storage/async-storage';
import notifee, { AndroidImportance } from '@notifee/react-native';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTheme, type AppColors } from '../../context/ThemeContext';
import { useLanguage } from '../../context/LanguageContext';
import {
  Clipboard,
  Linking,
  NativeModules,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { signal } from '../../utils/telemetry';
import { WelcomeGuide } from '../../components/welcome-guide';
import { HamburgerButton } from '../../components/drawer-menu';

// ─── Sound definitions ────────────────────────────────────────────────────────

const ALARM_SOUNDS = [
  { id: 'classic_alarm', label: 'Classic Alarm' },
  { id: 'musical1',      label: 'Musical 1'     },
  { id: 'musical2',      label: 'Musical 2'     },
  { id: 'buzzer',        label: 'Buzzer'         },
  { id: 'dreamscape',    label: 'Dreamscape'     },
  { id: 'morningjoy',    label: 'Morning Joy'    },
] as const;

const SOUND_KEY = 'nba_alarm_sound';

// ─── SettingsScreen ───────────────────────────────────────────────────────────

export default function SettingsScreen() {
  const { colors: C, mode: themeMode, toggleTheme, noSpoiler, toggleNoSpoiler } = useTheme();
  const { t, lang, toggleLanguage } = useLanguage();
  const styles = useMemo(() => makeStyles(C), [C]);

  const [userToken, setUserToken]           = useState('');
  const [guideVisible, setGuideVisible]         = useState(false);
  const [saving]                             = useState(false);
  const [savedId, setSavedId]               = useState<string | null>(null);
  const [savedSound, setSavedSound]         = useState('classic_alarm');
  const [pendingSound, setPendingSound]     = useState('classic_alarm');
  const [previewingSound, setPreviewingSound] = useState<string | null>(null);
  const [infoModal, setInfoModal] = useState<{ visible: boolean; title: string; message: string }>({ visible: false, title: '', message: '' });
  const [previewError, setPreviewError] = useState<string | null>(null);

  const showInfo = (title: string, message: string) => setInfoModal({ visible: true, title, message });

  const flashSaved = useCallback((id: string) => {
    setSavedId(id);
    setTimeout(() => setSavedId(prev => prev === id ? null : prev), 1500);
  }, []);

  useEffect(() => {
    messaging().getToken()
      .then(tok => setUserToken(tok))
      .catch(() => {});
  }, []);

  useEffect(() => {
    AsyncStorage.getItem(SOUND_KEY).then(s => {
      const v = s ?? 'classic_alarm';
      setSavedSound(v);
      setPendingSound(v);
    });
  }, []);

  const handlePreview = (id: string) => {
    try {
      if (previewingSound === id) {
        NativeModules.AlarmChannel?.stopAlarmSound();
        setPreviewingSound(null);
      } else {
        NativeModules.AlarmChannel?.stopAlarmSound();
        NativeModules.AlarmChannel?.startAlarmSound(id);
        setPreviewingSound(id);
      }
      setPendingSound(id);
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      console.error('AlarmChannel error:', msg);
      setPreviewError(msg);
      setPendingSound(id);
    }
  };

  const saveAlarmSound = async () => {
    NativeModules.AlarmChannel?.stopAlarmSound();
    setPreviewingSound(null);
    await AsyncStorage.setItem(SOUND_KEY, pendingSound);
    setSavedSound(pendingSound);
    await notifee.deleteChannel('classic_alarm').catch(() => {});
    await notifee.createChannel({
      id:         'classic_alarm',
      name:       'Classic Alarm',
      sound:      pendingSound,
      importance: AndroidImportance.HIGH,
      bypassDnd:  true,
      vibration:  true,
    });
    await NativeModules.AlarmChannel?.patchChannel('classic_alarm', pendingSound);
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    flashSaved('sound');
  };

  return (
    <SafeAreaView style={styles.container}>
      <View style={{ flexDirection: 'row', alignItems: 'center', paddingRight: 16 }}>
        <HamburgerButton />
        <Text style={[styles.header, { flex: 1, marginLeft: 4 }]}>{t('settings.title')}</Text>
      </View>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingBottom: 48 }}
        keyboardShouldPersistTaps="handled"
      >
        {/* ── Appearance ────────────────────────────────────────────── */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t('settings.appearance')}</Text>
          <View style={styles.switchRow}>
            <Ionicons
              name={themeMode === 'dark' ? 'moon' : 'sunny'}
              size={18}
              color={C.accentBlue}
              style={{ marginRight: 8 }}
            />
            <Text style={styles.switchLabel}>
              {themeMode === 'dark' ? t('settings.darkMode') : t('settings.lightMode')}
            </Text>
            <Switch
              value={themeMode === 'dark'}
              onValueChange={() => { signal('Settings.ThemeChanged', { theme: themeMode === 'dark' ? 'light' : 'dark' }); toggleTheme(); }}
              trackColor={{ false: C.border, true: C.accentBlue }}
              thumbColor={themeMode === 'dark' ? C.textPrimary : '#fff'}
            />
          </View>
          <View style={[styles.switchRow, { marginTop: 4 }]}>
            <Ionicons
              name="language-outline"
              size={18}
              color={C.accentBlue}
              style={{ marginRight: 8 }}
            />
            <Text style={styles.switchLabel}>
              {lang === 'he' ? t('settings.hebrew') : t('settings.english')}
            </Text>
            <Switch
              value={lang === 'he'}
              onValueChange={() => { signal('Settings.LanguageChanged', { language: lang === 'he' ? 'en' : 'he' }); toggleLanguage(); }}
              trackColor={{ false: C.border, true: C.accentBlue }}
              thumbColor={lang === 'he' ? C.textPrimary : '#fff'}
            />
          </View>
          <View style={[styles.switchRow, { marginTop: 4 }]}>
            <Ionicons
              name="eye-off-outline"
              size={18}
              color={C.accentBlue}
              style={{ marginRight: 8 }}
            />
            <View style={{ flex: 1 }}>
              <Text style={styles.switchLabel}>{t('settings.noSpoiler')}</Text>
              <Text style={{ fontSize: 11, color: C.textMuted, marginTop: 1 }}>{t('settings.noSpoilerSub')}</Text>
            </View>
            <Switch
              value={noSpoiler}
              onValueChange={toggleNoSpoiler}
              trackColor={{ false: C.border, true: C.accentBlue }}
              thumbColor={noSpoiler ? C.textPrimary : '#fff'}
            />
          </View>
        </View>

        {/* ── Watch Score Legend ────────────────────────────────────── */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t('settings.watchScore')}</Text>
          <Text style={styles.sectionDesc}>{t('settings.watchScoreSub')}</Text>
          {([
            { color: '#22c55e', key: 'settings.watchScoreGreen' },
            { color: C.orange,  key: 'settings.watchScoreOrange' },
            { color: C.textMuted, key: 'settings.watchScoreGray' },
          ] as const).map(({ color, key }) => (
            <View key={key} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <View style={{ width: 12, height: 12, borderRadius: 6, backgroundColor: color }} />
              <Text style={{ fontSize: 13, color: C.textPrimary, fontWeight: '600' }}>{t(key)}</Text>
            </View>
          ))}
        </View>

        {/* ── Alarm Sound ───────────────────────────────────────────── */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t('settings.alarmSound')}</Text>
          <Text style={styles.sectionDesc}>{t('settings.alarmSoundSub')}</Text>

          {ALARM_SOUNDS.map(s => (
            <TouchableOpacity
              key={s.id}
              style={[styles.soundRow, pendingSound === s.id && styles.soundRowSelected]}
              onPress={() => handlePreview(s.id)}
              activeOpacity={0.7}
            >
              <Ionicons
                name={previewingSound === s.id ? 'stop-circle' : 'play-circle-outline'}
                size={26}
                color={pendingSound === s.id ? C.orange : C.textMuted}
              />
              <Text style={[styles.soundLabel, pendingSound === s.id && styles.soundLabelSelected]}>
                {s.label}
              </Text>
              {savedSound === s.id && (
                <Ionicons name="checkmark-circle" size={20} color={C.liveGreen} />
              )}
            </TouchableOpacity>
          ))}

          {previewError && (
            <Text style={{ color: '#FF6B00', fontSize: 11, marginBottom: 8, fontFamily: 'monospace' }}>
              ⚠️ {previewError}
            </Text>
          )}
          <TouchableOpacity
            style={[styles.updateBtn, { marginTop: 12 }]}
            onPress={saveAlarmSound}
            disabled={saving}
          >
            <Text style={[styles.updateBtnText, savedId === 'sound' && styles.savedBtnText]}>
              {savedId === 'sound' ? t('settings.saved') : t('settings.saveSound')}
            </Text>
          </TouchableOpacity>
        </View>

        {/* ── Onboarding shortcuts ──────────────────────────────────── */}
        <View style={styles.onboardingRow}>
          <TouchableOpacity
            style={styles.onboardingBtn}
            onPress={() => setGuideVisible(true)}
          >
            <Text style={styles.onboardingBtnEmoji}>📖</Text>
            <Text style={styles.onboardingBtnText}>{t('settings.appGuide')}</Text>
          </TouchableOpacity>
        </View>

        {/* ── Privacy Policy ────────────────────────────────────────── */}
        <TouchableOpacity
          style={styles.privacyBtn}
          onPress={() => Linking.openURL('https://clutchalarm.com/privacy')}
        >
          <Text style={styles.privacyBtnText}>Privacy Policy</Text>
        </TouchableOpacity>

        <Text style={styles.contactTitle}>Contact Us</Text>
        <View style={styles.contactRow}>
          <TouchableOpacity
            style={styles.contactIcon}
            onPress={() => Linking.openURL('mailto:nadav@clutchalarm.com')}
          >
            <Ionicons name="mail" size={28} color={C.textMuted} />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.contactIcon}
            onPress={() => { signal('Instagram.Opened', { source: 'settings' }); Linking.openURL('https://www.instagram.com/clutch_alarm?igsh=NTFwcmt1djB6Nm94'); }}
          >
            <Ionicons name="logo-instagram" size={28} color={C.textMuted} />
          </TouchableOpacity>
        </View>

        {userToken ? (
          <TouchableOpacity
            style={styles.copyTokenBtn}
            onPress={() => {
              Clipboard.setString(userToken);
              showInfo(t('settings.copied'), t('settings.copiedMsg'));
            }}
          >
            <Ionicons name="copy-outline" size={13} color={C.textMuted} />
            <Text style={styles.copyTokenText}>{t('settings.copyToken')}</Text>
          </TouchableOpacity>
        ) : null}

      </ScrollView>

      {/* ── Welcome Guide Modal ───────────────────────────────────── */}
      <WelcomeGuide
        visible={guideVisible}
        onClose={() => setGuideVisible(false)}
        closeLabel={t('guide.close')}
      />

      {/* ── Info Modal ───────────────────────────────────────────── */}
      {infoModal.visible && (
        <View style={styles.infoOverlay} pointerEvents="box-none">
          <View style={styles.infoBox}>
            <Text style={styles.infoTitle}>{infoModal.title}</Text>
            <Text style={styles.infoMessage}>{infoModal.message}</Text>
            <TouchableOpacity
              style={styles.infoOkBtn}
              onPress={() => setInfoModal(prev => ({ ...prev, visible: false }))}
            >
              <Text style={styles.infoOkText}>OK</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}
    </SafeAreaView>
  );
}

// ─── Styles ──────────────────────────────────────────────────────────────────

function makeStyles(C: AppColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: C.bg },
    header: {
      fontSize: 28, fontWeight: '900', color: C.textPrimary,
      paddingHorizontal: 16, paddingTop: 8, paddingBottom: 4,
      letterSpacing: 0.5,
    },

    // Section
    section: {
      backgroundColor: C.card,
      marginHorizontal: 16, marginTop: 16,
      borderRadius: 16, padding: 16,
      borderWidth: 1, borderColor: C.border,
    },
    sectionTitle: {
      fontSize: 11, fontWeight: '800', color: C.textMuted,
      letterSpacing: 1.2, textTransform: 'uppercase', marginBottom: 12,
    },
    sectionDesc: {
      fontSize: 13, color: C.textMuted, marginBottom: 12, lineHeight: 18,
    },

    // Switch rows
    switchRow:   { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 4 },
    switchLabel: { flex: 1, fontSize: 15, color: C.textPrimary, fontWeight: '600' },

    // Guide button
    guideBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      gap: 8, marginHorizontal: 16, marginTop: 20,
      paddingVertical: 14, borderRadius: 14,
      backgroundColor: 'rgba(255,107,0,0.10)',
      borderWidth: 1, borderColor: 'rgba(255,107,0,0.3)',
    },
    guideBtnText: {
      color: C.orange, fontWeight: '800', fontSize: 14, letterSpacing: 0.5,
    },

    // Sound picker
    soundRow: {
      flexDirection: 'row', alignItems: 'center', gap: 12,
      paddingVertical: 11, paddingHorizontal: 6,
      borderBottomWidth: 1, borderColor: C.border,
    },
    soundRowSelected:   { backgroundColor: 'rgba(255,107,0,0.07)', borderRadius: 8, borderBottomWidth: 0 },
    soundLabel:         { flex: 1, fontSize: 15, color: C.textMuted, fontWeight: '600' },
    soundLabelSelected: { color: C.textPrimary },

    // Buttons
    updateBtn: {
      backgroundColor: 'rgba(29,66,138,0.25)', padding: 10,
      borderRadius: 10, alignItems: 'center',
      borderWidth: 1, borderColor: C.blue,
    },
    updateBtnText: { color: C.accentBlue, fontWeight: '800', fontSize: 13 },
    savedBtnText:  { color: C.liveGreen },

    // Footer
    rateBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      gap: 8, marginHorizontal: 16, marginTop: 16,
      paddingVertical: 14, borderRadius: 14,
      backgroundColor: 'rgba(255,200,0,0.10)',
      borderWidth: 1, borderColor: 'rgba(255,200,0,0.3)',
    },
    rateBtnText: {
      color: C.gold, fontWeight: '800', fontSize: 14, letterSpacing: 0.5,
    },
    onboardingRow: {
      flexDirection: 'row', gap: 10,
      marginHorizontal: 16, marginTop: 8, marginBottom: 4,
    },
    onboardingBtn: {
      flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      gap: 6, paddingVertical: 12, borderRadius: 12,
      backgroundColor: C.cardAlt, borderWidth: 1, borderColor: C.border,
    },
    onboardingBtnEmoji: { fontSize: 16 },
    onboardingBtnText: { fontSize: 13, fontWeight: '700', color: C.textMuted },
    privacyBtn: {
      alignItems: 'center', marginHorizontal: 16, marginTop: 8, marginBottom: 8,
      paddingVertical: 10,
    },
    privacyBtnText: {
      color: C.textMuted, fontSize: 12, textDecorationLine: 'underline',
    },
    contactTitle: {
      color: C.textMuted, fontSize: 12, textAlign: 'center', marginBottom: 10, fontWeight: '600', letterSpacing: 1,
    },
    contactRow: {
      flexDirection: 'row', justifyContent: 'center', gap: 24, marginBottom: 8,
    },
    contactIcon: {
      padding: 4,
    },
    copyTokenBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      gap: 5, marginBottom: 16,
    },
    copyTokenText: {
      color: C.textMuted, fontSize: 11,
    },

    // Info modal
    infoOverlay: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.7)', alignItems: 'center', justifyContent: 'center', padding: 32 },
    infoBox:     { width: '100%', backgroundColor: C.card, borderRadius: 20, borderWidth: 1, borderColor: C.border, padding: 28, alignItems: 'center' },
    infoTitle:   { fontSize: 20, fontWeight: '900', color: C.textPrimary, letterSpacing: 0.5, marginBottom: 10 },
    infoMessage: { fontSize: 14, color: C.textMuted, textAlign: 'center', lineHeight: 20, marginBottom: 28 },
    infoOkBtn:   { width: '100%', paddingVertical: 14, borderRadius: 12, backgroundColor: 'rgba(255,107,0,0.15)', borderWidth: 1, borderColor: 'rgba(255,107,0,0.4)', alignItems: 'center' },
    infoOkText:  { color: C.orange, fontWeight: '800', fontSize: 15 },
  });
}
