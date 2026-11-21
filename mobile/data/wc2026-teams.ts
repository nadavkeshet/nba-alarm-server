/**
 * Static World Cup 2026 team data (48 teams, Groups A–L).
 * BSD team IDs sourced from the server standings endpoint.
 * Used to seed the browse cache on first launch — no API call needed.
 */

export interface WcTeam {
  id: string;
  name: string;
  logo: string;
  group: string;
}

export const WC2026_TEAMS: WcTeam[] = [
  // Group A
  { id: '930', name: 'Czechia',            logo: 'https://sports.bzzoiro.com/img/team/930/', group: 'A' },
  { id: '451', name: 'Mexico',             logo: 'https://sports.bzzoiro.com/img/team/451/', group: 'A' },
  { id: '452', name: 'South Africa',       logo: 'https://sports.bzzoiro.com/img/team/452/', group: 'A' },
  { id: '453', name: 'South Korea',        logo: 'https://sports.bzzoiro.com/img/team/453/', group: 'A' },
  // Group B
  { id: '931', name: 'Bosnia & Herzegovina', logo: 'https://sports.bzzoiro.com/img/team/931/', group: 'B' },
  { id: '455', name: 'Canada',             logo: 'https://sports.bzzoiro.com/img/team/455/', group: 'B' },
  { id: '461', name: 'Qatar',              logo: 'https://sports.bzzoiro.com/img/team/461/', group: 'B' },
  { id: '462', name: 'Switzerland',        logo: 'https://sports.bzzoiro.com/img/team/462/', group: 'B' },
  // Group C
  { id: '463', name: 'Brazil',             logo: 'https://sports.bzzoiro.com/img/team/463/', group: 'C' },
  { id: '465', name: 'Haiti',              logo: 'https://sports.bzzoiro.com/img/team/465/', group: 'C' },
  { id: '464', name: 'Morocco',            logo: 'https://sports.bzzoiro.com/img/team/464/', group: 'C' },
  { id: '466', name: 'Scotland',           logo: 'https://sports.bzzoiro.com/img/team/466/', group: 'C' },
  // Group D
  { id: '459', name: 'Australia',          logo: 'https://sports.bzzoiro.com/img/team/459/', group: 'D' },
  { id: '458', name: 'Paraguay',           logo: 'https://sports.bzzoiro.com/img/team/458/', group: 'D' },
  { id: '932', name: 'Türkiye',            logo: 'https://sports.bzzoiro.com/img/team/932/', group: 'D' },
  { id: '457', name: 'USA',                logo: 'https://sports.bzzoiro.com/img/team/457/', group: 'D' },
  // Group E
  { id: '468', name: 'Curaçao',            logo: 'https://sports.bzzoiro.com/img/team/468/', group: 'E' },
  { id: '471', name: "Côte d'Ivoire",      logo: 'https://sports.bzzoiro.com/img/team/471/', group: 'E' },
  { id: '472', name: 'Ecuador',            logo: 'https://sports.bzzoiro.com/img/team/472/', group: 'E' },
  { id: '467', name: 'Germany',            logo: 'https://sports.bzzoiro.com/img/team/467/', group: 'E' },
  // Group F
  { id: '470', name: 'Japan',              logo: 'https://sports.bzzoiro.com/img/team/470/', group: 'F' },
  { id: '469', name: 'Netherlands',        logo: 'https://sports.bzzoiro.com/img/team/469/', group: 'F' },
  { id: '731', name: 'Sweden',             logo: 'https://sports.bzzoiro.com/img/team/731/', group: 'F' },
  { id: '474', name: 'Tunisia',            logo: 'https://sports.bzzoiro.com/img/team/474/', group: 'F' },
  // Group G
  { id: '477', name: 'Belgium',            logo: 'https://sports.bzzoiro.com/img/team/477/', group: 'G' },
  { id: '478', name: 'Egypt',              logo: 'https://sports.bzzoiro.com/img/team/478/', group: 'G' },
  { id: '481', name: 'Iran',               logo: 'https://sports.bzzoiro.com/img/team/481/', group: 'G' },
  { id: '482', name: 'New Zealand',        logo: 'https://sports.bzzoiro.com/img/team/482/', group: 'G' },
  // Group H
  { id: '476', name: 'Cabo Verde',         logo: 'https://sports.bzzoiro.com/img/team/476/', group: 'H' },
  { id: '479', name: 'Saudi Arabia',       logo: 'https://sports.bzzoiro.com/img/team/479/', group: 'H' },
  { id: '475', name: 'Spain',              logo: 'https://sports.bzzoiro.com/img/team/475/', group: 'H' },
  { id: '480', name: 'Uruguay',            logo: 'https://sports.bzzoiro.com/img/team/480/', group: 'H' },
  // Group I
  { id: '485', name: 'France',             logo: 'https://sports.bzzoiro.com/img/team/485/', group: 'I' },
  { id: '933', name: 'Iraq',               logo: 'https://sports.bzzoiro.com/img/team/933/', group: 'I' },
  { id: '488', name: 'Norway',             logo: 'https://sports.bzzoiro.com/img/team/488/', group: 'I' },
  { id: '486', name: 'Senegal',            logo: 'https://sports.bzzoiro.com/img/team/486/', group: 'I' },
  // Group J
  { id: '490', name: 'Algeria',            logo: 'https://sports.bzzoiro.com/img/team/490/', group: 'J' },
  { id: '489', name: 'Argentina',          logo: 'https://sports.bzzoiro.com/img/team/489/', group: 'J' },
  { id: '483', name: 'Austria',            logo: 'https://sports.bzzoiro.com/img/team/483/', group: 'J' },
  { id: '484', name: 'Jordan',             logo: 'https://sports.bzzoiro.com/img/team/484/', group: 'J' },
  // Group K
  { id: '498', name: 'Colombia',           logo: 'https://sports.bzzoiro.com/img/team/498/', group: 'K' },
  { id: '648', name: 'DR Congo',           logo: 'https://sports.bzzoiro.com/img/team/648/', group: 'K' },
  { id: '491', name: 'Portugal',           logo: 'https://sports.bzzoiro.com/img/team/491/', group: 'K' },
  { id: '497', name: 'Uzbekistan',         logo: 'https://sports.bzzoiro.com/img/team/497/', group: 'K' },
  // Group L
  { id: '494', name: 'Croatia',            logo: 'https://sports.bzzoiro.com/img/team/494/', group: 'L' },
  { id: '493', name: 'England',            logo: 'https://sports.bzzoiro.com/img/team/493/', group: 'L' },
  { id: '495', name: 'Ghana',              logo: 'https://sports.bzzoiro.com/img/team/495/', group: 'L' },
  { id: '496', name: 'Panama',             logo: 'https://sports.bzzoiro.com/img/team/496/', group: 'L' },
];

/** Pre-built SearchTeam list for instant browse seeding (no API call). */
export const WC2026_SEARCH_TEAMS = WC2026_TEAMS.map(t => ({
  id:     t.id,
  name:   t.name,
  logo:   t.logo,
  sport:  'football' as const,
  league: 'world_cup',
}));
