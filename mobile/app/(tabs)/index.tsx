import React, { useCallback, useEffect, useState } from 'react';
import {
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  View,
  TouchableOpacity,
} from 'react-native';
import { useAuth } from '../_layout';
import { getDashboardData, AccountCard } from '../../lib/api';

const TWO_HOURS_MS = 2 * 60 * 60 * 1000;

function timeAgo(isoStr: string | null | undefined): string {
  if (!isoStr) return 'Never synced';
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function isStale(isoStr: string | null | undefined): boolean {
  if (!isoStr) return true;
  return Date.now() - new Date(isoStr).getTime() > TWO_HOURS_MS;
}

function AccountCardRow({ card }: { card: AccountCard }) {
  const stale = isStale(card.last_synced_at);

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardEmoji}>{card.emoji ?? '🏦'}</Text>
        <View style={styles.cardTitles}>
          <Text style={styles.cardName} numberOfLines={1}>
            {card.display_name ?? card.source}
          </Text>
          <Text style={[styles.syncedAt, stale && styles.syncedAtStale]}>
            {timeAgo(card.last_synced_at)}
          </Text>
        </View>
      </View>

      {card.top_fields && card.top_fields.length > 0 && (
        <View style={styles.cardFields}>
          {card.top_fields.map((f, i) => (
            <View key={i} style={styles.fieldRow}>
              <Text style={styles.fieldLabel} numberOfLines={1}>
                {f.label}
              </Text>
              <Text style={styles.fieldValue} numberOfLines={1}>
                {f.value}
              </Text>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

export default function DashboardScreen() {
  const { apiKey } = useAuth();
  const [accounts, setAccounts] = useState<AccountCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [anyStale, setAnyStale] = useState(false);

  const loadData = useCallback(
    async (silent = false) => {
      if (!apiKey) return;
      if (!silent) setLoading(true);
      setError(null);

      const result = await getDashboardData(apiKey);

      if (result.ok && result.data) {
        setAccounts(result.data);
        setAnyStale(result.data.some((c) => isStale(c.last_synced_at)));
      } else {
        setError('Could not load accounts. Pull down to retry.');
      }

      setLoading(false);
      setRefreshing(false);
    },
    [apiKey]
  );

  useEffect(() => {
    loadData();
  }, [loadData]);

  const onRefresh = () => {
    setRefreshing(true);
    loadData(true);
  };

  if (loading) {
    return (
      <View style={styles.center}>
        <Text style={styles.loadingText}>Loading accounts…</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>✦ Mighty</Text>
      </View>

      {anyStale && (
        <View style={styles.nudge}>
          <Text style={styles.nudgeText}>
            Some accounts haven't synced recently. Go to Sync to refresh.
          </Text>
        </View>
      )}

      {error && (
        <View style={styles.errorBanner}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      <FlatList
        data={accounts}
        keyExtractor={(item) => item.source}
        renderItem={({ item }) => <AccountCardRow card={item} />}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>No accounts synced yet.</Text>
            <Text style={styles.emptyHint}>
              Visit the Sync tab to connect your first account.
            </Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  loadingText: { color: '#6b7280', fontSize: 15 },
  header: {
    backgroundColor: '#fff',
    paddingTop: 56,
    paddingBottom: 14,
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  headerTitle: { fontSize: 22, fontWeight: '700', color: '#111827' },
  nudge: {
    backgroundColor: '#fffbeb',
    borderBottomWidth: 1,
    borderBottomColor: '#fef3c7',
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  nudgeText: { fontSize: 13, color: '#92400e' },
  errorBanner: {
    backgroundColor: '#fef2f2',
    borderBottomWidth: 1,
    borderBottomColor: '#fecaca',
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  errorText: { fontSize: 13, color: '#dc2626' },
  list: { padding: 16, gap: 12 },
  card: {
    backgroundColor: '#fff',
    borderRadius: 14,
    padding: 16,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
    marginBottom: 12,
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: 10 },
  cardEmoji: { fontSize: 24, marginRight: 12 },
  cardTitles: { flex: 1 },
  cardName: { fontSize: 15, fontWeight: '600', color: '#111827' },
  syncedAt: { fontSize: 11, color: '#9ca3af', marginTop: 2 },
  syncedAtStale: { color: '#f97316' },
  cardFields: { borderTopWidth: 1, borderTopColor: '#f3f4f6', paddingTop: 10, gap: 6 },
  fieldRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  fieldLabel: { fontSize: 12, color: '#6b7280', flex: 1 },
  fieldValue: { fontSize: 13, fontWeight: '500', color: '#111827', flex: 1, textAlign: 'right' },
  empty: { alignItems: 'center', paddingTop: 60 },
  emptyText: { fontSize: 16, color: '#374151', fontWeight: '500' },
  emptyHint: { fontSize: 13, color: '#9ca3af', marginTop: 8, textAlign: 'center' },
});
