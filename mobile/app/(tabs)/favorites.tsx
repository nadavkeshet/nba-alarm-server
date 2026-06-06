import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { useFocusEffect } from '@react-navigation/native';
import messaging from '@react-native-firebase/messaging';
import notifee, { AndroidImportance, AndroidVisibility } from '@notifee/react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Image } from 'expo-image';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTheme, type AppColors } from '../../context/ThemeContext';
import { useLanguage } from '../../context/LanguageContext';
import {
  Animated,
  ActivityIndicator,
  FlatList,
  Modal,
  NativeModules,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';

import Slider from '@react-native-community/slider';
import { BasketballToggle } from '../../components/basketball-toggle';
import { TeamBadge } from '../../components/TeamBadge';
import { SERVER_URL } from '../../config';
import { teamFullToHe } from '../../utils/teamNames';
import { signal } from '../../utils/telemetry';
import {
  getFavorites,
  upsertFavTeam,
  deleteFavTeam as deleteSoccerFavTeam,
  upsertFavPlayer,
  deleteFavPlayer as deleteSoccerFavPlayer,
  deleteFavCompetition,
  upsertFavCompetition,
  searchAll,
  getStandings,
} from '../../services/soccer-service';
import type { SoccerFavoritesResponse, SoccerFavTeam, SoccerFavPlayer, SoccerFavCompetition } from '../../types/soccer';
import type { SearchTeam, SearchPlayer } from '../../services/soccer-service';
import { LeagueCatalog } from '../../services/league-catalog';
import { HamburgerButton } from '../../components/drawer-menu';
import { tabHeaderTitle } from '../../constants/tokens';

// ─── Types ────────────────────────────────────────────────────────────────────

interface GlobalRadar      { enabled: boolean; max_diff: number; start_quarter: number; alert_type: string; leagues?: string[] }
interface FavTeam          { team_id: string; team_name: string; league?: string; max_diff: number; start_quarter: number; alert_type: string; broadcast_only: boolean; notify_at_start?: boolean; paused?: boolean }
interface FavPlayerEntry {
  name:           string;
  league?:        string;
  total_pts:      number | null;
  early_pts_q:    number | null;
  early_pts:      number | null;
  td_watch:       boolean;
  alert_type:     string;
  broadcast_only: boolean;
  paused?:        boolean;
}
interface HistoricalCats   { scoring_40_half: boolean; threes_11: boolean; quad_double_threat: boolean }
interface AppSettings {
  GlobalGameRadar:  GlobalRadar;
  FavoriteTeams:    FavTeam[];
  FavoritePlayers:  Record<string, FavPlayerEntry>;
  HistoricalMode:   { enabled: boolean; alert_type: string; categories: HistoricalCats };
}
interface CachedPlayer { id: string; name: string; jersey: string; position: string }
interface CachedTeam   { id: string; name: string; abbreviation: string; logo: string | null; players: CachedPlayer[]; league?: string }

const TEAMS_CACHE_KEY    = 'all_teams_cache_v1';
const SETTINGS_CACHE_KEY = 'fav_settings_cache_v1';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function qLabel(q: number | string | undefined | null): string {
  return String(q) === '5' ? "Last 5'" : `Q${q ?? '?'}`;
}

const QRow = React.memo(function QRow({ value, onChange, quarters = ['1', '2', '3', '4', '5'], styles }: { value: string; onChange: (q: string) => void; quarters?: string[]; styles: any }) {
  return (
    <View style={styles.quarterRow}>
      {quarters.map(q => (
        <TouchableOpacity
          key={q}
          style={[styles.qBtn, value === q && styles.qBtnActive]}
          onPress={() => onChange(q)}
        >
          <Text style={[styles.qBtnText, value === q && styles.qBtnTextActive]}>{q === '5' ? "Last 5'" : `Q${q}`}</Text>
        </TouchableOpacity>
      ))}
    </View>
  );
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

type FavTab = 'basketball' | 'football' | 'radar' | 'historical';
type IoniconName = React.ComponentProps<typeof Ionicons>['name'];

function SoccerTeamLogo({
  teamId, teamIds, name, crestMap, logo, size = 32,
}: {
  teamId: string;
  teamIds?: string[];
  name: string;
  crestMap: Map<string, string>;
  logo?: string;
  size?: number;
}) {
  const crest = logo ?? crestMap.get(teamId) ?? teamIds?.map(id => crestMap.get(id)).find(Boolean);
  const initials = name
    .split(/\s+/)
    .filter(w => w.length > 0)
    .slice(0, 2)
    .map(w => w[0])
    .join('')
    .toUpperCase() || '?';
  if (crest) {
    return (
      <Image
        source={{ uri: crest }}
        style={{ width: size, height: size, borderRadius: size / 6, marginRight: 0 }}
        contentFit="contain"
      />
    );
  }
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: '#1a5c3a', alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: '#fff', fontSize: size * 0.38, fontWeight: '800', letterSpacing: -0.3 }}>{initials}</Text>
    </View>
  );
}

function BballTeamLogo({ team, logoMap, size = 28 }: { team: CachedTeam | undefined | null; logoMap?: Record<string, string>; size?: number }) {
  const uri = (logoMap && team?.abbreviation && logoMap[team.abbreviation]) || team?.logo || undefined;
  return <TeamBadge abbreviation={team?.abbreviation ?? ''} size={size} uri={uri || undefined} />;
}

function LeagueBadge({ leagueId, size = 32 }: { leagueId: string; size?: number }) {
  const info = LeagueCatalog.info(leagueId);
  const label = info.label.split(' ').map(w => w[0]).join('').slice(0, 3).toUpperCase();
  return (
    <View style={{ width: size, height: size, borderRadius: 8, backgroundColor: info.color, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: '#fff', fontSize: size * 0.32, fontWeight: '900', letterSpacing: -0.3 }}>{label}</Text>
    </View>
  );
}

// ─── FavPillButton ────────────────────────────────────────────────────────────

function FavPillButton({ tab, selected, onPress, C, styles }: {
  tab: { id: string; label: string; icon: IoniconName };
  selected: boolean;
  onPress: () => void;
  C: ReturnType<typeof useTheme>['colors'];
  styles: ReturnType<typeof makeStyles>;
}) {
  const scale = useRef(new Animated.Value(1)).current;

  const handlePress = () => {
    scale.setValue(1.25);
    Animated.spring(scale, { toValue: 1, tension: 200, friction: 5, useNativeDriver: true }).start();
    onPress();
  };

  return (
    <TouchableOpacity onPress={handlePress} activeOpacity={1}>
      <Animated.View style={[styles.favTabPill, selected && styles.favTabPillActive, { transform: [{ scale }] }]}>
        <Ionicons name={tab.icon} size={13} color={selected ? C.orange : C.textMuted} />
        <Text style={[styles.favTabPillText, selected && styles.favTabPillTextActive]}>{tab.label}</Text>
      </Animated.View>
    </TouchableOpacity>
  );
}

// ─── FavoritesScreen ──────────────────────────────────────────────────────────

export default function FavoritesScreen() {
  const { colors: C } = useTheme();
  const { t, isHebrew } = useLanguage();
  const styles = useMemo(() => makeStyles(C), [C]);
  const insets = useSafeAreaInsets();
  const [userToken, setUserToken]       = useState('');
  const [tokenLoaded, setTokenLoaded]   = useState(false);
  const [settings, setSettings]         = useState<AppSettings | null>(null);
  const [soccerFavs, setSoccerFavs]     = useState<SoccerFavoritesResponse | null>(null);
  const [loading, setLoading]           = useState(false);
  const [radarRunning, setRadarRunning] = useState(false);
  const [saving, setSaving]             = useState(false);
  const [savedId, setSavedId]           = useState<string | null>(null);
  const [activeTab, setActiveTab]       = useState<FavTab>('basketball');
  const [leaguePickerVisible, setLeaguePickerVisible] = useState(false);
  const [soccerTeamEdits, setSoccerTeamEdits] = useState<Record<string, {
    drama_threshold: number; alert_type: 'alarm' | 'silent'; paused: boolean; notify_at_kickoff: boolean;
  }>>({});
  const [infoModal, setInfoModal] = useState<{ visible: boolean; title: string; message: string }>({ visible: false, title: '', message: '' });

  const showInfo = (title: string, message: string) => setInfoModal({ visible: true, title, message });
  const hideInfo = () => setInfoModal(prev => ({ ...prev, visible: false }));

  const [allTeams, setAllTeams] = useState<CachedTeam[]>([]);
  const [bbLogoMap, setBbLogoMap] = useState<Record<string, string>>({});
  const [teamCrestMap, setTeamCrestMap] = useState<Map<string, string>>(new Map());
  const [soccerTeamCache, setSoccerTeamCache] = useState<SearchTeam[]>([]);

  // Soccer team add modal
  const [soccerTeamModalVisible, setSoccerTeamModalVisible] = useState(false);
  const [pickedSoccerTeam, setPickedSoccerTeam]             = useState<SearchTeam | null>(null);
  const [soccerTeamSearch, setSoccerTeamSearch]             = useState('');
  const [soccerTeamResults, setSoccerTeamResults]           = useState<SearchTeam[]>([]);
  const [soccerTeamSearching, setSoccerTeamSearching]       = useState(false);
  const [newSoccerDramaThreshold, setNewSoccerDramaThreshold] = useState(7.0);
  const [newSoccerAlertType, setNewSoccerAlertType]           = useState<'alarm' | 'silent'>('silent');
  const [newSoccerNotifyKickoff, setNewSoccerNotifyKickoff]   = useState(false);

  // Soccer player add modal
  const [soccerPlayerModalVisible, setSoccerPlayerModalVisible] = useState(false);
  const [pickedSoccerPlayer, setPickedSoccerPlayer]             = useState<SearchPlayer | null>(null);
  const [soccerPlayerSearch, setSoccerPlayerSearch]             = useState('');
  const [soccerPlayerResults, setSoccerPlayerResults]           = useState<SearchPlayer[]>([]);
  const [soccerPlayerSearching, setSoccerPlayerSearching]       = useState(false);
  const [newSoccerPlayerAlertType, setNewSoccerPlayerAlertType] = useState<'alarm' | 'silent'>('silent');

  const [leagueBrowseTeams, setLeagueBrowseTeams]   = useState<SearchTeam[]>([]);
  const [leagueBrowseLoading, setLeagueBrowseLoading] = useState(false);

  const soccerTeamDebounceRef  = useRef<ReturnType<typeof setTimeout> | null>(null);
  const soccerPlayerDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [soccerPickerLeague, setSoccerPickerLeague] = useState<string>('premier_league');

  const ListSeparator = useCallback(() => <View style={styles.separator} />, []);

  // Global radar local inputs
  const [radarMaxDiff, setRadarMaxDiff]     = useState('5');
  const [radarStartQ, setRadarStartQ]       = useState('4');
  const [radarLeagues, setRadarLeagues]     = useState<string[]>(['nba']);

  // Per-team in-place edit state
  const [teamEdits, setTeamEdits] = useState<Record<string, { max_diff: number; start_quarter: string; alert_type: string; broadcast_only: boolean; notify_at_start: boolean }>>({});

  // ── League filter + search (shared between team & player pickers) ────
  const LEAGUES = [
    { id: 'nba',          label: 'NBA' },
    { id: 'euroleague',   label: 'Euroleague' },
    { id: 'eurocup',      label: 'EuroCup' },
    { id: 'bcl',          label: 'BCL' },
    { id: 'winner_league', label: 'Winner' },
  ] as const;

  const [pickerLeague, setPickerLeague] = useState<string>('nba');
  const [pickerSearch, setPickerSearch] = useState('');

  // ── Add Team Modal state ──────────────────────────────────────────────
  const [teamModalVisible, setTeamModalVisible] = useState(false);
  const [pickedTeam, setPickedTeam]             = useState<CachedTeam | null>(null);
  const [newTeamMaxDiff, setNewTeamMaxDiff]           = useState(5);
  const [newTeamStartQ, setNewTeamStartQ]             = useState('4');
  const [newTeamAlertType, setNewTeamAlertType]       = useState<'alarm' | 'silent'>('silent');
  const [newTeamBroadcastOnly, setNewTeamBroadcastOnly] = useState(false);
  const [newTeamNoCondition, setNewTeamNoCondition]   = useState(false);

  // Per-player in-place edit state
  const [playerEdits, setPlayerEdits] = useState<Record<string, {
    totalPtsEnabled: boolean; total_pts: number;
    hotStartEnabled: boolean; early_pts_q: string; early_pts: number;
    td_watch: boolean; alert_type: string; broadcast_only: boolean;
  }>>({});

  // ── Add Player Modal state ────────────────────────────────────────────
  const [playerModalVisible, setPlayerModalVisible]   = useState(false);
  const [pickedTeamForPlayer, setPickedTeamForPlayer] = useState<CachedTeam | null>(null);
  const [pickedPlayer, setPickedPlayer]               = useState<CachedPlayer | null>(null);
  const [newTotalPtsEnabled,  setNewTotalPtsEnabled]  = useState(false);
  const [newTotalPts,         setNewTotalPts]         = useState(40);
  const [newHotStartEnabled,  setNewHotStartEnabled]  = useState(false);
  const [newHotStartQ,        setNewHotStartQ]        = useState('1');
  const [newHotStartPts,      setNewHotStartPts]      = useState(15);
  const [newTdWatch,          setNewTdWatch]          = useState(false);
  const [newPlayerAlertType,     setNewPlayerAlertType]     = useState<'alarm' | 'silent'>('silent');
  const [newPlayerBroadcastOnly, setNewPlayerBroadcastOnly] = useState(false);

  // ── Load push token once ───────────────────────────────────────────────
  useEffect(() => {
    messaging().getToken()
      .then(tok => setUserToken(tok))
      .catch(() => {})
      .finally(() => setTokenLoaded(true));
  }, []);

  // ── Load basketball teams (with AsyncStorage cache for instant open) ──
  useEffect(() => {
    AsyncStorage.getItem(TEAMS_CACHE_KEY)
      .then(raw => { if (raw) setAllTeams(JSON.parse(raw)); })
      .catch(() => {});
    fetch(`${SERVER_URL}/all-teams-and-players`)
      .then(r => r.json())
      .then(resp => {
        const teams = resp.teams ?? [];
        setAllTeams(teams);
        AsyncStorage.setItem(TEAMS_CACHE_KEY, JSON.stringify(teams)).catch(() => {});
      })
      .catch(() => {});
  }, []);

  // ── Build soccer team cache + crest map + bb logo map from sports cache ─
  useEffect(() => {
    AsyncStorage.getItem('sports_cache_v2').then(raw => {
      if (!raw) return;
      try {
        const cache = JSON.parse(raw);
        const crestMap = new Map<string, string>();
        const teamMap  = new Map<string, SearchTeam>();
        const bbLogos: Record<string, string> = {};
        for (const day of cache.dateGroups ?? []) {
          for (const lg of day.leagueGroups ?? []) {
            for (const m of lg.soccer ?? []) {
              if (m.home?.id) {
                if (m.home.crest) crestMap.set(m.home.id, m.home.crest);
                if (!teamMap.has(m.home.id))
                  teamMap.set(m.home.id, { id: m.home.id, name: m.home.name ?? '', logo: m.home.crest, sport: 'football', league: lg.leagueId });
              }
              if (m.away?.id) {
                if (m.away.crest) crestMap.set(m.away.id, m.away.crest);
                if (!teamMap.has(m.away.id))
                  teamMap.set(m.away.id, { id: m.away.id, name: m.away.name ?? '', logo: m.away.crest, sport: 'football', league: lg.leagueId });
              }
            }
            // Extract basketball team logos from ESPN game data
            for (const g of lg.basketball ?? []) {
              if (g.homeTeam && g.homeLogo) bbLogos[g.homeTeam] = g.homeLogo;
              if (g.awayTeam && g.awayLogo) bbLogos[g.awayTeam] = g.awayLogo;
            }
          }
        }
        if (crestMap.size > 0) setTeamCrestMap(crestMap);
        if (teamMap.size > 0)  setSoccerTeamCache(Array.from(teamMap.values()));
        if (Object.keys(bbLogos).length > 0) setBbLogoMap(bbLogos);
      } catch {}
    }).catch(() => {});
  }, []);

  // ── Auto-fetch teams from standings when cache is empty for the selected league ─
  useEffect(() => {
    if (!soccerTeamModalVisible || pickedSoccerTeam) return;
    if (soccerTeamSearch.length >= 2) return; // user is searching manually
    const cached = soccerTeamCache.filter(tm => tm.league === soccerPickerLeague);
    if (cached.length > 0) { setLeagueBrowseTeams([]); return; }
    setLeagueBrowseLoading(true);
    setLeagueBrowseTeams([]);
    getStandings(soccerPickerLeague)
      .then(standings => {
        const teams: SearchTeam[] = (standings.standings ?? []).map(row => ({
          id: String(row.team_id),
          name: row.team_name,
          logo: row.team_crest,
          sport: 'football' as const,
          league: soccerPickerLeague,
        }));
        // Populate crest map so logos render immediately
        setTeamCrestMap(prev => {
          const next = new Map(prev);
          for (const row of standings.standings ?? []) {
            if (row.team_crest) next.set(String(row.team_id), row.team_crest);
          }
          return next;
        });
        setLeagueBrowseTeams(teams);
      })
      .catch(() => {})
      .finally(() => setLeagueBrowseLoading(false));
  }, [soccerTeamModalVisible, soccerPickerLeague, soccerTeamCache, pickedSoccerTeam, soccerTeamSearch]);

  // ── Debounced soccer team / player search ────────────────────────────
  useEffect(() => {
    if (soccerTeamDebounceRef.current) clearTimeout(soccerTeamDebounceRef.current);
    if (soccerTeamSearch.length < 2) { setSoccerTeamResults([]); setSoccerTeamSearching(false); return; }
    setSoccerTeamSearching(true);
    soccerTeamDebounceRef.current = setTimeout(async () => {
      try {
        const results = await searchAll(soccerTeamSearch);
        setSoccerTeamResults(results.teams.filter(t => t.sport === 'football'));
      } catch {}
      setSoccerTeamSearching(false);
    }, 300);
  }, [soccerTeamSearch]);

  useEffect(() => {
    if (soccerPlayerDebounceRef.current) clearTimeout(soccerPlayerDebounceRef.current);
    if (soccerPlayerSearch.length < 2) { setSoccerPlayerResults([]); setSoccerPlayerSearching(false); return; }
    setSoccerPlayerSearching(true);
    soccerPlayerDebounceRef.current = setTimeout(async () => {
      try {
        const results = await searchAll(soccerPlayerSearch);
        setSoccerPlayerResults(results.players.filter(p => p.sport === 'football'));
      } catch {}
      setSoccerPlayerSearching(false);
    }, 300);
  }, [soccerPlayerSearch]);

  // ── Load settings + radar on focus ───────────────────────────────────
  const settingsLoaded = useRef(false);

  const applySettingsData = useCallback((s: AppSettings, radarIsRunning: boolean) => {
    setSettings(s);
    setRadarRunning(radarIsRunning);
    setRadarMaxDiff(String(s.GlobalGameRadar?.max_diff ?? 5));
    setRadarStartQ(String(s.GlobalGameRadar?.start_quarter ?? 4));
    setRadarLeagues(s.GlobalGameRadar?.leagues?.length ? s.GlobalGameRadar.leagues : ['nba']);
    const edits: Record<string, { max_diff: number; start_quarter: string; alert_type: string; broadcast_only: boolean; notify_at_start: boolean }> = {};
    for (const ft of s.FavoriteTeams ?? []) {
      edits[ft.team_id] = { max_diff: ft.max_diff, start_quarter: String(ft.start_quarter), alert_type: ft.alert_type ?? 'silent', broadcast_only: ft.broadcast_only ?? false, notify_at_start: ft.notify_at_start ?? false };
    }
    setTeamEdits(edits);
    const pEdits: Record<string, { totalPtsEnabled: boolean; total_pts: number; hotStartEnabled: boolean; early_pts_q: string; early_pts: number; td_watch: boolean; alert_type: string; broadcast_only: boolean }> = {};
    for (const [pid, p] of Object.entries(s.FavoritePlayers ?? {})) {
      pEdits[pid] = { totalPtsEnabled: p.total_pts != null, total_pts: p.total_pts ?? 40, hotStartEnabled: p.early_pts != null && p.early_pts_q != null, early_pts_q: String(p.early_pts_q ?? 1), early_pts: p.early_pts ?? 15, td_watch: p.td_watch ?? false, alert_type: p.alert_type ?? 'silent', broadcast_only: p.broadcast_only ?? false };
    }
    setPlayerEdits(pEdits);
  }, []);

  const loadSettings = useCallback(async () => {
    // Stale-while-revalidate: show cached data instantly, then refresh from network
    if (!settingsLoaded.current) {
      try {
        const raw = await AsyncStorage.getItem(SETTINGS_CACHE_KEY);
        if (raw) {
          const { settings: cs, radarRunning: cr } = JSON.parse(raw);
          applySettingsData(cs, cr ?? false);
        }
      } catch {}
    }
    try {
      const tokenParam = userToken ? `?user_token=${encodeURIComponent(userToken)}` : '';
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 8_000);
      const [sRes, rRes] = await Promise.all([
        fetch(`${SERVER_URL}/settings${tokenParam}`, { signal: ctrl.signal }),
        fetch(`${SERVER_URL}/radar-status`, { signal: ctrl.signal }),
      ]);
      clearTimeout(timer);
      const s: AppSettings = await sRes.json();
      const r              = await rRes.json();
      applySettingsData(s, r.running ?? false);
      AsyncStorage.setItem(SETTINGS_CACHE_KEY, JSON.stringify({ settings: s, radarRunning: r.running ?? false })).catch(() => {});

      // Load soccer favorites (non-blocking)
      if (userToken) {
        getFavorites(userToken).then(favs => {
          setSoccerFavs(favs);
          const sEdits: Record<string, { drama_threshold: number; alert_type: 'alarm' | 'silent'; paused: boolean; notify_at_kickoff: boolean }> = {};
          for (const tm of favs.teams) {
            sEdits[tm.team_id] = { drama_threshold: tm.drama_threshold, alert_type: tm.alert_type, paused: tm.paused, notify_at_kickoff: tm.notify_at_kickoff ?? false };
          }
          setSoccerTeamEdits(sEdits);
        }).catch(() => {});
      }
    } catch (e) {
      console.error('Favorites load error:', e);
    } finally {
      settingsLoaded.current = true;
      setLoading(false);
    }
  }, [userToken, applySettingsData]);

  useEffect(() => { if (tokenLoaded) loadSettings(); }, [loadSettings, tokenLoaded]);

  const flashSaved = useCallback((id: string) => {
    setSavedId(id);
    setTimeout(() => setSavedId(prev => prev === id ? null : prev), 1500);
  }, []);

  const requireToken = (): boolean => {
    if (userToken) return true;
    showInfo(t('settings.notReady'), t('settings.notReadyMsg'));
    return false;
  };

  // ── API helpers ────────────────────────────────────────────────────────
  const patchGlobalRadar = async (patch: Partial<GlobalRadar>) => {
    if (!requireToken() || !settings) return;
    const next = { ...settings.GlobalGameRadar, ...patch };
    setSettings(s => s ? { ...s, GlobalGameRadar: next } : s);
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/global-radar`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...next, user_token: userToken }),
      });
      if (next.enabled && !radarRunning) {
        await fetch(`${SERVER_URL}/start-radar`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ push_token: '', sound_channel: 'classic_alarm' }),
        });
        setRadarRunning(true);
      }
      Haptics.selectionAsync();
      flashSaved('radar');
    } finally { setSaving(false); }
  };

  const patchHistorical = async (patch: { enabled?: boolean; alert_type?: string; categories?: Partial<HistoricalCats> }) => {
    if (!requireToken() || !settings) return;
    const cur  = settings.HistoricalMode;
    const next = { enabled: patch.enabled ?? cur.enabled, alert_type: patch.alert_type ?? cur.alert_type ?? 'silent', categories: { ...cur.categories, ...patch.categories } };
    setSettings(s => s ? { ...s, HistoricalMode: next } : s);
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/historical`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...next, user_token: userToken }),
      });
      Haptics.selectionAsync();
    } finally { setSaving(false); }
  };

  // ── Team CRUD ─────────────────────────────────────────────────────────
  const saveTeam = async (payload: FavTeam) => {
    if (!requireToken()) return;
    setSaving(true);
    try {
      const resp = await fetch(`${SERVER_URL}/settings/teams`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, user_token: userToken }),
      });
      if (!resp.ok) {
        const msg = await resp.text().catch(() => '');
        showInfo('Save failed', msg || `Server error ${resp.status}`);
        setSaving(false);
        return;
      }
      setSettings(s => s ? {
        ...s,
        FavoriteTeams: [...s.FavoriteTeams.filter(ft => ft.team_id !== payload.team_id), payload],
      } : s);
      setTeamEdits(prev => ({
        ...prev,
        [payload.team_id]: { max_diff: payload.max_diff, start_quarter: String(payload.start_quarter), alert_type: payload.alert_type, broadcast_only: payload.broadcast_only ?? false, notify_at_start: payload.notify_at_start ?? false },
      }));
    } catch (e: any) {
      showInfo('Save failed', e?.message ?? 'Could not reach server');
    } finally { setSaving(false); }
  };

  const removeTeam = async (team_id: string) => {
    if (!requireToken()) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const q = `?user_token=${encodeURIComponent(userToken)}`;
    await fetch(`${SERVER_URL}/settings/teams/${team_id}${q}`, { method: 'DELETE' });
    signal('Favorite.Team.Removed');
    setSettings(s => s ? { ...s, FavoriteTeams: s.FavoriteTeams.filter(ft => ft.team_id !== team_id) } : s);
    setTeamEdits(prev => { const n = { ...prev }; delete n[team_id]; return n; });
  };

  const togglePauseTeam = async (team_id: string, currentPaused: boolean) => {
    if (!requireToken()) return;
    const next = !currentPaused;
    await fetch(`${SERVER_URL}/settings/teams/${team_id}/pause`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_token: userToken, paused: next }),
    });
    signal('Favorites.Paused', { value: String(next) });
    setSettings(s => s ? {
      ...s,
      FavoriteTeams: s.FavoriteTeams.map(ft => ft.team_id === team_id ? { ...ft, paused: next } : ft),
    } : s);
  };

  const pauseAllFavorites = async (paused: boolean) => {
    if (!requireToken()) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    await fetch(`${SERVER_URL}/settings/favorites/pause-all`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_token: userToken, paused }),
    });
    setSettings(s => s ? {
      ...s,
      FavoriteTeams:   s.FavoriteTeams.map(ft => ({ ...ft, paused })),
      FavoritePlayers: Object.fromEntries(Object.entries(s.FavoritePlayers).map(([id, p]) => [id, { ...p, paused }])),
    } : s);
  };

  const togglePausePlayer = async (player_id: string, currentPaused: boolean) => {
    if (!requireToken()) return;
    const next = !currentPaused;
    await fetch(`${SERVER_URL}/settings/players/${player_id}/pause`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_token: userToken, paused: next }),
    });
    signal('Favorites.Paused', { value: String(next) });
    setSettings(s => s ? {
      ...s,
      FavoritePlayers: {
        ...s.FavoritePlayers,
        [player_id]: { ...s.FavoritePlayers[player_id], paused: next },
      },
    } : s);
  };

  const confirmAddTeam = async () => {
    if (!pickedTeam) return;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    await saveTeam({
      team_id:         pickedTeam.id,
      team_name:       pickedTeam.name,
      league:          pickedTeam.league ?? 'nba',
      max_diff:        newTeamMaxDiff || 5,
      notify_at_start: newTeamNoCondition,
      start_quarter:   parseInt(newTeamStartQ, 10) || 4,
      alert_type:      newTeamAlertType,
      broadcast_only:  newTeamBroadcastOnly,
    });
    signal('Favorite.Team.Added', { mode: newTeamAlertType, maxDiff: String(newTeamMaxDiff || 5) });
    closeTeamModal();
  };

  const updateExistingTeam = async (team: FavTeam) => {
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    const edit = teamEdits[team.team_id] ?? { max_diff: team.max_diff, start_quarter: String(team.start_quarter), alert_type: team.alert_type, broadcast_only: team.broadcast_only ?? false, notify_at_start: team.notify_at_start ?? false };
    await saveTeam({
      ...team,
      max_diff:        edit.max_diff || 5,
      start_quarter:   parseInt(edit.start_quarter, 10) || 4,
      alert_type:      edit.alert_type,
      broadcast_only:  edit.broadcast_only,
      notify_at_start: edit.notify_at_start,
    });
    flashSaved(team.team_id);
  };

  // ── Player CRUD ───────────────────────────────────────────────────────
  const confirmAddPlayer = async () => {
    if (!requireToken() || !pickedPlayer) return;
    if (!newTotalPtsEnabled && !newHotStartEnabled && !newTdWatch) {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      showInfo(t('alarm.noCondition'), t('alarm.noConditionMsg'));
      return;
    }
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    const payload = {
      user_token:     userToken,
      player_id:      pickedPlayer.id,
      name:           pickedPlayer.name,
      league:         pickedTeamForPlayer?.league ?? 'nba',
      total_pts:      newTotalPtsEnabled  ? (newTotalPts  || 40) : null,
      early_pts_q:    newHotStartEnabled  ? (parseInt(newHotStartQ, 10) || 1) : null,
      early_pts:      newHotStartEnabled  ? (newHotStartPts || 15) : null,
      td_watch:       newTdWatch,
      alert_type:     newPlayerAlertType,
      broadcast_only: newPlayerBroadcastOnly,
    };
    setSaving(true);
    try {
      const resp = await fetch(`${SERVER_URL}/settings/players`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const msg = await resp.text().catch(() => '');
        showInfo('Save failed', msg || `Server error ${resp.status}`);
        setSaving(false);
        return;
      }
      // Verify the write actually landed — server always returns 200 even on DB failure
      const verifyResp = await fetch(`${SERVER_URL}/settings?user_token=${encodeURIComponent(userToken)}`);
      if (verifyResp.ok) {
        const verified = await verifyResp.json();
        if (!verified.FavoritePlayers?.[pickedPlayer.id]) {
          showInfo(t('settings.notReady'), 'Save failed — server error. Try again in a moment.');
          setSaving(false);
          return;
        }
      }
      setSettings(s => s ? {
        ...s,
        FavoritePlayers: {
          ...s.FavoritePlayers,
          [pickedPlayer.id]: {
            name:           pickedPlayer.name,
            total_pts:      payload.total_pts,
            early_pts_q:    payload.early_pts_q,
            early_pts:      payload.early_pts,
            td_watch:       payload.td_watch,
            alert_type:     newPlayerAlertType,
            broadcast_only: newPlayerBroadcastOnly,
          },
        },
      } : s);
      setPlayerEdits(prev => ({
        ...prev,
        [pickedPlayer.id]: {
          totalPtsEnabled: payload.total_pts    != null,
          total_pts:       payload.total_pts    ?? 40,
          hotStartEnabled: payload.early_pts != null,
          early_pts_q:     String(payload.early_pts_q  ?? 1),
          early_pts:       payload.early_pts ?? 15,
          td_watch:        payload.td_watch,
          alert_type:      newPlayerAlertType,
          broadcast_only:  newPlayerBroadcastOnly,
        },
      }));
      signal('Favorite.Player.Added', { mode: newPlayerAlertType, totalPts: String(payload.total_pts ?? 0), earlyPts: String(payload.early_pts ?? 0), tripleDbl: String(payload.td_watch) });
      closePlayerModal();
    } catch (e: any) {
      showInfo('Save failed', e?.message ?? 'Could not reach server');
    } finally { setSaving(false); }
  };

  const savePlayerSettings = async (player_id: string, name: string, alert_type: string) => {
    if (!requireToken()) return;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    const edit = playerEdits[player_id];
    if (!edit) return;
    const payload = {
      user_token:     userToken,
      player_id,
      name,
      league:         settings?.FavoritePlayers?.[player_id]?.league ?? 'nba',
      total_pts:      edit.totalPtsEnabled  ? (edit.total_pts || 40) : null,
      early_pts_q:    edit.hotStartEnabled  ? (parseInt(edit.early_pts_q, 10) || 1) : null,
      early_pts:      edit.hotStartEnabled  ? (edit.early_pts || 15) : null,
      td_watch:       edit.td_watch,
      alert_type,
      broadcast_only: edit.broadcast_only ?? false,
    };
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/players`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setSettings(s => s ? {
        ...s,
        FavoritePlayers: {
          ...s.FavoritePlayers,
          [player_id]: {
            name,
            total_pts:     payload.total_pts,
            early_pts_q:   payload.early_pts_q,
            early_pts:     payload.early_pts,
            td_watch:      payload.td_watch,
            alert_type,
            broadcast_only: payload.broadcast_only,
          },
        },
      } : s);
      flashSaved(player_id);
    } finally { setSaving(false); }
  };

  const removePlayer = async (player_id: string) => {
    if (!requireToken()) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const q = `?user_token=${encodeURIComponent(userToken)}`;
    await fetch(`${SERVER_URL}/settings/players/${player_id}${q}`, { method: 'DELETE' });
    signal('Favorite.Player.Removed');
    setSettings(s => {
      if (!s) return s;
      const next = { ...s.FavoritePlayers };
      delete next[player_id];
      return { ...s, FavoritePlayers: next };
    });
    setPlayerEdits(prev => { const n = { ...prev }; delete n[player_id]; return n; });
  };

  // ── Soccer team CRUD ──────────────────────────────────────────
  const saveSoccerTeam = async (teamId: string) => {
    if (!requireToken()) return;
    const team = soccerFavs?.teams.find(t => t.team_id === teamId);
    if (!team) return;
    const edit = soccerTeamEdits[teamId];
    if (!edit) return;
    setSaving(true);
    try {
      await upsertFavTeam({
        ...team,
        drama_threshold: edit.drama_threshold,
        alert_type: edit.alert_type,
        paused: edit.paused,
        notify_at_kickoff: edit.notify_at_kickoff,
        user_token: userToken,
      });
      setSoccerFavs(prev => prev ? {
        ...prev,
        teams: prev.teams.map(t => t.team_id === teamId
          ? { ...t, drama_threshold: edit.drama_threshold, alert_type: edit.alert_type, paused: edit.paused, notify_at_kickoff: edit.notify_at_kickoff }
          : t),
      } : prev);
      flashSaved(`s-${teamId}`);
    } finally { setSaving(false); }
  };

  const removeSoccerTeam = async (teamId: string) => {
    if (!userToken) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    await deleteSoccerFavTeam(teamId, userToken).catch(() => {});
    setSoccerFavs(prev => prev ? { ...prev, teams: prev.teams.filter(t => t.team_id !== teamId) } : prev);
    setSoccerTeamEdits(prev => { const n = { ...prev }; delete n[teamId]; return n; });
  };

  const togglePauseSoccerTeam = async (teamId: string) => {
    if (!requireToken()) return;
    const edit = soccerTeamEdits[teamId];
    if (!edit) return;
    const next = !edit.paused;
    setSoccerTeamEdits(prev => ({ ...prev, [teamId]: { ...edit, paused: next } }));
    const team = soccerFavs?.teams.find(t => t.team_id === teamId);
    if (team) {
      await upsertFavTeam({ ...team, paused: next, user_token: userToken }).catch(() => {});
    }
    setSoccerFavs(prev => prev ? { ...prev, teams: prev.teams.map(t => t.team_id === teamId ? { ...t, paused: next } : t) } : prev);
  };

  const removeSoccerPlayer = async (playerId: string) => {
    if (!userToken) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    await deleteSoccerFavPlayer(playerId, userToken).catch(() => {});
    setSoccerFavs(prev => prev ? { ...prev, players: prev.players.filter(p => p.player_id !== playerId) } : prev);
  };

  // ── Soccer team add modal ─────────────────────────────────────────────
  const openSoccerTeamModal = () => {
    setPickedSoccerTeam(null);
    setSoccerTeamSearch('');
    setSoccerTeamResults([]);
    setSoccerPickerLeague('premier_league');
    setNewSoccerDramaThreshold(7.0);
    setNewSoccerAlertType('silent');
    setNewSoccerNotifyKickoff(false);
    setSoccerTeamModalVisible(true);
  };
  const closeSoccerTeamModal = () => {
    setSoccerTeamModalVisible(false);
    setPickedSoccerTeam(null);
    setSoccerTeamSearch('');
    setSoccerTeamResults([]);
  };
  const confirmAddSoccerTeam = async () => {
    if (!pickedSoccerTeam || !requireToken()) return;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    setSaving(true);
    try {
      await upsertFavTeam({
        team_id: pickedSoccerTeam.id,
        team_name: pickedSoccerTeam.name,
        team_ids: pickedSoccerTeam.ids,
        drama_threshold: newSoccerDramaThreshold,
        alert_type: newSoccerAlertType,
        paused: false,
        notify_at_kickoff: newSoccerNotifyKickoff,
        user_token: userToken,
      });
      const newTeam: SoccerFavTeam = {
        team_id: pickedSoccerTeam.id,
        team_name: pickedSoccerTeam.name,
        team_ids: pickedSoccerTeam.ids,
        drama_threshold: newSoccerDramaThreshold,
        alert_type: newSoccerAlertType,
        paused: false,
        notify_at_kickoff: newSoccerNotifyKickoff,
      };
      setSoccerFavs(prev => prev
        ? { ...prev, teams: [...prev.teams.filter(t => t.team_id !== pickedSoccerTeam.id), newTeam] }
        : { teams: [newTeam], players: [], competitions: [] });
      setSoccerTeamEdits(prev => ({
        ...prev,
        [pickedSoccerTeam.id]: { drama_threshold: newSoccerDramaThreshold, alert_type: newSoccerAlertType, paused: false, notify_at_kickoff: newSoccerNotifyKickoff },
      }));
      closeSoccerTeamModal();
    } finally { setSaving(false); }
  };

  // ── Soccer player add modal ───────────────────────────────────────────
  const openSoccerPlayerModal = () => {
    setPickedSoccerPlayer(null);
    setSoccerPlayerSearch('');
    setSoccerPlayerResults([]);
    setNewSoccerPlayerAlertType('silent');
    setSoccerPlayerModalVisible(true);
  };
  const closeSoccerPlayerModal = () => {
    setSoccerPlayerModalVisible(false);
    setPickedSoccerPlayer(null);
    setSoccerPlayerSearch('');
    setSoccerPlayerResults([]);
  };
  const confirmAddSoccerPlayer = async () => {
    if (!pickedSoccerPlayer || !requireToken()) return;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    setSaving(true);
    try {
      await upsertFavPlayer({
        player_id: pickedSoccerPlayer.id,
        player_name: pickedSoccerPlayer.name,
        team_id: pickedSoccerPlayer.team_id,
        alert_type: newSoccerPlayerAlertType,
        paused: false,
        user_token: userToken,
      });
      const newPlayer: SoccerFavPlayer = {
        player_id: pickedSoccerPlayer.id,
        player_name: pickedSoccerPlayer.name,
        team_id: pickedSoccerPlayer.team_id,
        alert_type: newSoccerPlayerAlertType,
        paused: false,
      };
      setSoccerFavs(prev => prev
        ? { ...prev, players: [...prev.players.filter(p => p.player_id !== pickedSoccerPlayer.id), newPlayer] }
        : { teams: [], players: [newPlayer], competitions: [] });
      closeSoccerPlayerModal();
    } finally { setSaving(false); }
  };

  // ── Modal helpers ─────────────────────────────────────────────────────
  const openTeamModal = () => {
    setPickedTeam(null);
    setPickerLeague('nba');
    setPickerSearch('');
    setNewTeamMaxDiff(5);
    setNewTeamStartQ('4');
    setNewTeamAlertType('silent');
    setNewTeamBroadcastOnly(false);
    setNewTeamNoCondition(false);
    setTeamModalVisible(true);
  };
  const closeTeamModal = () => { setTeamModalVisible(false); setPickedTeam(null); };

  const openPlayerModal = () => {
    setPickedTeamForPlayer(null);
    setPickedPlayer(null);
    setPickerLeague('nba');
    setPickerSearch('');
    setPlayerModalVisible(true);
  };
  const closePlayerModal = () => {
    setPlayerModalVisible(false);
    setPickedTeamForPlayer(null);
    setPickedPlayer(null);
    setNewTotalPtsEnabled(false);
    setNewTotalPts(40);
    setNewHotStartEnabled(false);
    setNewHotStartQ('1');
    setNewHotStartPts(15);
    setNewTdWatch(false);
    setNewPlayerAlertType('silent');
    setNewPlayerBroadcastOnly(false);
  };

  // ── Derived ───────────────────────────────────────────────────────────
  const favTeamIds   = new Set(settings?.FavoriteTeams?.map(ft => ft.team_id) ?? []);
  const favPlayerIds = new Set(Object.keys(settings?.FavoritePlayers ?? {}));

  const allFavs = [...(settings?.FavoriteTeams ?? []), ...Object.values(settings?.FavoritePlayers ?? {})];
  const hasFavs = allFavs.length > 0;
  const allPaused = hasFavs && allFavs.every(f => f.paused);

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <ActivityIndicator size="large" color={C.accentBlue} style={{ flex: 1 }} />
      </SafeAreaView>
    );
  }

  const radar    = settings?.GlobalGameRadar;
  const hist     = settings?.HistoricalMode;
  const histCats = hist?.categories;

  const FAV_TABS: { id: FavTab; label: string; icon: IoniconName }[] = [
    { id: 'basketball', label: 'Basketball', icon: 'basketball-outline' },
    { id: 'football',   label: 'Football',   icon: 'football-outline'  },
    { id: 'radar',      label: 'Radar',      icon: 'radio-outline'     },
    { id: 'historical', label: 'Historical', icon: 'time-outline'      },
  ];

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: C.bg }]}>
      <View style={[styles.header, { borderBottomColor: C.border }]}>
        <HamburgerButton />
        <Text style={[styles.headerTitle, { color: C.textPrimary }]}>Alert Settings</Text>
        <View style={{ width: 44 }} />
      </View>

      {hasFavs && (
        <TouchableOpacity
          style={[styles.pauseAllBtn, allPaused && styles.pauseAllBtnResume]}
          onPress={() => pauseAllFavorites(!allPaused)}
        >
          <Ionicons name={allPaused ? 'play-circle' : 'pause-circle'} size={20} color="#fff" />
          <Text style={styles.pauseAllBtnText}>
            {allPaused ? 'Resume All Favorites' : 'Pause All Favorites'}
          </Text>
        </TouchableOpacity>
      )}

      {/* ── Tab bar ──────────────────────────────────────────────────── */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.favTabScroll}
        contentContainerStyle={styles.favTabPills}
      >
        {FAV_TABS.map(tab => (
          <FavPillButton
            key={tab.id}
            tab={tab}
            selected={activeTab === tab.id}
            onPress={() => setActiveTab(tab.id)}
            C={C}
            styles={styles}
          />
        ))}
      </ScrollView>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingBottom: 48 }}
        keyboardShouldPersistTaps="handled"
      >

        {/* ══════════════════════════════════════════════════════════════
            BASKETBALL TEAMS TAB
        ══════════════════════════════════════════════════════════════ */}
        {activeTab === 'basketball' && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>{t('settings.favTeams')}</Text>
            <Text style={styles.sectionDesc}>{t('settings.favTeamsSub')}</Text>

            <TouchableOpacity style={styles.addRowBtn} onPress={openTeamModal}>
              <Ionicons name="add-circle-outline" size={20} color={C.accentBlue} />
              <Text style={styles.addRowBtnText}>{t('settings.addTeam')}</Text>
            </TouchableOpacity>

            {(settings?.FavoriteTeams ?? []).length === 0 ? (
              <Text style={styles.emptyHint}>{t('settings.noFavTeams')}</Text>
            ) : (
              settings!.FavoriteTeams.map(team => {
                const edit = teamEdits[team.team_id] ?? { max_diff: team.max_diff, start_quarter: String(team.start_quarter), alert_type: team.alert_type, broadcast_only: team.broadcast_only ?? false, notify_at_start: team.notify_at_start ?? false };
                return (
                  <View key={team.team_id} style={styles.favCard}>
                    <View style={styles.favCardHeader}>
                      <BballTeamLogo team={allTeams.find(t => t.id === team.team_id)} logoMap={bbLogoMap} size={36} />
                      <Text style={styles.favName}>{isHebrew ? teamFullToHe(team.team_name) : team.team_name}</Text>
                      <TouchableOpacity onPress={() => togglePauseTeam(team.team_id, team.paused ?? false)} style={[styles.pauseBtn, team.paused ? styles.pauseBtnOff : styles.pauseBtnOn]}>
                        <Text style={[styles.pauseBtnText, { color: team.paused ? C.textMuted : C.liveGreen }]}>{team.paused ? 'OFF' : 'ON'}</Text>
                      </TouchableOpacity>
                      <TouchableOpacity onPress={() => removeTeam(team.team_id)} style={styles.removeBtn}>
                        <Text style={styles.removeBtnText}>{t('settings.remove')}</Text>
                      </TouchableOpacity>
                    </View>

                    <View style={styles.noCondRow}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.inputLabel}>{t('alarm.anyScore')}</Text>
                        <Text style={styles.noCondSub}>{t('alarm.anyScoreSub')}</Text>
                      </View>
                      <BasketballToggle
                        value={edit.notify_at_start}
                        onValueChange={v => setTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, notify_at_start: v } }))}
                      />
                    </View>

                    {!edit.notify_at_start && (
                      <>
                        <View style={styles.sliderRow}>
                          <Text style={styles.inputLabel}>{t('settings.maxDiff')}</Text>
                          <Text style={styles.sliderValue}>{edit.max_diff}</Text>
                        </View>
                        <Slider
                          style={styles.slider}
                          minimumValue={1} maximumValue={25} step={1}
                          value={edit.max_diff}
                          onValueChange={v => setTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, max_diff: v } }))}
                          minimumTrackTintColor={C.orange} maximumTrackTintColor={C.border} thumbTintColor={C.orange}
                        />
                      </>
                    )}

                    <Text style={styles.inputLabel}>{t('settings.startFrom')}</Text>
                    <QRow value={edit.start_quarter} onChange={q => setTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, start_quarter: q } }))} styles={styles} />

                    <View style={styles.alertTypeRow}>
                      {(['alarm', 'silent'] as const).map(type => (
                        <TouchableOpacity key={type} style={[styles.alertTypeBtn, (edit.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]} onPress={() => setTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, alert_type: type } }))}>
                          <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                          <Text style={[styles.alertTypeText, (edit.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>{type === 'alarm' ? t('settings.alarm') : t('settings.silent')}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>

                    <View style={[styles.condCard, { marginTop: 8 }]}>
                      <View style={styles.condCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.condCardTitle}>{t('settings.broadcastOnly')}</Text>
                          <Text style={styles.condCardSub}>{t('settings.broadcastOnlySub')}</Text>
                        </View>
                        <BasketballToggle value={edit.broadcast_only} onValueChange={v => setTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, broadcast_only: v } }))} />
                      </View>
                    </View>

                    <TouchableOpacity style={styles.updateBtn} onPress={() => updateExistingTeam(team)} disabled={saving}>
                      <Text style={[styles.updateBtnText, savedId === team.team_id && styles.savedBtnText]}>
                        {savedId === team.team_id ? t('settings.saved') : t('settings.update')}
                      </Text>
                    </TouchableOpacity>
                  </View>
                );
              })
            )}
          </View>
        )}

        {/* ══════════════════════════════════════════════════════════════
            BASKETBALL PLAYERS (same tab as bbTeams)
        ══════════════════════════════════════════════════════════════ */}
        {activeTab === 'basketball' && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>{t('settings.favPlayers')}</Text>
            <Text style={styles.sectionDesc}>{t('settings.favPlayersSub')}</Text>

            <TouchableOpacity style={styles.addRowBtn} onPress={openPlayerModal}>
              <Ionicons name="add-circle-outline" size={20} color={C.accentBlue} />
              <Text style={styles.addRowBtnText}>{t('settings.addPlayer')}</Text>
            </TouchableOpacity>

            {Object.keys(settings?.FavoritePlayers ?? {}).length === 0 ? (
              <Text style={styles.emptyHint}>{t('settings.noFavPlayers')}</Text>
            ) : (
              Object.entries(settings!.FavoritePlayers).map(([pid, p]) => {
                const edit = playerEdits[pid] ?? {
                  totalPtsEnabled: p.total_pts     != null,
                  total_pts:       p.total_pts     ?? 40,
                  hotStartEnabled: p.early_pts != null && p.early_pts_q != null,
                  early_pts_q:     String(p.early_pts_q   ?? 1),
                  early_pts:       p.early_pts ?? 15,
                  td_watch:        p.td_watch ?? false,
                  alert_type:      p.alert_type ?? 'silent',
                  broadcast_only:  p.broadcast_only ?? false,
                };
                const setEdit = (patch: Partial<typeof edit>) =>
                  setPlayerEdits(prev => ({ ...prev, [pid]: { ...edit, ...patch } }));

                return (
                  <View key={pid} style={styles.favCard}>
                    <View style={styles.favCardHeader}>
                      <Text style={styles.favName}>{p.name}</Text>
                      <TouchableOpacity onPress={() => togglePausePlayer(pid, p.paused ?? false)} style={[styles.pauseBtn, p.paused ? styles.pauseBtnOff : styles.pauseBtnOn]}>
                        <Text style={[styles.pauseBtnText, { color: p.paused ? C.textMuted : C.liveGreen }]}>{p.paused ? 'OFF' : 'ON'}</Text>
                      </TouchableOpacity>
                      <TouchableOpacity onPress={() => removePlayer(pid)} style={styles.removeBtn}>
                        <Text style={styles.removeBtnText}>{t('settings.remove')}</Text>
                      </TouchableOpacity>
                    </View>

                    <View style={styles.condCard}>
                      <View style={styles.condCardHeader}>
                        <View style={{ flex: 1 }}><Text style={styles.condCardTitle}>{t('settings.totalPtsAlert')}</Text></View>
                        <BasketballToggle value={edit.totalPtsEnabled} onValueChange={v => setEdit({ totalPtsEnabled: v })} />
                      </View>
                      {edit.totalPtsEnabled && (
                        <>
                          <View style={[styles.sliderRow, { marginTop: 10 }]}>
                            <Text style={styles.inputLabel}>{t('settings.ptsThreshold')}</Text>
                            <Text style={styles.sliderValue}>{edit.total_pts}</Text>
                          </View>
                          <Slider style={styles.slider} minimumValue={1} maximumValue={80} step={1} value={edit.total_pts} onValueChange={v => setEdit({ total_pts: v })} minimumTrackTintColor={C.orange} maximumTrackTintColor={C.border} thumbTintColor={C.orange} />
                        </>
                      )}
                    </View>

                    <View style={styles.condCard}>
                      <View style={styles.condCardHeader}>
                        <View style={{ flex: 1 }}><Text style={styles.condCardTitle}>{t('settings.earlyPtsAlert')}</Text></View>
                        <BasketballToggle value={edit.hotStartEnabled} onValueChange={v => setEdit({ hotStartEnabled: v })} />
                      </View>
                      {edit.hotStartEnabled && (
                        <>
                          <View style={[styles.sliderRow, { marginTop: 10 }]}>
                            <Text style={styles.inputLabel}>{t('settings.ptsThreshold')}</Text>
                            <Text style={styles.sliderValue}>{edit.early_pts}</Text>
                          </View>
                          <Slider style={styles.slider} minimumValue={1} maximumValue={80} step={1} value={edit.early_pts} onValueChange={v => setEdit({ early_pts: v })} minimumTrackTintColor={C.orange} maximumTrackTintColor={C.border} thumbTintColor={C.orange} />
                          <Text style={styles.inputLabel}>{t('settings.byEndOfQ')}</Text>
                          <QRow value={edit.early_pts_q} onChange={q => setEdit({ early_pts_q: q })} quarters={['1', '2', '3']} styles={styles} />
                        </>
                      )}
                    </View>

                    <View style={styles.condCard}>
                      <View style={styles.condCardHeader}>
                        <View style={{ flex: 1 }}><Text style={styles.condCardTitle}>{t('settings.tripleDouble')}</Text></View>
                        <BasketballToggle value={edit.td_watch} onValueChange={v => setEdit({ td_watch: v })} />
                      </View>
                    </View>

                    <View style={styles.condCard}>
                      <View style={styles.condCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.condCardTitle}>{t('settings.broadcastOnly')}</Text>
                          <Text style={styles.condCardSub}>{t('settings.broadcastOnlySub')}</Text>
                        </View>
                        <BasketballToggle value={edit.broadcast_only} onValueChange={v => setEdit({ broadcast_only: v })} />
                      </View>
                    </View>

                    <View style={styles.alertTypeRow}>
                      {(['alarm', 'silent'] as const).map(type => (
                        <TouchableOpacity key={type} style={[styles.alertTypeBtn, (edit.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]} onPress={() => setEdit({ alert_type: type })}>
                          <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                          <Text style={[styles.alertTypeText, (edit.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>{type === 'alarm' ? t('settings.alarm') : t('settings.silent')}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>

                    <TouchableOpacity style={[styles.updateBtn, { marginTop: 4 }]} onPress={() => savePlayerSettings(pid, p.name, edit.alert_type ?? p.alert_type)} disabled={saving}>
                      <Text style={[styles.updateBtnText, savedId === pid && styles.savedBtnText]}>
                        {savedId === pid ? t('settings.saved') : t('settings.update')}
                      </Text>
                    </TouchableOpacity>
                  </View>
                );
              })
            )}
          </View>
        )}

        {/* ══════════════════════════════════════════════════════════════
            SOCCER TEAMS TAB
        ══════════════════════════════════════════════════════════════ */}
        {activeTab === 'football' && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>{t('soccer.favorites.teams')}</Text>
            <Text style={styles.sectionDesc}>
              Persistent drama monitors — notified whenever a followed team plays a high-drama match.
            </Text>

            <TouchableOpacity style={styles.addRowBtn} onPress={openSoccerTeamModal}>
              <Ionicons name="add-circle-outline" size={20} color={C.accentBlue} />
              <Text style={styles.addRowBtnText}>Add Soccer Team</Text>
            </TouchableOpacity>

            {!soccerFavs || soccerFavs.teams.length === 0 ? (
              <Text style={styles.emptyHint}>{t('soccer.favorites.noTeams')}</Text>
            ) : (
              soccerFavs.teams.map(team => {
                const edit = soccerTeamEdits[team.team_id] ?? { drama_threshold: team.drama_threshold, alert_type: team.alert_type, paused: team.paused, notify_at_kickoff: team.notify_at_kickoff ?? false };
                return (
                  <View key={team.team_id} style={styles.favCard}>
                    <View style={styles.favCardHeader}>
                      <SoccerTeamLogo
                        teamId={team.team_id}
                        teamIds={team.team_ids}
                        name={team.team_name ?? team.team_id}
                        crestMap={teamCrestMap}
                        size={36}
                      />
                      <Text style={styles.favName}>{team.team_name ?? team.team_id}</Text>
                      <TouchableOpacity onPress={() => togglePauseSoccerTeam(team.team_id)} style={[styles.pauseBtn, edit.paused ? styles.pauseBtnOff : styles.pauseBtnOn]}>
                        <Text style={[styles.pauseBtnText, { color: edit.paused ? C.textMuted : C.liveGreen }]}>{edit.paused ? 'OFF' : 'ON'}</Text>
                      </TouchableOpacity>
                      <TouchableOpacity onPress={() => removeSoccerTeam(team.team_id)} style={styles.removeBtn}>
                        <Text style={styles.removeBtnText}>{t('settings.remove')}</Text>
                      </TouchableOpacity>
                    </View>

                    <View style={styles.sliderRow}>
                      <Text style={styles.inputLabel}>DRAMA THRESHOLD</Text>
                      <Text style={styles.sliderValue}>{edit.drama_threshold.toFixed(1)}</Text>
                    </View>
                    <Slider
                      style={styles.slider}
                      minimumValue={5} maximumValue={9.5} step={0.5}
                      value={edit.drama_threshold}
                      onValueChange={v => setSoccerTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, drama_threshold: v } }))}
                      minimumTrackTintColor={C.orange} maximumTrackTintColor={C.border} thumbTintColor={C.orange}
                    />

                    <View style={styles.noCondRow}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.inputLabel}>NOTIFY AT KICKOFF</Text>
                      </View>
                      <BasketballToggle
                        value={edit.notify_at_kickoff}
                        onValueChange={v => setSoccerTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, notify_at_kickoff: v } }))}
                      />
                    </View>

                    <View style={styles.alertTypeRow}>
                      {(['alarm', 'silent'] as const).map(type => (
                        <TouchableOpacity key={type} style={[styles.alertTypeBtn, edit.alert_type === type && styles.alertTypeBtnActive]} onPress={() => setSoccerTeamEdits(prev => ({ ...prev, [team.team_id]: { ...edit, alert_type: type } }))}>
                          <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                          <Text style={[styles.alertTypeText, edit.alert_type === type && styles.alertTypeTextActive]}>{type === 'alarm' ? t('settings.alarm') : t('settings.silent')}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>

                    <TouchableOpacity style={styles.updateBtn} onPress={() => saveSoccerTeam(team.team_id)} disabled={saving}>
                      <Text style={[styles.updateBtnText, savedId === `s-${team.team_id}` && styles.savedBtnText]}>
                        {savedId === `s-${team.team_id}` ? t('settings.saved') : t('settings.update')}
                      </Text>
                    </TouchableOpacity>
                  </View>
                );
              })
            )}
          </View>
        )}

        {/* ══════════════════════════════════════════════════════════════
            SOCCER PLAYERS (same tab as soccerTeams)
        ══════════════════════════════════════════════════════════════ */}
        {activeTab === 'football' && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>{t('soccer.favorites.players')}</Text>
            <Text style={styles.sectionDesc}>
              Persistent player monitors — notified when a followed player scores or gets carded.
            </Text>

            <TouchableOpacity style={styles.addRowBtn} onPress={openSoccerPlayerModal}>
              <Ionicons name="add-circle-outline" size={20} color={C.accentBlue} />
              <Text style={styles.addRowBtnText}>Add Soccer Player</Text>
            </TouchableOpacity>

            {!soccerFavs || soccerFavs.players.length === 0 ? (
              <Text style={styles.emptyHint}>{t('soccer.favorites.noPlayers')}</Text>
            ) : (
              soccerFavs.players.map(player => (
                <View key={player.player_id} style={styles.favCard}>
                  <View style={styles.favCardHeader}>
                    <View style={styles.playerInitialBadge}>
                      <Ionicons name="person" size={18} color={C.textMuted} />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.favName}>{player.player_name ?? player.player_id}</Text>
                      {player.team_id && <Text style={styles.pickerRowSub}>{player.team_id}</Text>}
                    </View>
                    <View style={[styles.alertTypePill, { borderColor: player.alert_type === 'alarm' ? C.red : C.border }]}>
                      <Ionicons
                        name={player.alert_type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'}
                        size={15}
                        color={player.alert_type === 'alarm' ? C.red : C.textMuted}
                      />
                    </View>
                    <TouchableOpacity onPress={() => removeSoccerPlayer(player.player_id)} style={styles.removeBtn}>
                      <Text style={styles.removeBtnText}>{t('settings.remove')}</Text>
                    </TouchableOpacity>
                  </View>
                </View>
              ))
            )}
          </View>
        )}

        {/* ══════════════════════════════════════════════════════════════
            RADAR TAB
        ══════════════════════════════════════════════════════════════ */}
        {activeTab === 'radar' && (<>

          {/* ── Basketball Radar ──────────────────────────────────────── */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>🏀 BASKETBALL</Text>
            <Text style={styles.sectionDesc}>{t('settings.radarSub')}</Text>

            <View style={styles.switchRow}>
              <Text style={styles.rowLabel}>{t('settings.enableRadar')}</Text>
              <BasketballToggle value={radar?.enabled ?? false} onValueChange={v => { signal('Radar.Toggled', { enabled: String(v), mode: radar?.alert_type ?? 'silent', maxDiff: String(radar?.max_diff ?? 5), leagueCount: String(radar?.leagues?.length ?? 1) }); patchGlobalRadar({ enabled: v }); }} />
            </View>

            <View style={[styles.alertTypeRow, !settings && { opacity: 0.4 }]}>
              {(['alarm', 'silent'] as const).map(type => (
                <TouchableOpacity key={type} activeOpacity={1} disabled={!settings} style={[styles.alertTypeBtn, (radar?.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]} onPress={() => patchGlobalRadar({ alert_type: type })}>
                  <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                  <Text style={[styles.alertTypeText, (radar?.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>{type === 'alarm' ? t('settings.alarm') : t('settings.silent')}</Text>
                </TouchableOpacity>
              ))}
            </View>

            {radar?.enabled && (
              <>
                <Text style={[styles.inputLabel, { marginTop: 12 }]}>LEAGUES</Text>
                <View style={styles.radarLeagueRow}>
                  {LEAGUES.map(lg => {
                    const active = radarLeagues.includes(lg.id);
                    return (
                      <TouchableOpacity key={lg.id} style={[styles.radarLeaguePill, active && styles.radarLeaguePillActive]} onPress={() => { if (active && radarLeagues.length === 1) return; const next = active ? radarLeagues.filter(l => l !== lg.id) : [...radarLeagues, lg.id]; setRadarLeagues(next); patchGlobalRadar({ leagues: next }); }}>
                        {active && <Ionicons name="checkmark" size={13} color={C.orange} />}
                        <Text style={[styles.radarLeaguePillText, active && styles.radarLeaguePillTextActive]}>{lg.label}</Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
                <View style={styles.sliderRow}>
                  <Text style={styles.inputLabel}>{t('settings.maxDiff')}</Text>
                  <Text style={styles.sliderValue}>{radarMaxDiff}</Text>
                </View>
                <Slider style={styles.slider} minimumValue={1} maximumValue={25} step={1} value={Number(radarMaxDiff)} onValueChange={v => setRadarMaxDiff(String(v))} minimumTrackTintColor={C.orange} maximumTrackTintColor={C.border} thumbTintColor={C.orange} />
                <Text style={styles.inputLabel}>{t('settings.startFrom')}</Text>
                <QRow value={radarStartQ} onChange={setRadarStartQ} styles={styles} />
                <TouchableOpacity style={styles.updateBtn} onPress={() => patchGlobalRadar({ max_diff: parseInt(radarMaxDiff, 10) || 5, start_quarter: parseInt(radarStartQ, 10) || 4, leagues: radarLeagues })} disabled={saving}>
                  <Text style={[styles.updateBtnText, savedId === 'radar' && styles.savedBtnText]}>
                    {saving ? t('settings.saving') : savedId === 'radar' ? t('settings.saved') : t('settings.saveRadar')}
                  </Text>
                </TouchableOpacity>
              </>
            )}

            <View style={[styles.switchRow, { marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderColor: C.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={styles.rowLabel}>{t('settings.radarStatus')}</Text>
                <Text style={styles.rowSub}>{radarRunning ? t('settings.radarRunning') : t('settings.radarStopped')}</Text>
              </View>
              <View style={[styles.statusDot, { backgroundColor: radarRunning ? C.liveGreen : C.border }]} />
            </View>
          </View>

          {/* ── Football Radar ────────────────────────────────────────── */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>⚽ FOOTBALL</Text>
            <Text style={styles.sectionDesc}>
              Receive alerts when any match in these leagues reaches your drama threshold.{'\n'}
              To follow a league for display only, tap ★ in the Sports tab.
            </Text>

            <TouchableOpacity style={styles.addRowBtn} onPress={() => setLeaguePickerVisible(true)}>
              <Ionicons name="add-circle-outline" size={20} color={C.accentBlue} />
              <Text style={styles.addRowBtnText}>Add League Monitor</Text>
            </TouchableOpacity>

            {soccerFavs && soccerFavs.competitions.map(comp => {
                const info = LeagueCatalog.info(comp.competition_id);
                return (
                  <View key={comp.competition_id} style={styles.leagueRow}>
                    <LeagueBadge leagueId={comp.competition_id} size={36} />
                    <View style={{ flex: 1 }}>
                      <Text style={styles.favName}>{info.label}</Text>
                      <Text style={styles.pickerRowSub}>Drama ≥ {comp.drama_threshold.toFixed(1)}</Text>
                    </View>
                    <TouchableOpacity
                      onPress={async () => {
                        if (!userToken) return;
                        await deleteFavCompetition(comp.competition_id, userToken).catch(() => {});
                        setSoccerFavs(prev => prev ? { ...prev, competitions: prev.competitions.filter(c => c.competition_id !== comp.competition_id) } : prev);
                      }}
                      style={styles.removeBtn}
                    >
                      <Text style={styles.removeBtnText}>{t('settings.remove')}</Text>
                    </TouchableOpacity>
                  </View>
                );
              })}
          </View>
        </>)}

        {/* ══════════════════════════════════════════════════════════════
            HISTORICAL MODE TAB
        ══════════════════════════════════════════════════════════════ */}
        {activeTab === 'historical' && (<>

          {/* ── Basketball Historical ─────────────────────────────────── */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>🏀 BASKETBALL</Text>
            <Text style={styles.sectionDesc}>{t('settings.historicalSub')}</Text>

            <View style={styles.switchRow}>
              <Text style={styles.rowLabel}>{t('settings.enableHistorical')}</Text>
              <BasketballToggle value={hist?.enabled ?? false} onValueChange={v => { signal('Historical.Toggled', { enabled: String(v), mode: (hist as any)?.alert_type ?? 'silent', scoring_40_half: String(hist?.categories?.scoring_40_half), threes_11: String(hist?.categories?.threes_11), quad_double: String(hist?.categories?.quad_double_threat) }); patchHistorical({ enabled: v }); }} />
            </View>

            <View style={[styles.alertTypeRow, !settings && { opacity: 0.4 }]}>
              {(['alarm', 'silent'] as const).map(type => (
                <TouchableOpacity key={type} activeOpacity={1} disabled={!settings} style={[styles.alertTypeBtn, ((hist as any)?.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]} onPress={() => patchHistorical({ alert_type: type })}>
                  <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                  <Text style={[styles.alertTypeText, ((hist as any)?.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>{type === 'alarm' ? t('settings.alarm') : t('settings.silent')}</Text>
                </TouchableOpacity>
              ))}
            </View>

            {hist?.enabled && (
              ([
                { key: 'scoring_40_half'    as const, label: t('settings.scoring40'),  sub: t('settings.scoring40Sub') },
                { key: 'threes_11'          as const, label: t('settings.threes11'),    sub: t('settings.threes11Sub') },
                { key: 'quad_double_threat' as const, label: t('settings.quadDouble'),  sub: t('settings.quadDoubleSub') },
              ] as const).map(({ key, label, sub }) => (
                <View key={key} style={styles.categoryRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowLabel}>{label}</Text>
                    <Text style={styles.rowSub}>{sub}</Text>
                  </View>
                  <BasketballToggle value={histCats?.[key] ?? false} onValueChange={v => patchHistorical({ categories: { [key]: v } })} />
                </View>
              ))
            )}
          </View>

          {/* ── Football Historical ───────────────────────────────────── */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>⚽ FOOTBALL</Text>
            <View style={styles.emptyState}>
              <Ionicons name="time-outline" size={28} color={C.border} />
              <Text style={styles.emptyHint}>Coming soon</Text>
            </View>
          </View>
        </>)}

      </ScrollView>

      {/* ═══════════════════════════════════════════════════════════════
          ADD TEAM MODAL
      ═══════════════════════════════════════════════════════════════ */}
      <Modal visible={teamModalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet, { paddingBottom: insets.bottom }]}>
            <View style={styles.modalHeader}>
              {pickedTeam ? (
                <TouchableOpacity onPress={() => setPickedTeam(null)} style={styles.backBtn}>
                  <Ionicons name="arrow-back" size={20} color={C.accentBlue} />
                  <Text style={styles.backBtnText}>Teams</Text>
                </TouchableOpacity>
              ) : <View style={{ width: 70 }} />}
              <Text style={styles.modalTitle}>
                {pickedTeam ? pickedTeam.abbreviation : t('settings.selectTeam')}
              </Text>
              <TouchableOpacity onPress={closeTeamModal} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

            {!pickedTeam && (
              <>
                {/* Search bar */}
                <View style={styles.searchBar}>
                  <Ionicons name="search" size={16} color={C.textMuted} />
                  <TextInput
                    style={styles.searchInput}
                    placeholder="Search team..."
                    placeholderTextColor={C.textMuted}
                    value={pickerSearch}
                    onChangeText={setPickerSearch}
                    autoCorrect={false}
                    autoCapitalize="none"
                  />
                  {pickerSearch.length > 0 && (
                    <TouchableOpacity onPress={() => setPickerSearch('')}>
                      <Ionicons name="close-circle" size={16} color={C.textMuted} />
                    </TouchableOpacity>
                  )}
                </View>

                {/* League pills */}
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.pickerLeagueScroll} contentContainerStyle={styles.pickerLeaguePills}>
                  {LEAGUES.map(lg => (
                    <TouchableOpacity
                      key={lg.id}
                      style={[styles.pickerLeaguePill, pickerLeague === lg.id && styles.pickerLeaguePillActive]}
                      onPress={() => { setPickerLeague(lg.id); setPickerSearch(''); }}
                    >
                      <Text style={[styles.pickerLeaguePillText, pickerLeague === lg.id && styles.pickerLeaguePillTextActive]}>
                        {lg.label}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>

                <FlatList
                  data={allTeams.filter(tm => {
                    const matchLeague = pickerSearch.length > 0 || tm.league === pickerLeague;
                    const matchSearch = pickerSearch.length === 0 ||
                      tm.name.toLowerCase().includes(pickerSearch.toLowerCase()) ||
                      tm.abbreviation.toLowerCase().includes(pickerSearch.toLowerCase());
                    return matchLeague && matchSearch;
                  })}
                  keyExtractor={item => item.id}
                  initialNumToRender={15} maxToRenderPerBatch={10} windowSize={5}
                  renderItem={({ item: team }) => {
                    const isFav = favTeamIds.has(team.id);
                    return (
                      <TouchableOpacity style={styles.pickerRow} onPress={() => setPickedTeam(team)}>
                        <BballTeamLogo team={team} logoMap={bbLogoMap} size={28} />
                        <Text style={styles.pickerRowText}>{isHebrew ? teamFullToHe(team.name) : team.name}</Text>
                        <View style={styles.pickerRowRight}>
                          {isFav && <Text style={styles.savedBadge}>{t('settings.savedBadge')}</Text>}
                          <Text style={styles.pickerRowAbbr}>{team.abbreviation}</Text>
                          <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                        </View>
                      </TouchableOpacity>
                    );
                  }}
                  ItemSeparatorComponent={ListSeparator}
                  contentContainerStyle={{ paddingBottom: 24 }}
                />
              </>
            )}

            {pickedTeam && (
              <ScrollView keyboardShouldPersistTaps="handled" style={{ flex: 1 }}>
                <View style={styles.configArea}>
                  <View style={styles.configTitleRow}>
                    <BballTeamLogo team={pickedTeam} logoMap={bbLogoMap} size={36} />
                    <Text style={styles.configTitle}>{isHebrew ? teamFullToHe(pickedTeam.name) : pickedTeam.name}</Text>
                  </View>
                  <Text style={styles.configSub}>{t('settings.configSub')}</Text>

                  <View style={styles.noCondRow}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.inputLabel}>{t('alarm.anyScore')}</Text>
                      <Text style={styles.noCondSub}>{t('alarm.anyScoreSub')}</Text>
                    </View>
                    <BasketballToggle value={newTeamNoCondition} onValueChange={setNewTeamNoCondition} />
                  </View>

                  {!newTeamNoCondition && (
                    <>
                      <View style={styles.sliderRow}>
                        <Text style={styles.inputLabel}>{t('settings.maxDiff')}</Text>
                        <Text style={styles.sliderValue}>{newTeamMaxDiff}</Text>
                      </View>
                      <Slider
                        style={styles.slider}
                        minimumValue={1} maximumValue={25} step={1}
                        value={newTeamMaxDiff} onValueChange={setNewTeamMaxDiff}
                        minimumTrackTintColor={C.orange} maximumTrackTintColor={C.border} thumbTintColor={C.orange}
                      />
                    </>
                  )}

                  <Text style={styles.inputLabel}>{t('settings.startFrom')}</Text>
                  <QRow value={newTeamStartQ} onChange={setNewTeamStartQ} styles={styles} />

                  <View style={styles.alertTypeRow}>
                    {(['alarm', 'silent'] as const).map(type => (
                      <TouchableOpacity
                        key={type}
                        style={[styles.alertTypeBtn, newTeamAlertType === type && styles.alertTypeBtnActive]}
                        onPress={() => setNewTeamAlertType(type)}
                      >
                        <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                        <Text style={[styles.alertTypeText, newTeamAlertType === type && styles.alertTypeTextActive]}>
                          {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  <View style={[styles.condCard, { marginTop: 8 }]}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.broadcastOnly')}</Text>
                        <Text style={styles.condCardSub}>{t('settings.broadcastOnlySub')}</Text>
                      </View>
                      <BasketballToggle value={newTeamBroadcastOnly} onValueChange={setNewTeamBroadcastOnly} />
                    </View>
                  </View>

                  <View style={styles.configSummary}>
                    <Ionicons name="information-circle-outline" size={14} color={C.textMuted} />
                    <Text style={styles.configSummaryText}>
                      {newTeamNoCondition
                        ? `Alert fires at the start of ${qLabel(newTeamStartQ)}, any score`
                        : `Alert fires when diff ≤ ${newTeamMaxDiff || 5} pts from ${qLabel(newTeamStartQ)}`}
                    </Text>
                  </View>

                  <TouchableOpacity
                    style={[styles.confirmBtn, saving && styles.confirmBtnDisabled]}
                    onPress={confirmAddTeam} disabled={saving}
                  >
                    <Text style={styles.confirmBtnText}>
                      {saving ? t('settings.saving') : t('settings.addTeamBtn', isHebrew ? teamFullToHe(pickedTeam.name) : pickedTeam.abbreviation)}
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.cancelBtn} onPress={closeTeamModal}>
                    <Text style={styles.cancelBtnText}>{t('settings.cancel')}</Text>
                  </TouchableOpacity>
                </View>
              </ScrollView>
            )}
          </View>
        </View>
      </Modal>

      {/* ═══════════════════════════════════════════════════════════════
          ADD PLAYER MODAL
      ═══════════════════════════════════════════════════════════════ */}
      <Modal visible={playerModalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet, { paddingBottom: insets.bottom }]}>
            <View style={styles.modalHeader}>
              {(pickedTeamForPlayer || pickedPlayer) ? (
                <TouchableOpacity
                  onPress={() => pickedPlayer ? setPickedPlayer(null) : setPickedTeamForPlayer(null)}
                  style={styles.backBtn}
                >
                  <Ionicons name="arrow-back" size={20} color={C.accentBlue} />
                  <Text style={styles.backBtnText}>
                    {pickedPlayer ? pickedTeamForPlayer?.abbreviation : 'Teams'}
                  </Text>
                </TouchableOpacity>
              ) : <View style={{ width: 70 }} />}
              <Text style={styles.modalTitle}>
                {pickedPlayer
                  ? t('settings.configureAlert')
                  : pickedTeamForPlayer
                    ? `${pickedTeamForPlayer.abbreviation} Roster`
                    : t('settings.selectTeam')}
              </Text>
              <TouchableOpacity onPress={closePlayerModal} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

            {!pickedTeamForPlayer && (
              <>
                {/* Search bar */}
                <View style={styles.searchBar}>
                  <Ionicons name="search" size={16} color={C.textMuted} />
                  <TextInput
                    style={styles.searchInput}
                    placeholder="Search player..."
                    placeholderTextColor={C.textMuted}
                    value={pickerSearch}
                    onChangeText={setPickerSearch}
                    autoCorrect={false}
                    autoCapitalize="none"
                  />
                  {pickerSearch.length > 0 && (
                    <TouchableOpacity onPress={() => setPickerSearch('')}>
                      <Ionicons name="close-circle" size={16} color={C.textMuted} />
                    </TouchableOpacity>
                  )}
                </View>

                {pickerSearch.length > 0 ? (
                  /* Player search results across all teams */
                  <FlatList
                    data={allTeams.flatMap(tm =>
                      tm.players
                        .filter(p => p.name.toLowerCase().includes(pickerSearch.toLowerCase()))
                        .map(p => ({ ...p, team: tm }))
                    )}
                    keyExtractor={item => `${item.team.id}-${item.id}`}
                    initialNumToRender={20} maxToRenderPerBatch={15} windowSize={5}
                    renderItem={({ item: p }) => {
                      const isFav = favPlayerIds.has(p.id);
                      return (
                        <TouchableOpacity style={styles.pickerRow} onPress={() => {
                          setPickedTeamForPlayer(p.team);
                          setPickedPlayer(p);
                        }}>
                          <BballTeamLogo team={p.team} logoMap={bbLogoMap} size={28} />
                          <View style={{ flex: 1 }}>
                            <Text style={styles.pickerRowText}>{p.name}</Text>
                            <Text style={styles.pickerRowSub}>
                              {isHebrew ? teamFullToHe(p.team.name) : p.team.name}
                              {p.jersey ? `  ·  #${p.jersey}` : ''}
                              {p.position ? `  ·  ${p.position}` : ''}
                            </Text>
                          </View>
                          <View style={styles.pickerRowRight}>
                            {isFav && <Text style={styles.savedBadge}>★</Text>}
                            <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                          </View>
                        </TouchableOpacity>
                      );
                    }}
                    ItemSeparatorComponent={ListSeparator}
                    contentContainerStyle={{ paddingBottom: 24 }}
                  />
                ) : (
                  <>
                    {/* League pills */}
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.pickerLeagueScroll} contentContainerStyle={styles.pickerLeaguePills}>
                      {LEAGUES.map(lg => (
                        <TouchableOpacity
                          key={lg.id}
                          style={[styles.pickerLeaguePill, pickerLeague === lg.id && styles.pickerLeaguePillActive]}
                          onPress={() => setPickerLeague(lg.id)}
                        >
                          <Text style={[styles.pickerLeaguePillText, pickerLeague === lg.id && styles.pickerLeaguePillTextActive]}>
                            {lg.label}
                          </Text>
                        </TouchableOpacity>
                      ))}
                    </ScrollView>

                    {/* Team list filtered by league */}
                    <FlatList
                      data={allTeams.filter(tm => tm.league === pickerLeague)}
                      keyExtractor={item => item.id}
                      initialNumToRender={15} maxToRenderPerBatch={10} windowSize={5}
                      renderItem={({ item: team }) => (
                        <TouchableOpacity style={styles.pickerRow} onPress={() => setPickedTeamForPlayer(team)}>
                          <BballTeamLogo team={team} logoMap={bbLogoMap} size={28} />
                          <Text style={styles.pickerRowText}>{isHebrew ? teamFullToHe(team.name) : team.name}</Text>
                          <View style={styles.pickerRowRight}>
                            <Text style={styles.pickerRowAbbr}>{t('settings.players', team.players.length)}</Text>
                            <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                          </View>
                        </TouchableOpacity>
                      )}
                      ItemSeparatorComponent={ListSeparator}
                      contentContainerStyle={{ paddingBottom: 24 }}
                    />
                  </>
                )}
              </>
            )}

            {pickedTeamForPlayer && !pickedPlayer && (
              <FlatList
                data={pickedTeamForPlayer.players}
                keyExtractor={p => p.id}
                initialNumToRender={17} maxToRenderPerBatch={10} windowSize={3}
                renderItem={({ item: p }) => {
                  const isFav = favPlayerIds.has(p.id);
                  return (
                    <TouchableOpacity style={styles.pickerRow} onPress={() => setPickedPlayer(p)}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.pickerRowText}>{p.name}</Text>
                        <Text style={styles.pickerRowSub}>
                          {p.jersey ? `#${p.jersey}` : ''}{p.jersey && p.position ? '  ·  ' : ''}{p.position}
                        </Text>
                      </View>
                      <View style={styles.pickerRowRight}>
                        {isFav && <Text style={styles.savedBadge}>★</Text>}
                        <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                      </View>
                    </TouchableOpacity>
                  );
                }}
                ItemSeparatorComponent={ListSeparator}
                contentContainerStyle={{ paddingBottom: 24 }}
              />
            )}

            {pickedPlayer && (
              <ScrollView keyboardShouldPersistTaps="handled" style={{ flex: 1 }}>
                <View style={styles.configArea}>
                  <Text style={styles.configTitle}>{pickedPlayer.name}</Text>
                  <Text style={styles.configSub}>
                    {pickedTeamForPlayer?.name}
                    {pickedPlayer.jersey ? `  ·  #${pickedPlayer.jersey}` : ''}
                    {pickedPlayer.position ? `  ·  ${pickedPlayer.position}` : ''}
                  </Text>
                  <Text style={[styles.configSub, { marginBottom: 16 }]}>
                    {t('settings.playerCondSub')}
                  </Text>

                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.totalPtsAlert')}</Text>
                        <Text style={styles.condCardSub}>{t('alarm.ptsGoalSub')}</Text>
                      </View>
                      <BasketballToggle value={newTotalPtsEnabled} onValueChange={setNewTotalPtsEnabled} />
                    </View>
                    {newTotalPtsEnabled && (
                      <>
                        <View style={[styles.sliderRow, { marginTop: 10 }]}>
                          <Text style={styles.inputLabel}>{t('settings.ptsThreshold')}</Text>
                          <Text style={styles.sliderValue}>{newTotalPts}</Text>
                        </View>
                        <Slider
                          style={styles.slider}
                          minimumValue={1} maximumValue={80} step={1}
                          value={newTotalPts} onValueChange={setNewTotalPts}
                          minimumTrackTintColor={C.orange} maximumTrackTintColor={C.border} thumbTintColor={C.orange}
                        />
                      </>
                    )}
                  </View>

                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.earlyPtsAlert')}</Text>
                        <Text style={styles.condCardSub}>{t('alarm.earlyPtsSub')}</Text>
                      </View>
                      <BasketballToggle value={newHotStartEnabled} onValueChange={setNewHotStartEnabled} />
                    </View>
                    {newHotStartEnabled && (
                      <>
                        <View style={[styles.sliderRow, { marginTop: 10 }]}>
                          <Text style={styles.inputLabel}>{t('settings.ptsThreshold')}</Text>
                          <Text style={styles.sliderValue}>{newHotStartPts}</Text>
                        </View>
                        <Slider
                          style={styles.slider}
                          minimumValue={1} maximumValue={80} step={1}
                          value={newHotStartPts} onValueChange={setNewHotStartPts}
                          minimumTrackTintColor={C.orange} maximumTrackTintColor={C.border} thumbTintColor={C.orange}
                        />
                        <Text style={styles.inputLabel}>{t('settings.byEndOfQ')}</Text>
                        <QRow value={newHotStartQ} onChange={setNewHotStartQ} quarters={['1', '2', '3']} styles={styles} />
                      </>
                    )}
                  </View>

                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.tripleDouble')}</Text>
                        <Text style={styles.condCardSub}>{t('alarm.tripleDoubleSub')}</Text>
                      </View>
                      <BasketballToggle value={newTdWatch} onValueChange={setNewTdWatch} />
                    </View>
                  </View>

                  <View style={styles.condCard}>
                    <View style={styles.condCardHeader}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.condCardTitle}>{t('settings.broadcastOnly')}</Text>
                        <Text style={styles.condCardSub}>{t('settings.broadcastOnlySub')}</Text>
                      </View>
                      <BasketballToggle value={newPlayerBroadcastOnly} onValueChange={setNewPlayerBroadcastOnly} />
                    </View>
                  </View>

                  <View style={styles.alertTypeRow}>
                    {(['alarm', 'silent'] as const).map(type => (
                      <TouchableOpacity
                        key={type}
                        style={[styles.alertTypeBtn, newPlayerAlertType === type && styles.alertTypeBtnActive]}
                        onPress={() => setNewPlayerAlertType(type)}
                      >
                        <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                        <Text style={[styles.alertTypeText, newPlayerAlertType === type && styles.alertTypeTextActive]}>
                          {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  {(newTotalPtsEnabled || newHotStartEnabled || newTdWatch) && (
                    <View style={styles.configSummary}>
                      <Ionicons name="information-circle-outline" size={14} color={C.textMuted} />
                      <Text style={styles.configSummaryText}>
                        {[
                          newTotalPtsEnabled && `${newTotalPts || 40}+ pts`,
                          newHotStartEnabled && `${newHotStartPts || 15} pts by Q${newHotStartQ}`,
                          newTdWatch         && t('active.tdWatch'),
                        ].filter(Boolean).join('  ·  ')}
                      </Text>
                    </View>
                  )}

                  <TouchableOpacity
                    style={[styles.confirmBtn, saving && styles.confirmBtnDisabled]}
                    onPress={confirmAddPlayer} disabled={saving}
                  >
                    <Text style={styles.confirmBtnText}>
                      {saving ? t('settings.saving') : t('settings.addPlayerBtn', pickedPlayer.name)}
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.cancelBtn} onPress={closePlayerModal}>
                    <Text style={styles.cancelBtnText}>{t('settings.cancel')}</Text>
                  </TouchableOpacity>
                </View>
              </ScrollView>
            )}
          </View>
        </View>
      </Modal>

      {/* ── Add League Monitor Modal ──────────────────────────────────── */}
      <Modal visible={leaguePickerVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet, { paddingBottom: insets.bottom }]}>
            <View style={styles.modalHeader}>
              <View style={{ width: 70 }} />
              <Text style={styles.modalTitle}>Add League Monitor</Text>
              <TouchableOpacity onPress={() => setLeaguePickerVisible(false)} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>
            <Text style={[styles.sectionDesc, { paddingHorizontal: 16, paddingTop: 8 }]}>
              Pick a league — you'll receive alerts when any match in it crosses the drama threshold.
            </Text>
            <FlatList
              data={LeagueCatalog.footballLeagues().filter(
                lg => !soccerFavs?.competitions.some(c => c.competition_id === lg.id)
              )}
              keyExtractor={item => item.id}
              renderItem={({ item: lg }) => (
                <TouchableOpacity
                  style={styles.pickerRow}
                  onPress={async () => {
                    if (!userToken) return;
                    setLeaguePickerVisible(false);
                    await upsertFavCompetition({ userToken, competitionId: lg.id, dramaThreshold: 7.0 }).catch(() => {});
                    const favs = await getFavorites(userToken).catch(() => null);
                    if (favs) setSoccerFavs(favs);
                  }}
                >
                  <LeagueBadge leagueId={lg.id} size={32} />
                  <Text style={styles.pickerRowText}>{lg.label}</Text>
                  <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                </TouchableOpacity>
              )}
              ItemSeparatorComponent={ListSeparator}
              contentContainerStyle={{ paddingBottom: 24 }}
            />
          </View>
        </View>
      </Modal>

      {/* ═══════════════════════════════════════════════════════════════
          ADD SOCCER TEAM MODAL
      ═══════════════════════════════════════════════════════════════ */}
      <Modal visible={soccerTeamModalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet, { paddingBottom: insets.bottom }]}>
            <View style={styles.modalHeader}>
              {pickedSoccerTeam ? (
                <TouchableOpacity onPress={() => setPickedSoccerTeam(null)} style={styles.backBtn}>
                  <Ionicons name="arrow-back" size={20} color={C.accentBlue} />
                  <Text style={styles.backBtnText}>Teams</Text>
                </TouchableOpacity>
              ) : <View style={{ width: 70 }} />}
              <Text style={styles.modalTitle}>
                {pickedSoccerTeam ? pickedSoccerTeam.name : 'Add Soccer Team'}
              </Text>
              <TouchableOpacity onPress={closeSoccerTeamModal} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

            {!pickedSoccerTeam && (
              <>
                <View style={styles.searchBar}>
                  <Ionicons name="search" size={16} color={C.textMuted} />
                  <TextInput
                    style={styles.searchInput}
                    placeholder="Search team..."
                    placeholderTextColor={C.textMuted}
                    value={soccerTeamSearch}
                    onChangeText={setSoccerTeamSearch}
                    autoCorrect={false}
                    autoCapitalize="none"
                  />
                  {soccerTeamSearch.length > 0 && (
                    <TouchableOpacity onPress={() => setSoccerTeamSearch('')}>
                      <Ionicons name="close-circle" size={16} color={C.textMuted} />
                    </TouchableOpacity>
                  )}
                </View>

                {soccerTeamSearch.length < 2 && (
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.pickerLeagueScroll} contentContainerStyle={styles.pickerLeaguePills}>
                    {LeagueCatalog.footballLeagues().map(lg => (
                      <TouchableOpacity
                        key={lg.id}
                        style={[styles.pickerLeaguePill, soccerPickerLeague === lg.id && styles.pickerLeaguePillActive]}
                        onPress={() => setSoccerPickerLeague(lg.id)}
                      >
                        <Text style={[styles.pickerLeaguePillText, soccerPickerLeague === lg.id && styles.pickerLeaguePillTextActive]}>
                          {lg.label}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </ScrollView>
                )}

                {soccerTeamSearch.length >= 2 ? (
                  soccerTeamSearching ? (
                    <ActivityIndicator style={{ marginTop: 20 }} color={C.accentBlue} />
                  ) : (
                    <FlatList
                      data={soccerTeamResults}
                      keyExtractor={item => item.id}
                      initialNumToRender={15} maxToRenderPerBatch={10} windowSize={5}
                      renderItem={({ item: team }) => {
                        const isFav = soccerFavs?.teams.some(t => t.team_id === team.id);
                        return (
                          <TouchableOpacity style={styles.pickerRow} onPress={() => setPickedSoccerTeam(team)}>
                            <SoccerTeamLogo teamId={team.id} teamIds={team.ids} name={team.name} crestMap={teamCrestMap} logo={team.logo} size={28} />
                            <View style={{ flex: 1 }}>
                              <Text style={styles.pickerRowText}>{team.name}</Text>
                              {team.league && <Text style={styles.pickerRowSub}>{LeagueCatalog.info(team.league).label}</Text>}
                            </View>
                            <View style={styles.pickerRowRight}>
                              {isFav && <Text style={styles.savedBadge}>★</Text>}
                              <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                            </View>
                          </TouchableOpacity>
                        );
                      }}
                      ItemSeparatorComponent={ListSeparator}
                      contentContainerStyle={{ paddingBottom: 24 }}
                      ListEmptyComponent={<Text style={[styles.emptyHint, { padding: 16 }]}>No teams found</Text>}
                    />
                  )
                ) : leagueBrowseLoading ? (
                  <ActivityIndicator style={{ marginTop: 20 }} color={C.accentBlue} />
                ) : (
                  <FlatList
                    data={(() => { const c = soccerTeamCache.filter(tm => tm.league === soccerPickerLeague); return c.length > 0 ? c : leagueBrowseTeams; })()}
                    keyExtractor={item => item.id}
                    initialNumToRender={15} maxToRenderPerBatch={10} windowSize={5}
                    renderItem={({ item: team }) => {
                      const isFav = soccerFavs?.teams.some(t => t.team_id === team.id);
                      return (
                        <TouchableOpacity style={styles.pickerRow} onPress={() => setPickedSoccerTeam(team)}>
                          <SoccerTeamLogo teamId={team.id} teamIds={team.ids} name={team.name} crestMap={teamCrestMap} logo={team.logo} size={28} />
                          <Text style={styles.pickerRowText}>{team.name}</Text>
                          <View style={styles.pickerRowRight}>
                            {isFav && <Text style={styles.savedBadge}>★</Text>}
                            <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                          </View>
                        </TouchableOpacity>
                      );
                    }}
                    ItemSeparatorComponent={ListSeparator}
                    contentContainerStyle={{ paddingBottom: 24 }}
                    ListEmptyComponent={<Text style={[styles.emptyHint, { padding: 16 }]}>No teams found — try searching by name.</Text>}
                  />
                )}
              </>
            )}

            {pickedSoccerTeam && (
              <ScrollView keyboardShouldPersistTaps="handled" style={{ flex: 1 }}>
                <View style={styles.configArea}>
                  <View style={styles.configTitleRow}>
                    <SoccerTeamLogo teamId={pickedSoccerTeam.id} teamIds={pickedSoccerTeam.ids} name={pickedSoccerTeam.name} crestMap={teamCrestMap} size={36} />
                    <Text style={styles.configTitle}>{pickedSoccerTeam.name}</Text>
                  </View>
                  <Text style={styles.configSub}>Drama monitor — notified when this team plays a high-drama match.</Text>

                  <View style={styles.sliderRow}>
                    <Text style={styles.inputLabel}>DRAMA THRESHOLD</Text>
                    <Text style={styles.sliderValue}>{newSoccerDramaThreshold.toFixed(1)}</Text>
                  </View>
                  <Slider
                    style={styles.slider}
                    minimumValue={5} maximumValue={9.5} step={0.5}
                    value={newSoccerDramaThreshold}
                    onValueChange={setNewSoccerDramaThreshold}
                    minimumTrackTintColor={C.orange} maximumTrackTintColor={C.border} thumbTintColor={C.orange}
                  />

                  <View style={styles.noCondRow}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.inputLabel}>NOTIFY AT KICKOFF</Text>
                      <Text style={styles.noCondSub}>Also notify when the match starts</Text>
                    </View>
                    <BasketballToggle value={newSoccerNotifyKickoff} onValueChange={setNewSoccerNotifyKickoff} />
                  </View>

                  <View style={styles.alertTypeRow}>
                    {(['alarm', 'silent'] as const).map(type => (
                      <TouchableOpacity
                        key={type}
                        style={[styles.alertTypeBtn, newSoccerAlertType === type && styles.alertTypeBtnActive]}
                        onPress={() => setNewSoccerAlertType(type)}
                      >
                        <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                        <Text style={[styles.alertTypeText, newSoccerAlertType === type && styles.alertTypeTextActive]}>
                          {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  <TouchableOpacity
                    style={[styles.confirmBtn, { marginTop: 16 }, saving && styles.confirmBtnDisabled]}
                    onPress={confirmAddSoccerTeam} disabled={saving}
                  >
                    <Text style={styles.confirmBtnText}>
                      {saving ? t('settings.saving') : `Add ${pickedSoccerTeam.name}`}
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.cancelBtn} onPress={closeSoccerTeamModal}>
                    <Text style={styles.cancelBtnText}>{t('settings.cancel')}</Text>
                  </TouchableOpacity>
                </View>
              </ScrollView>
            )}
          </View>
        </View>
      </Modal>

      {/* ═══════════════════════════════════════════════════════════════
          ADD SOCCER PLAYER MODAL
      ═══════════════════════════════════════════════════════════════ */}
      <Modal visible={soccerPlayerModalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet, { paddingBottom: insets.bottom }]}>
            <View style={styles.modalHeader}>
              {pickedSoccerPlayer ? (
                <TouchableOpacity onPress={() => setPickedSoccerPlayer(null)} style={styles.backBtn}>
                  <Ionicons name="arrow-back" size={20} color={C.accentBlue} />
                  <Text style={styles.backBtnText}>Search</Text>
                </TouchableOpacity>
              ) : <View style={{ width: 70 }} />}
              <Text style={styles.modalTitle}>
                {pickedSoccerPlayer ? pickedSoccerPlayer.name : 'Add Soccer Player'}
              </Text>
              <TouchableOpacity onPress={closeSoccerPlayerModal} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

            {!pickedSoccerPlayer && (
              <>
                <View style={styles.searchBar}>
                  <Ionicons name="search" size={16} color={C.textMuted} />
                  <TextInput
                    style={styles.searchInput}
                    placeholder="Search player..."
                    placeholderTextColor={C.textMuted}
                    value={soccerPlayerSearch}
                    onChangeText={setSoccerPlayerSearch}
                    autoCorrect={false}
                    autoCapitalize="none"
                  />
                  {soccerPlayerSearch.length > 0 && (
                    <TouchableOpacity onPress={() => setSoccerPlayerSearch('')}>
                      <Ionicons name="close-circle" size={16} color={C.textMuted} />
                    </TouchableOpacity>
                  )}
                </View>

                {soccerPlayerSearch.length < 2 ? (
                  <View style={{ padding: 32, alignItems: 'center', gap: 10 }}>
                    <Ionicons name="search-outline" size={36} color={C.textMuted} />
                    <Text style={[styles.emptyHint, { textAlign: 'center' }]}>Type a player's name to search</Text>
                  </View>
                ) : soccerPlayerSearching ? (
                  <ActivityIndicator style={{ marginTop: 20 }} color={C.accentBlue} />
                ) : (
                  <FlatList
                    data={soccerPlayerResults}
                    keyExtractor={item => item.id}
                    initialNumToRender={15} maxToRenderPerBatch={10} windowSize={5}
                    renderItem={({ item: player }) => {
                      const isFav = soccerFavs?.players.some(p => p.player_id === player.id);
                      return (
                        <TouchableOpacity style={styles.pickerRow} onPress={() => setPickedSoccerPlayer(player)}>
                          <View style={styles.playerInitialBadge}>
                            <Ionicons name="person" size={16} color={C.textMuted} />
                          </View>
                          <View style={{ flex: 1 }}>
                            <Text style={styles.pickerRowText}>{player.name}</Text>
                            {(player.team_name || player.position) && (
                              <Text style={styles.pickerRowSub}>
                                {[player.team_name, player.position].filter(Boolean).join('  ·  ')}
                              </Text>
                            )}
                          </View>
                          <View style={styles.pickerRowRight}>
                            {isFav && <Text style={styles.savedBadge}>★</Text>}
                            <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                          </View>
                        </TouchableOpacity>
                      );
                    }}
                    ItemSeparatorComponent={ListSeparator}
                    contentContainerStyle={{ paddingBottom: 24 }}
                    ListEmptyComponent={<Text style={[styles.emptyHint, { padding: 16 }]}>No players found</Text>}
                  />
                )}
              </>
            )}

            {pickedSoccerPlayer && (
              <ScrollView keyboardShouldPersistTaps="handled" style={{ flex: 1 }}>
                <View style={styles.configArea}>
                  <View style={[styles.playerInitialBadge, { width: 52, height: 52, borderRadius: 26, alignSelf: 'center', marginBottom: 12 }]}>
                    <Ionicons name="person" size={28} color={C.textMuted} />
                  </View>
                  <Text style={[styles.configTitle, { textAlign: 'center' }]}>{pickedSoccerPlayer.name}</Text>
                  <Text style={[styles.configSub, { textAlign: 'center', marginBottom: 24 }]}>
                    {[pickedSoccerPlayer.team_name, pickedSoccerPlayer.position].filter(Boolean).join('  ·  ')}
                  </Text>

                  <Text style={[styles.inputLabel, { marginBottom: 8 }]}>ALERT MODE</Text>
                  <View style={styles.alertTypeRow}>
                    {(['alarm', 'silent'] as const).map(type => (
                      <TouchableOpacity
                        key={type}
                        style={[styles.alertTypeBtn, newSoccerPlayerAlertType === type && styles.alertTypeBtnActive]}
                        onPress={() => setNewSoccerPlayerAlertType(type)}
                      >
                        <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                        <Text style={[styles.alertTypeText, newSoccerPlayerAlertType === type && styles.alertTypeTextActive]}>
                          {type === 'alarm' ? t('settings.alarm') : t('settings.silent')}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  <TouchableOpacity
                    style={[styles.confirmBtn, { marginTop: 20 }, saving && styles.confirmBtnDisabled]}
                    onPress={confirmAddSoccerPlayer} disabled={saving}
                  >
                    <Text style={styles.confirmBtnText}>
                      {saving ? t('settings.saving') : `Add ${pickedSoccerPlayer.name}`}
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.cancelBtn} onPress={closeSoccerPlayerModal}>
                    <Text style={styles.cancelBtnText}>{t('settings.cancel')}</Text>
                  </TouchableOpacity>
                </View>
              </ScrollView>
            )}
          </View>
        </View>
      </Modal>

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
  container: { flex: 1 },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 12, borderBottomWidth: StyleSheet.hairlineWidth },
  headerTitle: tabHeaderTitle,
  section: {
    marginHorizontal: 16, marginTop: 14,
    backgroundColor: C.card, borderRadius: 16,
    borderWidth: 1, borderColor: C.border, padding: 16,
  },
  sectionTitle: { fontSize: 11, fontWeight: '800', color: C.textMuted, letterSpacing: 2, marginBottom: 4 },
  sectionDesc:  { fontSize: 12, color: C.textMuted, lineHeight: 17, marginBottom: 14 },
  addRowBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 10, paddingHorizontal: 12,
    backgroundColor: 'rgba(77,140,245,0.1)',
    borderRadius: 10, borderWidth: 1, borderColor: C.accentBlue, marginBottom: 12,
  },
  addRowBtnText: { color: C.accentBlue, fontWeight: '700', fontSize: 14 },
  condCard: {
    backgroundColor: C.card, borderRadius: 10,
    borderWidth: 1, borderColor: C.border, padding: 12, marginBottom: 8,
  },
  condCardHeader: { flexDirection: 'row', alignItems: 'center' },
  condCardTitle:  { fontSize: 14, fontWeight: '700', color: C.textPrimary },
  condCardSub:    { fontSize: 12, color: C.textMuted, marginTop: 2 },
  favCard: {
    backgroundColor: C.bg, borderRadius: 12,
    borderWidth: 1, borderColor: C.border, padding: 12, marginTop: 10,
  },
  favCardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  favName: { fontSize: 15, fontWeight: '800', color: C.textPrimary, flex: 1 },
  favRow:  { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.borderMuted },
  removeText: { fontSize: 13, fontWeight: '600', color: C.red },
  inputLabel: { fontSize: 10, fontWeight: '800', color: C.textMuted, letterSpacing: 1.5, marginBottom: 0, marginTop: 10 },
  noCondRow:  { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 10, marginBottom: 4 },
  noCondSub:  { fontSize: 11, color: C.textMuted, marginTop: 2, lineHeight: 15 },
  sliderRow:  { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 10 },
  sliderValue: { fontSize: 18, fontWeight: '800', color: C.orange },
  slider:      { width: '100%', height: 40, marginBottom: 4 },
  quarterRow:  { flexDirection: 'row', gap: 8, marginBottom: 12 },
  qBtn: {
    flex: 1, height: 40, borderRadius: 8,
    backgroundColor: C.card, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },
  qBtnActive:     { backgroundColor: C.red, borderColor: C.red },
  qBtnText:       { fontSize: 13, fontWeight: '700', color: C.textMuted },
  qBtnTextActive: { color: '#fff' },
  updateBtn: {
    backgroundColor: 'rgba(29,66,138,0.25)', padding: 10,
    borderRadius: 10, alignItems: 'center',
    borderWidth: 1, borderColor: C.blue,
  },
  updateBtnText: { color: C.accentBlue, fontWeight: '800', fontSize: 13 },
  savedBtnText:  { color: C.liveGreen },
  removeBtn:     { paddingHorizontal: 10, paddingVertical: 4, backgroundColor: 'rgba(201,8,42,0.15)', borderRadius: 8 },
  removeBtnText: { color: C.red, fontSize: 12, fontWeight: '700' },
  pauseBtn:      { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8, borderWidth: 1 },
  pauseBtnOn:    { backgroundColor: 'rgba(34,197,94,0.12)', borderColor: 'rgba(34,197,94,0.4)' },
  pauseBtnOff:   { backgroundColor: 'rgba(150,150,150,0.10)', borderColor: 'rgba(150,150,150,0.3)' },
  pauseBtnText:  { fontSize: 11, fontWeight: '800', letterSpacing: 0.5 },
  switchRow:     { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 4 },
  categoryRow:   { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 10, borderTopWidth: 1, borderColor: C.border },
  rowLabel:      { fontSize: 14, color: C.textPrimary, fontWeight: '600' },
  rowSub:        { fontSize: 12, color: C.textMuted, marginTop: 2 },
  statusDot:     { width: 10, height: 10, borderRadius: 5 },
  emptyHint:     { color: C.textMuted, fontSize: 13, fontStyle: 'italic', paddingVertical: 6 },
  soundRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingVertical: 11, paddingHorizontal: 6,
    borderBottomWidth: 1, borderColor: C.border,
  },
  soundRowSelected:   { backgroundColor: 'rgba(255,107,0,0.07)', borderRadius: 8, borderBottomWidth: 0 },
  soundLabel:         { flex: 1, fontSize: 15, color: C.textMuted, fontWeight: '600' },
  soundLabelSelected: { color: C.textPrimary },
  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.75)', justifyContent: 'flex-end' },
  modalSheet: {
    backgroundColor: C.card,
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    borderWidth: 1, borderColor: C.border, height: '85%',
  },
  modalHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 14,
    borderBottomWidth: 1, borderColor: C.border,
  },
  modalTitle:  { fontSize: 16, fontWeight: '800', color: C.textPrimary },
  backBtn:     { flexDirection: 'row', alignItems: 'center', gap: 4, width: 70 },
  backBtnText: { color: C.accentBlue, fontSize: 14, fontWeight: '700' },
  closeBtn:    { width: 70, alignItems: 'flex-end' },
  pickerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 10, gap: 10,
  },
  pickerRowText:  { fontSize: 15, color: C.textPrimary, fontWeight: '600', flex: 1 },
  pickerRowSub:   { fontSize: 12, color: C.textMuted, marginTop: 2 },
  pickerRowRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pickerRowAbbr:  { fontSize: 13, color: C.textMuted },
  savedBadge:     { fontSize: 12, color: '#f5c518', fontWeight: '700' },
  separator:      { height: 1, backgroundColor: C.border, marginLeft: 16 },
  configArea:     { padding: 24 },
  configTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  configTitle:    { fontSize: 20, fontWeight: '800', color: C.textPrimary },
  configSub:      { fontSize: 13, color: C.textMuted, marginBottom: 8, lineHeight: 18 },
  configSummary:  { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: C.bg, borderRadius: 8, padding: 10, marginBottom: 20 },
  configSummaryText: { fontSize: 12, color: C.textMuted, flex: 1 },
  confirmBtn:         { backgroundColor: C.blue, padding: 16, borderRadius: 14, alignItems: 'center' },
  confirmBtnDisabled: { opacity: 0.5 },
  confirmBtnText:     { color: '#fff', fontWeight: '800', fontSize: 15 },
  cancelBtn:          { marginTop: 12, padding: 14, borderRadius: 14, alignItems: 'center' },
  cancelBtnText:      { color: C.textMuted, fontWeight: '700', fontSize: 14 },
  alertTypeRow: { flexDirection: 'row', gap: 10, marginTop: 10, marginBottom: 4 },
  alertTypeBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingVertical: 9, borderRadius: 10,
    backgroundColor: C.bg, borderWidth: 1, borderColor: C.border,
  },
  alertTypeBtnActive:  { borderColor: C.red, backgroundColor: 'rgba(201,8,42,0.12)' },
  alertTypeIcon:       { fontSize: 14 },
  alertTypeText:       { fontSize: 12, fontWeight: '700', color: C.textMuted },
  alertTypeTextActive: { color: C.red },
  pauseAllBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    marginHorizontal: 16, marginBottom: 8, paddingVertical: 13, borderRadius: 14,
    backgroundColor: '#C8102E',
  },
  pauseAllBtnResume: { backgroundColor: C.liveGreen },
  pauseAllBtnText: { color: '#fff', fontWeight: '800', fontSize: 15 },
  radarLeagueRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 12 },
  radarLeaguePill: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 16,
    backgroundColor: C.cardAlt, borderWidth: 1, borderColor: C.border,
  },
  radarLeaguePillActive: { backgroundColor: 'rgba(255,107,0,0.12)', borderColor: C.orange },
  radarLeaguePillText: { fontSize: 13, fontWeight: '700', color: C.textMuted },
  radarLeaguePillTextActive: { color: C.orange },
  searchBar: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    marginHorizontal: 16, marginTop: 10, marginBottom: 6,
    paddingHorizontal: 12, paddingVertical: 8,
    backgroundColor: C.bg, borderRadius: 10, borderWidth: 1, borderColor: C.border,
  },
  searchInput: { flex: 1, fontSize: 14, color: C.textPrimary, padding: 0 },
  pickerLeagueScroll: { height: 44, flexGrow: 0, flexShrink: 0, marginBottom: 2 },
  pickerLeaguePills: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 6, gap: 8 },
  pickerLeaguePill: {
    height: 30, paddingHorizontal: 14, borderRadius: 15,
    backgroundColor: C.cardAlt, borderWidth: 1, borderColor: C.border,
    flexShrink: 0, alignItems: 'center', justifyContent: 'center',
  },
  pickerLeaguePillActive: { backgroundColor: 'rgba(255,107,0,0.12)', borderColor: C.orange },
  pickerLeaguePillText: { fontSize: 13, fontWeight: '700', color: C.textMuted, includeFontPadding: false, textAlignVertical: 'center', lineHeight: 16 },
  pickerLeaguePillTextActive: { color: C.orange },
  // Tab bar
  favTabScroll: { height: 48, flexGrow: 0, flexShrink: 0 },
  favTabPills: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 8, gap: 8 },
  favTabPill: {
    height: 32, paddingHorizontal: 12, borderRadius: 16,
    backgroundColor: C.cardAlt, borderWidth: 1, borderColor: C.border,
    flexShrink: 0, flexDirection: 'row', alignItems: 'center', gap: 5,
  },
  favTabPillActive: { backgroundColor: 'rgba(255,107,0,0.14)', borderColor: C.orange },
  favTabPillText: { fontSize: 13, fontWeight: '700', color: C.textMuted, includeFontPadding: false, lineHeight: 16 },
  favTabPillTextActive: { color: C.orange },
  // League row
  leagueRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingVertical: 10, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.borderMuted,
  },
  // Player badge
  playerInitialBadge: {
    width: 36, height: 36, borderRadius: 18,
    backgroundColor: C.cardAlt, borderWidth: 1, borderColor: C.border,
    alignItems: 'center', justifyContent: 'center', marginRight: 2,
  },
  alertTypePill: {
    paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8,
    borderWidth: 1, marginRight: 4,
  },
  alertTypePillText: { fontSize: 13 },
  infoOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.7)', alignItems: 'center', justifyContent: 'center', padding: 32 },
  infoBox:     { width: '100%', backgroundColor: C.card, borderRadius: 20, borderWidth: 1, borderColor: C.border, padding: 28, alignItems: 'center' },
  infoTitle:   { fontSize: 20, fontWeight: '900', color: C.textPrimary, letterSpacing: 0.5, marginBottom: 10 },
  infoMessage: { fontSize: 14, color: C.textMuted, textAlign: 'center', lineHeight: 20, marginBottom: 28 },
  infoOkBtn:   { width: '100%', paddingVertical: 14, borderRadius: 12, backgroundColor: 'rgba(255,107,0,0.15)', borderWidth: 1, borderColor: 'rgba(255,107,0,0.4)', alignItems: 'center' },
  infoOkText:  { color: C.orange, fontWeight: '800', fontSize: 15 },
});
