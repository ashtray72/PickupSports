import { render, screen } from '@testing-library/react-native';

import { GameCard } from '../src/components/games/GameCard';
import type { GameSummary } from '../src/types/court';

const game: GameSummary = {
  game_id: 'test-game',
  court_id: 'test-court',
  court_name: 'Purdue CoRec - Court 2',
  start_time: '11:30 AM',
  end_time: '12:30 PM',
  skill_level: 'Mid',
  spots_available: 3,
  total_spots: 10,
  status: 'open',
};

describe('GameCard', () => {
  it('renders the court, distance in miles, time, skill, and availability', async () => {
    await render(<GameCard game={game} distanceMiles={0.3} />);

    for (const text of [
      'Purdue CoRec - Court 2',
      '0.30 mi away',
      '11:30 AM – 12:30 PM',
      'Mid',
      '3 spots left',
    ]) {
      expect(screen.getByText(text)).toBeOnTheScreen();
    }
  });

  it.each([
    [0, 'Full'],
    [1, '1 spot left'],
    [5, '5 spots left'],
  ])('displays %i available spots as %s', async (spots, expected) => {
    await render(
      <GameCard
        game={{ ...game, spots_available: spots }}
        distanceMiles={0.04}
      />,
    );
    expect(screen.getByText(expected)).toBeOnTheScreen();
  });

  it('gives full status precedence over a conflicting positive spot count', async () => {
    await render(
      <GameCard game={{ ...game, status: 'full' }} distanceMiles={0.04} />,
    );
    expect(screen.getByText('Full')).toBeOnTheScreen();
    expect(screen.queryByText('3 spots left')).not.toBeOnTheScreen();
  });
});
