import { StyleSheet, Text, View } from 'react-native';

import type { GameSummary } from '../../types/court';

type GameCardProps = {
  game: GameSummary;
  distanceMiles: number;
};

export function GameCard({ game, distanceMiles }: GameCardProps) {
  const isFull = game.status === 'full' || game.spots_available <= 0;
  const availability = isFull
    ? 'Full'
    : `${game.spots_available} ${game.spots_available === 1 ? 'spot' : 'spots'} left`;

  return (
    <View style={styles.card} testID={`game-card-${game.game_id}`}>
      <View style={styles.metaRow}>
        <Text style={styles.sport}>BASKETBALL</Text>
        <Text
          style={styles.distance}
        >{`${distanceMiles.toFixed(2)} mi away`}</Text>
      </View>

      <View style={styles.heading}>
        <Text style={styles.courtName}>{game.court_name}</Text>
        <Text
          style={styles.time}
        >{`${game.start_time} – ${game.end_time}`}</Text>
      </View>

      <View style={styles.detailsRow}>
        <View style={styles.skill}>
          <Text style={styles.detailLabel}>Skill level</Text>
          <Text style={styles.skillValue}>{game.skill_level}</Text>
        </View>
        <View style={[styles.availabilityPill, isFull && styles.fullPill]}>
          <Text style={[styles.availabilityText, isFull && styles.fullText]}>
            {availability}
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#FFFFFF',
    borderColor: '#E7E2DA',
    borderRadius: 22,
    borderWidth: 1,
    gap: 20,
    padding: 20,
  },
  metaRow: {
    alignItems: 'center',
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    justifyContent: 'space-between',
  },
  sport: {
    color: '#A84322',
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.2,
    lineHeight: 18,
  },
  distance: {
    color: '#68655F',
    fontSize: 13,
    lineHeight: 20,
  },
  heading: {
    gap: 8,
  },
  courtName: {
    color: '#242520',
    fontSize: 21,
    fontWeight: '700',
    lineHeight: 28,
  },
  time: {
    color: '#504E48',
    fontSize: 15,
    lineHeight: 23,
  },
  detailsRow: {
    alignItems: 'center',
    borderTopColor: '#F0ECE5',
    borderTopWidth: 1,
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    justifyContent: 'space-between',
    paddingTop: 16,
  },
  skill: {
    gap: 3,
  },
  detailLabel: {
    color: '#68655F',
    fontSize: 12,
    lineHeight: 18,
  },
  skillValue: {
    color: '#242520',
    fontSize: 15,
    fontWeight: '600',
    lineHeight: 22,
  },
  availabilityPill: {
    backgroundColor: '#EAF2E9',
    borderRadius: 12,
    maxWidth: '100%',
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  availabilityText: {
    color: '#375B3C',
    fontSize: 13,
    fontWeight: '600',
    lineHeight: 20,
  },
  fullPill: {
    backgroundColor: '#F0EFEC',
  },
  fullText: {
    color: '#605E58',
  },
});
