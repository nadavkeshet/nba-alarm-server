import React, { useEffect } from 'react';
import { useWindowDimensions, View } from 'react-native';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from 'react-native-reanimated';
import { useTheme } from '../context/ThemeContext';

const BAR_W_RATIO = 0.38; // width of the moving segment relative to screen width
const DURATION    = 1300; // ms for one pass

export function RefreshBar({ visible }: { visible: boolean }) {
  const { colors } = useTheme();
  const { width }  = useWindowDimensions();
  const barW       = width * BAR_W_RATIO;
  const x          = useSharedValue(-barW);

  useEffect(() => {
    if (visible) {
      x.value = -barW;
      x.value = withRepeat(
        withTiming(width + barW, { duration: DURATION, easing: Easing.inOut(Easing.ease) }),
        -1,
        false,
      );
    } else {
      cancelAnimation(x);
    }
  }, [visible, width, barW]);

  const animStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: x.value }],
  }));

  if (!visible) return null;

  return (
    <View style={{ height: 2, overflow: 'hidden', backgroundColor: `${colors.orange}28` }}>
      <Animated.View
        style={[
          { height: 2, width: barW, borderRadius: 1, backgroundColor: colors.orange },
          animStyle,
        ]}
      />
    </View>
  );
}
