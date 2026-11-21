import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { useTheme } from '../context/ThemeContext';
import { SERVER_URL } from '../config';

interface BasketballGame {
  id: string;
  homeTeam: string;
  homeLogo?: string;
  homeScore?: number;
  awayTeam: string;
  awayLogo?: string;
  awayScore?: number;
  status: 'pre' | 'in' | 'post';
  displayClock?: string;
  period?: number;
  kickoff?: string;
}

interface Props {
  game: BasketballGame;
  leagueId: string;
  yyyymmdd: string;
  visible: boolean;
  onClose: () => void;
  onSetAlarm?: () => void;
}

type Tab = 'details' | 'boxscore' | 'stats';

const BS_COLS = ['MIN', 'PTS', 'REB', 'AST', 'STL', 'BLK'] as const;
const minToNum = (m: any) => parseInt(String(m ?? '0').split(':')[0], 10);
const getStat = (p: any, col: string): string => {
  const v = col === 'MIN' ? p.minutes : p[col];
  return v != null ? String(v) : '—';
};
const shortName = (full: string) => {
  const parts = full.trim().split(' ');
  return parts.length > 1 ? `${parts[0][0]}. ${parts.slice(1).join(' ')}` : full;
};

// Module-level in-memory cache: finished games never change; live games expire after 30s.
const _cache = new Map<string, { data: any; ts: number }>();
const LIVE_TTL_MS = 30_000;
const _STORE = (id: string) => `bsum_v1_${id}`; // AsyncStorage key (survives kills)

export function BasketballSummaryView({ game, leagueId, yyyymmdd, visible, onClose, onSetAlarm }: Props) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [summary, setSummary] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>('details');

  useEffect(() => {
    if (!visible) return;

    const doFetch = async (showSpinner: boolean) => {
      if (showSpinner) setLoading(true);
      try {
        const data = await fetch(
          `${SERVER_URL}/basketball/event/${encodeURIComponent(game.id)}/summary`
        ).then(r => r.ok ? r.json() : Promise.reject(r.status));
        const entry = { data, ts: Date.now() };
        _cache.set(game.id, entry);
        setSummary(data);
        // Persist so the next cold start can show data instantly
        AsyncStorage.setItem(_STORE(game.id), JSON.stringify(entry)).catch(() => {});
      } catch {}
      setLoading(false);
    };

    // 1. In-memory hit (same session, still fresh)
    const inMem = _cache.get(game.id);
    const inMemStale = inMem && game.status === 'in' && Date.now() - inMem.ts > LIVE_TTL_MS;
    if (inMem && !inMemStale) {
      setSummary(inMem.data);
      setLoading(false);
      return;
    }

    // 2. AsyncStorage hit (survives app kill — instant display on cold start)
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(_STORE(game.id));
        if (raw) {
          const entry: { data: any; ts: number } = JSON.parse(raw);
          const asyncStale = game.status === 'in' && Date.now() - entry.ts > LIVE_TTL_MS;
          if (!asyncStale) {
            _cache.set(game.id, entry);
            setSummary(entry.data);
            setLoading(false);
            // Finished game: cached data is final — no need to refetch
            if (game.status === 'post') return;
            // Live game: show stale then silently refresh
            doFetch(false);
            return;
          }
        }
      } catch {}
      // 3. No cache (or stale live game): show spinner and fetch
      setSummary(null);
      doFetch(true);
    })();
  }, [visible, game.id, game.status]);

  const detail     = summary?.detail ?? null;
  const teamStats  = summary?.team_stats ?? null;
  const boxScore   = summary?.box_score ?? null;

  const homeRef    = detail?.home_team;
  const awayRef    = detail?.away_team;
  const leagueName = detail?.league?.name ?? leagueId.replace(/_/g, ' ').toUpperCase();
  const roundNum   = detail?.round_number;
  const periods: Record<string, [number, number]> = detail?.periods_score ?? {};
  const prediction = detail?.prediction ?? null;
  const isLive     = game.status === 'in';

  const statusLabel =
    game.status === 'post' ? 'FINAL'
    : game.status === 'in'  ? `Q${game.period ?? ''} ${game.displayClock ?? ''}`.trim()
    : game.kickoff ?? 'Upcoming';

  // ── Details: quarters ─────────────────────────────────────────────────────

  const renderQuarters = () => {
    const sorted = Object.entries(periods).sort(([a], [b]) =>
      parseInt(a.replace('period', '')) - parseInt(b.replace('period', ''))
    );
    if (!sorted.length) return null;

    const labels = sorted.map(([k]) => {
      const n = parseInt(k.replace('period', ''));
      return n <= 4 ? `Q${n}` : `OT${n - 4}`;
    });

    return (
      <View style={[styles.card, { backgroundColor: colors.card }]}>
        <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>QUARTER BY QUARTER</Text>
        <View style={styles.qRow}>
          <Text style={[styles.qTeamCol, { color: colors.textMuted }]} />
          {labels.map(l => <Text key={l} style={[styles.qCell, styles.qHeader, { color: colors.textMuted }]}>{l}</Text>)}
          <Text style={[styles.qCell, styles.qHeader, { color: colors.accentBlue }]}>T</Text>
        </View>
        {([
          { ref: awayRef, fallback: game.awayTeam, score: game.awayScore, idx: 1 },
          { ref: homeRef, fallback: game.homeTeam, score: game.homeScore, idx: 0 },
        ] as const).map(({ ref, fallback, score, idx }) => (
          <View key={String(idx)} style={styles.qRow}>
            <Text style={[styles.qTeamCol, { color: colors.textPrimary }]} numberOfLines={1}>
              {ref?.short_name ?? fallback}
            </Text>
            {sorted.map(([k, v]) => (
              <Text key={k} style={[styles.qCell, { color: v[idx] < 0 ? colors.textMuted : colors.textPrimary }]}>
                {v[idx] < 0 ? '—' : v[idx]}
              </Text>
            ))}
            <Text style={[styles.qCell, styles.qTotal, { color: colors.textPrimary }]}>{score ?? '—'}</Text>
          </View>
        ))}
      </View>
    );
  };

  // ── Details: prediction ───────────────────────────────────────────────────

  const renderPrediction = () => {
    if (!prediction) return null;
    const homeWinPct = Math.round((prediction.prob_home_win ?? 0) * 100);
    const awayWinPct = 100 - homeWinPct;
    const overPct    = prediction.prob_over_215 != null ? Math.round(prediction.prob_over_215 * 100) : null;

    return (
      <View style={[styles.card, { backgroundColor: colors.card }]}>
        <View style={styles.cardTitleRow}>
          <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>PREDICTION</Text>
          {prediction.is_correct != null && (
            <Text style={{ fontSize: 11, fontWeight: '700', color: prediction.is_correct ? '#3fb950' : colors.textMuted }}>
              {prediction.is_correct ? 'Model called it ✓' : 'Model missed'}
            </Text>
          )}
        </View>

        <Text style={[styles.predSubtitle, { color: colors.textMuted }]}>WIN PROBABILITY</Text>
        <View style={styles.probRow}>
          <Text style={[styles.probPct, { color: colors.textPrimary }]}>{awayWinPct}%</Text>
          <View style={styles.probBar}>
            <View style={[styles.probFill, { flex: awayWinPct, backgroundColor: '#ef4444' }]} />
            <View style={[styles.probFill, { flex: homeWinPct, backgroundColor: colors.accentBlue }]} />
          </View>
          <Text style={[styles.probPct, { color: colors.textPrimary, textAlign: 'right' }]}>{homeWinPct}%</Text>
        </View>
        <View style={styles.probTeamRow}>
          <Text style={[styles.probTeamLabel, { color: colors.textMuted }]}>{awayRef?.short_name ?? game.awayTeam}</Text>
          <Text style={[styles.probTeamLabel, { color: colors.textMuted, textAlign: 'right' }]}>{homeRef?.short_name ?? game.homeTeam}</Text>
        </View>

        {prediction.elo_away != null && (
          <View style={styles.eloRow}>
            <View style={styles.eloBlock}>
              <Text style={[styles.eloVal, { color: '#ef4444' }]}>{Math.round(prediction.elo_away)}</Text>
              <Text style={[styles.eloLabel, { color: colors.textMuted }]}>{awayRef?.short_name ?? game.awayTeam}</Text>
            </View>
            <Text style={[styles.eloSep, { color: colors.textMuted }]}>ELO</Text>
            <View style={[styles.eloBlock, { alignItems: 'flex-end' }]}>
              <Text style={[styles.eloVal, { color: colors.accentBlue }]}>{Math.round(prediction.elo_home)}</Text>
              <Text style={[styles.eloLabel, { color: colors.textMuted }]}>{homeRef?.short_name ?? game.homeTeam}</Text>
            </View>
          </View>
        )}

        {overPct != null && (
          <View style={[styles.overRow, { borderTopColor: colors.border }]}>
            <Text style={[styles.overLabel, { color: colors.textMuted }]}>OVER 215</Text>
            <View style={[styles.overBarBg, { backgroundColor: colors.border }]}>
              <View style={[styles.overBarFill, { width: `${overPct}%` as any, backgroundColor: '#f97316' }]} />
            </View>
            <Text style={[styles.overPct, { color: '#f97316' }]}>{overPct}%</Text>
          </View>
        )}

        {prediction.confidence && (
          <Text style={[styles.confidence, { color: colors.textMuted }]}>
            Confidence: {prediction.confidence.charAt(0).toUpperCase() + prediction.confidence.slice(1)}
          </Text>
        )}
      </View>
    );
  };

  // ── Details: top performers ───────────────────────────────────────────────

  const renderTopPerformers = () => {
    const homeBox: any[] = boxScore?.home_box ?? [];
    const awayBox: any[] = boxScore?.away_box ?? [];
    if (!homeBox.length && !awayBox.length) return null;

    const topN = (players: any[], n = 3) =>
      [...players].sort((a, b) => (b.PTS ?? 0) - (a.PTS ?? 0)).slice(0, n);

    const renderPerformer = (p: any) => {
      const statLine = [
        p.PTS != null ? `${p.PTS} PTS` : null,
        p.REB != null ? `${p.REB} REB` : null,
        p.AST != null ? `${p.AST} AST` : null,
      ].filter(Boolean).join('  ');
      return (
        <View key={p.player_id ?? p.player_name} style={styles.performerRow}>
          <Text style={[styles.performerName, { color: colors.textPrimary }]} numberOfLines={1}>
            {shortName(p.player_name ?? '')}
          </Text>
          <Text style={[styles.performerStats, { color: colors.textMuted }]}>{statLine}</Text>
        </View>
      );
    };

    return (
      <View style={[styles.card, { backgroundColor: colors.card }]}>
        <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>TOP PERFORMERS</Text>
        <View style={styles.performersGrid}>
          <View style={[styles.performerCol, { borderRightWidth: StyleSheet.hairlineWidth, borderRightColor: colors.border }]}>
            <Text style={[styles.performerTeamLabel, { color: '#ef4444' }]} numberOfLines={1}>
              ● {awayRef?.short_name ?? game.awayTeam}
            </Text>
            {topN(awayBox).map(renderPerformer)}
          </View>
          <View style={styles.performerCol}>
            <Text style={[styles.performerTeamLabel, { color: colors.accentBlue }]} numberOfLines={1}>
              {homeRef?.short_name ?? game.homeTeam} ●
            </Text>
            {topN(homeBox).map(renderPerformer)}
          </View>
        </View>
      </View>
    );
  };

  // ── Box Score tab ─────────────────────────────────────────────────────────

  const renderTeamTable = (players: any[], name: string) => {
    const sorted = (players ?? [])
      .filter(p => minToNum(p.minutes ?? '0') > 0)
      .sort((a, b) => {
        if (a.starter !== b.starter) return a.starter ? -1 : 1;
        return minToNum(b.minutes ?? '0') - minToNum(a.minutes ?? '0');
      });
    return (
      <View key={name} style={styles.teamBlock}>
        <View style={styles.bsTeamHeader}>
          <Text style={[styles.bsTeamName, { color: colors.accentBlue }]}>{name}</Text>
          {isLive && (
            <View style={styles.livePill}>
              <Text style={styles.liveText}>LIVE</Text>
            </View>
          )}
        </View>
        <View style={styles.bsRow}>
          <Text style={[styles.playerCol, styles.headerText]}>PLAYER</Text>
          {BS_COLS.map(c => <Text key={c} style={[styles.statCol, styles.headerText]}>{c}</Text>)}
        </View>
        {sorted.map((p, i) => (
          <View key={p.player_id ?? i} style={[styles.bsRow, i % 2 === 0 && { backgroundColor: 'rgba(255,255,255,0.03)' }]}>
            <Text style={[styles.playerCol, { color: colors.textPrimary }]} numberOfLines={1}>
              {p.starter ? '● ' : '  '}{shortName(p.player_name ?? '')}
            </Text>
            {BS_COLS.map(c => (
              <Text key={c} style={[styles.statCol, { color: colors.textPrimary }]}>{getStat(p, c)}</Text>
            ))}
          </View>
        ))}
      </View>
    );
  };

  // ── Statistics tab ────────────────────────────────────────────────────────

  const renderStatRow = (item: any, idx: number) => {
    const homeVal = item.homeValue ?? item.home_value ?? 0;
    const awayVal = item.awayValue ?? item.away_value ?? 0;
    const total = homeVal + awayVal;
    const homePct = total > 0 ? homeVal / total : 0.5;
    const homeWins = (item.compareCode ?? item.compare_code) === 1;
    const awayWins = (item.compareCode ?? item.compare_code) === 2;

    // Split "39/84 (46.4 %)" into fraction "39/84" + pct "(46.4%)"
    const fmtStat = (raw: string | undefined | null, fallback: string) => {
      const s = (raw ?? fallback).trim();
      const paren = s.indexOf('(');
      if (paren === -1) return { main: s, pct: null };
      return {
        main: s.slice(0, paren).trim(),
        pct: s.slice(paren).replace(/\s+/g, ''),
      };
    };

    const away = fmtStat(item.away, String(awayVal));
    const home = fmtStat(item.home, String(homeVal));

    return (
      <View key={item.key ?? item.name ?? idx} style={styles.statRow}>
        <View style={[styles.statSideCol, { alignItems: 'flex-start' }]}>
          <Text style={[styles.statSideMain, { color: awayWins ? '#ef4444' : colors.textPrimary }]}>
            {away.main}
          </Text>
          {away.pct && (
            <Text style={[styles.statSidePct, { color: awayWins ? '#ef444499' : colors.textMuted }]}>
              {away.pct}
            </Text>
          )}
        </View>

        <View style={styles.statBarArea}>
          <Text style={[styles.statName, { color: colors.textMuted }]} numberOfLines={1}>
            {item.name ?? item.key}
          </Text>
          <View style={styles.statBarTrack}>
            <View style={{ flex: 1 - homePct, height: 4, backgroundColor: awayWins ? '#ef4444' : colors.textMuted + '55', borderRadius: 2 }} />
            <View style={{ width: 2 }} />
            <View style={{ flex: homePct, height: 4, backgroundColor: homeWins ? colors.accentBlue : colors.textMuted + '55', borderRadius: 2 }} />
          </View>
        </View>

        <View style={[styles.statSideCol, { alignItems: 'flex-end' }]}>
          <Text style={[styles.statSideMain, { color: homeWins ? colors.accentBlue : colors.textPrimary }]}>
            {home.main}
          </Text>
          {home.pct && (
            <Text style={[styles.statSidePct, { color: homeWins ? colors.accentBlue + '99' : colors.textMuted }]}>
              {home.pct}
            </Text>
          )}
        </View>
      </View>
    );
  };

  const renderStatsTab = () => {
    const scope = (teamStats?.statistics ?? [])[0];
    const groups: any[] = scope?.groups ?? [];
    if (!groups.length) return (
      <View style={styles.centered}>
        <Text style={[styles.emptyText, { color: colors.textMuted }]}>Team statistics unavailable.</Text>
      </View>
    );
    return (
      <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
        <View style={[styles.statsHeaderRow, { backgroundColor: colors.card, borderRadius: 10, padding: 10, marginBottom: 12 }]}>
          <Text style={[styles.statsTeamLabel, { color: '#ef4444' }]} numberOfLines={1}>{awayRef?.short_name ?? game.awayTeam}</Text>
          <Text style={[styles.statsSep, { color: colors.textMuted }]}>vs</Text>
          <Text style={[styles.statsTeamLabel, { color: colors.accentBlue, textAlign: 'right' }]} numberOfLines={1}>{homeRef?.short_name ?? game.homeTeam}</Text>
        </View>
        {groups.map((g: any) => (
          <View key={g.groupName} style={[styles.card, { backgroundColor: colors.card }]}>
            <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>{String(g.groupName ?? '').toUpperCase()}</Text>
            {(g.statisticsItems ?? []).map((item: any, i: number) => renderStatRow(item, i))}
          </View>
        ))}
        <View style={{ height: 32 }} />
      </ScrollView>
    );
  };

  // ── Match header ──────────────────────────────────────────────────────────

  const renderHeader = () => (
    <View style={[styles.matchHeader, { borderBottomColor: colors.border }]}>
      <Text style={[styles.leagueChip, { color: colors.textMuted }]}>
        {leagueName}{roundNum ? ` · R${roundNum}` : ''}
      </Text>
      <View style={styles.scoreRow}>
        {/* Away */}
        <View style={styles.teamSide}>
          {(awayRef?.logo || game.awayLogo)
            ? <Image source={{ uri: awayRef?.logo ?? game.awayLogo }} style={styles.teamLogo} resizeMode="contain" />
            : <View style={[styles.teamLogoFallback, { backgroundColor: colors.card }]}>
                <Text style={[styles.teamLogoInitial, { color: colors.textPrimary }]}>{game.awayTeam.charAt(0)}</Text>
              </View>
          }
          <Text style={[styles.teamSideLabel, { color: colors.textPrimary }]} numberOfLines={2}>
            {awayRef?.name ?? game.awayTeam}
          </Text>
        </View>

        {/* Center */}
        <View style={styles.centerBlock}>
          <Text style={[styles.bigScore, { color: colors.textPrimary }]}>
            {game.awayScore != null ? `${game.awayScore}` : '—'}
          </Text>
          <Text style={[styles.scoreDash, { color: colors.textMuted }]}>–</Text>
          <Text style={[styles.bigScore, { color: colors.textPrimary }]}>
            {game.homeScore != null ? `${game.homeScore}` : '—'}
          </Text>
        </View>

        {/* Home */}
        <View style={styles.teamSide}>
          {(homeRef?.logo || game.homeLogo)
            ? <Image source={{ uri: homeRef?.logo ?? game.homeLogo }} style={styles.teamLogo} resizeMode="contain" />
            : <View style={[styles.teamLogoFallback, { backgroundColor: colors.card }]}>
                <Text style={[styles.teamLogoInitial, { color: colors.textPrimary }]}>{game.homeTeam.charAt(0)}</Text>
              </View>
          }
          <Text style={[styles.teamSideLabel, { color: colors.textPrimary }]} numberOfLines={2}>
            {homeRef?.name ?? game.homeTeam}
          </Text>
        </View>
      </View>

      {/* Status badge */}
      <View style={[styles.statusBadge, isLive && { backgroundColor: 'rgba(63,185,80,0.15)' }]}>
        <Text style={[styles.statusText, { color: isLive ? '#3fb950' : colors.textMuted }]}>{statusLabel}</Text>
      </View>
    </View>
  );

  // ── Tab bar ───────────────────────────────────────────────────────────────

  const TABS: { key: Tab; label: string; icon: string }[] = [
    { key: 'details',  label: 'Details',    icon: 'list-outline' },
    { key: 'boxscore', label: 'Box Score',  icon: 'grid-outline' },
    { key: 'stats',    label: 'Statistics', icon: 'bar-chart-outline' },
  ];

  const renderTabBar = () => (
    <View style={[styles.tabBar, { borderBottomColor: colors.border, backgroundColor: colors.bg }]}>
      {TABS.map(({ key, label, icon }) => {
        const active = tab === key;
        return (
          <TouchableOpacity key={key} style={styles.tabItem} onPress={() => setTab(key)} activeOpacity={0.7}>
            <Ionicons name={icon as any} size={15} color={active ? colors.textPrimary : colors.textMuted} />
            <Text style={[styles.tabLabel, { color: active ? colors.textPrimary : colors.textMuted, fontWeight: active ? '700' : '500' }]}>
              {label}
            </Text>
            {active && <View style={[styles.tabIndicator, { backgroundColor: colors.accentBlue }]} />}
          </TouchableOpacity>
        );
      })}
    </View>
  );

  // ── Content ───────────────────────────────────────────────────────────────

  const renderContent = () => {
    if (loading && !summary) {
      return <View style={styles.centered}><ActivityIndicator color={colors.accentBlue} size="large" /></View>;
    }
    if (!summary) {
      return (
        <View style={styles.centered}>
          <Text style={{ fontSize: 32, marginBottom: 8 }}>🏀</Text>
          <Text style={[styles.emptyText, { color: colors.textMuted }]}>
            {game.status === 'pre' ? 'Stats available once the game tips off.' : 'Summary unavailable.'}
          </Text>
        </View>
      );
    }
    if (tab === 'details') {
      return (
        <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
          {renderQuarters()}
          {renderPrediction()}
          {renderTopPerformers()}
          <View style={{ height: 32 }} />
        </ScrollView>
      );
    }
    if (tab === 'boxscore') {
      return (
        <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
          {boxScore
            ? <>
                {renderTeamTable(boxScore.away_box, awayRef?.name ?? game.awayTeam)}
                {renderTeamTable(boxScore.home_box, homeRef?.name ?? game.homeTeam)}
              </>
            : <View style={styles.centered}><Text style={[styles.emptyText, { color: colors.textMuted }]}>Boxscore unavailable.</Text></View>
          }
          <View style={{ height: 32 }} />
        </ScrollView>
      );
    }
    return renderStatsTab();
  };

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={[styles.container, { backgroundColor: colors.bg }]}>
        {/* Nav bar */}
        <View style={[styles.navBar, { borderBottomColor: colors.border, paddingTop: Math.max(insets.top, 12) }]}>
          <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
            <Ionicons name="chevron-down" size={24} color={colors.textPrimary} />
          </TouchableOpacity>
          <Text style={[styles.navTitle, { color: colors.textMuted }]}>Game Summary</Text>
          {isLive && onSetAlarm ? (
            <TouchableOpacity onPress={onSetAlarm} hitSlop={8} style={styles.closeBtn}>
              <Ionicons name="notifications-outline" size={22} color={colors.orange} />
            </TouchableOpacity>
          ) : (
            <View style={{ width: 40 }} />
          )}
        </View>

        {renderHeader()}
        {renderTabBar()}
        {renderContent()}
      </View>
    </Modal>
  );
}

// ── Background pre-fetch ──────────────────────────────────────────────────────
// Call this after the feed renders finished games. If the summary is not yet
// cached, fetches it silently so the user gets instant data when they tap.

export async function prefetchBasketballSummary(gameId: string): Promise<void> {
  try {
    const raw = await AsyncStorage.getItem(_STORE(gameId));
    if (raw) return; // already cached
  } catch {}
  try {
    const data = await fetch(
      `${SERVER_URL}/basketball/event/${encodeURIComponent(gameId)}/summary`
    ).then(r => r.ok ? r.json() : null);
    if (data) {
      const entry = { data, ts: Date.now() };
      _cache.set(gameId, entry);
      AsyncStorage.setItem(_STORE(gameId), JSON.stringify(entry)).catch(() => {});
    }
  } catch {}
}

const styles = StyleSheet.create({
  container:   { flex: 1 },

  // Nav
  navBar:      { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: StyleSheet.hairlineWidth },
  closeBtn:    { width: 40 },
  navTitle:    { fontSize: 15, fontWeight: '600' },

  // Match header
  matchHeader:      { paddingTop: 14, paddingBottom: 12, paddingHorizontal: 16, borderBottomWidth: StyleSheet.hairlineWidth, alignItems: 'center', gap: 10 },
  leagueChip:       { fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  scoreRow:         { flexDirection: 'row', alignItems: 'center', width: '100%' },
  teamSide:         { flex: 1, alignItems: 'center', gap: 6 },
  teamLogo:         { width: 52, height: 52 },
  teamLogoFallback: { width: 52, height: 52, borderRadius: 26, alignItems: 'center', justifyContent: 'center' },
  teamLogoInitial:  { fontSize: 20, fontWeight: '800' },
  teamSideLabel:    { fontSize: 12, fontWeight: '600', textAlign: 'center', lineHeight: 16 },
  centerBlock:      { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 4 },
  bigScore:         { fontSize: 32, fontWeight: '800', minWidth: 38, textAlign: 'center', letterSpacing: -1 },
  scoreDash:        { fontSize: 22, fontWeight: '300' },
  statusBadge:      { paddingHorizontal: 10, paddingVertical: 3, borderRadius: 6 },
  statusText:       { fontSize: 11, fontWeight: '800', letterSpacing: 0.8 },

  // Tab bar
  tabBar:      { flexDirection: 'row', borderBottomWidth: StyleSheet.hairlineWidth },
  tabItem:     { flex: 1, alignItems: 'center', justifyContent: 'center', paddingVertical: 10, gap: 3, position: 'relative' },
  tabLabel:    { fontSize: 10, letterSpacing: 0.3 },
  tabIndicator:{ position: 'absolute', bottom: 0, left: 12, right: 12, height: 2, borderRadius: 1 },

  // Common
  centered:      { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 },
  emptyText:     { fontSize: 14, textAlign: 'center', lineHeight: 20 },
  scrollContent: { paddingHorizontal: 16, paddingTop: 16 },
  card:          { borderRadius: 12, padding: 14, marginBottom: 12 },
  sectionTitle:  { fontSize: 10, fontWeight: '800', letterSpacing: 1.5, marginBottom: 10 },
  cardTitleRow:  { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 },

  // Quarters
  qRow:      { flexDirection: 'row', alignItems: 'center', marginBottom: 5 },
  qTeamCol:  { flex: 1, fontSize: 13, fontWeight: '600' },
  qCell:     { width: 34, textAlign: 'center', fontSize: 13 },
  qHeader:   { fontSize: 10, fontWeight: '700' },
  qTotal:    { fontWeight: '800' },

  // Prediction
  predSubtitle:   { fontSize: 9, fontWeight: '700', letterSpacing: 1, marginBottom: 6 },
  probRow:        { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 },
  probPct:        { fontSize: 14, fontWeight: '800', width: 38 },
  probBar:        { flex: 1, flexDirection: 'row', height: 8, borderRadius: 4, overflow: 'hidden' },
  probFill:       { height: 8 },
  probTeamRow:    { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 12 },
  probTeamLabel:  { fontSize: 10, fontWeight: '600', flex: 1 },
  eloRow:         { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 },
  eloBlock:       { alignItems: 'flex-start' },
  eloVal:         { fontSize: 22, fontWeight: '800' },
  eloLabel:       { fontSize: 10, fontWeight: '600' },
  eloSep:         { fontSize: 9, fontWeight: '700', letterSpacing: 1 },
  overRow:        { flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 10, borderTopWidth: StyleSheet.hairlineWidth, marginTop: 4 },
  overLabel:      { fontSize: 9, fontWeight: '700', letterSpacing: 0.5, width: 56 },
  overBarBg:      { flex: 1, height: 6, borderRadius: 3, overflow: 'hidden' },
  overBarFill:    { height: 6 },
  overPct:        { fontSize: 13, fontWeight: '700', width: 36, textAlign: 'right' },
  confidence:     { fontSize: 10, marginTop: 8 },

  // Top performers
  performersGrid:     { flexDirection: 'row', gap: 0 },
  performerCol:       { flex: 1, paddingRight: 10, gap: 6 },
  performerTeamLabel: { fontSize: 10, fontWeight: '800', letterSpacing: 1, marginBottom: 2 },
  performerRow:       { gap: 2 },
  performerName:      { fontSize: 12, fontWeight: '600' },
  performerStats:     { fontSize: 11 },

  // Box score
  teamBlock:    { marginBottom: 24 },
  bsTeamHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 },
  bsTeamName:   { fontSize: 13, fontWeight: '800', letterSpacing: 1.5, flex: 1 },
  livePill:     { backgroundColor: 'rgba(63,185,80,0.15)', borderRadius: 5, paddingHorizontal: 6, paddingVertical: 2 },
  liveText:     { fontSize: 9, fontWeight: '800', color: '#3fb950', letterSpacing: 1 },
  bsRow:        { flexDirection: 'row', alignItems: 'center', paddingVertical: 4, paddingHorizontal: 2 },
  headerText:   { color: '#888', fontSize: 9, fontWeight: '800', letterSpacing: 1 },
  playerCol:    { flex: 1, fontSize: 12, fontWeight: '500' },
  statCol:      { width: 32, textAlign: 'center', fontSize: 11, fontWeight: '600' },

  // Statistics
  statsHeaderRow:  { flexDirection: 'row', alignItems: 'center' },
  statsTeamLabel:  { flex: 1, fontSize: 13, fontWeight: '800' },
  statsSep:        { fontSize: 11, paddingHorizontal: 8 },
  statRow:         { flexDirection: 'row', alignItems: 'center', marginBottom: 14, gap: 6 },
  statSideCol:     { width: 68, justifyContent: 'center', gap: 1 },
  statSideMain:    { fontSize: 13, fontWeight: '700', fontVariant: ['tabular-nums'] },
  statSidePct:     { fontSize: 10, fontWeight: '500', fontVariant: ['tabular-nums'] },
  statBarArea:     { flex: 1, gap: 4 },
  statName:        { fontSize: 10, textAlign: 'center' },
  statBarTrack:    { flexDirection: 'row', height: 4, alignItems: 'center' },
});
