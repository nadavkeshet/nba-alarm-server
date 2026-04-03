import React from 'react';
import { Clipboard, ScrollView, Text, TouchableOpacity } from 'react-native'; // eslint-disable-line deprecation/deprecation

interface State { error: Error | null }

function DebugScreen({ error }: { error: Error }) {
  const text = `${error?.toString()}\n\n${error?.stack ?? ''}`;
  return (
    <ScrollView style={{ flex: 1, backgroundColor: '#0B1623', padding: 20 }}>
      <Text style={{ color: '#FF6B00', fontSize: 16, fontWeight: 'bold', marginTop: 40 }}>
        ⚠️ CRASH — copy and send to dev:
      </Text>
      <TouchableOpacity
        onPress={() => Clipboard.setString(text)}
        style={{
          marginTop: 12, marginBottom: 8,
          backgroundColor: 'rgba(255,107,0,0.15)',
          padding: 10, borderRadius: 8,
          borderWidth: 1, borderColor: 'rgba(255,107,0,0.4)',
        }}
      >
        <Text style={{ color: '#FF6B00', fontWeight: '700', fontSize: 13, textAlign: 'center' }}>
          📋 Copy error to clipboard
        </Text>
      </TouchableOpacity>
      <Text style={{ color: '#e6edf3', fontSize: 11, marginTop: 8, lineHeight: 17, fontFamily: 'monospace' }}>
        {text}
      </Text>
    </ScrollView>
  );
}

export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return <DebugScreen error={this.state.error} />;
    }
    return this.props.children;
  }
}
