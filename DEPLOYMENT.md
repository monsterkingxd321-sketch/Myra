# 🚀 Myra — Hosting & Deployment Guide

Deploy **Myra** (hybrid Hinglish chatbot + multi-userbot AutoChat) 24/7 on
**Railway**, plain **Docker**, a **Linux VPS**, or **Render**.

Everything needed is already in the repository:

| File | What it is for |
| :--- | :--- |
| `Dockerfile` | Multi-stage image (non-root, health-checked, `$PORT` aware) — used by Railway |
| `.dockerignore` | Keeps `.env`, sessions, logs and `.git` **out of the image** |
| `railway.json` | Tells Railway to build the Dockerfile, use `/health`, 1 replica, restart always |
| `docker-compose.yml` | Local / VPS one-command run |
| `nixpacks.toml`, `Procfile` | Fallbacks for hosts that do not build a Dockerfile |
| `tools/check_pyrogram.py` | Build gate: fails the image if plain `pyrogram` ever replaces Kurigram |

---

## 0. Collect these 5 values first

| Value | Where to get it |
| :--- | :--- |
| `API_ID` | [my.telegram.org](https://my.telegram.org) → *API development tools* |
| `API_HASH` | Same page as `API_ID` |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `OWNER_ID` | Your numeric Telegram id — from [@userinfobot](https://t.me/userinfobot) |
| `MONGO_URL` | [cloud.mongodb.com](https://cloud.mongodb.com) → free M0 cluster → *Connect → Drivers* |

Optional but recommended:

| Value | Why |
| :--- | :--- |
| `BOT_MONGO_URL` | A **second** MongoDB for everything the bot owns (users, chats, admins, userbot sessions, clones, settings, AI keys). The training brain stays alone on `MONGO_URL`. Leave empty to use one cluster for both (see [AI_SETUP.md](AI_SETUP.md)). |
| `GEMINI_API_KEYS` | Comma-separated [Gemini](https://aistudio.google.com/apikey) keys — the AI fallback. Empty = database-only mode. |
| `OPENAI_API_KEYS` | Comma-separated OpenAI keys, or any OpenAI-compatible proxy key (`OPENAI_BASE_URL`). |

> 💡 On MongoDB: create a database user, then *Network Access → Allow access from
> anywhere* (`0.0.0.0/0`) — Railway and Render have no fixed IP.

---

## 1. 🚂 Railway with GitHub (recommended)

1. Push this repository to GitHub (branch `main`).
2. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → pick the repo.
3. Railway reads `railway.json` and builds **`Dockerfile`** automatically. No build settings to change.
4. Open the service → **Variables** → **Raw Editor** and paste:

   ```env
   API_ID=1234567
   API_HASH=your_api_hash
   BOT_TOKEN=123456789:AA...
   OWNER_ID=123456789
   MONGO_URL=mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
   BOT_MONGO_URL=mongodb+srv://user:pass@cluster1.yyyyy.mongodb.net/?appName=Cluster0
   GEMINI_API_KEYS=AIza...
   OWNER_USERNAME=your_username
   ```

   *(Omit `BOT_MONGO_URL` and `GEMINI_API_KEYS` if you do not use them.)*
5. **Settings → Networking → Generate Domain** (gives you the URL used by uptime pings).
6. Deploy. First build ≈ 2–4 minutes.

**Do not add more replicas.** *Settings → Deploy → Replicas must stay at `1`*
(`railway.json` already sets it): two copies would answer every message twice and
fight over the same MongoDB.

> ⚠️ Hardware: give the service at least **1 GB RAM** and disable *Serverless /
> App Sleeping* so the userbots stay online. A 24/7 bot needs Railway's **Hobby**
> plan (~$5/month) — the trial credit is not enough for permanent hosting.

---

## 2. 🚂 Railway without GitHub (CLI)

Handy when the code is only on your machine (nothing to commit yet):

```bash
npm i -g @railway/cli
railway login
railway init          # create an empty project + service
railway up            # uploads this folder, builds the Dockerfile
railway domain        # optional: public URL for pings
```

Then add the variables (dashboard → **Variables**, or CLI):

```bash
railway variables --set "API_ID=1234567" --set "BOT_TOKEN=123456789:AA..." \
  --set "API_HASH=..." --set "OWNER_ID=123456789" --set "MONGO_URL=mongodb+srv://..."
railway up --detach   # redeploy with the new variables
```

---

## 3. Verify the deploy

Railway → **Deployments → View Logs**. A healthy boot looks like this:

```
🌐 Health server listening on http://0.0.0.0:8080/health
🍃 MongoDB (chat/training): mongodb+srv://***@cluster0.kfidbdj.mongodb.net/...
🍃 MongoDB (bot data):      mongodb+srv://***@cluster0.e7xarac.mongodb.net/...
🔑 gemini keys ready: 2/2 active (env 2 · file 0 · db 0)
🔑 openai keys ready: 1/1 active (env 0 · file 0 · db 1)
@MyraaRobot Started Successfully
Loading 1 userbot account(s)...
✅ Userbot … connected successfully!
```

Then open your Railway domain — it must answer `✅ Myra Bot is Alive!`
(and `/health` → `{"status":"ok",...}`).

Finally send `/start` to your bot on Telegram.

---

## 4. Golden rules

1. **One replica only.** Multi-userbot AutoChat, the join-request controller and
   AutoChat timers all assume a single process.
2. **The container disk is ephemeral.** Every redeploy wipes `log.txt` — that is
   fine, logs also go to the platform console (stdout). All real state lives in
   MongoDB, so nothing important is lost.
3. **Never let plain `pyrogram` be installed.** `requirements.txt` pins
   `kurigram==2.2.24`; the Docker build runs `tools/check_pyrogram.py` and fails
   if the wrong package is present (plain `pyrogram` breaks every userbot login
   with `406 UPDATE_APP_TO_LOGIN`). Details: [AI_SETUP.md §6.1.1](AI_SETUP.md).
4. **`.env` must never be committed or baked into an image.** `.gitignore` and
   `.dockerignore` both exclude it — put secrets in the host's Variables panel.
5. **Env changes need a redeploy** (Railway restarts automatically when you save
   variables).

---

## 5. 🐳 Docker (local or any VPS)

```bash
cp sample.env .env      # fill in your credentials
docker build -t myra-bot .
docker run -d --name myra-bot --restart unless-stopped \
  --env-file .env -p 5000:5000 myra-bot
docker logs -f myra-bot
```

Or with Compose (adds `init`, log rotation and a health check):

```bash
docker compose up -d --build
docker compose logs -f
```

Hosting behind a proxy? The container reads `PORT` from the environment, so
`docker run -e PORT=8080 -p 8080:8080 myra-bot` just works.

---

## 6. 🖥 Linux VPS (Ubuntu / Debian) + systemd

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git build-essential libffi-dev libssl-dev
git clone https://github.com/fifjrkrkrjrokrj-dotcom/AyushChatBot Myra
cd Myra
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python tools/check_pyrogram.py        # must print "OK: Kurigram is in place"
cp sample.env .env && nano .env       # fill in your credentials
python -m Myra                        # smoke test, then Ctrl+C
```

Run it permanently:

```bash
sudo cp myrabot.service /etc/systemd/system/myra.service
sudo nano /etc/systemd/system/myra.service   # fix WorkingDirectory/Owner if needed
sudo systemctl daemon-reload
sudo systemctl enable --now myra
journalctl -u myra -f                        # live logs
```

`setup.sh` automates the apt/venv/pip part if you prefer.

---

## 7. 🌐 Render

1. Push to GitHub → Render dashboard → **New → Web Service** → connect the repo.
2. Render detects `Dockerfile` (or use `render.yaml`).
3. Add the same variables as in step 1, plus `PORT` is injected automatically.
4. Health check path: `/health`. Choose a plan that does **not** sleep.

---

## 8. Updating a deployed bot

```bash
git add -A
git commit -m "update"
git push
```

* **Railway / Render** redeploy automatically on push.
* **CLI method:** `railway up`.
* **Docker/VPS:** `git pull && docker compose up -d --build`
  (or `sudo systemctl restart myra`).

Adding a *userbot* never needs a redeploy — upload the `.session` file or use
`/useraccounts → ➕ Add Userbot`; the session is stored in MongoDB.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| Deploy fails: `FATAL: this looks like plain pyrogram` | Something installed plain `pyrogram` | Keep `kurigram==2.2.24`; nothing may depend on `pyrogram`. The build is deliberately failing so userbots do not silently break. |
| Crash at startup: `ModuleNotFoundError: No module named 'pyrogram.emoji'` | `Abg` starts `Abg/inline/inline_keyboard.py` with `from pyrogram.emoji import *` and uses thirteen `FLAG_*` constants from it. Stock Pyrogram ships that module, **Kurigram dropped it**. A machine that once had plain Pyrogram installed still has the leftover file, which is why this only blew up in the container. | Already handled in the repo: `Myra/_compat/pyrogram_emoji.py` re-creates the flag constants and `Myra/__init__.py` registers it as `pyrogram.emoji` before importing `Abg` (nothing changes when the real module is present). Verify with `python tools/smoke_import.py` — the Docker build runs the same check as build gate #2. |
| `❌ Missing required environment variable(s): …` in logs | Variables not set / not redeployed | Add them in the host's Variables panel, then redeploy. |
| Health check timeouts, service restarts in a loop | Nothing listening on `$PORT` | Do not override `PORT`; the Flask server binds `0.0.0.0:$PORT`. Check the port in the logs. |
| Build fails at `tgcrypto` | A toolchain-less platform without wheels | The image installs `build-essential` in the builder stage — keep the multi-stage `Dockerfile` intact. |
| Bot replies twice to everything | More than one replica running | Set replicas to `1`. |
| `406 UPDATE_APP_TO_LOGIN` when adding a userbot | Client is not Kurigram | `pip install --force-reinstall --no-deps kurigram==2.2.24`, verify with `python tools/check_pyrogram.py`. |
| AI never answers, log says `insufficient_quota` / `credit_balance_exhausted` | Key has no credits | Add a working key (`/addkey gemini AIza…`) and run `/aireload`. The bot keeps working in database-only mode meanwhile. |
| Bot starts but says `MONGO_URL … missing` | No database configured | Set `MONGO_URL`. |
| Hybrid search slow / Atlas full | Large indexes on the free tier | Keep `AI_CREATE_TOKENS_INDEX=false` (default) and `AI_CREATE_TEXT_INDEX=false`. |

More detail: [README.md](README.md) · [AI_SETUP.md](AI_SETUP.md)
