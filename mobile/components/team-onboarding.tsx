import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import React, { useMemo, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { WC2026_TEAMS, type WcTeam } from '../data/wc2026-teams';
import { followTeam } from '../services/follow-service';

const C = {
  bg:     '#0B1623',
  card:   '#0d1f35',
  border: '#1a3451',
  text:   '#e6edf3',
  muted:  '#6b8aad',
  orange: '#FF6B00',
  green:  '#22c55e',
};

interface Props {
  visible: boolean;
  onDone:  () => void;
}

export function TeamOnboarding({ visible, onDone }: Props) {
  const [search,  setSearch]  = useState('');
  const [picked,  setPicked]  = useState<Set<string>>(new Set());
  const [saving,  setSaving]  = useState(false);
  const [saved,   setSaved]   = useState(false);

  const filtered = useMemo(() => {
    if (!search.trim()) return WC2026_TEAMS;
    const q = search.toLowerCase();
    return WC2026_TEAMS.filter(t => t.name.toLowerCase().includes(q));
  }, [search]);

  const toggleTeam = (team: WcTeam) => {
    setPicked(prev => {
      const next = new Set(prev);
      if (next.has(team.id)) next.delete(team.id);
      else next.add(team.id);
      return next;
    });
  };

  const handleFollow = async () => {
    if (picked.size === 0) return;
    setSaving(true);
    try {
      const teamsToFollow = WC2026_TEAMS.filter(t => picked.has(t.id));
      // Sequential writes to avoid cache race condition in followTeam
      for (const team of teamsToFollow) {
        await followTeam({ id: team.id, name: team.name, sport: 'football', leagues: ['world_cup'] });
      }
      setSaved(true);
      setTimeout(onDone, 900);
    } catch {
      onDone();
    } finally {
      setSaving(false);
    }
  };

  const ctaLabel = picked.size === 0
    ? 'Select teams'
    : picked.size === 1
    ? `Follow ${WC2026_TEAMS.find(t => picked.has(t.id))?.name}`
    : `Follow ${picked.size} teams`;

  return (
    <Modal visible={visible} animationType="slide" statusBarTranslucent>
      <SafeAreaView style={styles.container}>

        {/* Header */}
        <View style={styles.header}>
          <View style={{ flex: 1 }} />
          <TouchableOpacity onPress={onDone} hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}>
            <Text style={styles.skipText}>Skip</Text>
          </TouchableOpacity>
        </View>

        {/* Title */}
        <View style={styles.titleBlock}>
          <Text style={styles.emoji}>🏆</Text>
          <Text style={styles.title}>Pick your teams</Text>
          <Text style={styles.subtitle}>Follow national teams — we'll show their matches in your feed.</Text>
        </View>

        {/* Search */}
        <View style={styles.searchBar}>
          <Ionicons name="search" size={16} color={C.muted} />
          <TextInput
            style={styles.searchInput}
            placeholder="Search country..."
            placeholderTextColor={C.muted}
            value={search}
            onChangeText={setSearch}
            autoCorrect={false}
            autoCapitalize="none"
          />
          {search.length > 0 && (
            <TouchableOpacity onPress={() => setSearch('')}>
              <Ionicons name="close-circle" size={16} color={C.muted} />
            </TouchableOpacity>
          )}
        </View>

        {/* Team grid */}
        <FlatList
          data={filtered}
          keyExtractor={item => item.id}
          numColumns={3}
          contentContainerStyle={styles.grid}
          renderItem={({ item }) => {
            const selected = picked.has(item.id);
            return (
              <TouchableOpacity
                style={[styles.teamCard, selected && styles.teamCardSelected]}
                onPress={() => toggleTeam(item)}
                activeOpacity={0.7}
              >
                <Image
                  source={{ uri: item.logo }}
                  style={styles.teamLogo}
                  contentFit="contain"
                />
                <Text style={[styles.teamName, selected && styles.teamNameSelected]} numberOfLines={2}>
                  {item.name}
                </Text>
                {selected && (
                  <View style={styles.checkBadge}>
                    <Ionicons name="checkmark-circle" size={16} color={C.orange} />
                  </View>
                )}
              </TouchableOpacity>
            );
          }}
        />

        {/* CTA */}
        <View style={styles.footer}>
          {picked.size > 0 ? (
            <TouchableOpacity
              style={[styles.ctaBtn, saved && { backgroundColor: C.green }]}
              onPress={handleFollow}
              disabled={saving || saved}
              activeOpacity={0.85}
            >
              {saving ? (
                <ActivityIndicator color="#fff" />
              ) : saved ? (
                <>
                  <Ionicons name="checkmark-circle" size={18} color="#fff" />
                  <Text style={styles.ctaText}>Done!</Text>
                </>
              ) : (
                <>
                  <Ionicons name="heart" size={18} color="#fff" />
                  <Text style={styles.ctaText}>{ctaLabel}</Text>
                </>
              )}
            </TouchableOpacity>
          ) : (
            <TouchableOpacity style={styles.skipBtn} onPress={onDone}>
              <Text style={styles.skipBtnText}>Skip for now</Text>
            </TouchableOpacity>
          )}
        </View>

      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container:         { flex: 1, backgroundColor: C.bg },
  header:            { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingTop: 8, paddingBottom: 4, minHeight: 44 },
  skipText:          { color: C.muted, fontSize: 14, fontWeight: '600' },
  titleBlock:        { alignItems: 'center', paddingHorizontal: 28, paddingBottom: 16, gap: 6 },
  emoji:             { fontSize: 40 },
  title:             { fontSize: 26, fontWeight: '900', color: C.text, letterSpacing: 0.3 },
  subtitle:          { fontSize: 14, color: C.muted, textAlign: 'center', lineHeight: 20 },
  searchBar:         { flexDirection: 'row', alignItems: 'center', gap: 8, marginHorizontal: 16, marginBottom: 10, paddingHorizontal: 12, paddingVertical: 9, backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border },
  searchInput:       { flex: 1, fontSize: 14, color: C.text, padding: 0 },
  grid:              { paddingHorizontal: 12, paddingBottom: 16 },
  teamCard:          { flex: 1, margin: 5, alignItems: 'center', padding: 10, borderRadius: 14, backgroundColor: C.card, borderWidth: 1.5, borderColor: C.border, position: 'relative', minHeight: 100, justifyContent: 'center', gap: 6 },
  teamCardSelected:  { borderColor: C.orange, backgroundColor: 'rgba(255,107,0,0.1)' },
  teamLogo:          { width: 44, height: 44, borderRadius: 4 },
  teamName:          { fontSize: 11, fontWeight: '700', color: C.muted, textAlign: 'center', lineHeight: 14 },
  teamNameSelected:  { color: C.text },
  checkBadge:        { position: 'absolute', top: 6, right: 6 },
  footer:            { paddingHorizontal: 20, paddingBottom: 16, paddingTop: 8 },
  ctaBtn:            { backgroundColor: C.orange, borderRadius: 16, paddingVertical: 16, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
  ctaText:           { color: '#fff', fontSize: 16, fontWeight: '800' },
  skipBtn:           { alignItems: 'center', paddingVertical: 14 },
  skipBtnText:       { color: C.muted, fontSize: 14, fontWeight: '600' },
});
