import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface FollowedTeam {
  id: string;
  name: string;
  sport: 'football' | 'basketball';
  altIds?: string[];
  leagues?: string[];
}

export interface FollowedLeague {
  id: string;
  label: string;
  sport: 'football' | 'basketball';
}

export interface FollowStore {
  teams:   Record<string, FollowedTeam>;
  leagues: Record<string, FollowedLeague>;
}

// ── Storage ───────────────────────────────────────────────────────────────────

const FOLLOW_KEY = 'display_follows_v1';
let _cache: FollowStore | null = null;

async function _load(): Promise<FollowStore> {
  if (_cache) return _cache;
  try {
    const raw = await AsyncStorage.getItem(FOLLOW_KEY);
    if (raw) { _cache = JSON.parse(raw); return _cache!; }
  } catch {}
  _cache = { teams: {}, leagues: {} };
  return _cache;
}

async function _save(store: FollowStore): Promise<void> {
  _cache = store;
  AsyncStorage.setItem(FOLLOW_KEY, JSON.stringify(store)).catch(() => {});
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function getFollowStore(): Promise<FollowStore> {
  return _load();
}

export async function followTeam(team: FollowedTeam): Promise<void> {
  const s = await _load();
  await _save({ ...s, teams: { ...s.teams, [team.id]: team } });
}

export async function unfollowTeam(id: string): Promise<void> {
  const s = await _load();
  const { [id]: _removed, ...rest } = s.teams;
  await _save({ ...s, teams: rest });
}

export async function followLeague(league: FollowedLeague): Promise<void> {
  const s = await _load();
  await _save({ ...s, leagues: { ...s.leagues, [league.id]: league } });
}

export async function unfollowLeague(id: string): Promise<void> {
  const s = await _load();
  const { [id]: _removed, ...rest } = s.leagues;
  await _save({ ...s, leagues: rest });
}

export function isTeamFollowed(store: FollowStore, id: string): boolean {
  return id in store.teams;
}

export function isLeagueFollowed(store: FollowStore, id: string): boolean {
  return id in store.leagues;
}

export function hasAnyFollow(store: FollowStore): boolean {
  return Object.keys(store.teams).length > 0 || Object.keys(store.leagues).length > 0;
}
