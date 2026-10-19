// Puts the YoWASP Clang/LLD toolchain, which compiles custom C++ strategies to
// WebAssembly in the browser, in public/clang/, so the app serves it itself and nothing
// loads from a CDN at runtime: an App Store build must bundle every file it runs, and
// strategies keep working offline.
//
// Everything comes from the pinned `@yowasp/clang` npm package, whose integrity npm
// checked on install: the loader (bundle.js), the compiler (llvm.core*.wasm) and the
// headers and libraries it compiles against, libc++ included (llvm-resources.tar). The
// loader fetches the rest relative to its own URL, so they are served side by side. At
// about 105 MB, the copy is skipped when public/clang/ already holds this version.

import { existsSync } from 'node:fs'
import { copyFile, mkdir, readdir, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const source = join(web, 'node_modules', '@yowasp', 'clang', 'gen')
const target = join(web, 'public', 'clang')
const marker = join(target, '.vendored')

const { version } = JSON.parse(
  await readFile(join(web, 'node_modules', '@yowasp', 'clang', 'package.json'), 'utf8'),
)
/** The files the package publishes from gen/: what the loader imports and fetches. */
const files = (await readdir(source))
  .filter((name) => name === 'bundle.js' || name.endsWith('.wasm') || name.endsWith('.tar'))
  .sort()
for (const required of ['bundle.js', 'llvm.core.wasm', 'llvm-resources.tar']) {
  if (!files.includes(required)) {
    throw new Error(`@yowasp/clang ${version} has no gen/${required}; its layout changed.`)
  }
}

/** Whether public/clang/ already holds this version's files, at their sizes. */
async function current() {
  if (!existsSync(marker) || (await readFile(marker, 'utf8')) !== version) return false
  for (const file of files) {
    const copied = join(target, file)
    if (!existsSync(copied)) return false
    if ((await stat(copied)).size !== (await stat(join(source, file))).size) return false
  }
  return true
}

if (await current()) {
  console.log(`YoWASP Clang ${version} is already in public/clang/.`)
} else {
  await rm(target, { recursive: true, force: true })
  await mkdir(target, { recursive: true })
  let bytes = 0
  for (const file of files) {
    await copyFile(join(source, file), join(target, file))
    bytes += (await stat(join(target, file))).size
  }
  await writeFile(marker, version)
  console.log(
    `YoWASP Clang ${version} (${(bytes / 1e6).toFixed(1)} MB in ${files.length} files) is in ` +
      'public/clang/.',
  )
}
