// A strategy across tickers: name any tickers in strings and their prices are fetched
// for you, so there is no symbol to pick first. Those listed in SYMBOLS come first; the
// page shows every ticker found, and you can drop or add one there.
//
// Set weights(i, j), the share of capital to hold in ticker j after bar i's close: 0.5
// is half of capital, -0.5 half of capital short. Every weight starts at 0.0, so a ticker
// you only read as a signal is never held; whatever the weights leave over is cash, and
// the absolute weights may add up to at most 2.0 on a bar. The backtest holds each bar's
// weights over the next bar, so a signal never trades on the bar that produced it.
//
// universe.find("SPY") is SPY's index j, and universe[j] its pyqxel::Bars (open, high,
// low, close and volume, adjusted for splits and dividends). The bars are those of the
// dates every ticker traded, oldest first; universe.size is the number of bars and
// universe.timestamp[i] bar i's date in milliseconds since 1970, UTC. The code is C++20
// without exceptions, and whatever it prints appears below.

#include <pyqxel.h>

#include <ctime>
#include <vector>

const char* const SYMBOLS[] = {"SPY", "QQQ", "TLT", "GLD"};
const char* const FEAR = "^VIX";  // read only as a signal

// Each month, hold the ticker with the best six-month return, if it rose, at half size
// when the VIX is above 30; otherwise hold cash until the next month.
void strategy(const pyqxel::Universe& universe, pyqxel::Weights weights) {
    constexpr std::size_t lookback = 126;  // six months of daily bars
    const pyqxel::Bars& fear = universe[universe.find(FEAR)];
    std::vector<double> held(universe.count, 0.0);
    for (std::size_t i = 0; i < universe.size; ++i) {
        bool month_end = i + 1 == universe.size;
        if (!month_end) {
            std::time_t now = universe.timestamp[i] / 1000, next = universe.timestamp[i + 1] / 1000;
            month_end = std::gmtime(&now)->tm_mon != std::gmtime(&next)->tm_mon;
        }
        if (month_end && i >= lookback) {
            std::size_t best = universe.count;
            double best_return = 0.0;
            for (const char* symbol : SYMBOLS) {
                std::size_t j = universe.find(symbol);
                double change = universe[j].close[i] / universe[j].close[i - lookback] - 1.0;
                if (change > best_return) {
                    best_return = change;
                    best = j;
                }
            }
            double size = fear.close[i] > 30.0 ? 0.5 : 1.0;
            for (std::size_t j = 0; j < universe.count; ++j) held[j] = j == best ? size : 0.0;
        }
        for (std::size_t j = 0; j < universe.count; ++j) weights(i, j) = held[j];
    }
}
