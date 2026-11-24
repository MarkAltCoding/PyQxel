# Installing the App

PyQxel runs in a web browser at the hosted site, and as an app for Mac and iPhone. The app is the same PyQxel: it signs in to the same account and shows the same portfolios and saved work. It is free.

## Mac: download it

1. Open the [latest release](https://github.com/MarkAltCoding/PyQxel/releases/latest) and, under **Assets**, download the file ending in `.dmg` (for example `PyQxel_0.1.0_universal.dmg`). It runs on both Apple silicon and Intel Macs.
2. Open the downloaded file. In the window that appears, drag **PyQxel** onto the **Applications** folder, then eject the disk image.
3. Open PyQxel from Applications. The first time, macOS says it could not verify that PyQxel is free of malware. That is because the app is not signed with a paid Apple developer certificate, not because something was found. Choose **Done**, then:
   * open **System Settings**, then **Privacy & Security**;
   * scroll down to the message that PyQxel was blocked and choose **Open Anyway**;
   * enter your Mac's password, and choose **Open Anyway** once more.

   macOS remembers this, so later launches open normally. (On macOS 14 or earlier you can instead Control-click the app, choose **Open**, then **Open**.)
4. Acknowledge that PyQxel is not investment advice, then sign in with your email and password, or with GitHub or Google. An email and password sign in within the app. GitHub and Google open your browser to sign in, and when you finish it offers to open PyQxel again: allow it.
5. In **Settings**, add your market data keys. Free plans from Tiingo, Finnhub, and Alpha Vantage are enough to start; the page says what each one adds.

**Updates install themselves.** When a new version is out, PyQxel says so in its corner, on opening or within a few hours, with **Update and restart**: it downloads the new version, checks that it is signed by PyQxel, puts it in place of the old one, and reopens, keeping your sign-in and keys. **Later** hides that version until a newer one comes.

To uninstall, drag PyQxel from Applications to the Trash. Its saved sign-in and keys on your Mac are in `~/Library/Application Support/com.markaltcoding.pyqxel`, and its pages' storage in `~/Library/WebKit/com.markaltcoding.pyqxel`, which you can delete too. Your account and saved work stay on the server until you use **Delete account** in Settings.

## iPhone

The iPhone app is not on the App Store yet, so you build it with Xcode and install it on your own phone. A free Apple ID works, but apps installed with one stop opening after 7 days until you install them again. The [license](../LICENSE) allows this for your own use, with your PyQxel account, from the code as it is; changing the code, or sharing the app you build, needs permission.

1. Install **Xcode** from the Mac App Store, open it once so it finishes installing, and in **Xcode → Settings → Components** add the iOS platform. In **Xcode → Settings → Accounts**, add your Apple ID.
2. Get the code and prepare it. The setup script installs what is missing (Homebrew's Node.js and XcodeGen, Rust, and the app's packages), and prints your Apple ID's team ID at the end:
   ```bash
   git clone https://github.com/MarkAltCoding/PyQxel.git
   cd PyQxel
   scripts/setup-xcode.sh
   ```
   If it stops, it says what to do first (such as installing [Homebrew](https://brew.sh)); then run it again.
3. Open a new Terminal window, so it finds Rust, and go to the app's folder: `cd PyQxel/web`. Then, for your iPhone:
   * **Your iPhone**: connect it with a cable, unlock it, and choose **Trust** if it asks. Turn on **Settings → Privacy & Security → Developer Mode** on the phone (it restarts). Then, using the team ID the setup script printed and your phone's name from **Settings → General → About**:
     ```bash
     APPLE_DEVELOPMENT_TEAM=YOURTEAMID npx tauri ios run --release "Your iPhone"
     ```
     The first time, the phone refuses to open the app until you trust yourself as its developer: **Settings → General → VPN & Device Management**, choose your Apple ID, then **Trust**.

     If the command ends with an error about `exportOptionsPlist` after building, the app is built but was not installed, which happens with free Apple IDs. Install it with:
     ```bash
     xcrun devicectl list devices      # shows your phone's identifier
     xcrun devicectl device install app --device <identifier> \
       ~/Library/Developer/Xcode/DerivedData/pyqxel-*/Build/Products/release-iphoneos/PyQxel.app
     ```

The app you build calls the hosted PyQxel server, named in `web/.env.native`.

**To update the app you built**, get the newest code and build it again. The app's pages are built into it, so new features appear only after a rebuild; changes on the server reach it at once.

```bash
cd PyQxel && git pull && cd web && npm install
```

With the phone connected and unlocked, one command builds and installs the new version, and opens it:
```bash
APPLE_DEVELOPMENT_TEAM=YOURTEAMID scripts/install-iphone.sh
```
It finds the phone itself (set `IPHONE` to its identifier from `xcrun devicectl list devices` when more than one is connected), and it also restarts the 7 days a free Apple ID's install lasts. An iPhone app cannot update itself: outside the App Store, or Apple's TestFlight for testers, installing from a Mac is the only way. Once PyQxel is on the App Store, iPhones update it like any other app.

## Publishing a release (maintainers)

From a clean `main`, with everything you want released pushed:

```bash
scripts/release.sh 0.2.0
```

It sets the version everywhere it is written (`web/src-tauri/tauri.conf.json`, the Rust package, and `app/version.py`), commits, tags `v0.2.0`, and pushes. The tag starts the [Release workflow](../.github/workflows/release.yml), which builds the universal `.dmg` on a Mac runner and attaches it to a GitHub Release, along with the signed update (`PyQxel_universal.app.tar.gz`) and `latest.json`, which name the version and list the commits since the last release. Installed Mac apps read `latest.json` from the latest release and offer the update. Developing between releases needs none of this: commits to `main` reach the website at the next deploy and the apps only when you release.

Updates are signed with a key of PyQxel's own (not Apple's): apps accept only updates signed by it, checked against the public key in `tauri.conf.json`. The private key and its password live in the repository's Actions secrets `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, and in `~/.tauri/` on the machine that made them; keep a copy in a password manager, since an app can only be updated by the key it was built with. Without the secrets, the release has the `.dmg` alone and apps are not offered it. To make a new key: `npx tauri signer generate -w ~/.tauri/pyqxel-updater.key` (in `web/`), then put its `.pub` contents in `plugins.updater.pubkey` (apps built before then need the next version installed by hand).

To try a build without publishing, run the workflow by hand from the Actions tab; its `.dmg` is kept with the run.
