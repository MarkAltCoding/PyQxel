// Writes a Brotli-compressed copy, `name.br`, beside each large compressible file in the
// build, dist/. The server sends that copy to browsers that accept Brotli (app/web.py),
// so files are compressed once here instead of on every request: the in-browser C++
// compiler goes from 105 MB to about 17 MB.
//
// Compression runs at Brotli's highest quality, on every file at once, and takes about
// a minute and a half for the compiler. Results are cached by content in
// node_modules/.cache/precompress, so a rebuild only compresses what changed. Files that
// are already compressed, such as Python wheels, gain too little and get no copy.

import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdir, readdir, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { dirname, extname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import { brotliCompress, constants } from 'node:zlib'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const dist = join(web, 'dist')
const cache = join(web, 'node_modules', '.cache', 'precompress')

/** Types worth compressing; images, wheels and archives already are. */
const COMPRESSIBLE = new Set(['.js', '.mjs', '.css', '.html', '.json', '.svg', '.wasm', '.tar'])
/** Smaller files gain too little to be worth a second copy. */
const MIN_BYTES = 1024
/** A copy is kept only when it is at most this share of the original's size. */
const MAX_RATIO = 0.9
const QUALITY = 11

const compress = promisify(brotliCompress)

async function* walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) yield* walk(path)
    else yield path
  }
}

/** The file's Brotli copy, from the cache or else compressed, and its cache name. */
async function brotli(data) {
  const name = `${createHash('sha256').update(data).digest('hex')}-q${QUALITY}.br`
  const cached = join(cache, name)
  if (existsSync(cached)) return { name, bytes: await readFile(cached) }
  const bytes = await compress(data, {
    params: {
      [constants.BROTLI_PARAM_QUALITY]: QUALITY,
      [constants.BROTLI_PARAM_LGWIN]: 24,
      [constants.BROTLI_PARAM_SIZE_HINT]: data.length,
    },
  })
  await writeFile(cached, bytes)
  return { name, bytes }
}

if (!existsSync(dist)) throw new Error('dist/ does not exist: run the Vite build first.')
await mkdir(cache, { recursive: true })
const files = []
for await (const path of walk(dist)) {
  if (COMPRESSIBLE.has(extname(path)) && (await stat(path)).size >= MIN_BYTES) files.push(path)
}

const used = new Set()
let before = 0
let after = 0
let copies = 0
await Promise.all(
  files.map(async (path) => {
    const data = await readFile(path)
    const { name, bytes } = await brotli(data)
    used.add(name)
    before += data.length
    if (bytes.length > data.length * MAX_RATIO) {
      after += data.length
      return
    }
    await writeFile(`${path}.br`, bytes)
    after += bytes.length
    copies += 1
  }),
)
// Drop cached copies of files this build no longer has, such as old asset versions.
for (const name of await readdir(cache)) {
  if (!used.has(name)) await rm(join(cache, name))
}
console.log(
  `Brotli copies of ${copies} of ${files.length} files: ${(before / 1e6).toFixed(1)} MB sent ` +
    `as ${(after / 1e6).toFixed(1)} MB to browsers that accept Brotli.`,
)
