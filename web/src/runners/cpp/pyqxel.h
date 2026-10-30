// pyqxel.h: what a custom C++ strategy is given, and what it must define.
//
// Write
//
//     void strategy(const pyqxel::Bars& bars, std::span<double> positions);
//
// and set positions[i] to the position to hold after bar i's close: 1.0 fully long,
// 0.0 flat, -1.0 fully short, anything between, up to 2.0 leveraged. Every position
// starts at 0.0. The backtest holds each position over the next bar, so a signal never
// trades on the bar that produced it.
//
// With several symbols, define instead
//
//     void strategy(const pyqxel::Universe& universe, pyqxel::Weights weights);
//
// and set weights(i, j) to the share of capital in symbol j after bar i's close: 0.5 is
// half of capital, -0.5 half of capital short. Every weight starts at 0.0, whatever the
// weights leave over is cash, and the absolute weights may add up to at most 2.0 on a
// bar. The bars are those of the dates every symbol traded.
//
// The code is compiled as C++20 with -O2 and without exceptions, and runs in your
// browser with the standard library but no files or network. Whatever it prints to
// stdout or stderr is shown below the code.

#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>

namespace pyqxel {

// One instrument's bars, oldest first, adjusted for splits and dividends.
struct Bars {
    std::size_t size;           // Number of bars; every array below has this many entries.
    const double* open;         // NaN where the price is missing.
    const double* high;
    const double* low;
    const double* close;        // Always a number.
    const double* volume;       // NaN where the volume is missing.
    const std::int64_t* timestamp;  // Each bar's time, in milliseconds since 1970-01-01 UTC.
};

// Several instruments' bars on the dates they all traded, oldest first.
struct Universe {
    std::size_t size;                // Number of bars of each symbol.
    std::size_t count;               // Number of symbols.
    const Bars* assets;              // assets[j] is symbol j's bars.
    const char* const* symbols;      // symbols[j] is symbol j's ticker, e.g. "GOOG".
    const std::int64_t* timestamp;   // Each bar's date, as milliseconds since 1970-01-01 UTC.

    const Bars& operator[](std::size_t asset) const { return assets[asset]; }

    // The index of the symbol with this ticker, e.g. find("AAPL"), or count when the
    // strategy was not given it: universe[universe.find("AAPL")].close[i].
    std::size_t find(const char* ticker) const {
        for (std::size_t j = 0; j < count; ++j) {
            if (std::strcmp(symbols[j], ticker) == 0) return j;
        }
        return count;
    }
};

// The weight of every symbol on every bar, written as weights(bar, asset).
struct Weights {
    double* values;     // Row by row: bar 0's weights, then bar 1's, and so on.
    std::size_t count;  // Number of symbols.

    double& operator()(std::size_t bar, std::size_t asset) const {
        return values[bar * count + asset];
    }
};

// Whether a value is missing, as open, high, low and volume may be.
inline bool missing(double value) { return std::isnan(value); }

}  // namespace pyqxel

// The strategy every custom C++ strategy defines: across several symbols when the
// runner compiles it with PYQXEL_UNIVERSE, on one otherwise.
#ifdef PYQXEL_UNIVERSE
void strategy(const pyqxel::Universe& universe, pyqxel::Weights weights);
#else
void strategy(const pyqxel::Bars& bars, std::span<double> positions);
#endif
