import { fireEvent, render, screen } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { RecommendedGame } from '../src/api/games';
import { NearbyGamesScreen } from '../src/screens/NearbyGamesScreen';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, right: 0, bottom: 34, left: 0 },
};

const API = 'http://api.test';

function liveGame(
  id: string,
  courtName: string,
  skill: RecommendedGame['skill_level'],
): RecommendedGame {
  return {
    game_id: id,
    court_id: `court-${id}`,
    court_name: courtName,
    start_time: '5:00 PM',
    end_time: '6:00 PM',
    skill_level: skill,
    spots_available: 2,
    total_spots: 10,
    status: 'open',
    distance_miles: 0.5,
    match_score: 0.8,
  };
}

function respondWith(games: RecommendedGame[]) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        player_rating: 4,
        is_provisional: true,
        radius_miles: 5,
        games,
      }),
  });
}

async function renderScreen(apiUrl?: string) {
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <NearbyGamesScreen apiUrl={apiUrl} />
    </SafeAreaProvider>,
  );
}

const fetchMock = jest.fn();

beforeEach(() => {
  fetchMock.mockReset();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

describe('skill filter with demo data', () => {
  it.each([
    ['Mid', 2],
    ['Advanced', 1],
    ['Beginner', 1],
    ['Any level', 4],
  ])('shows the right number of games for %s', async (chip, count) => {
    await renderScreen();
    await fireEvent.press(screen.getByRole('button', { name: chip }));
    expect(screen.getAllByTestId(/^game-card-/)).toHaveLength(count);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('marks the selected chip', async () => {
    await renderScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Mid' }));
    expect(screen.getByRole('button', { name: 'Mid' })).toBeSelected();
    expect(
      screen.getByRole('button', { name: 'Any level' }),
    ).not.toBeSelected();
  });
});

describe('live feed from the API', () => {
  it('shows games in the order the server ranked them', async () => {
    fetchMock.mockImplementation(() =>
      respondWith([
        liveGame('best', 'Hillenbrand Hall Courts', 'Advanced'),
        liveGame('next', 'Purdue CoRec - Court 2', 'Mid'),
      ]),
    );
    await renderScreen(API);

    expect(
      await screen.findByText('Live games, best match first'),
    ).toBeOnTheScreen();
    const ids = screen
      .getAllByTestId(/^game-card-/)
      .map((card) => card.props.testID);
    expect(ids).toEqual(['game-card-best', 'game-card-next']);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      `${API}/api/v1/games/recommended?lat=40.4282&lng=-86.9222`,
    );
    expect(init.headers).toEqual({ 'X-Dev-User': 'seed:demo-player' });
  });

  it('sends the chosen skill level to the server', async () => {
    fetchMock.mockImplementation(() => respondWith([]));
    await renderScreen(API);
    await screen.findByText('Live games, best match first');

    await fireEvent.press(screen.getByRole('button', { name: 'Mid' }));
    await screen.findByText('Live games, best match first');

    const lastUrl = fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0];
    expect(lastUrl).toContain('skill=Mid');
  });

  it('tells the player when nothing matches', async () => {
    fetchMock.mockImplementation(() => respondWith([]));
    await renderScreen(API);
    expect(
      await screen.findByText(
        'No games match this skill level. Try another level or create a game.',
      ),
    ).toBeOnTheScreen();
  });

  it('falls back to demo games when the server is unreachable', async () => {
    fetchMock.mockImplementation(() =>
      Promise.reject(new Error('Network request failed')),
    );
    await renderScreen(API);
    expect(
      await screen.findByText(
        'Couldn’t reach the server, so these are demo games.',
      ),
    ).toBeOnTheScreen();
    expect(screen.getByText('Demo games')).toBeOnTheScreen();
    expect(screen.getAllByTestId(/^game-card-/)).toHaveLength(4);
  });

  it('falls back to demo games when the server returns an error', async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({}),
      }),
    );
    await renderScreen(API);
    expect(
      await screen.findByText(
        'Couldn’t reach the server, so these are demo games.',
      ),
    ).toBeOnTheScreen();
  });
});
