import { Ionicons } from '@expo/vector-icons';
import * as Notifications from 'expo-notifications';
import { useFocusEffect } from 'expo-router';
import React, { useCallback, useRef, useState } from 'react';
import {
  Alert,
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

// ─── Types ────────────────────────────────────────────────────────────────────

interface GameAlarm {
  game_id: string;
  game_name: string;
  max_diff: number;
  start_quarter: number;
  alert_type: string;
  status: string;        // "scheduled" | "live"
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
  status: string;        // "scheduled" | "live"
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
  condition_type: string;  // "points" | "score_diff" | "triple_double"
  threshold:      number;
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
  favorite_teams:   FavTeam[];                  // always an array from server
  favorite_players: Record<string, FavPlayer>;  // dict from server
  historical_mode:  { enabled: boolean; categories: HistoricalCats };
}

// ─── Theme ────────────────────────────────────────────────────────────────────

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
  gold:        '#f0a500',
  orange:      '#e05c00',
};

const CONDITION_LABEL: Record<string, string> = {
  pts_goal:      'Points Goal',
  pts_early:     'Early Scorer',
  triple_double: 'Triple-Double Threat',
};

// ─── Status Pill ──────────────────────────────────────────────────────────────

function GameStatusPill({ status }: { status: string }) {
  const isLive = status === 'live';
  return (
    <View style={[
      styles.livePill,
      !isLive && { backgroundColor: 'rgba(77,140,245,0.12)' },
    ]}>
      <View style={[styles.liveDot, !isLive && { backgroundColor: C.accentBlue }]} />
      <Text style={[styles.liveText, !isLive && { color: C.accentBlue }]}>
        {isLive ? 'WATCHING' : 'SCHEDULED'}
      </Text>
    </View>
  );
}

function PlayerStatusPill({ status }: { status: string }) {
  const isLive = status === 'live';
  return (
    <View style={[
      styles.livePill,
      { backgroundColor: isLive ? 'rgba(224,92,0,0.15)' : 'rgba(77,140,245,0.12)' },
    ]}>
      <View style={[
        styles.liveDot,
        { backgroundColor: isLive ? C.orange : C.accentBlue },
      ]} />
      <Text style={[
        styles.liveText,
        { color: isLive ? C.orange : C.accentBlue },
      ]}>
        {isLive ? 'WATCHING' : 'SCHEDULED'}
      </Text>
    </View>
  );
}

// ─── ActiveScreen ─────────────────────────────────────────────────────────────

export default function ActiveScreen() {
  const [data, setData]       = useState<ActiveMonitors | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const tokenRef              = useRef<string | null>(null);

  /** Resolve the push token: use cached ref, else call Notifications API. */
  const resolveToken = useCallback(async (): Promise<string | null> => {
    if (tokenRef.current) return tokenRef.current;
    try {
      const { data: token } = await Notifications.getExpoPushTokenAsync({
        projectId: EXPO_PROJECT_ID,
      });
      tokenRef.current = token;
      console.log('[Active] Resolved push token:', token.slice(0, 30) + '...');
      return token;
    } catch (e) {
      console.warn('[Active] Could not resolve push token:', e);
      return null;
    }
  }, []);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const token = await resolveToken();
      if (!token) {
        setError('Push token unavailable. Please restart the app.');
        return;
      }
      console.log('[Active] Fetching monitors with token:', token.slice(0, 30) + '...');
      const resp = await fetch(
        `${SERVER_URL}/active-monitors?user_token=${encodeURIComponent(token)}`,
      );
      if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
      const raw = await resp.json();
      console.log('[Active] Raw data from server:', JSON.stringify(raw));
      // Ensure db_alarms is always an array regardless of server shape
      const d: ActiveMonitors = {
        ...raw,
        db_alarms:     Array.isArray(raw.db_alarms)     ? raw.db_alarms     : [],
        game_alarms:   Array.isArray(raw.game_alarms)   ? raw.game_alarms   : [],
        player_alarms: Array.isArray(raw.player_alarms) ? raw.player_alarms : [],
        favorite_teams: Array.isArray(raw.favorite_teams) ? raw.favorite_teams : [],
        favorite_players: raw.favorite_players && typeof raw.favorite_players === 'object'
          ? raw.favorite_players : {},
      };
      console.log('[Active] db_alarms count:', d.db_alarms.length);
      setData(d);
    } catch (e: any) {
      console.error('[Active] Load error:', e);
      setError(e?.message ?? 'Failed to load. Check your connection.');
    } finally {
      setLoading(false);
    }
  }, [resolveToken]);

  // Refresh every time the tab is focused
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const cancelGameAlarm = (alarm: GameAlarm) => {
    Alert.alert(
      'Cancel Alarm',
      `Stop watching "${alarm.game_name}"?`,
      [
        { text: 'Keep', style: 'cancel' },
        {
          text: 'Cancel Alarm',
          style: 'destructive',
          onPress: () => {
            const t = tokenRef.current;
            const tokenParam = t ? `?user_token=${encodeURIComponent(t)}` : '';
            fetch(`${SERVER_URL}/active-monitors/game/${alarm.game_id}${tokenParam}`, { method: 'DELETE' })
              .then(() => load())
              .catch(e => console.error('Cancel game alarm error:', e));
          },
        },
      ],
    );
  };

  const cancelPlayerAlarm = (alarm: PlayerAlarm) => {
    Alert.alert(
      'Cancel Alarm',
      `Stop watching ${alarm.player_name}?`,
      [
        { text: 'Keep', style: 'cancel' },
        {
          text: 'Cancel Alarm',
          style: 'destructive',
          onPress: () => {
            const t = tokenRef.current;
            const tokenParam = t ? `?user_token=${encodeURIComponent(t)}` : '';
            fetch(`${SERVER_URL}/active-monitors/player/${alarm.player_id}${tokenParam}`, { method: 'DELETE' })
              .then(() => load())
              .catch(e => console.error('Cancel player alarm error:', e));
          },
        },
      ],
    );
  };

  const cancelDbAlarm = (alarm: DbAlarm) => {
    const label = alarm.condition_type === 'triple_double'
      ? `Triple-Double watch for ${alarm.target_id}`
      : `${alarm.condition_type === 'score_diff' ? 'Game' : 'Player'} alarm (threshold: ${alarm.threshold})`;

    Alert.alert(
      'Delete Alarm',
      `Permanently remove "${label}" from the database?`,
      [
        { text: 'Keep', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            const t = tokenRef.current;
            if (!t) {
              Alert.alert('Error', 'Push token not available. Please restart the app.');
              return;
            }
            const url = `${SERVER_URL}/active-monitors/db/${alarm.alarm_id}?user_token=${encodeURIComponent(t)}`;
            console.log('[Active] DELETE DB alarm →', url);
            try {
              const resp = await fetch(url, { method: 'DELETE' });
              if (!resp.ok) {
                const body = await resp.json().catch(() => ({}));
                const msg = body?.detail ?? `Server error ${resp.status}`;
                console.error('[Active] DB delete failed:', msg);
                Alert.alert('Delete Failed', msg);
                return;
              }
              const result = await resp.json();
              console.log('[Active] DB alarm deleted ✓', result);
              // Only update UI after confirmed server deletion
              setData(prev => prev ? {
                ...prev,
                db_alarms: prev.db_alarms.filter(a => a.alarm_id !== alarm.alarm_id),
              } : prev);
            } catch (e: any) {
              console.error('[Active] DB delete network error:', e);
              Alert.alert('Delete Failed', 'Could not reach the server. Check your connection.');
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <Text style={styles.header}>Mission Control</Text>
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
        <Text style={styles.header}>Mission Control</Text>
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

  // ── Split DB alarms into categories (done outside JSX so errors are visible) ─
  const allDbAlarms = Array.isArray(data?.db_alarms) ? data!.db_alarms : [];

  const dbGameAlarms = allDbAlarms.filter(
    a => (a.condition_type ?? '').trim().toLowerCase() === 'score_diff',
  );
  const dbPlayerAlarms = allDbAlarms.filter(a => {
    const ct = (a.condition_type ?? '').trim().toLowerCase();
    return ct === 'points' || ct === 'triple_double';
  });

  console.log('[Active] DB alarms total:', allDbAlarms.length);
  console.log('[Active] Game Alarms identified:', dbGameAlarms.length);
  console.log('[Active] Player Alarms identified:', dbPlayerAlarms.length);
  if (allDbAlarms.length > 0) {
    console.log('[Active] condition_types in DB:', allDbAlarms.map(a => a.condition_type));
  }

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
      <Text style={styles.header}>Mission Control</Text>

      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 40 }}>

        {/* ── Game Alarms ──────────────────────────────────────────────── */}
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
            <>
              {liveGameAlarms.map(alarm => (
                <View key={`live-${alarm.game_id}`} style={styles.alarmCard}>
                  <View style={styles.alarmCardLeft}>
                    <GameStatusPill status={alarm.status ?? 'scheduled'} />
                    <Text style={styles.alarmGame}>{alarm.game_name ?? 'Unknown Game'}</Text>
                    <Text style={styles.alarmSub}>
                      Alert if diff ≤ {alarm.max_diff ?? '?'} pts from Q{alarm.start_quarter ?? '?'}
                    </Text>
                    <Text style={styles.alarmSub}>
                      {alarm.alert_type === 'alarm' ? '🚨 Alarm' : '🔕 Silent'}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelGameAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>Cancel</Text>
                  </TouchableOpacity>
                </View>
              ))}
              {dbGameAlarms.map(alarm => (
                <View key={`db-${alarm.alarm_id ?? alarm.target_id}`} style={styles.alarmCard}>
                  <View style={styles.alarmCardLeft}>
                    <View style={[styles.livePill, { backgroundColor: 'rgba(77,140,245,0.12)' }]}>
                      <View style={[styles.liveDot, { backgroundColor: C.accentBlue }]} />
                      <Text style={[styles.liveText, { color: C.accentBlue }]}>SAVED</Text>
                    </View>
                    <Text style={styles.alarmGame}>{alarm.target_name || alarm.target_id || 'Unknown Game'}</Text>
                    <Text style={styles.alarmSub}>Score gap ≤ {alarm.threshold ?? '?'} pts</Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelDbAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>Delete</Text>
                  </TouchableOpacity>
                </View>
              ))}
            </>
          )}
        </View>

        {/* ── Player Alarms ────────────────────────────────────────────── */}
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
            <>
              {livePlayerAlarms.map(alarm => (
                <View key={`live-${alarm.player_id}`} style={styles.alarmCard}>
                  <View style={styles.alarmCardLeft}>
                    <PlayerStatusPill status={alarm.status ?? 'scheduled'} />
                    <Text style={styles.alarmGame}>{alarm.player_name ?? 'Unknown Player'}</Text>
                    <Text style={styles.alarmSub}>
                      {CONDITION_LABEL[alarm.condition] ?? alarm.condition}
                      {alarm.condition !== 'triple_double' ? ` · ${alarm.pts_threshold ?? '?'} pts` : ''}
                    </Text>
                    <Text style={[styles.alarmSub, { marginTop: 1 }]}>{alarm.game_name ?? ''}</Text>
                    <Text style={styles.alarmSub}>
                      {alarm.alert_type === 'alarm' ? '🚨 Alarm' : '🔕 Silent'}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelPlayerAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>Cancel</Text>
                  </TouchableOpacity>
                </View>
              ))}
              {dbPlayerAlarms.map(alarm => (
                <View key={`db-${alarm.alarm_id ?? alarm.target_id}`} style={styles.alarmCard}>
                  <View style={styles.alarmCardLeft}>
                    <View style={[styles.livePill, { backgroundColor: 'rgba(224,92,0,0.12)' }]}>
                      <View style={[styles.liveDot, { backgroundColor: C.orange }]} />
                      <Text style={[styles.liveText, { color: C.orange }]}>SAVED</Text>
                    </View>
                    <Text style={styles.alarmGame}>{alarm.target_name || alarm.target_id || 'Unknown Player'}</Text>
                    <Text style={styles.alarmSub}>
                      {(alarm.condition_type ?? '').toLowerCase() === 'triple_double'
                        ? 'Triple-Double Watch'
                        : `Points ≥ ${alarm.threshold ?? '?'}`}
                    </Text>
                  </View>
                  <TouchableOpacity style={styles.cancelBtn} onPress={() => cancelDbAlarm(alarm)}>
                    <Text style={styles.cancelBtnText}>Delete</Text>
                  </TouchableOpacity>
                </View>
              ))}
            </>
          )}
        </View>

        {/* ── Global Radar Status ──────────────────────────────────────── */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>GLOBAL RADAR STATUS</Text>

          <View style={styles.statusRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>Background Radar</Text>
              <Text style={styles.rowSub}>
                {radar?.running
                  ? 'Scanning all live games'
                  : 'Stopped — will start with next alarm'}
              </Text>
            </View>
            <View style={[styles.statusDot, {
              backgroundColor: radar?.running ? C.liveGreen : C.border,
            }]} />
          </View>

          <View style={styles.statusRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>Global Radar Alerts</Text>
              <Text style={styles.rowSub}>
                {radar?.enabled
                  ? `≤ ${radar.max_diff} pts diff from Q${radar.start_quarter}`
                  : 'Disabled'}
              </Text>
            </View>
            <View style={[styles.statusDot, {
              backgroundColor: radar?.enabled ? C.accentBlue : C.border,
            }]} />
          </View>
        </View>

        {/* ── Favorites Summary ────────────────────────────────────────── */}
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
                    <Text style={styles.rowSub}>
                      Alert if diff ≤ {t.max_diff} pts from Q{t.start_quarter}
                    </Text>
                  </View>
                </View>
              ))}
              {Object.entries(data?.favorite_players ?? {}).map(([pid, p]) => (
                <View key={pid} style={styles.favItem}>
                  <Text style={styles.favIcon}>⭐</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.favName}>{p.name}</Text>
                    <Text style={styles.rowSub}>
                      {p.total_pts} pts · Hot start Q{p.hot_start_q} @ {p.hot_start_pts} pts{p.td_watch ? ' · TD watch' : ''}
                    </Text>
                  </View>
                </View>
              ))}
            </>
          )}
        </View>

        {/* ── Historical Mode ──────────────────────────────────────────── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>HISTORICAL MODE</Text>
            <View style={[styles.badge, {
              backgroundColor: hist?.enabled ? C.gold : C.border,
            }]}>
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
                <View style={[styles.liveDot, { backgroundColor: C.gold, marginRight: 10 }]} />
                <Text style={styles.catLabel}>{cat}</Text>
              </View>
            ))
          )}
        </View>

        {/* ── Refresh ──────────────────────────────────────────────────── */}
        <TouchableOpacity style={styles.refreshBtn} onPress={() => load()} disabled={loading}>
          <Text style={styles.refreshBtnText}>Refresh</Text>
        </TouchableOpacity>

      </ScrollView>
    </SafeAreaView>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },

  header: {
    fontSize: 24, fontWeight: '800', color: C.textPrimary,
    textAlign: 'center', paddingTop: 12, paddingBottom: 8, letterSpacing: 1.5,
  },

  section: {
    marginHorizontal: 16, marginTop: 14,
    backgroundColor: C.card, borderRadius: 16,
    borderWidth: 1, borderColor: C.border, padding: 16,
  },
  sectionHeader: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 11, fontWeight: '800', color: C.textMuted, letterSpacing: 2,
  },

  badge: {
    minWidth: 22, height: 22, borderRadius: 11,
    alignItems: 'center', justifyContent: 'center', paddingHorizontal: 6,
  },
  badgeText: { fontSize: 11, fontWeight: '800', color: '#fff' },

  alarmCard: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 12, borderTopWidth: 1, borderColor: C.border,
  },
  alarmCardLeft: { flex: 1 },
  livePill: {
    flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-start',
    backgroundColor: 'rgba(63,185,80,0.12)',
    borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, marginBottom: 6,
  },
  liveDot: {
    width: 6, height: 6, borderRadius: 3,
    backgroundColor: C.liveGreen, marginRight: 5,
  },
  liveText: { fontSize: 10, fontWeight: '800', color: C.liveGreen, letterSpacing: 1 },
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

  refreshBtn: {
    margin: 16, padding: 14, borderRadius: 12,
    alignItems: 'center', backgroundColor: C.card,
    borderWidth: 1, borderColor: C.border,
  },
  refreshBtnText: { color: C.textMuted, fontWeight: '700', fontSize: 14 },
});
