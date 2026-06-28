import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { useAuth } from '../_layout';
import { getDashboardData, pushCapture, AccountCard } from '../../lib/api';
import { MOBILE_SITES, MobileSite } from '../../lib/sites';
import SyncWebView from '../../components/SyncWebView';

interface SyncState {
  status: 'idle' | 'syncing' | 'done' | 'skipped' | 'error';
  pagesCapured: number;
}

export default function SyncScreen() {
  const { apiKey } = useAuth();
  const [connectedSources, setConnectedSources] = useState<Set<string>>(new Set());
  const [syncStates, setSyncStates] = useState<Record<string, SyncState>>({});
  const [activeSite, setActiveSite] = useState<MobileSite | null>(null);
  const [syncAllQueue, setSyncAllQueue] = useState<string[]>([]);
  const [syncAllRunning, setSyncAllRunning] = useState(false);
  const [doneCount, setDoneCount] = useState(0);
  const syncAllQueueRef = useRef<string[]>([]);
  const capturedTextRef = useRef<string[]>([]);

  const loadConnected = useCallback(async () => {
    if (!apiKey) return;
    const result = await getDashboardData(apiKey);
    if (result.ok && result.data) {
      setConnectedSources(new Set(result.data.map((c) => c.source)));
    }
  }, [apiKey]);

  useEffect(() => {
    loadConnected();
  }, [loadConnected]);

  const setSiteStatus = (key: string, status: SyncState['status'], pages = 0) => {
    setSyncStates((prev) => ({ ...prev, [key]: { status, pagesCapured: pages } }));
  };

  const startSyncSite = (site: MobileSite) => {
    setSiteStatus(site.key, 'syncing');
    capturedTextRef.current = [];
    setActiveSite(site);
  };

  const handleData = (text: string) => {
    capturedTextRef.current.push(text);
  };

  const handleDone = useCallback(async () => {
    const site = activeSite;
    if (!site || !apiKey) return;

    setActiveSite(null);

    const pageCount = capturedTextRef.current.length;

    // Push each captured page to the server
    for (let i = 0; i < capturedTextRef.current.length; i++) {
      const url = site.accountPages[i] ?? site.loginUrl;
      await pushCapture(apiKey, site.key, capturedTextRef.current[i], url);
    }

    setSiteStatus(site.key, pageCount > 0 ? 'done' : 'error', pageCount);
    setConnectedSources((prev) => new Set([...prev, site.key]));

    // Advance sync-all queue
    if (syncAllQueueRef.current.length > 0) {
      const remaining = syncAllQueueRef.current.slice(1);
      syncAllQueueRef.current = remaining;
      setDoneCount((n) => n + 1);

      if (remaining.length > 0) {
        const nextKey = remaining[0];
        const nextSite = MOBILE_SITES.find((s) => s.key === nextKey);
        if (nextSite) {
          setTimeout(() => startSyncSite(nextSite), 600);
        }
      } else {
        setSyncAllRunning(false);
        setSyncAllQueue([]);
      }
    }
  }, [activeSite, apiKey]);

  const handleSkip = useCallback(() => {
    const site = activeSite;
    if (!site) return;
    setActiveSite(null);
    setSiteStatus(site.key, 'skipped');

    if (syncAllQueueRef.current.length > 0) {
      const remaining = syncAllQueueRef.current.slice(1);
      syncAllQueueRef.current = remaining;
      setDoneCount((n) => n + 1);

      if (remaining.length > 0) {
        const nextKey = remaining[0];
        const nextSite = MOBILE_SITES.find((s) => s.key === nextKey);
        if (nextSite) {
          setTimeout(() => startSyncSite(nextSite), 600);
        }
      } else {
        setSyncAllRunning(false);
        setSyncAllQueue([]);
      }
    }
  }, [activeSite]);

  const handleSyncAll = () => {
    const queue = MOBILE_SITES.map((s) => s.key);
    syncAllQueueRef.current = queue;
    setSyncAllQueue(queue);
    setSyncAllRunning(true);
    setDoneCount(0);

    const firstSite = MOBILE_SITES[0];
    if (firstSite) startSyncSite(firstSite);
  };

  const totalSites = MOBILE_SITES.length;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Sync Accounts</Text>
        <Text style={styles.headerSub}>
          Open each site, log in once, and Mighty captures your data.
        </Text>
      </View>

      {syncAllRunning && (
        <View style={styles.progressBar}>
          <View
            style={[
              styles.progressFill,
              { width: `${Math.round((doneCount / totalSites) * 100)}%` },
            ]}
          />
          <Text style={styles.progressLabel}>
            {activeSite ? `Syncing ${activeSite.name}…` : 'Starting…'}{' '}
            {doneCount}/{totalSites} done
          </Text>
        </View>
      )}

      <ScrollView contentContainerStyle={styles.list}>
        <TouchableOpacity
          style={[styles.syncAllButton, syncAllRunning && styles.syncAllDisabled]}
          onPress={handleSyncAll}
          disabled={syncAllRunning}
        >
          <Text style={styles.syncAllText}>
            {syncAllRunning ? 'Syncing all…' : '⚡ Sync All'}
          </Text>
        </TouchableOpacity>

        {MOBILE_SITES.map((site) => {
          const state = syncStates[site.key];
          const isActive = activeSite?.key === site.key;

          let statusLabel = connectedSources.has(site.key) ? '✓ Connected' : 'Not synced';
          let statusColor = connectedSources.has(site.key) ? '#10b981' : '#9ca3af';

          if (state) {
            if (state.status === 'syncing' || isActive) {
              statusLabel = 'Syncing…';
              statusColor = '#6366f1';
            } else if (state.status === 'done') {
              statusLabel = `✓ ${state.pagesCapured} page${state.pagesCapured !== 1 ? 's' : ''} captured`;
              statusColor = '#10b981';
            } else if (state.status === 'skipped') {
              statusLabel = 'Skipped';
              statusColor = '#9ca3af';
            } else if (state.status === 'error') {
              statusLabel = '⚠ No data captured';
              statusColor = '#f97316';
            }
          }

          return (
            <View key={site.key} style={styles.siteRow}>
              <Text style={styles.siteEmoji}>{site.emoji}</Text>
              <View style={styles.siteMeta}>
                <Text style={styles.siteName}>{site.name}</Text>
                <Text style={[styles.siteStatus, { color: statusColor }]}>
                  {statusLabel}
                </Text>
              </View>
              <TouchableOpacity
                style={[styles.syncButton, (isActive || syncAllRunning) && styles.syncButtonDisabled]}
                onPress={() => startSyncSite(site)}
                disabled={isActive || syncAllRunning}
              >
                <Text style={styles.syncButtonText}>Sync</Text>
              </TouchableOpacity>
            </View>
          );
        })}
      </ScrollView>

      <Modal visible={activeSite !== null} animationType="slide">
        {activeSite && (
          <SyncWebView
            source={activeSite.key}
            loginUrl={activeSite.loginUrl}
            accountPages={activeSite.accountPages}
            siteName={activeSite.name}
            onData={handleData}
            onDone={handleDone}
            onSkip={handleSkip}
          />
        )}
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  header: {
    backgroundColor: '#fff',
    paddingTop: 56,
    paddingBottom: 16,
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  headerTitle: { fontSize: 22, fontWeight: '700', color: '#111827' },
  headerSub: { fontSize: 13, color: '#6b7280', marginTop: 4 },
  progressBar: {
    backgroundColor: '#ede9fe',
    paddingVertical: 10,
    paddingHorizontal: 16,
    position: 'relative',
  },
  progressFill: {
    position: 'absolute',
    top: 0,
    left: 0,
    bottom: 0,
    backgroundColor: '#6366f1',
    opacity: 0.15,
  },
  progressLabel: { fontSize: 13, color: '#4338ca', fontWeight: '500' },
  list: { padding: 16 },
  syncAllButton: {
    backgroundColor: '#4f46e5',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    marginBottom: 20,
  },
  syncAllDisabled: { opacity: 0.5 },
  syncAllText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  siteRow: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 10,
    shadowColor: '#000',
    shadowOpacity: 0.03,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  siteEmoji: { fontSize: 22, marginRight: 12 },
  siteMeta: { flex: 1 },
  siteName: { fontSize: 14, fontWeight: '600', color: '#111827' },
  siteStatus: { fontSize: 11, marginTop: 2 },
  syncButton: {
    backgroundColor: '#ede9fe',
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  syncButtonDisabled: { opacity: 0.4 },
  syncButtonText: { fontSize: 13, fontWeight: '600', color: '#4f46e5' },
});
