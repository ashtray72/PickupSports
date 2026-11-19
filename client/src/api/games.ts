import type { GameSummary } from '../types/court';

/** A game from GET /api/v1/games/recommended, already in best-match order. */
export type RecommendedGame = GameSummary & {
  distance_miles: number;
  match_score: number;
};

export type RecommendedGamesResponse = {
  player_rating: number;
  is_provisional: boolean;
  radius_miles: number;
  games: RecommendedGame[];
};

/** Skill chips on the feed. 'Any' sends no filter. */
export type SkillFilter = 'Any' | 'Beginner' | 'Mid' | 'Advanced';

export type Location = { lat: number; lng: number };

/** Fixed Purdue campus demo location until the app asks for device location. */
export const DEMO_LOCATION: Location = { lat: 40.4282, lng: -86.9222 };

/**
 * Backend address, e.g. http://192.168.1.20:8000 (your computer's Wi-Fi IP when
 * testing on a phone). Unset means the app shows the built-in demo games.
 */
export const API_URL = process.env.EXPO_PUBLIC_API_URL;

/**
 * Prototype sign-in: the backend reads this as the player's Firebase UID until
 * real Firebase tokens are wired up.
 */
export const DEV_USER = process.env.EXPO_PUBLIC_DEV_USER || 'seed:demo-player';

const REQUEST_TIMEOUT_MS = 5000;

type FetchOptions = {
  baseUrl: string;
  skill: SkillFilter;
  location?: Location;
  devUser?: string;
};

function buildQuery(params: Record<string, string>): string {
  const parts: string[] = [];
  for (const key of Object.keys(params)) {
    parts.push(`${key}=${encodeURIComponent(params[key])}`);
  }
  return parts.join('&');
}

export async function fetchRecommendedGames(
  options: FetchOptions,
): Promise<RecommendedGame[]> {
  const location = options.location || DEMO_LOCATION;
  const devUser = options.devUser || DEV_USER;

  const params: Record<string, string> = {
    lat: String(location.lat),
    lng: String(location.lng),
  };
  if (options.skill !== 'Any') {
    params.skill = options.skill;
  }
  const url = `${options.baseUrl}/api/v1/games/recommended?${buildQuery(params)}`;

  // Give up after a few seconds so an unreachable server falls back to demo data.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      headers: { 'X-Dev-User': devUser },
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Server returned ${response.status}`);
    }
    const body = (await response.json()) as RecommendedGamesResponse;
    return body.games;
  } finally {
    clearTimeout(timer);
  }
}
