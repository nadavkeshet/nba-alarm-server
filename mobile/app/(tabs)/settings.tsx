import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { useFocusEffect } from '@react-navigation/native';
import messaging from '@react-native-firebase/messaging';
import AsyncStorage from '@react-native-async-storage/async-storage';
import notifee, { AndroidImportance, AndroidVisibility } from '@notifee/react-native';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTheme, type AppColors } from '../../context/ThemeContext';
import { useLanguage } from '../../context/LanguageContext';
import {
  ActivityIndicator,
  Clipboard,
  FlatList,
  Image,
  Linking,
  Modal,
  NativeModules,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';

import Slider from '@react-native-community/slider';
import { BasketballToggle } from '../../components/basketball-toggle';
import { WelcomeGuide } from '../../components/welcome-guide';
import { SERVER_URL } from '../../config';

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

// ─── Types ────────────────────────────────────────────────────────────────────

interface GlobalRadar      { enabled: boolean; max_diff: number; start_quarter: number; alert_type: string }
interface FavTeam          { team_id: string; team_name: string; max_diff: number; start_quarter: number; alert_type: string; broadcast_only: boolean }
// Stored on server as { player_id: { name, total_pts, early_pts_q, early_pts, td_watch, alert_type } }
interface FavPlayerEntry {
  name:           string;
  total_pts:      number | null;   // null = condition disabled
  early_pts_q:    number | null;
  early_pts:      number | null;
  td_watch:       boolean;
  alert_type:     string;
  broadcast_only: boolean;
}
interface HistoricalCats   { scoring_40_half: boolean; threes_11: boolean; quad_double_threat: boolean }
interface AppSettings {
  GlobalGameRadar:  GlobalRadar;
  FavoriteTeams:    FavTeam[];
  FavoritePlayers:  Record<string, FavPlayerEntry>;   // dict from server
  HistoricalMode:   { enabled: boolean; alert_type: string; categories: HistoricalCats };
}
interface CachedPlayer { id: string; name: string; jersey: string; position: string }
interface CachedTeam   { id: string; name: string; abbreviation: string; logo: string | null; players: CachedPlayer[] }

// ─── Theme ────────────────────────────────────────────────────────────────────


// ─── Helpers ──────────────────────────────────────────────────────────────────

const QRow = React.memo(function QRow({ value, onChange, quarters = ['1', '2', '3', '4'] }: { value: string; onChange: (q: string) => void; quarters?: string[] }) {
  return (
    <View style={styles.quarterRow}>
      {quarters.map(q => (
        <TouchableOpacity
          key={q}
          style={[styles.qBtn, value === q && styles.qBtnActive]}
          onPress={() => onChange(q)}
        >
          <Text style={[styles.qBtnText, value === q && styles.qBtnTextActive]}>Q{q}</Text>
        </TouchableOpacity>
      ))}
    </View>
  );
});

// ─── SettingsScreen ───────────────────────────────────────────────────────────

export default function SettingsScreen() {
  const { colors: C, mode: themeMode, toggleTheme } = useTheme();
  const { t, lang, toggleLanguage } = useLanguage();
  const styles = useMemo(() => makeStyles(C), [C]);
  const insets = useSafeAreaInsets();
  const [userToken, setUserToken]       = useState('');
  const [tokenLoaded, setTokenLoaded]   = useState(false);
  const [settings, setSettings]         = useState<AppSettings | null>(null);
  const [loading, setLoading]           = useState(true);
  const [radarRunning, setRadarRunning] = useState(false);
  const [saving, setSaving]             = useState(false);
  const [savedId, setSavedId]           = useState<string | null>(null);
  const [guideVisible, setGuideVisible] = useState(false);
  const [infoModal, setInfoModal] = useState<{ visible: boolean; title: string; message: string }>({ visible: false, title: '', message: '' });

  // ── Sound picker state ─────────────────────────────────────────────
  const [savedSound,     setSavedSound]     = useState('classic_alarm');
  const [pendingSound,   setPendingSound]   = useState('classic_alarm');
  const [previewingSound, setPreviewingSound] = useState<string | null>(null);
  const showInfo = (title: string, message: string) => setInfoModal({ visible: true, title, message });
  const hideInfo = () => setInfoModal(prev => ({ ...prev, visible: false }));
  const [allTeams, setAllTeams]         = useState<CachedTeam[]>([]);

  // Stable separator — defined once so FlatList doesn't re-render rows on parent re-render
  const ListSeparator = useCallback(() => <View style={styles.separator} />, []);

  // Global radar local inputs
  const [radarMaxDiff, setRadarMaxDiff] = useState('5');
  const [radarStartQ, setRadarStartQ]   = useState('4');

  // Per-team in-place edit state for existing favorites
  const [teamEdits, setTeamEdits] = useState<Record<string, { max_diff: number; start_quarter: string; alert_type: string; broadcast_only: boolean }>>({});

  // ── Add Team Modal state ──────────────────────────────────────────────
  const [teamModalVisible, setTeamModalVisible] = useState(false);
  const [pickedTeam, setPickedTeam]             = useState<CachedTeam | null>(null);
  const [newTeamMaxDiff, setNewTeamMaxDiff]       = useState(5);
  const [newTeamStartQ, setNewTeamStartQ]         = useState('4');
  const [newTeamAlertType, setNewTeamAlertType]     = useState<'alarm' | 'silent'>('silent');
  const [newTeamBroadcastOnly, setNewTeamBroadcastOnly] = useState(false);

  // Per-player in-place edit state (all 4 conditions + enabled toggles)
  const [playerEdits, setPlayerEdits] = useState<Record<string, {
    totalPtsEnabled: boolean; total_pts: number;
    hotStartEnabled: boolean; early_pts_q: string; early_pts: number;
    td_watch: boolean; alert_type: string; broadcast_only: boolean;
  }>>({});

  // ── Add Player Modal state ────────────────────────────────────────────
  const [playerModalVisible, setPlayerModalVisible]   = useState(false);
  const [pickedTeamForPlayer, setPickedTeamForPlayer] = useState<CachedTeam | null>(null);
  const [pickedPlayer, setPickedPlayer]               = useState<CachedPlayer | null>(null);
  // New player config — toggles default OFF; inputs only appear when toggled ON
  const [newTotalPtsEnabled,  setNewTotalPtsEnabled]  = useState(false);
  const [newTotalPts,         setNewTotalPts]         = useState(40);
  const [newHotStartEnabled,  setNewHotStartEnabled]  = useState(false);
  const [newHotStartQ,        setNewHotStartQ]        = useState('1');
  const [newHotStartPts,      setNewHotStartPts]      = useState(15);
  const [newTdWatch,          setNewTdWatch]          = useState(false);
  const [newPlayerAlertType, setNewPlayerAlertType]         = useState<'alarm' | 'silent'>('silent');
  const [newPlayerBroadcastOnly, setNewPlayerBroadcastOnly] = useState(false);

  // ── Load push token once ───────────────────────────────────────────────

  useEffect(() => {
    messaging().getToken()
      .then(tok => setUserToken(tok))
      .catch(() => {})
      .finally(() => setTokenLoaded(true));
  }, []);

  // ── Load saved sound once on mount ─────────────────────────────────
  useEffect(() => {
    AsyncStorage.getItem(SOUND_KEY).then(s => {
      const v = s ?? 'classic_alarm';
      setSavedSound(v);
      setPendingSound(v);
    });
  }, []);

  // ── Load teams once on mount (roster is static — no need to re-fetch on focus) ─

  useEffect(() => {
    fetch(`${SERVER_URL}/all-teams-and-players`)
      .then(r => r.json())
      .then(resp => setAllTeams(resp.teams ?? []))
      .catch(() => {});
  }, []);

  // ── Load settings + radar (re-fetched on focus to reflect server changes) ──

  const settingsLoaded = useRef(false);

  const loadSettings = useCallback(async () => {
    // Only show the loading spinner on the very first load
    if (!settingsLoaded.current) setLoading(true);
    try {
      const tokenParam = userToken ? `?user_token=${encodeURIComponent(userToken)}` : '';
      const [sRes, rRes] = await Promise.all([
        fetch(`${SERVER_URL}/settings${tokenParam}`),
        fetch(`${SERVER_URL}/radar-status`),
      ]);
      const s: AppSettings = await sRes.json();
      const r              = await rRes.json();

      setSettings(s);
      setRadarRunning(r.running ?? false);
      setRadarMaxDiff(s.GlobalGameRadar?.max_diff ?? 5);
      setRadarStartQ(String(s.GlobalGameRadar?.start_quarter ?? 4));

      // Init per-team editable fields
      const edits: Record<string, { max_diff: number; start_quarter: string; alert_type: string; broadcast_only: boolean }> = {};
      for (const ft of s.FavoriteTeams ?? []) {
        edits[ft.team_id] = { max_diff: ft.max_diff, start_quarter: String(ft.start_quarter), alert_type: ft.alert_type ?? 'silent', broadcast_only: ft.broadcast_only ?? false };
      }
      setTeamEdits(edits);

      // Init per-player editable fields — derive enabled state from null checks
      const pEdits: Record<string, {
        totalPtsEnabled: boolean; total_pts: number;
        hotStartEnabled: boolean; early_pts_q: string; early_pts: number;
        td_watch: boolean; alert_type: string; broadcast_only: boolean;
      }> = {};
      for (const [pid, p] of Object.entries(s.FavoritePlayers ?? {})) {
        pEdits[pid] = {
          totalPtsEnabled: p.total_pts     != null,
          total_pts:       p.total_pts     ?? 40,
          hotStartEnabled: p.early_pts != null && p.early_pts_q != null,
          early_pts_q:     String(p.early_pts_q   ?? 1),
          early_pts:       p.early_pts ?? 15,
          td_watch:        p.td_watch ?? false,
          alert_type:      p.alert_type ?? 'silent',
          broadcast_only:  p.broadcast_only ?? false,
        };
      }
      setPlayerEdits(pEdits);
    } catch (e) {
      console.error('Settings load error:', e);
    } finally {
      settingsLoaded.current = true;
      setLoading(false);
    }
  }, [userToken]);

  // Only load settings once the token attempt has completed (resolves the empty-token
  // race: no API call fires before we know the user's identity).
  useEffect(() => { if (tokenLoaded) loadSettings(); }, [loadSettings, tokenLoaded]);

  useFocusEffect(
    useCallback(() => {
      loadSettings();
      return () => {
        NativeModules.AlarmChannel?.stopAlarmSound();
        setPreviewingSound(null);
      };
    }, [loadSettings]),
  );

  const flashSaved = useCallback((id: string) => {
    setSavedId(id);
    setTimeout(() => setSavedId(prev => prev === id ? null : prev), 1500);
  }, []);

  // ── Sound picker handlers ──────────────────────────────────────────

  const handlePreview = (id: string) => {
    if (previewingSound === id) {
      NativeModules.AlarmChannel?.stopAlarmSound();
      setPreviewingSound(null);
    } else {
      NativeModules.AlarmChannel?.stopAlarmSound();
      NativeModules.AlarmChannel?.startAlarmSound(id);
      setPreviewingSound(id);
      setPendingSound(id);
    }
  };

  const saveAlarmSound = async () => {
    NativeModules.AlarmChannel?.stopAlarmSound();
    setPreviewingSound(null);
    await AsyncStorage.setItem(SOUND_KEY, pendingSound);
    setSavedSound(pendingSound);
    // Recreate notification channel with the new sound
    await notifee.deleteChannel('classic_alarm').catch(() => {});
    await notifee.createChannel({
      id:         'classic_alarm',
      name:       'Classic Alarm',
      sound:      pendingSound,
      importance: AndroidImportance.HIGH,
      bypassDnd:  true,
      visibility: AndroidVisibility.PUBLIC,
      vibration:  true,
    });
    await NativeModules.AlarmChannel?.patchChannel('classic_alarm', pendingSound);
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    flashSaved('sound');
  };

  // Guard used by every mutation: returns true only when the FCM token is
  // resolved.  Prevents creating DB rows under an empty user_token if the
  // user somehow triggers a save before the async token fetch completes.
  const requireToken = (): boolean => {
    if (userToken) return true;
    showInfo(t('settings.notReady'), t('settings.notReadyMsg'));
    return false;
  };

  // ── API helpers ────────────────────────────────────────────────────────

  const patchGlobalRadar = async (patch: Partial<GlobalRadar>) => {
    if (!requireToken() || !settings) return;
    const next = { ...settings.GlobalGameRadar, ...patch };
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/global-radar`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...next, user_token: userToken }),
      });
      setSettings(s => s ? { ...s, GlobalGameRadar: next } : s);
      // If radar was just enabled, also start the background loop
      if (next.enabled && !radarRunning) {
        await fetch(`${SERVER_URL}/start-radar`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ push_token: '', sound_channel: 'classic_alarm' }),
        });
        setRadarRunning(true);
      }
      Haptics.selectionAsync();
      flashSaved('radar');
    } finally { setSaving(false); }
  };


  const patchHistorical = async (patch: { enabled?: boolean; alert_type?: string; categories?: Partial<HistoricalCats> }) => {
    if (!requireToken() || !settings) return;
    const cur  = settings.HistoricalMode;
    const next = { enabled: patch.enabled ?? cur.enabled, alert_type: patch.alert_type ?? cur.alert_type ?? 'silent', categories: { ...cur.categories, ...patch.categories } };
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/historical`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...next, user_token: userToken }),
      });
      setSettings(s => s ? { ...s, HistoricalMode: next } : s);
      Haptics.selectionAsync();
    } finally { setSaving(false); }
  };

  // ── Team CRUD ─────────────────────────────────────────────────────────

  const saveTeam = async (payload: FavTeam) => {
    if (!requireToken()) return;
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/teams`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, user_token: userToken }),
      });
      setSettings(s => s ? {
        ...s,
        FavoriteTeams: [...s.FavoriteTeams.filter(ft => ft.team_id !== payload.team_id), payload],
      } : s);
      setTeamEdits(prev => ({
        ...prev,
        [payload.team_id]: { max_diff: payload.max_diff, start_quarter: String(payload.start_quarter), alert_type: payload.alert_type, broadcast_only: payload.broadcast_only ?? false },
      }));
    } finally { setSaving(false); }
  };

  const removeTeam = async (team_id: string) => {
    if (!requireToken()) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const q = `?user_token=${encodeURIComponent(userToken)}`;
    await fetch(`${SERVER_URL}/settings/teams/${team_id}${q}`, { method: 'DELETE' });
    setSettings(s => s ? { ...s, FavoriteTeams: s.FavoriteTeams.filter(ft => ft.team_id !== team_id) } : s);
    setTeamEdits(prev => { const n = { ...prev }; delete n[team_id]; return n; });
  };

  const confirmAddTeam = async () => {
    if (!pickedTeam) return;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    await saveTeam({
      team_id:        pickedTeam.id,
      team_name:      pickedTeam.name,
      max_diff:       newTeamMaxDiff || 5,
      start_quarter:  parseInt(newTeamStartQ, 10) || 4,
      alert_type:     newTeamAlertType,
      broadcast_only: newTeamBroadcastOnly,
    });
    closeTeamModal();
  };

  const updateExistingTeam = async (team: FavTeam) => {
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    const edit = teamEdits[team.team_id] ?? { max_diff: team.max_diff, start_quarter: String(team.start_quarter), alert_type: team.alert_type, broadcast_only: team.broadcast_only ?? false };
    await saveTeam({
      ...team,
      max_diff:       edit.max_diff || 5,
      start_quarter:  parseInt(edit.start_quarter, 10) || 4,
      alert_type:     edit.alert_type,
      broadcast_only: edit.broadcast_only,
    });
    flashSaved(team.team_id);
  };

  // ── Player CRUD ───────────────────────────────────────────────────────

  const confirmAddPlayer = async () => {
    if (!requireToken() || !pickedPlayer) return;
    if (!newTotalPtsEnabled && !newHotStartEnabled && !newTdWatch) {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      showInfo(t('alarm.noCondition'), t('alarm.noConditionMsg'));
      return;
    }
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    const payload = {
      user_token:     userToken,
      player_id:      pickedPlayer.id,
      name:           pickedPlayer.name,
      // Send null for disabled conditions — server will store as NULL
      total_pts:      newTotalPtsEnabled  ? (newTotalPts  || 40) : null,
      early_pts_q:    newHotStartEnabled  ? (parseInt(newHotStartQ, 10) || 1) : null,
      early_pts:      newHotStartEnabled  ? (newHotStartPts || 15) : null,
      td_watch:       newTdWatch,
      alert_type:     newPlayerAlertType,
      broadcast_only: newPlayerBroadcastOnly,
    };
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/players`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setSettings(s => s ? {
        ...s,
        FavoritePlayers: {
          ...s.FavoritePlayers,
          [pickedPlayer.id]: {
            name:           pickedPlayer.name,
            total_pts:      payload.total_pts,
            early_pts_q:    payload.early_pts_q,
            early_pts:      payload.early_pts,
            td_watch:       payload.td_watch,
            alert_type:     newPlayerAlertType,
            broadcast_only: newPlayerBroadcastOnly,
          },
        },
      } : s);
      setPlayerEdits(prev => ({
        ...prev,
        [pickedPlayer.id]: {
          totalPtsEnabled: payload.total_pts    != null,
          total_pts:       payload.total_pts    ?? 40,
          hotStartEnabled: payload.early_pts != null,
          early_pts_q:     String(payload.early_pts_q  ?? 1),
          early_pts:       payload.early_pts ?? 15,
          td_watch:        payload.td_watch,
          alert_type:      newPlayerAlertType,
          broadcast_only:  newPlayerBroadcastOnly,
        },
      }));
      closePlayerModal();
    } finally { setSaving(false); }
  };

  const savePlayerSettings = async (player_id: string, name: string, alert_type: string) => {
    if (!requireToken()) return;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    const edit = playerEdits[player_id];
    if (!edit) return;
    const payload = {
      user_token:     userToken,
      player_id,
      name,
      total_pts:      edit.totalPtsEnabled  ? (edit.total_pts || 40) : null,
      early_pts_q:    edit.hotStartEnabled  ? (parseInt(edit.early_pts_q, 10) || 1) : null,
      early_pts:      edit.hotStartEnabled  ? (edit.early_pts || 15) : null,
      td_watch:       edit.td_watch,
      alert_type,
      broadcast_only: edit.broadcast_only ?? false,
    };
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/players`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setSettings(s => s ? {
        ...s,
        FavoritePlayers: {
          ...s.FavoritePlayers,
          [player_id]: {
            name,
            total_pts:     payload.total_pts,
            early_pts_q:   payload.early_pts_q,
            early_pts: payload.early_pts,
            td_watch:       payload.td_watch,
            alert_type,
            broadcast_only: payload.broadcast_only,
          },
        },
      } : s);
      flashSaved(player_id);
    } finally { setSaving(false); }
  };

  const removePlayer = async (player_id: string) => {
    if (!requireToken()) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const q = `?user_token=${encodeURIComponent(userToken)}`;
    await fetch(`${SERVER_URL}/settings/players/${player_id}${q}`, { method: 'DELETE' });
    setSettings(s => {
      if (!s) return s;
      const next = { ...s.FavoritePlayers };
      delete next[player_id];
      return { ...s, FavoritePlayers: next };
    });
    setPlayerEdits(prev => { const n = { ...prev }; delete n[player_id]; return n; });
  };

  // ── Modal helpers ─────────────────────────────────────────────────────

  const openTeamModal = () => {
    setPickedTeam(null);
    setNewTeamMaxDiff(5);
    setNewTeamStartQ('4');
    setNewTeamAlertType('silent');
    setNewTeamBroadcastOnly(false);
    setTeamModalVisible(true);
  };
  const closeTeamModal = () => { setTeamModalVisible(false); setPickedTeam(null); };

  const openPlayerModal = () => {
    setPickedTeamForPlayer(null);
    setPickedPlayer(null);
    setPlayerModalVisible(true);
  };
  const closePlayerModal = () => {
    setPlayerModalVisible(false);
    setPickedTeamForPlayer(null);
    setPickedPlayer(null);
    setNewTotalPtsEnabled(false);
    setNewTotalPts(40);
    setNewHotStartEnabled(false);
    setNewHotStartQ('1');
    setNewHotStartPts(15);
    setNewTdWatch(false);
    setNewPlayerAlertType('silent');
    setNewPlayerBroadcastOnly(false);
  };

  // ── Derived ───────────────────────────────────────────────────────────

  const favTeamIds   = new Set(settings?.FavoriteTeams?.map(ft => ft.team_id) ?? []);
  const favPlayerIds = new Set(Object.keys(settings?.FavoritePlayers ?? {}));

  // ── Loading ───────────────────────────────────────────────────────────

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <ActivityIndicator size="large" color={C.accentBlue} style={{ flex: 1 }} />
      </SafeAreaView>
    );
  }

  const radar    = settings?.GlobalGameRadar;
  const hist     = settings?.HistoricalMode;
  const histCats = hist?.categories;

  // ─── Render ────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={styles.container}>
      <Text style={styles.header}>{t('settings.title')}</Text>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingBottom: 48 }}
        keyboardShouldPersistTaps="handled"
      >

        {/* ═══════════════════════════════════════════════════════════════
            FAVORITE TEAMS
        ═══════════════════════════════════════════════════════════════ */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t('settings.favTeams')}</Text>
          <Text style={styles.sectionDesc}>{t('settings.favTeamsSub')}</Text>

          <TouchableOpacity style={styles.addRowBtn} onPress={openTeamModal}>
            <Ionicons name="add-circle-outline" size={20} color={C.accentBlue} />
            <Text style={styles.addRowBtnText}>{t('settings.addTeam')}</Text>
          </TouchableOpacity>

          {(settings?.FavoriteTeams ?? []).length === 0 ? (
            <Text style={styles.emptyHint}>{t('settings.noFavTeams')}</Text>
          ) : (
            settings!.FavoriteTeams.map(team => {
              const edit = teamEdits[team.team_id] ?? { max_diff: team.max_diff, start_quarter: String(team.start_quarter), alert_type: team.alert_type, broadcast_only: team.broadcast_only ?? false };
              return (
                <View key={team.team_id} style={styles.favCard}>
                  <View style={styles.favCardHeader}>
                    <Image
                      source={{ uri: `https://a.espncdn.com/i/teamlogos/nba/500/${team.team_id}.png` }}
                      style={styles.favTeamLogo}
                    />
                    <Text style={styles.favName}>{team.team_name}</Text>
                    <TouchableOpacity onPress={() => removeTeam(team.team_id)} style={styles.removeBtn}>
                      <Text style={styles.removeBtnText}>{t('settings.remove')}</Text>
                    </TouchableOpacity>
                  </View>

                  <View style={styles.sliderRow}>
                    <Text style={styles.inputLabel}>{t('settings.maxDiff')}</Text>
                    <Text style={styles.sliderValue}>{edit.max_diff}</Text>
                  </View>
                  <Slider
                    style={styles.slider}
                    minimumValue={1} maximumValue={25} step={1}
                    value={edit.max_diff}
                    onValueChange={v => setTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, max_diff: v } }))}
                    minimumTrackTintColor={C.orange}
                    maximumTrackTintColor={C.border}
                    thumbTintColor={C.orange}
                  />

                  <Text style={styles.inputLabel}>{t('settings.startFrom')}</Text>
                  <QRow
                    value={edit.start_quarter}
                    onChange={q => setTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, start_quarter: q } }))}
                  />

                  <View style={styles.alertTypeRow}>
                    {(['alarm', 'silent'] as const).map(type => (
                      <TouchableOpacity
                        key={type}
                        style={[styles.alertTypeBtn, (edit.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]}
                        onPress={() => setTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, alert_type: type } }))}
                      >
                        <Text style={styles.alertTypeIcon}>{type === 'alarm' ? '🚨' : '🔕'}</Text>
                        <Text style={[styles.alertTypeText, (edit.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>
                          {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  {/* Broadcast Only */}
                  <View style={[styles.condCard, { marginTop: 8 }]}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.broadcastOnly')}</Text>
                        <Text style={styles.condCardSub}>{t('settings.broadcastOnlySub')}</Text>
                      </View>
                      <BasketballToggle
                        value={edit.broadcast_only}
                        onValueChange={v => setTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, broadcast_only: v } }))}
                      />
                    </View>
                  </View>

                  <TouchableOpacity style={styles.updateBtn} onPress={() => updateExistingTeam(team)} disabled={saving}>
                    <Text style={[styles.updateBtnText, savedId === team.team_id && styles.savedBtnText]}>
                      {savedId === team.team_id ? t('settings.saved') : t('settings.update')}
                    </Text>
                  </TouchableOpacity>
                </View>
              );
            })
          )}
        </View>

        {/* ═══════════════════════════════════════════════════════════════
            FAVORITE PLAYERS
        ═══════════════════════════════════════════════════════════════ */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t('settings.favPlayers')}</Text>
          <Text style={styles.sectionDesc}>{t('settings.favPlayersSub')}</Text>

          <TouchableOpacity style={styles.addRowBtn} onPress={openPlayerModal}>
            <Ionicons name="add-circle-outline" size={20} color={C.accentBlue} />
            <Text style={styles.addRowBtnText}>{t('settings.addPlayer')}</Text>
          </TouchableOpacity>

          {Object.keys(settings?.FavoritePlayers ?? {}).length === 0 ? (
            <Text style={styles.emptyHint}>{t('settings.noFavPlayers')}</Text>
          ) : (
            Object.entries(settings!.FavoritePlayers).map(([pid, p]) => {
              const edit = playerEdits[pid] ?? {
                totalPtsEnabled: p.total_pts     != null,
                total_pts:       p.total_pts     ?? 40,
                hotStartEnabled: p.early_pts != null && p.early_pts_q != null,
                early_pts_q:     String(p.early_pts_q   ?? 1),
                early_pts:       p.early_pts ?? 15,
                td_watch:        p.td_watch ?? false,
                alert_type:      p.alert_type ?? 'silent',
              };
              const setEdit = (patch: Partial<typeof edit>) =>
                setPlayerEdits(prev => ({ ...prev, [pid]: { ...edit, ...patch } }));

              return (
                <View key={pid} style={styles.favCard}>
                  <View style={styles.favCardHeader}>
                    <Image
                      source={{ uri: `https://a.espncdn.com/i/headshots/nba/players/full/${pid}.png` }}
                      style={styles.favPlayerHeadshot}
                    />
                    <Text style={styles.favName}>{p.name}</Text>
                    <TouchableOpacity onPress={() => removePlayer(pid)} style={styles.removeBtn}>
                      <Text style={styles.removeBtnText}>{t('settings.remove')}</Text>
                    </TouchableOpacity>
                  </View>

                  {/* Total PTS */}
                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.totalPtsAlert')}</Text>
                      </View>
                      <BasketballToggle
                        value={edit.totalPtsEnabled}
                        onValueChange={v => setEdit({ totalPtsEnabled: v })}
                      />
                    </View>
                    {edit.totalPtsEnabled && (
                      <>
                        <View style={[styles.sliderRow, { marginTop: 10 }]}>
                          <Text style={styles.inputLabel}>{t('settings.ptsThreshold')}</Text>
                          <Text style={styles.sliderValue}>{edit.total_pts}</Text>
                        </View>
                        <Slider
                          style={styles.slider}
                          minimumValue={1} maximumValue={80} step={1}
                          value={edit.total_pts}
                          onValueChange={v => setEdit({ total_pts: v })}
                          minimumTrackTintColor={C.orange}
                          maximumTrackTintColor={C.border}
                          thumbTintColor={C.orange}
                        />
                      </>
                    )}
                  </View>

                  {/* Early Points */}
                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.earlyPtsAlert')}</Text>
                      </View>
                      <BasketballToggle
                        value={edit.hotStartEnabled}
                        onValueChange={v => setEdit({ hotStartEnabled: v })}
                      />
                    </View>
                    {edit.hotStartEnabled && (
                      <>
                        <View style={[styles.sliderRow, { marginTop: 10 }]}>
                          <Text style={styles.inputLabel}>{t('settings.ptsThreshold')}</Text>
                          <Text style={styles.sliderValue}>{edit.early_pts}</Text>
                        </View>
                        <Slider
                          style={styles.slider}
                          minimumValue={1} maximumValue={80} step={1}
                          value={edit.early_pts}
                          onValueChange={v => setEdit({ early_pts: v })}
                          minimumTrackTintColor={C.orange}
                          maximumTrackTintColor={C.border}
                          thumbTintColor={C.orange}
                        />
                        <Text style={styles.inputLabel}>{t('settings.byEndOfQ')}</Text>
                        <QRow value={edit.early_pts_q} onChange={q => setEdit({ early_pts_q: q })} quarters={['1', '2', '3']} />
                      </>
                    )}
                  </View>

                  {/* Triple-Double Watch */}
                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.tripleDouble')}</Text>
                      </View>
                      <BasketballToggle
                        value={edit.td_watch}
                        onValueChange={v => setEdit({ td_watch: v })}
                      />
                    </View>
                  </View>

                  {/* Broadcast Only */}
                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.broadcastOnly')}</Text>
                        <Text style={styles.condCardSub}>{t('settings.broadcastOnlySub')}</Text>
                      </View>
                      <BasketballToggle
                        value={edit.broadcast_only}
                        onValueChange={v => setEdit({ broadcast_only: v })}
                      />
                    </View>
                  </View>

                  <View style={styles.alertTypeRow}>
                    {(['alarm', 'silent'] as const).map(type => (
                      <TouchableOpacity
                        key={type}
                        style={[styles.alertTypeBtn, (edit.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]}
                        onPress={() => setEdit({ alert_type: type })}
                      >
                        <Text style={styles.alertTypeIcon}>{type === 'alarm' ? '🚨' : '🔕'}</Text>
                        <Text style={[styles.alertTypeText, (edit.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>
                          {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  <TouchableOpacity
                    style={[styles.updateBtn, { marginTop: 4 }]}
                    onPress={() => savePlayerSettings(pid, p.name, edit.alert_type ?? p.alert_type)}
                    disabled={saving}
                  >
                    <Text style={[styles.updateBtnText, savedId === pid && styles.savedBtnText]}>
                      {savedId === pid ? t('settings.saved') : t('settings.update')}
                    </Text>
                  </TouchableOpacity>
                </View>
              );
            })
          )}
        </View>

        {/* ═══════════════════════════════════════════════════════════════
            GLOBAL GAME RADAR
        ═══════════════════════════════════════════════════════════════ */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t('settings.radar')}</Text>
          <Text style={styles.sectionDesc}>{t('settings.radarSub')}</Text>

          <View style={styles.switchRow}>
            <Text style={styles.rowLabel}>{t('settings.enableRadar')}</Text>
            <BasketballToggle
              value={radar?.enabled ?? false}
              onValueChange={v => patchGlobalRadar({ enabled: v })}
            />
          </View>

          <View style={styles.alertTypeRow}>
            {(['alarm', 'silent'] as const).map(type => (
              <TouchableOpacity
                key={type}
                style={[styles.alertTypeBtn, (radar?.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]}
                onPress={() => patchGlobalRadar({ alert_type: type })}
              >
                <Text style={styles.alertTypeIcon}>{type === 'alarm' ? '🚨' : '🔕'}</Text>
                <Text style={[styles.alertTypeText, (radar?.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>
                  {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {radar?.enabled && (
            <>
              <View style={styles.sliderRow}>
                <Text style={styles.inputLabel}>{t('settings.maxDiff')}</Text>
                <Text style={styles.sliderValue}>{radarMaxDiff}</Text>
              </View>
              <Slider
                style={styles.slider}
                minimumValue={1} maximumValue={25} step={1}
                value={radarMaxDiff}
                onValueChange={setRadarMaxDiff}
                minimumTrackTintColor={C.orange}
                maximumTrackTintColor={C.border}
                thumbTintColor={C.orange}
              />
              <Text style={styles.inputLabel}>{t('settings.startFrom')}</Text>
              <QRow value={radarStartQ} onChange={setRadarStartQ} />
              <TouchableOpacity
                style={styles.updateBtn}
                onPress={() => patchGlobalRadar({
                  max_diff:      radarMaxDiff || 5,
                  start_quarter: parseInt(radarStartQ, 10) || 4,
                })}
                disabled={saving}
              >
                <Text style={[styles.updateBtnText, savedId === 'radar' && styles.savedBtnText]}>
                  {saving ? t('settings.saving') : savedId === 'radar' ? t('settings.saved') : t('settings.saveRadar')}
                </Text>
              </TouchableOpacity>
            </>
          )}

          <View style={[styles.switchRow, { marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderColor: C.border }]}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>{t('settings.radarStatus')}</Text>
              <Text style={styles.rowSub}>{radarRunning ? t('settings.radarRunning') : t('settings.radarStopped')}</Text>
            </View>
            <View style={[styles.statusDot, { backgroundColor: radarRunning ? C.liveGreen : C.border }]} />
          </View>
        </View>

        {/* ═══════════════════════════════════════════════════════════════
            HISTORICAL ALERTS
        ═══════════════════════════════════════════════════════════════ */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t('settings.historical')}</Text>
          <Text style={styles.sectionDesc}>{t('settings.historicalSub')}</Text>

          <View style={styles.switchRow}>
            <Text style={styles.rowLabel}>{t('settings.enableHistorical')}</Text>
            <BasketballToggle
              value={hist?.enabled ?? false}
              onValueChange={v => patchHistorical({ enabled: v })}
            />
          </View>

          <View style={styles.alertTypeRow}>
            {(['alarm', 'silent'] as const).map(type => (
              <TouchableOpacity
                key={type}
                style={[styles.alertTypeBtn, ((hist as any)?.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]}
                onPress={() => patchHistorical({ alert_type: type })}
              >
                <Text style={styles.alertTypeIcon}>{type === 'alarm' ? '🚨' : '🔕'}</Text>
                <Text style={[styles.alertTypeText, ((hist as any)?.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>
                  {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {hist?.enabled && (
            <>
              {([
                { key: 'scoring_40_half'    as const, label: t('settings.scoring40'),  sub: t('settings.scoring40Sub') },
                { key: 'threes_11'          as const, label: t('settings.threes11'),    sub: t('settings.threes11Sub') },
                { key: 'quad_double_threat' as const, label: t('settings.quadDouble'),  sub: t('settings.quadDoubleSub') },
              ] as const).map(({ key, label, sub }) => (
                <View key={key} style={styles.categoryRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowLabel}>{label}</Text>
                    <Text style={styles.rowSub}>{sub}</Text>
                  </View>
                  <BasketballToggle
                    value={histCats?.[key] ?? false}
                    onValueChange={v => patchHistorical({ categories: { [key]: v } })}
                  />
                </View>
              ))}
            </>
          )}
        </View>

        {/* ═══════════════════════════════════════════════════════════════
            ALARM SOUND
        ═══════════════════════════════════════════════════════════════ */}
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

        {/* ── App Guide button ──────────────────────────────────────── */}
        <TouchableOpacity
          style={styles.guideBtn}
          onPress={() => setGuideVisible(true)}
        >
          <Ionicons name="information-circle-outline" size={20} color={C.orange} />
          <Text style={styles.guideBtnText}>{t('settings.appGuide')}</Text>
        </TouchableOpacity>

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
              onValueChange={toggleTheme}
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
              onValueChange={toggleLanguage}
              trackColor={{ false: C.border, true: C.accentBlue }}
              thumbColor={lang === 'he' ? C.textPrimary : '#fff'}
            />
          </View>
        </View>

        {/* ── Privacy Policy ────────────────────────────────────────── */}
        <TouchableOpacity
          style={styles.privacyBtn}
          onPress={() => Linking.openURL('https://sites.google.com/view/nba-smart-alarm-privacy/%D7%91%D7%99%D7%AA')}
        >
          <Text style={styles.privacyBtnText}>Privacy Policy</Text>
        </TouchableOpacity>

        <TouchableOpacity onPress={() => Linking.openURL('mailto:nbasmartalarm@gmail.com')}>
          <Text style={styles.contactText}>nbasmartalarm@gmail.com</Text>
        </TouchableOpacity>

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

      {/* ═══════════════════════════════════════════════════════════════
          ADD TEAM MODAL
      ═══════════════════════════════════════════════════════════════ */}
      <Modal visible={teamModalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet, { paddingBottom: insets.bottom }]}>

            {/* Header */}
            <View style={styles.modalHeader}>
              {pickedTeam ? (
                <TouchableOpacity onPress={() => setPickedTeam(null)} style={styles.backBtn}>
                  <Ionicons name="arrow-back" size={20} color={C.accentBlue} />
                  <Text style={styles.backBtnText}>Teams</Text>
                </TouchableOpacity>
              ) : <View style={{ width: 70 }} />}
              <Text style={styles.modalTitle}>
                {pickedTeam ? pickedTeam.abbreviation : t('settings.selectTeam')}
              </Text>
              <TouchableOpacity onPress={closeTeamModal} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

            {/* Step 1: Team list */}
            {!pickedTeam && (
              <FlatList
                data={allTeams}
                keyExtractor={item => item.id}
                initialNumToRender={15}
                maxToRenderPerBatch={10}
                windowSize={5}
                renderItem={({ item: team }) => {
                  const isFav = favTeamIds.has(team.id);
                  return (
                    <TouchableOpacity
                      style={styles.pickerRow}
                      onPress={() => setPickedTeam(team)}
                    >
                      <Text style={styles.pickerRowText}>{team.name}</Text>
                      <View style={styles.pickerRowRight}>
                        {isFav && <Text style={styles.savedBadge}>{t('settings.savedBadge')}</Text>}
                        <Text style={styles.pickerRowAbbr}>{team.abbreviation}</Text>
                        <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                      </View>
                    </TouchableOpacity>
                  );
                }}
                ItemSeparatorComponent={ListSeparator}
                contentContainerStyle={{ paddingBottom: 24 }}
              />
            )}

            {/* Step 2: Config */}
            {pickedTeam && (
              <ScrollView keyboardShouldPersistTaps="handled" style={{ flex: 1 }}>
                <View style={styles.configArea}>
                  <Text style={styles.configTitle}>{pickedTeam.name}</Text>
                  <Text style={styles.configSub}>{t('settings.configSub')}</Text>

                  <View style={styles.sliderRow}>
                    <Text style={styles.inputLabel}>{t('settings.maxDiff')}</Text>
                    <Text style={styles.sliderValue}>{newTeamMaxDiff}</Text>
                  </View>
                  <Slider
                    style={styles.slider}
                    minimumValue={1} maximumValue={25} step={1}
                    value={newTeamMaxDiff}
                    onValueChange={setNewTeamMaxDiff}
                    minimumTrackTintColor={C.orange}
                    maximumTrackTintColor={C.border}
                    thumbTintColor={C.orange}
                  />

                  <Text style={styles.inputLabel}>{t('settings.startFrom')}</Text>
                  <QRow value={newTeamStartQ} onChange={setNewTeamStartQ} />

                  <View style={styles.alertTypeRow}>
                    {(['alarm', 'silent'] as const).map(type => (
                      <TouchableOpacity
                        key={type}
                        style={[styles.alertTypeBtn, newTeamAlertType === type && styles.alertTypeBtnActive]}
                        onPress={() => setNewTeamAlertType(type)}
                      >
                        <Text style={styles.alertTypeIcon}>{type === 'alarm' ? '🚨' : '🔕'}</Text>
                        <Text style={[styles.alertTypeText, newTeamAlertType === type && styles.alertTypeTextActive]}>
                          {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  {/* Broadcast Only */}
                  <View style={[styles.condCard, { marginTop: 8 }]}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.broadcastOnly')}</Text>
                        <Text style={styles.condCardSub}>{t('settings.broadcastOnlySub')}</Text>
                      </View>
                      <BasketballToggle
                        value={newTeamBroadcastOnly}
                        onValueChange={setNewTeamBroadcastOnly}
                      />
                    </View>
                  </View>

                  <View style={styles.configSummary}>
                    <Ionicons name="information-circle-outline" size={14} color={C.textMuted} />
                    <Text style={styles.configSummaryText}>
                      {t('settings.alertDiff', newTeamMaxDiff || 5, newTeamStartQ)}
                    </Text>
                  </View>

                  <TouchableOpacity
                    style={[styles.confirmBtn, saving && styles.confirmBtnDisabled]}
                    onPress={confirmAddTeam}
                    disabled={saving}
                  >
                    <Text style={styles.confirmBtnText}>
                      {saving ? t('settings.saving') : t('settings.addTeamBtn', pickedTeam.abbreviation)}
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.cancelBtn} onPress={closeTeamModal}>
                    <Text style={styles.cancelBtnText}>{t('settings.cancel')}</Text>
                  </TouchableOpacity>
                </View>
              </ScrollView>
            )}

          </View>
        </View>
      </Modal>

      {/* ═══════════════════════════════════════════════════════════════
          ADD PLAYER MODAL
      ═══════════════════════════════════════════════════════════════ */}
      <Modal visible={playerModalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet, { paddingBottom: insets.bottom }]}>

            {/* Header */}
            <View style={styles.modalHeader}>
              {(pickedTeamForPlayer || pickedPlayer) ? (
                <TouchableOpacity
                  onPress={() => pickedPlayer ? setPickedPlayer(null) : setPickedTeamForPlayer(null)}
                  style={styles.backBtn}
                >
                  <Ionicons name="arrow-back" size={20} color={C.accentBlue} />
                  <Text style={styles.backBtnText}>
                    {pickedPlayer ? pickedTeamForPlayer?.abbreviation : 'Teams'}
                  </Text>
                </TouchableOpacity>
              ) : <View style={{ width: 70 }} />}

              <Text style={styles.modalTitle}>
                {pickedPlayer
                  ? t('settings.configureAlert')
                  : pickedTeamForPlayer
                    ? `${pickedTeamForPlayer.abbreviation} Roster`
                    : t('settings.selectTeam')}
              </Text>
              <TouchableOpacity onPress={closePlayerModal} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

            {/* Step 1: Team list */}
            {!pickedTeamForPlayer && (
              <FlatList
                data={allTeams}
                keyExtractor={item => item.id}
                initialNumToRender={15}
                maxToRenderPerBatch={10}
                windowSize={5}
                renderItem={({ item: team }) => (
                  <TouchableOpacity
                    style={styles.pickerRow}
                    onPress={() => setPickedTeamForPlayer(team)}
                  >
                    <Text style={styles.pickerRowText}>{team.name}</Text>
                    <View style={styles.pickerRowRight}>
                      <Text style={styles.pickerRowAbbr}>{t('settings.players', team.players.length)}</Text>
                      <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                    </View>
                  </TouchableOpacity>
                )}
                ItemSeparatorComponent={ListSeparator}
                contentContainerStyle={{ paddingBottom: 24 }}
              />
            )}

            {/* Step 2: Player list */}
            {pickedTeamForPlayer && !pickedPlayer && (
              <FlatList
                data={pickedTeamForPlayer.players}
                keyExtractor={p => p.id}
                initialNumToRender={17}
                maxToRenderPerBatch={10}
                windowSize={3}
                renderItem={({ item: p }) => {
                  const isFav = favPlayerIds.has(p.id);
                  return (
                    <TouchableOpacity
                      style={styles.pickerRow}
                      onPress={() => setPickedPlayer(p)}
                    >
                      <Image
                        source={{ uri: `https://a.espncdn.com/i/headshots/nba/players/full/${p.id}.png` }}
                        style={styles.pickerPlayerHeadshot}
                      />
                      <View style={{ flex: 1 }}>
                        <Text style={styles.pickerRowText}>{p.name}</Text>
                        <Text style={styles.pickerRowSub}>
                          {p.jersey ? `#${p.jersey}` : ''}{p.jersey && p.position ? '  ·  ' : ''}{p.position}
                        </Text>
                      </View>
                      <View style={styles.pickerRowRight}>
                        {isFav && <Text style={styles.savedBadge}>★</Text>}
                        <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                      </View>
                    </TouchableOpacity>
                  );
                }}
                ItemSeparatorComponent={ListSeparator}
                contentContainerStyle={{ paddingBottom: 24 }}
              />
            )}

            {/* Step 3: Config */}
            {pickedPlayer && (
              <ScrollView keyboardShouldPersistTaps="handled" style={{ flex: 1 }}>
                <View style={styles.configArea}>
                  <Text style={styles.configTitle}>{pickedPlayer.name}</Text>
                  <Text style={styles.configSub}>
                    {pickedTeamForPlayer?.name}
                    {pickedPlayer.jersey ? `  ·  #${pickedPlayer.jersey}` : ''}
                    {pickedPlayer.position ? `  ·  ${pickedPlayer.position}` : ''}
                  </Text>
                  <Text style={[styles.configSub, { marginBottom: 16 }]}>
                    {t('settings.playerCondSub')}
                  </Text>

                  {/* ── Total Points Alert ── */}
                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.totalPtsAlert')}</Text>
                        <Text style={styles.condCardSub}>{t('alarm.ptsGoalSub')}</Text>
                      </View>
                      <BasketballToggle
                        value={newTotalPtsEnabled}
                        onValueChange={setNewTotalPtsEnabled}
                      />
                    </View>
                    {newTotalPtsEnabled && (
                      <>
                        <View style={[styles.sliderRow, { marginTop: 10 }]}>
                          <Text style={styles.inputLabel}>{t('settings.ptsThreshold')}</Text>
                          <Text style={styles.sliderValue}>{newTotalPts}</Text>
                        </View>
                        <Slider
                          style={styles.slider}
                          minimumValue={1} maximumValue={80} step={1}
                          value={newTotalPts}
                          onValueChange={setNewTotalPts}
                          minimumTrackTintColor={C.orange}
                          maximumTrackTintColor={C.border}
                          thumbTintColor={C.orange}
                        />
                      </>
                    )}
                  </View>

                  {/* ── Early Points Alert ── */}
                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.earlyPtsAlert')}</Text>
                        <Text style={styles.condCardSub}>{t('alarm.earlyPtsSub')}</Text>
                      </View>
                      <BasketballToggle
                        value={newHotStartEnabled}
                        onValueChange={setNewHotStartEnabled}
                      />
                    </View>
                    {newHotStartEnabled && (
                      <>
                        <View style={[styles.sliderRow, { marginTop: 10 }]}>
                          <Text style={styles.inputLabel}>{t('settings.ptsThreshold')}</Text>
                          <Text style={styles.sliderValue}>{newHotStartPts}</Text>
                        </View>
                        <Slider
                          style={styles.slider}
                          minimumValue={1} maximumValue={80} step={1}
                          value={newHotStartPts}
                          onValueChange={setNewHotStartPts}
                          minimumTrackTintColor={C.orange}
                          maximumTrackTintColor={C.border}
                          thumbTintColor={C.orange}
                        />
                        <Text style={styles.inputLabel}>{t('settings.byEndOfQ')}</Text>
                        <QRow value={newHotStartQ} onChange={setNewHotStartQ} quarters={['1', '2', '3']} />
                      </>
                    )}
                  </View>

                  {/* ── Triple-Double Watch ── */}
                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.tripleDouble')}</Text>
                        <Text style={styles.condCardSub}>{t('alarm.tripleDoubleSub')}</Text>
                      </View>
                      <BasketballToggle
                        value={newTdWatch}
                        onValueChange={setNewTdWatch}
                      />
                    </View>
                  </View>

                  {/* ── Broadcast Only ── */}
                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.broadcastOnly')}</Text>
                        <Text style={styles.condCardSub}>{t('settings.broadcastOnlySub')}</Text>
                      </View>
                      <BasketballToggle
                        value={newPlayerBroadcastOnly}
                        onValueChange={setNewPlayerBroadcastOnly}
                      />
                    </View>
                  </View>

                  {/* ── Alert Type ── */}
                  <View style={styles.alertTypeRow}>
                    {(['alarm', 'silent'] as const).map(type => (
                      <TouchableOpacity
                        key={type}
                        style={[styles.alertTypeBtn, newPlayerAlertType === type && styles.alertTypeBtnActive]}
                        onPress={() => setNewPlayerAlertType(type)}
                      >
                        <Text style={styles.alertTypeIcon}>{type === 'alarm' ? '🚨' : '🔕'}</Text>
                        <Text style={[styles.alertTypeText, newPlayerAlertType === type && styles.alertTypeTextActive]}>
                          {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  {/* ── Summary ── */}
                  {(newTotalPtsEnabled || newHotStartEnabled || newTdWatch) && (
                    <View style={styles.configSummary}>
                      <Ionicons name="information-circle-outline" size={14} color={C.textMuted} />
                      <Text style={styles.configSummaryText}>
                        {[
                          newTotalPtsEnabled  && `${newTotalPts || 40}+ pts`,
                          newHotStartEnabled  && `${newHotStartPts || 15} pts by Q${newHotStartQ}`,
                          newTdWatch          && t('active.tdWatch'),
                        ].filter(Boolean).join('  ·  ')}
                      </Text>
                    </View>
                  )}

                  <TouchableOpacity
                    style={[styles.confirmBtn, saving && styles.confirmBtnDisabled]}
                    onPress={confirmAddPlayer}
                    disabled={saving}
                  >
                    <Text style={styles.confirmBtnText}>
                      {saving ? t('settings.saving') : t('settings.addPlayerBtn', pickedPlayer.name)}
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.cancelBtn} onPress={closePlayerModal}>
                    <Text style={styles.cancelBtnText}>{t('settings.cancel')}</Text>
                  </TouchableOpacity>
                </View>
              </ScrollView>
            )}

          </View>
        </View>
      </Modal>

      <Modal visible={infoModal.visible} transparent animationType="fade" onRequestClose={hideInfo}>
        <View style={styles.infoOverlay}>
          <View style={styles.infoBox}>
            <Text style={styles.infoTitle}>{infoModal.title}</Text>
            <Text style={styles.infoMessage}>{infoModal.message}</Text>
            <TouchableOpacity style={styles.infoOkBtn} onPress={hideInfo}>
              <Text style={styles.infoOkText}>OK</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

    </SafeAreaView>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const makeStyles = (C: AppColors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },

  header: {
    fontSize: 24, fontWeight: '800', color: C.textPrimary,
    textAlign: 'center', paddingTop: 12, paddingBottom: 8, letterSpacing: 1.5,
  },

  // Section card
  section: {
    marginHorizontal: 16, marginTop: 14,
    backgroundColor: C.card, borderRadius: 16,
    borderWidth: 1, borderColor: C.border, padding: 16,
  },
  sectionTitle: {
    fontSize: 11, fontWeight: '800', color: C.textMuted,
    letterSpacing: 2, marginBottom: 4,
  },
  sectionDesc: { fontSize: 12, color: C.textMuted, lineHeight: 17, marginBottom: 14 },

  // Add button row
  addRowBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 10, paddingHorizontal: 12,
    backgroundColor: 'rgba(77,140,245,0.1)',
    borderRadius: 10, borderWidth: 1, borderColor: C.accentBlue,
    marginBottom: 12,
  },
  addRowBtnText: { color: C.accentBlue, fontWeight: '700', fontSize: 14 },

  // Condition toggle card (inside player cards and modal step 3)
  condCard: {
    backgroundColor: C.card, borderRadius: 10,
    borderWidth: 1, borderColor: C.border,
    padding: 12, marginBottom: 8,
  },
  condCardHeader: { flexDirection: 'row', alignItems: 'center' },
  condCardTitle:  { fontSize: 14, fontWeight: '700', color: C.textPrimary },
  condCardSub:    { fontSize: 12, color: C.textMuted, marginTop: 2 },

  // Favorite card (existing items)
  favCard: {
    backgroundColor: C.bg, borderRadius: 12,
    borderWidth: 1, borderColor: C.border,
    padding: 12, marginTop: 10,
  },
  favCardHeader: {
    flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'center', marginBottom: 6,
  },
  favName: { fontSize: 15, fontWeight: '800', color: C.textPrimary, flex: 1 },
  favTeamLogo:       { width: 36, height: 36, marginRight: 8 },
  favPlayerHeadshot: { width: 36, height: 36, borderRadius: 18, marginRight: 8 },

  // Labels & inputs
  inputLabel: {
    fontSize: 10, fontWeight: '800', color: C.textMuted,
    letterSpacing: 1.5, marginBottom: 0, marginTop: 10,
  },
  sliderRow: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginTop: 10,
  },
  sliderValue: {
    fontSize: 18, fontWeight: '800', color: C.orange,
  },
  slider: {
    width: '100%', height: 40, marginBottom: 4,
  },

  // Quarter picker
  quarterRow: { flexDirection: 'row', gap: 8, marginBottom: 12 },
  qBtn: {
    flex: 1, height: 40, borderRadius: 8,
    backgroundColor: C.card, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },
  qBtnActive:     { backgroundColor: C.red, borderColor: C.red },
  qBtnText:       { fontSize: 13, fontWeight: '700', color: C.textMuted },
  qBtnTextActive: { color: '#fff' },

  // Buttons
  updateBtn: {
    backgroundColor: 'rgba(29,66,138,0.25)', padding: 10,
    borderRadius: 10, alignItems: 'center',
    borderWidth: 1, borderColor: C.blue,
  },
  updateBtnText: { color: C.accentBlue, fontWeight: '800', fontSize: 13 },
  savedBtnText:  { color: C.liveGreen },
  removeBtn: {
    paddingHorizontal: 10, paddingVertical: 4,
    backgroundColor: 'rgba(201,8,42,0.15)', borderRadius: 8,
  },
  removeBtnText: { color: C.red, fontSize: 12, fontWeight: '700' },

  // Switch rows
  switchRow:   { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 4 },
  categoryRow: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', paddingVertical: 10,
    borderTopWidth: 1, borderColor: C.border,
  },
  rowLabel: { fontSize: 14, color: C.textPrimary, fontWeight: '600' },
  rowSub:   { fontSize: 12, color: C.textMuted, marginTop: 2 },

  statusDot: { width: 10, height: 10, borderRadius: 5 },
  emptyHint: { color: C.textMuted, fontSize: 13, fontStyle: 'italic', paddingVertical: 6 },

  // Sound picker
  soundRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingVertical: 11, paddingHorizontal: 6,
    borderBottomWidth: 1, borderColor: C.border,
  },
  soundRowSelected: { backgroundColor: 'rgba(255,107,0,0.07)', borderRadius: 8, borderBottomWidth: 0 },
  soundLabel:         { flex: 1, fontSize: 15, color: C.textMuted, fontWeight: '600' },
  soundLabelSelected: { color: C.textPrimary },

  // ── Modal ──────────────────────────────────────────────────────────────

  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.75)', justifyContent: 'flex-end' },
  modalSheet: {
    backgroundColor: C.card,
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    borderWidth: 1, borderColor: C.border,
    height: '85%',
  },
  modalHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 14,
    borderBottomWidth: 1, borderColor: C.border,
  },
  modalTitle: { fontSize: 16, fontWeight: '800', color: C.textPrimary },
  backBtn:    { flexDirection: 'row', alignItems: 'center', gap: 4, width: 70 },
  backBtnText:{ color: C.accentBlue, fontSize: 14, fontWeight: '700' },
  closeBtn:   { width: 70, alignItems: 'flex-end' },

  // Picker list rows
  pickerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 10, gap: 10,
  },
  pickerPlayerHeadshot: { width: 36, height: 36, borderRadius: 18 },
  pickerRowText:  { fontSize: 15, color: C.textPrimary, fontWeight: '600', flex: 1 },
  pickerRowSub:   { fontSize: 12, color: C.textMuted, marginTop: 2 },
  pickerRowRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pickerRowAbbr:  { fontSize: 13, color: C.textMuted },
  savedBadge:     { fontSize: 12, color: '#f5c518', fontWeight: '700' },
  separator:      { height: 1, backgroundColor: C.border, marginLeft: 16 },

  // Config area (step 2/3)
  configArea: { padding: 24 },
  configTitle:{ fontSize: 20, fontWeight: '800', color: C.textPrimary, marginBottom: 4 },
  configSub:  { fontSize: 13, color: C.textMuted, marginBottom: 8, lineHeight: 18 },
  configSummary: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: C.bg, borderRadius: 8, padding: 10, marginBottom: 20,
  },
  configSummaryText: { fontSize: 12, color: C.textMuted, flex: 1 },
  confirmBtn: {
    backgroundColor: C.blue, padding: 16,
    borderRadius: 14, alignItems: 'center',
  },
  confirmBtnDisabled: { opacity: 0.5 },
  confirmBtnText: { color: '#fff', fontWeight: '800', fontSize: 15 },
  cancelBtn: {
    marginTop: 12, padding: 14,
    borderRadius: 14, alignItems: 'center',
  },
  cancelBtnText: { color: C.textMuted, fontWeight: '700', fontSize: 14 },

  // Alert type toggle
  alertTypeRow: {
    flexDirection: 'row', gap: 10, marginTop: 10, marginBottom: 4,
  },
  alertTypeBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingVertical: 9, borderRadius: 10,
    backgroundColor: C.bg, borderWidth: 1, borderColor: C.border,
  },
  alertTypeBtnActive: { borderColor: C.red, backgroundColor: 'rgba(201,8,42,0.12)' },
  alertTypeIcon: { fontSize: 14 },
  alertTypeText: { fontSize: 12, fontWeight: '700', color: C.textMuted },
  alertTypeTextActive: { color: C.red },

  // ── App Guide button ───────────────────────────────────────────────
  guideBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 8, marginHorizontal: 16, marginTop: 4, marginBottom: 36,
    paddingVertical: 14, borderRadius: 14,
    backgroundColor: 'rgba(255,107,0,0.08)',
    borderWidth: 1, borderColor: 'rgba(255,107,0,0.25)',
  },
  guideBtnText: {
    color: C.orange, fontWeight: '800', fontSize: 14, letterSpacing: 0.5,
  },

  privacyBtn: {
    alignItems: 'center', marginHorizontal: 16, marginTop: 8, marginBottom: 8,
    paddingVertical: 10,
  },
  privacyBtnText: {
    color: C.textMuted, fontSize: 12, textDecorationLine: 'underline',
  },
  contactText: {
    color: C.textMuted, fontSize: 12, textAlign: 'center', marginBottom: 8,
  },
  copyTokenBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 5, marginBottom: 16,
  },
  copyTokenText: {
    color: C.textMuted, fontSize: 11,
  },

  infoOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.7)',
    alignItems: 'center', justifyContent: 'center', padding: 32,
  },
  infoBox: {
    width: '100%', backgroundColor: C.card,
    borderRadius: 20, borderWidth: 1, borderColor: C.border,
    padding: 28, alignItems: 'center',
  },
  infoTitle: {
    fontSize: 20, fontWeight: '900', color: C.textPrimary,
    letterSpacing: 0.5, marginBottom: 10,
  },
  infoMessage: {
    fontSize: 14, color: C.textMuted, textAlign: 'center',
    lineHeight: 20, marginBottom: 28,
  },
  infoOkBtn: {
    width: '100%', paddingVertical: 14, borderRadius: 12,
    backgroundColor: 'rgba(255,107,0,0.15)',
    borderWidth: 1, borderColor: 'rgba(255,107,0,0.4)',
    alignItems: 'center',
  },
  infoOkText: { color: C.orange, fontWeight: '800', fontSize: 15 },
});
