import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Device from 'expo-device';
import * as Haptics from 'expo-haptics';
import * as Notifications from 'expo-notifications';
import * as TaskManager from 'expo-task-manager';
import { Audio, InterruptionModeAndroid, InterruptionModeIOS } from 'expo-av';
import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  AppState,
  AppStateStatus,
  FlatList,
  Image,
  LayoutAnimation,
  Linking,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  UIManager,
  View,
} from 'react-native';

import { BACKGROUND_NOTIFICATION_TASK } from '../_background-notification-task';

// ─── Alarm status key (AsyncStorage single source of truth) ───────────────────
const ALARM_STATUS_KEY = 'ALARM_STATUS';

// ─── Notification category ─────────────────────────────────────────────────
// Registers the 'alarm' category with a DISMISS action button that appears
// directly inside every alarm notification. Called once on startup.
async function registerAlarmCategory(): Promise<void> {
  try {
    await Notifications.setNotificationCategoryAsync('alarm', [
      {
        identifier: 'DISMISS_ALARM',
        buttonTitle: 'DISMISS',
        options: {
          // true = Android wakes the JS thread before firing the response listener.
          // Without this, a suspended background JS thread never receives the
          // DISMISS_ALARM action and the button appears to do nothing.
          opensAppToForeground: true,
          isDestructive:           true,   // red tint on iOS
          isAuthenticationRequired: false,
        },
      },
    ]);
    console.log('[Category] Alarm category registered.');
  } catch (e) {
    console.warn('[Category] Could not register alarm category:', e);
  }
}
import { SafeAreaView } from 'react-native-safe-area-context';

import { AlarmOverlay } from '../../components/alarm-overlay';
import { SkeletonGameCard } from '../../components/skeleton-card';
import { SERVER_URL } from '../../config';

// Enable LayoutAnimation on Android
if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

// ─── Hardcoded project ID (bypasses any app.json lookup issues) ───────────────
const EXPO_PROJECT_ID = '19104813-313d-40c1-975a-63b78d3ae7fc';
const REGISTER_TOKEN_URL = `${SERVER_URL}/register-token`;

// ─── Types ────────────────────────────────────────────────────────────────────

// ─── Notification setup ───────────────────────────────────────────────────────

const ALARM_CHANNEL = 'classic_alarm';
const ANDROID_CHANNELS = [
  { id: ALARM_CHANNEL, name: 'Classic Alarm', sound: 'classic_alarm.wav' },
];

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
    priority: Notifications.AndroidNotificationPriority.MAX,
  }),
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getTeams(event: any) {
  const competitors = event?.competitions?.[0]?.competitors ?? [];
  const away = competitors.find((c: any) => c.homeAway === 'away') ?? competitors[0] ?? {};
  const home = competitors.find((c: any) => c.homeAway === 'home') ?? competitors[1] ?? {};
  return { home, away };
}

/** Parse the ISO UTC date from ESPN and return { localTime, usDetail } strings. */
function getGameTimes(item: any): { localTime: string; usDetail: string } | null {
  const isoDate: string | undefined = item?.date;
  const detail: string | undefined  = item?.status?.type?.detail;
  if (!isoDate) return null;
  try {
    const d = new Date(isoDate);
    if (isNaN(d.getTime())) return null;
    const localTime = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    return { localTime, usDetail: detail ?? '' };
  } catch {
    return null;
  }
}

// ─── GameCard ─────────────────────────────────────────────────────────────────

function GameCard({
  item,
  onPress,
  favoriteTeamIds,
  onToggleFav,
}: {
  item: any;
  onPress: () => void;
  favoriteTeamIds: Set<string>;
  onToggleFav: (teamId: string, teamName: string) => void;
}) {
  const { home, away } = getTeams(item);
  const state: string  = item?.status?.type?.state ?? 'pre';
  const isLive       = state === 'in';
  const isFinal      = state === 'post';
  const isScheduled  = !isLive && !isFinal;
  const showScore    = isLive || isFinal;
  const gameTimes    = isScheduled ? getGameTimes(item) : null;

  const homeId = home?.team?.id?.toString() ?? '';
  const awayId = away?.team?.id?.toString() ?? '';
  const homeStarred = homeId ? favoriteTeamIds.has(homeId) : false;
  const awayStarred = awayId ? favoriteTeamIds.has(awayId) : false;

  // Pulsing live dot
  const dotOpacity = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (!isLive) return;
    const anim = Animated.loop(
      Animated.sequence([
        Animated.timing(dotOpacity, { toValue: 0.25, duration: 700, useNativeDriver: true }),
        Animated.timing(dotOpacity, { toValue: 1,    duration: 700, useNativeDriver: true }),
      ]),
    );
    anim.start();
    return () => anim.stop();
  }, [isLive]);

  return (
    <TouchableOpacity style={styles.gameCard} onPress={onPress} activeOpacity={0.75}>
      <View style={styles.cardHeader}>
        {isLive ? (
          <View style={styles.liveBadge}>
            <Animated.View style={[styles.liveDot, { opacity: dotOpacity }]} />
            <Text style={styles.liveText}>LIVE</Text>
          </View>
        ) : (
          <View style={styles.statusBadge}>
            <Text style={styles.statusBadgeText}>{isFinal ? 'FINAL' : 'SCHEDULED'}</Text>
          </View>
        )}

        {gameTimes ? (
          <View style={styles.timesBlock}>
            <Text style={styles.localTime}>{gameTimes.localTime}</Text>
            <Text style={styles.usTime}>{gameTimes.usDetail}</Text>
          </View>
        ) : (
          <Text style={styles.statusDetail}>{item.status?.type?.detail}</Text>
        )}
      </View>

      <View style={styles.teamsRow}>
        <View style={styles.teamBlock}>
          {away?.team?.logo
            ? <Image source={{ uri: away.team.logo }} style={styles.teamLogo} resizeMode="contain" />
            : <View style={[styles.teamLogo, styles.logoPlaceholder]} />}
          <Text style={styles.teamAbbr}>{away?.team?.abbreviation ?? '?'}</Text>
          {showScore && <Text style={styles.scoreText}>{away?.score ?? '—'}</Text>}
          {awayId ? (
            <TouchableOpacity
              onPress={() => onToggleFav(awayId, away?.team?.displayName ?? away?.team?.abbreviation ?? awayId)}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Text style={[styles.starBtn, awayStarred && styles.starBtnActive]}>
                {awayStarred ? '★' : '☆'}
              </Text>
            </TouchableOpacity>
          ) : null}
        </View>

        <View style={styles.vsDivider}>
          <Text style={styles.vsText}>VS</Text>
        </View>

        <View style={styles.teamBlock}>
          {home?.team?.logo
            ? <Image source={{ uri: home.team.logo }} style={styles.teamLogo} resizeMode="contain" />
            : <View style={[styles.teamLogo, styles.logoPlaceholder]} />}
          <Text style={styles.teamAbbr}>{home?.team?.abbreviation ?? '?'}</Text>
          {showScore && <Text style={styles.scoreText}>{home?.score ?? '—'}</Text>}
          {homeId ? (
            <TouchableOpacity
              onPress={() => onToggleFav(homeId, home?.team?.displayName ?? home?.team?.abbreviation ?? homeId)}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Text style={[styles.starBtn, homeStarred && styles.starBtnActive]}>
                {homeStarred ? '★' : '☆'}
              </Text>
            </TouchableOpacity>
          ) : null}
        </View>
      </View>
    </TouchableOpacity>
  );
}

// ─── Shared token helpers (used by HomeScreen) ────────────────────────────────

/**
 * Get the Expo push token. Hardcodes the projectId so it always works
 * regardless of how Constants.expoConfig resolves in the build.
 */
async function getPushToken(): Promise<string | undefined> {
  try {
    if (Platform.OS === 'android') {
      for (const ch of ANDROID_CHANNELS) {
        await Notifications.setNotificationChannelAsync(ch.id, {
          name:                 ch.name,
          importance:           Notifications.AndroidImportance.MAX,
          sound:                ch.sound,
          bypassDnd:            true,
          showBadge:            true,
          enableLights:         true,
          enableVibrate:        true,
          vibrationPattern:     [0, 300, 200, 400],
          lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
        });
      }
    }

    if (!Device.isDevice) {
      alert('Push notifications require a physical device.');
      return undefined;
    }

    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;
    if (existingStatus !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }
    if (finalStatus !== 'granted') {
      alert('Notification permission denied. Please enable it in Settings to receive alarms.');
      return undefined;
    }

    // Hardcoded projectId – never relies on app.json at runtime
    const projectId = EXPO_PROJECT_ID;
    console.log('[TokenReg] Using projectId:', projectId);
    const token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
    console.log('[TokenReg] Token obtained:', token);
    return token;
  } catch (error: any) {
    alert(`Token Error: ${error?.message ?? String(error)}`);
    console.error('[TokenReg] Error getting push token:', error);
    return undefined;
  }
}

/**
 * Send the token to the Render server.
 * Always uses the hardcoded REGISTER_TOKEN_URL, never SERVER_URL,
 * so it works even when SERVER_URL points to localhost during dev.
 */
async function sendTokenToServer(token: string): Promise<boolean> {
  console.log('[TokenReg] POST →', REGISTER_TOKEN_URL);
  try {
    const resp = await fetch(REGISTER_TOKEN_URL, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ push_token: token, sound_channel: ALARM_CHANNEL }),
    });
    if (resp.ok) {
      console.log('[TokenReg] Server confirmed token registration.');
      return true;
    }
    const text = await resp.text();
    console.warn('[TokenReg] Server rejected token. Status:', resp.status, text);
    return false;
  } catch (e: any) {
    console.warn('[TokenReg] Network error:', e?.message);
    return false;
  }
}

// ─── HomeScreen ───────────────────────────────────────────────────────────────

export default function HomeScreen() {
  // ── Games state ───────────────────────────────────────────────────
  const [games, setGames]     = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [expoPushToken, setExpoPushToken] = useState('');

  // ── Favorites state ───────────────────────────────────────────────
  const [favoriteTeamIds, setFavoriteTeamIds]     = useState<Set<string>>(new Set());
  const [favoritePlayerIds, setFavoritePlayerIds] = useState<Set<string>>(new Set());

  // ── Modal / alarm tab state ───────────────────────────────────────
  const [selectedGame, setSelectedGame] = useState<any>(null);
  const [alarmTab, setAlarmTab]         = useState<'game' | 'player'>('game');

  // ── Alert type ────────────────────────────────────────────────────
  const [alertType, setAlertType] = useState<'alarm' | 'silent'>('alarm');

  // ── Game alarm state ──────────────────────────────────────────────
  const [maxDiff, setMaxDiff]           = useState('5');
  const [startQuarter, setStartQuarter] = useState('4');

  // ── Player alarm state ────────────────────────────────────────────
  const [teams, setTeams]                   = useState<any[]>([]);
  const [rosterLoading, setRosterLoading]   = useState(false);
  const [selectedPlayer, setSelectedPlayer] = useState<any>(null);
  // Multi-condition toggles (each fires independently)
  const [ptsGoalEnabled,    setPtsGoalEnabled]    = useState(true);
  const [ptsGoalThreshold,  setPtsGoalThreshold]  = useState('25');
  const [ptsEarlyEnabled,   setPtsEarlyEnabled]   = useState(false);
  const [earlyPtsThreshold, setEarlyPtsThreshold] = useState('15');
  const [earlyQuarter,      setEarlyQuarter]      = useState('3');
  const [tdWatchEnabled,    setTdWatchEnabled]    = useState(false);

  // ── Android "Draw over other apps" banner ────────────────────────
  // Shows ONCE ever (AsyncStorage persists across app kills).
  const [showOverlayBanner, setShowOverlayBanner] = useState(false);

  // ── Alarm overlay + audio ─────────────────────────────────────────
  const [overlayVisible, setOverlayVisible] = useState(false);
  const [overlayTitle, setOverlayTitle]     = useState('');
  const [overlayBody, setOverlayBody]       = useState('');
  const soundRef        = useRef<Audio.Sound | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Load favorites from server settings ──────────────────────────
  const loadFavorites = async () => {
    try {
      const resp = await fetch(`${SERVER_URL}/settings`);
      if (!resp.ok) return;
      const prefs = await resp.json();
      setFavoriteTeamIds(new Set((prefs.FavoriteTeams ?? []).map((t: any) => t.team_id)));
      setFavoritePlayerIds(new Set(Object.keys(prefs.FavoritePlayers ?? {})));
    } catch (e) {
      console.error('loadFavorites error:', e);
    }
  };

  // ── Toggle favorite team ──────────────────────────────────────────
  const toggleTeamFavorite = async (teamId: string, teamName: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    const isCurrentlyFav = favoriteTeamIds.has(teamId);
    setFavoriteTeamIds(prev => {
      const next = new Set(prev);
      isCurrentlyFav ? next.delete(teamId) : next.add(teamId);
      return next;
    });
    try {
      if (isCurrentlyFav) {
        await fetch(`${SERVER_URL}/settings/teams/${teamId}`, { method: 'DELETE' });
      } else {
        await fetch(`${SERVER_URL}/settings/teams`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ team_id: teamId, team_name: teamName }),
        });
      }
    } catch (e) {
      console.error('toggleTeamFavorite error:', e);
    }
  };

  // ── Toggle favorite player ────────────────────────────────────────
  const togglePlayerFavorite = async (playerId: string, playerName: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    const isCurrentlyFav = favoritePlayerIds.has(playerId);
    setFavoritePlayerIds(prev => {
      const next = new Set(prev);
      isCurrentlyFav ? next.delete(playerId) : next.add(playerId);
      return next;
    });
    try {
      if (isCurrentlyFav) {
        await fetch(`${SERVER_URL}/settings/players/${playerId}`, { method: 'DELETE' });
      } else {
        await fetch(`${SERVER_URL}/settings/players`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ player_id: playerId, name: playerName }),
        });
      }
    } catch (e) {
      console.error('togglePlayerFavorite error:', e);
    }
  };

  // ── Fetch games ──────────────────────────────────────────────────
  const fetchGames = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const resp = await fetch(`${SERVER_URL}/games`);
      if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
      const data = await resp.json();
      if (!silent) LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
      setGames(data.games || []);
    } catch (error) {
      console.error('Games fetch error:', error);
      if (!silent) setGames([]);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  // ── Load roster ──────────────────────────────────────────────────
  const loadRoster = async () => {
    if (!selectedGame) return;
    setRosterLoading(true);
    try {
      const resp = await fetch(`${SERVER_URL}/game-summary?gameId=${selectedGame.id}`);
      if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
      const data = await resp.json();
      setTeams(data.teams || []);
    } catch (error) {
      console.error('Roster fetch error:', error);
      setTeams([]);
    } finally {
      setRosterLoading(false);
    }
  };

  // ── Check AsyncStorage for an alarm stored by the background task ────────
  const checkPendingAlarm = async () => {
    try {
      // If the user already dismissed the alarm (e.g. via notification action),
      // clear both keys and do not show the overlay.
      const alarmStatus = await AsyncStorage.getItem(ALARM_STATUS_KEY);
      if (alarmStatus === 'dismissed') {
        await AsyncStorage.multiRemove(['pendingAlarm', ALARM_STATUS_KEY]);
        return;
      }

      const raw = await AsyncStorage.getItem('pendingAlarm');
      if (!raw) return;
      const { title, body, ts } = JSON.parse(raw) as { title: string; body: string; ts: number };
      // Ignore stale alarms older than 5 minutes
      if (Date.now() - ts > 5 * 60 * 1000) {
        await AsyncStorage.multiRemove(['pendingAlarm', ALARM_STATUS_KEY]);
        return;
      }
      await AsyncStorage.removeItem('pendingAlarm');
      triggerAlarm(title, body);
    } catch (e) {
      console.warn('[PendingAlarm] Read error:', e);
    }
  };

  // ── Alarm audio helpers ───────────────────────────────────────────
  const triggerAlarm = async (title: string, body: string) => {
    setOverlayTitle(title);
    setOverlayBody(body);
    setOverlayVisible(true);

    // Mark alarm as active — DISMISS notification action will flip this to 'dismissed'
    await AsyncStorage.setItem(ALARM_STATUS_KEY, 'active').catch(() => {});

    try {
      await Audio.setAudioModeAsync({
        playsInSilentModeIOS:    true,
        staysActiveInBackground: true,
        shouldDuckAndroid:       false,
        interruptionModeAndroid: InterruptionModeAndroid.DoNotMix,
        interruptionModeIOS:     InterruptionModeIOS.DoNotMix,
      });
      const sound = new Audio.Sound();
      await sound.loadAsync(require('../../assets/sounds/classic_alarm.wav'));
      await sound.setIsLoopingAsync(true);
      await sound.setVolumeAsync(1.0);
      await sound.playAsync();
      soundRef.current = sound;
    } catch (e) {
      console.error('Alarm sound error:', e);
    }

    // Poll AsyncStorage every second — if DISMISS action was tapped the status
    // will change to 'dismissed' and we stop the alarm without needing the JS
    // notification response listener to fire.
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    pollIntervalRef.current = setInterval(async () => {
      try {
        const status = await AsyncStorage.getItem(ALARM_STATUS_KEY);
        if (status === 'dismissed') {
          console.log('[Poll] ALARM_STATUS=dismissed detected — stopping alarm.');
          await dismissAlarm();
        }
      } catch {}
    }, 1000);
  };

  const dismissAlarm = async () => {
    // Clear the poll first so it doesn't re-enter
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    setOverlayVisible(false);
    await AsyncStorage.setItem(ALARM_STATUS_KEY, 'dismissed').catch(() => {});
    if (soundRef.current) {
      try { await soundRef.current.stopAsync(); await soundRef.current.unloadAsync(); } catch {}
      soundRef.current = null;
    }
  };

  // ── Startup effect ────────────────────────────────────────────────
  useEffect(() => {
    // Show Draw Over Apps banner only on first launch
    if (Platform.OS === 'android') {
      AsyncStorage.getItem('hasSeenOverlayPrompt').then(seen => {
        if (!seen) setShowOverlayBanner(true);
      });
    }

    // Get token, save to state, register with server (retry every 30 s)
    getPushToken().then(async token => {
      if (!token) return;
      setExpoPushToken(token);

      // Register background notification task (safe to call if already registered)
      try {
        const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_NOTIFICATION_TASK).catch(() => false);
        if (!isRegistered) {
          await Notifications.registerTaskAsync(BACKGROUND_NOTIFICATION_TASK);
          console.log('[BGTask] Registered background notification task.');
        }
      } catch (e) {
        console.warn('[BGTask] Could not register background task:', e);
      }

      const success = await sendTokenToServer(token);
      if (!success) {
        const retryInterval = setInterval(async () => {
          const ok = await sendTokenToServer(token);
          if (ok) clearInterval(retryInterval);
        }, 30_000);
      }
    });

    fetchGames();
    loadFavorites();

    // Register alarm notification category with DISMISS action button
    registerAlarmCategory();

    // Cold-start: check AsyncStorage for alarm stored by background task
    checkPendingAlarm();

    // Foreground-only: notification arrives while app is in foreground
    const subReceived = Notifications.addNotificationReceivedListener(notification => {
      const data = notification.request.content.data as any;
      if (data?.alert_type === 'alarm') {
        triggerAlarm(
          notification.request.content.title ?? 'Alarm',
          notification.request.content.body  ?? '',
        );
      }
    });

    // NOTE: addNotificationResponseReceivedListener and getLastNotificationResponseAsync
    // are both handled in _layout.tsx at the root level so they remain active
    // regardless of which tab is visible or whether HomeScreen is mounted.

    // App foregrounded from background — pick up any alarm stored by background task
    const appStateSub = AppState.addEventListener('change', (nextState: AppStateStatus) => {
      if (nextState === 'active') {
        checkPendingAlarm();
      }
    });

    // 60-second live-score auto-refresh
    const refreshInterval = setInterval(() => fetchGames(true), 60_000);

    return () => {
      subReceived.remove();
      appStateSub.remove();
      clearInterval(refreshInterval);
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, []);

  // Auto-load roster when switching to player tab
  useEffect(() => {
    if (alarmTab === 'player' && selectedGame && teams.length === 0 && !rosterLoading) {
      loadRoster();
    }
  }, [alarmTab, selectedGame]);

  // ── Close modal helper ────────────────────────────────────────────
  const closeModal = () => {
    setSelectedGame(null);
    setAlarmTab('game');
    setAlertType('alarm');
    setTeams([]);
    setSelectedPlayer(null);
    setPtsGoalEnabled(true);
    setPtsGoalThreshold('25');
    setPtsEarlyEnabled(false);
    setEarlyPtsThreshold('15');
    setEarlyQuarter('3');
    setTdWatchEnabled(false);
  };

  // ── Start game alarm ─────────────────────────────────────────────
  const startGameMonitoring = async () => {
    if (!selectedGame) return;
    // Re-register token before sending alarm (ensures server has latest token)
    const freshToken = expoPushToken || (await getPushToken()) || '';
    if (freshToken && freshToken !== expoPushToken) {
      setExpoPushToken(freshToken);
      sendTokenToServer(freshToken);
    }
    try {
      const response = await fetch(`${SERVER_URL}/start-alarm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          game_id:       selectedGame.id,
          game_name:     selectedGame.name,
          max_diff:      parseInt(maxDiff, 10),
          start_quarter: parseInt(startQuarter, 10),
          push_token:    freshToken,
          sound_channel: ALARM_CHANNEL,
          alert_type:    alertType,
        }),
      });
      if (response.ok) {
        // Persist to PostgreSQL via /set_alarm
        const dbPayload = {
          user_token:     freshToken,
          target_id:      selectedGame.id,
          target_name:    selectedGame.name,
          condition_type: 'score_diff',
          threshold:      parseInt(maxDiff, 10),
          start_quarter:  parseInt(startQuarter, 10),
        };
        fetch(`${SERVER_URL}/set_alarm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(dbPayload),
        })
          .then(r => r.json())
          .then(data => console.log('[DB] Game alarm saved to PostgreSQL:', data))
          .catch(err => console.warn('[DB] /set_alarm failed (non-critical):', err));

        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        alert(`Game alarm set for ${selectedGame.name}!`);
      } else {
        alert('Server error. Make sure the server is running.');
      }
    } catch {
      alert(`Could not connect to ${SERVER_URL}`);
    } finally {
      closeModal();
    }
  };

  // ── Start player alarm ───────────────────────────────────────────
  const startPlayerMonitoring = async () => {
    if (!selectedGame || !selectedPlayer) return;
    if (!ptsGoalEnabled && !ptsEarlyEnabled && !tdWatchEnabled) {
      alert('Please enable at least one alarm condition.');
      return;
    }
    const freshToken = expoPushToken || (await getPushToken()) || '';
    if (freshToken && freshToken !== expoPushToken) {
      setExpoPushToken(freshToken);
      sendTokenToServer(freshToken);
    }

    // Build the list of enabled conditions — each fires independently
    const conditions: Array<{
      condition: string; pts_threshold: number; quarter_deadline: number;
      condition_type: string; threshold: number; start_quarter: number;
    }> = [];
    if (ptsGoalEnabled) conditions.push({
      condition: 'pts_goal', pts_threshold: parseInt(ptsGoalThreshold, 10), quarter_deadline: 0,
      condition_type: 'points', threshold: parseInt(ptsGoalThreshold, 10), start_quarter: 0,
    });
    if (ptsEarlyEnabled) conditions.push({
      condition: 'pts_early', pts_threshold: parseInt(earlyPtsThreshold, 10), quarter_deadline: parseInt(earlyQuarter, 10),
      condition_type: 'points', threshold: parseInt(earlyPtsThreshold, 10), start_quarter: parseInt(earlyQuarter, 10),
    });
    if (tdWatchEnabled) conditions.push({
      condition: 'triple_double', pts_threshold: 0, quarter_deadline: 0,
      condition_type: 'triple_double', threshold: 0, start_quarter: 0,
    });

    try {
      let successCount = 0;
      for (const cond of conditions) {
        const response = await fetch(`${SERVER_URL}/start-player-alarm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            game_id:          selectedGame.id,
            game_name:        selectedGame.name,
            player_id:        selectedPlayer.id,
            player_name:      selectedPlayer.name,
            push_token:       freshToken,
            sound_channel:    ALARM_CHANNEL,
            condition:        cond.condition,
            pts_threshold:    cond.pts_threshold,
            quarter_deadline: cond.quarter_deadline,
            alert_type:       alertType,
          }),
        });
        if (!response.ok) continue;
        successCount++;
        // Persist each condition as its own DB row
        fetch(`${SERVER_URL}/set_alarm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_token:     freshToken,
            target_id:      selectedPlayer.id,
            target_name:    selectedPlayer.name,
            condition_type: cond.condition_type,
            threshold:      cond.threshold,
            start_quarter:  cond.start_quarter,
          }),
        })
          .then(r => r.json())
          .then(data => console.log(`[DB] Saved ${cond.condition_type} alarm:`, data))
          .catch(err => console.warn('[DB] /set_alarm failed (non-critical):', err));
      }

      if (successCount > 0) {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        alert(`${successCount} alarm${successCount > 1 ? 's' : ''} set for ${selectedPlayer.name}!`);
      } else {
        alert('Server error. Make sure the server is running.');
      }
    } catch {
      alert(`Could not connect to ${SERVER_URL}`);
    } finally {
      closeModal();
    }
  };

  // ─── Render ───────────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={styles.container}>
      {/* Header */}
      <Text style={styles.header}>NBA Smart Alarm</Text>

      {/* ── Android "Draw over other apps" permission banner ─────────── */}
      {showOverlayBanner && (
        <View style={styles.overlayBanner}>
          <Ionicons name="warning-outline" size={18} color="#f0a500" style={{ marginRight: 8 }} />
          <Text style={styles.overlayBannerText} numberOfLines={2}>
            Enable "Display over other apps" for the alarm overlay to appear on the lock screen.
          </Text>
          <TouchableOpacity
            style={styles.overlayBannerBtn}
            onPress={() => {
              Linking.openSettings();
              setShowOverlayBanner(false);
              AsyncStorage.setItem('hasSeenOverlayPrompt', 'true');
            }}
          >
            <Text style={styles.overlayBannerBtnText}>Enable</Text>
          </TouchableOpacity>
          <TouchableOpacity
            onPress={() => {
              setShowOverlayBanner(false);
              AsyncStorage.setItem('hasSeenOverlayPrompt', 'true');
            }}
            hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
          >
            <Ionicons name="close" size={18} color="#8b949e" />
          </TouchableOpacity>
        </View>
      )}

      {/* Game list */}
      <Text style={[styles.sectionLabel, { paddingHorizontal: 16, marginBottom: 8 }]}>
        TODAY'S GAMES
      </Text>

      {loading ? (
        <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
          {[0, 1, 2, 3].map(i => <SkeletonGameCard key={i} />)}
        </ScrollView>
      ) : games.length === 0 ? (
        <View style={styles.centeredMessage}>
          <Ionicons name="basketball-outline" size={56} color="#30363d" style={{ marginBottom: 16 }} />
          <Text style={styles.emptyTitle}>No Games Today</Text>
          <Text style={styles.emptyText}>Check back tomorrow for the next slate of games.</Text>
        </View>
      ) : (
        <FlatList
          data={games}
          keyExtractor={(item: any) => item.id.toString()}
          renderItem={({ item }: { item: any }) => (
            <GameCard
              item={item}
              onPress={() => setSelectedGame(item)}
              favoriteTeamIds={favoriteTeamIds}
              onToggleFav={toggleTeamFavorite}
            />
          )}
          contentContainerStyle={{ paddingBottom: 24 }}
        />
      )}

      {/* ── Alarm Overlay ── */}
      <AlarmOverlay
        visible={overlayVisible}
        title={overlayTitle}
        body={overlayBody}
        onDismiss={dismissAlarm}
      />

      {/* ── Alarm Modal ── */}
      <Modal visible={!!selectedGame} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>

            {/* Game name */}
            <Text style={styles.modalSubtitle}>Set Alarm for</Text>
            <Text style={styles.modalGameName}>{selectedGame?.name}</Text>

            {/* Alert type toggle */}
            <View style={styles.alertTypeRow}>
              {(['alarm', 'silent'] as const).map(type => (
                <TouchableOpacity
                  key={type}
                  style={[styles.alertTypeBtn, alertType === type && styles.alertTypeBtnActive]}
                  onPress={() => setAlertType(type)}
                >
                  <Text style={styles.alertTypeIcon}>{type === 'alarm' ? '🚨' : '🔕'}</Text>
                  <Text style={[styles.alertTypeText, alertType === type && styles.alertTypeTextActive]}>
                    {type === 'alarm' ? 'Alarm' : 'Silent'}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            {/* Tab selector */}
            <View style={styles.alarmTabs}>
              {(['game', 'player'] as const).map(tab => (
                <TouchableOpacity
                  key={tab}
                  style={[styles.alarmTab, alarmTab === tab && styles.alarmTabActive]}
                  onPress={() => setAlarmTab(tab)}
                >
                  <Text style={[styles.alarmTabText, alarmTab === tab && styles.alarmTabTextActive]}>
                    {tab === 'game' ? 'Game Alarm' : 'Player Alarm'}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            {/* ── Game Alarm tab ── */}
            {alarmTab === 'game' && (
              <>
                <Text style={styles.inputLabel}>Max Point Difference</Text>
                <TextInput
                  style={styles.input}
                  keyboardType="numeric"
                  value={maxDiff}
                  onChangeText={setMaxDiff}
                  placeholderTextColor="#555"
                />

                <Text style={styles.inputLabel}>Start Monitoring from Quarter</Text>
                <View style={styles.quarterRow}>
                  {['1', '2', '3', '4'].map(q => (
                    <TouchableOpacity
                      key={q}
                      style={[styles.quarterButton, startQuarter === q && styles.quarterButtonActive]}
                      onPress={() => setStartQuarter(q)}
                    >
                      <Text style={[styles.quarterText, startQuarter === q && styles.quarterTextActive]}>
                        Q{q}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>

                <TouchableOpacity style={styles.startButton} onPress={startGameMonitoring}>
                  <Text style={styles.startButtonText}>Start Monitoring</Text>
                </TouchableOpacity>
              </>
            )}

            {/* ── Player Alarm tab ── */}
            {alarmTab === 'player' && (
              <>
                {rosterLoading ? (
                  <View style={styles.rosterLoading}>
                    <ActivityIndicator color="#4d8cf5" />
                    <Text style={styles.rosterLoadingText}>Loading roster...</Text>
                  </View>
                ) : teams.length === 0 ? (
                  <Text style={styles.emptyText}>
                    Roster currently unavailable. Try again closer to tip-off.
                  </Text>
                ) : (
                  <ScrollView style={styles.playerList} showsVerticalScrollIndicator={false}>
                    {teams.map(team => (
                      <View key={team.id}>
                        <View style={styles.teamHeader}>
                          {team.logo
                            ? <Image source={{ uri: team.logo }} style={styles.teamHeaderLogo} resizeMode="contain" />
                            : null}
                          <Text style={styles.teamHeaderName}>{team.abbreviation}</Text>
                        </View>

                        {team.players.map((player: any) => {
                          const isSelected = selectedPlayer?.id === player.id;
                          return (
                            <TouchableOpacity
                              key={player.id}
                              style={[styles.playerRow, isSelected && styles.playerRowSelected]}
                              onPress={() => setSelectedPlayer(player)}
                            >
                              <Text style={styles.playerJersey}>
                                {player.jersey ? `#${player.jersey}` : '  '}
                              </Text>
                              <Text style={[styles.playerName, isSelected && styles.playerNameSelected]}>
                                {player.name}
                              </Text>
                              <Text style={styles.playerPos}>{player.position}</Text>
                            </TouchableOpacity>
                          );
                        })}
                      </View>
                    ))}
                  </ScrollView>
                )}

                {selectedPlayer && (
                  <>
                    <View style={styles.selectedPlayerHeader}>
                      <Text style={styles.selectedPlayerLabel}>
                        {selectedPlayer.name}
                      </Text>
                      <TouchableOpacity
                        onPress={() => togglePlayerFavorite(selectedPlayer.id, selectedPlayer.name)}
                        style={styles.playerStarBtn}
                        hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                      >
                        <Text style={[styles.starBtn, favoritePlayerIds.has(selectedPlayer.id) && styles.starBtnActive]}>
                          {favoritePlayerIds.has(selectedPlayer.id) ? '★' : '☆'}
                        </Text>
                      </TouchableOpacity>
                    </View>

                    <Text style={styles.inputLabel}>Alarm Conditions</Text>
                    <Text style={styles.conditionHint}>Enable one or more — each fires independently</Text>

                    {/* ── Points Goal ── */}
                    <View style={styles.conditionCard}>
                      <View style={styles.conditionCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.conditionCardTitle}>Points Goal</Text>
                          <Text style={styles.conditionCardSub}>Alert when player hits a point total</Text>
                        </View>
                        <Switch
                          value={ptsGoalEnabled}
                          onValueChange={setPtsGoalEnabled}
                          trackColor={{ false: C.border, true: C.blue }}
                          thumbColor={ptsGoalEnabled ? C.blue : C.textMuted}
                        />
                      </View>
                      {ptsGoalEnabled && (
                        <TextInput
                          style={[styles.input, { marginTop: 10 }]}
                          keyboardType="numeric"
                          value={ptsGoalThreshold}
                          onChangeText={setPtsGoalThreshold}
                          placeholder="25"
                          placeholderTextColor="#555"
                        />
                      )}
                    </View>

                    {/* ── Early Points ── */}
                    <View style={styles.conditionCard}>
                      <View style={styles.conditionCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.conditionCardTitle}>Early Points</Text>
                          <Text style={styles.conditionCardSub}>Alert if threshold is hit before a quarter ends</Text>
                        </View>
                        <Switch
                          value={ptsEarlyEnabled}
                          onValueChange={setPtsEarlyEnabled}
                          trackColor={{ false: C.border, true: C.blue }}
                          thumbColor={ptsEarlyEnabled ? C.blue : C.textMuted}
                        />
                      </View>
                      {ptsEarlyEnabled && (
                        <View style={[styles.earlyRow, { marginBottom: 0, marginTop: 10 }]}>
                          <View style={styles.earlyField}>
                            <Text style={styles.inputLabel}>Points</Text>
                            <TextInput
                              style={[styles.input, styles.inputSmall]}
                              keyboardType="numeric"
                              value={earlyPtsThreshold}
                              onChangeText={setEarlyPtsThreshold}
                              placeholderTextColor="#555"
                            />
                          </View>
                          <Text style={styles.earlyBy}>by</Text>
                          <View style={styles.earlyField}>
                            <Text style={styles.inputLabel}>Before Q</Text>
                            <View style={styles.miniQuarterRow}>
                              {['2', '3', '4'].map(q => (
                                <TouchableOpacity
                                  key={q}
                                  style={[styles.miniQBtn, earlyQuarter === q && styles.quarterButtonActive]}
                                  onPress={() => setEarlyQuarter(q)}
                                >
                                  <Text style={[styles.quarterText, earlyQuarter === q && styles.quarterTextActive]}>
                                    Q{q}
                                  </Text>
                                </TouchableOpacity>
                              ))}
                            </View>
                          </View>
                        </View>
                      )}
                    </View>

                    {/* ── Triple-Double Watch ── */}
                    <View style={styles.conditionCard}>
                      <View style={styles.conditionCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.conditionCardTitle}>Triple-Double Watch</Text>
                          <Text style={styles.conditionCardSub}>Alert when approaching 9+ in 3 stats</Text>
                        </View>
                        <Switch
                          value={tdWatchEnabled}
                          onValueChange={setTdWatchEnabled}
                          trackColor={{ false: C.border, true: C.blue }}
                          thumbColor={tdWatchEnabled ? C.blue : C.textMuted}
                        />
                      </View>
                    </View>

                    <TouchableOpacity style={styles.startButton} onPress={startPlayerMonitoring}>
                      <Text style={styles.startButtonText}>Start Monitoring</Text>
                    </TouchableOpacity>
                  </>
                )}
              </>
            )}

            {/* Cancel */}
            <TouchableOpacity style={styles.cancelButton} onPress={closeModal}>
              <Text style={styles.cancelButtonText}>Cancel</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const C = {
  bg:          '#0d1117',
  card:        '#161b22',
  border:      '#30363d',
  blue:        '#1d428a',
  red:         '#c9082a',
  textPrimary: '#e6edf3',
  textMuted:   '#8b949e',
  liveGreen:   '#3fb950',
  accentBlue:  '#4d8cf5',
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },

  header: {
    fontSize: 24, fontWeight: '800', color: C.textPrimary,
    textAlign: 'center', paddingTop: 12, paddingBottom: 16, letterSpacing: 1.5,
  },

  sectionLabel: {
    fontSize: 11, fontWeight: '700', color: C.textMuted,
    letterSpacing: 1.5, marginBottom: 10,
  },

  centeredMessage: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  emptyTitle: { fontSize: 16, fontWeight: '700', color: C.textMuted, marginBottom: 6 },
  emptyText:  { fontSize: 13, color: C.textMuted, textAlign: 'center', paddingHorizontal: 32 },

  gameCard: {
    backgroundColor: C.card, borderRadius: 16,
    marginHorizontal: 16, marginBottom: 12,
    padding: 16, borderWidth: 1, borderColor: C.border,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 6,
  },
  cardHeader: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginBottom: 14,
  },
  liveBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    backgroundColor: 'rgba(63,185,80,0.15)',
    borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3,
  },
  liveDot:  { width: 6, height: 6, borderRadius: 3, backgroundColor: C.liveGreen },
  liveText: { fontSize: 11, color: C.liveGreen, fontWeight: '800', letterSpacing: 1 },
  statusBadge: {
    backgroundColor: 'rgba(77,140,245,0.12)',
    borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3,
  },
  statusBadgeText: { fontSize: 11, color: C.accentBlue, fontWeight: '700', letterSpacing: 1 },
  statusDetail:    { fontSize: 12, color: C.textMuted },

  timesBlock: { alignItems: 'flex-end' },
  localTime:  { fontSize: 15, fontWeight: '800', color: C.textPrimary },
  usTime:     { fontSize: 11, color: C.textMuted, marginTop: 1 },

  teamsRow:        { flexDirection: 'row', alignItems: 'center' },
  teamBlock:       { flex: 1, alignItems: 'center', gap: 6 },
  teamLogo:        { width: 56, height: 56 },
  logoPlaceholder: { backgroundColor: C.border, borderRadius: 28 },
  teamAbbr:        { fontSize: 14, fontWeight: '800', color: C.textPrimary, letterSpacing: 0.5 },
  scoreText:       { fontSize: 32, fontWeight: '800', color: C.textPrimary, lineHeight: 36 },
  vsDivider:       { width: 40, alignItems: 'center' },
  vsText:          { fontSize: 12, color: C.textMuted, fontWeight: '700', letterSpacing: 1 },

  starBtn:       { fontSize: 22, color: C.textMuted },
  starBtnActive: { color: '#f5c518' },

  modalOverlay: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.75)' },
  modalContent: {
    backgroundColor: C.card,
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    borderWidth: 1, borderColor: C.border,
    padding: 24, paddingBottom: 40,
    maxHeight: '90%',
  },
  modalSubtitle: { fontSize: 13, color: C.textMuted, marginBottom: 2 },
  modalGameName: { fontSize: 18, fontWeight: '800', color: C.textPrimary, marginBottom: 16 },

  alertTypeRow: { flexDirection: 'row', gap: 10, marginBottom: 16 },
  alertTypeBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingVertical: 9, borderRadius: 10,
    backgroundColor: C.bg, borderWidth: 1, borderColor: C.border,
  },
  alertTypeBtnActive: { borderColor: C.red, backgroundColor: 'rgba(201,8,42,0.12)' },
  alertTypeIcon: { fontSize: 14 },
  alertTypeText:       { fontSize: 12, fontWeight: '700', color: C.textMuted },
  alertTypeTextActive: { color: C.red },

  alarmTabs: {
    flexDirection: 'row', marginBottom: 20,
    backgroundColor: C.bg, borderRadius: 10, overflow: 'hidden',
    borderWidth: 1, borderColor: C.border,
  },
  alarmTab:           { flex: 1, paddingVertical: 9, alignItems: 'center' },
  alarmTabActive:     { backgroundColor: C.blue },
  alarmTabText:       { fontSize: 13, fontWeight: '700', color: C.textMuted },
  alarmTabTextActive: { color: '#fff' },

  inputLabel: { fontSize: 12, color: C.textMuted, fontWeight: '700', marginBottom: 8, letterSpacing: 1 },
  input: {
    borderBottomWidth: 2, borderColor: C.blue,
    marginBottom: 20, fontSize: 28,
    color: C.textPrimary, paddingVertical: 4,
  },
  inputSmall: { fontSize: 22, marginBottom: 0 },

  quarterRow:          { flexDirection: 'row', gap: 10, marginBottom: 20 },
  quarterButton: {
    flex: 1, height: 46, borderRadius: 10,
    backgroundColor: C.bg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },
  quarterButtonActive: { backgroundColor: C.red, borderColor: C.red },
  quarterText:         { fontSize: 14, fontWeight: '800', color: C.textMuted },
  quarterTextActive:   { color: '#fff' },

  startButton:      { backgroundColor: C.blue, padding: 16, borderRadius: 14, alignItems: 'center', marginTop: 4 },
  startButtonText:  { color: '#fff', fontWeight: '800', fontSize: 15 },
  cancelButton:     { padding: 14, alignItems: 'center', marginTop: 4 },
  cancelButtonText: { color: C.textMuted, fontSize: 15 },

  playerList: { maxHeight: 220, marginBottom: 12 },

  rosterLoading: {
    height: 80, justifyContent: 'center', alignItems: 'center',
    flexDirection: 'row', gap: 10,
  },
  rosterLoadingText: { color: C.textMuted, fontSize: 14 },

  teamHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 6, paddingHorizontal: 2, marginTop: 6,
    borderBottomWidth: 1, borderColor: C.border,
  },
  teamHeaderLogo: { width: 20, height: 20 },
  teamHeaderName: { fontSize: 11, fontWeight: '800', color: C.accentBlue, letterSpacing: 1.5 },

  playerRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 8, paddingHorizontal: 6,
    borderRadius: 8,
  },
  playerRowSelected: { backgroundColor: 'rgba(29,66,138,0.25)' },
  playerJersey:      { fontSize: 12, color: C.textMuted, width: 30, textAlign: 'right' },
  playerName:        { flex: 1, fontSize: 14, color: C.textPrimary, fontWeight: '600' },
  playerNameSelected:{ color: C.accentBlue },
  playerPos:         { fontSize: 12, color: C.textMuted, width: 28, textAlign: 'center' },

  selectedPlayerHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: 'rgba(29,66,138,0.15)',
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8, marginBottom: 12,
  },
  selectedPlayerLabel: { fontSize: 15, fontWeight: '800', color: C.textPrimary },
  playerStarBtn:       { padding: 4 },

  conditionHint: { fontSize: 12, color: C.textMuted, marginTop: -8, marginBottom: 12 },
  conditionCard: {
    backgroundColor: C.bg, borderWidth: 1, borderColor: C.border,
    borderRadius: 10, padding: 12, marginBottom: 10,
  },
  conditionCardHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  conditionCardTitle:  { fontSize: 14, fontWeight: '700', color: C.textPrimary },
  conditionCardSub:    { fontSize: 12, color: C.textMuted, marginTop: 2 },

  earlyRow:  { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 16 },
  earlyField:{ flex: 1 },
  earlyBy:   { fontSize: 14, color: C.textMuted, marginTop: 20 },

  miniQuarterRow: { flexDirection: 'row', gap: 6 },
  miniQBtn: {
    flex: 1, height: 38, borderRadius: 8,
    backgroundColor: C.bg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },

  overlayBanner: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: 'rgba(240,165,0,0.1)',
    borderColor: '#f0a500', borderWidth: 1,
    borderRadius: 10, marginHorizontal: 16, marginBottom: 10,
    paddingHorizontal: 10, paddingVertical: 8,
  },
  overlayBannerText: { flex: 1, fontSize: 12, color: '#e6edf3', lineHeight: 16 },
  overlayBannerBtn: {
    backgroundColor: '#f0a500', borderRadius: 6,
    paddingHorizontal: 10, paddingVertical: 4, marginHorizontal: 8,
  },
  overlayBannerBtnText: { fontSize: 12, fontWeight: '700', color: '#000' },
});
