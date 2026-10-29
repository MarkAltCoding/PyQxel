// A strategy across several symbols sets weights(i, j), the share of capital to hold in
// symbol j after bar i's close: 0.5 is half of capital, -0.5 half of capital short.
// Every weight starts at 0.0, whatever the weights leave over is cash, and the absolute
// weights may add up to at most 2.0 on a bar. The backtest holds each bar's weights over
// the next bar, so a signal never trades on the bar that produced it.
//
// universe[j] is symbol j's pyqxel::Bars (open, high, low, close and volume, adjusted for
// splits and dividends; all but close NaN where missing), universe.symbols[j] its ticker,
// universe.size the number of bars and universe.count the number of symbols. The bars
// are those of the dates every symbol traded, oldest first; universe.timestamp[i] is bar
// i's date in milliseconds since 1970, UTC. The code is C++20 without exceptions, and
// whatever it prints appears below.

#include <pyqxel.h>

#include <cstdio>
#include <ctime>
#include <vector>

// Each month, put all capital in the symbol with the best six-month return, if it rose;
// otherwise hold cash until the next month.
void strategy(const pyqxel::Universe& universe, pyqxel::Weights weights) {
    constexpr std::size_t lookback = 126;  // six months of daily bars
    std::vector<double> held(universe.count, 0.0);
    for (std::size_t i = 0; i < universe.size; ++i) {
        // Reallocate at the last bar of each month, keeping the weights in between.
        bool month_end = i + 1 == universe.size;
        if (!month_end) {
            std::time_t now = universe.timestamp[i] / 1000, next = universe.timestamp[i + 1] / 1000;
            month_end = std::gmtime(&now)->tm_mon != std::gmtime(&next)->tm_mon;
        }
        if (month_end && i >= lookback) {
            std::size_t best = 0;
            double best_return = -1.0;
            for (std::size_t j = 0; j < universe.count; ++j) {
                double change = universe[j].close[i] / universe[j].close[i - lookback] - 1.0;
                if (change > best_return) {
                    best_return = change;
                    best = j;
                }
            }
            for (std::size_t j = 0; j < universe.count; ++j) {
                held[j] = j == best && best_return > 0.0 ? 1.0 : 0.0;
            }
        }
        for (std::size_t j = 0; j < universe.count; ++j) weights(i, j) = held[j];
    }
}
