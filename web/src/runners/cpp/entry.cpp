// The entry point compiled beside every custom C++ strategy, so its author never writes
// one. The module is a WASI reactor. For one symbol, the runner calls pyqxel_allocate
// with the number of bars, fills the arrays it points to through pyqxel_column and
// pyqxel_timestamps, calls pyqxel_run, and reads the positions back from column 5.
// Compiled with PYQXEL_UNIVERSE, for several symbols, pyqxel_allocate also takes the
// number of symbols and the length of their names, pyqxel_column the symbol, and
// pyqxel_symbols points to the names, each ended by a NUL; the weights are read back
// from pyqxel_weights.

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <span>
#include <vector>

#include "pyqxel.h"

namespace {

constexpr int kFields = 5;  // open, high, low, close, volume
std::vector<std::int64_t> timestamps;

#ifdef PYQXEL_UNIVERSE
std::size_t symbol_count = 0;
std::vector<double> fields;  // Symbol by symbol, field by field, bar by bar.
std::vector<char> names;
std::vector<double> weights;
#else
constexpr int kColumns = kFields + 1;  // the fields, then positions
std::vector<double> columns[kColumns];
#endif

}  // namespace

extern "C" {

#ifdef PYQXEL_UNIVERSE

__attribute__((export_name("pyqxel_allocate"))) void pyqxel_allocate(
    std::uint32_t size, std::uint32_t count, std::uint32_t name_bytes) {
    symbol_count = count;
    fields.assign(std::size_t{size} * count * kFields, NAN);
    names.assign(name_bytes, '\0');
    weights.assign(std::size_t{size} * count, 0.0);
    timestamps.assign(size, 0);
}

__attribute__((export_name("pyqxel_column"))) double* pyqxel_column(int asset, int field) {
    return fields.data() + (std::size_t(asset) * kFields + field) * timestamps.size();
}

__attribute__((export_name("pyqxel_symbols"))) char* pyqxel_symbols() { return names.data(); }

__attribute__((export_name("pyqxel_weights"))) double* pyqxel_weights() {
    return weights.data();
}

__attribute__((export_name("pyqxel_timestamps"))) std::int64_t* pyqxel_timestamps() {
    return timestamps.data();
}

__attribute__((export_name("pyqxel_run"))) void pyqxel_run() {
    std::vector<pyqxel::Bars> assets;
    std::vector<const char*> symbols;
    const char* name = names.data();
    for (std::size_t asset = 0; asset < symbol_count; ++asset) {
        auto field = [asset](int column) { return pyqxel_column(int(asset), column); };
        assets.push_back({timestamps.size(), field(0), field(1), field(2), field(3), field(4),
                          timestamps.data()});
        symbols.push_back(name);
        while (*name != '\0') ++name;
        ++name;
    }
    const pyqxel::Universe universe{timestamps.size(), symbol_count, assets.data(),
                                    symbols.data(), timestamps.data()};
    strategy(universe, pyqxel::Weights{weights.data(), symbol_count});
    // A reactor never exits, so what the strategy printed is flushed here.
    std::fflush(nullptr);
}

#else

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

#endif

}  // extern "C"
