import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { GameCard } from '../components/games/GameCard';
import { mockCourts } from '../data/mockCourts';

export function NearbyGamesScreen() {
  return (
    <SafeAreaView style={styles.page}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Text style={styles.brand}>PICKUPSPORTS</Text>
          <Text accessibilityRole="header" style={styles.title}>
            Nearby Basketball Games
          </Text>
          <Text style={styles.demo}>Demo games</Text>
          <Text style={styles.note}>
            Distances from the Purdue campus demo location.
          </Text>
        </View>
        {mockCourts.map((court) =>
          court.games.map((game) => (
            <GameCard
              key={game.game_id}
              game={game}
              distanceMiles={court.distance_miles}
            />
          )),
        )}
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
});
