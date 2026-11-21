import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import Slider from '@react-native-community/slider';

import { useTheme } from '../context/ThemeContext';
import { useLanguage } from '../context/LanguageContext';
import { BasketballToggle } from '../components/basketball-toggle';
import { LeagueCatalog } from '../services/league-catalog';
import { createAlarm, getLineups, getTeamSquad } from '../services/soccer-service';
import type { SoccerMatch, SoccerAlarmType, MatchLineups, LineupPlayer } from '../types/soccer';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Alarm type definitions ─────────────────────────────────────────────────────

interface AlarmTypeEntry {
  type: SoccerAlarmType;
  icon: keyof typeof Ionicons.glyphMap;
  titleKey: string;
  subtitleKey: string;
}

const ALARM_TYPES: AlarmTypeEntry[] = [
  { type: 'drama_radar',   icon: 'radio-outline',         titleKey: 'soccer.alarm.dramaRadar',   subtitleKey: 'soccer.alarm.dramaRadarSub'   },
  { type: 'match_moments', icon: 'list-outline',          titleKey: 'soccer.alarm.matchMoments', subtitleKey: 'soccer.alarm.matchMomentsSub' },
  { type: 'late_drama',    icon: 'time-outline',          titleKey: 'soccer.alarm.lateDrama',    subtitleKey: 'soccer.alarm.lateDramaSub'    },
  { type: 'comeback',      icon: 'arrow-up-circle-outline', titleKey: 'soccer.alarm.comeback',   subtitleKey: 'soccer.alarm.comebackSub'     },
  { type: 'big_chance',    icon: 'flash-outline',         titleKey: 'soccer.alarm.bigChance',    subtitleKey: 'soccer.alarm.bigChanceSub'    },
  { type: 'sensation',     icon: 'star-outline',          titleKey: 'soccer.alarm.sensation',    subtitleKey: 'soccer.alarm.sensationSub'    },
  { type: 'score_gap',     icon: 'options-outline',       titleKey: 'soccer.alarm.scoreGap',     subtitleKey: 'soccer.alarm.scoreGapSub'     },
  { type: 'match',         icon: 'flag-outline',          titleKey: 'soccer.alarm.kickoff',      subtitleKey: 'soccer.alarm.kickoffSub'      },
  { type: 'knockout_et',   icon: 'trophy-outline',        titleKey: 'soccer.alarm.knockoutEt',   subtitleKey: 'soccer.alarm.knockoutEtSub'   },
  { type: 'card_penalty',  icon: 'alert-circle-outline',  titleKey: 'soccer.alarm.cardPenalty',  subtitleKey: 'soccer.alarm.cardPenaltySub'  },
];

// ── Position helpers ──────────────────────────────────────────────────────────

const POS_KEY: Record<string, string> = {
  g: 'goalkeeper', gk: 'goalkeeper',
  d: 'defender',   df: 'defender',
  m: 'midfielder', mf: 'midfielder',
  f: 'forward',    fw: 'forward',
};

function positionLabel(pos: string, t: (k: string) => string): string {
  const normalized = POS_KEY[pos.toLowerCase()] ?? pos.toLowerCase();
  return t(`player.pos.${normalized}`);
}

// ── Drama threshold helpers ────────────────────────────────────────────────────

function thresholdBand(value: number): 'mustWatch' | 'worthLooking' | 'quiet' {
  if (value >= 8) return 'mustWatch';
  if (value >= 5) return 'worthLooking';
  return 'quiet';
}

function bandLabelKey(band: ReturnType<typeof thresholdBand>): string {
  return `drama.band.${band}`;
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  match: SoccerMatch;
  visible: boolean;
  onClose: () => void;
}

type Tab = 'match' | 'player';


export function SoccerAlarmSheet({ match, visible, onClose }: Props) {
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();

  // Shared state
  const [tab, setTab]           = useState<Tab>('match');
  const [alertMode, setAlertMode] = useState<'alarm' | 'silent'>('alarm');
  const [isSaving, setIsSaving] = useState(false);
  const [didSave, setDidSave]   = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Match alarm state
  const [selectedType, setSelectedType] = useState<SoccerAlarmType>('late_drama');
  const [dramaThreshold, setDramaThreshold] = useState(7.0);
  const [scoreGapMax,  setScoreGapMax]  = useState(2);
  const [scoreGapFrom, setScoreGapFrom] = useState(45);
  // late_drama configurable
  const [ldMaxDiff,    setLdMaxDiff]    = useState(1);   // 0-3 goals
  const [ldLastMin,    setLdLastMin]    = useState(15);  // last N minutes: 15|30|45
  // match_moments
  const [mmKickoff,    setMmKickoff]    = useState(true);
  const [mmGoals,      setMmGoals]      = useState(true);
  const [mmHalftime,   setMmHalftime]   = useState(true);
  const [mmSecondHalf, setMmSecondHalf] = useState(true);
  const [mmRedCards,   setMmRedCards]   = useState(true);
  const [mmPenalties,  setMmPenalties]  = useState(true);
  const [mmMatchEnd,   setMmMatchEnd]   = useState(true);
  const [mmLineups,    setMmLineups]    = useState(false);
  const [mmReminder,   setMmReminder]   = useState(0);     // minutes before kickoff: 0=off
  // card_penalty
  const [cpRedCard,    setCpRedCard]    = useState(true);
  const [cpPenalty,    setCpPenalty]    = useState(true);
  // big_chance
  const [bcSensitivity, setBcSensitivity] = useState<'low' | 'medium' | 'high'>('medium');

  // Player alarm state
  const [lineups, setLineups]       = useState<MatchLineups | null>(null);
  const [lineupLoading, setLineupLoading] = useState(false);
  const [lineupError, setLineupError]     = useState<string | null>(null);
  const [lineupRetry, setLineupRetry]     = useState(0);
  const [selectedPlayer, setSelectedPlayer] = useState<LineupPlayer | null>(null);
  const [goalAlerts, setGoalAlerts]   = useState(true);
  const [clutchOnly, setClutchOnly]   = useState(false);
  const [hatTrickWatch, setHatTrickWatch] = useState(true);
  const [cardWatch, setCardWatch]     = useState(false);
  const [cardYellow, setCardYellow]   = useState(false);
  const [cardRed, setCardRed]         = useState(true);

  // Reset when opening + kick off background squad prefetch immediately
  useEffect(() => {
    if (visible) {
      setTab('match');
      setSelectedType('late_drama');
      setDramaThreshold(7.0);
      setAlertMode('alarm');
      setDidSave(false);
      setSaveError(null);
      setSelectedPlayer(null);
      setLineups(null);
      setLineupError(null);
      setLineupRetry(0);
      // Prefetch squads in background so Player tab is instant when user taps it
      getTeamSquad(match.home.id).catch(() => {});
      getTeamSquad(match.away.id).catch(() => {});
    }
  }, [visible]);

  // Load lineups when switching to player tab; fall back to WC squads for future matches
  useEffect(() => {
    if (tab !== 'player' || lineups || lineupLoading) return;
    let cancelled = false;

    setLineupLoading(true);
    setLineupError(null);

    const toLineupPlayers = (players: Awaited<ReturnType<typeof getTeamSquad>>): LineupPlayer[] =>
      players.map(p => ({
        id: p.player_id,
        name: p.name,
        position: p.position,
        jersey_number: p.jersey_number,
      }));

    (async () => {
      try {
        const lu = await getLineups(match.id);
        if (cancelled) return;
        if (lu.lineup_status !== 'unavailable' && lu.lineups) {
          setLineups(lu);
          setLineupLoading(false);
          return;
        }
      } catch {
        // HTTP error (e.g. future match) → fall through to squad
      }

      // Squad fallback
      try {
        const [homePlayers, awayPlayers] = await Promise.all([
          getTeamSquad(match.home.id),
          getTeamSquad(match.away.id),
        ]);
        if (cancelled) return;
        if (homePlayers.length === 0 && awayPlayers.length === 0) {
          setLineupError('not_found');
        } else {
          setLineups({
            lineup_status: 'predicted',
            lineups: {
              home: { team_id: parseInt(match.home.id) || 1, team_name: match.home.name, players: toLineupPlayers(homePlayers), substitutes: [] },
              away: { team_id: parseInt(match.away.id) || 2, team_name: match.away.name, players: toLineupPlayers(awayPlayers), substitutes: [] },
            },
          });
        }
      } catch {
        if (!cancelled) setLineupError('fetch_failed');
      }
      if (!cancelled) setLineupLoading(false);
    })();

    return () => { cancelled = true; };
  }, [tab, match.id, lineupRetry]);

  const saveAlarm = async () => {
    setSaveError(null);
    setIsSaving(true);
    try {
      const userToken = await AsyncStorage.getItem('nba_fcm_token');
      if (!userToken) throw new Error('No token');

      const matchName = `${match.home.name} vs ${match.away.name}`;

      if (tab === 'match') {
        let threshold: number | undefined;
        let alarmConfig: Record<string, boolean | string | number> | undefined;

        if (selectedType === 'drama_radar') {
          threshold = dramaThreshold;
        } else if (selectedType === 'score_gap') {
          threshold = scoreGapMax;
          alarmConfig = { from_minute: scoreGapFrom };
        } else if (selectedType === 'late_drama') {
          alarmConfig = { max_diff: ldMaxDiff, last_minutes: ldLastMin };
        } else if (selectedType === 'match_moments') {
          alarmConfig = {
            enabled: true,
            kickoff: mmKickoff, goals: mmGoals, halftime: mmHalftime,
            second_half: mmSecondHalf, red_cards: mmRedCards, penalties: mmPenalties,
            match_end: mmMatchEnd, lineups: mmLineups,
            reminder_minutes: mmReminder, alert_type: alertMode,
          };
        } else if (selectedType === 'card_penalty') {
          alarmConfig = { red_card: cpRedCard, penalty: cpPenalty };
        } else if (selectedType === 'big_chance') {
          alarmConfig = { sensitivity: bcSensitivity };
        }

        await createAlarm({
          userToken,
          alarmType:  selectedType,
          matchId:    match.id,
          matchName,
          threshold,
          config:     alarmConfig,
          alertType:  alertMode,
        });
      } else {
        if (!selectedPlayer) return;
        const config: Record<string, boolean | string> = {
          goal_alerts:   goalAlerts,
          clutch_only:   clutchOnly,
          hat_trick:     hatTrickWatch,
          card_watch:    cardWatch,
          card_yellow:   cardYellow,
          card_red:      cardRed,
          player_name:   selectedPlayer.name,
        };
        await createAlarm({
          userToken,
          alarmType: 'player_alert',
          matchId:   match.id,
          matchName,
          playerId:  String(selectedPlayer.id),
          alertType: alertMode,
          config,
        });
      }

      setDidSave(true);
      setTimeout(onClose, 800);
    } catch (e: any) {
      setSaveError(e.message ?? t('alarm.serverError'));
    } finally {
      setIsSaving(false);
    }
  };

  const league = LeagueCatalog.info(match.competition);
  const kickoffStr = match.kickoff
    ? new Date(match.kickoff).toLocaleString([], { hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' })
    : '';

  const band = thresholdBand(dramaThreshold);
  const bandColor =
    band === 'mustWatch'    ? colors.orange :
    band === 'worthLooking' ? colors.accentBlue :
                              colors.textMuted;

  const saveDisabled = tab === 'player' && !selectedPlayer;

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
          <TouchableOpacity onPress={onClose} style={styles.cancelBtn}>
            <Text style={[styles.cancelText, { color: colors.accentBlue }]}>
              {t('settings.cancel')}
            </Text>
          </TouchableOpacity>
          <Text style={[styles.navTitle, { color: colors.textPrimary }]}>
            {t('soccer.alarm.title')}
          </Text>
          <View style={{ width: 60 }} />
        </View>

        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.scroll}>
          {/* Match header */}
          <View style={[styles.matchHeader, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}>
            <Text style={[styles.leagueLabel, { color: colors.textMuted }]}>
              {league.label.toUpperCase()}
            </Text>
            <View style={styles.matchTeamsRow}>
              <View style={styles.matchTeam}>
                {match.home.crest
                  ? <Image source={{ uri: match.home.crest }} style={styles.matchCrest} contentFit="contain" />
                  : <View style={[styles.matchCrestPlaceholder, { backgroundColor: colors.border }]} />}
                <Text style={[styles.matchTeamName, { color: colors.textPrimary }]} numberOfLines={2}>{match.home.name}</Text>
              </View>
              <Text style={[styles.matchVs, { color: colors.textMuted }]}>vs</Text>
              <View style={styles.matchTeam}>
                {match.away.crest
                  ? <Image source={{ uri: match.away.crest }} style={styles.matchCrest} contentFit="contain" />
                  : <View style={[styles.matchCrestPlaceholder, { backgroundColor: colors.border }]} />}
                <Text style={[styles.matchTeamName, { color: colors.textPrimary }]} numberOfLines={2}>{match.away.name}</Text>
              </View>
            </View>
            {kickoffStr !== '' && (
              <Text style={[styles.kickoffText, { color: colors.textMuted }]}>{kickoffStr}</Text>
            )}
          </View>

          {/* Tab picker */}
          <View style={[styles.tabRow, { backgroundColor: colors.cardAlt, borderColor: colors.borderMuted }]}>
            {(['match', 'player'] as Tab[]).map(tabKey => (
              <TouchableOpacity
                key={tabKey}
                style={[
                  styles.tabBtn,
                  tab === tabKey && { backgroundColor: colors.orange },
                ]}
                onPress={() => setTab(tabKey)}
                activeOpacity={0.8}
              >
                <Text style={[styles.tabLabel, { color: tab === tabKey ? '#fff' : colors.textMuted }]}>
                  {t(`soccer.alarm.tab.${tabKey}`)}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Tab content */}
          {tab === 'match' ? (
            <MatchAlarmTab
              selectedType={selectedType}
              onSelectType={setSelectedType}
              dramaThreshold={dramaThreshold}  onChangeDrama={setDramaThreshold}
              scoreGapMax={scoreGapMax}        onChangeGapMax={setScoreGapMax}
              scoreGapFrom={scoreGapFrom}      onChangeGapFrom={setScoreGapFrom}
              ldMaxDiff={ldMaxDiff}            onChangeLdMaxDiff={setLdMaxDiff}
              ldLastMin={ldLastMin}            onChangeLdLastMin={setLdLastMin}
              mmKickoff={mmKickoff}            onMmKickoff={setMmKickoff}
              mmGoals={mmGoals}               onMmGoals={setMmGoals}
              mmHalftime={mmHalftime}         onMmHalftime={setMmHalftime}
              mmSecondHalf={mmSecondHalf}     onMmSecondHalf={setMmSecondHalf}
              mmRedCards={mmRedCards}         onMmRedCards={setMmRedCards}
              mmPenalties={mmPenalties}       onMmPenalties={setMmPenalties}
              mmMatchEnd={mmMatchEnd}         onMmMatchEnd={setMmMatchEnd}
              mmLineups={mmLineups}           onMmLineups={setMmLineups}
              mmReminder={mmReminder}         onMmReminder={setMmReminder}
              cpRedCard={cpRedCard}           onCpRedCard={setCpRedCard}
              cpPenalty={cpPenalty}           onCpPenalty={setCpPenalty}
              bcSensitivity={bcSensitivity}   onBcSensitivity={setBcSensitivity}
              band={band}
              bandColor={bandColor}
              colors={colors}
              t={t}
            />
          ) : (
            <PlayerAlarmTab
              lineups={lineups}
              loading={lineupLoading}
              error={lineupError}
              onRetry={() => { setLineups(null); setLineupError(null); setLineupRetry(r => r + 1); }}
              selectedPlayer={selectedPlayer}
              onSelectPlayer={setSelectedPlayer}
              goalAlerts={goalAlerts}
              setGoalAlerts={setGoalAlerts}
              clutchOnly={clutchOnly}
              setClutchOnly={setClutchOnly}
              hatTrickWatch={hatTrickWatch}
              setHatTrickWatch={setHatTrickWatch}
              cardWatch={cardWatch}
              setCardWatch={setCardWatch}
              cardYellow={cardYellow}
              setCardYellow={setCardYellow}
              cardRed={cardRed}
              setCardRed={setCardRed}
              alertMode={alertMode}
              setAlertMode={setAlertMode}
              onSave={saveAlarm}
              isSaving={isSaving}
              didSave={didSave}
              saveError={saveError}
              colors={colors}
              t={t}
            />
          )}

          {/* Alert mode + save — only shown for match tab */}
          {tab === 'match' && (
            <>
              <View style={styles.section}>
                <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>
                  {t('soccer.alarm.modeHeader')}
                </Text>
                <View style={[styles.segmented, { backgroundColor: colors.cardAlt, borderColor: colors.borderMuted }]}>
                  {(['alarm', 'silent'] as const).map(mode => (
                    <TouchableOpacity
                      key={mode}
                      style={[styles.segBtn, alertMode === mode && { backgroundColor: colors.orange }]}
                      onPress={() => setAlertMode(mode)}
                      activeOpacity={1}
                    >
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                        <Ionicons name={mode === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={13} color={alertMode === mode ? '#fff' : colors.textMuted} />
                        <Text style={[styles.segLabel, { color: alertMode === mode ? '#fff' : colors.textMuted }]}>
                          {mode === 'alarm' ? t('alarm.alarm') : t('alarm.silent')}
                        </Text>
                      </View>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>

              {saveError && (
                <Text style={[styles.errorText, { color: colors.red }]}>{saveError}</Text>
              )}

              <TouchableOpacity
                style={[
                  styles.saveBtn,
                  { backgroundColor: didSave ? colors.liveGreen : colors.orange },
                ]}
                onPress={saveAlarm}
                disabled={isSaving}
                activeOpacity={0.85}
              >
                {isSaving ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.saveBtnText}>
                    {didSave ? t('soccer.alarm.saved') : t('soccer.alarm.save')}
                  </Text>
                )}
              </TouchableOpacity>
            </>
          )}
        </ScrollView>
      </View>
    </Modal>
  );
}

// ── Match alarm tab ───────────────────────────────────────────────────────────

function MatchAlarmTab({
  selectedType, onSelectType,
  dramaThreshold, onChangeDrama,
  scoreGapMax, onChangeGapMax, scoreGapFrom, onChangeGapFrom,
  ldMaxDiff, onChangeLdMaxDiff, ldLastMin, onChangeLdLastMin,
  mmKickoff, onMmKickoff, mmGoals, onMmGoals,
  mmHalftime, onMmHalftime, mmSecondHalf, onMmSecondHalf,
  mmRedCards, onMmRedCards, mmPenalties, onMmPenalties,
  mmMatchEnd, onMmMatchEnd, mmLineups, onMmLineups,
  mmReminder, onMmReminder,
  cpRedCard, onCpRedCard, cpPenalty, onCpPenalty,
  bcSensitivity, onBcSensitivity,
  band, bandColor, colors, t,
}: {
  selectedType: SoccerAlarmType; onSelectType: (v: SoccerAlarmType) => void;
  dramaThreshold: number; onChangeDrama: (v: number) => void;
  scoreGapMax: number; onChangeGapMax: (v: number) => void;
  scoreGapFrom: number; onChangeGapFrom: (v: number) => void;
  ldMaxDiff: number; onChangeLdMaxDiff: (v: number) => void;
  ldLastMin: number; onChangeLdLastMin: (v: number) => void;
  mmKickoff: boolean; onMmKickoff: (v: boolean) => void;
  mmGoals: boolean; onMmGoals: (v: boolean) => void;
  mmHalftime: boolean; onMmHalftime: (v: boolean) => void;
  mmSecondHalf: boolean; onMmSecondHalf: (v: boolean) => void;
  mmRedCards: boolean; onMmRedCards: (v: boolean) => void;
  mmPenalties: boolean; onMmPenalties: (v: boolean) => void;
  mmMatchEnd: boolean; onMmMatchEnd: (v: boolean) => void;
  mmLineups: boolean; onMmLineups: (v: boolean) => void;
  mmReminder: number; onMmReminder: (v: number) => void;
  cpRedCard: boolean; onCpRedCard: (v: boolean) => void;
  cpPenalty: boolean; onCpPenalty: (v: boolean) => void;
  bcSensitivity: 'low' | 'medium' | 'high'; onBcSensitivity: (v: 'low' | 'medium' | 'high') => void;
  band: string; bandColor: string;
  colors: ReturnType<typeof useTheme>['colors']; t: (k: string) => string;
}) {
  return (
    <View style={styles.section}>
      <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>
        {t('soccer.alarm.typeHeader')}
      </Text>
      {ALARM_TYPES.map(entry => {
        const selected = selectedType === entry.type;
        return (
          <React.Fragment key={entry.type}>
            <TouchableOpacity
              style={[styles.alarmTypeRow, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}
              onPress={() => onSelectType(entry.type)}
              activeOpacity={0.7}
            >
              <Ionicons
                name={entry.icon}
                size={20}
                color={selected ? colors.orange : colors.textMuted}
                style={{ width: 24 }}
              />
              <View style={styles.alarmTypeText}>
                <Text style={[styles.alarmTypeTitle, { color: colors.textPrimary }]}>
                  {t(entry.titleKey)}
                </Text>
                <Text style={[styles.alarmTypeSub, { color: colors.textMuted }]}>
                  {t(entry.subtitleKey)}
                </Text>
              </View>
              {selected && (
                <Ionicons name="checkmark-circle" size={20} color={colors.orange} />
              )}
            </TouchableOpacity>

            {/* Inline config — appears directly below the selected row */}
            {selected && entry.type === 'drama_radar' && (
              <View style={[styles.dramaThresholdCard, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}>
                <View style={styles.dramaThresholdHeader}>
                  <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>
                    {t('drama.radar.threshold')}
                  </Text>
                  <Text style={[styles.thresholdValue, { color: bandColor }]}>
                    {dramaThreshold.toFixed(1)}
                  </Text>
                </View>
                <Slider
                  value={dramaThreshold}
                  minimumValue={5.0}
                  maximumValue={9.5}
                  step={0.5}
                  onValueChange={onChangeDrama}
                  minimumTrackTintColor={bandColor}
                  maximumTrackTintColor={colors.border}
                  thumbTintColor={bandColor}
                />
                <View style={styles.dramaRangeRow}>
                  <Text style={[styles.dramaRangeText, { color: colors.textMuted }]}>5.0</Text>
                  <Text style={[styles.drambandLabel, { color: bandColor }]}>
                    {t(bandLabelKey(band as any)).toUpperCase()}
                  </Text>
                  <Text style={[styles.dramaRangeText, { color: colors.textMuted }]}>9.5</Text>
                </View>
                <Text style={[styles.dramaSubText, { color: colors.textMuted }]}>
                  {t('drama.radar.subtitle')}
                </Text>
              </View>
            )}

            {selected && entry.type === 'score_gap' && (
              <View style={[styles.dramaThresholdCard, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}>
                <View style={styles.dramaThresholdHeader}>
                  <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>
                    {t('soccer.alarm.gapMax')}
                  </Text>
                  <Text style={[styles.thresholdValue, { color: colors.orange }]}>
                    {scoreGapMax === 0 ? '0' : `≤ ${scoreGapMax}`}
                  </Text>
                </View>
                <Slider
                  value={scoreGapMax}
                  minimumValue={0}
                  maximumValue={5}
                  step={1}
                  onValueChange={v => onChangeGapMax(Math.round(v))}
                  minimumTrackTintColor={colors.orange}
                  maximumTrackTintColor={colors.border}
                  thumbTintColor={colors.orange}
                />
                <View style={styles.dramaRangeRow}>
                  <Text style={[styles.dramaRangeText, { color: colors.textMuted }]}>0</Text>
                  <Text style={[styles.drambandLabel, { color: colors.orange }]}>
                    {scoreGapMax === 0 ? 'TIE ONLY' : scoreGapMax === 1 ? '1 GOAL' : `${scoreGapMax} GOALS`}
                  </Text>
                  <Text style={[styles.dramaRangeText, { color: colors.textMuted }]}>5</Text>
                </View>
                <Text style={[styles.sectionTitle, { color: colors.textMuted, marginTop: 12 }]}>
                  {t('soccer.alarm.gapFrom')}
                </Text>
                <View style={styles.minuteRow}>
                  {[25, 45, 65].map(min => (
                    <TouchableOpacity
                      key={min}
                      style={[styles.minuteBtn, { borderColor: colors.border }, scoreGapFrom === min && { backgroundColor: colors.orange, borderColor: colors.orange }]}
                      onPress={() => onChangeGapFrom(min)}
                    >
                      <Text style={[styles.minuteBtnTxt, { color: scoreGapFrom === min ? '#fff' : colors.textMuted }]}>
                        {min}'
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
            )}

            {/* ── late_drama configurable ── */}
            {selected && entry.type === 'late_drama' && (
              <View style={[styles.dramaThresholdCard, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}>
                <View style={styles.dramaThresholdHeader}>
                  <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>{t('soccer.alarm.ldMaxDiff')}</Text>
                  <Text style={[styles.thresholdValue, { color: colors.orange }]}>{ldMaxDiff === 0 ? t('soccer.alarm.tiedOnly') : `≤ ${ldMaxDiff}`}</Text>
                </View>
                <Slider value={ldMaxDiff} minimumValue={0} maximumValue={3} step={1}
                  onValueChange={v => onChangeLdMaxDiff(Math.round(v))}
                  minimumTrackTintColor={colors.orange} maximumTrackTintColor={colors.border} thumbTintColor={colors.orange} />
                <View style={styles.dramaRangeRow}>
                  <Text style={[styles.dramaRangeText, { color: colors.textMuted }]}>0</Text>
                  <Text style={[styles.drambandLabel, { color: colors.orange }]}>{['TIE', '1G', '2G', '3G'][ldMaxDiff]}</Text>
                  <Text style={[styles.dramaRangeText, { color: colors.textMuted }]}>3</Text>
                </View>
                <Text style={[styles.sectionTitle, { color: colors.textMuted, marginTop: 12 }]}>{t('soccer.alarm.ldLastMin')}</Text>
                <View style={styles.minuteRow}>
                  {[15, 30, 45].map(m => (
                    <TouchableOpacity key={m}
                      style={[styles.minuteBtn, { borderColor: colors.border }, ldLastMin === m && { backgroundColor: colors.orange, borderColor: colors.orange }]}
                      onPress={() => onChangeLdLastMin(m)}>
                      <Text style={[styles.minuteBtnTxt, { color: ldLastMin === m ? '#fff' : colors.textMuted }]}>Last {m}'</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
            )}

            {/* ── match_moments ── */}
            {selected && entry.type === 'match_moments' && (
              <View style={[styles.dramaThresholdCard, { backgroundColor: colors.cardAlt, borderColor: colors.border, gap: 2 }]}>
                {([
                  ['kickoff',     mmKickoff,    onMmKickoff,    'soccer.alarm.mm.kickoff'],
                  ['goals',       mmGoals,      onMmGoals,      'soccer.alarm.mm.goals'],
                  ['halftime',    mmHalftime,   onMmHalftime,   'soccer.alarm.mm.halftime'],
                  ['second_half', mmSecondHalf, onMmSecondHalf, 'soccer.alarm.mm.secondHalf'],
                  ['red_cards',   mmRedCards,   onMmRedCards,   'soccer.alarm.mm.redCards'],
                  ['penalties',   mmPenalties,  onMmPenalties,  'soccer.alarm.mm.penalties'],
                  ['match_end',   mmMatchEnd,   onMmMatchEnd,   'soccer.alarm.mm.matchEnd'],
                  ['lineups',     mmLineups,    onMmLineups,    'soccer.alarm.mm.lineups'],
                ] as [string, boolean, (v: boolean) => void, string][]).map(([key, val, setter, tk]) => (
                  <View key={key} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 6 }}>
                    <Text style={[{ flex: 1, fontSize: 14, fontWeight: '500', color: colors.textPrimary }]}>{t(tk)}</Text>
                    <BasketballToggle value={val} onValueChange={setter} emoji="⚽" />
                  </View>
                ))}
                <View style={[styles.dramaRangeRow, { marginTop: 10 }]}>
                  <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>{t('soccer.alarm.mm.reminder')}</Text>
                </View>
                <View style={styles.minuteRow}>
                  {[0, 15, 30, 60].map(m => (
                    <TouchableOpacity key={m}
                      style={[styles.minuteBtn, { borderColor: colors.border }, mmReminder === m && { backgroundColor: colors.orange, borderColor: colors.orange }]}
                      onPress={() => onMmReminder(m)}>
                      <Text style={[styles.minuteBtnTxt, { color: mmReminder === m ? '#fff' : colors.textMuted }]}>{m === 0 ? t('soccer.alarm.mm.noReminder') : `${m}'`}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
            )}

            {/* ── big_chance sensitivity ── */}
            {selected && entry.type === 'big_chance' && (
              <View style={[styles.dramaThresholdCard, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}>
                <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>{t('soccer.alarm.sensitivity')}</Text>
                <View style={[styles.minuteRow, { marginTop: 8 }]}>
                  {(['low', 'medium', 'high'] as const).map(s => (
                    <TouchableOpacity key={s}
                      style={[styles.minuteBtn, { borderColor: colors.border }, bcSensitivity === s && { backgroundColor: colors.orange, borderColor: colors.orange }]}
                      onPress={() => onBcSensitivity(s)}>
                      <Text style={[styles.minuteBtnTxt, { color: bcSensitivity === s ? '#fff' : colors.textMuted }]}>
                        {t(`soccer.alarm.sens.${s}`)}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
            )}

            {/* ── card_penalty ── */}
            {selected && entry.type === 'card_penalty' && (
              <View style={[styles.dramaThresholdCard, { backgroundColor: colors.cardAlt, borderColor: colors.border, gap: 4 }]}>
                {([
                  [cpRedCard, onCpRedCard, 'soccer.alarm.cp.redCard'],
                  [cpPenalty, onCpPenalty, 'soccer.alarm.cp.penalty'],
                ] as [boolean, (v: boolean) => void, string][]).map(([val, setter, tk], i) => (
                  <View key={i} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 8 }}>
                    <Text style={[{ flex: 1, fontSize: 14, fontWeight: '500', color: colors.textPrimary }]}>{t(tk)}</Text>
                    <BasketballToggle value={val} onValueChange={setter} emoji="⚽" />
                  </View>
                ))}
              </View>
            )}

          </React.Fragment>
        );
      })}
    </View>
  );
}

// ── Player alarm tab ──────────────────────────────────────────────────────────

function PlayerAlarmTab({
  lineups,
  loading,
  error,
  onRetry,
  selectedPlayer,
  onSelectPlayer,
  goalAlerts, setGoalAlerts,
  clutchOnly, setClutchOnly,
  hatTrickWatch, setHatTrickWatch,
  cardWatch, setCardWatch,
  cardYellow, setCardYellow,
  cardRed, setCardRed,
  alertMode, setAlertMode,
  onSave,
  isSaving,
  didSave,
  saveError,
  colors,
  t,
}: {
  lineups: MatchLineups | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  selectedPlayer: LineupPlayer | null;
  onSelectPlayer: (p: LineupPlayer | null) => void;
  goalAlerts: boolean; setGoalAlerts: (v: boolean) => void;
  clutchOnly: boolean; setClutchOnly: (v: boolean) => void;
  hatTrickWatch: boolean; setHatTrickWatch: (v: boolean) => void;
  cardWatch: boolean; setCardWatch: (v: boolean) => void;
  cardYellow: boolean; setCardYellow: (v: boolean) => void;
  cardRed: boolean; setCardRed: (v: boolean) => void;
  alertMode: 'alarm' | 'silent'; setAlertMode: (v: 'alarm' | 'silent') => void;
  onSave: () => void;
  isSaving: boolean;
  didSave: boolean;
  saveError: string | null;
  colors: ReturnType<typeof useTheme>['colors'];
  t: (k: string) => string;
}) {
  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.orange} />
        <Text style={[styles.loadingText, { color: colors.textMuted }]}>
          {t('soccer.alarm.loadingLineups')}
        </Text>
      </View>
    );
  }

  // Network/fetch error — show retry
  if (error && !lineups) {
    return (
      <View style={styles.centered}>
        <Ionicons name="cloud-offline-outline" size={32} color={colors.textMuted} />
        <Text style={[styles.loadingText, { color: colors.textMuted }]}>
          {t('soccer.alarm.lineupsUnavailable')}
        </Text>
        <TouchableOpacity
          style={[styles.retryBtn, { backgroundColor: colors.orange, marginTop: 8 }]}
          onPress={onRetry}
        >
          <Text style={styles.retryBtnText}>Try Again</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.centered}>
        <Ionicons name="wifi-outline" size={32} color={colors.textMuted} />
        <Text style={[styles.loadingText, { color: colors.textMuted }]}>{error}</Text>
      </View>
    );
  }

  const isUnavailable =
    !lineups ||
    lineups.lineup_status === 'unavailable' ||
    !lineups.lineups;

  if (isUnavailable) {
    return (
      <View style={[styles.unavailableCard, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}>
        <Ionicons name="person-circle-outline" size={24} color={colors.textMuted} />
        <Text style={[styles.alarmTypeTitle, { color: colors.textPrimary }]}>
          {t('soccer.alarm.lineupsUnavailable')}
        </Text>
      </View>
    );
  }

  const { home, away } = lineups.lineups!;

  return (
    <View style={styles.section}>
      <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>
        {t('soccer.alarm.pickPlayer')}
      </Text>

      {/* Player list — config expands inline right below the selected player */}
      <View style={[styles.playerListContainer, { borderColor: colors.border }]}>
        {[home, away].map(team => (
          <View key={team.team_id}>
            <View style={[styles.teamHeaderRow, { borderBottomColor: colors.border }]}>
              <Text style={[styles.teamSubheader, { color: colors.accentBlue }]}>
                {team.team_name.toUpperCase()}
              </Text>
            </View>
            {[...team.players, ...team.substitutes].map(player => {
              const selected = selectedPlayer?.id === player.id;
              return (
                <React.Fragment key={player.id}>
                  <TouchableOpacity
                    style={[
                      styles.playerRow,
                      selected && { backgroundColor: colors.orangeDim },
                    ]}
                    onPress={() => onSelectPlayer(selected ? null : player)}
                    activeOpacity={0.7}
                  >
                    <PlayerAvatar player={player} size={36} />
                    <Text style={[styles.playerName, { color: selected ? colors.orange : colors.textPrimary }]}>
                      {player.name}
                    </Text>
                    <Text style={[styles.playerPos, { color: colors.textMuted }]}>
                      {player.position ? positionLabel(player.position, t) : ''}
                    </Text>
                  </TouchableOpacity>

                  {/* Config — appears inline right below the selected player */}
                  {selected && (
                    <View style={{ backgroundColor: colors.cardAlt, paddingHorizontal: 12, paddingBottom: 12 }}>
                      <PlayerAlertConfig
                        goalAlerts={goalAlerts} setGoalAlerts={setGoalAlerts}
                        clutchOnly={clutchOnly} setClutchOnly={setClutchOnly}
                        hatTrickWatch={hatTrickWatch} setHatTrickWatch={setHatTrickWatch}
                        cardWatch={cardWatch} setCardWatch={setCardWatch}
                        cardYellow={cardYellow} setCardYellow={setCardYellow}
                        cardRed={cardRed} setCardRed={setCardRed}
                        colors={colors}
                        t={t}
                      />
                      <View style={[styles.alertTypeRow, { gap: 10, marginTop: 8 }]}>
                        {(['alarm', 'silent'] as const).map(mode => (
                          <TouchableOpacity
                            key={mode}
                            style={[
                              styles.alertTypeBtn,
                              { borderColor: alertMode === mode ? colors.red : colors.border, backgroundColor: alertMode === mode ? 'rgba(201,8,42,0.12)' : colors.bg },
                            ]}
                            onPress={() => setAlertMode(mode)}
                            activeOpacity={0.8}
                          >
                            <Ionicons name={mode === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={alertMode === mode ? colors.red : colors.textMuted} />
                            <Text style={[styles.alertTypeBtnText, { color: alertMode === mode ? colors.red : colors.textMuted }]}>
                              {mode === 'alarm' ? t('alarm.alarm') : t('alarm.silent')}
                            </Text>
                          </TouchableOpacity>
                        ))}
                      </View>
                      {saveError && (
                        <Text style={[styles.errorText, { color: colors.red, marginTop: 6 }]}>{saveError}</Text>
                      )}
                      <TouchableOpacity
                        style={[styles.saveBtn, { backgroundColor: didSave ? colors.liveGreen : colors.orange, marginTop: 10 }]}
                        onPress={onSave}
                        disabled={isSaving}
                        activeOpacity={0.85}
                      >
                        {isSaving ? (
                          <ActivityIndicator color="#fff" />
                        ) : (
                          <Text style={styles.saveBtnText}>
                            {didSave ? t('soccer.alarm.saved') : t('soccer.alarm.save')}
                          </Text>
                        )}
                      </TouchableOpacity>
                    </View>
                  )}
                </React.Fragment>
              );
            })}
          </View>
        ))}
      </View>
    </View>
  );
}

// ── Player alert config ───────────────────────────────────────────────────────

function PlayerAlertConfig({
  goalAlerts, setGoalAlerts,
  clutchOnly, setClutchOnly,
  hatTrickWatch, setHatTrickWatch,
  cardWatch, setCardWatch,
  cardYellow, setCardYellow,
  cardRed, setCardRed,
  colors,
  t,
}: {
  goalAlerts: boolean; setGoalAlerts: (v: boolean) => void;
  clutchOnly: boolean; setClutchOnly: (v: boolean) => void;
  hatTrickWatch: boolean; setHatTrickWatch: (v: boolean) => void;
  cardWatch: boolean; setCardWatch: (v: boolean) => void;
  cardYellow: boolean; setCardYellow: (v: boolean) => void;
  cardRed: boolean; setCardRed: (v: boolean) => void;
  colors: ReturnType<typeof useTheme>['colors'];
  t: (k: string) => string;
}) {
  return (
    <>
      {/* Goal alerts card */}
      <View style={[styles.conditionCard, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}>
        <View style={styles.conditionCardHeader}>
          <View style={{ flex: 1 }}>
            <Text style={[styles.conditionCardTitle, { color: colors.textPrimary }]}>{t('soccer.alarm.goals')}</Text>
            <Text style={[styles.conditionCardSub, { color: colors.textMuted }]}>{t('soccer.alarm.goalsSub')}</Text>
          </View>
          <BasketballToggle value={goalAlerts} onValueChange={setGoalAlerts} emoji="⚽" />
        </View>
        {goalAlerts && (
          <View style={[styles.segmented, { backgroundColor: colors.bg, borderColor: colors.borderMuted, marginTop: 10 }]}>
            {(['all', 'clutch'] as const).map(mode => {
              const isClutch = mode === 'clutch';
              const active = isClutch ? clutchOnly : !clutchOnly;
              return (
                <TouchableOpacity
                  key={mode}
                  style={[styles.segBtn, active && { backgroundColor: colors.orange }, { paddingVertical: 10, gap: 2 }]}
                  onPress={() => setClutchOnly(isClutch)}
                >
                  <Text style={[styles.segLabel, { color: active ? '#fff' : colors.textMuted }]}>
                    {isClutch ? t('soccer.alarm.clutchOnly') : t('soccer.alarm.allGoals')}
                  </Text>
                  <Text style={{ fontSize: 10, color: active ? 'rgba(255,255,255,0.75)' : colors.textMuted, textAlign: 'center', lineHeight: 13 }}>
                    {isClutch ? t('soccer.alarm.clutchOnlySub') : t('soccer.alarm.allGoalsSub')}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        )}
      </View>

      {/* Hat-trick card */}
      <View style={[styles.conditionCard, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}>
        <View style={styles.conditionCardHeader}>
          <View style={{ flex: 1 }}>
            <Text style={[styles.conditionCardTitle, { color: colors.textPrimary }]}>{t('soccer.alarm.hatTrickWatch')}</Text>
            <Text style={[styles.conditionCardSub, { color: colors.textMuted }]}>{t('soccer.alarm.hatTrickWatchSub')}</Text>
          </View>
          <BasketballToggle value={hatTrickWatch} onValueChange={setHatTrickWatch} emoji="⚽" />
        </View>
      </View>

      {/* Card watch card */}
      <View style={[styles.conditionCard, { backgroundColor: colors.cardAlt, borderColor: colors.border }]}>
        <View style={styles.conditionCardHeader}>
          <View style={{ flex: 1 }}>
            <Text style={[styles.conditionCardTitle, { color: colors.textPrimary }]}>{t('soccer.alarm.cardWatch')}</Text>
          </View>
          <BasketballToggle value={cardWatch} onValueChange={setCardWatch} emoji="⚽" />
        </View>
        {cardWatch && (
          <View style={[styles.cardChips, { marginTop: 10 }]}>
            <TouchableOpacity
              style={[styles.chip, cardYellow && { backgroundColor: '#F59E0B22', borderColor: '#F59E0B' }]}
              onPress={() => setCardYellow(!cardYellow)}
            >
              <Text style={[styles.chipText, { color: cardYellow ? '#F59E0B' : colors.textMuted }]}>
                🟨 {t('soccer.alarm.yellow')}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.chip, cardRed && { backgroundColor: colors.red + '22', borderColor: colors.red }]}
              onPress={() => setCardRed(!cardRed)}
            >
              <Text style={[styles.chipText, { color: cardRed ? colors.red : colors.textMuted }]}>
                🟥 {t('soccer.alarm.red')}
              </Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    </>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

// ── Shared player avatar ──────────────────────────────────────────────────────

function PlayerAvatar({ player, size = 36 }: { player: LineupPlayer; size?: number }) {
  const [imgError, setImgError] = useState(false);
  const showPhoto = !!player.photo_url && !imgError;
  return (
    <View style={{
      width: size, height: size, borderRadius: size / 2,
      overflow: 'hidden', backgroundColor: '#2e7d3220',
      borderWidth: 1, borderColor: 'rgba(120,120,120,0.25)',
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
        <Text style={{ fontSize: size * 0.3, fontWeight: '800', color: '#888' }}>
          {player.jersey_number != null ? String(player.jersey_number) : '?'}
        </Text>
      )}
    </View>
  );
}

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
  cancelBtn: { minWidth: 60 },
  cancelText: { fontSize: 16 },
  navTitle: { fontSize: 16, fontWeight: '700' },

  scroll: { padding: 16, gap: 16, paddingBottom: 48 },

  matchHeader: {
    borderRadius: 12,
    borderWidth: 1,
    padding: 16,
    alignItems: 'center',
    gap: 8,
  },
  leagueLabel: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 2,
    textTransform: 'uppercase',
  },
  matchTeamsRow:       { flexDirection: 'row', alignItems: 'center', gap: 8, width: '100%' },
  matchTeam:           { flex: 1, alignItems: 'center', gap: 6 },
  matchCrest:          { width: 48, height: 48 },
  matchCrestPlaceholder: { width: 48, height: 48, borderRadius: 24 },
  matchTeamName:       { fontSize: 13, fontWeight: '700', textAlign: 'center' },
  matchVs:             { fontSize: 12, fontWeight: '600', paddingHorizontal: 4 },
  kickoffText: { fontSize: 12 },

  tabRow: {
    flexDirection: 'row',
    borderRadius: 10,
    borderWidth: 1,
    overflow: 'hidden',
  },
  tabBtn: {
    flex: 1,
    paddingVertical: 9,
    alignItems: 'center',
  },
  tabLabel: { fontSize: 13, fontWeight: '600' },

  section: { gap: 8 },
  sectionTitle: {
    fontSize: 11,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },

  alarmTypeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 12,
    borderRadius: 10,
    borderWidth: 1,
  },
  alarmTypeText: { flex: 1 },
  alarmTypeTitle: { fontSize: 14, fontWeight: '600' },
  alarmTypeSub:   { fontSize: 11, marginTop: 2 },

  dramaThresholdCard: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    gap: 8,
  },
  dramaThresholdHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  thresholdValue: { fontSize: 18, fontWeight: '800', fontVariant: ['tabular-nums'] },
  dramaRangeRow:  { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  minuteRow:      { flexDirection: 'row', gap: 10, marginTop: 8 },
  minuteBtn:      { flex: 1, paddingVertical: 10, borderRadius: 10, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  minuteBtnTxt:   { fontSize: 15, fontWeight: '700' },
  dramaRangeText: { fontSize: 10 },
  drambandLabel:  { fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  dramaSubText:   { fontSize: 11 },

  segmented: {
    flexDirection: 'row',
    borderRadius: 10,
    borderWidth: 1,
    overflow: 'hidden',
  },
  segBtn:   { flex: 1, paddingVertical: 9, alignItems: 'center' },
  segLabel: { fontSize: 13, fontWeight: '600' },

  errorText: { fontSize: 12, textAlign: 'center' },
  retryBtn: { paddingHorizontal: 24, paddingVertical: 10, borderRadius: 10 },
  retryBtnText: { color: '#fff', fontSize: 14, fontWeight: '700' },

  saveBtn: {
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: 'center',
  },
  saveBtnText: { color: '#fff', fontSize: 15, fontWeight: '800' },

  centered: {
    paddingVertical: 40,
    alignItems: 'center',
    gap: 12,
  },
  loadingText: { fontSize: 13 },

  unavailableCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 14,
    borderRadius: 10,
    borderWidth: 1,
  },

  playerListContainer: {
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    overflow: 'hidden',
  },
  teamHeaderRow: {
    paddingVertical: 6,
    paddingHorizontal: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  teamSubheader: {
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.5,
  },
  playerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 8,
    paddingHorizontal: 8,
    borderRadius: 0,
  },
  jerseyNum:  { fontSize: 12, fontWeight: '600' },
  playerName: { flex: 1, fontSize: 14, fontWeight: '600' },
  playerPos:  { fontSize: 12, width: 60, textAlign: 'right' },

  selectedHeader: {
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginTop: 4,
    marginBottom: 4,
  },
  selectedHeaderText: { fontSize: 15, fontWeight: '800' },

  conditionCard: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 12,
    marginBottom: 10,
  },
  conditionCardHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  conditionCardTitle:  { fontSize: 14, fontWeight: '700' },
  conditionCardSub:    { fontSize: 12, marginTop: 2 },

  alertTypeRow: { flexDirection: 'row' },
  alertTypeBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 9,
    borderRadius: 10,
    borderWidth: 1,
  },
  alertTypeBtnText: { fontSize: 12, fontWeight: '700' },

  cardChips: { flexDirection: 'row', gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'transparent',
    backgroundColor: 'transparent',
  },
  chipText: { fontSize: 13, fontWeight: '600' },
});
