> ### ⚠️ WARNING & DISCLAIMER
> **This app was written with Claude and tested by a human. It can use your Roblox login cookie. Use it at your own risk.**
> - Run it only on your own computer. **Never host it on a cloud server or expose it to a network.**
> - **I am not responsible for any bans, lost items, lost Robux or anything else caused by this software.**
> - Sober Hub is an unofficial community project. It is not affiliated with or endorsed by Roblox or the Sober / VinegarHQ team.
![Sober Hub Image](https://raw.githubusercontent.com/Phsyt/Sober-hub-/refs/heads/main/image.png
)

# 🧊 Sober Hub

**Sober Hub** is a lightweight launcher and companion desktop app for [**Sober**](https://sober.vinegarhq.org/) (Roblox on Linux). It is a single Python file with no required dependencies. It gives you a Roblox-style home for launching games, discovering new ones, managing your avatar, tweaking Sober, and keeping track of your playtime.

## ✨ Features

* 🎮 **Launcher & Quick Join:** Launch by place ID, `roblox.com/games` link, `roblox://` link or private-server link. Browse public servers and join a friend's server directly.
* 💎 **Better Recommendations (default):** Trending games, hidden gems and picks matched to your play history, built from the community-made Rolimons game list plus Roblox's own APIs. You can switch back to normal Roblox recommendations, or plug in your own API (see [below](#-recommendations)).
* 🧍 **Avatar Editor:** Wardrobe by item type, worn items, saved outfits (wear, rename, delete, save current), skin tones, R6/R15 switching, body scales, and **bundles** (wear any bundle you own).
* 🛒 **Catalog:** Search items and bundles, buy them with a confirmation prompt, and wear bundles straight from the results.
* ⚙️ **Tweaks & Mods:** Bloxstrap-style fast-flag presets (FPS cap, lighting technology, texture quality, graphics level, no shadows, no post-processing, no grass), fast-flag import/export, and an asset overlay for custom death sounds, cursors or any file under `content/`.
* 🛠 **Sober Settings:** Edit Sober's own options and fast flags, update Sober, force-quit it, and open its config folder.
* 📊 **Playtime Analytics:** Total, daily and per-game playtime, a 14-day chart, longest sessions, and CSV export. An optional break reminder fires while a game launched from the hub is running.
* 👥 **Friends & Social:** Online status and in-game location for every friend, pinned friends, join-in-their-server, friend requests (accept or send in bulk), and desktop notifications.
* 💬 **Chat & Messages:** Roblox chat, inbox/sent/archive messages, and account privacy toggles.
* 🔒 **Private Servers:** Save and label private-server links and manage the ones you own.
* 📚 **Library:** Favorites, collections, history, per-game notes, hidden games, and a "Surprise me" button.
* 🎨 **Style & CSS:** Color presets (Roblox Dark/Light, Amoled, Rose, Sunset, Ocean, Forest, Purple, Mono), accent/radius/font/tile-size controls, compact mode, and live custom CSS.
* ⌨️ **Quick-jump palette:** Press `Ctrl+K` to jump to any tab, or paste a game link or ID.

## 📋 Prerequisites

* **Linux** with **Python 3.8+**
* [**Flatpak**](https://flatpak.org/) with Sober installed:
  ```bash
  flatpak install flathub org.vinegarhq.Sober
  ```
* *(Optional, recommended)* **pywebview** for a native window instead of a browser app window. See [`requirements.txt`](requirements.txt) for the GUI backend it needs on Linux:
  ```bash
  pip install -r requirements.txt
  ```
  If pywebview is installed but has no GUI backend, Sober Hub falls back to the browser window automatically.

## 🚀 Quick Start

1. Get the code:
   ```bash
   git clone https://github.com/your-username/sober-hub.git
   cd sober-hub
   ```

2. Run it:
   ```bash
   python3 sober_hub.py
   ```
   * With pywebview installed it opens as a native window.
   * Otherwise it opens an app window in Brave, Chromium, Chrome or Edge (whichever it finds first), or your default browser.
   * Add `--browser` to force the browser window.

3. *(Optional)* Add a desktop shortcut:
   ```bash
   python3 sober_hub.py --install
   ```
   This writes a launcher and icon to `~/.local/share/`, so Sober Hub appears in your app menu. If you move the script, run this again.

4. *(Optional)* Open **🔗 Hub Login** and paste your `.ROBLOSECURITY` cookie to unlock friends, chat, avatar, catalog and messages. Without it you can still use the launcher, discovery, tweaks, playtime stats and public profile lookups.

## 💎 Recommendations

Choose the source in **🧰 Hub Settings → Recommendations**:

| Option | What it does |
|---|---|
| **Better recommendations** (default) | Rolimons list plus Roblox data: *Picked for you*, *Trending now*, *Hidden gems* and *More <your top genre>*. |
| **My own API** | Your endpoint, GET or POST (see below). |
| **Normal Roblox recommendations** | Roblox's own Discover sorts. |

"Picked for you" ranks games by how well their genre matches your favorites, playtime and launch history, plus like ratio and a small popularity term. The ranking runs on your machine, and nothing about you is sent to Rolimons. If a source fails, Sober Hub falls back to the next one: your API, then Better, then Roblox.

### Using your own API

1. Open **🧰 Hub Settings** and set Recommendations to **My own API**.
2. Choose **GET** or **POST** and enter the URL, for example `https://your-api.com/recs?user={userId}`.
3. Optionally add **headers**, one per line (for example `Authorization: Bearer abc123`), a **POST body**, and a **path to the list** such as `data.games`.
4. Click **Save & test**. It reports how many games and sections came back, or the exact error.

**Placeholders** for the URL and body: `{userId}`, `{username}`, `{history}` (your last 20 launched place IDs) and `{favs}` (your favorites). Nothing about you is sent unless you use one.

**Expected response** (JSON). Any of these shapes works:

```json
[ { "placeId": 1818, "name": "Classic: Crossroads" } ]
```
```json
{ "games": [ { "placeId": 1818 } ] }
```
```json
{ "sections": [ { "name": "My picks", "games": [ { "placeId": 1818 } ] } ] }
```

Each game needs a place ID in `placeId`, `place`, `rootPlaceId` or `id`. `name`, `icon`, `playing` and `like` are optional, and missing names and icons are looked up from Roblox. If the list is nested elsewhere, set the **path to list**. Redirects are not followed, so use the final URL. Responses are cached for 60 seconds.

## 🎨 Custom CSS

**🎨 Style & CSS** has presets, a template and a live editor. Your CSS loads after the theme, so rules such as `:root{--bg:#000}` override the theme. Wallpapers work with local files:

```css
body { background-image: url(file:///home/you/wall.jpg); background-size: cover; }
```

Sober Hub serves images (png, jpg, gif, webp, svg, bmp) from your home folder. Images inside hidden folders such as `~/.config` are not served.

## 🧩 Tweaks & Mods notes

* Changes are written to Sober's `config.json` (fast flags) or its asset overlay folder (mods). **Fully close and reopen Sober to apply them.**
* The hub keeps Sober's comment header in `config.json` and saves a one-time backup next to it as `config.json.hub.bak`.
* Roblox changes its fast flags often, and Sober may ignore flags it does not allow. A tweak that does nothing is usually the flag's fault.

## 🔒 Security & Privacy

* **Local only:** The server listens on `127.0.0.1`, rejects requests that are not addressed to localhost, and has no authentication of its own. Never expose its port to a network or run it on a server.
* **Local storage:** Data lives in `~/.config/sober-hub/data.json` with `0600` permissions. Sober's own config is edited in place.
* **Your cookie:**
  * It is stored locally, in plain text in `data.json`, and sent **only** to `*.roblox.com`.
  * It is never sent to Rolimons, to your custom recommendations API, or to the UI, and it is excluded from backups and imports.
  * Redirects are never followed, so a redirect cannot carry the cookie to another host.
  * Use **Hub Login → Disconnect** to remove it, or revoke it by logging out on roblox.com.
* **Custom API headers** (which may contain a key) are stored in `data.json` and left out of exported backups.
* **Purchases:** Buying always asks for confirmation first. It spends real Robux, so double-check the price.

## ⚠️ Known limitations

* Sober Hub relies on Roblox web APIs that can change or be rate-limited without notice. If a feature breaks, the toast usually shows Roblox's own error message, so please include it when you open an issue.
* Bundle wearing, outfit wearing and body scales depend on Roblox accepting the request. Items that are off-sale or not owned may be skipped.
* Wardrobe → Bundles uses a Roblox endpoint that may not be available for every account.

## 🤝 Contributing

Everything lives in `sober_hub.py`: a Python backend at the top and the HTML/CSS/JS interface in the `HTML` string below it. Backend routes are the entries of the `R` dict, and pages are functions in `PAGES`. The file header documents the layout. Bug reports and pull requests are welcome.

## 📜 License

Distributed under the **Apache License 2.0**. See the [`LICENSE`](LICENSE) file or <http://www.apache.org/licenses/LICENSE-2.0>.
