import React from 'react';
import { ScrollView, Text, View } from 'react-native';

interface State { error: Error | null }

export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <ScrollView style={{ flex: 1, backgroundColor: '#0B1623', padding: 20 }}>
          <Text style={{ color: '#FF6B00', fontSize: 16, fontWeight: 'bold', marginTop: 40 }}>
            CRASH — copy this and send:
          </Text>
          <Text style={{ color: '#e6edf3', fontSize: 12, marginTop: 12, lineHeight: 18 }}>
            {this.state.error?.toString()}
            {'\n\n'}
            {this.state.error?.stack}
          </Text>
        </ScrollView>
      );
    }
    return this.props.children;
  }
}
