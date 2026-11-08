> ### ⚠️ CRITICAL WARNING & LIABILITY DISCLAIMER
> **THIS APP IS A WRAPPER MADE WITH CLAUDE TESTED BY A HUMAN AND IT USES ROBLOX COOKIES. USE AT YOUR OWN RISK**
> NEVER HOST IT ON A CLOUD SERVER . 
> **I AM NOT RESPONSIBLE FOR ANY BANS OR ANYTHING CAUSED BY THIS SOFTWARE.**
> Use this app entirely at your own risk!.



# 🧊 Sober Hub

**Sober Hub** is a feature-packed, lightweight launcher and companion desktop app for [**Sober**](https://sober.vinegarhq.org/) (Roblox on Linux). It offers an all-in-one UI for launching games, organizing collections, tweaking Roblox fast flags, managing asset mods, tracking playtime, and discovering hidden gem games via flexible APIs.


## ✨ Features

* 🎮 **Game Launcher & Quick Join:** Instant launching via Roblox place IDs, private server links, share links (`roblox://`), or server direct joins.
* 💎 **Underrated Game Discovery:** Recommendation system leveraging Rolimon's community stats and Roblox APIs to surface high-rated "hidden gems" alongside trending games.
* ⚙️ **Fast Flags & Performance Tweaks:** Fine-tune client settings (FPS unlocker, graphics quality overrides, texture controls, lighting models, and custom flag imports).
* 🧩 **Bloxstrap-style Asset Overlay (Mods):** Swap cursors, death sounds (`ouch.ogg`), and custom UI textures directly in Sober.
* 📊 **Playtime Analytics & Streaks:** Track total, daily, and weekly playtimes, view detailed streak graphs, and export CSV reports.
* 👥 **Friends & Social Integration:** View online status, in-game locations, manage friend requests, and launch directly into a friend's active server.
* 💬 **Roblox Chat & Messages:** Built-in chat client, direct messaging, and message privacy toggles.
* 🔒 **Private Server Manager:** Save, label, create, and manage private server permissions right from the app interface.
* 🎨 **Deep Customization:** Preset color themes (Amoled, Rose, Ocean, Sunset, etc.), compact UI options, and support for live custom CSS overlays.

## 📋 Prerequisites

* **Linux OS** with Python 3.8+
* [**Flatpak**](https://flatpak.org/) installed with the Sober app package:
  ```bash
  flatpak install flathub org.vinegarhq.Sober
  ```
* *(Optional but recommended)* `pywebview` for a native application window:
  ```bash
  pip install pywebview
  ```

## 🚀 Quick Start

1. Clone or download the repository:
   ```bash
   git clone https://github.com/your-username/sober-hub.git
   cd sober-hub
   ```

2. Run the script:
   ```bash
   python3 sober_hub_v5.py
   ```
   * *If `pywebview` is installed, it will open as a native app window.*
   * *If `pywebview` is not present, it will launch in app mode via your default Chromium-based browser.*

3. *(Optional)* Install the Desktop Application Shortcut:
   ```bash
   python3 sober_hub_v5.py --install
   ```
   This creates a `.desktop` launcher entry and icon in `~/.local/share/applications/` for your application menu.

## ⚙️ Custom Recommendations API Setup

By default, Sober Hub parses data from **Rolimon's API** to locate high-rated games with smaller player bases. You can switch to official Roblox sorts or supply your own API in **⚙️ Hub Settings**:

1. Open **⚙️ Hub Settings**.
2. Under **Recommendations**, pick **My own API**.
3. Fill in your endpoint details:
   * **Method:** `GET` or `POST`
   * **URL:** `https://your-api.com/recs?user={userId}`
   * **Path to list:** `data.games` (or appropriate response JSON path)
4. Click **Save & test**.

## 🔒 Security & Privacy Notice

* **Local Storage:** Configuration and history files are strictly stored locally in `~/.config/sober-hub/data.json` with secure file permissions (`0600`).
* **Session Cookie Handling:** If you choose to supply your `.ROBLOSECURITY` cookie to enable friends list features and chat, **it is only attached to requests sent directly to `*.roblox.com`**.
* **Zero External Cookie Sharing:** Your authentication tokens are **never** forwarded to third-party endpoints or custom recommendation APIs.

## 📜 License

Distributed under the **Apache License 2.0**. See the `LICENSE` file or [http://www.apache.org/licenses/LICENSE-2.0](http://www.apache.org/licenses/LICENSE-2.0) for more details.
