interface ImportMetaEnv {
  /** The PyQxel server the app calls, such as `https://pyqxel.example`. Left unset, the
   * web app calls the server that served it; a native app build sets it, since its pages
   * are bundled and the server is elsewhere. */
  readonly VITE_API_ORIGIN?: string
  /** Where the app's copy of Pyodide is served from; defaults to `pyodide/` under the base
   * URL. A native app build can point it at the files it bundles. */
  readonly VITE_PYODIDE_URL?: string
  /** Where the app's copy of the Clang toolchain is served from; defaults to `clang/` under
   * the base URL. */
  readonly VITE_CLANG_URL?: string
  /** `off` in a build for the Mac App Store, which updates apps itself, so the app does
   * not look for releases on GitHub (`lib/appUpdates.ts`). */
  readonly VITE_APP_UPDATES?: string
}
