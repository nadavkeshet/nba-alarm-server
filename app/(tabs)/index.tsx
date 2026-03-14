import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Haptics from 'expo-haptics';
import * as Notifications from 'expo-notifications';
import { Audio, InterruptionModeAndroid, InterruptionModeIOS } from 'expo-av';
import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  FlatList,
  Image,
  LayoutAnimation,
  Linking,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  UIManager,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AlarmOverlay } from '../../components/alarm-overlay';
import { SkeletonGameCard } from '../../components/skeleton-card';
import { SERVER_URL } from '../../config';

// Enable LayoutAnimation on Android
if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

// ─── Types ────────────────────────────────────────────────────────────────────

type PlayerCondition = 'pts_goal' | 'pts_early' | 'triple_double';

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

        {/* Right side of header: live/final show detail; scheduled shows dual times */}
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

// ─── HomeScreen ───────────────────────────────────────────────────────────────

export default function HomeScreen() {
  // ── Games state ───────────────────────────────────────────────────────
  const [games, setGames]     = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [expoPushToken, setExpoPushToken] = useState('');

  // ── Favorites state ───────────────────────────────────────────────────
  const [favoriteTeamIds, setFavoriteTeamIds]     = useState<Set<string>>(new Set());
  const [favoritePlayerIds, setFavoritePlayerIds] = useState<Set<string>>(new Set());

  // ── Modal / alarm tab state ───────────────────────────────────────────
  const [selectedGame, setSelectedGame] = useState<any>(null);
  const [alarmTab, setAlarmTab]         = useState<'game' | 'player'>('game');

  // ── Alert type ────────────────────────────────────────────────────────
  const [alertType, setAlertType] = useState<'alarm' | 'silent'>('alarm');

  // ── Game alarm state ──────────────────────────────────────────────────
  const [maxDiff, setMaxDiff]           = useState('5');
  const [startQuarter, setStartQuarter] = useState('4');

  // ── Player alarm state ────────────────────────────────────────────────
  const [teams, setTeams]                   = useState<any[]>([]);
  const [rosterLoading, setRosterLoading]   = useState(false);
  const [selectedPlayer, setSelectedPlayer] = useState<any>(null);
  const [playerCondition, setPlayerCondition] = useState<PlayerCondition>('pts_goal');
  const [ptsThreshold, setPtsThreshold]       = useState('20');
  const [quarterDeadline, setQuarterDeadline] = useState('3');

  // ── Android "Draw over other apps" banner ────────────────────────────
  // Shows once per session on Android so the user can grant the permission
  // needed for the alarm to appear over the lock screen.
  const [showOverlayBanner, setShowOverlayBanner] = useState(Platform.OS === 'android');

  // ── Alarm overlay + audio ─────────────────────────────────────────────
  const [overlayVisible, setOverlayVisible] = useState(false);
  const [overlayTitle, setOverlayTitle]     = useState('');
  const [overlayBody, setOverlayBody]       = useState('');
  const soundRef = useRef<Audio.Sound | null>(null);

  // ── Push registration ────────────────────────────────────────────────
  async function registerForPushNotificationsAsync() {
    if (Platform.OS === 'android') {
      for (const ch of ANDROID_CHANNELS) {
        await Notifications.setNotificationChannelAsync(ch.id, {
          name:                 ch.name,
          importance:           Notifications.AndroidImportance.MAX,
          sound:                ch.sound,
          bypassDnd:            true,   // plays even in Do-Not-Disturb
          showBadge:            true,
          vibrationPattern:     [0, 300, 200, 400],
          lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
        });
      }
    }
    if (!Device.isDevice) {
      alert('Push notifications require a physical device.');
      return;
    }
    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;
    if (existingStatus !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }
    if (finalStatus !== 'granted') {
      alert('Please grant notification permissions to receive alarms.');
      return;
    }
    const projectId: string = Constants.expoConfig?.extra?.eas?.projectId;
    if (!projectId) {
      alert('EAS projectId not found in app.json → extra.eas.projectId');
      return;
    }
    const token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
    console.log('Expo Push Token:', token);
    return token;
  }

  // ── Load favorites from server settings ──────────────────────────────
  const loadFavorites = async () => {
    try {
      const resp = await fetch(`${SERVER_URL}/settings`);
      if (!resp.ok) return;
      const prefs = await resp.json();
      setFavoriteTeamIds(new Set((prefs.FavoriteTeams ?? []).map((t: any) => t.team_id)));
      setFavoritePlayerIds(new Set((prefs.FavoritePlayers ?? []).map((p: any) => p.player_id)));
    } catch (e) {
      console.error('loadFavorites error:', e);
    }
  };

  // ── Toggle favorite team ──────────────────────────────────────────────
  const toggleTeamFavorite = async (teamId: string, teamName: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    const isCurrentlyFav = favoriteTeamIds.has(teamId);
    // Optimistic update
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

  // ── Toggle favorite player ────────────────────────────────────────────
  const togglePlayerFavorite = async (playerId: string, playerName: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    const isCurrentlyFav = favoritePlayerIds.has(playerId);
    // Optimistic update
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

  // ── Fetch games ──────────────────────────────────────────────────────
  // silent=true → background auto-refresh; no skeleton, scores update in place.
  // silent=false (default) → initial/manual load; shows skeleton loader.
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

  // ── Load roster ──────────────────────────────────────────────────────
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

  // ── Alarm audio helpers ───────────────────────────────────────────────
  const triggerAlarm = async (title: string, body: string) => {
    setOverlayTitle(title);
    setOverlayBody(body);
    setOverlayVisible(true);
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
  };

  const dismissAlarm = async () => {
    setOverlayVisible(false);
    if (soundRef.current) {
      try { await soundRef.current.stopAsync(); await soundRef.current.unloadAsync(); } catch {}
      soundRef.current = null;
    }
  };

  useEffect(() => {
    registerForPushNotificationsAsync().then(token => setExpoPushToken(token ?? ''));
    fetchGames();
    loadFavorites();

    // ── Cold-start / killed-app: app was launched by tapping a notification ──
    // addNotificationResponseReceivedListener won't fire for this case, so we
    // read the last response that brought the app to foreground instead.
    Notifications.getLastNotificationResponseAsync().then(response => {
      if (!response) return;
      const data = response.notification.request.content.data as any;
      if (data?.alert_type === 'alarm') {
        triggerAlarm(
          response.notification.request.content.title ?? 'Alarm',
          response.notification.request.content.body  ?? '',
        );
      }
    });

    // ── Foreground notification ───────────────────────────────────────────────
    const subReceived = Notifications.addNotificationReceivedListener(notification => {
      const data = notification.request.content.data as any;
      if (data?.alert_type === 'alarm') {
        triggerAlarm(
          notification.request.content.title ?? 'Alarm',
          notification.request.content.body  ?? '',
        );
      }
    });

    // ── Background tap (app suspended, not killed) ────────────────────────────
    const subResponse = Notifications.addNotificationResponseReceivedListener(response => {
      const data = response.notification.request.content.data as any;
      if (data?.alert_type === 'alarm') {
        triggerAlarm(
          response.notification.request.content.title ?? 'Alarm',
          response.notification.request.content.body  ?? '',
        );
      }
    });

    // ── 60-second live-score auto-refresh ─────────────────────────────────────
    // Uses silent=true so the skeleton never re-appears; scores update in place.
    const refreshInterval = setInterval(() => fetchGames(true), 60_000);

    return () => {
      subReceived.remove();
      subResponse.remove();
      clearInterval(refreshInterval);
    };
  }, []);

  // Auto-load roster when switching to player tab
  useEffect(() => {
    if (alarmTab === 'player' && selectedGame && teams.length === 0 && !rosterLoading) {
      loadRoster();
    }
  }, [alarmTab, selectedGame]);

  // ── Close modal helper (resets all alarm state) ──────────────────────
  const closeModal = () => {
    setSelectedGame(null);
    setAlarmTab('game');
    setAlertType('alarm');
    setTeams([]);
    setSelectedPlayer(null);
    setPlayerCondition('pts_goal');
  };

  // ── Start game alarm ─────────────────────────────────────────────────
  const startGameMonitoring = async () => {
    if (!selectedGame) return;
    try {
      const response = await fetch(`${SERVER_URL}/start-alarm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          game_id:       selectedGame.id,
          game_name:     selectedGame.name,
          max_diff:      parseInt(maxDiff, 10),
          start_quarter: parseInt(startQuarter, 10),
          push_token:    expoPushToken,
          sound_channel: ALARM_CHANNEL,
          alert_type:    alertType,
        }),
      });
      if (response.ok) {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        alert(`Game alarm set for ${selectedGame.name}!`);
      } else {
        alert('Server error. Make sure the Python server is running.');
      }
    } catch {
      alert(`Could not connect to ${SERVER_URL}\nCheck your Wi-Fi and server IP.`);
    } finally {
      closeModal();
    }
  };

  // ── Start player alarm ───────────────────────────────────────────────
  const startPlayerMonitoring = async () => {
    if (!selectedGame || !selectedPlayer) return;
    try {
      const response = await fetch(`${SERVER_URL}/start-player-alarm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          game_id:          selectedGame.id,
          game_name:        selectedGame.name,
          player_id:        selectedPlayer.id,
          player_name:      selectedPlayer.name,
          push_token:       expoPushToken,
          sound_channel:    ALARM_CHANNEL,
          condition:        playerCondition,
          pts_threshold:    parseInt(ptsThreshold, 10),
          quarter_deadline: parseInt(quarterDeadline, 10),
          alert_type:       alertType,
        }),
      });
      if (response.ok) {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        alert(`Player alarm set for ${selectedPlayer.name}!`);
      } else {
        alert('Server error. Make sure the Python server is running.');
      }
    } catch {
      alert(`Could not connect to ${SERVER_URL}\nCheck your Wi-Fi and server IP.`);
    } finally {
      closeModal();
    }
  };

  // ─── Render ───────────────────────────────────────────────────────────
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
            onPress={() => Linking.openSettings()}
          >
            <Text style={styles.overlayBannerBtnText}>Enable</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setShowOverlayBanner(false)} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
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

      {/* ── Alarm Overlay (full-screen when alert_type === "alarm") ── */}
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
                {/* Player list */}
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
                        {/* Team header */}
                        <View style={styles.teamHeader}>
                          {team.logo
                            ? <Image source={{ uri: team.logo }} style={styles.teamHeaderLogo} resizeMode="contain" />
                            : null}
                          <Text style={styles.teamHeaderName}>{team.abbreviation}</Text>
                        </View>

                        {/* Players */}
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

                {/* Condition config — only shown once a player is selected */}
                {selectedPlayer && (
                  <>
                    {/* Selected player banner with favorite star */}
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

                    {/* Condition buttons */}
                    <Text style={styles.inputLabel}>Alarm Condition</Text>
                    <View style={styles.conditionRow}>
                      {([
                        { key: 'pts_goal',      label: 'Points Goal'    },
                        { key: 'pts_early',     label: 'Early Points'   },
                        { key: 'triple_double', label: 'Near Triple-Dbl'},
                      ] as { key: PlayerCondition; label: string }[]).map(({ key, label }) => (
                        <TouchableOpacity
                          key={key}
                          style={[styles.conditionBtn, playerCondition === key && styles.conditionBtnActive]}
                          onPress={() => setPlayerCondition(key)}
                        >
                          <Text style={[styles.conditionBtnText, playerCondition === key && styles.conditionBtnTextActive]}>
                            {label}
                          </Text>
                        </TouchableOpacity>
                      ))}
                    </View>

                    {/* Condition-specific inputs */}
                    {playerCondition === 'pts_goal' && (
                      <>
                        <Text style={styles.inputLabel}>Points Threshold</Text>
                        <TextInput
                          style={styles.input}
                          keyboardType="numeric"
                          value={ptsThreshold}
                          onChangeText={setPtsThreshold}
                          placeholderTextColor="#555"
                        />
                      </>
                    )}

                    {playerCondition === 'pts_early' && (
                      <View style={styles.earlyRow}>
                        <View style={styles.earlyField}>
                          <Text style={styles.inputLabel}>Points</Text>
                          <TextInput
                            style={[styles.input, styles.inputSmall]}
                            keyboardType="numeric"
                            value={ptsThreshold}
                            onChangeText={setPtsThreshold}
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
                                style={[styles.miniQBtn, quarterDeadline === q && styles.quarterButtonActive]}
                                onPress={() => setQuarterDeadline(q)}
                              >
                                <Text style={[styles.quarterText, quarterDeadline === q && styles.quarterTextActive]}>
                                  Q{q}
                                </Text>
                              </TouchableOpacity>
                            ))}
                          </View>
                        </View>
                      </View>
                    )}

                    {playerCondition === 'triple_double' && (
                      <Text style={styles.tripleDoubleHint}>
                        Alert fires when the player has 9+ in at least 3 of: Points, Rebounds, Assists, Steals, Blocks.
                      </Text>
                    )}

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
  // Layout
  container: { flex: 1, backgroundColor: C.bg },

  // Header
  header: {
    fontSize: 24, fontWeight: '800', color: C.textPrimary,
    textAlign: 'center', paddingTop: 12, paddingBottom: 16, letterSpacing: 1.5,
  },

  // Section label
  sectionLabel: {
    fontSize: 11, fontWeight: '700', color: C.textMuted,
    letterSpacing: 1.5, marginBottom: 10,
  },

  // Empty / loading state
  centeredMessage: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  emptyTitle: { fontSize: 16, fontWeight: '700', color: C.textMuted, marginBottom: 6 },
  emptyText:  { fontSize: 13, color: C.textMuted, textAlign: 'center', paddingHorizontal: 32 },

  // Game card
  gameCard: {
    backgroundColor: C.card, borderRadius: 16,
    marginHorizontal: 16, marginBottom: 12,
    padding: 16, borderWidth: 1, borderColor: C.border,
    // Shadows
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

  // Dual time display (scheduled games)
  timesBlock:  { alignItems: 'flex-end' },
  localTime:   { fontSize: 15, fontWeight: '800', color: C.textPrimary },
  usTime:      { fontSize: 11, color: C.textMuted, marginTop: 1 },

  // Teams
  teamsRow:        { flexDirection: 'row', alignItems: 'center' },
  teamBlock:       { flex: 1, alignItems: 'center', gap: 6 },
  teamLogo:        { width: 56, height: 56 },
  logoPlaceholder: { backgroundColor: C.border, borderRadius: 28 },
  teamAbbr:        { fontSize: 14, fontWeight: '800', color: C.textPrimary, letterSpacing: 0.5 },
  scoreText:       { fontSize: 32, fontWeight: '800', color: C.textPrimary, lineHeight: 36 },
  vsDivider:       { width: 40, alignItems: 'center' },
  vsText:          { fontSize: 12, color: C.textMuted, fontWeight: '700', letterSpacing: 1 },

  // Star button (team cards + player banner)
  starBtn:       { fontSize: 22, color: C.textMuted },
  starBtnActive: { color: '#f5c518' },

  // Modal shell
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

  // Alarm tabs (Game / Player)
  alarmTabs: {
    flexDirection: 'row', marginBottom: 20,
    backgroundColor: C.bg, borderRadius: 10, overflow: 'hidden',
    borderWidth: 1, borderColor: C.border,
  },
  alarmTab:           { flex: 1, paddingVertical: 9, alignItems: 'center' },
  alarmTabActive:     { backgroundColor: C.blue },
  alarmTabText:       { fontSize: 13, fontWeight: '700', color: C.textMuted },
  alarmTabTextActive: { color: '#fff' },

  // Shared input styles
  inputLabel: { fontSize: 12, color: C.textMuted, fontWeight: '700', marginBottom: 8, letterSpacing: 1 },
  input: {
    borderBottomWidth: 2, borderColor: C.blue,
    marginBottom: 20, fontSize: 28,
    color: C.textPrimary, paddingVertical: 4,
  },
  inputSmall: { fontSize: 22, marginBottom: 0 },

  // Quarter row
  quarterRow:          { flexDirection: 'row', gap: 10, marginBottom: 20 },
  quarterButton: {
    flex: 1, height: 46, borderRadius: 10,
    backgroundColor: C.bg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },
  quarterButtonActive: { backgroundColor: C.red, borderColor: C.red },
  quarterText:         { fontSize: 14, fontWeight: '800', color: C.textMuted },
  quarterTextActive:   { color: '#fff' },

  // Buttons
  startButton: {
    backgroundColor: C.blue, padding: 16,
    borderRadius: 14, alignItems: 'center', marginTop: 4,
  },
  startButtonText:  { color: '#fff', fontWeight: '800', fontSize: 15 },
  cancelButton:     { padding: 14, alignItems: 'center', marginTop: 4 },
  cancelButtonText: { color: C.textMuted, fontSize: 15 },

  // ── Player alarm styles ────────────────────────────────────────────────

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
  playerRowSelected:  { backgroundColor: 'rgba(29,66,138,0.25)' },
  playerJersey:       { fontSize: 12, color: C.textMuted, width: 28, textAlign: 'right' },
  playerName:         { flex: 1, fontSize: 14, color: C.textPrimary, fontWeight: '600' },
  playerNameSelected: { color: C.accentBlue },
  playerPos:          { fontSize: 11, color: C.textMuted, width: 30 },

  // Selected player banner
  selectedPlayerHeader: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginBottom: 14, marginTop: 4,
  },
  selectedPlayerLabel: {
    flex: 1, fontSize: 13, fontWeight: '800', color: C.accentBlue,
    borderLeftWidth: 3, borderColor: C.blue, paddingLeft: 8,
  },
  playerStarBtn: { paddingHorizontal: 6 },

  // Condition buttons
  conditionRow: { flexDirection: 'row', gap: 6, marginBottom: 16 },
  conditionBtn: {
    flex: 1, paddingVertical: 8, borderRadius: 8, alignItems: 'center',
    backgroundColor: C.bg, borderWidth: 1, borderColor: C.border,
  },
  conditionBtnActive:     { backgroundColor: C.blue, borderColor: C.blue },
  conditionBtnText:       { fontSize: 11, fontWeight: '700', color: C.textMuted, textAlign: 'center' },
  conditionBtnTextActive: { color: '#fff' },

  // "pts_early" row
  earlyRow:  { flexDirection: 'row', alignItems: 'flex-end', gap: 12, marginBottom: 16 },
  earlyField:{ flex: 1 },
  earlyBy:   { color: C.textMuted, fontSize: 14, marginBottom: 12, fontWeight: '700' },

  miniQuarterRow: { flexDirection: 'row', gap: 6 },
  miniQBtn: {
    flex: 1, height: 38, borderRadius: 8,
    backgroundColor: C.bg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },

  // Triple-double hint
  tripleDoubleHint: {
    fontSize: 13, color: C.textMuted, lineHeight: 19,
    marginBottom: 16, fontStyle: 'italic',
  },

  // Alert type toggle
  alertTypeRow: {
    flexDirection: 'row', gap: 10, marginBottom: 14,
  },
  alertTypeBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingVertical: 9, borderRadius: 10,
    backgroundColor: C.bg, borderWidth: 1, borderColor: C.border,
  },
  alertTypeBtnActive: { borderColor: C.red, backgroundColor: 'rgba(201,8,42,0.12)' },
  alertTypeIcon: { fontSize: 16 },
  alertTypeText: { fontSize: 13, fontWeight: '700', color: C.textMuted },
  alertTypeTextActive: { color: C.red },

  // Android overlay-permission banner
  overlayBanner: {
    flexDirection: 'row', alignItems: 'center',
    marginHorizontal: 16, marginBottom: 10,
    backgroundColor: 'rgba(240,165,0,0.1)',
    borderWidth: 1, borderColor: 'rgba(240,165,0,0.35)',
    borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10,
    gap: 6,
  },
  overlayBannerText: {
    flex: 1, fontSize: 12, color: C.textMuted, lineHeight: 16,
  },
  overlayBannerBtn: {
    paddingHorizontal: 10, paddingVertical: 5,
    backgroundColor: 'rgba(240,165,0,0.2)',
    borderRadius: 8, borderWidth: 1, borderColor: 'rgba(240,165,0,0.5)',
  },
  overlayBannerBtnText: {
    fontSize: 12, fontWeight: '800', color: '#f0a500',
  },
});
