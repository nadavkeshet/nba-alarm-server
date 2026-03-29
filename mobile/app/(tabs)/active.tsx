import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import messaging from '@react-native-firebase/messaging';
import { useFocusEffect } from 'expo-router';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTheme, type AppColors } from '../../context/ThemeContext';
import { useLanguage } from '../../context/LanguageContext';
import {
  Animated,
  InteractionManager,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { SkeletonAlarmCard } from '../../components/skeleton-card';
import { TeamBadge } from '../../components/TeamBadge';
import { PlayerAvatar } from '../../components/PlayerAvatar';

import { SERVER_URL } from '../../config';
import { translateGameName, teamFullToHe } from '../../utils/teamNames';

const TROPHY_KEY = 'nba_alarm_recent_hits';

// ─── Types ────────────────────────────────────────────────────────────────────

interface GameAlarm {
  game_id: string;
  game_name: string;
  max_diff: number;
  start_quarter: number;
  alert_type: string;
  status: string;
  started_at: string;
}

interface PlayerAlarm {
  game_id: string;
  game_name: string;
  player_id: string;
  player_name: string;
  condition: string;
  pts_threshold: number;
  alert_type: string;
  status: string;
  started_at: string;
}

interface GlobalRadar {
  enabled: boolean;
  running: boolean;
  max_diff: number;
  start_quarter: number;
}

interface FavTeam {
  team_id:         string;
  team_name:       string;
  max_diff:        number;
  start_quarter:   number;
  notify_at_start?: boolean;
}

interface FavPlayer {
  name:          string;
  total_pts:     number;
  early_pts_q:   number;
  early_pts: number;
  td_watch:      boolean;
  alert_type:    string;
}

interface DbAlarm {
  alarm_id:         number;
  target_id:        string;
  target_name:      string;
  condition_type:   string;
  threshold:        number;
  start_quarter?:   number;
  home_team_id?:    string;
  away_team_id?:    string;
  notify_at_start?: boolean;
}

interface HistoricalCats {
  scoring_40_half: boolean;
  threes_11: boolean;
  quad_double_threat: boolean;
}

interface ActiveMonitors {
  game_alarms:      GameAlarm[];
  player_alarms:    PlayerAlarm[];
  db_alarms:        DbAlarm[];
  global_radar:     GlobalRadar;
  favorite_teams:   FavTeam[];
  favorite_players: Record<string, FavPlayer>;
  historical_mode:  { enabled: boolean; categories: HistoricalCats };
}

interface TrophyHit {
  title: string;
  body: string;
  receivedAt: string;
}

// ─── Theme ────────────────────────────────────────────────────────────────────


const CONDITION_LABEL: Record<string, string> = {
  pts_goal:      'active.condPtsGoal',
  pts_early:     'active.condEarly',
  triple_double: 'active.condTriple',
};

// ─── Static styles for module-level components ────────────────────────────────
// These components are defined outside ActiveScreen so they cannot access the
// theme-computed `styles` object. Static/structural styles go here; dynamic
// colors are passed as props.

const pillStyles = StyleSheet.create({
  pill:    { flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-start', borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, marginBottom: 6 },
  pillDot: { width: 6, height: 6, borderRadius: 3, marginRight: 5 },
  pillText:{ fontSize: 10, fontWeight: '900' as const, letterSpacing: 1 },
});

const cardLiveStyle = { flexDirection: 'row' as const, alignItems: 'center' as const, backgroundColor: 'rgba(255,107,0,0.04)', borderRadius: 12, borderWidth: 1.5, borderColor: 'rgba(255,107,0,0.25)', padding: 12 };
const pulsingBorderStyle = { ...StyleSheet.absoluteFillObject, borderRadius: 12, borderWidth: 1.5, borderColor: 'rgba(255,107,0,0.85)' };

// ─── Pulsing Card (for LIVE alarms) ───────────────────────────────────────────

const PulsingCard = React.memo(function PulsingCard({ children, cardStyle }: { children: React.ReactNode; cardStyle: object }) {
  const pulse = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    // Defer animation start until after the tab-switch interaction completes
    // so it doesn't compete with the navigation gesture on the JS thread
    const handle = InteractionManager.runAfterInteractions(() => {
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulse, { toValue: 1, duration: 1400, useNativeDriver: true }),
          Animated.timing(pulse, { toValue: 0, duration: 1400, useNativeDriver: true }),
        ]),
      ).start();
    });
    return () => { handle.cancel(); };
  }, [pulse]);

  return (
    // Base card keeps a faint static border; the bright border fades in/out
    // via an absolutely-positioned overlay — opacity is native-driver safe
    <View style={[cardStyle, cardLiveStyle]}>
      <Animated.View style={[pulsingBorderStyle, { opacity: pulse }]} pointerEvents="none" />
      {children}
    </View>
  );
});

// ─── Status Pills ─────────────────────────────────────────────────────────────

const LivePill = React.memo(function LivePill({ label, color, bg }: { label: string; color: string; bg: string }) {
  return (
    <View style={[pillStyles.pill, { backgroundColor: bg }]}>
      <View style={[pillStyles.pillDot, { backgroundColor: color }]} />
      <Text style={[pillStyles.pillText, { color }]}>{label}</Text>
    </View>
  );
});

/** Returns "Last 5'" for start_quarter=5, otherwise "Q{n}" */
function qLabel(q: number | string | undefined | null): string {
  return String(q) === '5' ? "Last 5'" : `Q${q ?? '?'}`;
}

// ─── ActiveScreen ─────────────────────────────────────────────────────────────

export default function ActiveScreen() {
  const { colors: C } = useTheme();
  const styles = useMemo(() => makeStyles(C), [C]);
  const { t, isHebrew } = useLanguage();
  const [data, setData]           = useState<ActiveMonitors | null>(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  const [trophies, setTrophies]   = useState<TrophyHit[]>([]);
  const tokenRef                  = useRef<string | null>(null);
  // Tracks whether we've ever completed a load — used to skip showing the
  // skeleton on subsequent tab-focus refreshes (stale-while-revalidate)
  const dataLoadedRef             = useRef(false);

  // Custom confirm modal (replaces system Alert for deletions)
  const [confirmModal, setConfirmModal] = useState<{
    visible: boolean;
    message: string;
    onConfirm: () => void;
  }>({ visible: false, message: '', onConfirm: () => {} });

  const showConfirm = (message: string, onConfirm: () => void) =>
    setConfirmModal({ visible: true, message, onConfirm });
  const hideConfirm = () =>
    setConfirmModal(prev => ({ ...prev, visible: false }));

  const resolveToken = useCallback(async (): Promise<string | null> => {
    if (tokenRef.current) return tokenRef.current;
    try {
      const token = await messaging().getToken();
      tokenRef.current = token;
      return token;
    } catch (e) {
      console.warn('[Active] Could not resolve push token:', e);
      return null;
    }
  }, []);

  // Store received FCM alarm notifications in AsyncStorage for Trophy Room
  useEffect(() => {
    const unsub = messaging().onMessage(async remoteMessage => {
      const data = remoteMessage.data ?? {};
      if (data.alert_type !== 'alarm') return;
      const hit: TrophyHit = {
        title:      String(data.title ?? 'Alarm Fired'),
        body:       String(data.body  ?? ''),
        receivedAt: new Date().toISOString(),
      };
      try {
        const raw  = await AsyncStorage.getItem(TROPHY_KEY);
        const prev = raw ? (JSON.parse(raw) as TrophyHit[]) : [];
        const next = [hit, ...prev].slice(0, 10);
        await AsyncStorage.setItem(TROPHY_KEY, JSON.stringify(next));
        setTrophies(next);
      } catch {}
    });
    return () => unsub();
  }, []);

  const loadTrophies = async () => {
    try {
      const raw = await AsyncStorage.getItem(TROPHY_KEY);
      if (raw) setTrophies(JSON.parse(raw) as TrophyHit[]);
    } catch {}
  };

  const load = useCallback(async () => {
    setError(null);
    // Only show skeleton on the very first load — on subsequent tab-focus
    // refreshes the existing data stays visible while the fetch runs in background
    if (!dataLoadedRef.current) setLoading(true);
    try {
      const token = await resolveToken();
      if (!token) {
        if (!dataLoadedRef.current) setError('Push token unavailable. Please restart the app.');
        return;
      }
      const resp = await fetch(`${SERVER_URL}/active-monitors?user_token=${encodeURIComponent(token)}`);
      if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
      const raw = await resp.json();
      const d: ActiveMonitors = {
        ...raw,
        db_alarms:       Array.isArray(raw.db_alarms)       ? raw.db_alarms       : [],
        game_alarms:     Array.isArray(raw.game_alarms)     ? raw.game_alarms     : [],
        player_alarms:   Array.isArray(raw.player_alarms)   ? raw.player_alarms   : [],
        favorite_teams:  Array.isArray(raw.favorite_teams)  ? raw.favorite_teams  : [],
        favorite_players: raw.favorite_players && typeof raw.favorite_players === 'object'
          ? raw.favorite_players : {},
      };
      setData(d);
    } catch (e: any) {
      // Only surface the error when there's nothing to show — otherwise keep stale data visible
      if (!dataLoadedRef.current) setError(e?.message ?? 'Failed to load. Check your connection.');
    } finally {
      dataLoadedRef.current = true;
      setLoading(false);
    }
  }, [resolveToken]);

  useFocusEffect(
    useCallback(() => {
      load();
      loadTrophies();
    }, [load]),
  );

  // ── Derived state — memoised so filters don't re-run on every render ───

  const derived = useMemo(() => {
    const allDbAlarms    = Array.isArray(data?.db_alarms) ? data!.db_alarms : [];
    const dbGameAlarms   = allDbAlarms.filter(a => (a.condition_type ?? '').trim().toLowerCase() === 'score_diff');
    const dbPlayerAlarmsFlat = allDbAlarms.filter(a => {
      const ct = (a.condition_type ?? '').trim().toLowerCase();
      return ct === 'points' || ct === 'triple_double';
    });
    const liveGameAlarms   = Array.isArray(data?.game_alarms)   ? data!.game_alarms   : [];
    const livePlayerAlarmsFlat = Array.isArray(data?.player_alarms) ? data!.player_alarms : [];

    // Group player alarms by player so multiple conditions show as one card
    const livePlayerAlarmGroups: PlayerAlarm[][] = [];
    const livePlayerMap = new Map<string, PlayerAlarm[]>();
    for (const a of livePlayerAlarmsFlat) {
      const group = livePlayerMap.get(a.player_id) ?? [];
      group.push(a);
      livePlayerMap.set(a.player_id, group);
    }
    livePlayerMap.forEach(g => livePlayerAlarmGroups.push(g));

    const dbPlayerAlarmGroups: DbAlarm[][] = [];
    const dbPlayerMap = new Map<string, DbAlarm[]>();
    for (const a of dbPlayerAlarmsFlat) {
      const group = dbPlayerMap.get(a.target_id) ?? [];
      group.push(a);
      dbPlayerMap.set(a.target_id, group);
    }
    dbPlayerMap.forEach(g => dbPlayerAlarmGroups.push(g));

    const histCats = data?.historical_mode?.categories;
    const activeCategories = histCats ? [
      histCats.scoring_40_half    && 'settings.scoring40',
      histCats.threes_11          && 'settings.threes11',
      histCats.quad_double_threat && 'settings.quadDouble',
    ].filter(Boolean) as string[] : [];
    return { dbGameAlarms, liveGameAlarms, livePlayerAlarmGroups, dbPlayerAlarmGroups, activeCategories };
  }, [data]);

  const cancelGameAlarm = (alarm: GameAlarm) => {
    showConfirm(t('active.deleteConfirm'), () => {
      const token = tokenRef.current;
      const param = token ? `?user_token=${encodeURIComponent(token)}` : '';
      fetch(`${SERVER_URL}/active-monitors/game/${alarm.game_id}${param}`, { method: 'DELETE' })
        .then(() => load())
        .catch(e => console.error('Cancel game alarm error:', e));
    });
  };

  const cancelPlayerAlarm = (alarms: PlayerAlarm[]) => {
    const first = alarms[0];
    showConfirm(t('active.deleteConfirm'), () => {
      const token = tokenRef.current;
      const param = token ? `?user_token=${encodeURIComponent(token)}` : '';
      // A single DELETE cancels all condition threads for this player on the server
      fetch(`${SERVER_URL}/active-monitors/player/${first.player_id}${param}`, { method: 'DELETE' })
        .then(() => load())
        .catch(e => console.error('Cancel player alarm error:', e));
    });
  };

  const cancelDbAlarm = (alarm: DbAlarm) => {
    showConfirm(t('active.deleteConfirm'), async () => {
      const token = tokenRef.current;
      if (!token) return;
      const url = `${SERVER_URL}/active-monitors/db/${alarm.alarm_id}?user_token=${encodeURIComponent(token)}`;
      try {
        const resp = await fetch(url, { method: 'DELETE' });
        if (!resp.ok) return;
        setData(prev => prev ? {
          ...prev,
          db_alarms: prev.db_alarms.filter(a => a.alarm_id !== alarm.alarm_id),
        } : prev);
      } catch {
        console.error('Delete failed — could not reach server');
      }
    });
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <Text style={styles.header}>{t('active.title')}</Text>
        <ScrollView contentContainerStyle={{ paddingBottom: 40 }}>
          {[0, 1, 2].map(i => (
            <View key={i} style={styles.section}>
              <SkeletonAlarmCard />
              <SkeletonAlarmCard />
            </View>
          ))}
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={styles.container}>
        <Text style={styles.header}>{t('active.title')}</Text>
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 }}>
          <Ionicons name="cloud-offline-outline" size={48} color={C.border} />
          <Text style={[styles.emptyHint, { marginTop: 12 }]}>{error}</Text>
          <TouchableOpacity style={[styles.refreshBtn, { marginTop: 24 }]} onPress={() => load()}>
            <Text style={styles.refreshBtnText}>{t('active.tryAgain')}</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  const radar    = data?.global_radar;
  const hist     = data?.historical_mode;

  const { dbGameAlarms, liveGameAlarms, livePlayerAlarmGroups, dbPlayerAlarmGroups, activeCategories } = derived;
  const totalGameAlarms   = liveGameAlarms.length + dbGameAlarms.length;
  const totalPlayerAlarms = livePlayerAlarmGroups.length + dbPlayerAlarmGroups.length;

  return (
    <SafeAreaView style={styles.container}>
      <Text style={styles.header}>{t('active.title')}</Text>

      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 40 }}>

        {/* ── Game Alarms ─────────────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>{t('active.gameAlarms')}</Text>
            <View style={[styles.badge, { backgroundColor: totalGameAlarms ? C.red : C.border }]}>
              <Text style={styles.badgeText}>{totalGameAlarms}</Text>
            </View>
          </View>

          {totalGameAlarms === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="notifications-off-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>{t('active.noGameAlarms')}</Text>
            </View>
          ) : (
            <View style={{ gap: 8 }}>
              {liveGameAlarms.map(alarm => (
                <PulsingCard key={`live-${alarm.game_id}`} cardStyle={styles.alarmCard}>
                  <View style={styles.alarmCardLeft}>
                    <LivePill
                      label={t('active.watching')}
                      color={C.orange}
                      bg={C.orangeDim}
                    />
                    <Text style={styles.alarmGame}>{isHebrew ? translateGameName(alarm.game_name ?? '') || t('active.unknownGame') : (alarm.game_name ?? t('active.unknownGame'))}</Text>
                    <Text style={styles.alarmSub}>
                      {alarm.notify_at_start ? `Any score · From ${qLabel(alarm.start_quarter)}` : `≤ ${alarm.threshold ?? '?'} pts · From ${qLabel(alarm.start_quarter)}`}
                    </Text>
                    <Text style={styles.alarmSub}>
                      {alarm.alert_type === 'alarm' ? t('active.fullAlarm') : t('active.silentMode')}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelGameAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>{t('active.cancel')}</Text>
                  </TouchableOpacity>
                </PulsingCard>
              ))}
              {dbGameAlarms.map(alarm => (
                <View key={`db-${alarm.alarm_id ?? alarm.target_id}`} style={styles.alarmCard}>
                  {(alarm.home_team_id || alarm.away_team_id) && (
                    <View style={styles.alarmTeamLogos}>
                      {alarm.away_team_id ? (
                        <TeamBadge abbreviation="" size={28} />
                      ) : null}
                      {alarm.home_team_id ? (
                        <TeamBadge abbreviation="" size={28} />
                      ) : null}
                    </View>
                  )}
                  <View style={styles.alarmCardLeft}>
                    <LivePill label={t('active.saved')} color={C.accentBlue} bg="rgba(77,140,245,0.12)" />
                    <Text style={styles.alarmGame}>{isHebrew ? translateGameName(alarm.target_name || '') || alarm.target_id || t('active.unknownGame') : (alarm.target_name || alarm.target_id || t('active.unknownGame'))}</Text>
                    <Text style={styles.alarmSub}>
                      {`≤ ${alarm.threshold ?? '?'} pts · From ${qLabel(alarm.start_quarter ?? 4)}`}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelDbAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>{t('active.delete')}</Text>
                  </TouchableOpacity>
                </View>
              ))}
            </View>
          )}
        </View>

        {/* ── Player Alarms ────────────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>{t('active.playerAlarms')}</Text>
            <View style={[styles.badge, { backgroundColor: totalPlayerAlarms ? C.orange : C.border }]}>
              <Text style={styles.badgeText}>{totalPlayerAlarms}</Text>
            </View>
          </View>

          {totalPlayerAlarms === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="person-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>{t('active.noPlayerAlarms')}</Text>
            </View>
          ) : (
            <View style={{ gap: 8 }}>
              {livePlayerAlarmGroups.map(group => (
                <PulsingCard key={`live-${group[0].player_id}`} cardStyle={styles.alarmCard}>
                  <PlayerAvatar size={40} />
                  <View style={styles.alarmCardLeft}>
                    <LivePill label={t('active.watching')} color={C.orange} bg={C.orangeDim} />
                    <Text style={styles.alarmGame}>{group[0].player_name ?? t('active.unknownPlayer')}</Text>
                    {group.map((alarm, i) => (
                      <Text key={i} style={styles.alarmSub}>
                        {t(CONDITION_LABEL[alarm.condition] ?? alarm.condition)}
                        {alarm.condition !== 'triple_double' ? ` · ${alarm.pts_threshold ?? '?'} ${t('active.pts')}` : ''}
                      </Text>
                    ))}
                    <Text style={[styles.alarmSub, { marginTop: 1 }]}>{isHebrew ? translateGameName(group[0].game_name ?? '') : (group[0].game_name ?? '')}</Text>
                    <Text style={styles.alarmSub}>
                      {group[0].alert_type === 'alarm' ? t('active.fullAlarm') : t('active.silentMode')}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelPlayerAlarm(group)}>
                    <Text style={styles.cancelBtnText}>{t('active.cancel')}</Text>
                  </TouchableOpacity>
                </PulsingCard>
              ))}
              {dbPlayerAlarmGroups.map(group => (
                <View key={`db-${group[0].target_id}`} style={styles.alarmCard}>
                  <PlayerAvatar size={40} />
                  <View style={styles.alarmCardLeft}>
                    <LivePill label={t('active.saved')} color={C.orange} bg={C.orangeDim} />
                    <Text style={styles.alarmGame}>{group[0].target_name || group[0].target_id || t('active.unknownPlayer')}</Text>
                    {group.map((alarm, i) => (
                      <Text key={i} style={styles.alarmSub}>
                        {(alarm.condition_type ?? '').toLowerCase() === 'triple_double'
                          ? t('active.tripleDoubleWatch')
                          : alarm.start_quarter && alarm.start_quarter > 0
                            ? t('active.ptsBeforeQ', alarm.threshold ?? '?', alarm.start_quarter)
                            : t('active.ptsAnyQ', alarm.threshold ?? '?')}
                      </Text>
                    ))}
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => {
                    showConfirm(t('active.deletePlayer', group[0].target_name || ''), async () => {
                      const token = tokenRef.current;
                      if (!token) return;
                      await Promise.all(group.map(alarm =>
                        fetch(`${SERVER_URL}/active-monitors/db/${alarm.alarm_id}?user_token=${encodeURIComponent(token)}`, { method: 'DELETE' })
                      ));
                      setData(prev => prev ? {
                        ...prev,
                        db_alarms: prev.db_alarms.filter(a => !group.some(g => g.alarm_id === a.alarm_id)),
                      } : prev);
                    });
                  }}>
                    <Text style={styles.cancelBtnText}>{t('active.delete')}</Text>
                  </TouchableOpacity>
                </View>
              ))}
            </View>
          )}
        </View>

        {/* ── Global Radar Status ──────────────────────────────────────────── */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t('active.globalRadar')}</Text>

          <View style={styles.statusRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>{t('active.bgRadar')}</Text>
              <Text style={styles.rowSub}>
                {radar?.running ? t('active.radarRunning') : t('active.radarStopped')}
              </Text>
            </View>
            <View style={[styles.statusDot, { backgroundColor: radar?.running ? C.liveGreen : C.border }]} />
          </View>

          <View style={styles.statusRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>{t('active.radarAlerts')}</Text>
              <Text style={styles.rowSub}>
                {radar?.enabled ? `≤ ${radar.max_diff} pts · From ${qLabel(radar.start_quarter)}` : t('active.radarDisabled')}
              </Text>
            </View>
            <View style={[styles.statusDot, { backgroundColor: radar?.enabled ? C.accentBlue : C.border }]} />
          </View>
        </View>

        {/* ── Favorites Summary ────────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>{t('active.favTracked')}</Text>
            <View style={[styles.badge, { backgroundColor: C.blue }]}>
              <Text style={styles.badgeText}>
                {(data?.favorite_teams.length ?? 0) + Object.keys(data?.favorite_players ?? {}).length}
              </Text>
            </View>
          </View>

          {(data?.favorite_teams ?? []).length === 0 && Object.keys(data?.favorite_players ?? {}).length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="star-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>{t('active.noFavorites')}</Text>
            </View>
          ) : (
            <>
              {(data?.favorite_teams ?? []).map(team => (
                <View key={team.team_id} style={styles.favItem}>
                  <Text style={styles.favIcon}>🏀</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.favName}>{isHebrew ? teamFullToHe(team.team_name) : team.team_name}</Text>
                    <Text style={styles.rowSub}>{team.notify_at_start ? `Any score · From ${qLabel(team.start_quarter)}` : `≤ ${team.max_diff} pts · From ${qLabel(team.start_quarter)}`}</Text>
                  </View>
                </View>
              ))}
              {Object.entries(data?.favorite_players ?? {}).map(([pid, p]) => (
                <View key={pid} style={styles.favItem}>
                  <Text style={styles.favIcon}>⭐</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.favName}>{p.name}</Text>
                    <Text style={styles.rowSub}>
                      {[
                        p.total_pts != null && `${p.total_pts} pts`,
                        p.early_pts != null && p.early_pts_q != null && t('active.earlyPtsRow', p.early_pts, p.early_pts_q),
                        p.td_watch && t('active.tdWatch'),
                      ].filter(Boolean).join(' · ')}
                    </Text>
                  </View>
                </View>
              ))}
            </>
          )}
        </View>

        {/* ── Historical Mode ──────────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>{t('active.historical')}</Text>
            <View style={[styles.badge, { backgroundColor: hist?.enabled ? C.gold : C.border }]}>
              <Text style={[styles.badgeText, { color: hist?.enabled ? '#000' : C.textMuted }]}>
                {hist?.enabled ? t('active.on') : t('active.off')}
              </Text>
            </View>
          </View>

          {!hist?.enabled ? (
            <View style={styles.emptyState}>
              <Ionicons name="time-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>{t('active.historicalOff')}</Text>
            </View>
          ) : activeCategories.length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="time-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>{t('active.historicalNone')}</Text>
            </View>
          ) : (
            activeCategories.map(cat => (
              <View key={cat} style={styles.catItem}>
                <View style={[styles.pillDot, { backgroundColor: C.gold, marginRight: 10 }]} />
                <Text style={styles.catLabel}>{t(cat)}</Text>
              </View>
            ))
          )}
        </View>

        {/* ── Trophy Room ──────────────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>{t('active.trophy')}</Text>
            <TouchableOpacity
              onPress={async () => {
                await AsyncStorage.removeItem(TROPHY_KEY);
                setTrophies([]);
              }}
            >
              <Text style={{ color: C.textMuted, fontSize: 11, fontWeight: '700' }}>{t('active.clear')}</Text>
            </TouchableOpacity>
          </View>

          {trophies.length === 0 ? (
            <View style={styles.emptyState}>
              <Text style={{ fontSize: 28 }}>🏆</Text>
              <Text style={styles.emptyHint}>{t('active.trophyEmpty')}</Text>
            </View>
          ) : (
            trophies.map((hit, i) => (
              <View key={i} style={styles.trophyItem}>
                <View style={styles.trophyIconWrap}>
                  <Text style={{ fontSize: 20 }}>🔔</Text>
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.trophyTitle}>{hit.title}</Text>
                  <Text style={styles.trophyBody}>{hit.body}</Text>
                  <Text style={styles.trophyTime}>
                    {new Date(hit.receivedAt).toLocaleDateString(undefined, {
                      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                    })}
                  </Text>
                </View>
              </View>
            ))
          )}
        </View>

        {/* ── Refresh ──────────────────────────────────────────────────────── */}
        <TouchableOpacity style={styles.refreshBtn} onPress={() => load()} disabled={loading}>
          <Ionicons name="refresh-outline" size={16} color={C.orange} style={{ marginRight: 6 }} />
          <Text style={styles.refreshBtnText}>{t('active.refresh')}</Text>
        </TouchableOpacity>

      </ScrollView>

      {/* ── Custom Delete Confirmation Modal ───────────────────────── */}
      <Modal
        visible={confirmModal.visible}
        transparent
        animationType="fade"
        onRequestClose={hideConfirm}
      >
        <View style={styles.confirmOverlay}>
          <View style={styles.confirmBox}>
            <Text style={styles.confirmTitle}>{t('active.removeAlert')}</Text>
            <Text style={styles.confirmMessage}>{confirmModal.message}</Text>
            <View style={styles.confirmButtons}>
              <TouchableOpacity style={styles.confirmCancelBtn} onPress={hideConfirm}>
                <Text style={styles.confirmCancelText}>{t('active.keep')}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.confirmDeleteBtn}
                onPress={() => { hideConfirm(); confirmModal.onConfirm(); }}
              >
                <Text style={styles.confirmDeleteText}>{t('active.delete')}</Text>
              </TouchableOpacity>
            </View>
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
    fontSize: 18, fontWeight: '900', color: C.textPrimary,
    textAlign: 'center', paddingTop: 12, paddingBottom: 8,
    letterSpacing: 4,
  },

  section: {
    marginHorizontal: 14, marginTop: 14,
    backgroundColor: C.card, borderRadius: 18,
    borderWidth: 1, borderColor: C.border, padding: 16,
  },
  sectionHeader: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 10, fontWeight: '900', color: C.textMuted, letterSpacing: 2.5,
  },

  badge: {
    minWidth: 22, height: 22, borderRadius: 11,
    alignItems: 'center', justifyContent: 'center', paddingHorizontal: 6,
  },
  badgeText: { fontSize: 11, fontWeight: '800', color: '#fff' },

  // Default saved card (no pulse)
  alarmCard: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: C.cardAlt, borderRadius: 12,
    borderWidth: 1, borderColor: C.borderMuted,
    padding: 12,
  },
  // Live card — base faint border; bright border pulsed by the native-driver overlay
  alarmCardLive: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: 'rgba(255,107,0,0.04)', borderRadius: 12,
    borderWidth: 1.5, borderColor: 'rgba(255,107,0,0.25)', padding: 12,
  },
  // Absolutely-positioned overlay whose opacity is animated with useNativeDriver: true
  pulsingBorder: {
    ...StyleSheet.absoluteFillObject,
    borderRadius: 12, borderWidth: 1.5,
    borderColor: 'rgba(255,107,0,0.85)',
  },
  alarmCardLeft: { flex: 1 },
  alarmTeamLogos: { flexDirection: 'row', alignItems: 'center', gap: 2, marginRight: 10 },
  alarmTeamLogo:  { width: 28, height: 28, resizeMode: 'contain' },
  alarmPlayerHeadshot: { width: 40, height: 40, borderRadius: 20, marginRight: 10 },

  pill: {
    flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-start',
    borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, marginBottom: 6,
  },
  pillDot: { width: 6, height: 6, borderRadius: 3, marginRight: 5 },
  pillText: { fontSize: 10, fontWeight: '900', letterSpacing: 1 },

  alarmGame: { fontSize: 14, fontWeight: '700', color: C.textPrimary },
  alarmSub:  { fontSize: 12, color: C.textMuted, marginTop: 2 },

  cancelBtn: {
    paddingHorizontal: 12, paddingVertical: 7,
    backgroundColor: 'rgba(201,8,42,0.15)', borderRadius: 8,
    marginLeft: 12,
  },
  cancelBtnText: { color: C.red, fontSize: 12, fontWeight: '700' },

  statusRow: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', paddingVertical: 10,
    borderTopWidth: 1, borderColor: C.border,
  },
  rowLabel: { fontSize: 14, color: C.textPrimary, fontWeight: '600' },
  rowSub:   { fontSize: 12, color: C.textMuted, marginTop: 2 },
  statusDot: { width: 10, height: 10, borderRadius: 5 },

  favItem: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 10, borderTopWidth: 1, borderColor: C.border,
  },
  favIcon: { fontSize: 18, marginRight: 12 },
  favName: { fontSize: 14, fontWeight: '700', color: C.textPrimary },

  catItem: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 8, borderTopWidth: 1, borderColor: C.border,
  },
  catLabel: { fontSize: 13, color: C.textPrimary, fontWeight: '600' },

  emptyState: { alignItems: 'center', paddingVertical: 16, gap: 8 },
  emptyHint:  { color: C.textMuted, fontSize: 13, fontStyle: 'italic', textAlign: 'center' },

  trophyItem: {
    flexDirection: 'row', alignItems: 'flex-start',
    paddingVertical: 10, borderTopWidth: 1, borderColor: C.border, gap: 12,
  },
  trophyIconWrap: {
    width: 36, height: 36, borderRadius: 10,
    backgroundColor: 'rgba(240,165,0,0.12)',
    alignItems: 'center', justifyContent: 'center',
  },
  trophyTitle: { fontSize: 13, fontWeight: '700', color: C.textPrimary },
  trophyBody:  { fontSize: 12, color: C.textMuted, marginTop: 1 },
  trophyTime:  { fontSize: 10, color: C.border, marginTop: 3, fontWeight: '600' },

  refreshBtn: {
    margin: 16, padding: 14, borderRadius: 14,
    flexDirection: 'row',
    alignItems: 'center', justifyContent: 'center',
    backgroundColor: 'rgba(255,107,0,0.1)',
    borderWidth: 1, borderColor: 'rgba(255,107,0,0.3)',
  },
  refreshBtnText: { color: C.orange, fontWeight: '800', fontSize: 14, letterSpacing: 1 },

  // ── Confirm Modal ──────────────────────────────────────────────────
  confirmOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.7)',
    alignItems: 'center', justifyContent: 'center',
    padding: 32,
  },
  confirmBox: {
    width: '100%', backgroundColor: C.card,
    borderRadius: 20, borderWidth: 1, borderColor: C.border,
    padding: 28, alignItems: 'center',
  },
  confirmTitle: {
    fontSize: 20, fontWeight: '900', color: C.textPrimary,
    letterSpacing: 0.5, marginBottom: 10,
  },
  confirmMessage: {
    fontSize: 14, color: C.textMuted, textAlign: 'center',
    lineHeight: 20, marginBottom: 28,
  },
  confirmButtons: {
    flexDirection: 'row', gap: 12, width: '100%',
  },
  confirmCancelBtn: {
    flex: 1, paddingVertical: 14, borderRadius: 12,
    backgroundColor: C.cardAlt,
    borderWidth: 1, borderColor: C.border,
    alignItems: 'center',
  },
  confirmCancelText: { color: C.textMuted, fontWeight: '700', fontSize: 15 },
  confirmDeleteBtn: {
    flex: 1, paddingVertical: 14, borderRadius: 12,
    backgroundColor: 'rgba(201,8,42,0.2)',
    borderWidth: 1, borderColor: 'rgba(201,8,42,0.5)',
    alignItems: 'center',
  },
  confirmDeleteText: { color: '#ff4d6d', fontWeight: '800', fontSize: 15 },
});
