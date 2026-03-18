import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Notifications from 'expo-notifications';
import { useFocusEffect } from 'expo-router';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Animated,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { SkeletonAlarmCard } from '../../components/skeleton-card';

import { SERVER_URL } from '../../config';

const EXPO_PROJECT_ID = '19104813-313d-40c1-975a-63b78d3ae7fc';
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
  team_id: string;
  team_name: string;
  max_diff: number;
  start_quarter: number;
}

interface FavPlayer {
  name:          string;
  total_pts:     number;
  hot_start_q:   number;
  hot_start_pts: number;
  td_watch:      boolean;
  alert_type:    string;
}

interface DbAlarm {
  alarm_id:       number;
  target_id:      string;
  target_name:    string;
  condition_type: string;
  threshold:      number;
  start_quarter?: number;
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

const C = {
  bg:          '#0B1623',
  card:        '#0d1f35',
  cardAlt:     '#111d2e',
  border:      '#1a3451',
  borderMuted: '#1e2a3d',
  blue:        '#1d428a',
  red:         '#c9082a',
  textPrimary: '#e6edf3',
  textMuted:   '#6b8aad',
  liveGreen:   '#3fb950',
  accentBlue:  '#4d8cf5',
  gold:        '#f0a500',
  orange:      '#FF6B00',
  orangeDim:   'rgba(255,107,0,0.15)',
};

const CONDITION_LABEL: Record<string, string> = {
  pts_goal:      'Points Goal',
  pts_early:     'Early Scorer',
  triple_double: 'Triple-Double Threat',
};

// ─── Pulsing Card (for LIVE alarms) ───────────────────────────────────────────

function PulsingCard({ children }: { children: React.ReactNode }) {
  const pulse = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 1400, useNativeDriver: false }),
        Animated.timing(pulse, { toValue: 0, duration: 1400, useNativeDriver: false }),
      ]),
    ).start();
  }, [pulse]);

  const borderColor = pulse.interpolate({
    inputRange: [0, 1],
    outputRange: ['rgba(255,107,0,0.25)', 'rgba(255,107,0,0.85)'],
  });

  return (
    <Animated.View style={[styles.alarmCard, styles.alarmCardLive, { borderColor }]}>
      {children}
    </Animated.View>
  );
}

// ─── Status Pills ─────────────────────────────────────────────────────────────

function LivePill({ label, color, bg }: { label: string; color: string; bg: string }) {
  return (
    <View style={[styles.pill, { backgroundColor: bg }]}>
      <View style={[styles.pillDot, { backgroundColor: color }]} />
      <Text style={[styles.pillText, { color }]}>{label}</Text>
    </View>
  );
}

// ─── ActiveScreen ─────────────────────────────────────────────────────────────

export default function ActiveScreen() {
  const [data, setData]           = useState<ActiveMonitors | null>(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  const [trophies, setTrophies]   = useState<TrophyHit[]>([]);
  const tokenRef                  = useRef<string | null>(null);

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
      const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId: EXPO_PROJECT_ID });
      tokenRef.current = token;
      return token;
    } catch (e) {
      console.warn('[Active] Could not resolve push token:', e);
      return null;
    }
  }, []);

  // Store received push notifications in AsyncStorage for Trophy Room
  useEffect(() => {
    const sub = Notifications.addNotificationReceivedListener(async notification => {
      const title = notification.request.content.title ?? 'Alarm Fired';
      const body  = notification.request.content.body  ?? '';
      const hit: TrophyHit = { title, body, receivedAt: new Date().toISOString() };
      try {
        const raw  = await AsyncStorage.getItem(TROPHY_KEY);
        const prev = raw ? (JSON.parse(raw) as TrophyHit[]) : [];
        const next = [hit, ...prev].slice(0, 10);
        await AsyncStorage.setItem(TROPHY_KEY, JSON.stringify(next));
        setTrophies(next);
      } catch {}
    });
    return () => sub.remove();
  }, []);

  const loadTrophies = async () => {
    try {
      const raw = await AsyncStorage.getItem(TROPHY_KEY);
      if (raw) setTrophies(JSON.parse(raw) as TrophyHit[]);
    } catch {}
  };

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const token = await resolveToken();
      if (!token) { setError('Push token unavailable. Please restart the app.'); return; }
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
      setError(e?.message ?? 'Failed to load. Check your connection.');
    } finally {
      setLoading(false);
    }
  }, [resolveToken]);

  useFocusEffect(
    useCallback(() => {
      load();
      loadTrophies();
    }, [load]),
  );

  const cancelGameAlarm = (alarm: GameAlarm) => {
    showConfirm(`Stop watching "${alarm.game_name}"?`, () => {
      const t = tokenRef.current;
      const param = t ? `?user_token=${encodeURIComponent(t)}` : '';
      fetch(`${SERVER_URL}/active-monitors/game/${alarm.game_id}${param}`, { method: 'DELETE' })
        .then(() => load())
        .catch(e => console.error('Cancel game alarm error:', e));
    });
  };

  const cancelPlayerAlarm = (alarm: PlayerAlarm) => {
    showConfirm(`Stop watching ${alarm.player_name}?`, () => {
      const t = tokenRef.current;
      const param = t ? `?user_token=${encodeURIComponent(t)}` : '';
      fetch(`${SERVER_URL}/active-monitors/player/${alarm.player_id}${param}`, { method: 'DELETE' })
        .then(() => load())
        .catch(e => console.error('Cancel player alarm error:', e));
    });
  };

  const cancelDbAlarm = (alarm: DbAlarm) => {
    showConfirm('Are you sure you want to delete this alarm?', async () => {
      const t = tokenRef.current;
      if (!t) return;
      const url = `${SERVER_URL}/active-monitors/db/${alarm.alarm_id}?user_token=${encodeURIComponent(t)}`;
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
        <Text style={styles.header}>MISSION CONTROL</Text>
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
        <Text style={styles.header}>MISSION CONTROL</Text>
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 }}>
          <Ionicons name="cloud-offline-outline" size={48} color={C.border} />
          <Text style={[styles.emptyHint, { marginTop: 12 }]}>{error}</Text>
          <TouchableOpacity style={[styles.refreshBtn, { marginTop: 24 }]} onPress={() => load()}>
            <Text style={styles.refreshBtnText}>Try Again</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  const radar    = data?.global_radar;
  const hist     = data?.historical_mode;
  const histCats = hist?.categories;

  const allDbAlarms    = Array.isArray(data?.db_alarms) ? data!.db_alarms : [];
  const dbGameAlarms   = allDbAlarms.filter(a => (a.condition_type ?? '').trim().toLowerCase() === 'score_diff');
  const dbPlayerAlarms = allDbAlarms.filter(a => {
    const ct = (a.condition_type ?? '').trim().toLowerCase();
    return ct === 'points' || ct === 'triple_double';
  });

  const liveGameAlarms   = Array.isArray(data?.game_alarms)   ? data!.game_alarms   : [];
  const livePlayerAlarms = Array.isArray(data?.player_alarms) ? data!.player_alarms : [];

  const totalGameAlarms   = liveGameAlarms.length   + dbGameAlarms.length;
  const totalPlayerAlarms = livePlayerAlarms.length + dbPlayerAlarms.length;

  const activeCategories = histCats ? [
    histCats.scoring_40_half    && '40+ pts before halftime',
    histCats.threes_11          && '11+ three-pointers',
    histCats.quad_double_threat && 'Quad-double threat',
  ].filter(Boolean) as string[] : [];

  return (
    <SafeAreaView style={styles.container}>
      <Text style={styles.header}>MISSION CONTROL</Text>

      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 40 }}>

        {/* ── Game Alarms ─────────────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>GAME ALARMS</Text>
            <View style={[styles.badge, { backgroundColor: totalGameAlarms ? C.red : C.border }]}>
              <Text style={styles.badgeText}>{totalGameAlarms}</Text>
            </View>
          </View>

          {totalGameAlarms === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="notifications-off-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>No game alarms set. Tap a game on the Home tab to create one.</Text>
            </View>
          ) : (
            <View style={{ gap: 8 }}>
              {liveGameAlarms.map(alarm => (
                <PulsingCard key={`live-${alarm.game_id}`}>
                  <View style={styles.alarmCardLeft}>
                    <LivePill
                      label="WATCHING"
                      color={C.orange}
                      bg={C.orangeDim}
                    />
                    <Text style={styles.alarmGame}>{alarm.game_name ?? 'Unknown Game'}</Text>
                    <Text style={styles.alarmSub}>
                      Alert if diff ≤ {alarm.max_diff ?? '?'} pts from Q{alarm.start_quarter ?? '?'}
                    </Text>
                    <Text style={styles.alarmSub}>
                      {alarm.alert_type === 'alarm' ? '🚨 Full Alarm' : '🔕 Silent'}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelGameAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>Cancel</Text>
                  </TouchableOpacity>
                </PulsingCard>
              ))}
              {dbGameAlarms.map(alarm => (
                <View key={`db-${alarm.alarm_id ?? alarm.target_id}`} style={styles.alarmCard}>
                  <View style={styles.alarmCardLeft}>
                    <LivePill label="SAVED" color={C.accentBlue} bg="rgba(77,140,245,0.12)" />
                    <Text style={styles.alarmGame}>{alarm.target_name || alarm.target_id || 'Unknown Game'}</Text>
                    <Text style={styles.alarmSub}>
                      Score gap ≤ {alarm.threshold ?? '?'} pts · From Q{alarm.start_quarter ?? 4}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelDbAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>Delete</Text>
                  </TouchableOpacity>
                </View>
              ))}
            </View>
          )}
        </View>

        {/* ── Player Alarms ────────────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>PLAYER ALARMS</Text>
            <View style={[styles.badge, { backgroundColor: totalPlayerAlarms ? C.orange : C.border }]}>
              <Text style={styles.badgeText}>{totalPlayerAlarms}</Text>
            </View>
          </View>

          {totalPlayerAlarms === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="person-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>No player alarms set. Open a game and choose "Player Alarm".</Text>
            </View>
          ) : (
            <View style={{ gap: 8 }}>
              {livePlayerAlarms.map(alarm => (
                <PulsingCard key={`live-${alarm.player_id}`}>
                  <View style={styles.alarmCardLeft}>
                    <LivePill label="WATCHING" color={C.orange} bg={C.orangeDim} />
                    <Text style={styles.alarmGame}>{alarm.player_name ?? 'Unknown Player'}</Text>
                    <Text style={styles.alarmSub}>
                      {CONDITION_LABEL[alarm.condition] ?? alarm.condition}
                      {alarm.condition !== 'triple_double' ? ` · ${alarm.pts_threshold ?? '?'} pts` : ''}
                    </Text>
                    <Text style={[styles.alarmSub, { marginTop: 1 }]}>{alarm.game_name ?? ''}</Text>
                    <Text style={styles.alarmSub}>
                      {alarm.alert_type === 'alarm' ? '🚨 Full Alarm' : '🔕 Silent'}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelPlayerAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>Cancel</Text>
                  </TouchableOpacity>
                </PulsingCard>
              ))}
              {dbPlayerAlarms.map(alarm => (
                <View key={`db-${alarm.alarm_id ?? alarm.target_id}`} style={styles.alarmCard}>
                  <View style={styles.alarmCardLeft}>
                    <LivePill label="SAVED" color={C.orange} bg={C.orangeDim} />
                    <Text style={styles.alarmGame}>{alarm.target_name || alarm.target_id || 'Unknown Player'}</Text>
                    <Text style={styles.alarmSub}>
                      {(alarm.condition_type ?? '').toLowerCase() === 'triple_double'
                        ? 'Triple-Double Watch'
                        : alarm.start_quarter && alarm.start_quarter > 0
                          ? `${alarm.threshold ?? '?'}+ pts (Before Q${alarm.start_quarter})`
                          : `${alarm.threshold ?? '?'}+ pts (Any Quarter)`}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelDbAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>Delete</Text>
                  </TouchableOpacity>
                </View>
              ))}
            </View>
          )}
        </View>

        {/* ── Global Radar Status ──────────────────────────────────────────── */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>GLOBAL RADAR STATUS</Text>

          <View style={styles.statusRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>Background Radar</Text>
              <Text style={styles.rowSub}>
                {radar?.running ? 'Scanning all live games' : 'Stopped — will start with next alarm'}
              </Text>
            </View>
            <View style={[styles.statusDot, { backgroundColor: radar?.running ? C.liveGreen : C.border }]} />
          </View>

          <View style={styles.statusRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>Global Radar Alerts</Text>
              <Text style={styles.rowSub}>
                {radar?.enabled ? `≤ ${radar.max_diff} pts diff from Q${radar.start_quarter}` : 'Disabled'}
              </Text>
            </View>
            <View style={[styles.statusDot, { backgroundColor: radar?.enabled ? C.accentBlue : C.border }]} />
          </View>
        </View>

        {/* ── Favorites Summary ────────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>FAVORITES BEING TRACKED</Text>
            <View style={[styles.badge, { backgroundColor: C.blue }]}>
              <Text style={styles.badgeText}>
                {(data?.favorite_teams.length ?? 0) + Object.keys(data?.favorite_players ?? {}).length}
              </Text>
            </View>
          </View>

          {(data?.favorite_teams ?? []).length === 0 && Object.keys(data?.favorite_players ?? {}).length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="star-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>No favorites set. Add teams and players in Settings.</Text>
            </View>
          ) : (
            <>
              {(data?.favorite_teams ?? []).map(t => (
                <View key={t.team_id} style={styles.favItem}>
                  <Text style={styles.favIcon}>🏀</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.favName}>{t.team_name}</Text>
                    <Text style={styles.rowSub}>Alert if diff ≤ {t.max_diff} pts from Q{t.start_quarter}</Text>
                  </View>
                </View>
              ))}
              {Object.entries(data?.favorite_players ?? {}).map(([pid, p]) => (
                <View key={pid} style={styles.favItem}>
                  <Text style={styles.favIcon}>⭐</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.favName}>{p.name}</Text>
                    <Text style={styles.rowSub}>
                      {p.total_pts} pts · Early {p.hot_start_pts}+ pts by Q{p.hot_start_q}{p.td_watch ? ' · TD watch' : ''}
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
            <Text style={styles.sectionTitle}>HISTORICAL MODE</Text>
            <View style={[styles.badge, { backgroundColor: hist?.enabled ? C.gold : C.border }]}>
              <Text style={[styles.badgeText, { color: hist?.enabled ? '#000' : C.textMuted }]}>
                {hist?.enabled ? 'ON' : 'OFF'}
              </Text>
            </View>
          </View>

          {!hist?.enabled ? (
            <View style={styles.emptyState}>
              <Ionicons name="time-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>Disabled. Enable in Settings to catch all-time rare stat lines.</Text>
            </View>
          ) : activeCategories.length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="time-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>Enabled but no categories are active.</Text>
            </View>
          ) : (
            activeCategories.map(cat => (
              <View key={cat} style={styles.catItem}>
                <View style={[styles.pillDot, { backgroundColor: C.gold, marginRight: 10 }]} />
                <Text style={styles.catLabel}>{cat}</Text>
              </View>
            ))
          )}
        </View>

        {/* ── Trophy Room ──────────────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>🏆 TROPHY ROOM</Text>
            <TouchableOpacity
              onPress={async () => {
                await AsyncStorage.removeItem(TROPHY_KEY);
                setTrophies([]);
              }}
            >
              <Text style={{ color: C.textMuted, fontSize: 11, fontWeight: '700' }}>CLEAR</Text>
            </TouchableOpacity>
          </View>

          {trophies.length === 0 ? (
            <View style={styles.emptyState}>
              <Text style={{ fontSize: 28 }}>🏆</Text>
              <Text style={styles.emptyHint}>Alarms that fire will appear here as your Trophy Room fills up.</Text>
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
          <Text style={styles.refreshBtnText}>Refresh</Text>
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
            <Text style={styles.confirmTitle}>Remove Alert?</Text>
            <Text style={styles.confirmMessage}>{confirmModal.message}</Text>
            <View style={styles.confirmButtons}>
              <TouchableOpacity style={styles.confirmCancelBtn} onPress={hideConfirm}>
                <Text style={styles.confirmCancelText}>Keep</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.confirmDeleteBtn}
                onPress={() => { hideConfirm(); confirmModal.onConfirm(); }}
              >
                <Text style={styles.confirmDeleteText}>Delete</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

    </SafeAreaView>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
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
  // Live card (animated borderColor applied via PulsingCard)
  alarmCardLive: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: 'rgba(255,107,0,0.04)', borderRadius: 12,
    borderWidth: 1.5, padding: 12,
  },
  alarmCardLeft: { flex: 1 },

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
