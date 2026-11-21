import type { SoccerFollowConfig } from '../types/soccer';

export function defaultFollowConfig(): SoccerFollowConfig {
  return {
    drama_radar:   { enabled: true, threshold: 7.0 },
    late_drama:    { enabled: false, max_diff: 1, last_minutes: 15 },
    match_moments: { enabled: false, kickoff: true, goals: true, halftime: true, second_half: true, red_cards: true, penalties: true, match_end: true, lineups: false, reminder_minutes: 0, alert_type: 'silent' },
    card_penalty:  { enabled: false, red_card: true, penalty: true },
    sensation:     { enabled: false },
    comeback:      { enabled: false },
    big_chance:    { enabled: false, sensitivity: 'medium' },
  };
}

export function parseServerConfig(tm: { drama_threshold: number; config?: SoccerFollowConfig }): SoccerFollowConfig {
  if (tm.config) return tm.config;
  const def = defaultFollowConfig();
  def.drama_radar = { enabled: true, threshold: tm.drama_threshold };
  return def;
}
