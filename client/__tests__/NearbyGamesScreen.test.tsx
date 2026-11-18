import { render, screen, within } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { NearbyGamesScreen } from '../src/screens/NearbyGamesScreen';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, right: 0, bottom: 34, left: 0 },
};

describe('NearbyGamesScreen', () => {
  beforeEach(async () => {
    await render(
      <SafeAreaProvider initialMetrics={metrics}>
        <NearbyGamesScreen />
      </SafeAreaProvider>,
    );
  });

  it('explains that the games and location are demo data', () => {
    expect(
      screen.getByRole('header', { name: 'Nearby Basketball Games' }),
    ).toBeOnTheScreen();
    expect(screen.getByText('Demo games')).toBeOnTheScreen();
    expect(
      screen.getByText('Distances from the Purdue campus demo location.'),
    ).toBeOnTheScreen();
  });

  it('renders four separate games, including both games at CoRec Court 2', () => {
    expect(screen.getAllByTestId(/^game-card-/)).toHaveLength(4);
    expect(screen.getAllByText('Purdue CoRec - Court 2')).toHaveLength(2);
    expect(
      within(screen.getByTestId('game-card-gm_corec_002')).getByText(
        '11:30 AM – 12:30 PM',
      ),
    ).toBeOnTheScreen();
    expect(
      within(screen.getByTestId('game-card-gm_corec_003')).getByText(
        '1:00 PM – 2:00 PM',
      ),
    ).toBeOnTheScreen();
  });

  it.each([
    [
      'gm_corec_002',
      'Purdue CoRec - Court 2',
      '0.04 mi away',
      '11:30 AM – 12:30 PM',
      'Mid',
      '3 spots left',
    ],
    [
      'gm_corec_003',
      'Purdue CoRec - Court 2',
      '0.04 mi away',
      '1:00 PM – 2:00 PM',
      'Mid',
      'Full',
    ],
    [
      'gm_hillenbrand_001',
      'Hillenbrand Hall Courts',
      '0.16 mi away',
      '5:00 PM – 6:00 PM',
      'Advanced',
      '1 spot left',
    ],
    [
      'gm_pickett_001',
      'Pickett Park Basketball Courts',
      '0.30 mi away',
      '4:30 PM – 5:45 PM',
      'Beginner',
      '5 spots left',
    ],
  ])('pairs the correct information on %s', (id, ...expected) => {
    const card = within(screen.getByTestId(`game-card-${id}`));
    for (const text of expected) {
      expect(card.getByText(text)).toBeOnTheScreen();
    }
  });
});
