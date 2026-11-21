import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  SectionList,
  Modal,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import { useFocusEffect } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { useTheme } from '../../context/ThemeContext';
import { useLanguage } from '../../context/LanguageContext';
import { HamburgerButton } from '../../components/drawer-menu';
import { SportFilterPills, SportFilter } from '../../components/sport-filter-pills';
import { RefreshBar } from '../../components/refresh-bar';
import { CompactMatchRow } from '../../components/compact-match-row';
import { tabHeaderTitle } from '../../constants/tokens';
import { LeagueLogo } from '../../components/league-logo';
import { LeagueCatalog } from '../../services/league-catalog';
import { WC2026_SEARCH_TEAMS, WC2026_TEAMS } from '../../data/wc2026-teams';
import { getMatchesCached, invalidateMatchCache, searchAll, getTeamMatches, getBasketballTeamGames, getStandings, getBasketballStandings } from '../../services/soccer-service';
import type { SearchTeam, SearchLeague } from '../../services/soccer-service';
import type { SoccerMatch } from '../../types/soccer';
import { isLiveLike, displayMinute, soccerMatchRoute } from '../../types/soccer';
import { SoccerAlarmSheet } from '../../views/soccer-alarm-sheet';
import { BasketballAlarmSheet } from '../../views/basketball-alarm-sheet';
import { BasketballSummaryView, prefetchBasketballSummary } from '../../views/basketball-summary-view';
import { MatchSummaryView } from '../../views/match-summary-view';
import {
  getFollowStore,
  followTeam,
  unfollowTeam,
  followLeague,
  unfollowLeague,
  isTeamFollowed,
  isLeagueFollowed,
  hasAnyFollow,
} from '../../services/follow-service';
import type { FollowStore } from '../../services/follow-service';
import { SERVER_URL } from '../../config';

// ── Types ─────────────────────────────────────────────────────────────────────

interface FeedBballGame {
  id: string;
  homeTeam: string;
  homeId?: string;
  homeLogo?: string;
  homeScore?: number;
  awayTeam: string;
  awayId?: string;
  awayLogo?: string;
  awayScore?: number;
  status: 'pre' | 'in' | 'post';
  displayClock?: string;
  period?: number;
  kickoff?: string;
  watchScore?: number;
}

interface LeagueGroup {
  leagueId: string;
  sport: 'basketball' | 'football';
  leagueLabel: string;
  soccer: SoccerMatch[];
  basketball: FeedBballGame[];
}

interface DateGroup {
  yyyymmdd: string;
  title: string;
  leagueGroups: LeagueGroup[];
}

// ── Basketball fetcher ────────────────────────────────────────────────────────

function localDateStr(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}${m}${day}`;
}

const _BB_FEED_STORE = (l: string, d: string) => `bb_feed_v1_${l}_${d}`;

async function fetchBballGames(leagueId: string, date: string): Promise<FeedBballGame[]> {
  const today = localDateStr(new Date());
  const isPast = date < today;

  if (isPast) {
    try {
      const raw = await AsyncStorage.getItem(_BB_FEED_STORE(leagueId, date));
      if (raw) {
        const cached: FeedBballGame[] = JSON.parse(raw);
        if (cached.every(g => g.status === 'post')) {
          // Merge watch scores even for cached data (watchScore may be missing in old cache)
          if (cached.some(g => g.watchScore == null)) {
            try {
              const ws = await fetch(`${SERVER_URL}/watch-scores?date=${date}&league=${encodeURIComponent(leagueId)}`);
              if (ws.ok) {
                const wsData = await ws.json();
                for (const [k, v] of Object.entries(wsData.scores ?? {})) {
                  const g = cached.find(x => `${x.homeTeam}_${x.awayTeam}` === k);
                  if (g) g.watchScore = (v as any).score;
                }
              }
            } catch {}
          }
          return cached;
        }
      }
    } catch {}
  }

  try {
    const resp = await fetch(`${SERVER_URL}/games?date=${date}&league=${leagueId}`);
    if (!resp.ok) return [];
    const data = await resp.json();
    const games: FeedBballGame[] = (data.games ?? []).map((ev: any) => {
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
        homeId:   String(home.team?.id ?? ''),
        homeLogo: home.team?.logo || undefined,
        homeScore: (isLive || isFinal) && home.score != null ? parseInt(home.score, 10) : undefined,
        awayTeam: away.team?.abbreviation ?? away.team?.displayName ?? '?',
        awayId:   String(away.team?.id ?? ''),
        awayLogo: away.team?.logo || undefined,
        awayScore: (isLive || isFinal) && away.score != null ? parseInt(away.score, 10) : undefined,
        status: isLive ? 'in' : isFinal ? 'post' : 'pre',
        displayClock: ev.status?.displayClock,
        period: ev.status?.period,
        kickoff: ev.date,
      } satisfies FeedBballGame;
    });
    // Deduplicate: same game ID, then same team pair (playoffs)
    const byId = games.filter((g, i, a) => a.findIndex(x => x.id === g.id) === i);
    const seen = new Map<string, FeedBballGame>();
    for (const g of byId) {
      const key = [g.homeTeam, g.awayTeam].sort().join('|');
      const prev = seen.get(key);
      if (!prev) { seen.set(key, g); continue; }
      const gScore = g.status === 'in' ? 2 : g.status === 'post' ? 1 : 0;
      const pScore = prev.status === 'in' ? 2 : prev.status === 'post' ? 1 : 0;
      if (gScore > pScore || (gScore === pScore && (g.kickoff ?? '') < (prev.kickoff ?? ''))) seen.set(key, g);
    }
    const deduped = Array.from(seen.values());

    // Merge watch scores for finished games
    if (deduped.some(g => g.status === 'post')) {
      try {
        const ws = await fetch(`${SERVER_URL}/watch-scores?date=${date}&league=${encodeURIComponent(leagueId)}`);
        if (ws.ok) {
          const wsData = await ws.json();
          const scores: Record<string, number> = {};
          for (const [k, v] of Object.entries(wsData.scores ?? {})) scores[k] = (v as any).score;
          for (const g of deduped) {
            const key = `${g.homeTeam}_${g.awayTeam}`;
            if (scores[key] != null) g.watchScore = scores[key];
          }
        }
      } catch {}
    }

    // Only cache past dates once all games are final — avoids stale 'pre' cache
    if (isPast && deduped.length > 0 && deduped.every(g => g.status === 'post')) {
      AsyncStorage.setItem(_BB_FEED_STORE(leagueId, date), JSON.stringify(deduped)).catch(() => {});
    }
    return deduped;
  } catch {
    return [];
  }
}

// ── Cache ─────────────────────────────────────────────────────────────────────

const FEED_CACHE_KEY   = 'feed_cache_v3';
const SPORTS_CACHE_KEY = 'sports_cache_v2';

interface FeedCache { dateGroups: DateGroup[]; followKey: string; savedAt: number; }

function followKey(store: FollowStore): string {
  return JSON.stringify({
    t: Object.keys(store.teams).sort(),
    l: Object.keys(store.leagues).sort(),
  });
}

function buildTeamIdSet(store: FollowStore): Set<string> {
  const ids = new Set<string>();
  Object.values(store.teams).forEach(team => {
    ids.add(team.id);
    team.altIds?.forEach(id => ids.add(id));
  });
  return ids;
}

function deriveFromSportsCache(
  rawGroups: any[],
  store: FollowStore,
  tFn: (k: string) => string
): DateGroup[] {
  const followedTeamIds   = buildTeamIdSet(store);
  const followedLeagueIds = new Set(Object.keys(store.leagues));
  const cutoff = (() => {
    const d = new Date(); d.setDate(d.getDate() - 3);
    return localDateStr(d);
  })();

  const result: DateGroup[] = [];
  for (const day of rawGroups) {
    if (day.yyyymmdd < cutoff) continue;
    const leagueGroups: LeagueGroup[] = [];

    for (const lg of (day.leagueGroups ?? [])) {
      if (lg.sport === 'football') {
        const filtered = (lg.soccer ?? []).filter((m: SoccerMatch) =>
          followedLeagueIds.has(m.competition) ||
          followedTeamIds.has(m.home?.id) ||
          followedTeamIds.has(m.away?.id)
        );
        if (filtered.length > 0) {
          leagueGroups.push({ leagueId: lg.leagueId, sport: 'football', leagueLabel: lg.leagueLabel, soccer: filtered, basketball: [] });
        }
      } else if (lg.sport === 'basketball') {
        const leagueFollowed = followedLeagueIds.has(lg.leagueId);
        const filtered = (lg.basketball ?? []).filter((g: any) =>
          leagueFollowed ||
          followedTeamIds.has(g.homeId ?? '') ||
          followedTeamIds.has(g.awayId ?? '')
        );
        if (filtered.length > 0) {
          leagueGroups.push({ leagueId: lg.leagueId, sport: 'basketball', leagueLabel: lg.leagueLabel, soccer: [], basketball: filtered });
        }
      }
    }

    if (leagueGroups.length > 0) {
      result.push({
        yyyymmdd: day.yyyymmdd,
        title: dateLabel(day.yyyymmdd, tFn),
        leagueGroups: leagueGroups.sort((a, b) => a.leagueLabel.localeCompare(b.leagueLabel)),
      });
    }
  }
  return result.sort((a, b) => a.yyyymmdd.localeCompare(b.yyyymmdd));
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
  const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
  const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);

  if (yyyymmdd === today) return t('home.today');
  if (date.toDateString() === tomorrow.toDateString()) return t('home.tomorrow');
  if (date.toDateString() === yesterday.toDateString()) return t('home.yesterday');
  return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

function leagueDisplayName(leagueId: string): string {
  const found = LeagueCatalog.all.find(l => l.id === leagueId);
  if (found) return found.label;
  return leagueId.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
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

// ── Main component ────────────────────────────────────────────────────────────

export default function FeedScreen() {
  const { colors } = useTheme();
  const { t } = useLanguage();

  const [sportFilter, setSportFilter]     = useState<SportFilter>('all');
  const [dateGroups, setDateGroups]       = useState<DateGroup[]>([]);
  const [loading, setLoading]             = useState(true);
  const [refreshing, setRefreshing]       = useState(false);
  const [bgRefreshing, setBgRefreshing]   = useState(false);
  const [followStore, setFollowStore]         = useState<FollowStore | null>(null);
  const [searchVisible, setSearchVisible]     = useState(false);
  const [selectedSoccer, setSelectedSoccer]         = useState<SoccerMatch | null>(null);
  const [selectedMatchSummary, setSelectedMatchSummary] = useState<SoccerMatch | null>(null);
  const [selectedBball, setSelectedBball]           = useState<{ game: FeedBballGame; leagueId: string; yyyymmdd: string } | null>(null);
  const [selectedBballAlarm, setSelectedBballAlarm] = useState<{ game: FeedBballGame; leagueId: string; yyyymmdd: string } | null>(null);

  const runIdRef    = useRef(0);
  const accRef      = useRef<Map<string, DateGroup>>(new Map());
  const flushRef    = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastLoadRef = useRef(0); // timestamp of last loadMatches call (guards double-load)

  const dateGroupsRef  = useRef<DateGroup[]>([]);
  useEffect(() => { dateGroupsRef.current = dateGroups; }, [dateGroups]);
  const followStoreRef = useRef<FollowStore | null>(null);
  useEffect(() => { followStoreRef.current = followStore; }, [followStore]);
  const liveIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const commitGroups = useCallback((runId: number, store: FollowStore, persist = false) => {
    if (runId !== runIdRef.current) return;
    if (accRef.current.size === 0) { setLoading(false); setBgRefreshing(false); return; }
    const today = todayStr();
    const now = Date.now();
    const groups = Array.from(accRef.current.values())
      .map(g => {
        const lg = [...g.leagueGroups].sort((a, b) => a.leagueLabel.localeCompare(b.leagueLabel));
        if (g.yyyymmdd >= today) return { ...g, leagueGroups: lg };
        // Past dates: strip any match that is still pre-game (stale scheduled data is wrong info)
        const cleanedLg = lg.map(league => ({
          ...league,
          soccer: league.soccer.filter(m => m.status !== 'scheduled' || (m.kickoff ? new Date(m.kickoff).getTime() > now : false)),
          basketball: league.basketball.filter(bball => bball.status !== 'pre' || (bball.kickoff ? new Date(bball.kickoff).getTime() > now : false)),
        })).filter(league => league.soccer.length > 0 || league.basketball.length > 0);
        return { ...g, leagueGroups: cleanedLg };
      })
      .filter(g => g.leagueGroups.length > 0)
      .sort((a, b) => a.yyyymmdd.localeCompare(b.yyyymmdd));
    setDateGroups(groups);
    setLoading(false);
    setBgRefreshing(false);
    if (persist) {
      AsyncStorage.setItem(
        FEED_CACHE_KEY,
        JSON.stringify({ dateGroups: groups, followKey: followKey(store), savedAt: Date.now() } satisfies FeedCache)
      ).catch(() => {});
    }
    // Pre-warm summary cache for recently-finished basketball games (last 48h)
    // so the user gets instant stats on tap instead of waiting for the server.
    if (persist) {
      const cutoffMs = Date.now() - 48 * 60 * 60 * 1000;
      groups
        .flatMap(dg => dg.leagueGroups.flatMap(lg => lg.basketball))
        .filter(g => g.status === 'post' && g.kickoff && new Date(g.kickoff).getTime() > cutoffMs)
        .slice(0, 5)
        .forEach(g => prefetchBasketballSummary(g.id).catch(() => {}));
    }
  }, []);

  const upsertGroup = useCallback((yyyymmdd: string, group: LeagueGroup, runId: number, tFn: (k: string) => string) => {
    if (runId !== runIdRef.current) return;
    if (!accRef.current.has(yyyymmdd)) {
      accRef.current.set(yyyymmdd, { yyyymmdd, title: dateLabel(yyyymmdd, tFn), leagueGroups: [] });
    }
    const day = accRef.current.get(yyyymmdd)!;
    const idx = day.leagueGroups.findIndex(lg => lg.leagueId === group.leagueId);
    if (idx >= 0) day.leagueGroups[idx] = group;
    else day.leagueGroups.push(group);
  }, []);

  const loadMatches = useCallback(async (store: FollowStore, { silent = false, seed }: { silent?: boolean; seed?: DateGroup[] } = {}) => {
    const runId = ++runIdRef.current;
    lastLoadRef.current = Date.now();

    const followedTeamIds   = buildTeamIdSet(store);
    const followedLeagueIds = new Set(Object.keys(store.leagues));

    if (silent) {
      setBgRefreshing(true);
      // Seed with current data — failed/empty fetches won't erase existing matches.
      // Filter the seed by current follows so stale league data (e.g. WC after unfollowing)
      // doesn't linger in the accumulator while the fresh fetch runs.
      const seedData = seed ?? dateGroupsRef.current;
      const filteredSeed = seedData.map(dg => ({
        ...dg,
        leagueGroups: dg.leagueGroups.map(lg => ({
          ...lg,
          soccer: (lg.soccer ?? []).filter((m: SoccerMatch) =>
            followedLeagueIds.has(m.competition) ||
            followedTeamIds.has(m.home?.id) ||
            followedTeamIds.has(m.away?.id)
          ),
          basketball: (lg.basketball ?? []).filter((g: any) =>
            followedLeagueIds.has(lg.leagueId) ||
            followedTeamIds.has(g.homeId ?? '') ||
            followedTeamIds.has(g.awayId ?? '')
          ),
        })).filter(lg => lg.soccer.length > 0 || lg.basketball.length > 0),
      })).filter(dg => dg.leagueGroups.length > 0);
      accRef.current = new Map(
        filteredSeed.map(dg => [dg.yyyymmdd, { ...dg, leagueGroups: [...dg.leagueGroups] }])
      );
    } else {
      accRef.current = new Map();
      setLoading(true);
      setDateGroups([]);
    }

    // ── Football leagues to fetch ──────────────────────────────────────────
    const allFootballIds = new Set(LeagueCatalog.footballLeagues().map(l => l.id));
    const footballToFetch = new Set<string>(
      [...followedLeagueIds].filter(id => allFootballIds.has(id))
    );
    let needAllFootball = false;
    Object.values(store.teams).forEach(team => {
      if (team.sport !== 'football') return;
      if (team.leagues?.length) team.leagues.forEach(lg => { if (allFootballIds.has(lg)) footballToFetch.add(lg); });
      else needAllFootball = true;
    });
    if (needAllFootball) allFootballIds.forEach(id => footballToFetch.add(id));
    const footballLeagues = LeagueCatalog.footballLeagues().filter(l => footballToFetch.has(l.id));

    // ── Basketball leagues to fetch ────────────────────────────────────────
    const allBballIds = new Set(LeagueCatalog.basketballLeagues().map(l => l.id));
    const bballToFetch = new Set<string>(
      [...followedLeagueIds].filter(id => allBballIds.has(id))
    );
    let needAllBball = false;
    Object.values(store.teams).forEach(team => {
      if (team.sport !== 'basketball') return;
      if (team.leagues?.length) team.leagues.forEach(lg => { if (allBballIds.has(lg)) bballToFetch.add(lg); });
      else needAllBball = true;
    });
    if (needAllBball) allBballIds.forEach(id => bballToFetch.add(id));
    const bballLeagues = LeagueCatalog.basketballLeagues().filter(l => bballToFetch.has(l.id));

    // Silent refreshes only cover the "active" window (yesterday → next 7 days)
    // Full loads cover a wider range for schedule browsing
    const dates = silent ? dateRange(1, 25) : dateRange(3, 30);

    // Always run a flush interval — in silent mode the seed data is already visible
    // so intermediate flushes just update individual rows as requests complete.
    if (flushRef.current) clearInterval(flushRef.current);
    flushRef.current = setInterval(() => commitGroups(runId, store, false), 200);

    await Promise.allSettled([
      // Football
      ...dates.flatMap(yyyymmdd =>
        footballLeagues.map(async league => {
          try {
            const matches = await getMatchesCached(league.id, yyyymmdd);
            const filtered = matches
              .filter((m, i, a) => a.findIndex(x => x.id === m.id) === i) // dedup by id
              .filter(m =>
                followedLeagueIds.has(m.competition) ||
                followedTeamIds.has(m.home.id) ||
                followedTeamIds.has(m.away.id)
              );
            if (!filtered.length) return;
            upsertGroup(yyyymmdd, { leagueId: league.id, sport: 'football', leagueLabel: league.label, soccer: filtered, basketball: [] }, runId, t);
          } catch {}
        })
      ),
      // Basketball
      ...dates.flatMap(yyyymmdd =>
        bballLeagues.map(async league => {
          try {
            const games = await fetchBballGames(league.id, yyyymmdd);
            const leagueFollowed = followedLeagueIds.has(league.id);
            const filtered = leagueFollowed
              ? games
              : games.filter(g =>
                  followedTeamIds.has(g.homeId ?? '') ||
                  followedTeamIds.has(g.awayId ?? '')
                );
            if (!filtered.length) return;
            upsertGroup(yyyymmdd, { leagueId: league.id, sport: 'basketball', leagueLabel: league.label, soccer: [], basketball: filtered }, runId, t);
          } catch {}
        })
      ),
    ]);

    if (flushRef.current) { clearInterval(flushRef.current); flushRef.current = null; }
    commitGroups(runId, store, true);
  }, [t, commitGroups, upsertGroup]);

  // Mount: show data instantly from cache, then refresh silently
  useEffect(() => {
    (async () => {
      const store = await getFollowStore().catch(() => ({ teams: {}, leagues: {} } as FollowStore));
      setFollowStore(store);

      if (!hasAnyFollow(store)) { setLoading(false); return; }

      const cutoff = localDateStr((() => { const d = new Date(); d.setDate(d.getDate() - 3); return d; })());

      // 1. Feed-specific cache
      try {
        const raw = await AsyncStorage.getItem(FEED_CACHE_KEY);
        if (raw) {
          const cached: FeedCache = JSON.parse(raw);
          if (cached.followKey === followKey(store) && cached.dateGroups.length > 0) {
            const today = todayStr();
            const now = Date.now();
            const restored = cached.dateGroups
              .filter(g => g.yyyymmdd >= cutoff)
              .map(g => {
                const base = { ...g, title: dateLabel(g.yyyymmdd, t) };
                if (g.yyyymmdd >= today) return base;
                // Strip stale pre-game matches from past dates
                const cleanedLg = (g.leagueGroups ?? []).map((lg: LeagueGroup) => ({
                  ...lg,
                  soccer: (lg.soccer ?? []).filter((m: SoccerMatch) => m.status !== 'scheduled' || (m.kickoff ? new Date(m.kickoff).getTime() > now : false)),
                  basketball: (lg.basketball ?? []).filter((bball: FeedBballGame) => bball.status !== 'pre' || (bball.kickoff ? new Date(bball.kickoff).getTime() > now : false)),
                })).filter((lg: LeagueGroup) => lg.soccer.length > 0 || lg.basketball.length > 0);
                return { ...base, leagueGroups: cleanedLg };
              })
              .filter(g => g.leagueGroups.length > 0);
            if (restored.length > 0) {
              setDateGroups(restored);
              setLoading(false);
              loadMatches(store, { silent: true, seed: restored });
              return;
            }
          }
        }
      } catch {}

      // 2. Derive from sports screen cache (free, instant)
      try {
        const sportsRaw = await AsyncStorage.getItem(SPORTS_CACHE_KEY);
        if (sportsRaw) {
          const sportsCache = JSON.parse(sportsRaw);
          const derived = deriveFromSportsCache(sportsCache.dateGroups ?? [], store, t);
          if (derived.length > 0) {
            setDateGroups(derived);
            setLoading(false);
            loadMatches(store, { silent: true, seed: derived });
            return;
          }
        }
      } catch {}

      // 3. Fresh network fetch with progressive rendering
      loadMatches(store, { silent: false });
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useFocusEffect(
    useCallback(() => {
      // Skip if a load just ran (prevents double-load on mount with useEffect)
      if (Date.now() - lastLoadRef.current < 30_000) return;
      getFollowStore().then(store => {
        setFollowStore(store);
        if (store && hasAnyFollow(store)) {
          loadMatches(store, { silent: true });
        }
      }).catch(() => {});
    }, [loadMatches])
  );

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
              // Refresh if kickoff was within the last 3 hours (match may be live but status stale)
              return now > t - 5 * 60 * 1000 && now < t + 3 * 60 * 60 * 1000;
            }
            return false;
          })
        )
      );
      if (shouldRefresh && followStoreRef.current) {
        invalidateMatchCache();
        loadMatches(followStoreRef.current, { silent: true });
      }
    }, 60_000);
    return () => {
      if (liveIntervalRef.current) { clearInterval(liveIntervalRef.current); liveIntervalRef.current = null; }
    };
  }, [loadMatches]));

  const onRefresh = async () => {
    setRefreshing(true);
    const store = await getFollowStore().catch(() => followStore!);
    setFollowStore(store);
    // Fire-and-forget: data merges in silently via the flush interval.
    // Spinner stops immediately — no waiting for all ~80 requests to finish.
    if (store && hasAnyFollow(store)) {
      loadMatches(store, { silent: true, seed: dateGroupsRef.current });
    }
    setRefreshing(false);
  };

  const handleFollowStoreChanged = useCallback(() => {
    getFollowStore().then(store => {
      setFollowStore(store);
      // Keep existing matches visible; new league/team matches merge in silently.
      // If the user removed a follow, their matches will filter out as each league re-fetches.
      loadMatches(store, { silent: true, seed: dateGroupsRef.current });
    }).catch(() => {});
  }, [loadMatches]);

  const hasAnyFavorite = followStore ? hasAnyFollow(followStore) : false;

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: colors.bg }]}>
      <View style={[styles.header, { borderBottomColor: colors.border }]}>
        <View style={styles.searchRow}>
          <HamburgerButton />
          <TouchableOpacity
            style={[styles.searchPill, { backgroundColor: colors.card, borderColor: colors.border }]}
            onPress={() => setSearchVisible(true)}
            activeOpacity={0.75}
          >
            <Ionicons name="search-outline" size={15} color={colors.textMuted} />
            <Text style={[styles.searchPlaceholder, { color: colors.textMuted }]}>
              {t('sports.searchHint')}
            </Text>
          </TouchableOpacity>
        </View>
        <Text style={[styles.headerTitle, { color: colors.textPrimary }]}>
          {t('favorites.feed.title')}
        </Text>
      </View>

      <SportFilterPills value={sportFilter} onChange={setSportFilter} />
      <RefreshBar visible={bgRefreshing} />

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator color={colors.orange} size="large" />
        </View>
      ) : !hasAnyFavorite ? (
        <EmptyFavoritesState colors={colors} t={t} onSearch={() => setSearchVisible(true)} />
      ) : dateGroups.length === 0 ? (
        <View style={styles.centered}>
          <Text style={[styles.emptyText, { color: colors.textMuted }]}>{t('favorites.feed.noGames')}</Text>
        </View>
      ) : (
        <ScrollView
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          showsVerticalScrollIndicator={false}
        >
          {dateGroups.map(dayGroup => (
            <DateSection
              key={dayGroup.yyyymmdd}
              group={dayGroup}
              sportFilter={sportFilter}
              colors={colors}
              t={t}
              onSelectSoccer={(m) => {
                if (soccerMatchRoute(m) === 'alarm') {
                  setSelectedSoccer(m);
                } else {
                  setSelectedMatchSummary(m);
                }
              }}
              onSelectBball={(game, leagueId, yyyymmdd) => {
                if (game.status === 'pre') {
                  setSelectedBballAlarm({ game, leagueId, yyyymmdd });
                } else {
                  setSelectedBball({ game, leagueId, yyyymmdd });
                }
              }}
            />
          ))}
          <View style={{ height: 32 }} />
        </ScrollView>
      )}

      {followStore && (
        <SearchFollowsModal
          visible={searchVisible}
          onClose={() => setSearchVisible(false)}
          followStore={followStore}
          onChanged={handleFollowStoreChanged}
          colors={colors}
        />
      )}

      {selectedMatchSummary && (
        <MatchSummaryView
          match={selectedMatchSummary}
          visible={true}
          onClose={() => setSelectedMatchSummary(null)}
          onSetAlarm={() => setSelectedSoccer(selectedMatchSummary)}
        />
      )}

      {selectedSoccer && (
        <SoccerAlarmSheet
          match={selectedSoccer}
          visible={true}
          onClose={() => setSelectedSoccer(null)}
        />
      )}

      {selectedBball && (
        <BasketballSummaryView
          game={selectedBball.game as any}
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
          game={selectedBballAlarm.game as any}
          leagueId={selectedBballAlarm.leagueId}
          yyyymmdd={selectedBballAlarm.yyyymmdd}
          visible={true}
          onClose={() => setSelectedBballAlarm(null)}
        />
      )}
    </SafeAreaView>
  );
}

// ── Search modal ──────────────────────────────────────────────────────────────

function _formatKickoff(kickoff: string | null | undefined): string {
  if (!kickoff) return '—';
  const d = new Date(kickoff);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function _compLabel(comp: string | null | undefined): string {
  if (!comp || comp === 'other') return 'Friendly';
  return LeagueCatalog.info(comp).label;
}

function SearchFollowsModal({
  visible, onClose, followStore, onChanged, colors,
}: {
  visible: boolean;
  onClose: () => void;
  followStore: FollowStore;
  onChanged: () => void;
  colors: ReturnType<typeof useTheme>['colors'];
}) {
  const insets = useSafeAreaInsets();
  const [query, setQuery]     = useState('');
  const [results, setResults] = useState<{ teams: SearchTeam[]; leagues: SearchLeague[] }>({ teams: [], leagues: [] });
  const [searching, setSearching] = useState(false);
  const [localStore, setLocalStore] = useState<FollowStore>(followStore);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef    = useRef<TextInput>(null);

  // Team preview panel
  const [previewTeam, setPreviewTeam]           = useState<SearchTeam | null>(null);
  const [teamMatches, setTeamMatches]           = useState<SoccerMatch[]>([]);
  const [bbTeamGames, setBbTeamGames]           = useState<any[]>([]);
  const [teamMatchesLoading, setTeamMatchesLoading] = useState(false);
  const [addedConfirm, setAddedConfirm]         = useState(false);
  // Cache: keeps last successful fetch so re-opening the same team is instant
  const teamMatchesCacheRef = useRef<Map<string, { soccer: SoccerMatch[]; bb: any[] }>>(new Map());

  // Browse mode state (shown when query is empty)
  const [browseSport, setBrowseSport]     = useState<'football' | 'basketball'>('football');
  const [browseLeague, setBrowseLeague]   = useState<string>('premier_league');
  const [browseTeams, setBrowseTeams]     = useState<SearchTeam[]>([]);
  const [browseLoading, setBrowseLoading] = useState(false);
  const browseTeamsCacheRef = useRef<Map<string, SearchTeam[]>>(new Map());

  useEffect(() => { setLocalStore(followStore); }, [followStore]);

  // Pre-populate browseTeamsCacheRef from persisted caches — instant load on re-open.
  useEffect(() => {
    // WC 2026: seed all 48 teams from the static bundle — zero API latency.
    browseTeamsCacheRef.current.set('world_cup', WC2026_SEARCH_TEAMS);

    // Other football leagues: use standings-based cache persisted to AsyncStorage.
    // sports_cache_v2 is intentionally NOT used here — it only contains teams with
    // matches in the current date window (e.g. only 10 WC teams for day 1-3).
    AsyncStorage.getItem('football_browse_cache_v2').then(raw => {
      if (!raw) return;
      try {
        const saved = JSON.parse(raw) as Record<string, SearchTeam[]>;
        for (const [lid, teams] of Object.entries(saved))
          if (teams.length > 0) browseTeamsCacheRef.current.set(lid, teams);
      } catch {}
    }).catch(() => {});

    // Basketball: all teams from the teams cache, keyed by league
    AsyncStorage.getItem('all_teams_cache_v1').then(raw => {
      if (!raw) return;
      try {
        const teams: Array<{ id: string; name: string; logo: string | null; league?: string }> = JSON.parse(raw);
        const byLeague = new Map<string, SearchTeam[]>();
        for (const t of teams) {
          if (!t.league) continue;
          if (!byLeague.has(t.league)) byLeague.set(t.league, []);
          byLeague.get(t.league)!.push({ id: t.id, name: t.name, logo: t.logo ?? null, sport: 'basketball', league: t.league });
        }
        for (const [lid, lgTeams] of byLeague)
          if (lgTeams.length > 0 && !browseTeamsCacheRef.current.has(lid))
            browseTeamsCacheRef.current.set(lid, lgTeams);
      } catch {}
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!visible) {
      setQuery('');
      setResults({ teams: [], leagues: [] });
      setPreviewTeam(null);
      setAddedConfirm(false);
      setBrowseSport('football');
      setBrowseLeague('premier_league');
    } else {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [visible]);

  // When sport tab switches, jump to the first league of that sport
  useEffect(() => {
    const first = LeagueCatalog.leagues(browseSport)[0];
    if (first) setBrowseLeague(first.id);
  }, [browseSport]);

  // Load teams for the selected browse league.
  // Depends on visible too so it re-runs when the modal opens (cache is ready by then).
  useEffect(() => {
    if (!visible) return;
    const cached = browseTeamsCacheRef.current.get(browseLeague);
    if (cached) { setBrowseTeams(cached); setBrowseLoading(false); return; }
    setBrowseTeams([]);
    setBrowseLoading(true);
    const sport = LeagueCatalog.info(browseLeague).sport;
    (sport === 'football' ? getStandings(browseLeague) : getBasketballStandings(browseLeague))
      .then(standings => {
        const teams: SearchTeam[] = (standings.standings ?? []).map((row: any) => ({
          id: String(row.team_id),
          name: row.team_name,
          logo: row.team_crest ?? null,
          sport,
          league: browseLeague,
        }));
        setBrowseTeams(teams);
        if (teams.length > 0) {
          browseTeamsCacheRef.current.set(browseLeague, teams);
          // Persist football browse teams to AsyncStorage for cold-start cache
          if (sport === 'football') {
            AsyncStorage.getItem('football_browse_cache_v2').then(raw => {
              const existing = raw ? JSON.parse(raw) as Record<string, SearchTeam[]> : {};
              existing[browseLeague] = teams;
              AsyncStorage.setItem('football_browse_cache_v2', JSON.stringify(existing)).catch(() => {});
            }).catch(() => {});
          }
        }
      })
      .catch(() => {})
      .finally(() => setBrowseLoading(false));
  }, [browseLeague, visible]);

  // Fetch matches/games when a team is previewed — serve from cache on re-open
  useEffect(() => {
    if (!previewTeam) return;
    const cached = teamMatchesCacheRef.current.get(previewTeam.id);
    if (cached) {
      // Show cached data immediately; no spinner, no blank slate
      setTeamMatches(cached.soccer);
      setBbTeamGames(cached.bb);
      return;
    }
    // No cache — show spinner only if we don't already have data for this team
    setTeamMatches([]);
    setBbTeamGames([]);
    setTeamMatchesLoading(true);
    const id = previewTeam.id;
    if (previewTeam.sport === 'football') {
      getTeamMatches(id)
        .then(m => {
          if (m.length > 0) {
            setTeamMatches(m);
            teamMatchesCacheRef.current.set(id, { soccer: m, bb: [] });
          }
        })
        .catch(() => {})
        .finally(() => setTeamMatchesLoading(false));
    } else {
      const league = previewTeam.league ?? previewTeam.leagues?.[0] ?? 'nba';
      getBasketballTeamGames(id, league)
        .then(g => {
          if (g.length > 0) {
            setBbTeamGames(g);
            teamMatchesCacheRef.current.set(id, { soccer: [], bb: g });
          }
        })
        .catch(() => {})
        .finally(() => setTeamMatchesLoading(false));
    }
  }, [previewTeam]);

  useEffect(() => {
    const q = query.trim();
    if (!q) { setResults({ teams: [], leagues: [] }); return; }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      try { const r = await searchAll(q); setResults(r); }
      catch { setResults({ teams: [], leagues: [] }); }
      finally { setSearching(false); }
    }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query]);

  const doToggleTeam = async (team: SearchTeam) => {
    const already = isTeamFollowed(localStore, team.id);
    const altIds = (team.ids ?? []).filter(id => id !== team.id);
    const teamLeagues = team.leagues ?? (team.league ? [team.league] : []);
    if (already) {
      const { [team.id]: _r, ...rest } = localStore.teams;
      setLocalStore({ ...localStore, teams: rest });
    } else {
      setLocalStore({ ...localStore, teams: { ...localStore.teams, [team.id]: { id: team.id, name: team.name, sport: team.sport, altIds, leagues: teamLeagues } } });
    }
    try {
      if (already) await unfollowTeam(team.id);
      else await followTeam({ id: team.id, name: team.name, sport: team.sport, altIds, leagues: teamLeagues });
    } finally {
      const updated = await getFollowStore();
      setLocalStore(updated);
      onChanged();
    }
  };

  const toggleLeague = async (league: SearchLeague) => {
    const already = isLeagueFollowed(localStore, league.id);
    if (already) {
      const { [league.id]: _r, ...rest } = localStore.leagues;
      setLocalStore({ ...localStore, leagues: rest });
    } else {
      setLocalStore({ ...localStore, leagues: { ...localStore.leagues, [league.id]: { id: league.id, label: league.name, sport: league.sport } } });
    }
    try {
      if (already) await unfollowLeague(league.id);
      else await followLeague({ id: league.id, label: league.name, sport: league.sport });
    } finally {
      const updated = await getFollowStore();
      setLocalStore(updated);
      onChanged();
    }
  };

  const handleTeamRowPress = (team: SearchTeam) => {
    setPreviewTeam(team);
    setAddedConfirm(false);
  };

  const handleAddRemove = async () => {
    if (!previewTeam) return;
    const already = isTeamFollowed(localStore, previewTeam.id);
    await doToggleTeam(previewTeam);
    if (!already) {
      setAddedConfirm(true);
      setTimeout(() => { setPreviewTeam(null); setAddedConfirm(false); }, 900);
    } else {
      setPreviewTeam(null);
    }
  };

  const hasResults = results.teams.length > 0 || results.leagues.length > 0;
  // Soccer
  const upcomingMatches = teamMatches.filter(m => m.status === 'scheduled' || m.status === 'postponed').slice(0, 6);
  const recentMatches   = teamMatches.filter(m => m.status === 'ft').slice(-4);
  // Basketball — extract from ESPN format
  const bbUpcoming = bbTeamGames.filter(g => g?.status?.type?.state === 'pre').slice(0, 6);
  const bbRecent   = bbTeamGames.filter(g => g?.status?.type?.state === 'post').slice(-4);
  const hasGames   = upcomingMatches.length + recentMatches.length + bbUpcoming.length + bbRecent.length > 0;

  function _bbGameRow(g: any, teamName: string, key: string) {
    const comps = g?.competitions?.[0]?.competitors ?? [];
    const home  = comps.find((c: any) => c.homeAway === 'home');
    const away  = comps.find((c: any) => c.homeAway === 'away');
    const myAbbr  = (teamName || '').toUpperCase().slice(0, 3);
    const isHome  = home?.team?.abbreviation === myAbbr ||
                    home?.team?.displayName?.toLowerCase().includes((teamName || '').toLowerCase());
    const opp     = isHome ? (away?.team?.displayName ?? away?.team?.abbreviation ?? '?')
                           : (home?.team?.displayName ?? home?.team?.abbreviation ?? '?');
    const myScore  = isHome ? Number(home?.score) : Number(away?.score);
    const oppScore = isHome ? Number(away?.score) : Number(home?.score);
    const isFinal  = g?.status?.type?.state === 'post';
    const won  = isFinal && myScore > oppScore;
    const drew = isFinal && myScore === oppScore;
    return (
      <View key={key} style={[searchStyles.matchRow, { borderBottomColor: colors.border }]}>
        <Text style={[searchStyles.matchDate, { color: colors.textMuted }]}>{_formatKickoff(g.date)}</Text>
        <View style={{ flex: 1 }}>
          <Text style={[searchStyles.matchName, { color: colors.textPrimary }]} numberOfLines={1}>
            {isHome ? 'vs' : '@'} <Text style={{ fontWeight: '700' }}>{opp}</Text>
          </Text>
          <Text style={[searchStyles.matchLeague, { color: colors.textMuted }]}>{_compLabel(g.league)}</Text>
        </View>
        {isFinal && (
          <View style={[searchStyles.scoreBadge, { backgroundColor: won ? '#14532d' : drew ? colors.card : '#450a0a' }]}>
            <Text style={[searchStyles.scoreTxt, { color: won ? '#4ade80' : drew ? colors.textMuted : '#f87171' }]}>
              {myScore}–{oppScore}
            </Text>
          </View>
        )}
      </View>
    );
  }

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={[searchStyles.container, { backgroundColor: colors.bg }]}>

        {/* ── Team preview panel ── */}
        {previewTeam ? (
          <View style={{ flex: 1 }}>
            {/* Header */}
            <View style={[searchStyles.searchBar, { paddingTop: insets.top + 8, borderBottomColor: colors.border }]}>
              <TouchableOpacity onPress={() => setPreviewTeam(null)} style={{ paddingHorizontal: 8, paddingVertical: 4 }}>
                <Ionicons name="chevron-back" size={22} color={colors.textPrimary} />
              </TouchableOpacity>
              {previewTeam.logo
                ? <Image source={{ uri: previewTeam.logo }} style={searchStyles.teamLogo} contentFit="contain" />
                : null}
              <Text style={[searchStyles.rowName, { flex: 1, marginLeft: 6, fontSize: 17, color: colors.textPrimary }]} numberOfLines={1}>
                {previewTeam.name}
              </Text>
              <TouchableOpacity onPress={onClose} style={searchStyles.doneBtn}>
                <Text style={[searchStyles.doneTxt, { color: colors.textPrimary }]}>Done</Text>
              </TouchableOpacity>
            </View>

            {teamMatchesLoading ? (
              <ActivityIndicator style={{ marginTop: 48 }} color={colors.textMuted} />
            ) : (
              <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: insets.bottom + 130 }}>
                {/* ── Soccer upcoming ── */}
                {upcomingMatches.length > 0 && (
                  <>
                    <Text style={[searchStyles.sectionHeader, { color: colors.textMuted }]}>UPCOMING</Text>
                    {upcomingMatches.map(m => {
                      const isHome = m.home?.name === previewTeam.name;
                      const opp = isHome ? m.away?.name : m.home?.name;
                      return (
                        <View key={m.id} style={[searchStyles.matchRow, { borderBottomColor: colors.border }]}>
                          <Text style={[searchStyles.matchDate, { color: colors.textMuted }]}>{_formatKickoff(m.kickoff)}</Text>
                          <View style={{ flex: 1 }}>
                            <Text style={[searchStyles.matchName, { color: colors.textPrimary }]} numberOfLines={1}>
                              {isHome ? 'vs' : '@'} <Text style={{ fontWeight: '700' }}>{opp}</Text>
                            </Text>
                            <Text style={[searchStyles.matchLeague, { color: colors.textMuted }]}>{_compLabel(m.competition)}</Text>
                          </View>
                        </View>
                      );
                    })}
                  </>
                )}
                {/* ── Basketball upcoming ── */}
                {bbUpcoming.length > 0 && (
                  <>
                    <Text style={[searchStyles.sectionHeader, { color: colors.textMuted }]}>UPCOMING</Text>
                    {bbUpcoming.map((g, i) => _bbGameRow(g, previewTeam.name, `bbu-${i}`))}
                  </>
                )}
                {/* ── Soccer recent ── */}
                {recentMatches.length > 0 && (
                  <>
                    <Text style={[searchStyles.sectionHeader, { color: colors.textMuted, marginTop: 8 }]}>RECENT</Text>
                    {recentMatches.map(m => {
                      const isHome = m.home?.name === previewTeam.name;
                      const myScore  = isHome ? m.home?.score : m.away?.score;
                      const oppScore = isHome ? m.away?.score : m.home?.score;
                      const opp = isHome ? m.away?.name : m.home?.name;
                      const won  = myScore != null && oppScore != null && myScore > oppScore;
                      const drew = myScore != null && oppScore != null && myScore === oppScore;
                      return (
                        <View key={m.id} style={[searchStyles.matchRow, { borderBottomColor: colors.border }]}>
                          <Text style={[searchStyles.matchDate, { color: colors.textMuted }]}>{_formatKickoff(m.kickoff)}</Text>
                          <View style={{ flex: 1 }}>
                            <Text style={[searchStyles.matchName, { color: colors.textPrimary }]} numberOfLines={1}>
                              {isHome ? 'vs' : '@'} <Text style={{ fontWeight: '700' }}>{opp}</Text>
                            </Text>
                            <Text style={[searchStyles.matchLeague, { color: colors.textMuted }]}>{_compLabel(m.competition)}</Text>
                          </View>
                          <View style={[searchStyles.scoreBadge, { backgroundColor: won ? '#14532d' : drew ? colors.card : '#450a0a' }]}>
                            <Text style={[searchStyles.scoreTxt, { color: won ? '#4ade80' : drew ? colors.textMuted : '#f87171' }]}>
                              {myScore ?? '—'}–{oppScore ?? '—'}
                            </Text>
                          </View>
                        </View>
                      );
                    })}
                  </>
                )}
                {/* ── Basketball recent ── */}
                {bbRecent.length > 0 && (
                  <>
                    <Text style={[searchStyles.sectionHeader, { color: colors.textMuted, marginTop: 8 }]}>RECENT</Text>
                    {bbRecent.map((g, i) => _bbGameRow(g, previewTeam.name, `bbr-${i}`))}
                  </>
                )}
                {!hasGames && (
                  <View style={searchStyles.placeholder}>
                    <Text style={[searchStyles.placeholderTxt, { color: colors.textMuted }]}>No recent or upcoming games found</Text>
                  </View>
                )}
              </ScrollView>
            )}

            {/* Add / Remove button */}
            <View style={[searchStyles.previewFooter, { borderTopColor: colors.border, backgroundColor: colors.bg, paddingBottom: insets.bottom + 12 }]}>
              {(() => {
                const followed = isTeamFollowed(localStore, previewTeam.id);
                if (addedConfirm) {
                  return (
                    <View style={[searchStyles.addBtn, { backgroundColor: '#14532d' }]}>
                      <Ionicons name="checkmark-circle" size={20} color="#4ade80" />
                      <Text style={[searchStyles.addBtnTxt, { color: '#4ade80' }]}>Added to Favorites!</Text>
                    </View>
                  );
                }
                if (followed) {
                  return (
                    <TouchableOpacity style={[searchStyles.addBtn, { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border }]} onPress={handleAddRemove}>
                      <Ionicons name="star" size={18} color="#F59E0B" />
                      <Text style={[searchStyles.addBtnTxt, { color: colors.textPrimary }]}>Following · Tap to remove</Text>
                    </TouchableOpacity>
                  );
                }
                return (
                  <TouchableOpacity style={[searchStyles.addBtn, { backgroundColor: colors.orange }]} onPress={handleAddRemove}>
                    <Ionicons name="star-outline" size={18} color="#fff" />
                    <Text style={[searchStyles.addBtnTxt, { color: '#fff' }]}>Add to Favorites</Text>
                  </TouchableOpacity>
                );
              })()}
            </View>
          </View>
        ) : (
          /* ── Search results panel ── */
          <>
            <View style={[searchStyles.searchBar, { paddingTop: insets.top + 8, borderBottomColor: colors.border }]}>
              <View style={[searchStyles.inputWrap, { backgroundColor: colors.card, borderColor: colors.border }]}>
                <Ionicons name="search-outline" size={16} color={colors.textMuted} style={{ marginLeft: 10 }} />
                <TextInput
                  ref={inputRef}
                  style={[searchStyles.input, { color: colors.textPrimary }]}
                  placeholder="Search teams & leagues…"
                  placeholderTextColor={colors.textMuted}
                  value={query}
                  onChangeText={setQuery}
                  returnKeyType="search"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                {query.length > 0 && (
                  <TouchableOpacity onPress={() => setQuery('')} style={{ padding: 8 }}>
                    <Ionicons name="close-circle" size={16} color={colors.textMuted} />
                  </TouchableOpacity>
                )}
              </View>
              <TouchableOpacity onPress={onClose} style={searchStyles.doneBtn}>
                <Text style={[searchStyles.doneTxt, { color: colors.textPrimary }]}>Done</Text>
              </TouchableOpacity>
            </View>

            {searching ? (
              <ActivityIndicator style={{ marginTop: 40 }} color={colors.textMuted} />
            ) : !query.trim() ? (
              /* ── Browse mode ── */
              <View style={{ flex: 1 }}>
                {/* Sport pills */}
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={searchStyles.pillScroll} contentContainerStyle={searchStyles.pillRow}>
                  {(['football', 'basketball'] as const).map(sport => (
                    <TouchableOpacity
                      key={sport}
                      style={[searchStyles.pill, { backgroundColor: colors.card, borderColor: colors.border }, browseSport === sport && searchStyles.pillActive]}
                      onPress={() => setBrowseSport(sport)}
                    >
                      <Ionicons
                        name={sport === 'football' ? 'football-outline' : 'basketball-outline'}
                        size={13}
                        color={browseSport === sport ? '#FF6B00' : colors.textMuted}
                      />
                      <Text style={[searchStyles.pillTxt, { color: colors.textMuted }, browseSport === sport && searchStyles.pillTxtActive]}>
                        {sport === 'football' ? 'Football' : 'Basketball'}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>
                {/* League pills */}
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={searchStyles.pillScroll} contentContainerStyle={searchStyles.pillRow}>
                  {LeagueCatalog.leagues(browseSport).map(lg => (
                    <TouchableOpacity
                      key={lg.id}
                      style={[searchStyles.pill, { backgroundColor: colors.card, borderColor: colors.border }, browseLeague === lg.id && searchStyles.pillActive]}
                      onPress={() => setBrowseLeague(lg.id)}
                    >
                      <Text style={[searchStyles.pillTxt, { color: colors.textMuted }, browseLeague === lg.id && searchStyles.pillTxtActive]}>
                        {lg.label}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>
                {/* Follow league row */}
                {(() => {
                  const lg = LeagueCatalog.info(browseLeague);
                  const followed = isLeagueFollowed(localStore, browseLeague);
                  return (
                    <TouchableOpacity
                      style={[searchStyles.followLeagueRow, { borderBottomColor: colors.border, borderTopColor: colors.border }]}
                      onPress={() => toggleLeague({ id: lg.id, name: lg.label, sport: lg.sport })}
                      activeOpacity={0.7}
                    >
                      <Text style={[searchStyles.followLeagueTxt, { color: colors.textPrimary }]}>{lg.label}</Text>
                      <Ionicons name={followed ? 'star' : 'star-outline'} size={20} color={followed ? '#F59E0B' : colors.textMuted} />
                    </TouchableOpacity>
                  );
                })()}
                {/* Team list */}
                {browseLoading ? (
                  <ActivityIndicator style={{ marginTop: 40 }} color={colors.textMuted} />
                ) : browseLeague === 'world_cup' ? (
                  /* ── World Cup: grouped by A-L ── */
                  <SectionList
                    sections={(() => {
                      const groups = new Map<string, typeof WC2026_TEAMS>();
                      for (const t of WC2026_TEAMS) {
                        if (!groups.has(t.group)) groups.set(t.group, []);
                        groups.get(t.group)!.push(t);
                      }
                      return [...groups.entries()].map(([g, teams]) => ({
                        title: `GROUP ${g}`,
                        data: teams.map(t => ({ id: t.id, name: t.name, logo: t.logo, sport: 'football' as const, league: 'world_cup' })),
                      }));
                    })()}
                    keyExtractor={item => item.id}
                    initialNumToRender={24}
                    maxToRenderPerBatch={16}
                    windowSize={5}
                    renderSectionHeader={({ section }) => (
                      <Text style={[searchStyles.sectionHeader, { color: colors.textMuted, backgroundColor: colors.bg }]}>{section.title}</Text>
                    )}
                    renderItem={({ item: team }) => {
                      const followed = isTeamFollowed(localStore, team.id);
                      return (
                        <TouchableOpacity style={[searchStyles.browseRow, { borderBottomColor: colors.border }]} onPress={() => handleTeamRowPress(team)} activeOpacity={0.7}>
                          <Image source={{ uri: team.logo }} style={searchStyles.teamLogo} contentFit="contain" />
                          <Text style={[searchStyles.browseRowTxt, { color: colors.textPrimary }]} numberOfLines={1}>{team.name}</Text>
                          <Ionicons name={followed ? 'star' : 'chevron-forward'} size={16} color={followed ? '#F59E0B' : colors.textMuted} />
                        </TouchableOpacity>
                      );
                    }}
                    contentContainerStyle={{ paddingBottom: 40 }}
                  />
                ) : (
                  /* ── Other leagues: flat list ── */
                  <FlatList
                    data={browseTeams}
                    keyExtractor={item => item.id}
                    initialNumToRender={20}
                    maxToRenderPerBatch={15}
                    windowSize={5}
                    renderItem={({ item: team }) => {
                      const followed = isTeamFollowed(localStore, team.id);
                      const initials = (team.name ?? '').split(' ').map((w: string) => w[0]).join('').slice(0, 2).toUpperCase();
                      return (
                        <TouchableOpacity style={[searchStyles.browseRow, { borderBottomColor: colors.border }]} onPress={() => handleTeamRowPress(team)} activeOpacity={0.7}>
                          {team.logo
                            ? <Image source={{ uri: team.logo }} style={searchStyles.teamLogo} contentFit="contain" />
                            : <View style={[searchStyles.teamBadge, { backgroundColor: colors.card, borderColor: colors.border }]}><Text style={[searchStyles.teamBadgeTxt, { color: colors.textMuted }]}>{initials}</Text></View>
                          }
                          <Text style={[searchStyles.browseRowTxt, { color: colors.textPrimary }]} numberOfLines={1}>{team.name}</Text>
                          <Ionicons name={followed ? 'star' : 'chevron-forward'} size={16} color={followed ? '#F59E0B' : colors.textMuted} />
                        </TouchableOpacity>
                      );
                    }}
                    ListEmptyComponent={<View style={searchStyles.placeholder}><Text style={[searchStyles.placeholderTxt, { color: colors.textMuted }]}>No teams found</Text></View>}
                    contentContainerStyle={{ paddingBottom: 40 }}
                  />
                )}
              </View>
            ) : !hasResults ? (
              <View style={searchStyles.placeholder}>
                <Text style={[searchStyles.placeholderTxt, { color: colors.textMuted }]}>No results for "{query}"</Text>
              </View>
            ) : (
              <ScrollView keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
                {results.leagues.length > 0 && (
                  <>
                    <Text style={[searchStyles.sectionHeader, { color: colors.textMuted }]}>LEAGUES</Text>
                    {results.leagues.map(league => {
                      const followed = isLeagueFollowed(localStore, league.id);
                      return (
                        <TouchableOpacity key={`league-${league.id}`} style={[searchStyles.row, { borderBottomColor: colors.border }]} onPress={() => toggleLeague(league)} activeOpacity={0.7}>
                          <LeagueLogo leagueId={league.id} size={28} />
                          <View style={{ flex: 1 }}>
                            <Text style={[searchStyles.rowName, { color: colors.textPrimary }]}>{league.name}</Text>
                            <Text style={[searchStyles.rowSub, { color: colors.textMuted }]}>{league.sport === 'football' ? '⚽ Football' : '🏀 Basketball'}</Text>
                          </View>
                          <Ionicons name={followed ? 'star' : 'star-outline'} size={22} color={followed ? '#F59E0B' : colors.textMuted} />
                        </TouchableOpacity>
                      );
                    })}
                  </>
                )}
                {results.teams.length > 0 && (
                  <>
                    <Text style={[searchStyles.sectionHeader, { color: colors.textMuted }]}>TEAMS</Text>
                    {results.teams.map(team => {
                      const followed = isTeamFollowed(localStore, team.id);
                      const initials = (team.name ?? '').split(' ').map((w: string) => w[0]).join('').slice(0, 2).toUpperCase();
                      return (
                        <TouchableOpacity key={`team-${team.id}`} style={[searchStyles.row, { borderBottomColor: colors.border }]} onPress={() => handleTeamRowPress(team)} activeOpacity={0.7}>
                          {team.logo
                            ? <Image source={{ uri: team.logo }} style={searchStyles.teamLogo} contentFit="contain" />
                            : <View style={[searchStyles.teamBadge, { backgroundColor: colors.card, borderColor: colors.border }]}><Text style={[searchStyles.teamBadgeTxt, { color: colors.textMuted }]}>{initials}</Text></View>
                          }
                          <View style={{ flex: 1 }}>
                            <Text style={[searchStyles.rowName, { color: colors.textPrimary }]}>{team.name}</Text>
                            <Text style={[searchStyles.rowSub, { color: colors.textMuted }]}>
                              {team.sport === 'football' ? '⚽ Football' : '🏀 Basketball'}
                              {team.league ? ` · ${leagueDisplayName(team.league)}` : ''}
                            </Text>
                          </View>
                          <Ionicons name="chevron-forward" size={18} color={colors.textMuted} />
                        </TouchableOpacity>
                      );
                    })}
                  </>
                )}
                <View style={{ height: 40 }} />
              </ScrollView>
            )}
          </>
        )}
      </View>
    </Modal>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function EmptyFavoritesState({ colors, t, onSearch }: { colors: ReturnType<typeof useTheme>['colors']; t: (k: string) => string; onSearch: () => void }) {
  return (
    <View style={styles.centered}>
      <Ionicons name="star-outline" size={48} color={colors.textMuted} style={{ marginBottom: 12 }} />
      <Text style={[styles.emptyTitle, { color: colors.textPrimary }]}>{t('favorites.feed.empty.title')}</Text>
      <Text style={[styles.emptySubtitle, { color: colors.textMuted }]}>{t('favorites.feed.empty.subtitle')}</Text>
      <TouchableOpacity onPress={onSearch} style={[styles.searchCta, { backgroundColor: colors.card, borderColor: colors.border }]} activeOpacity={0.7}>
        <Ionicons name="search-outline" size={16} color={colors.textPrimary} />
        <Text style={[styles.searchCtaTxt, { color: colors.textPrimary }]}>Search teams & leagues</Text>
      </TouchableOpacity>
    </View>
  );
}

function DateSection({
  group, sportFilter, colors, t, onSelectSoccer, onSelectBball,
}: {
  group: DateGroup;
  sportFilter: SportFilter;
  colors: ReturnType<typeof useTheme>['colors'];
  t: (k: string) => string;
  onSelectSoccer: (m: SoccerMatch) => void;
  onSelectBball: (game: FeedBballGame, leagueId: string, yyyymmdd: string) => void;
}) {
  const filtered = group.leagueGroups.filter(lg =>
    sportFilter === 'all' ||
    (sportFilter === 'football' && lg.sport === 'football') ||
    (sportFilter === 'basketball' && lg.sport === 'basketball')
  );
  if (!filtered.length) return null;

  return (
    <View>
      <View style={[styles.dateHeader, { borderBottomColor: colors.border }]}>
        <Text style={[styles.dateTitle, { color: colors.textPrimary }]}>{dateLabel(group.yyyymmdd, t)}</Text>
      </View>
      {filtered.map(lg => (
        <LeagueSection
          key={lg.leagueId}
          group={lg}
          yyyymmdd={group.yyyymmdd}
          colors={colors}
          onSelectSoccer={onSelectSoccer}
          onSelectBball={onSelectBball}
        />
      ))}
    </View>
  );
}

function LeagueSection({
  group, yyyymmdd, colors, onSelectSoccer, onSelectBball,
}: {
  group: LeagueGroup;
  yyyymmdd: string;
  colors: ReturnType<typeof useTheme>['colors'];
  onSelectSoccer: (m: SoccerMatch) => void;
  onSelectBball: (game: FeedBballGame, leagueId: string, yyyymmdd: string) => void;
}) {
  return (
    <View style={[styles.leagueSection, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <View style={[styles.leagueHeader, { borderBottomColor: colors.borderMuted }]}>
        <Text style={[styles.leagueLabel, { color: colors.textMuted }]} numberOfLines={1}>
          {group.sport === 'football' ? '⚽ ' : '🏀 '}{group.leagueLabel}
        </Text>
      </View>
      {group.soccer.map(match => (
        <SoccerRow key={match.id} match={match} colors={colors} onTap={() => onSelectSoccer(match)} />
      ))}
      {group.basketball.map(game => (
        <BballRow key={game.id} game={game} onTap={() => onSelectBball(game, group.leagueId, yyyymmdd)} />
      ))}
    </View>
  );
}

function SoccerRow({ match, colors, onTap }: { match: SoccerMatch; colors: ReturnType<typeof useTheme>['colors']; onTap: () => void }) {
  const live = isLiveLike(match.status);
  const finished = match.status === 'ft' || match.status === 'et' || match.status === 'pens';
  const statusLabel = displayMinute(match) || (match.kickoff
    ? new Date(match.kickoff).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : '–');
  return (
    <CompactMatchRow
      statusLabel={statusLabel} isLive={live} isFinished={finished}
      watchScore={finished && match.watch_score != null ? match.watch_score : null}
      homeName={match.home.name} homeCrest={match.home.crest} homeScore={match.home.score} homePensScore={match.home.score_pens}
      awayName={match.away.name} awayCrest={match.away.crest} awayScore={match.away.score} awayPensScore={match.away.score_pens}
      hasAlarm={false} onBellTap={() => {}} onRowTap={onTap} hideBell
    />
  );
}

function BballRow({ game, onTap }: { game: FeedBballGame; onTap: () => void }) {
  const isLive = game.status === 'in';
  const isPost = game.status === 'post';
  let statusLabel = '';
  if (isLive && game.period && game.displayClock) statusLabel = `Q${game.period} ${game.displayClock}`;
  else if (isLive) statusLabel = 'LIVE';
  else if (isPost) statusLabel = 'Final';
  else if (game.kickoff) statusLabel = new Date(game.kickoff).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  return (
    <CompactMatchRow
      statusLabel={statusLabel} isLive={isLive} isFinished={isPost}
      watchScore={isPost ? (game.watchScore ?? null) : null}
      homeName={game.homeTeam} homeCrest={game.homeLogo} homeScore={game.status !== 'pre' ? game.homeScore : null}
      awayName={game.awayTeam} awayCrest={game.awayLogo} awayScore={game.status !== 'pre' ? game.awayScore : null}
      hasAlarm={false} onBellTap={() => {}} onRowTap={onTap} hideBell
    />
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: { paddingBottom: 8, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: 'transparent' },
  searchRow: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 4, paddingTop: 4, paddingBottom: 6, gap: 8 },
  searchPill: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 7, paddingHorizontal: 12, paddingVertical: 9, borderRadius: 10, borderWidth: StyleSheet.hairlineWidth },
  searchPlaceholder: { fontSize: 14, flex: 1 },
  headerTitle: { ...tabHeaderTitle, paddingHorizontal: 16, paddingBottom: 4 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  emptyTitle: { fontSize: 18, fontWeight: '700', marginBottom: 8, textAlign: 'center' },
  emptySubtitle: { fontSize: 14, textAlign: 'center', lineHeight: 20, marginBottom: 24 },
  emptyText: { fontSize: 14 },
  searchCta: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 20, paddingVertical: 12, borderRadius: 24, borderWidth: 1 },
  searchCtaTxt: { fontSize: 14, fontWeight: '600' },
  dateHeader: { paddingHorizontal: 16, paddingVertical: 10, borderBottomWidth: StyleSheet.hairlineWidth },
  dateTitle: { fontSize: 13, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  leagueSection: { marginHorizontal: 12, marginVertical: 6, borderRadius: 12, borderWidth: 1, overflow: 'hidden' },
  leagueHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  leagueLabel: { fontSize: 12, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.5, flex: 1 },
});

const searchStyles = StyleSheet.create({
  container: { flex: 1 },
  searchBar: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12, paddingBottom: 10, borderBottomWidth: StyleSheet.hairlineWidth, gap: 8 },
  inputWrap: { flex: 1, flexDirection: 'row', alignItems: 'center', borderRadius: 10, borderWidth: 1, height: 40 },
  input: { flex: 1, height: 40, paddingHorizontal: 8, fontSize: 15 },
  doneBtn: { paddingHorizontal: 4, paddingVertical: 6 },
  doneTxt: { fontSize: 15, fontWeight: '600' },
  placeholder: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 40 },
  placeholderTxt: { fontSize: 15, textAlign: 'center' },
  sectionHeader: { fontSize: 11, fontWeight: '700', letterSpacing: 0.6, paddingHorizontal: 16, paddingTop: 16, paddingBottom: 4 },
  row: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: StyleSheet.hairlineWidth, gap: 12 },
  rowName: { fontSize: 15, fontWeight: '600' },
  rowSub: { fontSize: 12, marginTop: 2 },
  teamLogo: { width: 36, height: 36, borderRadius: 4 },
  teamBadge: { width: 36, height: 36, borderRadius: 18, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  teamBadgeTxt: { fontSize: 11, fontWeight: '700' },
  // ── Team preview panel ─────────────────────────────────────────────
  matchRow: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 11, borderBottomWidth: StyleSheet.hairlineWidth, gap: 12 },
  matchDate: { fontSize: 12, fontWeight: '600', width: 50 },
  matchName: { fontSize: 14, fontWeight: '500' },
  matchLeague: { fontSize: 11, marginTop: 2 },
  scoreBadge: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: 6, minWidth: 46, alignItems: 'center' },
  scoreTxt: { fontSize: 12, fontWeight: '700' },
  previewFooter: { position: 'absolute', bottom: 0, left: 0, right: 0, padding: 16, borderTopWidth: StyleSheet.hairlineWidth },
  addBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 15, borderRadius: 14 },
  addBtnTxt: { fontSize: 16, fontWeight: '700' },
  // ── Browse mode (matches favorites.tsx picker style exactly) ────
  pillScroll:  { height: 44, flexGrow: 0, flexShrink: 0, marginBottom: 2 },
  pillRow:     { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 6, gap: 8 },
  pill:        { height: 30, paddingHorizontal: 14, borderRadius: 15, borderWidth: 1, flexShrink: 0, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 5 },
  pillActive:  { backgroundColor: 'rgba(255,107,0,0.12)', borderColor: '#FF6B00' },
  pillTxt:     { fontSize: 13, fontWeight: '700', includeFontPadding: false, textAlignVertical: 'center', lineHeight: 16 },
  pillTxtActive: { color: '#FF6B00' },
  browseRow:    { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 10, gap: 10, borderBottomWidth: StyleSheet.hairlineWidth },
  browseRowTxt: { fontSize: 15, fontWeight: '600', flex: 1 },
  followingChip:    { flexDirection: 'row', alignItems: 'center', height: 30, paddingHorizontal: 12, borderRadius: 15, borderWidth: 1, flexShrink: 0, gap: 4 },
  followLeagueRow:  { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 11, gap: 10, borderTopWidth: StyleSheet.hairlineWidth, borderBottomWidth: StyleSheet.hairlineWidth },
  followLeagueTxt:  { flex: 1, fontSize: 15, fontWeight: '600' },
});
