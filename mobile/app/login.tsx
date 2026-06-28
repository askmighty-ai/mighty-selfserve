import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Alert,
  ScrollView,
} from 'react-native';
import { useAuth } from './_layout';
import { getMe } from '../lib/api';

export default function LoginScreen() {
  const { setApiKey } = useAuth();
  const [key, setKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConnect = async () => {
    const trimmed = key.trim();
    if (!trimmed) {
      setError('Please enter your Mighty API key.');
      return;
    }

    setLoading(true);
    setError(null);

    const result = await getMe(trimmed);

    setLoading(false);

    if (!result.ok) {
      setError(
        result.status === 401
          ? 'Invalid API key. Check your key and try again.'
          : `Connection failed (${result.status ?? 'network error'}). Make sure you're online.`
      );
      return;
    }

    await setApiKey(trimmed);
    // Navigation handled automatically by AuthGate in _layout
  };

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        contentContainerStyle={styles.container}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.logoBlock}>
          <Text style={styles.logoText}>✦ Mighty</Text>
          <Text style={styles.tagline}>Your loyalty accounts, at a glance.</Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.label}>Mighty API Key</Text>
          <TextInput
            style={styles.input}
            value={key}
            onChangeText={(v) => {
              setKey(v);
              setError(null);
            }}
            placeholder="mk_live_..."
            placeholderTextColor="#9ca3af"
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry={false}
            returnKeyType="done"
            onSubmitEditing={handleConnect}
          />
          <Text style={styles.hint}>
            Find your key at mighty-selfserve-production.up.railway.app/settings
          </Text>

          {error ? <Text style={styles.errorText}>{error}</Text> : null}

          <TouchableOpacity
            style={[styles.button, loading && styles.buttonDisabled]}
            onPress={handleConnect}
            disabled={loading}
          >
            {loading ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.buttonText}>Connect</Text>
            )}
          </TouchableOpacity>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: '#f9fafb' },
  container: {
    flexGrow: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  logoBlock: {
    alignItems: 'center',
    marginBottom: 40,
  },
  logoText: {
    fontSize: 32,
    fontWeight: '700',
    color: '#111827',
    letterSpacing: -0.5,
  },
  tagline: {
    fontSize: 14,
    color: '#6b7280',
    marginTop: 6,
  },
  card: {
    width: '100%',
    backgroundColor: '#fff',
    borderRadius: 16,
    padding: 24,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 3,
  },
  label: {
    fontSize: 13,
    fontWeight: '600',
    color: '#374151',
    marginBottom: 8,
  },
  input: {
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 10,
    padding: 14,
    fontSize: 15,
    color: '#111827',
    backgroundColor: '#f9fafb',
  },
  hint: {
    fontSize: 11,
    color: '#9ca3af',
    marginTop: 8,
    lineHeight: 16,
  },
  errorText: {
    fontSize: 13,
    color: '#dc2626',
    marginTop: 12,
    lineHeight: 18,
  },
  button: {
    backgroundColor: '#4f46e5',
    borderRadius: 10,
    padding: 15,
    alignItems: 'center',
    marginTop: 20,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  buttonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
});
