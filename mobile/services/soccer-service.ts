import AsyncStorage from '@react-native-async-storage/async-storage';
import { SERVER_URL } from '../config';
import {
  SoccerMatch,
  SoccerMatchDetail,
  SoccerDramaScore,
  DramaTimelinePoint,
  MatchLineups,
  LeagueStandings,
  SoccerAlarm,
  SoccerAlarmType,
  SoccerFavoritesResponse,
  SoccerFavTeam,
  SoccerFavPlayer,
  SoccerFavCompetition,
  SoccerHistoricPref,
  SoccerHistoricCategoryKey,
} from '../types/soccer';

// ── In-memory cache for schedule data (5 min TTL) ────────────────────────────

interface CacheEntry<T> {
  data: T;
  at: number;
}

const _matchCache = new Map<string, CacheEntry<SoccerMatch[]>>();
const CACHE_TTL = 5 * 60 * 1000; // 5 min

function _cacheKey(league: string, date: string) {
  return `${league}|${date}`;
}

function _todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
}
const _SM_STORE = (l: string, d: string) => `sm_v1_${l}_${d}`;

// ── Core fetch helper ─────────────────────────────────────────────────────────

async function _get<T>(path: string, params?: Record<string, string>, timeoutMs = 12_000): Promise<T> {
  const url = new URL(`${SERVER_URL}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url.toString(), { signal: ctrl.signal });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json() as Promise<T>;
  } finally {
    clearTimeout(timer);
  }
}

async function _post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${SERVER_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

async function _delete<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${SERVER_URL}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }
  const res = await fetch(url.toString(), { method: 'DELETE' });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

interface MatchesResponse {
  matches: SoccerMatch[];
  league?: string;
  date?: string;
}

export async function getMatches(
  league: string,
  date?: string,
  tz: string = Intl.DateTimeFormat().resolvedOptions().timeZone
): Promise<SoccerMatch[]> {
  const params: Record<string, string> = { league, tz };
  if (date) params.date = date;
  const resp = await _get<MatchesResponse>('/soccer/games', params);
  return resp.matches;
}

export async function getMatchesCached(
  league: string,
  date: string
): Promise<SoccerMatch[]> {
  const key = _cacheKey(league, date);
  const cached = _matchCache.get(key);
  if (cached && Date.now() - cached.at < CACHE_TTL) {
    return cached.data;
  }

  const isPast = date < _todayStr();

  // Past dates: only serve from AsyncStorage if every match is truly finished
  // (stale 'scheduled' cache from a pre-game session would otherwise persist forever)
  if (isPast && !cached) {
    try {
      const raw = await AsyncStorage.getItem(_SM_STORE(league, date));
      if (raw) {
        const data: SoccerMatch[] = JSON.parse(raw);
        const allDone = data.every(m =>
          m.status === 'ft' || m.status === 'postponed' || m.status === 'cancelled'
        );
        if (allDone) {
          _matchCache.set(key, { data, at: Date.now() });
          return data;
        }
        // Has unfinished matches — fall through to network to get real status
      }
    } catch {}
  }

  try {
    const matches = await getMatches(league, date);
    _matchCache.set(key, { data: matches, at: Date.now() });
    // Only cache past dates once all matches are final — avoids stale 'scheduled' cache
    const allDone = matches.every(m =>
      m.status === 'ft' || m.status === 'postponed' || m.status === 'cancelled'
    );
    if (isPast && matches.length > 0 && allDone) {
      AsyncStorage.setItem(_SM_STORE(league, date), JSON.stringify(matches)).catch(() => {});
    }
    return matches;
  } catch {
    return cached?.data ?? [];
  }
}

export function invalidateMatchCache(league?: string) {
  if (league) {
    for (const key of _matchCache.keys()) {
      if (key.startsWith(`${league}|`)) _matchCache.delete(key);
    }
  } else {
    _matchCache.clear();
  }
}

// ── Match detail ──────────────────────────────────────────────────────────────

const _detailCache = new Map<string, CacheEntry<SoccerMatchDetail>>();
const DETAIL_TTL = 5 * 60 * 1000;

export async function getMatchDetail(canonicalId: string): Promise<SoccerMatchDetail> {
  const cached = _detailCache.get(canonicalId);
  if (cached && Date.now() - cached.at < DETAIL_TTL) return cached.data;
  const detail = await _get<SoccerMatchDetail>(`/soccer/match/${canonicalId}`);
  _detailCache.set(canonicalId, { data: detail, at: Date.now() });
  return detail;
}

// ── Drama Engine ──────────────────────────────────────────────────────────────

interface DramaResponse {
  match_id: string;
  minute?: number;
  status?: string;
  drama: SoccerDramaScore;
}

export async function getDramaScore(canonicalId: string): Promise<SoccerDramaScore> {
  const resp = await _get<DramaResponse>(`/soccer/drama/${canonicalId}`);
  return resp.drama;
}

interface DramaTimelineResponse {
  match_id: string;
  timeline: DramaTimelinePoint[];
}

export async function getDramaTimeline(
  canonicalId: string,
  step = 2
): Promise<DramaTimelinePoint[]> {
  const resp = await _get<DramaTimelineResponse>(
    `/soccer/drama/${canonicalId}/timeline`,
    { step: String(step) }
  );
  return resp.timeline;
}

// ── Lineups ───────────────────────────────────────────────────────────────────

export async function getLineups(canonicalId: string): Promise<MatchLineups> {
  return _get<MatchLineups>(`/soccer/match/${canonicalId}/lineups`);
}

export interface SquadPlayer {
  player_id: number;
  name: string;
  jersey_number?: number;
  position?: string;
  club?: string;
}

// ── Squad cache (memory + AsyncStorage) ──────────────────────────────────────

const _squadMem = new Map<string, { players: SquadPlayer[]; ts: number }>();
const _SQUAD_MEM_TTL  = 4 * 60 * 60 * 1000; // 4 h in-process
const _SQUAD_DISK_TTL = Infinity;             // squads don't change during a tournament
const _squadKey = (id: string) => `squad_v1_${id}`;

export async function getTeamSquad(teamId: string): Promise<SquadPlayer[]> {
  // 1. In-memory (warm start — instant)
  const mem = _squadMem.get(teamId);
  if (mem && Date.now() - mem.ts < _SQUAD_MEM_TTL) return mem.players;

  // 2. AsyncStorage (cold start — fast, no network)
  try {
    const raw = await AsyncStorage.getItem(_squadKey(teamId));
    if (raw) {
      const { players, ts }: { players: SquadPlayer[]; ts: number } = JSON.parse(raw);
      if (Date.now() - ts < _SQUAD_DISK_TTL) {
        _squadMem.set(teamId, { players, ts });
        return players;
      }
    }
  } catch {}

  // 3. Network
  const data = await _get<{ players: SquadPlayer[] }>(`/soccer/team/${teamId}/squad`);
  const players = data.players ?? [];
  const entry = { players, ts: Date.now() };
  _squadMem.set(teamId, entry);
  AsyncStorage.setItem(_squadKey(teamId), JSON.stringify(entry)).catch(() => {});
  return players;
}

// ── Live feed ─────────────────────────────────────────────────────────────────

export async function getLiveMatches(leagues?: string[]): Promise<SoccerMatch[]> {
  const params: Record<string, string> = {};
  if (leagues?.length) params.leagues = leagues.join(',');
  const resp = await _get<MatchesResponse>('/soccer/live', params);
  return resp.matches;
}

// ── Standings ─────────────────────────────────────────────────────────────────

export async function getStandings(leagueSlug: string): Promise<LeagueStandings> {
  return _get<LeagueStandings>(`/soccer/league/${leagueSlug}/standings`);
}

// ── Soccer Alarms CRUD ────────────────────────────────────────────────────────

type AlarmConfigValue = boolean | string | number;

interface CreateAlarmRequest {
  user_token: string;
  alarm_type: string;
  match_id?: string;
  match_name?: string;
  competition_id?: string;
  player_id?: string;
  threshold?: number;
  config?: Record<string, AlarmConfigValue>;
  alert_type: string;
}

interface CreateAlarmResponse {
  status: string;
  alarm_id: number;
  alarm_type: string;
  match_id?: string;
}

export async function createAlarm(params: {
  userToken: string;
  alarmType: SoccerAlarmType;
  matchId?: string;
  matchName?: string;
  competitionId?: string;
  playerId?: string;
  threshold?: number;
  config?: Record<string, AlarmConfigValue>;
  alertType: 'alarm' | 'silent';
}): Promise<CreateAlarmResponse> {
  const body: CreateAlarmRequest = {
    user_token: params.userToken,
    alarm_type: params.alarmType,
    match_id: params.matchId,
    match_name: params.matchName,
    competition_id: params.competitionId,
    player_id: params.playerId,
    threshold: params.threshold,
    config: params.config,
    alert_type: params.alertType,
  };
  return _post<CreateAlarmResponse>('/soccer-alarm', body);
}

export async function deleteAlarm(id: number, userToken: string): Promise<void> {
  await _delete(`/soccer-alarm/${id}`, { user_token: userToken });
}

interface AlarmsWrapper {
  alarms: SoccerAlarm[];
}

export async function listAlarms(userToken: string): Promise<SoccerAlarm[]> {
  const resp = await _get<AlarmsWrapper>('/soccer-alarms', { user_token: userToken });
  return resp.alarms;
}

// ── Soccer Favorites ──────────────────────────────────────────────────────────

export async function getFavorites(userToken: string): Promise<SoccerFavoritesResponse> {
  return _get<SoccerFavoritesResponse>('/soccer/favorites', { user_token: userToken });
}

export async function upsertFavTeam(
  team: SoccerFavTeam & { user_token: string }
): Promise<void> {
  await _post('/soccer/favorites/teams', team);
}

export async function deleteFavTeam(teamId: string, userToken: string): Promise<void> {
  await _delete(`/soccer/favorites/teams/${teamId}`, { user_token: userToken });
}

export async function upsertFavPlayer(
  player: SoccerFavPlayer & { user_token: string }
): Promise<void> {
  await _post('/soccer/favorites/players', player);
}

export async function deleteFavPlayer(playerId: string, userToken: string): Promise<void> {
  await _delete(`/soccer/favorites/players/${playerId}`, { user_token: userToken });
}

export async function upsertFavCompetition(params: {
  userToken: string;
  competitionId: string;
  dramaThreshold?: number;
  alertType?: 'alarm' | 'silent';
}): Promise<void> {
  await _post('/soccer/favorites/competitions', {
    user_token: params.userToken,
    competition_id: params.competitionId,
    drama_threshold: params.dramaThreshold ?? 7.0,
    alert_type: params.alertType ?? 'silent',
  });
}

export async function deleteFavCompetition(
  competitionId: string,
  userToken: string
): Promise<void> {
  await _delete(`/soccer/favorites/competitions/${competitionId}`, {
    user_token: userToken,
  });
}

// ── Search ────────────────────────────────────────────────────────────────────

export interface SearchTeam {
  id: string;
  ids?: string[];
  leagues?: string[];
  name: string;
  sport: 'football' | 'basketball';
  league?: string;
  logo?: string;
}

export interface SearchLeague {
  id: string;
  name: string;
  sport: 'football' | 'basketball';
}

export interface SearchPlayer {
  id: string;
  name: string;
  team_id?: string;
  team_name?: string;
  sport: 'football' | 'basketball';
  position?: string;
}

export interface SearchResults {
  teams: SearchTeam[];
  leagues: SearchLeague[];
  players: SearchPlayer[];
}

export async function searchAll(query: string, limit = 15): Promise<SearchResults> {
  const resp = await _get<{ teams: SearchTeam[]; leagues: SearchLeague[]; players: SearchPlayer[] }>(
    '/search',
    { q: query, limit: String(limit) }
  );
  return { teams: resp.teams ?? [], leagues: resp.leagues ?? [], players: resp.players ?? [] };
}

export async function getBasketballStandings(leagueSlug: string): Promise<LeagueStandings> {
  return _get<LeagueStandings>(`/basketball/league/${leagueSlug}/standings`);
}

export async function getTeamMatches(teamId: string): Promise<SoccerMatch[]> {
  try {
    const res = await _get<SoccerMatch[]>(`/soccer/team/${teamId}/matches`);
    return Array.isArray(res) ? res : [];
  } catch {
    return [];
  }
}

export async function getBasketballTeamGames(teamId: string, league: string): Promise<any[]> {
  try {
    const res = await _get<any[]>(`/basketball/team/${teamId}/games`, { league });
    return Array.isArray(res) ? res : [];
  } catch {
    return [];
  }
}

export async function upsertSoccerHistoric(params: {
  userToken: string;
  enabled: boolean;
  alertType: 'alarm' | 'silent';
  categories: Partial<Record<SoccerHistoricCategoryKey, boolean>>;
}): Promise<SoccerHistoricPref> {
  return _post<SoccerHistoricPref>('/settings/soccer-historic', {
    user_token: params.userToken,
    enabled: params.enabled,
    alert_type: params.alertType,
    categories: params.categories,
  });
}
