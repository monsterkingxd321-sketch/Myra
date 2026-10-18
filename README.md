<h2 align="center">
    ──「 𓆩꯭❛ 𝙈𝙔𝙍𝘼 ❜꯭𓆪⁩ 」──
</h2>
<p align="center">
  <img src="https://i.ibb.co/wNRdVdnL/file-3604.jpg" alt="Myra Bot Logo">
</p>
<h2 align="center"> ──「 ᴅᴇᴍᴏ ʀᴏʙᴏᴛ 」──</h2>
<p align="center">
  <a href="https://t.me/MyraaRobot">
    <img src="https://img.shields.io/badge/Check-Demo%20Robot-black?style=for-the-badge&logo=Telegram" width="220" height="39"/>
  </a>
</p>

An elegant, modern, high-performance Telegram Chatbot written in Python using **Pyrogram v2** and **MongoDB (Motor)**. Fully optimized for production deployments (e.g., Render, Heroku, VPS, or Docker).

---

## 🚀 Features

- **Asynchronous Architecture:** Rewritten to use purely non-blocking `Motor` asynchronous calls for MongoDB. Eliminates latency spikes and blockages in Pyrogram's event loop.
- **Hybrid AI Brain (NEW):** Database-first replies with a Google Gemini **and** OpenAI / ChatGPT fallback, automatic auto-training, multi-key rotation for both providers and offline mode. See **[AI_SETUP.md](AI_SETUP.md)**.
- **Auto-Learning Chatbot:** Dynamically learns conversation replies from chat groups and uses them to intelligently respond.
- **Admin Utilities:** Promotes, demotes, kicks, mutes, unmutes, bans, unbans, and pins messages using inline buttons.
- **Built-in Games:** Play dice, dart, basketball, bowling, football, and slot machines.
- **Media Download Command:** Download videos and images from Pinterest, Instagram, or YouTube via external API.
- **Broadcasting & Announcements:** Direct broadcasting and message forwarding to all served groups and users.
- **Cloned Bots (NEW):** any user can host their **own** bot with `/clone <bot_token>`. It runs inside the same process and chats on its own. The trained brain (MongoDB), the AI keys **and the whole admin panel** are shared: `/admin` belongs to the main owner and the admins the owner added, in every bot, and shows the same data everywhere. `/start` and `/clone` are published to Telegram's `/` command menu.
- **Token-safe cloning:** the message carrying a bot token is **deleted the moment it is sent**, and a bare token pasted in a private chat is removed immediately as well.
- **Flask Keep-Alive:** Integrated Flask server for port-binding and health checks in cloud environments (Render, Heroku).

---

## 🛠️ Configuration

Configure the bot using a `.env` file or direct environment variables:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `API_ID` | Telegram API ID from my.telegram.org | `22243185` |
| `API_HASH` | Telegram API Hash from my.telegram.org | *Required* |
| `BOT_TOKEN` | Bot Token from [@BotFather](https://t.me/BotFather) | *Required* |
| `MONGO_URL` | MongoDB URI for the **chat/training brain only** (`Word.WordDb`) | *Required* |
| `BOT_MONGO_URL` | Second MongoDB URI for everything the bot owns (users, chats, admins, sessions, clones, settings, AI keys). Empty = use `MONGO_URL` too | *(empty)* |
| `OWNER_ID` | User ID of the primary owner | *Required* |
| `OWNER_USERNAME` | Username of the owner (without `@`) — leave empty to show nothing | *(empty)* |
| `SUPPORT_GRP` | Support group username — leave empty to show nothing | *(empty)* |
| `UPDATE_CHNL` | Update channel username — leave empty to show nothing | *(empty)* |
| `GEMINI_API_KEYS` | Comma-separated Gemini API keys (auto-rotated) | *Optional* |
| `GEMINI_MODEL` | Gemini model id | `gemini-2.0-flash` |
| `OPENAI_API_KEYS` | Comma-separated OpenAI / ChatGPT keys (any OpenAI-compatible key works) | *Optional* |
| `OPENAI_MODEL` | OpenAI model id | `gpt-4o-mini` |
| `OPENAI_BASE_URL` | Endpoint for the OpenAI provider (point it at a proxy for free keys) | `https://api.openai.com/v1` |
| `AI_PROVIDER_ORDER` | Which provider is asked first after the database | `gemini,openai` |
| `AI_ENABLE` | Enable the AI fallback | `true` |
| `AI_DB_FIRST` | Search MongoDB before calling an AI provider | `true` |
| `AI_AUTO_TRAIN` | Save cleaned AI replies back to MongoDB | `true` |
| `AI_GROUP_FALLBACK` | Allow AI replies in groups | `true` |
| `AI_SIMILARITY_THRESHOLD` | Minimum fuzzy-match score (0-1) | `0.72` |

> 📖 Full walkthrough (MongoDB schema, similarity search, key rotation, commands): **[AI_SETUP.md](AI_SETUP.md)**

---

## 📦 Deployment

Step-by-step guide for every host (Railway, Docker, VPS + systemd, Render, plus a
troubleshooting table): **[DEPLOYMENT.md](DEPLOYMENT.md)**

### 🚂 Railway (recommended)

1. Push this repository to GitHub.
2. Railway → **New Project** → **Deploy from GitHub repo** → pick the repo.
   `railway.json` makes Railway build the **`Dockerfile`** automatically
   (health check `/health`, one replica, restart always) — no settings to change.
3. Service → **Variables**, add at least:

   ```env
   API_ID=1234567
   API_HASH=your_api_hash
   BOT_TOKEN=123456789:AA...
   OWNER_ID=123456789
   MONGO_URL=mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
   # optional but recommended
   BOT_MONGO_URL=mongodb+srv://user:pass@cluster1.yyyyy.mongodb.net/?appName=Cluster0
   GEMINI_API_KEYS=AIza...
   ```

4. **Settings → Networking → Generate Domain**, then deploy. Open the URL — it must
   answer `✅ Myra Bot is Alive!` — and finally send `/start` to your bot.

> ⚠️ Keep **replicas at 1** (two copies would answer everything twice) and pick a
> plan that does **not** sleep (Railway Hobby, ~$5/month) so the userbots stay
> online 24/7.
>
> No GitHub? `npm i -g @railway/cli && railway login && railway init && railway up`

### 🐳 Docker (local or VPS)

```bash
cp sample.env .env          # fill in your credentials
docker build -t myra-bot .
docker run -d --name myra-bot --restart unless-stopped \
  --env-file .env -p 5000:5000 myra-bot
docker logs -f myra-bot
```

…or `docker compose up -d --build`. The container listens on `$PORT` (default
`5000`) and serves `/` and `/health` for the platform health check.

### 💻 Local run

1. Clone this repository:
   ```bash
   git clone https://github.com/fifjrkrkrjrokrj-dotcom/AyushChatBot.git
   cd AyushChatBot
   ```
2. Copy `sample.env` to `.env` and fill in the values:
   ```bash
   cp sample.env .env
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Start the bot:
   ```bash
   bash start
   # or
   python3 -m Myra
   ```

> ⚠️ **Userbot logins need a modern client.** `requirements.txt` pins
> `kurigram==2.2.24` (a maintained Pyrogram v2 fork that speaks a current
> Telegram API layer). Plain `pyrogram` (layer ≤ 158) makes Telegram reject every
> phone/OTP login with `406 UPDATE_APP_TO_LOGIN`. Verify any time with
> `python tools/check_pyrogram.py` (expect `OK: Kurigram is in place`) — the
> Docker build runs the same check and refuses to ship a broken image.
> Details: **[AI_SETUP.md §6.1.1](AI_SETUP.md)**.

---

## 🤖 The Bot

- **Bot:** [@MyraaRobot](https://t.me/MyraaRobot)
- **Commands:** `/start` · `/clone` (everything else is hidden from the `/` menu and reachable via `/admin`)
