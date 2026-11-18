/** The subset of the planned court response needed by the nearby-games screen. */
export type GameSummary = {
  game_id: string;
  court_id: string;
  court_name: string;
  start_time: string;
  end_time: string;
  skill_level: 'Beginner' | 'Mid' | 'Advanced' | 'All';
  spots_available: number;
  total_spots: number;
  status: 'open' | 'full' | 'looking_for_players';
};

export type Court = {
  court_id: string;
  name: string;
  distance_miles: number;
  games: GameSummary[];
};
