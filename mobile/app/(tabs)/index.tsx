import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Haptics from 'expo-haptics';
import messaging from '@react-native-firebase/messaging';
import notifee, { AndroidImportance, AndroidCategory, AndroidVisibility } from '@notifee/react-native';
import { registerStopHandler } from '../../alarmSoundHandler';
import { NativeModules } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTheme, type AppColors } from '../../context/ThemeContext';
import {
  ActivityIndicator,
  Animated,
  FlatList,
  RefreshControl,
  Image,
  LayoutAnimation,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  UIManager,
  View,
} from 'react-native';

import Slider from '@react-native-community/slider';
import { BasketballToggle } from '../../components/basketball-toggle';


import { SafeAreaView } from 'react-native-safe-area-context';

import { SkeletonGameCard } from '../../components/skeleton-card';
import { SERVER_URL } from '../../config';

// Enable LayoutAnimation on Android
if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

const REGISTER_TOKEN_URL = `${SERVER_URL}/register-token`;

// ─── Notification setup ───────────────────────────────────────────────────────

const ALARM_CHANNEL = 'classic_alarm';

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
}: {
  item: any;
  onPress: () => void;
  favoriteTeamIds: Set<string>;
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
          {awayStarred ? <Text style={styles.starBtnActive}>★</Text> : null}
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
          {homeStarred ? <Text style={styles.starBtnActive}>★</Text> : null}
        </View>
      </View>
    </TouchableOpacity>
  );
}

// ─── Shared token helpers (used by HomeScreen) ────────────────────────────────

/**
 * Set up Notifee channels and get the FCM push token.
 * Creates/recreates the alarm channel on every call so Android picks up
 * any channel config changes (Android caches channel settings otherwise).
 */
async function setupNotifee(): Promise<string | undefined> {
  try {
    if (Platform.OS === 'android') {
      const savedSound = (await AsyncStorage.getItem('nba_alarm_sound').catch(() => null)) ?? 'classic_alarm';
      // Notifee creates the base channel, then the native module overwrites it
      // with AudioAttributes.USAGE_ALARM so the sound plays even in vibrate mode.
      await notifee.deleteChannel(ALARM_CHANNEL).catch(() => {});
      await notifee.createChannel({
        id:         ALARM_CHANNEL,
        name:       'Classic Alarm',
        sound:      savedSound,
        importance: AndroidImportance.HIGH,
        bypassDnd:  true,
        visibility: AndroidVisibility.PUBLIC,
        vibration:  true,
        vibrationPattern: [100, 300, 200, 400],
      });
      // Patch channel to use STREAM_ALARM — bypasses vibrate/ringer-off mode
      await NativeModules.AlarmChannel?.patchChannel(ALARM_CHANNEL, savedSound);
    }

    const permission = await notifee.requestPermission();
    if (permission.authorizationStatus < 1) {
      console.warn('[Notifee] Notification permission denied');
      return undefined;
    }

    const token = await messaging().getToken();
    return token;
  } catch (error: any) {
    console.error('[Notifee] Setup error:', error?.message ?? error);
    return undefined;
  }
}

/**
 * Send the token to the Render server.
 * Always uses the hardcoded REGISTER_TOKEN_URL, never SERVER_URL,
 * so it works even when SERVER_URL points to localhost during dev.
 */
async function sendTokenToServer(token: string): Promise<boolean> {
  try {
    const resp = await fetch(REGISTER_TOKEN_URL, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ push_token: token, sound_channel: ALARM_CHANNEL }),
    });
    return resp.ok;
  } catch {
    return false;
  }
}

// ─── HomeScreen ───────────────────────────────────────────────────────────────

export default function HomeScreen() {
  const { colors: C } = useTheme();
  const styles = useMemo(() => makeStyles(C), [C]);
  // ── Games state ───────────────────────────────────────────────────
  const [games, setGames]       = useState<any[]>([]);
  const [loading, setLoading]   = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fetchError, setFetchError] = useState(false);
  const [expoPushToken, setExpoPushToken] = useState('');

  // ── Favorites state ───────────────────────────────────────────────
  const [favoriteTeamIds, setFavoriteTeamIds]     = useState<Set<string>>(new Set());
  const [favoritePlayerIds, setFavoritePlayerIds] = useState<Set<string>>(new Set());

  // ── Info modal (replaces system alert() for confirmations & errors) ─
  const [infoModal, setInfoModal] = useState<{ visible: boolean; title: string; message: string }>(
    { visible: false, title: '', message: '' },
  );
  const showInfo = (title: string, message: string) =>
    setInfoModal({ visible: true, title, message });
  const hideInfo = () =>
    setInfoModal(prev => ({ ...prev, visible: false }));

  // ── Modal / alarm tab state ───────────────────────────────────────
  const [selectedGame, setSelectedGame] = useState<any>(null);
  const [alarmTab, setAlarmTab]         = useState<'game' | 'player'>('game');

  // ── Alert type ────────────────────────────────────────────────────
  const [alertType, setAlertType] = useState<'alarm' | 'silent'>('alarm');

  // ── Game alarm state ──────────────────────────────────────────────
  const [maxDiff, setMaxDiff]           = useState(5);
  const [startQuarter, setStartQuarter] = useState('4');

  // ── Player alarm state ────────────────────────────────────────────
  const [teams, setTeams]                   = useState<any[]>([]);
  const [rosterLoading, setRosterLoading]   = useState(false);
  const [selectedPlayer, setSelectedPlayer] = useState<any>(null);
  // Multi-condition toggles (each fires independently)
  const [ptsGoalEnabled,    setPtsGoalEnabled]    = useState(true);
  const [ptsGoalThreshold,  setPtsGoalThreshold]  = useState(25);
  const [ptsEarlyEnabled,   setPtsEarlyEnabled]   = useState(false);
  const [earlyPtsThreshold, setEarlyPtsThreshold] = useState(15);
  const [earlyQuarter,      setEarlyQuarter]      = useState('3');
  const [tdWatchEnabled,    setTdWatchEnabled]    = useState(false);


  // ── Load favorites from server settings ──────────────────────────
  // token must be passed explicitly — never call without it or star
  // indicators will show another user's (or no user's) favorites.
  const loadFavorites = async (token: string) => {
    if (!token) return;
    try {
      const resp = await fetch(`${SERVER_URL}/settings?user_token=${encodeURIComponent(token)}`);
      if (!resp.ok) return;
      const prefs = await resp.json();
      setFavoriteTeamIds(new Set((prefs.FavoriteTeams ?? []).map((t: any) => t.team_id)));
      setFavoritePlayerIds(new Set(Object.keys(prefs.FavoritePlayers ?? {})));
    } catch (e) {
      console.error('loadFavorites error:', e);
    }
  };

  // ── Fetch games ──────────────────────────────────────────────────
  const fetchGames = async (silent = false) => {
    if (!silent) setLoading(true);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10_000);
    try {
      const resp = await fetch(`${SERVER_URL}/games`, { signal: controller.signal });
      if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
      const data = await resp.json();
      if (!silent) LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
      setGames(data.games || []);
      setFetchError(false);
      // Server recovered — stop auto-retry if running
      if (gamesRetryRef.current) {
        clearInterval(gamesRetryRef.current);
        gamesRetryRef.current = null;
      }
    } catch (error) {
      console.error('Games fetch error:', error);
      if (!silent) setFetchError(true);
      // Auto-retry every 15s until the server responds
      if (!gamesRetryRef.current) {
        gamesRetryRef.current = setInterval(() => fetchGames(true), 15_000);
      }
    } finally {
      clearTimeout(timer);
      if (!silent) setLoading(false);
    }
  };

  const onRefresh = async () => {
    setRefreshing(true);
    await fetchGames(false);
    setRefreshing(false);
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

  // ── Alarm audio helpers (native MediaPlayer via AlarmChannelModule) ──
  const playAlarmSound = async () => {
    const sound = (await AsyncStorage.getItem('nba_alarm_sound').catch(() => null)) ?? 'classic_alarm';
    NativeModules.AlarmChannel?.startAlarmSound(sound);
  };

  const stopAlarmSound = () => {
    NativeModules.AlarmChannel?.stopAlarmSound();
  };

  // ── Startup effect ────────────────────────────────────────────────
  const retryIntervalRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const gamesRetryRef      = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    // Set up Notifee channels + get FCM token, then register with server
    setupNotifee().then(async (token: string | undefined) => {
      if (!token) return;
      setExpoPushToken(token);
      // Persist token so _layout.tsx can read it as `old_token` when FCM rotates.
      AsyncStorage.setItem('nba_fcm_token', token).catch(() => {});
      // Load favorites only after the token is known so we fetch the correct user's data.
      loadFavorites(token);
      const success = await sendTokenToServer(token);
      if (!success) {
        retryIntervalRef.current = setInterval(async () => {
          const ok = await sendTokenToServer(token);
          if (ok) {
            clearInterval(retryIntervalRef.current!);
            retryIntervalRef.current = null;
          }
        }, 30_000);
      }
    });

    fetchGames();

    // Foreground: FCM data message arrives while app is open → display via Notifee
    const unsubMessage = messaging().onMessage(async remoteMessage => {
      const data = remoteMessage.data ?? {};
      if (data.alert_type === 'alarm') {
        const savedSound = (await AsyncStorage.getItem('nba_alarm_sound').catch(() => null)) ?? 'classic_alarm';
        await notifee.displayNotification({
          title: String(data.title ?? 'NBA Alarm'),
          body:  String(data.body  ?? ''),
          data:  { alert_type: 'alarm' },
          android: {
            channelId:        String(data.channel_id ?? ALARM_CHANNEL),
            sound:            savedSound,
            importance:       AndroidImportance.HIGH,
            category:         AndroidCategory.ALARM,
            visibility:       AndroidVisibility.PUBLIC,
            pressAction:      { id: 'default' },
            actions:          [{ title: 'DISMISS', pressAction: { id: 'dismiss' } }],
          },
        });
        playAlarmSound();
      } else {
        // Silent mode — regular notification, no sound
        await notifee.createChannel({ id: 'silent', name: 'Silent Updates', importance: AndroidImportance.DEFAULT });
        await notifee.displayNotification({
          title: String(data.title ?? 'NBA Update'),
          body:  String(data.body  ?? ''),
          data:  { alert_type: 'silent' },
          android: {
            channelId:   'silent',
            importance:  AndroidImportance.DEFAULT,
            pressAction: { id: 'default' },
          },
        });
      }
    });

    // Register stop handler so _layout.tsx can stop the alarm sound on DISMISS
    registerStopHandler(stopAlarmSound);

    // 60-second live-score auto-refresh
    const refreshInterval = setInterval(() => fetchGames(true), 60_000);

    return () => {
      unsubMessage();
      clearInterval(refreshInterval);
      if (retryIntervalRef.current) clearInterval(retryIntervalRef.current);
      if (gamesRetryRef.current) clearInterval(gamesRetryRef.current);
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
    setPtsGoalThreshold(25);
    setPtsEarlyEnabled(false);
    setEarlyPtsThreshold(15);
    setEarlyQuarter('3');
    setTdWatchEnabled(false);
  };

  // ── Start game alarm ─────────────────────────────────────────────
  const startGameMonitoring = async () => {
    if (!selectedGame) return;
    // Re-fetch token before sending alarm (ensures server has latest token)
    const freshToken = expoPushToken || (await messaging().getToken().catch(() => '')) || '';
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
          max_diff:      maxDiff,
          start_quarter: parseInt(startQuarter, 10),
          push_token:    freshToken,
          sound_channel: ALARM_CHANNEL,
          alert_type:    alertType,
        }),
      });
      if (response.ok) {

        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        showInfo('Alarm Set!', `Now watching ${selectedGame.name}.`);
      } else {
        showInfo('Server Error', 'Make sure the server is running.');
      }
    } catch {
      showInfo('Connection Error', `Could not reach the server at ${SERVER_URL}.`);
    } finally {
      closeModal();
    }
  };

  // ── Start player alarm ───────────────────────────────────────────
  const startPlayerMonitoring = async () => {
    if (!selectedGame || !selectedPlayer) return;
    if (!ptsGoalEnabled && !ptsEarlyEnabled && !tdWatchEnabled) {
      showInfo('No Condition Selected', 'Please enable at least one alarm condition.');
      return;
    }
    const freshToken = expoPushToken || (await messaging().getToken().catch(() => '')) || '';
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
      condition: 'pts_goal', pts_threshold: ptsGoalThreshold, quarter_deadline: 0,
      condition_type: 'points', threshold: ptsGoalThreshold, start_quarter: 0,
    });
    if (ptsEarlyEnabled) conditions.push({
      condition: 'pts_early', pts_threshold: earlyPtsThreshold, quarter_deadline: parseInt(earlyQuarter, 10),
      condition_type: 'points', threshold: earlyPtsThreshold, start_quarter: parseInt(earlyQuarter, 10),
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
      }

      if (successCount > 0) {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        showInfo('Alarm Set!', `${successCount} condition${successCount > 1 ? 's' : ''} active for ${selectedPlayer.name}.`);
      } else {
        showInfo('Server Error', 'Make sure the server is running.');
      }
    } catch {
      showInfo('Connection Error', `Could not reach the server at ${SERVER_URL}.`);
    } finally {
      closeModal();
    }
  };

  // ─── Render ───────────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={styles.container}>
      {/* Header */}
      <Text style={styles.header}>NBA Smart Alarm</Text>

      {/* Game list */}
      <Text style={[styles.sectionLabel, { paddingHorizontal: 16, marginBottom: 8 }]}>
        TODAY'S GAMES
      </Text>

      {loading ? (
        <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
          {[0, 1, 2, 3].map(i => <SkeletonGameCard key={i} />)}
        </ScrollView>
      ) : fetchError && games.length === 0 ? (
        <View style={styles.centeredMessage}>
          <Ionicons name="wifi-outline" size={56} color="#30363d" style={{ marginBottom: 16 }} />
          <Text style={styles.emptyTitle}>Connection Error</Text>
          <Text style={styles.emptyText}>Could not reach the server.</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={() => fetchGames()}>
            <Text style={styles.retryBtnText}>Retry</Text>
          </TouchableOpacity>
        </View>
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
            />
          )}
          contentContainerStyle={{ paddingBottom: 24 }}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#4d8cf5" />}
        />
      )}

      {/* ── Alarm Modal ── */}
      <Modal visible={!!selectedGame} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
          <ScrollView
            showsVerticalScrollIndicator={false}
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={{ paddingBottom: 8 }}
          >

            {/* Header row with X button */}
            <View style={styles.modalHeaderRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.modalSubtitle}>Set Alarm for</Text>
                <Text style={styles.modalGameName}>{selectedGame?.name}</Text>
              </View>
              <TouchableOpacity onPress={closeModal} style={styles.modalCloseBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

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
                <View style={styles.sliderRow}>
                  <Text style={styles.inputLabel}>Max Point Difference</Text>
                  <Text style={styles.sliderValue}>{maxDiff}</Text>
                </View>
                <Slider
                  style={styles.slider}
                  minimumValue={1} maximumValue={25} step={1}
                  value={maxDiff}
                  onValueChange={setMaxDiff}
                  minimumTrackTintColor={C.orange}
                  maximumTrackTintColor={C.border}
                  thumbTintColor={C.orange}
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
                  <ScrollView style={styles.playerList} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 12 }}>
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
                              onPress={() => setSelectedPlayer(isSelected ? null : player)}
                            >
                              <Image
                                source={{ uri: `https://a.espncdn.com/i/headshots/nba/players/full/${player.id}.png` }}
                                style={styles.playerHeadshot}
                              />
                              <Text style={styles.playerJersey}>
                                {player.jersey ? `#${player.jersey}` : '  '}
                              </Text>
                              <Text style={[styles.playerName, isSelected && styles.playerNameSelected]}>
                                {player.name}
                              </Text>
                              {favoritePlayerIds.has(player.id) ? <Text style={styles.starBtnActive}>★</Text> : null}
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
                      {favoritePlayerIds.has(selectedPlayer.id) ? (
                        <Text style={styles.starBtnActive}>★</Text>
                      ) : null}
                    </View>

                    <Text style={styles.inputLabel}>Alarm Conditions</Text>
                    <Text style={[styles.conditionHint, { marginTop: 4 }]}>Enable one or more — each fires independently</Text>

                    {/* ── Points Goal ── */}
                    <View style={styles.conditionCard}>
                      <View style={styles.conditionCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.conditionCardTitle}>Points Goal</Text>
                          <Text style={styles.conditionCardSub}>Alert when player hits a point total</Text>
                        </View>
                        <BasketballToggle
                          value={ptsGoalEnabled}
                          onValueChange={setPtsGoalEnabled}
                        />
                      </View>
                      {ptsGoalEnabled && (
                        <>
                          <View style={[styles.sliderRow, { marginTop: 10 }]}>
                            <Text style={styles.inputLabel}>Points threshold</Text>
                            <Text style={styles.sliderValue}>{ptsGoalThreshold}</Text>
                          </View>
                          <Slider
                            style={styles.slider}
                            minimumValue={1} maximumValue={80} step={1}
                            value={ptsGoalThreshold}
                            onValueChange={setPtsGoalThreshold}
                            minimumTrackTintColor={C.orange}
                            maximumTrackTintColor={C.border}
                            thumbTintColor={C.orange}
                          />
                        </>
                      )}
                    </View>

                    {/* ── Early Points ── */}
                    <View style={styles.conditionCard}>
                      <View style={styles.conditionCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.conditionCardTitle}>Early Points</Text>
                          <Text style={styles.conditionCardSub}>Alert if threshold is hit before a quarter ends</Text>
                        </View>
                        <BasketballToggle
                          value={ptsEarlyEnabled}
                          onValueChange={setPtsEarlyEnabled}
                        />
                      </View>
                      {ptsEarlyEnabled && (
                        <>
                          <View style={[styles.sliderRow, { marginTop: 10 }]}>
                            <Text style={styles.inputLabel}>Points threshold</Text>
                            <Text style={styles.sliderValue}>{earlyPtsThreshold}</Text>
                          </View>
                          <Slider
                            style={styles.slider}
                            minimumValue={1} maximumValue={80} step={1}
                            value={earlyPtsThreshold}
                            onValueChange={setEarlyPtsThreshold}
                            minimumTrackTintColor={C.orange}
                            maximumTrackTintColor={C.border}
                            thumbTintColor={C.orange}
                          />
                          <Text style={[styles.inputLabel, { marginTop: 4 }]}>Before end of quarter</Text>
                          <View style={styles.miniQuarterRow}>
                            {['1', '2', '3'].map(q => (
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
                        </>
                      )}
                    </View>

                    {/* ── Triple-Double Watch ── */}
                    <View style={styles.conditionCard}>
                      <View style={styles.conditionCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.conditionCardTitle}>Triple-Double Watch</Text>
                          <Text style={styles.conditionCardSub}>Alert when approaching 9+ in 3 stats (with 3+ min left)</Text>
                        </View>
                        <BasketballToggle
                          value={tdWatchEnabled}
                          onValueChange={setTdWatchEnabled}
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

          </ScrollView>
          </View>
        </View>
      </Modal>

      {/* ── Info / Confirmation Modal ──────────────────────────────── */}
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
    textAlign: 'center', paddingTop: 12, paddingBottom: 16, letterSpacing: 1.5,
  },

  sectionLabel: {
    fontSize: 11, fontWeight: '700', color: C.textMuted,
    letterSpacing: 1.5, marginBottom: 10,
  },

  centeredMessage: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  emptyTitle: { fontSize: 16, fontWeight: '700', color: C.textMuted, marginBottom: 6 },
  emptyText:  { fontSize: 13, color: C.textMuted, textAlign: 'center', paddingHorizontal: 32 },
  retryBtn:     { marginTop: 20, paddingHorizontal: 28, paddingVertical: 10, borderRadius: 10, backgroundColor: 'rgba(77,140,245,0.15)', borderWidth: 1, borderColor: C.accentBlue },
  retryBtnText: { color: C.accentBlue, fontWeight: '700', fontSize: 14 },

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
  modalHeaderRow: {
    flexDirection: 'row', alignItems: 'flex-start',
    marginBottom: 16,
  },
  modalCloseBtn: {
    padding: 4, marginTop: 2,
  },
  modalSubtitle: { fontSize: 13, color: C.textMuted, marginBottom: 2 },
  modalGameName: { fontSize: 18, fontWeight: '800', color: C.textPrimary },

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
  alarmTabActive:     { backgroundColor: C.orange },
  alarmTabText:       { fontSize: 13, fontWeight: '700', color: C.textMuted },
  alarmTabTextActive: { color: '#fff' },

  inputLabel: { fontSize: 12, color: C.textMuted, fontWeight: '700', marginBottom: 0, letterSpacing: 1 },
  sliderRow: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginTop: 10,
  },
  sliderValue: { fontSize: 18, fontWeight: '800', color: C.orange },
  slider: { width: '100%', height: 40, marginBottom: 8 },

  quarterRow:          { flexDirection: 'row', gap: 10, marginBottom: 20 },
  quarterButton: {
    flex: 1, height: 46, borderRadius: 10,
    backgroundColor: C.bg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },
  quarterButtonActive: { backgroundColor: C.orange, borderColor: C.orange },
  quarterText:         { fontSize: 14, fontWeight: '800', color: C.textMuted },
  quarterTextActive:   { color: '#fff' },

  startButton:      { backgroundColor: C.orange, padding: 16, borderRadius: 14, alignItems: 'center', marginTop: 4 },
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
  playerHeadshot:    { width: 32, height: 32, borderRadius: 16 },
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
    backgroundColor: C.cardAlt, borderWidth: 1, borderColor: C.border,
    borderRadius: 10, padding: 12, marginBottom: 10,
  },
  conditionCardHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  conditionCardTitle:  { fontSize: 14, fontWeight: '700', color: C.textPrimary },
  conditionCardSub:    { fontSize: 12, color: C.textMuted, marginTop: 2 },

  miniQuarterRow: { flexDirection: 'row', gap: 6, marginBottom: 8 },
  miniQBtn: {
    flex: 1, height: 38, borderRadius: 8,
    backgroundColor: C.bg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },

  // ── Info Modal ────────────────────────────────────────────────────
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
