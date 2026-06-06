import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from 'expo-router';

import { useTheme } from '../../context/ThemeContext';
import { useLanguage } from '../../context/LanguageContext';
import { HamburgerButton } from '../../components/drawer-menu';
import { SportFilterPills, SportFilter } from '../../components/sport-filter-pills';
import { CompactMatchRow } from '../../components/compact-match-row';
import { LeagueLogo } from '../../components/league-logo';
import { LeagueCatalog } from '../../services/league-catalog';
import { getMatchesCached, invalidateMatchCache } from '../../services/soccer-service';
import { RefreshBar } from '../../components/refresh-bar';
import {
  getFollowStore,
  followLeague,
  unfollowLeague,
  isLeagueFollowed,
} from '../../services/follow-service';
import type { FollowStore } from '../../services/follow-service';
import type { SoccerMatch } from '../../types/soccer';
import { isLiveLike, displayMinute, soccerMatchRoute } from '../../types/soccer';
import { MatchSummaryView } from '../../views/match-summary-view';
import { BasketballSummaryView } from '../../views/basketball-summary-view';
import { BasketballAlarmSheet } from '../../views/basketball-alarm-sheet';
import { SoccerAlarmSheet } from '../../views/soccer-alarm-sheet';
import { tabHeaderTitle } from '../../constants/tokens';
import { SERVER_URL } from '../../config';
import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Types ─────────────────────────────────────────────────────────────────────

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

interface LeagueGroup {
  leagueId: string;
  sport: 'basketball' | 'football';
  leagueLabel: string;
  soccer: SoccerMatch[];
  basketball: BasketballGame[];
  isStarred: boolean;
}

interface DateGroup {
  yyyymmdd: string;
  title: string;
  leagueGroups: LeagueGroup[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function todayStr(): string {
  return localDateStr(new Date());
}

function dateLabel(yyyymmdd: string, t: (k: string) => string): string {
  const today = todayStr();
  const y = parseInt(yyyymmdd.slice(0, 4), 10);
  const m = parseInt(yyyymmdd.slice(4, 6), 10) - 1;
  const d = parseInt(yyyymmdd.slice(6, 8), 10);
  const date = new Date(y, m, d);
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);

  if (yyyymmdd === today) return t('home.today');
  if (date.toDateString() === tomorrow.toDateString()) return t('home.tomorrow');
  if (date.toDateString() === yesterday.toDateString()) return t('home.yesterday');
  return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

function localDateStr(d: Date): string {
  // Use local date (not UTC) to match the server's TZ-aware calendar day
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}${m}${day}`;
}

function dateRange(daysBack: number, daysForward: number): string[] {
  const dates: string[] = [];
  const now = new Date();
  for (let i = -daysBack; i <= daysForward; i++) {
    const d = new Date(now);
    d.setDate(d.getDate() + i);
    dates.push(localDateStr(d));
  }
  return dates;
}


const _BB_STORE = (l: string, d: string) => `bb_v1_${l}_${d}`;

async function fetchBasketballGames(leagueId: string, date: string): Promise<BasketballGame[]> {
  const isPast = date < localDateStr(new Date());

  // Past dates: finished games never change — serve from AsyncStorage if available
  if (isPast) {
    try {
      const raw = await AsyncStorage.getItem(_BB_STORE(leagueId, date));
      if (raw) return JSON.parse(raw) as BasketballGame[];
    } catch {}
  }

  try {
    const resp = await fetch(`${SERVER_URL}/games?date=${date}&league=${leagueId}`);
    if (!resp.ok) return [];
    const data = await resp.json();
    const games = data.games ?? [];
    const result: BasketballGame[] = games.map((ev: any) => {
      const comps = ev.competitions?.[0] ?? {};
      const competitors = comps.competitors ?? [];
      const home = competitors.find((c: any) => c.homeAway === 'home') ?? {};
      const away = competitors.find((c: any) => c.homeAway === 'away') ?? {};
      const state: string = ev.status?.type?.state ?? 'pre';
      const isLive = state === 'in';
      const isFinal = state === 'post';
      return {
        id: ev.id,
        homeTeam: home.team?.abbreviation ?? home.team?.displayName ?? '?',
        homeLogo: home.team?.logo || undefined,
        homeScore: (isLive || isFinal) && home.score != null ? parseInt(home.score, 10) : undefined,
        awayTeam: away.team?.abbreviation ?? away.team?.displayName ?? '?',
        awayLogo: away.team?.logo || undefined,
        awayScore: (isLive || isFinal) && away.score != null ? parseInt(away.score, 10) : undefined,
        status: isLive ? 'in' : isFinal ? 'post' : 'pre',
        displayClock: ev.status?.displayClock,
        period: ev.status?.period,
        kickoff: ev.date,
      } satisfies BasketballGame;
    });
    // Persist past dates so future Load Earlier is instant and consistent
    if (isPast && result.length > 0) {
      AsyncStorage.setItem(_BB_STORE(leagueId, date), JSON.stringify(result)).catch(() => {});
    }
    return result;
  } catch {
    return [];
  }
}

// ── Main component ────────────────────────────────────────────────────────────

const DAYS_BACK_INITIAL = 1;
const DAYS_FORWARD      = 7;
const DAYS_BACK_LOAD    = 7;
const MAX_DAYS_BACK     = 30;

const SPORTS_CACHE_KEY = 'sports_cache_v2';
interface SportsCache { dateGroups: DateGroup[]; daysBack: number; savedAt: number; }

export default function SportsScreen() {
  const { colors } = useTheme();
  const { t } = useLanguage();

  const [sportFilter, setSportFilter]       = useState<SportFilter>('all');
  const [dateGroups, setDateGroups]         = useState<DateGroup[]>([]);
  const [loading, setLoading]               = useState(true);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [refreshing, setRefreshing]         = useState(false);
  const [bgRefreshing, setBgRefreshing]     = useState(false);
  const [daysBack, setDaysBack]             = useState(DAYS_BACK_INITIAL);

  const dateGroupsRef = useRef<DateGroup[]>([]);
  useEffect(() => { dateGroupsRef.current = dateGroups; }, [dateGroups]);
  const daysBackRef = useRef(DAYS_BACK_INITIAL);
  useEffect(() => { daysBackRef.current = daysBack; }, [daysBack]);
  const liveIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Each sport filter has its own date range — switching resets to the default window
  const handleFilterChange = useCallback((filter: SportFilter) => {
    setSportFilter(filter);
    setDaysBack(DAYS_BACK_INITIAL);
  }, []);

  // Slice dateGroups to only the dates within the current daysBack window
  const visibleGroups = useMemo(() => {
    const cutoff = localDateStr(new Date(Date.now() - daysBack * 24 * 60 * 60 * 1000));
    return dateGroups.filter(g => g.yyyymmdd >= cutoff);
  }, [dateGroups, daysBack]);
  const [followStore, setFollowStore]       = useState<FollowStore>({ teams: {}, leagues: {} });
  const [selectedMatch, setSelectedMatch]           = useState<SoccerMatch | null>(null);
  const [selectedSoccerAlarm, setSelectedSoccerAlarm] = useState<SoccerMatch | null>(null);
  const [selectedBball, setSelectedBball]           = useState<{ game: BasketballGame; leagueId: string; yyyymmdd: string } | null>(null);
  const [selectedBballAlarm, setSelectedBballAlarm] = useState<{ game: BasketballGame; leagueId: string; yyyymmdd: string } | null>(null);

  useEffect(() => {
    getFollowStore().then(setFollowStore).catch(() => {});
  }, []);

  useFocusEffect(
    useCallback(() => {
      getFollowStore().then(setFollowStore).catch(() => {});
    }, [])
  );

  // Accumulated results across all parallel requests (mutable ref, avoids stale closure)
  const accRef      = useRef<Map<string, DateGroup>>(new Map());
  const runIdRef    = useRef(0);
  const flushRef    = useRef<ReturnType<typeof setInterval> | null>(null);

  // persist=true → write AsyncStorage (final commit only, not intermediate flushes)
  const commitGroups = useCallback((runId: number, persist = true, back = DAYS_BACK_INITIAL) => {
    if (runId !== runIdRef.current) return;
    if (accRef.current.size === 0) return;
    const groups = Array.from(accRef.current.values())
      .map(g => ({
        ...g,
        leagueGroups: [...g.leagueGroups].sort((a, b) => a.leagueLabel.localeCompare(b.leagueLabel)),
      }))
      .sort((a, b) => a.yyyymmdd.localeCompare(b.yyyymmdd));
    setDateGroups(groups);
    setLoading(false);
    setBgRefreshing(false);
    if (persist) {
      AsyncStorage.setItem(
        SPORTS_CACHE_KEY,
        JSON.stringify({ dateGroups: groups, daysBack: back, savedAt: Date.now() } satisfies SportsCache)
      ).catch(() => {});
    }
  }, []);

  const upsertLeague = useCallback((yyyymmdd: string, group: LeagueGroup, runId: number) => {
    if (runId !== runIdRef.current) return;
    if (!accRef.current.has(yyyymmdd)) {
      accRef.current.set(yyyymmdd, {
        yyyymmdd,
        title: dateLabel(yyyymmdd, t),
        leagueGroups: [],
      });
    }
    const dateGroup = accRef.current.get(yyyymmdd)!;
    const idx = dateGroup.leagueGroups.findIndex(lg => lg.leagueId === group.leagueId);
    if (idx >= 0) {
      dateGroup.leagueGroups[idx] = group;
    } else {
      dateGroup.leagueGroups.push(group);
    }
    // Flush is driven by the interval in loadAllGames; no per-result update here.
  }, [t]);

  const loadAllGames = useCallback(async (back: number, { silent = false }: { silent?: boolean } = {}) => {
    const runId = ++runIdRef.current;
    accRef.current = new Map();
    if (silent) {
      setBgRefreshing(true);
    } else {
      setLoading(true);
      setDateGroups([]);
    }

    const allDates = dateRange(back, DAYS_FORWARD);
    const today = todayStr();
    const pastDates   = allDates.filter(d => d < today);
    const todayOnward = allDates.filter(d => d >= today);

    const footballLeagues   = LeagueCatalog.footballLeagues();
    const basketballLeagues = LeagueCatalog.basketballLeagues();

    const makeBballFetches = (dates: string[]) =>
      dates.flatMap(yyyymmdd =>
        basketballLeagues.map(async l => {
          const games = await fetchBasketballGames(l.id, yyyymmdd);
          if (games.length > 0) {
            const byId = games.filter((g, i, a) => a.findIndex(x => x.id === g.id) === i);
            const seen = new Map<string, BasketballGame>();
            for (const g of byId) {
              const key = [g.homeTeam, g.awayTeam].sort().join('|');
              const prev = seen.get(key);
              if (!prev) { seen.set(key, g); continue; }
              const gScore = g.status === 'in' ? 2 : g.status === 'post' ? 1 : 0;
              const pScore = prev.status === 'in' ? 2 : prev.status === 'post' ? 1 : 0;
              if (gScore > pScore) { seen.set(key, g); continue; }
              if (gScore === pScore && (g.kickoff ?? '') < (prev.kickoff ?? '')) seen.set(key, g);
            }
            upsertLeague(yyyymmdd, { leagueId: l.id, sport: 'basketball', leagueLabel: l.label, basketball: Array.from(seen.values()), soccer: [], isStarred: false }, runId);
          }
        })
      );

    const makeSoccerFetches = (dates: string[]) =>
      dates.flatMap(yyyymmdd =>
        footballLeagues.map(async l => {
          try {
            const matches = await getMatchesCached(l.id, yyyymmdd);
            if (matches.length > 0) {
              const deduped = matches.filter((m, i, a) => a.findIndex(x => x.id === m.id) === i);
              upsertLeague(yyyymmdd, { leagueId: l.id, sport: 'football', leagueLabel: l.label, soccer: deduped, basketball: [], isStarred: false }, runId);
            }
          } catch {}
        })
      );

    // Phase 1: fetch all past dates first (blocking) so they're in accRef before first flush.
    // This prevents yesterday's matches from "jumping in" 5-10s after initial render.
    if (pastDates.length > 0) {
      await Promise.allSettled([
        ...makeBballFetches(pastDates),
        ...makeSoccerFetches(pastDates),
      ]);
    }

    // Phase 2: start progressive flush now that past data is ready, then fetch today onward.
    if (!silent) {
      if (flushRef.current) clearInterval(flushRef.current);
      flushRef.current = setInterval(() => {
        commitGroups(runId, false);
      }, 400);
    }

    await Promise.allSettled([
      ...makeBballFetches(todayOnward),
      ...makeSoccerFetches(todayOnward),
    ]);

    // Stop progressive flushes and do the final authoritative commit with AsyncStorage write
    if (flushRef.current) { clearInterval(flushRef.current); flushRef.current = null; }
    commitGroups(runId, true, back);
  }, [t, upsertLeague, commitGroups]);

  // Mount: restore cache instantly, then refresh silently in background
  useEffect(() => {
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(SPORTS_CACHE_KEY);
        if (raw) {
          const parsed: SportsCache = JSON.parse(raw);
          // Only restore the default date window — old matches from a previous
          // Load Earlier session should not appear automatically on cold start.
          const cutoff = localDateStr(
            new Date(Date.now() - DAYS_BACK_INITIAL * 24 * 60 * 60 * 1000)
          );
          const defaultGroups = parsed.dateGroups
            .filter(g => g.yyyymmdd >= cutoff)
            .map(g => ({ ...g, title: dateLabel(g.yyyymmdd, t) }));
          if (defaultGroups.length > 0) {
            setDateGroups(defaultGroups);
            setLoading(false);
            loadAllGames(DAYS_BACK_INITIAL, { silent: true });
            return;
          }
        }
      } catch {}
      loadAllGames(DAYS_BACK_INITIAL, { silent: false });
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadEarlier = async () => {
    if (loadingEarlier || daysBack >= MAX_DAYS_BACK) return;
    const newBack = Math.min(daysBack + DAYS_BACK_LOAD, MAX_DAYS_BACK);
    setDaysBack(newBack);
    setLoadingEarlier(true);
    // silent=true: keep existing data visible while we fetch the expanded date range.
    // A single atomic commit replaces data at the end — no mid-flight flicker.
    await loadAllGames(newBack, { silent: true });
    setLoadingEarlier(false);
  };

  const onRefresh = async () => {
    setRefreshing(true);
    invalidateMatchCache();
    loadAllGames(daysBackRef.current, { silent: true }); // fire-and-forget
    setRefreshing(false);
  };

  // Live match polling — 60-second interval while screen is focused
  useFocusEffect(useCallback(() => {
    liveIntervalRef.current = setInterval(() => {
      const now = Date.now();
      const shouldRefresh = dateGroupsRef.current.some(dg =>
        dg.leagueGroups.some(lg =>
          lg.soccer.some(m => {
            if (isLiveLike(m.status) || m.status === 'unknown') return true;
            if (m.kickoff) {
              const t = new Date(m.kickoff).getTime();
              // Refresh if kickoff was within the last 3 hours or in the next 5 min
              return now > t - 5 * 60 * 1000 && now < t + 3 * 60 * 60 * 1000;
            }
            return false;
          })
        )
      );
      if (shouldRefresh) {
        invalidateMatchCache();
        loadAllGames(daysBackRef.current, { silent: true });
      }
    }, 60_000);
    return () => {
      if (liveIntervalRef.current) { clearInterval(liveIntervalRef.current); liveIntervalRef.current = null; }
    };
  }, [loadAllGames]));

  const toggleLeagueStar = async (leagueId: string) => {
    const isStarred = isLeagueFollowed(followStore, leagueId);
    if (isStarred) {
      await unfollowLeague(leagueId);
    } else {
      const info = LeagueCatalog.info(leagueId);
      await followLeague({ id: leagueId, label: info.label, sport: info.sport });
    }
    const updated = await getFollowStore();
    setFollowStore(updated);
  };

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: colors.bg }]}>
      {/* Header */}
      <View style={[styles.header, { borderBottomColor: colors.border }]}>
        <View style={styles.searchRow}>
          <HamburgerButton />
          <View style={[styles.searchPill, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Ionicons name="search-outline" size={15} color={colors.textMuted} />
            <Text style={[styles.searchPlaceholder, { color: colors.textMuted }]}>
              {t('sports.searchHint')}
            </Text>
          </View>
        </View>
        <Text style={[styles.headerTitle, { color: colors.textPrimary }]}>
          {t('sports.title')}
        </Text>
      </View>

      {/* Sport filter pills */}
      <SportFilterPills value={sportFilter} onChange={handleFilterChange} />
      <RefreshBar visible={bgRefreshing} />

      {/* Content */}
      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator color={colors.orange} size="large" />
        </View>
      ) : visibleGroups.length === 0 ? (
        <View style={styles.centered}>
          <Text style={[styles.emptyText, { color: colors.textMuted }]}>
            {t('sports.noGames')}
          </Text>
        </View>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={colors.orange}
              colors={[colors.orange]}
            />
          }
        >
          {/* Load earlier button / loading banner */}
          {loadingEarlier ? (
            <View style={[styles.loadEarlierBtn, { borderColor: colors.border }]}>
              <ActivityIndicator size="small" color={colors.textMuted} />
              <Text style={[styles.loadEarlierText, { color: colors.textMuted, marginLeft: 8 }]}>
                {t('sports.loadingMore')}
              </Text>
            </View>
          ) : daysBack < MAX_DAYS_BACK ? (
            <TouchableOpacity
              style={[styles.loadEarlierBtn, { borderColor: colors.border }]}
              onPress={loadEarlier}
            >
              <Text style={[styles.loadEarlierText, { color: colors.accentBlue }]}>
                {t('sports.loadMore')}
              </Text>
            </TouchableOpacity>
          ) : null}

          {visibleGroups.map(dayGroup => (
            <DateSection
              key={dayGroup.yyyymmdd}
              group={dayGroup}
              sportFilter={sportFilter}
              starredLeagues={followStore}
              onToggleStar={toggleLeagueStar}
              onSelectMatch={(m: SoccerMatch) => {
                if (soccerMatchRoute(m) === 'alarm') {
                  setSelectedSoccerAlarm(m);
                } else {
                  setSelectedMatch(m);
                }
              }}
              onSelectBasketball={(game, leagueId) => {
                if (game.status === 'pre') {
                  setSelectedBballAlarm({ game, leagueId, yyyymmdd: dayGroup.yyyymmdd });
                } else {
                  setSelectedBball({ game, leagueId, yyyymmdd: dayGroup.yyyymmdd });
                }
              }}
              colors={colors}
              t={t}
            />
          ))}
          <View style={{ height: 32 }} />
        </ScrollView>
      )}

      {selectedMatch && (
        <MatchSummaryView
          match={selectedMatch}
          visible={true}
          onClose={() => setSelectedMatch(null)}
          onSetAlarm={() => setSelectedSoccerAlarm(selectedMatch)}
        />
      )}

      {/* Alarm sheet stacks on top of summary — closing it returns to summary */}
      {selectedSoccerAlarm && (
        <SoccerAlarmSheet
          match={selectedSoccerAlarm}
          visible={true}
          onClose={() => setSelectedSoccerAlarm(null)}
        />
      )}

      {selectedBball && (
        <BasketballSummaryView
          game={selectedBball.game}
          leagueId={selectedBball.leagueId}
          yyyymmdd={selectedBball.yyyymmdd}
          visible={true}
          onClose={() => setSelectedBball(null)}
          onSetAlarm={() => setSelectedBballAlarm(selectedBball)}
        />
      )}

      {/* Alarm sheet stacks on top of summary — closing it returns to summary */}
      {selectedBballAlarm && (
        <BasketballAlarmSheet
          game={selectedBballAlarm.game}
          leagueId={selectedBballAlarm.leagueId}
          yyyymmdd={selectedBballAlarm.yyyymmdd}
          visible={true}
          onClose={() => setSelectedBballAlarm(null)}
        />
      )}
    </SafeAreaView>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function DateSection({
  group,
  sportFilter,
  starredLeagues: followStoreParam,
  onToggleStar,
  onSelectMatch,
  onSelectBasketball,
  colors,
  t,
}: {
  group: DateGroup;
  sportFilter: SportFilter;
  starredLeagues: FollowStore;
  onToggleStar: (id: string) => void;
  onSelectMatch: (m: SoccerMatch) => void;
  onSelectBasketball: (game: BasketballGame, leagueId: string) => void;
  colors: ReturnType<typeof useTheme>['colors'];
  t: (k: string) => string;
}) {
  const filtered = group.leagueGroups.filter(lg => {
    if (sportFilter === 'all') return true;
    if (sportFilter === 'football') return lg.sport === 'football';
    return lg.sport === 'basketball';
  });
  if (filtered.length === 0) return null;

  return (
    <View>
      <View style={[styles.dateHeader, { borderBottomColor: colors.border }]}>
        <Text style={[styles.dateTitle, { color: colors.textPrimary }]}>{group.title}</Text>
      </View>

      {filtered.map(lg => (
        <LeagueSection
          key={lg.leagueId}
          group={lg}
          isStarred={isLeagueFollowed(followStoreParam, lg.leagueId)}
          onToggleStar={lg.sport === 'football' ? () => onToggleStar(lg.leagueId) : undefined}
          onSelectMatch={onSelectMatch}
          onSelectBasketball={onSelectBasketball}
          colors={colors}
        />
      ))}
    </View>
  );
}

function LeagueSection({
  group,
  isStarred,
  onToggleStar,
  onSelectMatch,
  onSelectBasketball,
  colors,
}: {
  group: LeagueGroup;
  isStarred: boolean;
  onToggleStar?: () => void;
  onSelectMatch: (m: SoccerMatch) => void;
  onSelectBasketball: (game: BasketballGame, leagueId: string) => void;
  colors: ReturnType<typeof useTheme>['colors'];
}) {
  return (
    <View style={[styles.leagueSection, { backgroundColor: colors.card, borderColor: colors.border }]}>
      {/* League header */}
      <View style={[styles.leagueHeader, { borderBottomColor: colors.borderMuted }]}>
        <Text style={[styles.leagueLabel, { color: colors.textMuted }]} numberOfLines={1}>
          {group.sport === 'football' ? '⚽ ' : '🏀 '}{group.leagueLabel}
        </Text>
        {onToggleStar && (
          <TouchableOpacity onPress={onToggleStar} hitSlop={8}>
            <Ionicons
              name={isStarred ? 'star' : 'star-outline'}
              size={16}
              color={isStarred ? colors.gold : colors.textMuted}
            />
          </TouchableOpacity>
        )}
      </View>

      {/* Soccer rows */}
      {group.soccer.map(match => (
        <SoccerRow key={match.id} match={match} colors={colors} onTap={() => onSelectMatch(match)} />
      ))}

      {/* Basketball rows */}
      {group.basketball.map(game => (
        <BasketballRow
          key={game.id}
          game={game}
          onTap={() => onSelectBasketball(game, group.leagueId)}
        />
      ))}
    </View>
  );
}

function SoccerRow({
  match,
  colors,
  onTap,
}: {
  match: SoccerMatch;
  colors: ReturnType<typeof useTheme>['colors'];
  onTap: () => void;
}) {
  const live = isLiveLike(match.status);
  const statusLabel = displayMinute(match) || (match.kickoff
    ? new Date(match.kickoff).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : '–');

  return (
    <CompactMatchRow
      statusLabel={statusLabel}
      isLive={live}
      homeName={match.home.name}
      homeCrest={match.home.crest}
      homeScore={match.home.score}
      homePensScore={match.home.score_pens}
      awayName={match.away.name}
      awayCrest={match.away.crest}
      awayScore={match.away.score}
      awayPensScore={match.away.score_pens}
      hideBell
      hasAlarm={false}
      onBellTap={() => {}}
      onRowTap={onTap}
    />
  );
}

function BasketballRow({
  game,
  onTap,
}: {
  game: BasketballGame;
  onTap?: () => void;
}) {
  const isLive = game.status === 'in';
  let statusLabel = '';
  if (isLive && game.period && game.displayClock) {
    statusLabel = `Q${game.period} ${game.displayClock}`;
  } else if (isLive) {
    statusLabel = 'LIVE';
  } else if (game.status === 'post') {
    statusLabel = 'Final';
  } else if (game.kickoff) {
    statusLabel = new Date(game.kickoff).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  return (
    <CompactMatchRow
      statusLabel={statusLabel}
      isLive={isLive}
      homeName={game.homeTeam}
      homeCrest={game.homeLogo}
      homeScore={game.status !== 'pre' ? game.homeScore : null}
      awayName={game.awayTeam}
      awayCrest={game.awayLogo}
      awayScore={game.status !== 'pre' ? game.awayScore : null}
      hideBell
      hasAlarm={false}
      onBellTap={() => {}}
      onRowTap={onTap ?? (() => {})}
    />
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1 },

  header: {
    paddingBottom: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  searchRow: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 4, paddingTop: 4, paddingBottom: 6, gap: 8 },
  searchPill: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 7, paddingHorizontal: 12, paddingVertical: 9, borderRadius: 10, borderWidth: StyleSheet.hairlineWidth },
  searchPlaceholder: { fontSize: 14, flex: 1 },
  headerTitle: { ...tabHeaderTitle, paddingHorizontal: 16, paddingBottom: 4 },

  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyText: {
    fontSize: 14,
  },

  loadEarlierBtn: {
    margin: 12,
    padding: 10,
    borderRadius: 10,
    borderWidth: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  loadEarlierText: {
    fontSize: 13,
    fontWeight: '600',
  },

  dateHeader: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  dateTitle: {
    fontSize: 13,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },

  leagueSection: {
    marginHorizontal: 12,
    marginVertical: 6,
    borderRadius: 12,
    borderWidth: 1,
    overflow: 'hidden',
  },
  leagueHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  leagueLabel: {
    fontSize: 12,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    flex: 1,
  },
});
