export type Sport = 'basketball' | 'football';

export interface LeagueInfo {
  id: string;
  label: string;
  sport: Sport;
  color: string;
}

const ALL_LEAGUES: LeagueInfo[] = [
  // ── Basketball ─────────────────────────────────────────────────────────────
  { id: 'nba',           label: 'NBA',              sport: 'basketball', color: '#1d428a' },
  { id: 'euroleague',    label: 'Euroleague',        sport: 'basketball', color: '#0046A8' },
  { id: 'eurocup',       label: 'EuroCup',           sport: 'basketball', color: '#2B5BA8' },
  { id: 'bcl',           label: 'BCL',               sport: 'basketball', color: '#1a5276' },
  { id: 'winner_league', label: "Winner League",     sport: 'basketball', color: '#003366' },
  // ── Football ───────────────────────────────────────────────────────────────
  { id: 'world_cup',      label: 'World Cup',         sport: 'football',  color: '#006341' },
  { id: 'premier_league', label: 'Premier League',   sport: 'football',  color: '#3D195B' },
  { id: 'la_liga',        label: 'La Liga',           sport: 'football',  color: '#EE8707' },
  { id: 'serie_a',        label: 'Serie A',           sport: 'football',  color: '#024494' },
  { id: 'bundesliga',     label: 'Bundesliga',        sport: 'football',  color: '#D3010C' },
  { id: 'ligue_1',        label: 'Ligue 1',           sport: 'football',  color: '#091C3E' },
  { id: 'ucl',            label: 'Champions League',  sport: 'football',  color: '#0E1E5B' },
  { id: 'europa_league',  label: 'Europa League',     sport: 'football',  color: '#F77F00' },
  { id: 'ligat_haal',     label: "Ligat Ha'al",       sport: 'football',  color: '#0038A8' },
];

export const LeagueCatalog = {
  all: ALL_LEAGUES,

  info(id: string): LeagueInfo {
    return ALL_LEAGUES.find(l => l.id === id) ?? {
      id,
      label: id,
      sport: 'basketball',
      color: '#6b8aad',
    };
  },

  leagues(forSport: Sport): LeagueInfo[] {
    return ALL_LEAGUES.filter(l => l.sport === forSport);
  },

  footballLeagues(): LeagueInfo[] {
    return ALL_LEAGUES.filter(l => l.sport === 'football');
  },

  basketballLeagues(): LeagueInfo[] {
    return ALL_LEAGUES.filter(l => l.sport === 'basketball');
  },
};
