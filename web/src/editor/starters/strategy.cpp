// A custom strategy sets positions[i], the position to hold after bar i's close:
//   1.0 fully long, 0.0 flat, -1.0 fully short, up to 2.0 leveraged.
// Every position starts at 0.0. The backtest holds each position over the next bar, so
// a signal never trades on the bar that produced it.
//
// bars.open, high, low, close and volume hold each bar's prices and volume, oldest
// first, adjusted for splits and dividends; all but close are NaN where missing (test
// with pyqxel::missing). bars.timestamp[i] is bar i's time in milliseconds since 1970,
// UTC. The code is C++20 without exceptions, and whatever it prints appears below.

#include <pyqxel.h>

#include <cmath>
#include <cstdio>

// Long while the 50-bar average close is above the 200-bar one, otherwise flat.
void strategy(const pyqxel::Bars& bars, std::span<double> positions) {
    constexpr std::size_t fast = 50, slow = 200;
    double fast_sum = 0.0, slow_sum = 0.0;
    for (std::size_t i = 0; i < bars.size; ++i) {
        fast_sum += bars.close[i];
        slow_sum += bars.close[i];
        if (i >= fast) fast_sum -= bars.close[i - fast];
        if (i >= slow) slow_sum -= bars.close[i - slow];
        // Flat until the slow average has a full window.
        if (i + 1 >= slow) positions[i] = fast_sum / fast > slow_sum / slow ? 1.0 : 0.0;
    }
}

// A stateful strategy, where each bar depends on what the strategy did before it, which
// pandas handles poorly: buy a close above the previous 20 bars' highest, then hold with
// a stop 3 average true ranges below the highest close since entry, and wait 5 bars
// after being stopped out before buying again. To try it, replace the strategy above
// with it.
/*
void strategy(const pyqxel::Bars& bars, std::span<double> positions) {
    constexpr std::size_t breakout = 20, atr_window = 14, cooldown = 5;
    constexpr double stop_atrs = 3.0;
    double atr = 0.0, highest = 0.0;
    bool held = false;
    std::size_t waiting = 0;
    for (std::size_t i = 1; i < bars.size; ++i) {
        // Wilder's average true range; a missing high or low counts as the close.
        double high = pyqxel::missing(bars.high[i]) ? bars.close[i] : bars.high[i];
        double low = pyqxel::missing(bars.low[i]) ? bars.close[i] : bars.low[i];
        double previous = bars.close[i - 1];
        double range = std::fmax(high, previous) - std::fmin(low, previous);
        atr = i <= atr_window ? atr + range / atr_window : atr + (range - atr) / atr_window;
        if (i < breakout || i <= atr_window) continue;

        if (held) {
            highest = std::fmax(highest, bars.close[i]);
            if (bars.close[i] < highest - stop_atrs * atr) {
                held = false;
                waiting = cooldown;
            }
        } else if (waiting > 0) {
            --waiting;
        } else {
            double top = bars.close[i - breakout];
            for (std::size_t j = i - breakout; j < i; ++j) top = std::fmax(top, bars.close[j]);
            if (bars.close[i] > top) {
                held = true;
                highest = bars.close[i];
            }
        }
        positions[i] = held ? 1.0 : 0.0;
    }
}
*/
