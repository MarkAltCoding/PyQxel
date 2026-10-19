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
// The code is compiled as C++20 with -O2 and without exceptions, and runs in your
// browser with the standard library but no files or network. Whatever it prints to
// stdout or stderr is shown below the code.

#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
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

// Whether a value is missing, as open, high, low and volume may be.
inline bool missing(double value) { return std::isnan(value); }

}  // namespace pyqxel

// The strategy every custom C++ strategy defines.
void strategy(const pyqxel::Bars& bars, std::span<double> positions);
