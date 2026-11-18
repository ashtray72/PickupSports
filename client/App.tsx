import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { NearbyGamesScreen } from './src/screens/NearbyGamesScreen';
export default function App() {
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <NearbyGamesScreen />
    </SafeAreaProvider>
  );
}
