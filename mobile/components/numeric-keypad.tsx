import React, { useRef, useState } from 'react';
import {
  Modal,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useNumericKeypad() {
  const [visible,  setVisible]  = useState(false);
  const [value,    setValue]    = useState('');
  const [label,    setLabel]    = useState('');
  const [maxLen,   setMaxLen]   = useState(3);
  const setterRef = useRef<(v: string) => void>(() => {});

  const open = (
    currentVal: string,
    setter:     (v: string) => void,
    lbl = '',
    ml  = 3,
  ) => {
    setValue(currentVal);
    setterRef.current = setter;
    setLabel(lbl);
    setMaxLen(ml);
    setVisible(true);
  };

  const close = () => setVisible(false);

  const handleChange = (v: string) => {
    setValue(v);
    setterRef.current(v);
  };

  return {
    kpVisible:  visible,
    kpValue:    value,
    kpLabel:    label,
    kpMaxLen:   maxLen,
    openKp:     open,
    closeKp:    close,
    onKpChange: handleChange,
  };
}

// ─── Component ────────────────────────────────────────────────────────────────

interface Props {
  visible:  boolean;
  value:    string;
  label?:   string;
  maxLen?:  number;
  onChange: (v: string) => void;
  onDone:   () => void;
}

const ROWS = [
  ['1', '2', '3'],
  ['4', '5', '6'],
  ['7', '8', '9'],
  ['⌫', '0', '✓'],
];

export function NumericKeypad({ visible, value, label, maxLen = 3, onChange, onDone }: Props) {
  const handleKey = (key: string) => {
    if (key === '⌫') {
      onChange(value.slice(0, -1));
    } else if (key === '✓') {
      onDone();
    } else {
      if (value.length >= maxLen) return;
      // Prevent leading zeros (unless the whole value is '0')
      if (value === '0') {
        onChange(key);
      } else {
        onChange(value + key);
      }
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onDone}>
      {/* Dim overlay — tap to dismiss */}
      <TouchableOpacity style={styles.overlay} activeOpacity={1} onPress={onDone} />

      <View style={styles.sheet}>
        {/* Display */}
        <View style={styles.display}>
          {label ? <Text style={styles.displayLabel}>{label}</Text> : null}
          <Text style={[styles.displayValue, !value && styles.displayPlaceholder]}>
            {value || '0'}
          </Text>
        </View>

        {/* Divider */}
        <View style={styles.divider} />

        {/* Keys */}
        {ROWS.map((row, ri) => (
          <View key={ri} style={styles.row}>
            {row.map(key => {
              const isDone = key === '✓';
              const isDel  = key === '⌫';
              return (
                <TouchableOpacity
                  key={key}
                  style={[
                    styles.key,
                    isDone && styles.keyDone,
                    isDel  && styles.keyDel,
                  ]}
                  onPress={() => handleKey(key)}
                  activeOpacity={0.6}
                >
                  <Text style={[
                    styles.keyText,
                    isDone && styles.keyDoneText,
                    isDel  && styles.keyDelText,
                  ]}>
                    {key}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        ))}
      </View>
    </Modal>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const C = {
  bg:     '#0B1623',
  sheet:  '#0d1f35',
  border: '#1a3451',
  text:   '#e6edf3',
  muted:  '#6b8aad',
  orange: '#FF6B00',
  key:    '#111d2e',
  red:    '#c9082a',
};

const KEY_H = 62;

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
  },

  sheet: {
    backgroundColor: C.sheet,
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    borderTopWidth: 1.5, borderLeftWidth: 1, borderRightWidth: 1,
    borderColor: C.border,
    paddingBottom: 32,
    paddingHorizontal: 16,
    paddingTop: 20,
  },

  // ── Display ──────────────────────────────────────────────────────────
  display: {
    alignItems: 'center',
    paddingVertical: 12,
    marginBottom: 4,
  },
  displayLabel: {
    fontSize: 11, fontWeight: '800', color: C.muted,
    letterSpacing: 2.5, marginBottom: 8, textTransform: 'uppercase',
  },
  displayValue: {
    fontSize: 52, fontWeight: '900', color: C.orange,
    letterSpacing: 1,
  },
  displayPlaceholder: {
    color: C.border,
  },

  divider: {
    height: 1, backgroundColor: C.border,
    marginHorizontal: -16, marginBottom: 16,
  },

  // ── Keys ─────────────────────────────────────────────────────────────
  row: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 10,
  },
  key: {
    flex: 1, height: KEY_H,
    backgroundColor: C.key,
    borderRadius: 14,
    borderWidth: 1, borderColor: C.border,
    alignItems: 'center', justifyContent: 'center',
  },
  keyText: {
    fontSize: 22, fontWeight: '700', color: C.text,
  },

  keyDone: {
    backgroundColor: C.orange,
    borderColor: C.orange,
  },
  keyDoneText: {
    color: '#fff',
    fontSize: 20,
  },

  keyDel: {
    backgroundColor: 'rgba(201,8,42,0.12)',
    borderColor: 'rgba(201,8,42,0.3)',
  },
  keyDelText: {
    color: '#ff4d6d',
    fontSize: 20,
  },
});
