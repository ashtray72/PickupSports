import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { API_URL, fetchRecommendedGames } from '../api/games';
import type { SkillFilter } from '../api/games';
import { GameCard } from '../components/games/GameCard';
import { mockCourts } from '../data/mockCourts';
import type { GameSummary } from '../types/court';

const SKILL_FILTERS: SkillFilter[] = ['Any', 'Beginner', 'Mid', 'Advanced'];

type FeedItem = { game: GameSummary; distanceMiles: number };
type FeedSource = 'demo' | 'live' | 'loading';
type LiveResult = { skill: SkillFilter; items: FeedItem[]; failed: boolean };

type NearbyGamesScreenProps = {
  /** Backend address. Leave unset to show the built-in demo games. */
  apiUrl?: string;
};

function skillMatches(game: GameSummary, skill: SkillFilter): boolean {
  if (skill === 'Any') {
    return true;
  }
  // All-levels games fit every skill chip.
  return game.skill_level === skill || game.skill_level === 'All';
}

function demoFeed(skill: SkillFilter): FeedItem[] {
  const items: FeedItem[] = [];
  for (const court of mockCourts) {
    for (const game of court.games) {
      if (skillMatches(game, skill)) {
        items.push({ game, distanceMiles: court.distance_miles });
      }
    }
  }
  return items;
}

function filterLabel(skill: SkillFilter): string {
  if (skill === 'Any') {
    return 'Any level';
  }
  return skill;
}

export function NearbyGamesScreen({
  apiUrl = API_URL,
}: NearbyGamesScreenProps) {
  const [skill, setSkill] = useState<SkillFilter>('Any');
  const [liveResult, setLiveResult] = useState<LiveResult | null>(null);

  useEffect(() => {
    if (!apiUrl) {
      return;
    }
    // Ignore a response that arrives after the user picked another chip.
    let stale = false;
    fetchRecommendedGames({ baseUrl: apiUrl, skill })
      .then((games) => {
        if (stale) {
          return;
        }
        const items: FeedItem[] = [];
        for (const game of games) {
          items.push({ game, distanceMiles: game.distance_miles });
        }
        setLiveResult({ skill, items, failed: false });
      })
      .catch(() => {
        if (stale) {
          return;
        }
        setLiveResult({ skill, items: [], failed: true });
      });
    return () => {
      stale = true;
    };
  }, [apiUrl, skill]);

  // Demo data unless the server answered for the current chip.
  let source: FeedSource = 'demo';
  let items = demoFeed(skill);
  let serverError = false;
  if (apiUrl) {
    if (liveResult === null || liveResult.skill !== skill) {
      source = 'loading';
    } else if (liveResult.failed) {
      serverError = true;
    } else {
      source = 'live';
      items = liveResult.items;
    }
  }

  let sourceLabel = 'Demo games';
  if (source === 'live') {
    sourceLabel = 'Live games, best match first';
  }
  if (source === 'loading') {
    sourceLabel = 'Loading games…';
  }

  return (
    <SafeAreaView style={styles.page}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Text style={styles.brand}>PICKUPSPORTS</Text>
          <Text accessibilityRole="header" style={styles.title}>
            Nearby Basketball Games
          </Text>
          <Text style={styles.demo}>{sourceLabel}</Text>
          <Text style={styles.note}>
            Distances from the Purdue campus demo location.
          </Text>
          {serverError && (
            <Text style={styles.note}>
              Couldn’t reach the server, so these are demo games.
            </Text>
          )}
        </View>

        <View style={styles.filters}>
          {SKILL_FILTERS.map((option) => {
            const selected = option === skill;
            return (
              <Pressable
                key={option}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                onPress={() => setSkill(option)}
                style={[styles.chip, selected && styles.chipSelected]}
              >
                <Text
                  style={[styles.chipText, selected && styles.chipTextSelected]}
                >
                  {filterLabel(option)}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {source === 'loading' && <ActivityIndicator />}

        {source !== 'loading' && items.length === 0 && (
          <Text style={styles.empty}>
            No games match this skill level. Try another level or create a game.
          </Text>
        )}

        {source !== 'loading' &&
          items.map((item) => (
            <GameCard
              key={item.game.game_id}
              game={item.game}
              distanceMiles={item.distanceMiles}
            />
          ))}
      </ScrollView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: '#F7F5F0' },
  content: {
    padding: 20,
    paddingBottom: 32,
    gap: 16,
    width: '100%',
    maxWidth: 640,
    alignSelf: 'center',
  },
  header: { gap: 12, paddingVertical: 20 },
  brand: {
    fontSize: 12,
    letterSpacing: 2,
    fontWeight: '700',
    color: '#A84322',
  },
  title: { fontSize: 34, lineHeight: 40, fontWeight: '800', color: '#242520' },
  demo: { fontSize: 14, fontWeight: '600', color: '#504E48' },
  note: { fontSize: 13, lineHeight: 20, color: '#68655F' },
  filters: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    borderColor: '#E7E2DA',
    borderRadius: 18,
    borderWidth: 1,
    backgroundColor: '#FFFFFF',
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  chipSelected: { backgroundColor: '#242520', borderColor: '#242520' },
  chipText: { fontSize: 14, fontWeight: '600', color: '#504E48' },
  chipTextSelected: { color: '#FFFFFF' },
  empty: { fontSize: 15, lineHeight: 22, color: '#504E48' },
});
