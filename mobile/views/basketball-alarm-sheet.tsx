import React, { useMemo, useState } from 'react';
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
import Slider from '@react-native-community/slider';
import * as Haptics from 'expo-haptics';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useTheme, type AppColors } from '../context/ThemeContext';
import { useLanguage } from '../context/LanguageContext';
import { BasketballToggle } from '../components/basketball-toggle';
import { TeamBadge } from '../components/TeamBadge';
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
  onAlarmSet?: () => void;
}

const ALARM_CHANNEL = 'classic_alarm';

export function BasketballAlarmSheet({ game, leagueId, yyyymmdd, visible, onClose, onAlarmSet }: Props) {
  const { colors: C } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const styles = useMemo(() => makeStyles(C), [C]);

  // ── Shared state ────────────────────────────────────────────────────────────
  const [alarmTab, setAlarmTab]           = useState<'game' | 'player'>('game');
  const [alertType, setAlertType]         = useState<'alarm' | 'silent'>('alarm');
  const [saving, setSaving]               = useState(false);
  const [saveError, setSaveError]         = useState<string | null>(null);

  // ── Game alarm state ────────────────────────────────────────────────────────
  const [noCondition, setNoCondition]     = useState(false);
  const [maxDiff, setMaxDiff]             = useState(8);
  const [startQuarter, setStartQuarter]   = useState('3');

  // ── Player alarm state ──────────────────────────────────────────────────────
  const [teams, setTeams]                     = useState<any[]>([]);
  const [rosterLoading, setRosterLoading]     = useState(false);
  const [selectedPlayer, setSelectedPlayer]   = useState<any>(null);
  const [ptsGoalEnabled, setPtsGoalEnabled]   = useState(true);
  const [ptsGoalThreshold, setPtsGoalThreshold] = useState(25);
  const [ptsEarlyEnabled, setPtsEarlyEnabled] = useState(false);
  const [earlyPtsThreshold, setEarlyPtsThreshold] = useState(15);
  const [earlyQuarter, setEarlyQuarter]       = useState('3');
  const [tdWatchEnabled, setTdWatchEnabled]   = useState(false);


  const loadRoster = async () => {
    const TEAMS_CACHE_KEY = 'all_teams_cache_v1';

    const applyTeams = (allTeams: any[]) => {
      const lg = leagueId.toLowerCase();
      const findTeam = (abbr: string) => {
        const a = abbr.toUpperCase();
        return (
          allTeams.find(t => t.abbreviation?.toUpperCase() === a &&
                             t.league?.toLowerCase() === lg) ??
          allTeams.find(t => t.abbreviation?.toUpperCase() === a) ??
          null
        );
      };
      const away = findTeam(game.awayTeam);
      const home = findTeam(game.homeTeam);
      const found = [away, home].filter(Boolean);
      if (found.length > 0) setTeams(found);
    };

    // Show cached data instantly, then refresh in background
    try {
      const raw = await AsyncStorage.getItem(TEAMS_CACHE_KEY);
      if (raw) {
        applyTeams(JSON.parse(raw));
        setRosterLoading(false);
      }
    } catch {}

    try {
      const resp = await fetch(`${SERVER_URL}/all-teams-and-players`);
      if (resp.ok) {
        const data = await resp.json();
        const allTeams: any[] = data.teams || [];
        applyTeams(allTeams);
        AsyncStorage.setItem(TEAMS_CACHE_KEY, JSON.stringify(allTeams)).catch(() => {});
      }
    } catch {}
    setRosterLoading(false);
  };

  const resetState = () => {
    setAlarmTab('game');
    setAlertType('alarm');
    setNoCondition(false);
    setMaxDiff(8);
    setStartQuarter('3');
    setTeams([]);
    setRosterLoading(false);
    setSelectedPlayer(null);
    setPtsGoalEnabled(true);
    setPtsGoalThreshold(25);
    setPtsEarlyEnabled(false);
    setEarlyPtsThreshold(15);
    setEarlyQuarter('3');
    setTdWatchEnabled(false);
    setSaving(false);
    setSaveError(null);
  };

  const handleClose = () => {
    resetState();
    onClose();
  };

  // ── Game alarm submit ───────────────────────────────────────────────────────
  const handleSetGameAlarm = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const token = await AsyncStorage.getItem('nba_fcm_token').catch(() => null);
      if (!token) { setSaveError('Push token unavailable. Restart the app.'); setSaving(false); return; }
      const resp = await fetch(`${SERVER_URL}/start-alarm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          game_id:         game.id,
          game_name:       `${game.awayTeam} at ${game.homeTeam}`,
          league:          leagueId,
          max_diff:        maxDiff,
          start_quarter:   parseInt(startQuarter, 10),
          notify_at_start: noCondition,
          push_token:      token,
          sound_channel:   ALARM_CHANNEL,
          alert_type:      alertType,
          home_team_id:    game.homeTeam,
          away_team_id:    game.awayTeam,
          game_date:       yyyymmdd,
        }),
      });
      if (resp.ok) {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        onAlarmSet?.();
        handleClose();
      } else {
        const msg = await resp.text().catch(() => '');
        setSaveError(msg || `Server error ${resp.status}`);
        setSaving(false);
      }
    } catch (e: any) {
      setSaveError(e?.message ?? 'Could not reach server');
      setSaving(false);
    }
  };

  // ── Player alarm submit ─────────────────────────────────────────────────────
  const handleSetPlayerAlarm = async () => {
    if (!selectedPlayer) return;
    if (!ptsGoalEnabled && !ptsEarlyEnabled && !tdWatchEnabled) return;
    setSaving(true);
    setSaveError(null);
    try {
      const token = await AsyncStorage.getItem('nba_fcm_token').catch(() => null);
      if (!token) { setSaveError('Push token unavailable. Restart the app.'); setSaving(false); return; }

      const conditions: Array<{ condition: string; pts_threshold: number; quarter_deadline: number }> = [];
      if (ptsGoalEnabled) conditions.push({ condition: 'pts_goal', pts_threshold: ptsGoalThreshold, quarter_deadline: 0 });
      if (ptsEarlyEnabled) conditions.push({ condition: 'pts_early', pts_threshold: earlyPtsThreshold, quarter_deadline: parseInt(earlyQuarter, 10) });
      if (tdWatchEnabled) conditions.push({ condition: 'triple_double', pts_threshold: 0, quarter_deadline: 0 });

      const results = await Promise.all(conditions.map(cond =>
        fetch(`${SERVER_URL}/start-player-alarm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            game_id:          game.id,
            game_name:        `${game.awayTeam} at ${game.homeTeam}`,
            league:           leagueId,
            player_id:        selectedPlayer.id,
            player_name:      selectedPlayer.name,
            push_token:       token,
            sound_channel:    ALARM_CHANNEL,
            condition:        cond.condition,
            pts_threshold:    cond.pts_threshold,
            quarter_deadline: cond.quarter_deadline,
            alert_type:       alertType,
            game_date:        yyyymmdd,
          }),
        })
      ));
      const failed = results.find(r => !r.ok);
      if (failed) {
        const msg = await failed.text().catch(() => '');
        setSaveError(msg || `Server error ${failed.status}`);
        setSaving(false);
        return;
      }
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      onAlarmSet?.();
      handleClose();
    } catch (e: any) {
      setSaveError(e?.message ?? 'Could not reach server');
      setSaving(false);
    }
  };

  const isLive = game.status === 'in';

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={handleClose}>
      <View style={styles.overlay}>
        <View style={[styles.sheet, { paddingBottom: Math.max(insets.bottom, 12) }]}>
          <ScrollView
            showsVerticalScrollIndicator={false}
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={{ paddingBottom: 8 }}
          >
            {/* Header */}
            <View style={styles.headerRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.subtitle}>{t('alarm.setAlarmFor')}</Text>
                <Text style={styles.gameName}>{game.awayTeam} at {game.homeTeam}</Text>
              </View>
              <TouchableOpacity onPress={handleClose} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

            {/* Team crests + live score */}
            <View style={styles.teamsRow}>
              <View style={styles.teamCol}>
                <TeamBadge abbreviation={game.awayTeam} size={44} uri={game.awayLogo} />
                <Text style={styles.teamName}>{game.awayTeam}</Text>
                {isLive && game.awayScore != null && <Text style={styles.score}>{game.awayScore}</Text>}
              </View>
              <View style={styles.vsCol}>
                {isLive && game.period && game.displayClock ? (
                  <>
                    <Text style={styles.quarter}>Q{game.period}</Text>
                    <Text style={styles.clock}>{game.displayClock}</Text>
                  </>
                ) : game.kickoff ? (
                  <Text style={styles.kickoffTime}>
                    {new Date(game.kickoff).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </Text>
                ) : (
                  <Text style={styles.vs}>vs</Text>
                )}
              </View>
              <View style={styles.teamCol}>
                <TeamBadge abbreviation={game.homeTeam} size={44} uri={game.homeLogo} />
                <Text style={styles.teamName}>{game.homeTeam}</Text>
                {isLive && game.homeScore != null && <Text style={styles.score}>{game.homeScore}</Text>}
              </View>
            </View>

            {/* Alert type toggle */}
            <View style={styles.alertTypeRow}>
              {(['alarm', 'silent'] as const).map(type => (
                <TouchableOpacity
                  key={type}
                  activeOpacity={1}
                  style={[styles.alertTypeBtn, alertType === type && styles.alertTypeBtnActive]}
                  onPress={() => setAlertType(type)}
                >
                  <Ionicons name={type === 'alarm' ? 'notifications-outline' : 'notifications-off-outline'} size={14} color={C.textMuted} />
                  <Text style={[styles.alertTypeText, alertType === type && styles.alertTypeTextActive]}>
                    {type === 'alarm' ? t('alarm.alarm') : t('alarm.silent')}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            {/* Game / Player tab switcher */}
            <View style={styles.tabs}>
              {(['game', 'player'] as const).map(tab => (
                <TouchableOpacity
                  key={tab}
                  style={[styles.tab, alarmTab === tab && styles.tabActive]}
                  onPress={() => {
                    setAlarmTab(tab);
                    if (tab === 'player' && teams.length === 0 && !rosterLoading) {
                      loadRoster();
                    }
                  }}
                >
                  <Text style={[styles.tabText, alarmTab === tab && styles.tabTextActive]}>
                    {tab === 'game' ? t('alarm.gameAlarm') : t('alarm.playerAlarm')}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            {/* ── Game Alarm tab ─────────────────────────────────────────── */}
            {alarmTab === 'game' && (
              <>
                <View style={styles.noCondRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.inputLabel}>{t('alarm.anyScore')}</Text>
                    <Text style={styles.noCondSub}>{t('alarm.anyScoreSub')}</Text>
                  </View>
                  <BasketballToggle value={noCondition} onValueChange={setNoCondition} />
                </View>

                {!noCondition && (
                  <>
                    <View style={styles.sliderRow}>
                      <Text style={styles.inputLabel}>{t('alarm.maxPointDiff')}</Text>
                      <Text style={styles.sliderValue}>{maxDiff}</Text>
                    </View>
                    <Slider
                      style={styles.slider}
                      minimumValue={1} maximumValue={25} step={1}
                      value={maxDiff} onValueChange={setMaxDiff}
                      minimumTrackTintColor={C.orange}
                      maximumTrackTintColor={C.border}
                      thumbTintColor={C.orange}
                    />
                    <Text style={[styles.inputLabel, { marginTop: 4 }]}>{t('alarm.startFrom')}</Text>
                    <View style={styles.quarterRow}>
                      {['1', '2', '3', '4', '5'].map(q => (
                        <TouchableOpacity
                          key={q}
                          style={[styles.quarterBtn, startQuarter === q && styles.quarterBtnActive]}
                          onPress={() => setStartQuarter(q)}
                        >
                          <Text style={[styles.quarterText, startQuarter === q && styles.quarterTextActive]}>
                            {q === '5' ? "Last 5'" : `Q${q}`}
                          </Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  </>
                )}

                {saveError && <Text style={styles.saveError}>{saveError}</Text>}
                <TouchableOpacity style={[styles.setBtn, saving && styles.setBtnDisabled]} onPress={handleSetGameAlarm} disabled={saving}>
                  {saving
                    ? <ActivityIndicator color="#fff" />
                    : <Text style={styles.setBtnText}>{t('alarm.setAlarm')}</Text>
                  }
                </TouchableOpacity>
              </>
            )}

            {/* ── Player Alarm tab ───────────────────────────────────────── */}
            {alarmTab === 'player' && (
              <>
                {rosterLoading ? (
                  <View style={styles.rosterLoading}>
                    <ActivityIndicator color={C.accentBlue} />
                    <Text style={styles.rosterLoadingText}>{t('alarm.selectPlayer')}</Text>
                  </View>
                ) : teams.length === 0 ? (
                  <View style={styles.rosterLoading}>
                    <Text style={styles.rosterLoadingText}>{t('alarm.noRoster')}</Text>
                    <TouchableOpacity onPress={loadRoster} style={{ marginTop: 8, padding: 8 }}>
                      <Text style={[styles.rosterLoadingText, { color: C.accentBlue }]}>Retry</Text>
                    </TouchableOpacity>
                  </View>
                ) : (
                  <ScrollView style={styles.playerList} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 12 }} nestedScrollEnabled>
                    {teams.map(team => {
                      const abbr = (team.abbreviation ?? '').toUpperCase();
                      const logoUri =
                        abbr === game.homeTeam.toUpperCase() ? game.homeLogo :
                        abbr === game.awayTeam.toUpperCase() ? game.awayLogo :
                        undefined;
                      return (
                      <View key={team.id}>
                        <View style={styles.teamHeader}>
                          <TeamBadge abbreviation={team.abbreviation ?? ''} size={20} uri={logoUri} />
                          <Text style={styles.teamHeaderName}>{team.abbreviation}</Text>
                        </View>
                        {team.players.map((player: any) => {
                          const isSel = selectedPlayer?.id === player.id;
                          return (
                            <TouchableOpacity
                              key={player.id}
                              style={[styles.playerRow, isSel && styles.playerRowSelected]}
                              onPress={() => setSelectedPlayer(isSel ? null : player)}
                            >
                              <Text style={styles.playerJersey}>{player.jersey ? `#${player.jersey}` : '  '}</Text>
                              <Text style={[styles.playerName, isSel && styles.playerNameSelected]}>{player.name}</Text>
                              <Text style={styles.playerPos}>{player.position}</Text>
                            </TouchableOpacity>
                          );
                        })}
                      </View>
                      );
                    })}
                  </ScrollView>
                )}

                {selectedPlayer && (
                  <>
                    <View style={styles.selectedPlayerHeader}>
                      <Text style={styles.selectedPlayerLabel}>{selectedPlayer.name}</Text>
                    </View>

                    <Text style={styles.inputLabel}>{t('alarm.playerAlarm')}</Text>
                    <Text style={[styles.conditionHint, { marginTop: 4 }]}>{t('settings.playerCondSub')}</Text>

                    {/* Points Goal */}
                    <View style={styles.conditionCard}>
                      <View style={styles.conditionCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.conditionCardTitle}>{t('alarm.ptsGoal')}</Text>
                          <Text style={styles.conditionCardSub}>{t('alarm.ptsGoalSub')}</Text>
                        </View>
                        <BasketballToggle value={ptsGoalEnabled} onValueChange={setPtsGoalEnabled} />
                      </View>
                      {ptsGoalEnabled && (
                        <>
                          <View style={[styles.sliderRow, { marginTop: 10 }]}>
                            <Text style={styles.inputLabel}>{t('alarm.ptsThreshold')}</Text>
                            <Text style={styles.sliderValue}>{ptsGoalThreshold}</Text>
                          </View>
                          <Slider
                            style={styles.slider}
                            minimumValue={1} maximumValue={80} step={1}
                            value={ptsGoalThreshold} onValueChange={setPtsGoalThreshold}
                            minimumTrackTintColor={C.orange}
                            maximumTrackTintColor={C.border}
                            thumbTintColor={C.orange}
                          />
                        </>
                      )}
                    </View>

                    {/* Early Points */}
                    <View style={styles.conditionCard}>
                      <View style={styles.conditionCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.conditionCardTitle}>{t('alarm.earlyPts')}</Text>
                          <Text style={styles.conditionCardSub}>{t('alarm.earlyPtsSub')}</Text>
                        </View>
                        <BasketballToggle value={ptsEarlyEnabled} onValueChange={setPtsEarlyEnabled} />
                      </View>
                      {ptsEarlyEnabled && (
                        <>
                          <View style={[styles.sliderRow, { marginTop: 10 }]}>
                            <Text style={styles.inputLabel}>{t('alarm.ptsThreshold')}</Text>
                            <Text style={styles.sliderValue}>{earlyPtsThreshold}</Text>
                          </View>
                          <Slider
                            style={styles.slider}
                            minimumValue={1} maximumValue={80} step={1}
                            value={earlyPtsThreshold} onValueChange={setEarlyPtsThreshold}
                            minimumTrackTintColor={C.orange}
                            maximumTrackTintColor={C.border}
                            thumbTintColor={C.orange}
                          />
                          <Text style={[styles.inputLabel, { marginTop: 4 }]}>{t('alarm.byEndOfQ')}</Text>
                          <View style={styles.miniQuarterRow}>
                            {['1', '2', '3'].map(q => (
                              <TouchableOpacity
                                key={q}
                                style={[styles.miniQBtn, earlyQuarter === q && styles.quarterBtnActive]}
                                onPress={() => setEarlyQuarter(q)}
                              >
                                <Text style={[styles.quarterText, earlyQuarter === q && styles.quarterTextActive]}>Q{q}</Text>
                              </TouchableOpacity>
                            ))}
                          </View>
                        </>
                      )}
                    </View>

                    {/* Triple-Double Watch */}
                    <View style={styles.conditionCard}>
                      <View style={styles.conditionCardHeader}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.conditionCardTitle}>{t('alarm.tripleDouble')}</Text>
                          <Text style={styles.conditionCardSub}>{t('alarm.tripleDoubleSub')}</Text>
                        </View>
                        <BasketballToggle value={tdWatchEnabled} onValueChange={setTdWatchEnabled} />
                      </View>
                    </View>

                    {saveError && <Text style={styles.saveError}>{saveError}</Text>}
                    <TouchableOpacity style={[styles.setBtn, saving && styles.setBtnDisabled]} onPress={handleSetPlayerAlarm} disabled={saving}>
                      {saving
                        ? <ActivityIndicator color="#fff" />
                        : <Text style={styles.setBtnText}>{t('alarm.setAlarm')}</Text>
                      }
                    </TouchableOpacity>
                  </>
                )}
              </>
            )}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const makeStyles = (C: AppColors) => StyleSheet.create({
  overlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0,0,0,0.75)',
  },
  sheet: {
    backgroundColor: C.card,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    borderWidth: 1,
    borderColor: C.border,
    paddingHorizontal: 20,
    paddingTop: 20,
    maxHeight: '92%',
  },

  headerRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 16,
  },
  subtitle:  { fontSize: 13, color: C.textMuted, marginBottom: 2 },
  gameName:  { fontSize: 18, fontWeight: '800', color: C.textPrimary },
  closeBtn:  { padding: 4, marginTop: 2 },

  teamsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 20,
    paddingHorizontal: 8,
  },
  teamCol: { flex: 1, alignItems: 'center', gap: 6 },
  teamName: { fontSize: 13, fontWeight: '700', color: C.textPrimary, textAlign: 'center' },
  score:    { fontSize: 22, fontWeight: '900', color: C.textPrimary, fontVariant: ['tabular-nums'] },
  vsCol:    { width: 64, alignItems: 'center' },
  vs:       { fontSize: 16, fontWeight: '600', color: C.textMuted },
  quarter:  { fontSize: 14, fontWeight: '800', color: C.liveGreen },
  clock:    { fontSize: 13, fontWeight: '600', color: C.textMuted },
  kickoffTime: { fontSize: 14, fontWeight: '600', color: C.textMuted },

  alertTypeRow: { flexDirection: 'row', gap: 10, marginBottom: 16 },
  alertTypeBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingVertical: 9, borderRadius: 10,
    backgroundColor: C.bg, borderWidth: 1, borderColor: C.border,
  },
  alertTypeBtnActive:  { borderColor: C.red, backgroundColor: 'rgba(201,8,42,0.12)' },
  alertTypeIcon:       { fontSize: 14 },
  alertTypeText:       { fontSize: 12, fontWeight: '700', color: C.textMuted },
  alertTypeTextActive: { color: C.red },

  tabs: { flexDirection: 'row', backgroundColor: C.bg, borderRadius: 10, marginBottom: 16, padding: 3 },
  tab: {
    flex: 1, paddingVertical: 8, borderRadius: 8,
    alignItems: 'center', justifyContent: 'center',
  },
  tabActive:    { backgroundColor: C.card },
  tabText:      { fontSize: 13, fontWeight: '700', color: C.textMuted },
  tabTextActive:{ color: C.textPrimary },

  noCondRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 10, marginBottom: 4,
  },
  inputLabel: { fontSize: 12, color: C.textMuted, fontWeight: '700', letterSpacing: 1 },
  noCondSub:  { fontSize: 11, color: C.textMuted, marginTop: 2, lineHeight: 15 },

  sliderRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 10,
  },
  sliderValue: { fontSize: 20, fontWeight: '800', color: C.orange },
  slider:      { width: '100%', height: 40, marginBottom: 8 },

  quarterRow: { flexDirection: 'row', gap: 8, marginTop: 8, marginBottom: 20 },
  quarterBtn: {
    flex: 1, height: 46, borderRadius: 10,
    backgroundColor: C.bg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },
  quarterBtnActive:  { backgroundColor: C.orange, borderColor: C.orange },
  quarterText:       { fontSize: 14, fontWeight: '800', color: C.textMuted },
  quarterTextActive: { color: '#fff' },

  setBtn: {
    backgroundColor: C.orange, padding: 16, borderRadius: 14,
    alignItems: 'center', marginTop: 4,
  },
  setBtnDisabled: { opacity: 0.6 },
  setBtnText: { color: '#fff', fontWeight: '800', fontSize: 15 },
  saveError: { color: C.red, fontSize: 12, textAlign: 'center', marginBottom: 8, marginTop: 4 },

  // Player alarm
  rosterLoading: {
    height: 80, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 10,
  },
  rosterLoadingText: { fontSize: 14, color: C.textMuted },
  playerList:        { maxHeight: 220, marginBottom: 12 },
  teamHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 6, paddingHorizontal: 2, marginTop: 6,
    borderBottomWidth: 1, borderColor: C.border,
  },
  teamHeaderName: { fontSize: 11, fontWeight: '800', color: C.accentBlue, letterSpacing: 1.5 },
  playerRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 8, paddingHorizontal: 6, borderRadius: 8,
  },
  playerRowSelected: { backgroundColor: 'rgba(29,66,138,0.25)' },
  playerJersey:      { fontSize: 12, color: C.textMuted, width: 30, textAlign: 'right' },
  playerName:        { flex: 1, fontSize: 14, fontWeight: '600', color: C.textPrimary },
  playerNameSelected:{ color: C.accentBlue },
  playerPos:         { fontSize: 12, color: C.textMuted, width: 28, textAlign: 'center' },

  selectedPlayerHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: 'rgba(29,66,138,0.15)',
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8, marginBottom: 12, marginTop: 8,
  },
  selectedPlayerLabel: { fontSize: 15, fontWeight: '800', color: C.textPrimary },

  conditionHint: { fontSize: 12, color: C.textMuted, marginBottom: 12 },
  conditionCard: {
    backgroundColor: C.cardAlt, borderWidth: 1, borderColor: C.border,
    borderRadius: 10, padding: 12, marginBottom: 10,
  },
  conditionCardHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  conditionCardTitle:  { fontSize: 14, fontWeight: '700', color: C.textPrimary },
  conditionCardSub:    { fontSize: 12, color: C.textMuted, marginTop: 2 },

  miniQuarterRow: { flexDirection: 'row', gap: 6, marginBottom: 8 },
  miniQBtn: {
    flex: 1, height: 38, borderRadius: 8,
    backgroundColor: C.bg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },
});
