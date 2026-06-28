import React, { createContext, useContext, useEffect, useState } from 'react';
import { Stack, useRouter, useSegments } from 'expo-router';
import * as SecureStore from 'expo-secure-store';
import { ActivityIndicator, View } from 'react-native';

const API_KEY_STORE_KEY = 'mighty_api_key';

interface AuthContextValue {
  apiKey: string | null;
  setApiKey: (key: string) => Promise<void>;
  clearApiKey: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  apiKey: null,
  setApiKey: async () => {},
  clearApiKey: async () => {},
});

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const { apiKey } = useAuth();
  const segments = useSegments();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    const inTabs = segments[0] === '(tabs)';
    const inLogin = segments[0] === 'login';

    if (!apiKey && !inLogin) {
      router.replace('/login');
    } else if (apiKey && inLogin) {
      router.replace('/(tabs)/');
    }
  }, [apiKey, segments, ready]);

  return <>{children}</>;
}

export default function RootLayout() {
  const [apiKey, setApiKeyState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    SecureStore.getItemAsync(API_KEY_STORE_KEY)
      .then((stored) => {
        if (stored) setApiKeyState(stored);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const setApiKey = async (key: string) => {
    await SecureStore.setItemAsync(API_KEY_STORE_KEY, key);
    setApiKeyState(key);
  };

  const clearApiKey = async () => {
    await SecureStore.deleteItemAsync(API_KEY_STORE_KEY);
    setApiKeyState(null);
  };

  if (loading) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator size="large" />
      </View>
    );
  }

  return (
    <AuthContext.Provider value={{ apiKey, setApiKey, clearApiKey }}>
      <AuthGate>
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="login" />
          <Stack.Screen name="(tabs)" />
        </Stack>
      </AuthGate>
    </AuthContext.Provider>
  );
}
