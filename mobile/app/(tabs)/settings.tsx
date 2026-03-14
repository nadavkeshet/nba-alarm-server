import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { SERVER_URL } from '../../config';

// ─── Types ────────────────────────────────────────────────────────────────────

interface GlobalRadar    { enabled: boolean; max_diff: number; start_quarter: number; alert_type: string }
interface FavTeam        { team_id: string; team_name: string; max_diff: number; start_quarter: number }
interface FavPlayer      { player_id: string; name: string; monitor_career_high: boolean }
interface HistoricalCats { scoring_40_half: boolean; threes_11: boolean; quad_double_threat: boolean }
interface AppSettings {
  GlobalGameRadar: GlobalRadar;
  FavoriteTeams:   FavTeam[];
  FavoritePlayers: FavPlayer[];
  HistoricalMode:  { enabled: boolean; alert_type: string; categories: HistoricalCats };
}
interface CachedPlayer { id: string; name: string; jersey: string; position: string }
interface CachedTeam   { id: string; name: string; abbreviation: string; logo: string | null; players: CachedPlayer[] }

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
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function QRow({ value, onChange }: { value: string; onChange: (q: string) => void }) {
  return (
    <View style={styles.quarterRow}>
      {['1', '2', '3', '4'].map(q => (
        <TouchableOpacity
          key={q}
          style={[styles.qBtn, value === q && styles.qBtnActive]}
          onPress={() => onChange(q)}
        >
          <Text style={[styles.qBtnText, value === q && styles.qBtnTextActive]}>Q{q}</Text>
        </TouchableOpacity>
      ))}
    </View>
  );
}

// ─── SettingsScreen ───────────────────────────────────────────────────────────

export default function SettingsScreen() {
  const [settings, setSettings]         = useState<AppSettings | null>(null);
  const [loading, setLoading]           = useState(true);
  const [radarRunning, setRadarRunning] = useState(false);
  const [saving, setSaving]             = useState(false);
  const [allTeams, setAllTeams]         = useState<CachedTeam[]>([]);

  // Global radar local inputs
  const [radarMaxDiff, setRadarMaxDiff] = useState('5');
  const [radarStartQ, setRadarStartQ]   = useState('4');

  // Per-team in-place edit state for existing favorites
  const [teamEdits, setTeamEdits] = useState<Record<string, { max_diff: string; start_quarter: string }>>({});

  // ── Add Team Modal state ──────────────────────────────────────────────
  const [teamModalVisible, setTeamModalVisible] = useState(false);
  const [pickedTeam, setPickedTeam]             = useState<CachedTeam | null>(null);
  const [newTeamMaxDiff, setNewTeamMaxDiff]     = useState('5');
  const [newTeamStartQ, setNewTeamStartQ]       = useState('4');

  // ── Add Player Modal state ────────────────────────────────────────────
  const [playerModalVisible, setPlayerModalVisible]   = useState(false);
  const [pickedTeamForPlayer, setPickedTeamForPlayer] = useState<CachedTeam | null>(null);
  const [pickedPlayer, setPickedPlayer]               = useState<CachedPlayer | null>(null);
  const [newPlayerCareerHigh, setNewPlayerCareerHigh] = useState(true);

  // ── Load all data ──────────────────────────────────────────────────────

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [sRes, rRes, tRes] = await Promise.all([
        fetch(`${SERVER_URL}/settings`),
        fetch(`${SERVER_URL}/radar-status`),
        fetch(`${SERVER_URL}/all-teams-and-players`),
      ]);
      const s: AppSettings = await sRes.json();
      const r              = await rRes.json();
      const t              = await tRes.json();

      setSettings(s);
      setRadarRunning(r.running ?? false);
      setRadarMaxDiff(String(s.GlobalGameRadar?.max_diff ?? 5));
      setRadarStartQ(String(s.GlobalGameRadar?.start_quarter ?? 4));
      setAllTeams(t.teams ?? []);

      // Init per-team editable fields
      const edits: Record<string, { max_diff: string; start_quarter: string }> = {};
      for (const ft of s.FavoriteTeams ?? []) {
        edits[ft.team_id] = { max_diff: String(ft.max_diff), start_quarter: String(ft.start_quarter) };
      }
      setTeamEdits(edits);
    } catch (e) {
      console.error('Settings load error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => { loadAll(); }, [loadAll]),
  );

  // ── API helpers ────────────────────────────────────────────────────────

  const patchGlobalRadar = async (patch: Partial<GlobalRadar>) => {
    if (!settings) return;
    const next = { ...settings.GlobalGameRadar, ...patch };
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/global-radar`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      });
      setSettings(s => s ? { ...s, GlobalGameRadar: next } : s);
      // If radar was just enabled, also start the background loop
      if (next.enabled && !radarRunning) {
        await fetch(`${SERVER_URL}/start-radar`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ push_token: '', sound_channel: 'classic_alarm' }),
        });
        setRadarRunning(true);
      }
    } finally { setSaving(false); }
  };

  const patchHistorical = async (patch: { enabled?: boolean; alert_type?: string; categories?: Partial<HistoricalCats> }) => {
    if (!settings) return;
    const cur  = settings.HistoricalMode;
    const next = { enabled: patch.enabled ?? cur.enabled, alert_type: patch.alert_type ?? cur.alert_type ?? 'silent', categories: { ...cur.categories, ...patch.categories } };
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/historical`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      });
      setSettings(s => s ? { ...s, HistoricalMode: next } : s);
    } finally { setSaving(false); }
  };

  // ── Team CRUD ─────────────────────────────────────────────────────────

  const saveTeam = async (payload: FavTeam) => {
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/teams`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setSettings(s => s ? {
        ...s,
        FavoriteTeams: [...s.FavoriteTeams.filter(t => t.team_id !== payload.team_id), payload],
      } : s);
      setTeamEdits(prev => ({
        ...prev,
        [payload.team_id]: { max_diff: String(payload.max_diff), start_quarter: String(payload.start_quarter) },
      }));
    } finally { setSaving(false); }
  };

  const removeTeam = async (team_id: string) => {
    await fetch(`${SERVER_URL}/settings/teams/${team_id}`, { method: 'DELETE' });
    setSettings(s => s ? { ...s, FavoriteTeams: s.FavoriteTeams.filter(t => t.team_id !== team_id) } : s);
    setTeamEdits(prev => { const n = { ...prev }; delete n[team_id]; return n; });
  };

  const confirmAddTeam = async () => {
    if (!pickedTeam) return;
    await saveTeam({
      team_id:       pickedTeam.id,
      team_name:     pickedTeam.name,
      max_diff:      parseInt(newTeamMaxDiff, 10) || 5,
      start_quarter: parseInt(newTeamStartQ, 10) || 4,
    });
    closeTeamModal();
  };

  const updateExistingTeam = async (team: FavTeam) => {
    const edit = teamEdits[team.team_id] ?? { max_diff: '5', start_quarter: '4' };
    await saveTeam({
      ...team,
      max_diff:      parseInt(edit.max_diff, 10) || 5,
      start_quarter: parseInt(edit.start_quarter, 10) || 4,
    });
  };

  // ── Player CRUD ───────────────────────────────────────────────────────

  const confirmAddPlayer = async () => {
    if (!pickedPlayer) return;
    const payload: FavPlayer = {
      player_id:           pickedPlayer.id,
      name:                pickedPlayer.name,
      monitor_career_high: newPlayerCareerHigh,
    };
    setSaving(true);
    try {
      await fetch(`${SERVER_URL}/settings/players`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setSettings(s => s ? {
        ...s,
        FavoritePlayers: [...s.FavoritePlayers.filter(p => p.player_id !== payload.player_id), payload],
      } : s);
      closePlayerModal();
    } finally { setSaving(false); }
  };

  const removePlayer = async (player_id: string) => {
    await fetch(`${SERVER_URL}/settings/players/${player_id}`, { method: 'DELETE' });
    setSettings(s => s ? { ...s, FavoritePlayers: s.FavoritePlayers.filter(p => p.player_id !== player_id) } : s);
  };

  const toggleCareerHigh = async (player: FavPlayer) => {
    const updated = { ...player, monitor_career_high: !player.monitor_career_high };
    await fetch(`${SERVER_URL}/settings/players`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updated),
    });
    setSettings(s => s ? {
      ...s,
      FavoritePlayers: s.FavoritePlayers.map(p => p.player_id === player.player_id ? updated : p),
    } : s);
  };

  // ── Modal helpers ─────────────────────────────────────────────────────

  const openTeamModal = () => {
    setPickedTeam(null);
    setNewTeamMaxDiff('5');
    setNewTeamStartQ('4');
    setTeamModalVisible(true);
  };
  const closeTeamModal = () => { setTeamModalVisible(false); setPickedTeam(null); };

  const openPlayerModal = () => {
    setPickedTeamForPlayer(null);
    setPickedPlayer(null);
    setNewPlayerCareerHigh(true);
    setPlayerModalVisible(true);
  };
  const closePlayerModal = () => {
    setPlayerModalVisible(false);
    setPickedTeamForPlayer(null);
    setPickedPlayer(null);
  };

  // ── Derived ───────────────────────────────────────────────────────────

  const favTeamIds   = new Set(settings?.FavoriteTeams?.map(t => t.team_id) ?? []);
  const favPlayerIds = new Set(settings?.FavoritePlayers?.map(p => p.player_id) ?? []);

  // ── Loading ───────────────────────────────────────────────────────────

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

  // ─── Render ────────────────────────────────────────────────────────────
  return (
    <SafeAreaView style={styles.container}>
      <Text style={styles.header}>Settings</Text>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingBottom: 48 }}
        keyboardShouldPersistTaps="handled"
      >

        {/* ═══════════════════════════════════════════════════════════════
            FAVORITE TEAMS
        ═══════════════════════════════════════════════════════════════ */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>FAVORITE TEAMS</Text>
          <Text style={styles.sectionDesc}>
            Get alerted when a favorite team's game gets close. You can also tap ★ on the Home screen.
          </Text>

          <TouchableOpacity style={styles.addRowBtn} onPress={openTeamModal}>
            <Ionicons name="add-circle-outline" size={20} color={C.accentBlue} />
            <Text style={styles.addRowBtnText}>Add New Favorite Team</Text>
          </TouchableOpacity>

          {(settings?.FavoriteTeams ?? []).length === 0 ? (
            <Text style={styles.emptyHint}>No favorite teams yet.</Text>
          ) : (
            settings!.FavoriteTeams.map(t => {
              const edit = teamEdits[t.team_id] ?? { max_diff: String(t.max_diff), start_quarter: String(t.start_quarter) };
              return (
                <View key={t.team_id} style={styles.favCard}>
                  <View style={styles.favCardHeader}>
                    <Text style={styles.favName}>{t.team_name}</Text>
                    <TouchableOpacity onPress={() => removeTeam(t.team_id)} style={styles.removeBtn}>
                      <Text style={styles.removeBtnText}>Remove</Text>
                    </TouchableOpacity>
                  </View>

                  <Text style={styles.inputLabel}>MAX POINT DIFF</Text>
                  <TextInput
                    style={styles.numInput}
                    keyboardType="numeric"
                    value={edit.max_diff}
                    onChangeText={v => setTeamEdits(prev => ({ ...prev, [t.team_id]: { ...edit, max_diff: v } }))}
                    placeholderTextColor={C.textMuted}
                  />

                  <Text style={styles.inputLabel}>START MONITORING FROM</Text>
                  <QRow
                    value={edit.start_quarter}
                    onChange={q => setTeamEdits(prev => ({ ...prev, [t.team_id]: { ...edit, start_quarter: q } }))}
                  />

                  <TouchableOpacity style={styles.updateBtn} onPress={() => updateExistingTeam(t)} disabled={saving}>
                    <Text style={styles.updateBtnText}>Update</Text>
                  </TouchableOpacity>
                </View>
              );
            })
          )}
        </View>

        {/* ═══════════════════════════════════════════════════════════════
            FAVORITE PLAYERS
        ═══════════════════════════════════════════════════════════════ */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>FAVORITE PLAYERS</Text>
          <Text style={styles.sectionDesc}>
            Get career-high alerts for any player. You can also tap ★ in the Player Alarm.
          </Text>

          <TouchableOpacity style={styles.addRowBtn} onPress={openPlayerModal}>
            <Ionicons name="add-circle-outline" size={20} color={C.accentBlue} />
            <Text style={styles.addRowBtnText}>Add New Favorite Player</Text>
          </TouchableOpacity>

          {(settings?.FavoritePlayers ?? []).length === 0 ? (
            <Text style={styles.emptyHint}>No favorite players yet.</Text>
          ) : (
            settings!.FavoritePlayers.map(p => (
              <View key={p.player_id} style={styles.favCard}>
                <View style={styles.favCardHeader}>
                  <Text style={styles.favName}>{p.name}</Text>
                  <TouchableOpacity onPress={() => removePlayer(p.player_id)} style={styles.removeBtn}>
                    <Text style={styles.removeBtnText}>Remove</Text>
                  </TouchableOpacity>
                </View>
                <View style={styles.switchRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowLabel}>Career High Alert</Text>
                    <Text style={styles.rowSub}>Alert when within 5 pts of personal record</Text>
                  </View>
                  <Switch
                    value={p.monitor_career_high}
                    onValueChange={() => toggleCareerHigh(p)}
                    trackColor={{ false: C.border, true: C.gold }}
                    thumbColor={p.monitor_career_high ? C.gold : C.textMuted}
                  />
                </View>
              </View>
            ))
          )}
        </View>

        {/* ═══════════════════════════════════════════════════════════════
            GLOBAL GAME RADAR
        ═══════════════════════════════════════════════════════════════ */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>GLOBAL GAME RADAR</Text>
          <Text style={styles.sectionDesc}>
            Automatically detect any close NBA game — even without a manual alarm.
          </Text>

          <View style={styles.switchRow}>
            <Text style={styles.rowLabel}>Enable Radar</Text>
            <Switch
              value={radar?.enabled ?? false}
              onValueChange={v => patchGlobalRadar({ enabled: v })}
              trackColor={{ false: C.border, true: C.blue }}
              thumbColor={radar?.enabled ? C.accentBlue : C.textMuted}
            />
          </View>

          <View style={styles.alertTypeRow}>
            {(['alarm', 'silent'] as const).map(type => (
              <TouchableOpacity
                key={type}
                style={[styles.alertTypeBtn, (radar?.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]}
                onPress={() => patchGlobalRadar({ alert_type: type })}
              >
                <Text style={styles.alertTypeIcon}>{type === 'alarm' ? '🚨' : '🔕'}</Text>
                <Text style={[styles.alertTypeText, (radar?.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>
                  {type === 'alarm' ? 'Alarm' : 'Silent'}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {radar?.enabled && (
            <>
              <Text style={styles.inputLabel}>MAX POINT DIFF TO ALERT</Text>
              <TextInput
                style={styles.numInput}
                keyboardType="numeric"
                value={radarMaxDiff}
                onChangeText={setRadarMaxDiff}
                placeholderTextColor={C.textMuted}
              />
              <Text style={styles.inputLabel}>START MONITORING FROM</Text>
              <QRow value={radarStartQ} onChange={setRadarStartQ} />
              <TouchableOpacity
                style={styles.updateBtn}
                onPress={() => patchGlobalRadar({
                  max_diff:      parseInt(radarMaxDiff, 10) || 5,
                  start_quarter: parseInt(radarStartQ, 10) || 4,
                })}
                disabled={saving}
              >
                <Text style={styles.updateBtnText}>{saving ? 'Saving…' : 'Save Radar Settings'}</Text>
              </TouchableOpacity>
            </>
          )}

          <View style={[styles.switchRow, { marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderColor: C.border }]}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>Radar Status</Text>
              <Text style={styles.rowSub}>{radarRunning ? 'Running in background' : 'Stopped'}</Text>
            </View>
            <View style={[styles.statusDot, { backgroundColor: radarRunning ? C.liveGreen : C.border }]} />
          </View>
        </View>

        {/* ═══════════════════════════════════════════════════════════════
            HISTORICAL ALERTS
        ═══════════════════════════════════════════════════════════════ */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>HISTORICAL ALERTS</Text>
          <Text style={styles.sectionDesc}>
            Get notified when any player is approaching an all-time record in a live game.
          </Text>

          <View style={styles.switchRow}>
            <Text style={styles.rowLabel}>Enable Historical Mode</Text>
            <Switch
              value={hist?.enabled ?? false}
              onValueChange={v => patchHistorical({ enabled: v })}
              trackColor={{ false: C.border, true: C.blue }}
              thumbColor={hist?.enabled ? C.accentBlue : C.textMuted}
            />
          </View>

          <View style={styles.alertTypeRow}>
            {(['alarm', 'silent'] as const).map(type => (
              <TouchableOpacity
                key={type}
                style={[styles.alertTypeBtn, ((hist as any)?.alert_type ?? 'silent') === type && styles.alertTypeBtnActive]}
                onPress={() => patchHistorical({ alert_type: type })}
              >
                <Text style={styles.alertTypeIcon}>{type === 'alarm' ? '🚨' : '🔕'}</Text>
                <Text style={[styles.alertTypeText, ((hist as any)?.alert_type ?? 'silent') === type && styles.alertTypeTextActive]}>
                  {type === 'alarm' ? 'Alarm' : 'Silent'}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {hist?.enabled && (
            <>
              {([
                { key: 'scoring_40_half'    as const, label: 'Historic Scoring Pace',  sub: '40+ pts by halftime (end of Q2)' },
                { key: 'threes_11'          as const, label: '3-Point Record Pace',     sub: '11+ threes in a single game' },
                { key: 'quad_double_threat' as const, label: 'Quad-Double Threat',      sub: '8+ in any 4 of PTS/REB/AST/STL/BLK' },
              ] as const).map(({ key, label, sub }) => (
                <View key={key} style={styles.categoryRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowLabel}>{label}</Text>
                    <Text style={styles.rowSub}>{sub}</Text>
                  </View>
                  <Switch
                    value={histCats?.[key] ?? false}
                    onValueChange={v => patchHistorical({ categories: { [key]: v } })}
                    trackColor={{ false: C.border, true: C.blue }}
                    thumbColor={histCats?.[key] ? C.accentBlue : C.textMuted}
                  />
                </View>
              ))}
            </>
          )}
        </View>

      </ScrollView>

      {/* ═══════════════════════════════════════════════════════════════
          ADD TEAM MODAL
      ═══════════════════════════════════════════════════════════════ */}
      <Modal visible={teamModalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.modalSheet}>

            {/* Header */}
            <View style={styles.modalHeader}>
              {pickedTeam ? (
                <TouchableOpacity onPress={() => setPickedTeam(null)} style={styles.backBtn}>
                  <Ionicons name="arrow-back" size={20} color={C.accentBlue} />
                  <Text style={styles.backBtnText}>Teams</Text>
                </TouchableOpacity>
              ) : <View style={{ width: 70 }} />}
              <Text style={styles.modalTitle}>
                {pickedTeam ? pickedTeam.abbreviation : 'Select Team'}
              </Text>
              <TouchableOpacity onPress={closeTeamModal} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

            {/* Step 1: Team list */}
            {!pickedTeam && (
              <FlatList
                data={allTeams}
                keyExtractor={t => t.id}
                renderItem={({ item: t }) => {
                  const isFav = favTeamIds.has(t.id);
                  return (
                    <TouchableOpacity
                      style={styles.pickerRow}
                      onPress={() => setPickedTeam(t)}
                    >
                      <Text style={styles.pickerRowText}>{t.name}</Text>
                      <View style={styles.pickerRowRight}>
                        {isFav && <Text style={styles.savedBadge}>★ saved</Text>}
                        <Text style={styles.pickerRowAbbr}>{t.abbreviation}</Text>
                        <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                      </View>
                    </TouchableOpacity>
                  );
                }}
                ItemSeparatorComponent={() => <View style={styles.separator} />}
              />
            )}

            {/* Step 2: Config */}
            {pickedTeam && (
              <ScrollView keyboardShouldPersistTaps="handled" style={{ flex: 1 }}>
                <View style={styles.configArea}>
                  <Text style={styles.configTitle}>{pickedTeam.name}</Text>
                  <Text style={styles.configSub}>Set alert criteria for this team's games.</Text>

                  <Text style={styles.inputLabel}>MAX POINT DIFF TO ALERT</Text>
                  <TextInput
                    style={styles.numInput}
                    keyboardType="numeric"
                    value={newTeamMaxDiff}
                    onChangeText={setNewTeamMaxDiff}
                    placeholderTextColor={C.textMuted}
                    autoFocus
                  />

                  <Text style={styles.inputLabel}>START MONITORING FROM QUARTER</Text>
                  <QRow value={newTeamStartQ} onChange={setNewTeamStartQ} />

                  <View style={styles.configSummary}>
                    <Ionicons name="information-circle-outline" size={14} color={C.textMuted} />
                    <Text style={styles.configSummaryText}>
                      Alert fires when diff ≤ {newTeamMaxDiff || '5'} pts from Q{newTeamStartQ}
                    </Text>
                  </View>

                  <TouchableOpacity
                    style={[styles.confirmBtn, saving && styles.confirmBtnDisabled]}
                    onPress={confirmAddTeam}
                    disabled={saving}
                  >
                    <Text style={styles.confirmBtnText}>
                      {saving ? 'Saving…' : `Add ${pickedTeam.abbreviation} to Favorites`}
                    </Text>
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
          <View style={styles.modalSheet}>

            {/* Header */}
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
                  ? 'Configure Alert'
                  : pickedTeamForPlayer
                    ? `${pickedTeamForPlayer.abbreviation} Roster`
                    : 'Select Team'}
              </Text>
              <TouchableOpacity onPress={closePlayerModal} style={styles.closeBtn}>
                <Ionicons name="close" size={22} color={C.textMuted} />
              </TouchableOpacity>
            </View>

            {/* Step 1: Team list */}
            {!pickedTeamForPlayer && (
              <FlatList
                data={allTeams}
                keyExtractor={t => t.id}
                renderItem={({ item: t }) => (
                  <TouchableOpacity
                    style={styles.pickerRow}
                    onPress={() => setPickedTeamForPlayer(t)}
                  >
                    <Text style={styles.pickerRowText}>{t.name}</Text>
                    <View style={styles.pickerRowRight}>
                      <Text style={styles.pickerRowAbbr}>{t.players.length} players</Text>
                      <Ionicons name="chevron-forward" size={16} color={C.textMuted} />
                    </View>
                  </TouchableOpacity>
                )}
                ItemSeparatorComponent={() => <View style={styles.separator} />}
              />
            )}

            {/* Step 2: Player list */}
            {pickedTeamForPlayer && !pickedPlayer && (
              <FlatList
                data={pickedTeamForPlayer.players}
                keyExtractor={p => p.id}
                renderItem={({ item: p }) => {
                  const isFav = favPlayerIds.has(p.id);
                  return (
                    <TouchableOpacity
                      style={styles.pickerRow}
                      onPress={() => setPickedPlayer(p)}
                    >
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
                ItemSeparatorComponent={() => <View style={styles.separator} />}
              />
            )}

            {/* Step 3: Config */}
            {pickedPlayer && (
              <ScrollView keyboardShouldPersistTaps="handled" style={{ flex: 1 }}>
                <View style={styles.configArea}>
                  <Text style={styles.configTitle}>{pickedPlayer.name}</Text>
                  <Text style={styles.configSub}>
                    {pickedTeamForPlayer?.name}
                    {pickedPlayer.jersey ? `  ·  #${pickedPlayer.jersey}` : ''}
                    {pickedPlayer.position ? `  ·  ${pickedPlayer.position}` : ''}
                  </Text>

                  <View style={[styles.switchRow, { marginTop: 20 }]}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.rowLabel}>Career High Alert</Text>
                      <Text style={styles.rowSub}>Alert when within 5 pts of personal record</Text>
                    </View>
                    <Switch
                      value={newPlayerCareerHigh}
                      onValueChange={setNewPlayerCareerHigh}
                      trackColor={{ false: C.border, true: C.gold }}
                      thumbColor={newPlayerCareerHigh ? C.gold : C.textMuted}
                    />
                  </View>

                  <TouchableOpacity
                    style={[styles.confirmBtn, saving && styles.confirmBtnDisabled, { marginTop: 24 }]}
                    onPress={confirmAddPlayer}
                    disabled={saving}
                  >
                    <Text style={styles.confirmBtnText}>
                      {saving ? 'Saving…' : `Add ${pickedPlayer.name} to Favorites`}
                    </Text>
                  </TouchableOpacity>
                </View>
              </ScrollView>
            )}

          </View>
        </View>
      </Modal>

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

  // Section card
  section: {
    marginHorizontal: 16, marginTop: 14,
    backgroundColor: C.card, borderRadius: 16,
    borderWidth: 1, borderColor: C.border, padding: 16,
  },
  sectionTitle: {
    fontSize: 11, fontWeight: '800', color: C.textMuted,
    letterSpacing: 2, marginBottom: 4,
  },
  sectionDesc: { fontSize: 12, color: C.textMuted, lineHeight: 17, marginBottom: 14 },

  // Add button row
  addRowBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 10, paddingHorizontal: 12,
    backgroundColor: 'rgba(77,140,245,0.1)',
    borderRadius: 10, borderWidth: 1, borderColor: C.accentBlue,
    marginBottom: 12,
  },
  addRowBtnText: { color: C.accentBlue, fontWeight: '700', fontSize: 14 },

  // Favorite card (existing items)
  favCard: {
    backgroundColor: C.bg, borderRadius: 12,
    borderWidth: 1, borderColor: C.border,
    padding: 12, marginTop: 10,
  },
  favCardHeader: {
    flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'center', marginBottom: 6,
  },
  favName: { fontSize: 15, fontWeight: '800', color: C.textPrimary, flex: 1 },

  // Labels & inputs
  inputLabel: {
    fontSize: 10, fontWeight: '800', color: C.textMuted,
    letterSpacing: 1.5, marginBottom: 6, marginTop: 10,
  },
  numInput: {
    borderBottomWidth: 2, borderColor: C.blue,
    color: C.textPrimary, fontSize: 26,
    paddingVertical: 4, marginBottom: 10,
  },

  // Quarter picker
  quarterRow: { flexDirection: 'row', gap: 8, marginBottom: 12 },
  qBtn: {
    flex: 1, height: 40, borderRadius: 8,
    backgroundColor: C.card, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: C.border,
  },
  qBtnActive:     { backgroundColor: C.red, borderColor: C.red },
  qBtnText:       { fontSize: 13, fontWeight: '700', color: C.textMuted },
  qBtnTextActive: { color: '#fff' },

  // Buttons
  updateBtn: {
    backgroundColor: 'rgba(29,66,138,0.25)', padding: 10,
    borderRadius: 10, alignItems: 'center',
    borderWidth: 1, borderColor: C.blue,
  },
  updateBtnText: { color: C.accentBlue, fontWeight: '800', fontSize: 13 },
  removeBtn: {
    paddingHorizontal: 10, paddingVertical: 4,
    backgroundColor: 'rgba(201,8,42,0.15)', borderRadius: 8,
  },
  removeBtnText: { color: C.red, fontSize: 12, fontWeight: '700' },

  // Switch rows
  switchRow:   { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 4 },
  categoryRow: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', paddingVertical: 10,
    borderTopWidth: 1, borderColor: C.border,
  },
  rowLabel: { fontSize: 14, color: C.textPrimary, fontWeight: '600' },
  rowSub:   { fontSize: 12, color: C.textMuted, marginTop: 2 },

  statusDot: { width: 10, height: 10, borderRadius: 5 },
  emptyHint: { color: C.textMuted, fontSize: 13, fontStyle: 'italic', paddingVertical: 6 },

  // ── Modal ──────────────────────────────────────────────────────────────

  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.75)', justifyContent: 'flex-end' },
  modalSheet: {
    backgroundColor: C.card,
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    borderWidth: 1, borderColor: C.border,
    height: '85%',
  },
  modalHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 14,
    borderBottomWidth: 1, borderColor: C.border,
  },
  modalTitle: { fontSize: 16, fontWeight: '800', color: C.textPrimary },
  backBtn:    { flexDirection: 'row', alignItems: 'center', gap: 4, width: 70 },
  backBtnText:{ color: C.accentBlue, fontSize: 14, fontWeight: '700' },
  closeBtn:   { width: 70, alignItems: 'flex-end' },

  // Picker list rows
  pickerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 14,
  },
  pickerRowText:  { fontSize: 15, color: C.textPrimary, fontWeight: '600', flex: 1 },
  pickerRowSub:   { fontSize: 12, color: C.textMuted, marginTop: 2 },
  pickerRowRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pickerRowAbbr:  { fontSize: 13, color: C.textMuted },
  savedBadge:     { fontSize: 12, color: '#f5c518', fontWeight: '700' },
  separator:      { height: 1, backgroundColor: C.border, marginLeft: 16 },

  // Config area (step 2/3)
  configArea: { padding: 24 },
  configTitle:{ fontSize: 20, fontWeight: '800', color: C.textPrimary, marginBottom: 4 },
  configSub:  { fontSize: 13, color: C.textMuted, marginBottom: 8, lineHeight: 18 },
  configSummary: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: C.bg, borderRadius: 8, padding: 10, marginBottom: 20,
  },
  configSummaryText: { fontSize: 12, color: C.textMuted, flex: 1 },
  confirmBtn: {
    backgroundColor: C.blue, padding: 16,
    borderRadius: 14, alignItems: 'center',
  },
  confirmBtnDisabled: { opacity: 0.5 },
  confirmBtnText: { color: '#fff', fontWeight: '800', fontSize: 15 },

  // Alert type toggle
  alertTypeRow: {
    flexDirection: 'row', gap: 10, marginTop: 10, marginBottom: 4,
  },
  alertTypeBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingVertical: 9, borderRadius: 10,
    backgroundColor: C.bg, borderWidth: 1, borderColor: C.border,
  },
  alertTypeBtnActive: { borderColor: C.red, backgroundColor: 'rgba(201,8,42,0.12)' },
  alertTypeIcon: { fontSize: 14 },
  alertTypeText: { fontSize: 12, fontWeight: '700', color: C.textMuted },
  alertTypeTextActive: { color: C.red },
});
