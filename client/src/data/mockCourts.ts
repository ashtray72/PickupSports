import type { Court } from '../types/court';

/**
 * Demo games adapted from the origin/prototype campus seeds.
 * Distances are fixed examples from (40.4282, -86.9222), not device location.
 * Time strings are sample schedules without an associated date or time zone.
 */
export const mockCourts: Court[] = [
  {
    court_id: 'mock_place_corec_c2',
    name: 'Purdue CoRec - Court 2',
    distance_miles: 0.04,
    games: [
      {
        game_id: 'gm_corec_002',
        court_id: 'mock_place_corec_c2',
        court_name: 'Purdue CoRec - Court 2',
        start_time: '11:30 AM',
        end_time: '12:30 PM',
        skill_level: 'Mid',
        spots_available: 3,
        total_spots: 10,
        status: 'open',
      },
      {
        game_id: 'gm_corec_003',
        court_id: 'mock_place_corec_c2',
        court_name: 'Purdue CoRec - Court 2',
        start_time: '1:00 PM',
        end_time: '2:00 PM',
        skill_level: 'Mid',
        spots_available: 0,
        total_spots: 10,
        status: 'full',
      },
    ],
  },
  {
    court_id: 'mock_place_hillenbrand',
    name: 'Hillenbrand Hall Courts',
    distance_miles: 0.16,
    games: [
      {
        game_id: 'gm_hillenbrand_001',
        court_id: 'mock_place_hillenbrand',
        court_name: 'Hillenbrand Hall Courts',
        start_time: '5:00 PM',
        end_time: '6:00 PM',
        skill_level: 'Advanced',
        spots_available: 1,
        total_spots: 6,
        status: 'open',
      },
    ],
  },
  {
    court_id: 'mock_place_pickett_1',
    name: 'Pickett Park Basketball Courts',
    distance_miles: 0.3,
    games: [
      {
        game_id: 'gm_pickett_001',
        court_id: 'mock_place_pickett_1',
        court_name: 'Pickett Park Basketball Courts',
        start_time: '4:30 PM',
        end_time: '5:45 PM',
        skill_level: 'Beginner',
        spots_available: 5,
        total_spots: 10,
        status: 'open',
      },
    ],
  },
];
