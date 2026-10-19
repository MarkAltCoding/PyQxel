// The entry point compiled beside every custom C++ strategy, so its author never writes
// one. The module is a WASI reactor: the runner calls pyqxel_allocate with the number of
// bars, fills the arrays it points to through pyqxel_column and pyqxel_timestamps,
// calls pyqxel_run, and reads the positions back from column 5.

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <span>
#include <vector>

#include "pyqxel.h"

namespace {

constexpr int kColumns = 6;  // open, high, low, close, volume, positions
std::vector<double> columns[kColumns];
std::vector<std::int64_t> timestamps;

}  // namespace

extern "C" {

__attribute__((export_name("pyqxel_allocate"))) void pyqxel_allocate(std::uint32_t size) {
    for (int column = 0; column < kColumns - 1; ++column) columns[column].assign(size, NAN);
    columns[kColumns - 1].assign(size, 0.0);
    timestamps.assign(size, 0);
}

__attribute__((export_name("pyqxel_column"))) double* pyqxel_column(int column) {
    return columns[column].data();
}

__attribute__((export_name("pyqxel_timestamps"))) std::int64_t* pyqxel_timestamps() {
    return timestamps.data();
}

__attribute__((export_name("pyqxel_run"))) void pyqxel_run() {
    const pyqxel::Bars bars{
        timestamps.size(), columns[0].data(), columns[1].data(), columns[2].data(),
        columns[3].data(), columns[4].data(), timestamps.data(),
    };
    strategy(bars, std::span<double>(columns[kColumns - 1]));
    // A reactor never exits, so what the strategy printed is flushed here.
    std::fflush(nullptr);
}

}  // extern "C"
