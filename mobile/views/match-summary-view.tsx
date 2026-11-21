import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { useTheme } from '../context/ThemeContext';
import { useLanguage } from '../context/LanguageContext';
import { getMatchDetail, getDramaScore, getDramaTimeline, getLineups } from '../services/soccer-service';
import { LeagueCatalog } from '../services/league-catalog';
import { DramaMeter } from '../components/drama-meter';
import type {
  SoccerMatch,
  SoccerMatchDetail,
  SoccerDramaScore,
  DramaTimelinePoint,
  MatchLineups,
  LineupTeam,
  Incident,
  TeamStats,
} from '../types/soccer';
import { isLiveLike, displayMinute } from '../types/soccer';

interface Props {
  match: SoccerMatch;
  visible: boolean;
  onClose: () => void;
  onSetAlarm?: () => void;
}

type Section = 'events' | 'stats' | 'lineups';

// ── Module-level cache ────────────────────────────────────────────────────────

interface CacheEntry<T> { data: T; ts: number }
const LIVE_TTL     = 30_000;
const LINEUPS_TTL  = 10 * 60_000; // 10 min — ensures photo_url is fresh after server updates
const _detailCache    = new Map<string, CacheEntry<SoccerMatchDetail>>();
const _lineupsCache   = new Map<string, CacheEntry<MatchLineups>>();
const _dramaCache     = new Map<string, CacheEntry<SoccerDramaScore>>();
const _timelineCache  = new Map<string, CacheEntry<DramaTimelinePoint[]>>();
const _DETAIL_STORE   = (id: string) => `msv_v1_${id}`;
const _LINEUPS_STORE  = (id: string) => `msv_lineups_v1_${id}`;

function detailFresh(id: string, live: boolean): SoccerMatchDetail | null {
  const e = _detailCache.get(id);
  if (!e) return null;
  if (live && Date.now() - e.ts > LIVE_TTL) return null;
  return e.data;
}

async function fetchAndCacheLineups(id: string, live: boolean): Promise<MatchLineups | null> {
  const cached = _lineupsCache.get(id);
  if (cached && Date.now() - cached.ts < LINEUPS_TTL) return cached.data;

  // For finished matches, check AsyncStorage first
  if (!live) {
    try {
      const raw = await AsyncStorage.getItem(_LINEUPS_STORE(id));
      if (raw) {
        const entry: CacheEntry<MatchLineups> = JSON.parse(raw);
        _lineupsCache.set(id, entry);
        return entry.data;
      }
    } catch {}
  }

  try {
    const l = await getLineups(id);
    const entry = { data: l, ts: Date.now() };
    _lineupsCache.set(id, entry);
    // Persist finished match lineups — they're final and load instantly next time
    if (!live) {
      AsyncStorage.setItem(_LINEUPS_STORE(id), JSON.stringify(entry)).catch(() => {});
    }
    return l;
  } catch {
    return null;
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

export function MatchSummaryView({ match, visible, onClose, onSetAlarm }: Props) {
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();

  const [detail, setDetail]       = useState<SoccerMatchDetail | null>(null);
  const [drama, setDrama]         = useState<SoccerDramaScore | null>(null);
  const [timeline, setTimeline]   = useState<DramaTimelinePoint[]>([]);
  const [lineups, setLineups]     = useState<MatchLineups | null>(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [lineupsLoading, setLineupsLoading] = useState(false);
  const [section, setSection]     = useState<Section>('events');
  const [lineupSide, setLineupSide] = useState<'home' | 'away'>('home');

  const live = isLiveLike(match.status);
  const minuteLabel = displayMinute(match);
  const league = LeagueCatalog.info(match.competition);

  // Load match detail + drama when opened
  useEffect(() => {
    if (!visible) return;
    setSection('events');
    setError(null);

    (async () => {
      // 1. In-memory hit
      const inMem = detailFresh(match.id, live);
      if (inMem) {
        setDetail(inMem);
        setLoading(false);
        if (!live) return; // finished match — cached data is final
        // live: show stale, then fall through to API fetch
      }

      // 2. AsyncStorage hit for finished matches (survives app kill)
      let hadStoredDetail = false;
      if (!live && !inMem) {
        try {
          const raw = await AsyncStorage.getItem(_DETAIL_STORE(match.id));
          if (raw) {
            const entry: CacheEntry<SoccerMatchDetail> = JSON.parse(raw);
            _detailCache.set(match.id, entry);
            setDetail(entry.data);
            setLoading(false);
            hadStoredDetail = true;
            // Fall through to API — server may have corrected the score (e.g. AET)
          }
        } catch {}
      }

      // 3. Fetch from API
      if (!inMem && !hadStoredDetail) { setLoading(true); setDetail(null); }
      try {
        const d = await getMatchDetail(match.id);
        const entry = { data: d, ts: Date.now() };
        _detailCache.set(match.id, entry);
        setDetail(d);
        setError(null);
        if (!live) {
          AsyncStorage.setItem(_DETAIL_STORE(match.id), JSON.stringify(entry)).catch(() => {});
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Unknown error');
      }
      setLoading(false);
    })();

    if (live) {
      const dc = _dramaCache.get(match.id);
      if (dc && Date.now() - dc.ts < LIVE_TTL) setDrama(dc.data);
      getDramaScore(match.id)
        .then(d => { _dramaCache.set(match.id, { data: d, ts: Date.now() }); setDrama(d); })
        .catch(() => {});
      setTimeline([]);
    } else {
      setDrama(null);
      const tc = _timelineCache.get(match.id);
      if (tc) {
        setTimeline(tc.data);
      } else {
        getDramaTimeline(match.id, 2)
          .then(tl => { _timelineCache.set(match.id, { data: tl, ts: Date.now() }); setTimeline(tl); })
          .catch(() => {});
      }
    }

    // Pre-fetch lineups in parallel — by the time user taps the tab it's ready
    fetchAndCacheLineups(match.id, live)
      .then(l => { if (l) setLineups(l); })
      .catch(() => {});
  }, [visible, match.id]);

  // Live match polling — refresh detail + drama every 30s while open
  useEffect(() => {
    if (!visible || !live) return;
    const id = setInterval(() => {
      getMatchDetail(match.id)
        .then(d => { _detailCache.set(match.id, { data: d, ts: Date.now() }); setDetail(d); })
        .catch(() => {});
      getDramaScore(match.id)
        .then(d => { _dramaCache.set(match.id, { data: d, ts: Date.now() }); setDrama(d); })
        .catch(() => {});
    }, 30_000);
    return () => clearInterval(id);
  }, [visible, live, match.id]);

  // Lineups tab: show spinner only if pre-fetch hasn't landed yet
  useEffect(() => {
    if (!visible || section !== 'lineups') return;
    const cached = _lineupsCache.get(match.id);
    if (cached && Date.now() - cached.ts < LINEUPS_TTL) { setLineups(cached.data); return; }
    // Pre-fetch is still in flight — show spinner and wait for it
    setLineupsLoading(true);
    fetchAndCacheLineups(match.id, live)
      .then(l => { if (l) setLineups(l); })
      .catch(() => {})
      .finally(() => setLineupsLoading(false));
  }, [visible, section, match.id, live]);

  const showAlarmBtn = onSetAlarm && (match.status === 'scheduled' || live);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={onClose}
    >
      <View style={[styles.container, { backgroundColor: colors.bg }]}>
        {/* Nav bar — paddingTop ensures button clears camera punch-hole on Android */}
        <View style={[styles.navBar, { borderBottomColor: colors.border, paddingTop: Math.max(insets.top, 12) }]}>
          <TouchableOpacity onPress={onClose} hitSlop={8}>
            <Ionicons name="chevron-down" size={24} color={colors.textPrimary} />
          </TouchableOpacity>
          <Text style={[styles.navTitle, { color: colors.textMuted }]}>
            {league.label}
          </Text>
          {showAlarmBtn ? (
            <TouchableOpacity onPress={onSetAlarm} hitSlop={8}>
              <Ionicons name="notifications-outline" size={22} color={colors.orange} />
            </TouchableOpacity>
          ) : (
            <View style={{ width: 24 }} />
          )}
        </View>

        <ScrollView showsVerticalScrollIndicator={false}>
          {/* Match header */}
          <MatchHeader match={match} minuteLabel={minuteLabel} live={live} colors={colors} />

          {/* Set Alarm button strip — visible for live + scheduled matches */}
          {showAlarmBtn && (
            <TouchableOpacity
              style={[styles.alarmStrip, { backgroundColor: colors.card, borderColor: colors.border }]}
              onPress={onSetAlarm}
              activeOpacity={0.75}
            >
              <Ionicons name="notifications-outline" size={18} color={colors.orange} />
              <Text style={[styles.alarmStripText, { color: colors.orange }]}>
                {live ? 'Set Match Alarm' : 'Set Alarm'}
              </Text>
              <Ionicons name="chevron-forward" size={16} color={colors.orange} />
            </TouchableOpacity>
          )}

          {/* Drama meter (live only) */}
          {drama && (
            <View style={[styles.dramaWrap, { backgroundColor: colors.card, borderColor: colors.border }]}>
              <DramaMeter score={drama} expandable initialExpanded />
            </View>
          )}

          {/* xG bar */}
          {detail && !loading && (
            <XgBar match={match} detail={detail} colors={colors} t={t} />
          )}

          {/* Drama timeline (finished matches only) */}
          {!live && timeline.length > 0 && (
            <DramaTimelineChart
              timeline={timeline}
              incidents={detail?.incidents ?? []}
              colors={colors}
            />
          )}

          {/* Section tabs */}
          <View style={[styles.sectionTabs, { borderBottomColor: colors.border }]}>
            {(['events', 'stats', 'lineups'] as Section[]).map(sec => (
              <TouchableOpacity
                key={sec}
                style={[
                  styles.sectionTab,
                  section === sec && [styles.sectionTabActive, { borderBottomColor: colors.orange }],
                ]}
                onPress={() => setSection(sec)}
              >
                <Text style={[
                  styles.sectionTabText,
                  { color: section === sec ? colors.orange : colors.textMuted },
                ]}>
                  {t(sec === 'events' ? 'match.incidents' : `match.${sec}`)}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {loading ? (
            <View style={styles.centered}>
              <ActivityIndicator color={colors.orange} />
            </View>
          ) : error || !detail ? (
            <View style={styles.centered}>
              <Ionicons name="cloud-offline-outline" size={32} color={colors.textMuted} />
              <Text style={[styles.errorText, { color: colors.textMuted }]}>
                {error ?? t('home.connectionError')}
              </Text>
              <TouchableOpacity
                style={[styles.retryBtn, { backgroundColor: colors.orange }]}
                onPress={() => {
                  setError(null);
                  setLoading(true);
                  setDetail(null);
                  _detailCache.delete(match.id);
                  getMatchDetail(match.id)
                    .then(d => { _detailCache.set(match.id, { data: d, ts: Date.now() }); setDetail(d); setError(null); })
                    .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Error'))
                    .finally(() => setLoading(false));
                }}
              >
                <Text style={styles.retryBtnText}>Retry</Text>
              </TouchableOpacity>
            </View>
          ) : (
            <>
              {section === 'events' && (
                <IncidentsSection
                  incidents={detail.incidents ?? []}
                  colors={colors}
                />
              )}
              {section === 'stats' && (
                <StatsSection
                  home={detail.stats?.home}
                  away={detail.stats?.away}
                  homeName={match.home.short_name || match.home.name}
                  awayName={match.away.short_name || match.away.name}
                  colors={colors}
                  t={t}
                />
              )}
              {section === 'lineups' && (
                <LineupsSection
                  lineups={lineups}
                  loading={lineupsLoading}
                  side={lineupSide}
                  onSideChange={setLineupSide}
                  homeName={match.home.short_name || match.home.name}
                  awayName={match.away.short_name || match.away.name}
                  homeCrest={match.home.crest}
                  awayCrest={match.away.crest}
                  colors={colors}
                  t={t}
                />
              )}
            </>
          )}

          <View style={{ height: 48 }} />
        </ScrollView>
      </View>
    </Modal>
  );
}

// ── Match header ──────────────────────────────────────────────────────────────

function MatchHeader({
  match, minuteLabel, live, colors,
}: {
  match: SoccerMatch;
  minuteLabel: string;
  live: boolean;
  colors: ReturnType<typeof useTheme>['colors'];
}) {
  const hasScore = match.home.score != null && match.away.score != null;
  const isFt = match.status === 'ft';

  return (
    <View style={styles.matchHeader}>
      {/* Status badge */}
      {(live && minuteLabel) ? (
        <View style={[styles.statusBadge, { backgroundColor: colors.liveGreen + '22', borderColor: colors.liveGreen }]}>
          <View style={[styles.liveDot, { backgroundColor: colors.liveGreen }]} />
          <Text style={[styles.statusBadgeText, { color: colors.liveGreen }]}>{minuteLabel}</Text>
        </View>
      ) : isFt ? (
        <View style={[styles.statusBadge, { backgroundColor: colors.border }]}>
          <Text style={[styles.statusBadgeText, { color: colors.textMuted }]}>FT</Text>
        </View>
      ) : null}

      {/* Teams + score */}
      <View style={styles.teamsRow}>
        <TeamBlock name={match.home.name} crest={match.home.crest} colors={colors} align="left" />
        <View style={styles.vsCol}>
          {hasScore ? (
            <>
              <Text style={[styles.scoreText, { color: colors.textPrimary }]}>
                {match.home.score} – {match.away.score}
              </Text>
              {match.home.score_pens != null && (
                <Text style={[styles.htScoreText, { color: colors.textMuted }]}>
                  ({match.home.score_pens} – {match.away.score_pens} pens)
                </Text>
              )}
              {match.home.score_ht != null && (
                <Text style={[styles.htScoreText, { color: colors.textMuted }]}>
                  ({match.home.score_ht} – {match.away.score_ht} HT)
                </Text>
              )}
            </>
          ) : (
            <Text style={[styles.vsText, { color: colors.textMuted }]}>vs</Text>
          )}
        </View>
        <TeamBlock name={match.away.name} crest={match.away.crest} colors={colors} align="right" />
      </View>
    </View>
  );
}

function TeamBlock({
  name, crest, colors, align,
}: {
  name: string;
  crest: string | null | undefined;
  colors: ReturnType<typeof useTheme>['colors'];
  align: 'left' | 'right';
}) {
  return (
    <View style={[styles.teamBlock, { alignItems: align === 'left' ? 'flex-start' : 'flex-end' }]}>
      {crest ? (
        <Image source={{ uri: crest }} style={styles.teamCrest} contentFit="contain" />
      ) : (
        <View style={[styles.teamCrestFallback, { backgroundColor: colors.border }]}>
          <Text style={[styles.teamCrestInitial, { color: colors.textMuted }]}>
            {name.slice(0, 2).toUpperCase()}
          </Text>
        </View>
      )}
      <Text style={[styles.teamName, { color: colors.textPrimary }]} numberOfLines={2}>
        {name}
      </Text>
    </View>
  );
}

// ── xG bar ────────────────────────────────────────────────────────────────────

function toNumberOrNull(v: number | string | null | undefined): number | null {
  if (v == null) return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function XgBar({
  match, detail, colors, t,
}: {
  match: SoccerMatch;
  detail: SoccerMatchDetail;
  colors: ReturnType<typeof useTheme>['colors'];
  t: (k: string) => string;
}) {
  // prefer detail stats xG over xg_per_minute cumulative (server may send these as strings)
  const homeXg = toNumberOrNull(detail.stats?.home?.expected_goals ?? detail.home?.xg);
  const awayXg = toNumberOrNull(detail.stats?.away?.expected_goals ?? detail.away?.xg);
  if (homeXg == null && awayXg == null) return null;

  const hv = homeXg ?? 0;
  const av = awayXg ?? 0;
  const total = hv + av || 1;
  const homePct = hv / total;

  return (
    <View style={[styles.xgWrap, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <View style={styles.xgRow}>
        <Text style={[styles.xgValue, { color: colors.textPrimary }]}>{hv.toFixed(2)}</Text>
        <Text style={[styles.xgLabel, { color: colors.textMuted }]}>{t('soccer.label.xg')}</Text>
        <Text style={[styles.xgValue, { color: colors.textPrimary }]}>{av.toFixed(2)}</Text>
      </View>
      <View style={[styles.xgBar, { backgroundColor: colors.border }]}>
        <View style={[styles.xgBarFill, { flex: homePct, backgroundColor: colors.accentBlue }]} />
        <View style={[styles.xgBarFill, { flex: 1 - homePct, backgroundColor: colors.orange }]} />
      </View>
      <View style={styles.xgTeamRow}>
        <Text style={[styles.xgTeamName, { color: colors.textMuted }]}>
          {match.home.short_name || match.home.name}
        </Text>
        <Text style={[styles.xgTeamName, { color: colors.textMuted }]}>
          {match.away.short_name || match.away.name}
        </Text>
      </View>
    </View>
  );
}

// ── Incidents timeline ────────────────────────────────────────────────────────

function incidentIcon(inc: Incident): string {
  const t = inc.type ?? '';
  if (t === 'goal') return '⚽';
  if (t === 'owngoal' || t === 'own_goal') return '🔁';
  if (t === 'card') {
    const ct = (inc.card_type ?? '').toLowerCase();
    if (ct === 'red') return '🟥';
    if (ct === 'yellowred') return '🟥'; // second yellow = red
    return '🟨'; // default yellow
  }
  if (t === 'yellow_card') return '🟨';
  if (t === 'red_card') return '🟥';
  if (t === 'substitution') return '🔄';
  if (t === 'penalty') return '🎯';
  if (t === 'var') return '📺';
  return '';
}

function IncidentsSection({
  incidents, colors,
}: {
  incidents: Incident[];
  colors: ReturnType<typeof useTheme>['colors'];
}) {
  // Filter to only meaningful events; skip period markers and injury-time notices
  const visible = incidents.filter(inc => {
    const t = inc.type ?? '';
    return t !== 'period' && t !== 'injuryTime' && t !== 'injurytime' && t !== '';
  });

  if (visible.length === 0) {
    return (
      <View style={styles.centered}>
        <Text style={{ color: colors.textMuted }}>No events yet</Text>
      </View>
    );
  }

  const sorted = [...visible].sort((a, b) => (a.minute ?? 0) - (b.minute ?? 0));

  return (
    <View style={styles.incidentsWrap}>
      {sorted.map((inc, i) => {
        const icon   = incidentIcon(inc);
        if (!icon) return null; // skip unknown types
        const isHome = inc.is_home ?? false;
        const playerName = inc.player ?? inc.player_in ?? '';
        const subOut = inc.type === 'substitution' ? inc.player_out : null;

        return (
          <View key={i} style={[styles.incidentRow, { borderBottomColor: colors.borderMuted }]}>
            {isHome ? (
              <>
                <View style={styles.incidentHomeInfo}>
                  <Text style={[styles.incidentPlayer, { color: colors.textPrimary }]} numberOfLines={1}>
                    {playerName}
                  </Text>
                  {subOut && (
                    <Text style={[styles.incidentSub, { color: colors.textMuted }]} numberOfLines={1}>
                      ↓ {subOut}
                    </Text>
                  )}
                  {inc.assist && (
                    <Text style={[styles.incidentAssist, { color: colors.textMuted }]} numberOfLines={1}>
                      {inc.assist}
                    </Text>
                  )}
                  {inc.home_score != null && (
                    <Text style={[styles.incidentScore, { color: colors.orange }]}>
                      {inc.home_score} – {inc.away_score}
                    </Text>
                  )}
                </View>
                <View style={styles.incidentMid}>
                  <Text style={styles.incidentIcon}>{icon}</Text>
                  <Text style={[styles.incidentMin, { color: colors.textMuted }]}>
                    {inc.minute != null ? `${inc.minute}'` : ''}
                  </Text>
                </View>
                <View style={{ flex: 1 }} />
              </>
            ) : (
              <>
                <View style={{ flex: 1 }} />
                <View style={styles.incidentMid}>
                  <Text style={[styles.incidentMin, { color: colors.textMuted }]}>
                    {inc.minute != null ? `${inc.minute}'` : ''}
                  </Text>
                  <Text style={styles.incidentIcon}>{icon}</Text>
                </View>
                <View style={styles.incidentAwayInfo}>
                  <Text style={[styles.incidentPlayer, { color: colors.textPrimary }]} numberOfLines={1}>
                    {playerName}
                  </Text>
                  {subOut && (
                    <Text style={[styles.incidentSub, { color: colors.textMuted }]} numberOfLines={1}>
                      ↓ {subOut}
                    </Text>
                  )}
                  {inc.assist && (
                    <Text style={[styles.incidentAssist, { color: colors.textMuted }]} numberOfLines={1}>
                      {inc.assist}
                    </Text>
                  )}
                  {inc.home_score != null && (
                    <Text style={[styles.incidentScore, { color: colors.orange }]}>
                      {inc.home_score} – {inc.away_score}
                    </Text>
                  )}
                </View>
              </>
            )}
          </View>
        );
      })}
    </View>
  );
}

// ── Stats section ─────────────────────────────────────────────────────────────

interface StatRow {
  labelKey: string;
  homeVal?: number;
  awayVal?: number;
  format?: 'percent' | 'decimal' | 'default';
}

function StatsSection({
  home, away, homeName, awayName, colors, t,
}: {
  home?: TeamStats;
  away?: TeamStats;
  homeName: string;
  awayName: string;
  colors: ReturnType<typeof useTheme>['colors'];
  t: (k: string) => string;
}) {
  if (!home && !away) {
    return (
      <View style={styles.centered}>
        <Text style={{ color: colors.textMuted }}>No stats available</Text>
      </View>
    );
  }

  const rows: StatRow[] = ([
    { labelKey: 'match.stats.possession',     homeVal: home?.ball_possession, awayVal: away?.ball_possession, format: 'percent' },
    { labelKey: 'soccer.label.xg',            homeVal: home?.expected_goals,  awayVal: away?.expected_goals,  format: 'decimal' },
    { labelKey: 'match.stats.shots',          homeVal: home?.total_shots,     awayVal: away?.total_shots },
    { labelKey: 'match.stats.shotsOnTarget',  homeVal: home?.shots_on_target, awayVal: away?.shots_on_target },
    { labelKey: 'match.stats.bigChances',     homeVal: home?.big_chances,     awayVal: away?.big_chances },
    { labelKey: 'match.stats.corners',        homeVal: home?.corner_kicks,    awayVal: away?.corner_kicks },
    { labelKey: 'match.stats.fouls',          homeVal: home?.fouls,           awayVal: away?.fouls },
    { labelKey: 'match.stats.yellows',        homeVal: home?.yellow_cards,    awayVal: away?.yellow_cards },
    { labelKey: 'match.stats.reds',           homeVal: home?.red_cards,       awayVal: away?.red_cards },
  ] as StatRow[]).filter(r => r.homeVal != null || r.awayVal != null);

  return (
    <View style={styles.statsWrap}>
      <View style={styles.statsHeader}>
        <Text style={[styles.statsTeam, { color: colors.accentBlue }]}>{homeName}</Text>
        <View style={{ flex: 1 }} />
        <Text style={[styles.statsTeam, { color: colors.orange }]}>{awayName}</Text>
      </View>

      {rows.map(row => {
        const hv = row.homeVal ?? 0;
        const av = row.awayVal ?? 0;
        const total = hv + av || 1;
        const homePct = hv / total;
        const fmt = (v: number) =>
          row.format === 'percent' ? `${v}%`
          : row.format === 'decimal' ? v.toFixed(2)
          : String(v);

        return (
          <View key={row.labelKey} style={[styles.statRow, { borderBottomColor: colors.borderMuted }]}>
            <Text style={[styles.statValue, { color: colors.accentBlue }]}>{fmt(hv)}</Text>
            <View style={styles.statBarWrap}>
              <Text style={[styles.statLabel, { color: colors.textMuted }]}>{t(row.labelKey)}</Text>
              <View style={[styles.statBarBg, { backgroundColor: colors.border }]}>
                <View style={[styles.statBarSide, { flex: homePct, backgroundColor: colors.accentBlue }]} />
                <View style={[styles.statBarSide, { flex: 1 - homePct, backgroundColor: colors.orange }]} />
              </View>
            </View>
            <Text style={[styles.statValue, { color: colors.orange, textAlign: 'right' }]}>{fmt(av)}</Text>
          </View>
        );
      })}
    </View>
  );
}

// ── Lineups section ───────────────────────────────────────────────────────────

function LineupsSection({
  lineups, loading, side, onSideChange,
  homeName, awayName, homeCrest, awayCrest,
  colors, t,
}: {
  lineups: MatchLineups | null;
  loading: boolean;
  side: 'home' | 'away';
  onSideChange: (s: 'home' | 'away') => void;
  homeName: string;
  awayName: string;
  homeCrest: string | null | undefined;
  awayCrest: string | null | undefined;
  colors: ReturnType<typeof useTheme>['colors'];
  t: (k: string) => string;
}) {
  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.orange} />
        <Text style={[styles.lineupsLoadingText, { color: colors.textMuted }]}>
          {t('soccer.alarm.loadingLineups')}
        </Text>
      </View>
    );
  }

  if (!lineups || lineups.lineup_status === 'unavailable' || !lineups.lineups) {
    return (
      <View style={styles.centered}>
        <Ionicons name="people-outline" size={36} color={colors.textMuted} />
        <Text style={[styles.lineupsEmptyText, { color: colors.textMuted }]}>
          {t('soccer.alarm.lineupsUnavailable')}
        </Text>
      </View>
    );
  }

  const team: LineupTeam = lineups.lineups[side];
  const otherCrest = side === 'home' ? homeCrest : awayCrest;
  const isPredicted = lineups.lineup_status === 'predicted';

  return (
    <View style={styles.lineupsWrap}>
      {/* Side selector pills */}
      <View style={[styles.selectorRow, { backgroundColor: colors.card, borderColor: colors.border }]}>
        {(['home', 'away'] as const).map(s => (
          <TouchableOpacity
            key={s}
            style={[
              styles.selectorPill,
              side === s && { backgroundColor: colors.orange },
            ]}
            onPress={() => onSideChange(s)}
          >
            {(s === 'home' ? homeCrest : awayCrest) ? (
              <Image
                source={{ uri: s === 'home' ? homeCrest! : awayCrest! }}
                style={styles.pillCrest}
                contentFit="contain"
              />
            ) : null}
            <Text style={[
              styles.selectorPillText,
              { color: side === s ? '#fff' : colors.textMuted },
            ]} numberOfLines={1}>
              {s === 'home' ? homeName : awayName}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Formation + predicted badge */}
      <View style={styles.formationRow}>
        {team.formation ? (
          <View style={[styles.formationChip, { backgroundColor: colors.border }]}>
            <Text style={[styles.formationText, { color: colors.textPrimary }]}>
              {team.formation}
            </Text>
          </View>
        ) : null}
        {isPredicted && (
          <View style={[styles.predictedChip, { backgroundColor: colors.orange + '22', borderColor: colors.orange }]}>
            <Text style={[styles.predictedText, { color: colors.orange }]}>Predicted</Text>
          </View>
        )}
      </View>

      {/* Pitch formation */}
      <PitchView team={team} />

      {/* Substitutes */}
      {team.substitutes.length > 0 && (
        <>
          <Text style={[styles.lineupsGroupLabel, { color: colors.textMuted, marginTop: 16 }]}>SUBSTITUTES</Text>
          {team.substitutes.map((p, i) => (
            <View key={p.id ?? i} style={[styles.playerRow, { borderBottomColor: colors.borderMuted }]}>
              <PlayerAvatar player={p} size={32} dimmed />
              <Text style={[styles.playerName, { color: colors.textMuted }]} numberOfLines={1}>
                {p.short_name || p.name}
              </Text>
              {p.ai_score != null && (
                <View style={[styles.aiScoreBadge, { backgroundColor: aiScoreColor(p.ai_score) + '22', borderColor: aiScoreColor(p.ai_score) }]}>
                  <Text style={[styles.aiScoreText, { color: aiScoreColor(p.ai_score) }]}>
                    {p.ai_score.toFixed(1)}
                  </Text>
                </View>
              )}
            </View>
          ))}
        </>
      )}
    </View>
  );
}

// ── Pitch formation view ──────────────────────────────────────────────────────

function parsePitchRows(formation: string | null | undefined, players: LineupPlayer[]): LineupPlayer[][] {
  const counts: number[] = [1]; // GK
  if (formation) {
    const parts = formation.split('-').map(Number).filter(n => !isNaN(n) && n > 0);
    counts.push(...parts);
  } else {
    // fallback: spread 10 outfield players evenly
    const outfield = players.length - 1;
    if (outfield <= 0) { counts.push(...[4, 3, 3]); }
    else {
      let remaining = outfield;
      while (remaining > 0) { const r = Math.min(4, remaining); counts.push(r); remaining -= r; }
    }
  }
  const rows: LineupPlayer[][] = [];
  let idx = 0;
  for (const n of counts) {
    rows.push(players.slice(idx, idx + n));
    idx += n;
  }
  return rows;
}

function PlayerAvatar({ player, size = 40, dimmed = false }: { player: LineupPlayer; size?: number; dimmed?: boolean }) {
  const [imgError, setImgError] = useState(false);
  const showPhoto = !!player.photo_url && !imgError;
  return (
    <View style={{
      width: size, height: size, borderRadius: size / 2,
      overflow: 'hidden',
      backgroundColor: dimmed ? 'rgba(120,120,120,0.2)' : 'rgba(255,255,255,0.15)',
      borderWidth: 1.5, borderColor: dimmed ? 'rgba(150,150,150,0.3)' : 'rgba(255,255,255,0.5)',
      alignItems: 'center', justifyContent: 'center',
    }}>
      {showPhoto ? (
        <Image
          source={{ uri: player.photo_url }}
          style={{ width: size, height: size }}
          contentFit="cover"
          onError={() => setImgError(true)}
        />
      ) : (
        <Text style={{ fontSize: size * 0.32, fontWeight: '800', color: dimmed ? '#888' : '#fff' }}>
          {player.jersey_number != null ? String(player.jersey_number) : '?'}
        </Text>
      )}
    </View>
  );
}

function PitchView({ team }: { team: LineupTeam }) {
  const rows = parsePitchRows(team.formation, team.players);
  // Render GK at bottom → forwards at top (reverse for natural reading)
  const displayRows = [...rows].reverse();

  return (
    <View style={pitchStyles.pitch}>
      {/* Pitch markings */}
      <View style={pitchStyles.centerLine} />
      <View style={pitchStyles.centerCircle} />
      <View style={pitchStyles.penAreaTop} />
      <View style={pitchStyles.penAreaBottom} />

      {/* Players */}
      <View style={pitchStyles.playersArea}>
        {displayRows.map((rowPlayers, rowIdx) => (
          <View key={rowIdx} style={pitchStyles.row}>
            {rowPlayers.map((p) => (
              <View key={p.id} style={pitchStyles.playerSlot}>
                <PlayerAvatar player={p} size={40} />
                <Text style={pitchStyles.playerLabel} numberOfLines={1}>
                  {(p.short_name || p.name).split(' ').slice(-1)[0]}
                </Text>
              </View>
            ))}
          </View>
        ))}
      </View>
    </View>
  );
}

const pitchStyles = StyleSheet.create({
  pitch: {
    backgroundColor: '#2e7d32',
    borderRadius: 12,
    overflow: 'hidden',
    paddingVertical: 12,
    marginBottom: 8,
    position: 'relative',
  },
  centerLine: {
    position: 'absolute',
    left: 0, right: 0,
    top: '50%',
    height: 1,
    backgroundColor: 'rgba(255,255,255,0.25)',
  },
  centerCircle: {
    position: 'absolute',
    width: 60, height: 60,
    borderRadius: 30,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.25)',
    top: '50%', left: '50%',
    marginTop: -30, marginLeft: -30,
  },
  penAreaTop: {
    position: 'absolute',
    top: 0, left: '25%', right: '25%',
    height: 32,
    borderLeftWidth: 1, borderRightWidth: 1, borderBottomWidth: 1,
    borderColor: 'rgba(255,255,255,0.2)',
  },
  penAreaBottom: {
    position: 'absolute',
    bottom: 0, left: '25%', right: '25%',
    height: 32,
    borderLeftWidth: 1, borderRightWidth: 1, borderTopWidth: 1,
    borderColor: 'rgba(255,255,255,0.2)',
  },
  playersArea: {
    paddingHorizontal: 8,
    gap: 4,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    paddingVertical: 6,
  },
  playerSlot: {
    alignItems: 'center',
    width: 56,
    gap: 3,
  },
  playerLabel: {
    color: '#fff',
    fontSize: 10,
    fontWeight: '700',
    textAlign: 'center',
    textShadowColor: 'rgba(0,0,0,0.6)',
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 2,
  },
});

// ── Drama timeline chart ──────────────────────────────────────────────────────

function DramaTimelineChart({
  timeline,
  incidents,
  colors,
}: {
  timeline: DramaTimelinePoint[];
  incidents: Incident[];
  colors: ReturnType<typeof useTheme>['colors'];
}) {
  const [chartW, setChartW] = useState(0);
  const CHART_H = 76;

  if (timeline.length < 2) return null;

  const n = timeline.length;
  const peak = Math.max(...timeline.map(p => p.drama));
  const mustWatchY = CHART_H * (1 - 7 / 10); // y from top where drama=7 sits

  // Goal minutes for ⚽ markers
  const goalMins = new Set<number>(
    incidents
      .filter(inc => inc.type === 'goal' || inc.type === 'owngoal' || inc.type === 'own_goal')
      .map(inc => inc.minute ?? -1)
      .filter(m => m >= 0)
  );

  // Line segments calculated from measured width
  const pts = chartW > 0
    ? timeline.map((pt, i) => ({
        x: (i / (n - 1)) * chartW,
        y: CHART_H * (1 - Math.max(0, Math.min(10, pt.drama)) / 10),
        drama: pt.drama,
        minute: pt.m,
      }))
    : [];

  // HT divider x position
  const htIdx = timeline.findIndex(p => p.m >= 45);
  const htX = htIdx >= 0 && pts.length > 0 ? pts[htIdx].x : -1;

  return (
    <View style={[tlStyles.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
      {/* Header row */}
      <View style={tlStyles.header}>
        <Text style={[tlStyles.title, { color: colors.textMuted }]}>DRAMA TIMELINE</Text>
        <View style={[tlStyles.peakBadge, {
          backgroundColor: peak >= 7 ? colors.orange + '22' : colors.border,
        }]}>
          <Text style={[tlStyles.peakText, { color: peak >= 7 ? colors.orange : colors.textMuted }]}>
            Peak {peak.toFixed(1)}
          </Text>
        </View>
      </View>

      {/* Chart area */}
      <View style={{ height: CHART_H + 18, position: 'relative' }}>

        {/* Fill columns — flex layout, no width measurement needed */}
        <View style={{
          position: 'absolute', left: 0, right: 0, top: 0, height: CHART_H,
          flexDirection: 'row', alignItems: 'flex-end',
        }}>
          {timeline.map((pt, i) => {
            const barH = Math.max(1, (Math.max(0, pt.drama) / 10) * CHART_H);
            const hi = pt.drama >= 7;
            const mid = pt.drama >= 4;
            const col = hi ? colors.orange + '55' : mid ? colors.accentBlue + '30' : colors.textMuted + '18';
            return (
              <View
                key={i}
                style={{ flex: 1, height: barH, backgroundColor: col }}
              />
            );
          })}
        </View>

        {/* Must-Watch threshold line (drama = 7.0) */}
        <View style={{
          position: 'absolute', left: 0, right: 0, top: mustWatchY, height: 1,
          backgroundColor: colors.orange + '44',
        }} />

        {/* HT vertical divider */}
        {htX >= 0 && (
          <View style={{
            position: 'absolute', left: htX, top: 0, width: 1, height: CHART_H,
            backgroundColor: colors.border,
          }} />
        )}

        {/* Line overlay — renders segments once width is known */}
        <View
          style={{ position: 'absolute', left: 0, right: 0, top: 0, height: CHART_H }}
          onLayout={e => setChartW(e.nativeEvent.layout.width)}
        >
          {pts.slice(0, -1).map((pt, i) => {
            const next = pts[i + 1];
            const dx = next.x - pt.x;
            const dy = next.y - pt.y;
            const len = Math.sqrt(dx * dx + dy * dy);
            if (len < 0.5) return null;
            const angle = Math.atan2(dy, dx) * (180 / Math.PI);
            const cx = (pt.x + next.x) / 2;
            const cy = (pt.y + next.y) / 2;
            const hi = pt.drama >= 7 || next.drama >= 7;
            return (
              <View
                key={i}
                style={{
                  position: 'absolute',
                  left: cx - len / 2,
                  top: cy - 1,
                  width: len,
                  height: 2.5,
                  backgroundColor: hi ? colors.orange : colors.accentBlue,
                  borderRadius: 1,
                  transform: [{ rotate: `${angle}deg` }],
                }}
              />
            );
          })}

          {/* Goal markers: small ⚽ below each goal minute */}
          {pts.filter(pt => goalMins.has(pt.minute)).map((pt, i) => (
            <View key={`goal-${i}`} style={{
              position: 'absolute',
              left: pt.x - 4,
              top: CHART_H - 2,
              width: 8,
              height: 2,
              backgroundColor: colors.orange,
              borderRadius: 1,
            }} />
          ))}
        </View>

        {/* Bottom labels */}
        <View style={{
          position: 'absolute', left: 0, right: 0, top: CHART_H + 4,
          flexDirection: 'row', justifyContent: 'space-between',
        }}>
          <Text style={[tlStyles.minLabel, { color: colors.textMuted }]}>0'</Text>
          <Text style={[tlStyles.minLabel, { color: colors.textMuted }]}>HT</Text>
          <Text style={[tlStyles.minLabel, { color: colors.textMuted }]}>{timeline[n - 1].m}'</Text>
        </View>
      </View>
    </View>
  );
}

const tlStyles = StyleSheet.create({
  card: {
    marginHorizontal: 12,
    marginBottom: 8,
    borderRadius: 12,
    borderWidth: 1,
    padding: 12,
    paddingBottom: 10,
    overflow: 'hidden',
  },
  header:     { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  title:      { fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.8 },
  peakBadge:  { borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  peakText:   { fontSize: 11, fontWeight: '700' },
  minLabel:   { fontSize: 10, fontWeight: '600' },
});

function posBg(pos: string | undefined, colors: ReturnType<typeof useTheme>['colors']): string {
  switch (pos) {
    case 'G':  return '#27ae60';
    case 'D':  return '#2980b9';
    case 'M':  return colors.orange;
    case 'F':  return '#c0392b';
    default:   return colors.border;
  }
}

function aiScoreColor(score: number): string {
  if (score >= 8)  return '#f39c12';
  if (score >= 7)  return '#27ae60';
  if (score >= 6)  return '#2980b9';
  return '#7f8c8d';
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1 },

  navBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  navTitle: { fontSize: 14, fontWeight: '600' },

  matchHeader: { padding: 20, alignItems: 'center', gap: 12 },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  liveDot:        { width: 6, height: 6, borderRadius: 3 },
  statusBadgeText: { fontSize: 12, fontWeight: '700' },

  teamsRow: { flexDirection: 'row', alignItems: 'center', width: '100%' },
  teamBlock: { flex: 1, gap: 6 },
  teamCrest: { width: 52, height: 52, borderRadius: 4 },
  teamCrestFallback: {
    width: 52, height: 52, borderRadius: 4,
    alignItems: 'center', justifyContent: 'center',
  },
  teamCrestInitial:  { fontSize: 16, fontWeight: '800' },
  teamName:          { fontSize: 13, fontWeight: '600', lineHeight: 18 },

  vsCol:       { alignItems: 'center', paddingHorizontal: 12 },
  scoreText:   { fontSize: 28, fontWeight: '800', fontVariant: ['tabular-nums'] },
  htScoreText: { fontSize: 11, marginTop: 2 },
  vsText:      { fontSize: 18, fontWeight: '700' },

  alarmStrip: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    marginHorizontal: 12, marginTop: 8, marginBottom: 4,
    borderRadius: 12, borderWidth: 1,
    paddingHorizontal: 14, paddingVertical: 12,
  },
  alarmStripText: { flex: 1, fontSize: 14, fontWeight: '700' },
  dramaWrap: {
    marginHorizontal: 12,
    marginBottom: 4,
    borderRadius: 12,
    borderWidth: 1,
    padding: 12,
  },

  xgWrap: {
    marginHorizontal: 12,
    marginBottom: 8,
    borderRadius: 12,
    borderWidth: 1,
    padding: 12,
    gap: 6,
  },
  xgRow:      { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  xgLabel:    { fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 1 },
  xgValue:    { fontSize: 16, fontWeight: '700', fontVariant: ['tabular-nums'] },
  xgBar:      { height: 6, borderRadius: 3, flexDirection: 'row', overflow: 'hidden' },
  xgBarFill:  { height: '100%' },
  xgTeamRow:  { flexDirection: 'row', justifyContent: 'space-between' },
  xgTeamName: { fontSize: 10, fontWeight: '600' },

  sectionTabs: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    marginHorizontal: 12,
    marginTop: 4,
  },
  sectionTab: {
    flex: 1,
    paddingVertical: 10,
    alignItems: 'center',
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  sectionTabActive: {},
  sectionTabText: { fontSize: 13, fontWeight: '600' },

  centered:     { padding: 40, alignItems: 'center', gap: 10 },
  errorText:    { fontSize: 13, textAlign: 'center' },
  retryBtn:     { marginTop: 4, paddingHorizontal: 20, paddingVertical: 8, borderRadius: 8 },
  retryBtnText: { color: '#fff', fontSize: 13, fontWeight: '700' },

  // Incidents
  incidentsWrap:    { paddingHorizontal: 12, paddingTop: 8 },
  incidentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  incidentHomeInfo: { flex: 1, alignItems: 'flex-start' },
  incidentAwayInfo: { flex: 1, alignItems: 'flex-end' },
  incidentMid:      { alignItems: 'center', paddingHorizontal: 10, gap: 1 },
  incidentIcon:     { fontSize: 16 },
  incidentMin:      { fontSize: 10, fontWeight: '600' },
  incidentPlayer:   { fontSize: 13, fontWeight: '600', maxWidth: 120 },
  incidentSub:      { fontSize: 10, maxWidth: 120 },
  incidentAssist:   { fontSize: 11, maxWidth: 120 },
  incidentScore:    { fontSize: 12, fontWeight: '700', fontVariant: ['tabular-nums'] },

  // Stats
  statsWrap:   { paddingHorizontal: 12, paddingTop: 12, gap: 2 },
  statsHeader: { flexDirection: 'row', marginBottom: 8, paddingHorizontal: 4 },
  statsTeam:   { fontSize: 12, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  statRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  statValue:   { width: 40, fontSize: 13, fontWeight: '700', fontVariant: ['tabular-nums'] },
  statBarWrap: { flex: 1, gap: 3, alignItems: 'center' },
  statBarBg: {
    height: 4, borderRadius: 2, width: '100%',
    flexDirection: 'row', overflow: 'hidden',
  },
  statBarSide: { height: '100%' },
  statLabel:   { fontSize: 10, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.3 },

  // Lineups
  lineupsWrap:       { paddingHorizontal: 12, paddingTop: 12 },
  selectorRow: {
    flexDirection: 'row',
    borderRadius: 10,
    borderWidth: 1,
    overflow: 'hidden',
    marginBottom: 12,
  },
  selectorPill: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 8,
    paddingHorizontal: 6,
    gap: 6,
  },
  pillCrest:          { width: 18, height: 18, borderRadius: 2 },
  selectorPillText:   { fontSize: 12, fontWeight: '700', flexShrink: 1 },
  formationRow:       { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  formationChip: {
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  formationText:  { fontSize: 13, fontWeight: '700' },
  predictedChip: {
    borderRadius: 6,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  predictedText:     { fontSize: 10, fontWeight: '700' },
  lineupsGroupLabel: {
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 4,
  },
  playerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 8,
  },
  jerseyBadge: {
    width: 28, height: 28, borderRadius: 14,
    alignItems: 'center', justifyContent: 'center',
  },
  jerseyNumber:   { fontSize: 11, fontWeight: '700' },
  posBadge: {
    width: 22, height: 22, borderRadius: 4,
    alignItems: 'center', justifyContent: 'center',
  },
  posText:        { fontSize: 9, fontWeight: '800' },
  playerName:     { flex: 1, fontSize: 13, fontWeight: '600' },
  aiScoreBadge: {
    borderRadius: 6,
    borderWidth: 1,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  aiScoreText:       { fontSize: 11, fontWeight: '700', fontVariant: ['tabular-nums'] },
  lineupsLoadingText: { fontSize: 13, marginTop: 8 },
  lineupsEmptyText:   { fontSize: 14, textAlign: 'center', marginTop: 8 },
});
