export type SoccerStatus =
  | 'scheduled'
  | 'live'
  | 'ht'
  | 'ft'
  | 'et'
  | 'pens'
  | 'postponed'
  | 'cancelled'
  | 'unknown';

export type SoccerPeriod =
  | 'pre'
  | '1h'
  | 'ht'
  | '2h'
  | 'et'
  | 'pens'
  | 'ft'
  | 'unknown';

export interface SoccerTeamSide {
  id: string;
  name: string;
  short_name: string;
  crest: string | null;
  score: number | null;
  score_ht: number | null;
  score_pens: number | null;
  xg: number | null;
}

export interface SoccerVenue {
  id?: number;
  name?: string;
}

export interface SoccerMatch {
  id: string;               // canonical: "bsd-372" | "sofa-14023956" | "s365-2123"
  source: string;
  source_event_id?: number;
  competition: string;      // slug: "premier_league", "ucl", "ligat_haal", ...
  competition_id_native?: number;
  kickoff: string | null;   // ISO 8601 UTC
  status: SoccerStatus;
  period: SoccerPeriod;
  minute: number | null;
  home: SoccerTeamSide;
  away: SoccerTeamSide;
  venue?: SoccerVenue;
  is_derby: boolean;
  is_neutral_ground: boolean;
  round_number?: number;
  round_name?: string;
  watch_score?: number | null;
}

// ── Drama Engine ─────────────────────────────────────────────────────────────

export type DramaBand = 'mustWatch' | 'worthLooking' | 'quiet';

export interface SoccerDramaScore {
  total: number;      // 0–10
  state: number;      // 0–7
  momentum: number;   // −2 to +4
  events: number;     // 0–4
  stakes: number;     // 0.6–2.0
  band: DramaBand;
}

export interface DramaTimelinePoint {
  m: number;          // minute
  drama: number;
  state: number;
  momentum: number;
  events: number;
  stakes: number;
}

// ── Match Detail (full payload from /soccer/match/{id}) ──────────────────────

export interface MomentumPoint {
  m: number;    // minute (decimal — 45.5 = HT)
  v: number;    // value −100..+100
}

export interface XgPoint {
  m: number;
  cum_home: number;
  cum_away: number;
}

export interface ShotCoord {
  x?: number;
  y?: number;
}

export interface Shot {
  xg?: number;
  min?: number;
  sit?: string;          // situation
  body?: string;         // body part
  home?: boolean;
  type?: string;         // 'goal' | 'miss' | 'save' | 'block'
  player_id?: number;
  xgot?: number;
  pos?: ShotCoord;
}

export interface SequenceStep {
  event?: string;
  player?: string;
  assist?: boolean;
  pos?: ShotCoord;
}

export interface Incident {
  type?: string;
  minute?: number;
  is_home?: boolean;
  player?: string;
  assist?: string;
  player_id?: number;
  card_type?: string;        // 'yellow' | 'red' | 'yellow-red'
  player_in?: string;
  player_out?: string;
  player_in_id?: number;
  player_out_id?: number;
  text?: string;
  home_score?: number;
  away_score?: number;
  length?: number;
  sequence?: SequenceStep[];
}

export interface TeamStats {
  expected_goals?: number;
  ball_possession?: number;
  total_shots?: number;
  shots_on_target?: number;
  blocked_shots?: number;
  big_chances?: number;
  big_chances_scored?: number;
  big_chances_missed?: number;
  corner_kicks?: number;
  fouls?: number;
  yellow_cards?: number;
  red_cards?: number;
  offsides?: number;
  accurate_passes?: number;
  total_passes?: number;
  pass_accuracy_pct?: number;
}

export interface MatchStats {
  home?: TeamStats;
  away?: TeamStats;
}

export interface SoccerMatchDetail extends SoccerMatch {
  momentum?: MomentumPoint[];
  xg_per_minute?: XgPoint[];
  shotmap?: Shot[];
  incidents?: Incident[];
  stats?: MatchStats;
}

// ── Lineups ──────────────────────────────────────────────────────────────────

export interface LineupPlayer {
  id: number;
  name: string;
  short_name?: string;
  position?: string;    // "G" | "D" | "M" | "F"
  jersey_number?: number;
  ai_score?: number;
  photo_url?: string;
}

export interface LineupTeam {
  team_id: number;
  team_name: string;
  formation?: string;
  confidence?: number;
  players: LineupPlayer[];
  substitutes: LineupPlayer[];
}

export interface MatchLineups {
  event_id?: number;
  lineup_status: 'confirmed' | 'predicted' | 'unavailable';
  beta?: boolean;
  lineups?: {
    home: LineupTeam;
    away: LineupTeam;
  };
}

// ── Standings ────────────────────────────────────────────────────────────────

export interface StandingRow {
  position: number;
  team_id: number;
  team_name: string;
  team_crest?: string;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  gf: number;
  ga: number;
  gd: number;
  pts: number;
  form?: string;
  live?: boolean;
}

export interface LeagueStandings {
  league_id: number;
  season: { id: number; name: string };
  grouped: boolean;
  standings: StandingRow[];
}

// ── Follow Config ─────────────────────────────────────────────────────────────

export interface SoccerFollowConfig {
  drama_radar?:   { enabled: boolean; threshold: number };
  late_drama?:    { enabled: boolean; max_diff: number; last_minutes: number };
  match_moments?: { enabled: boolean; kickoff: boolean; goals: boolean; halftime: boolean; second_half: boolean; red_cards: boolean; penalties: boolean; match_end: boolean; lineups: boolean; reminder_minutes: number; alert_type: 'alarm' | 'silent' };
  card_penalty?:  { enabled: boolean; red_card: boolean; penalty: boolean };
  sensation?:     { enabled: boolean };
  comeback?:      { enabled: boolean };
  big_chance?:    { enabled: boolean; sensitivity: 'low' | 'medium' | 'high' };
}

// ── Alarms ───────────────────────────────────────────────────────────────────

export type SoccerAlarmType =
  | 'drama_radar'
  | 'match_moments'
  | 'late_drama'
  | 'score_gap'
  | 'comeback'
  | 'big_chance'
  | 'sensation'
  | 'card_penalty'
  | 'match'
  | 'knockout_et'
  | 'hat_trick'
  | 'red_card'
  | 'penalty'
  | 'player_alert';

export interface SoccerAlarm {
  id: number;
  alarm_type: SoccerAlarmType;
  match_id: string | null;
  match_name: string | null;
  competition_id: string | null;
  player_id: string | null;
  threshold: number | null;
  alert_type: 'alarm' | 'silent';
  config?: Record<string, boolean | string>;
  created_at: string;
  fired_at: string | null;
  is_active: boolean;
}

// ── Favorites ────────────────────────────────────────────────────────────────

export interface SoccerFavTeam {
  team_id: string;
  team_name?: string;
  team_ids?: string[];
  competitions?: string[];
  drama_threshold: number;
  alert_type: 'alarm' | 'silent';
  paused: boolean;
  notify_at_kickoff?: boolean;
  config?: SoccerFollowConfig;
}

export interface SoccerFavPlayer {
  player_id: string;
  player_name?: string;
  team_id?: string;
  alert_type: 'alarm' | 'silent';
  paused: boolean;
}

export interface SoccerFavCompetition {
  competition_id: string;
  drama_threshold: number;
  alert_type?: 'alarm' | 'silent';
}

export interface SoccerFavoritesResponse {
  teams: SoccerFavTeam[];
  players: SoccerFavPlayer[];
  competitions: SoccerFavCompetition[];
}

// ── UI helpers ───────────────────────────────────────────────────────────────

export function isLiveLike(status: SoccerStatus): boolean {
  return status === 'live' || status === 'ht' || status === 'et' || status === 'pens';
}

/**
 * Returns 'summary' or 'alarm' for consistent tap routing across all screens.
 *   finished / live / unknown  → 'summary' (show stats, alarm button inside)
 *   scheduled / postponed / cancelled → 'alarm'  (open alarm sheet directly)
 * Handles stale cache: if status is 'scheduled' but kickoff has passed, routes to 'summary'.
 */
export function soccerMatchRoute(match: SoccerMatch): 'summary' | 'alarm' {
  // If there's a minute value the match has started → always show summary
  if (match.minute != null) return 'summary';

  // Only truly pre-match statuses go to alarm (with kickoff-time safety check)
  if (match.status === 'postponed' || match.status === 'cancelled') return 'alarm';
  if (match.status === 'scheduled') {
    // If kickoff has passed at all, the match may be live with a stale status → show summary
    const kickoffMs = match.kickoff ? new Date(match.kickoff).getTime() : 0;
    if (kickoffMs > 0 && Date.now() >= kickoffMs) return 'summary';
    return 'alarm';
  }
  // live, ht, et, pens, ft, unknown → always show summary
  return 'summary';
}

export function displayMinute(match: SoccerMatch): string {
  switch (match.status) {
    case 'ht':        return 'HT';
    case 'ft':        return 'FT';
    case 'et':        return match.minute != null ? `ET ${match.minute}'` : 'ET';
    case 'pens':      return 'Pens';
    case 'postponed': return 'PPD';
    case 'cancelled': return 'CANC';
    case 'live':
    case 'unknown':   return match.minute != null ? `${match.minute}'` : '';
    case 'scheduled': return '';
  }
}

export function dramaBandColor(
  band: DramaBand,
  colors: { orange: string; accentBlue: string; textMuted: string }
): string {
  switch (band) {
    case 'mustWatch':    return colors.orange;
    case 'worthLooking': return colors.accentBlue;
    case 'quiet':        return colors.textMuted;
  }
}

// ── Historic Moments ──────────────────────────────────────────────────────────

export const SOCCER_HISTORIC_GROUPS = [
  {
    key: 'goals',
    categories: [
      { key: 'hat_trick',         labelKey: 'hist.hatTrick',        subKey: 'hist.hatTrickSub' },
      { key: 'poker',             labelKey: 'hist.poker',           subKey: 'hist.pokerSub' },
      { key: 'five_plus',         labelKey: 'hist.fivePlus',        subKey: 'hist.fivePlusSub' },
      { key: 'fast_hat_trick',    labelKey: 'hist.fastHatTrick',    subKey: 'hist.fastHatTrickSub' },
      { key: 'penalty_hat_trick', labelKey: 'hist.penHatTrick',     subKey: 'hist.penHatTrickSub' },
      { key: 'goal_involvements', labelKey: 'hist.goalInvolvements',subKey: 'hist.goalInvolvementsSub' },
    ],
  },
  {
    key: 'scoreline',
    categories: [
      { key: 'rout',              labelKey: 'hist.rout',            subKey: 'hist.routSub' },
      { key: 'goal_feast',        labelKey: 'hist.goalFeast',       subKey: 'hist.goalFeastSub' },
      { key: 'end_to_end',        labelKey: 'hist.endToEnd',        subKey: 'hist.endToEndSub' },
      { key: 'goal_blitz',        labelKey: 'hist.goalBlitz',       subKey: 'hist.goalBlitzSub' },
      { key: 'lightning_goal',    labelKey: 'hist.lightningGoal',   subKey: 'hist.lightningGoalSub' },
      { key: 'last_gasp_winner',  labelKey: 'hist.lastGaspWinner',  subKey: 'hist.lastGaspWinnerSub' },
      { key: 'historic_comeback', labelKey: 'hist.historicComeback',subKey: 'hist.historicComebackSub' },
    ],
  },
  {
    key: 'discipline',
    categories: [
      { key: 'early_red',         labelKey: 'hist.earlyRed',        subKey: 'hist.earlyRedSub' },
      { key: 'mass_red_cards',    labelKey: 'hist.massRed',         subKey: 'hist.massRedSub' },
      { key: 'penalty_barrage',   labelKey: 'hist.penBarrage',      subKey: 'hist.penBarrageSub' },
      { key: 'stoppage_penalty',  labelKey: 'hist.stoppagePen',     subKey: 'hist.stoppagePenSub' },
    ],
  },
  {
    key: 'rarities',
    categories: [
      { key: 'own_goal',          labelKey: 'hist.ownGoal',         subKey: 'hist.ownGoalSub' },
      { key: 'multiple_own_goals',labelKey: 'hist.multiOwnGoal',    subKey: 'hist.multiOwnGoalSub' },
      { key: 'goalkeeper_goal',   labelKey: 'hist.gkGoal',          subKey: 'hist.gkGoalSub' },
      { key: 'defender_brace',    labelKey: 'hist.defBrace',        subKey: 'hist.defBraceSub' },
    ],
  },
  {
    key: 'shootout',
    categories: [
      { key: 'marathon_shootout', labelKey: 'hist.marathonShootout',subKey: 'hist.marathonShootoutSub' },
      { key: 'shootout_decided',  labelKey: 'hist.shootoutDecided', subKey: 'hist.shootoutDecidedSub' },
    ],
  },
] as const;

export type SoccerHistoricCategoryKey =
  (typeof SOCCER_HISTORIC_GROUPS)[number]['categories'][number]['key'];

export interface SoccerHistoricPref {
  enabled: boolean;
  alert_type: 'alarm' | 'silent';
  categories: Record<SoccerHistoricCategoryKey, boolean>;
}
