import React, { useState } from 'react';
import {
  Alert,
  Linking,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
  ScrollView,
} from 'react-native';
import { useAuth } from '../_layout';

function maskKey(key: string): string {
  if (key.length <= 8) return '••••••••';
  return key.slice(0, 6) + '••••••••' + key.slice(-4);
}

export default function SettingsScreen() {
  const { apiKey, clearApiKey } = useAuth();
  const [showKey, setShowKey] = useState(false);

  const handleDisconnect = () => {
    Alert.alert(
      'Disconnect',
      'This will remove your API key from the device. Your data on the server is not deleted.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Disconnect',
          style: 'destructive',
          onPress: () => clearApiKey(),
        },
      ]
    );
  };

  const handleOpenDashboard = () => {
    Linking.openURL('https://mighty-selfserve-production.up.railway.app/dashboard');
  };

  const handleOpenSettings = () => {
    Linking.openURL('https://mighty-selfserve-production.up.railway.app/settings');
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Settings</Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionLabel}>API KEY</Text>
        <View style={styles.keyRow}>
          <Text style={styles.keyText} numberOfLines={1}>
            {showKey ? apiKey ?? '' : maskKey(apiKey ?? '')}
          </Text>
          <TouchableOpacity onPress={() => setShowKey((v) => !v)}>
            <Text style={styles.toggleLabel}>{showKey ? 'Hide' : 'Show'}</Text>
          </TouchableOpacity>
        </View>
        <Text style={styles.keyHint}>
          Stored securely in device keychain. Never sent anywhere except mighty-selfserve-production.up.railway.app.
        </Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionLabel}>ACCOUNT</Text>

        <TouchableOpacity style={styles.linkRow} onPress={handleOpenDashboard}>
          <Text style={styles.linkText}>Open Dashboard in Browser</Text>
          <Text style={styles.linkArrow}>→</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.linkRow} onPress={handleOpenSettings}>
          <Text style={styles.linkText}>Manage Settings on Web</Text>
          <Text style={styles.linkArrow}>→</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.section}>
        <TouchableOpacity style={styles.disconnectButton} onPress={handleDisconnect}>
          <Text style={styles.disconnectText}>Disconnect This Device</Text>
        </TouchableOpacity>
        <Text style={styles.disconnectHint}>
          Removes your API key from this device only. Your account and synced data are not affected.
        </Text>
      </View>

      <Text style={styles.version}>Mighty Mobile · Expo SDK 51</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  content: { padding: 0, paddingBottom: 40 },
  header: {
    backgroundColor: '#fff',
    paddingTop: 56,
    paddingBottom: 16,
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
    marginBottom: 24,
  },
  headerTitle: { fontSize: 22, fontWeight: '700', color: '#111827' },
  section: {
    backgroundColor: '#fff',
    marginHorizontal: 16,
    borderRadius: 14,
    padding: 16,
    marginBottom: 16,
    shadowColor: '#000',
    shadowOpacity: 0.03,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  sectionLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: '#9ca3af',
    letterSpacing: 0.8,
    marginBottom: 12,
  },
  keyRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#f9fafb',
    borderRadius: 8,
    padding: 12,
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  keyText: {
    fontSize: 13,
    color: '#374151',
    fontFamily: 'Courier',
    flex: 1,
  },
  toggleLabel: {
    fontSize: 13,
    color: '#6366f1',
    fontWeight: '500',
    marginLeft: 10,
  },
  keyHint: {
    fontSize: 11,
    color: '#9ca3af',
    marginTop: 8,
    lineHeight: 16,
  },
  linkRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  linkText: { fontSize: 15, color: '#111827' },
  linkArrow: { fontSize: 15, color: '#9ca3af' },
  disconnectButton: {
    backgroundColor: '#fef2f2',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#fecaca',
  },
  disconnectText: {
    fontSize: 15,
    fontWeight: '600',
    color: '#dc2626',
  },
  disconnectHint: {
    fontSize: 11,
    color: '#9ca3af',
    marginTop: 10,
    textAlign: 'center',
    lineHeight: 16,
  },
  version: {
    textAlign: 'center',
    fontSize: 11,
    color: '#d1d5db',
    marginTop: 8,
  },
});
