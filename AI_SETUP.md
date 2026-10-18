# 🧠 Hybrid Database-First AI — Setup & Architecture

This upgrade turns the **Myra bot + Multi-Userbot AutoChat** system into a
*hybrid intelligence* engine:

> **MongoDB first → Google Gemini fallback → clean the answer → train the DB → offline-safe.**

The bot and **every** userbot share the *same* MongoDB collection and the *same*
Gemini logic, so they all learn from each other.

---

## 1. Project structure (modular)

```
Myra/
├── ai/                         ← NEW: the intelligence layer
│   ├── __init__.py             # package exports
│   ├── cleaner.py              # strict anti-spam / link / mention cleaner
│   ├── humanizer.py            ← NEW: human-like pacing (delay, typing, chance)
│   ├── audience.py             ← NEW: only talk to *real* humans (never bots/self)
│   ├── key_rotator.py          # multi API-key rotation (429 / invalid handling)
│   ├── gemini.py               # google-genai wrapper (shared singleton)
│   ├── openai_client.py        ← NEW: OpenAI / ChatGPT (any compatible endpoint)
│   └── brain.py                # the hybrid decision engine (tries every provider)
│
├── database/
│   ├── training.py             ← NEW: training store + similarity search + /clean_db
│   ├── settings.py             ← NEW: global runtime settings (/speed, /chance)
│   ├── autochat.py             ← NEW: time-to-time messaging settings
│   ├── joinreq.py              ← NEW: Group Request Controller — per-group config
│   ├── ai_keys.py              ← NEW: runtime API keys, tagged per provider
│   ├── scope.py                ← NEW: clone identity + the global "main" view
│   ├── bots.py                 ← NEW: cloned-bot registry + identity cache
│   ├── admins.py               # owner/admin checks (GLOBAL: main owner + admins)
│   ├── chats.py / users.py     # audience (one shared pool)
│   └── __init__.py
│
├── services/                   ← NEW: background services
│   ├── broadcast.py            # send as bot / as all userbots → groups · DMs · both
│   ├── autochat.py             # the time-to-time scheduler (bot + every userbot)
│   ├── commands.py             ← NEW: the minimal `/` command menu (start + clone)
│   ├── joinreq.py              ← NEW: join-request flow (verify → approve)
│   ├── clones.py               ← NEW: clone manager (same process, own identity)
│   └── userstats.py            ← NEW: audience counts for the panel
│
├── modules/
│   ├── admin_panel.py          ← NEW: /admin inline control panel (everything)
│   ├── clone.py                ← NEW: /clone /mybots /delbot — host your own bot
│   ├── joinreq.py              ← NEW: ChatJoinRequest + ✅ VERIFY handlers
│   ├── session_upload.py       ← NEW: add userbots by uploading .session / .zip
│   ├── broadcast.py            # REWRITTEN: /br quick cast + /an announce
│   ├── ai_admin.py             ← NEW: /speed /chance /addkey /delkey /clean_db /aistats …
│   ├── chatbot.py              # REWRITTEN: hybrid brain + human pacing
│   ├── start.py                # UPDATED: one clean TEXT card, no photo, no animation
│   ├── ping.py                 # UPDATED: plain-text latency check (no sticker/photo)
│   ├── useraccounts.py         # UPDATED: per-userbot group mode + reply chance
│   ├── stats.py                # UPDATED: trained replies, API keys, DB size
│   └── ...
│
└── userbot/
    ├── chatbot_engine.py       # thin wrapper around Myra.ai.brain (shared!)
    ├── handlers.py             # UPDATED: replies to plain group messages, human pacing
    ├── importer.py             ← NEW: Pyrogram **and** Telethon .session import
    └── ...
```

Nothing else in the repo needs to change — the public entry points
(`get_chatbot_response`, `Myra.on_cmd`, the userbot handlers) keep the same
name and signature, so you can drop this into your existing repo.

---

## 2. `.env` configuration

```env
# ── Telegram ──
API_ID=
API_HASH=
BOT_TOKEN=
OWNER_ID=

# ── MongoDB: TWO separate clusters ──
# MONGO_URL is used for the CHAT/TRAINING brain only (Word.WordDb: the learned
# replies and the trained stickers). The bot never writes anything else here.
MONGO_URL=
# BOT_MONGO_URL holds everything the bot owns: users, chats, admins, userbot
# accounts, sessions (LoginDB/SessionDB), clones, settings and the AI keys.
# Leave it empty to keep everything on MONGO_URL (old single-cluster setup).
BOT_MONGO_URL=

# ── Hybrid AI (Google Gemini) ──
# Use ONE OR MANY keys. Separate with commas. Rotation is automatic.
GEMINI_API_KEYS=AIzaSy........,AIzaSy........,AIzaSy........
# Optional: a text file with one key per line (unlimited keys, editable live).
GEMINI_API_KEYS_FILE=
GEMINI_MODEL=gemini-2.0-flash

# ── Hybrid AI (OpenAI / ChatGPT) — optional second provider ──
# Works with api.openai.com AND with any OpenAI-compatible endpoint, so a free
# ChatGPT key from a proxy (OpenRouter, Groq, Together, a local gateway …) works:
# just set OPENAI_BASE_URL to that proxy. Add keys at runtime with /addkey openai.
OPENAI_API_KEYS=sk-........,sk-........
OPENAI_API_KEY=                 # single-key alias, same thing
OPENAI_API_KEYS_FILE=           # optional text file, one key per line
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_ENABLE=true
# Which provider is asked FIRST when the DB has no answer (both are always tried).
AI_PROVIDER_ORDER=gemini,openai # or: openai,gemini

AI_ENABLE=true              # master switch for the AI fallback
AI_DB_FIRST=true            # search MongoDB before spending an API call
AI_AUTO_TRAIN=true          # save cleaned AI replies back to the DB
AI_PRIVATE_FALLBACK=true    # allow AI in private chats
AI_GROUP_FALLBACK=true      # allow AI in groups when the bot is addressed
AI_GROUP_FALLBACK_PLAIN=false   # also use AI for plain group msgs (quota hungry!)
AI_SIMILARITY_THRESHOLD=0.72
AI_SEARCH_CANDIDATES=80
AI_MAX_OUTPUT_TOKENS=160
AI_KEY_COOLDOWN=60          # seconds a key is parked after a 429
AI_CREATE_TEXT_INDEX=false  # optional full-text index (large)
AI_CREATE_TOKENS_INDEX=false # optional 'tokens' index (large, better recall)

# ── Human-like behaviour ──
AI_GROUP_REPLY_CHANCE=85    # % of group messages the BOT answers (100 = always)
AI_PRIVATE_REPLY_CHANCE=100 # % of DM messages the bot answers
USERBOT_GROUP_CHANCE=70     # % of eligible group messages a userbot answers
USERBOT_PRIVATE_CHANCE=100  # % of DMs a userbot answers
USERBOT_GROUP_REPLY_ALL=true    # userbots also reply to plain "hy / hello" in groups
HUMAN_TYPING_WPM=220        # typing speed used for the "typing…" indicator
HUMAN_SPLIT_REPLIES=true    # sometimes send a long reply as two bubbles

# ── Girl voice ──
AI_FEMININE=true            # feminize EVERY reply, DB replies included
AI_GIRLY_EMOJI=true         # occasionally add a cute emoji to a reply with none

# ── Audience ──
ONLY_REAL_USERS=true        # ignore bots + our own userbots (no self-chatting)

# ── Time-to-time messaging (AutoChat) ──
# Startup defaults only — change them live from /admin → ⏰ AutoChat.
AUTOCHAT_ENABLED=true           # master switch for the background service
AUTOCHAT_TICK_SECONDS=20        # how often the scheduler wakes up
AUTOCHAT_BATCH=1                # how many chats get a message per round
AUTOCHAT_BOT_ENABLED=false      # main bot auto-messages
AUTOCHAT_BOT_INTERVAL_MS=900000 # 15 min average (±40% jitter)
AUTOCHAT_BOT_TARGETS=groups     # groups | dm | both
AUTOCHAT_UB_ENABLED=false       # userbots auto-message
AUTOCHAT_UB_INTERVAL_MS=600000  # 10 min average (±40% jitter)
AUTOCHAT_UB_TARGETS=groups      # groups | dm | both

# Userbot delay (milliseconds) used until /speed changes it
USERBOT_SPEED_DEFAULT_MS=0

# ── Cloned bots (same process, shared brain) ──
# Anyone can host their own bot with `/clone <bot_token>`. A clone chats on its
# own, but the admin surface stays global: ONE panel, ONE owner (OWNER_ID) and
# ONE admin list, identical in every bot. Cloning gives reach, not control.
CLONE_ENABLED=true
MAX_CLONES=20               # the process is shared, so it is capped
CLONE_ALLOWED_USERS=        # empty = everyone may clone, else comma-separated ids
```

> **Unlimited keys without editing `.env`.** An admin can add keys at runtime with
> `/addkey <key>` — as many as they want. They are stored in MongoDB
> (`UserbotDB.ai_keys`), validated first, and merged with the `.env` keys on every
> startup and `/aireload`.

Install the new dependency:

```bash
pip install -r requirements.txt      # includes google-genai and openai
```

Get free Gemini keys at <https://aistudio.google.com/apikey>. OpenAI / ChatGPT
keys go in `OPENAI_API_KEYS` (auto-detected by the `sk-` prefix), and a free key
from any OpenAI-compatible proxy works too — point `OPENAI_BASE_URL` at it.

---

## 3. MongoDB structure for "Similarity Search"

### 3.1 Collection

```
MongoDB  →  Database: Word  →  Collection: WordDb
```

`WordDb` is the **same collection** the old bot already used, so your existing
training data keeps working. Shared by the bot *and* all userbots.

### 3.2 Document schema

```jsonc
{
  "_id":         ObjectId("..."),

  // ── legacy fields (unchanged) ──
  "word":        "hello bhai kaise ho",   // normalized trigger  ← indexed
  "text":        "hlooo ✨ badhiya ho",   // reply text OR sticker file_id
  "check":       "none",                  // "none" = text, "sticker" = sticker
  "id":          "<sticker file_unique_id>",  // stickers only

  // ── added by the hybrid upgrade ──
  "trigger_raw": "Hello Bhai!! Kaise ho",  // original message
  "tokens":      ["hello","bhai","kaise","ho"],  // token index for fuzzy search
  "source":      "gemini",                  // "gemini" | "learned" | "manual"
  "usage_count": 0,
  "created_at":  1700000000.0,
  "updated_at":  1700000000.0
}
```

### 3.3 Indexes (created automatically on startup)

```js
db.WordDb.createIndex({ "word":   1 })          // exact / prefix lookups  (always)
db.WordDb.createIndex({ "tokens": 1 })          // multi-key token lookup  (opt-in)
db.WordDb.createIndex({ "text":  "text" })      // full-text fallback      (opt-in)
```

`Myra/database/training.py → ensure_indexes()` runs these at startup (and it is
also triggered on module import).

> **Only the `word` index is created by default.** The `tokens` and `text`
> indexes are large on multi-million document collections and are therefore
> opt-in (`AI_CREATE_TOKENS_INDEX` / `AI_CREATE_TEXT_INDEX`). When the `tokens`
> index is missing the search automatically skips the token query — no errors,
> no full collection scans.

### 3.4 How the similarity search works (no full collection scans)

`search_reply()` collects a **bounded candidate set**, then scores it locally:

1. **Exact / normalized match** — `word ∈ {raw, lower, cleaned}` (fast, indexed).
2. **Token overlap** — `tokens: { $in: [...] }` (uses the multi-key index).
3. **Prefix regex** — `word: /^first_token/i` when too few candidates.
4. **MongoDB `$text`** — full-text fallback when nothing else matched.

Every candidate is then scored by `similarity()`:

| Rule | Score |
| :--- | :--- |
| identical strings | `1.00` |
| one string contains the other | `0.82 – 0.97` (length-weighted) |
| token Jaccard / containment | up to `0.92` |
| fuzzy ratio (`rapidfuzz` if installed, else `difflib`) | ×0.90 |

The best candidate is returned **only if `score ≥ AI_SIMILARITY_THRESHOLD`**.
Otherwise the brain falls through to Gemini → the API is only paid for
*genuinely new* messages.

> **Optional upgrade — Atlas Vector Search.** If you want semantic matching,
> add an embedding field and let Gemini embed the trigger:
> ```python
> # store:  {"embedding": [0.12, -0.9, ...]}
> # query:
> pipeline = [{
>     "$vectorSearch": {
>         "index": "vector_index",
>         "path": "embedding",
>         "queryVector": query_embedding,
>         "numCandidates": 100,
>         "limit": 5
>     }
> }]
> ```
> Create the index in Atlas UI (`Atlas Search → JSON Editor`):
> ```json
> {
>   "fields": [{
>     "type": "vector", "path": "embedding",
>     "numDimensions": 768, "similarity": "cosine"
>   }]
> }
> ```
> The token-based approach above already works on every MongoDB (including the
> free tier) without embeddings.

---

## 4. The Brain — request lifecycle

`Myra/ai/brain.py → get_ai_reply()`

```
incoming message
      │
      ├─▶ 1. sticker lookup?            → reply with sticker (DB)
      │
      ├─▶ 2. search_reply() fuzzy DB    → HIT  → reply          (0 API cost)
      │        └── also: bump usage_count
      │
      ├─▶ 3. AI fallback                 → AI_PROVIDER_ORDER: gemini, then openai
      │        │
      │        ├─ cleaner.clean_text()  → links/@tags/promo removed
      │        │        └── rejected? → offline mode (return None)
      │        │
      │        ├─ AI_AUTO_TRAIN         → save_reply() back into WordDb
      │        └─ return reply (source = "gemini" | "openai")
      │
      └─▶ 4. offline mode               → return None (DB-only operation)
```

* **Every** reply (from an AI provider *or* from human learning) passes through
  `cleaner.clean_text()` **before being sent and before being stored**.
* If **all keys are removed / rate-limited**, `ai_available()` returns `False`
  and the bot keeps running strictly on the database.

### Humanization persona

Set in `config.AI_PERSONA` (override with the `AI_PERSONA` env var):

> *"You are a cute, lively Hinglish girl chatting casually on Telegram. Always
> reply in short Hinglish (Roman Hindi + English mix), max 1-2 lines, under 120
> characters, playful and emotional with emojis like ✨ 🥺 😂 💖 😅 🙈. Never
> claim to be a bot. STRICT RULES: no links, no http/https, no t.me, no
> @usernames, no promotion, no 'join', 'subscribe', 'buy', 'click'."*

### Human-like behaviour

`Myra/ai/humanizer.py` makes the bot act like a person instead of a script:

| Behaviour | How it works |
| :--- | :--- |
| **Replies "kabhi kabhi"** | `should_reply(chance)` — a 0-100 probability. The bot does *not* answer every single message, so the chat looks natural instead of robotic. |
| **Thinking time** | `reading_pause(text)` — a small random pause before typing starts. |
| **Real typing speed** | `typing_seconds(text)` — the *"typing…"* indicator is shown for as long as a human would need to type that reply (based on `HUMAN_TYPING_WPM`), with random jitter. |
| **Two bubbles** | `maybe_split()` — a longer reply is occasionally sent as two separate messages, one thought at a time. |
| **No formal full stops** | `human_touch()` — trims the trailing `.` and collapses stray spaces, like real Telegram chat. |
| **Girl voice** | `feminize()` — every outgoing line is converted to feminine Hinglish first-person grammar: `karta hoon → karti hoon`, `kar raha hoon → kar rahi hoon`, `karunga → karungi`, `thak gaya hoon → thak gayi hoon`. It **only touches first-person forms**, so a reply that talks about the user (`tu kya kar raha hai`) is never broken. |
| **Cute emoji** | `girly_touch()` — occasionally adds ✨ 🥺 😅 🙈 💖 🌸 to a reply that has no emoji at all. |

> **The girl voice is applied to *every* reply — including the ones coming from
> the migrated training database** (that data was written without a fixed
> gender). New replies are also *stored* feminized, so the voice gets more
> consistent over time. Disable with `AI_FEMININE=false`.

`respond_like_human()` glues this together and is used by **both** the bot and
the userbots:

```python
await respond_like_human(
    client, message, {"text": "haye ✨ kya hua?"},
    chance=config.USERBOT_GROUP_CHANCE,   # reply only ~70% of the time
    base_delay_ms=account_delay,          # /speed value
    wpm=config.HUMAN_TYPING_WPM,
)
```

### Where do the replies come from?

* **Private (DM):** the bot chats with everyone — MongoDB answer first, Gemini
  when nothing matches (`AI_PRIVATE_FALLBACK`). A DM is always answered, even if
  it starts with `@thebotusername hello` (the mention is stripped first).
* **Groups:** the bot **and** every userbot answer plain messages like
  `hy`, `hello`, `kaise ho` **without needing a tag or reply** — exactly like a
  real member of the group.
  * **When the bot is mentioned** (`@botname hello`), replied to, or its username
    appears in the text, it **always** answers (reply chance is forced to 100%)
    and the `@mention` is removed before the database search, so
    `@mybot kaisi ho` is looked up as `kaisi ho`. That works for the bot **and**
    every userbot.
  * For the userbot this is `USERBOT_GROUP_REPLY_ALL=true` (or the per-userbot
    **Group Reply: All / Only when tagged** toggle in `/useraccounts`).
  * Gemini is only spent on messages that actually address the bot. Plain
    messages are answered from the trained DB (`AI_GROUP_FALLBACK_PLAIN=false`
    by default) so a busy group does not burn your API quota.
* **`/start`** sends a single clean card: the message text plus **Add me to your
  group** and **Clone your bot** — two buttons, nothing else. There is no photo,
  no intro animation, no sticker and no follow-up greeting — and every card is
  plain standard English with no emoji and no formatting.

#### Only real users chat (no bot-to-bot)

`Myra/ai/audience.py → is_real_user()` is checked **before** any reply — by the
bot and by every userbot — so the fleet can never talk to itself. Ignored
senders:

| Sender | Why |
|---|---|
| any **bot account** (including this bot) | bots are not users |
| **our own userbot accounts** (memory + `UserbotDB.accounts`) | prevents userbot ⇄ userbot chat |
| the sender itself / own outgoing messages | loop prevention |
| **channel posts**, anonymous admins, service messages (`from_user is None`) | not a real person |

A DM from the main bot to a userbot is therefore ignored, and a userbot never
replies to another userbot — only genuine humans get answers. The account list is
cached for 60 s to keep it free of database round-trips. Disable with
`ONLY_REAL_USERS=false`.

---

## 5. Multi-key rotation logic

`Myra/ai/key_rotator.py → KeyRotator.execute(factory)`

```python
rotator = KeyRotator(["key1", "key2", "key3"], cooldown=60, name="gemini")
response = await rotator.execute(lambda key: call_provider(key))
```

Each provider owns **its own pool** (`Myra/ai/gemini.py`,
`Myra/ai/openai_client.py`), so a Gemini key is never sent to OpenAI and a
ChatGPT key never to Google:

```python
from Myra.ai.brain import PROVIDERS, provider_order   # ['gemini', 'openai']

for name in provider_order():          # try each provider in order
    reply = await PROVIDERS[name].generate(prompt)
    if reply:
        break                          # Gemini first, then OpenAI
```

1. Builds the list of **healthy** keys (`cooldown_until <= now` and not `invalid`).
2. Starts from a rotating cursor so load is spread evenly (round-robin).
3. Calls `await factory(key)`:
   * **success** → return the result immediately.
   * **429 / quota / RESOURCE_EXHAUSTED** → park the key for `AI_KEY_COOLDOWN`
     seconds and try the next key.
   * **invalid / 401 / 403 / API key not valid** → mark the key `invalid` and
     remove it from the pool permanently.
   * **any other error** → short cooldown (≤15s) and continue to the next key.
4. If the active provider has **no healthy key left**, the brain simply moves on
   to the next provider in `AI_PROVIDER_ORDER`. If *all* of them are exhausted it
   degrades to database-only mode — *the user's message is never dropped*.

Inspect the live state with `/aikeys` (grouped per provider), or clear cooldowns
with `/aireload` after adding fresh keys.

---

## 6. Admin commands

| Command | What it does |
| :--- | :--- |
| `/speed [ms]` | Set the userbot reply delay in **milliseconds** (global + every connected userbot). `/speed 1500` = 1.5 s, `/speed 0` = instant. Without an argument it shows the current configuration. |
| `/chance [group\|private] <0-100>` | How often replies are sent — the human "kabhi kabhi" factor. `/chance 70` sets the group chance, `/chance private 100` the DM chance. Without an argument it shows both. |
| `/addkey [provider] <key> [label\|base_url]` | Add a **Gemini** or **OpenAI/ChatGPT** API key — **unlimited**. The provider is auto-detected (`sk-…` → OpenAI, `AIza…` → Gemini) or forced: `/addkey openai <key> [base_url]`. The key is validated with a live test call before it is saved to MongoDB, and the message containing the key is deleted for safety. |
| `/delkey [provider] <masked\|index>` | Remove a stored key (e.g. `/delkey AIzaSy...7890`, `/delkey 2`, `/delkey openai 1`). Keys that came from `.env` must be removed there. |
| `/clean_db` | Scan the whole `WordDb` and **delete** every entry containing links, `t.me`, @usernames or promotional words. Prints a report with reasons. |
| `/stats` | Total trained replies, **active API keys**, database size and current mode (hybrid / offline). |
| `/aistats` | Full hybrid-brain status (DB-first, auto-train, threshold, provider order, models, per-key state). |
| `/aikeys` | Per-key rotation status, grouped by provider (active / cooling / invalid, uses, failures). |
| `/aireload` | Re-read keys from **`.env` + key file + MongoDB** for every provider and clear all key cooldowns — **no restart needed**. |
| `/admin` | 🛡 Open the full **inline control panel** (broadcast · autochat · userbots · keys · behaviour · clean · stats). |
| `/broadcast` (or `/bc`) | 📢 Jump straight to the broadcast section: pick the **sender** (main bot / all userbots / one userbot) and the **target** (groups only / DMs only / both), then send the message. |
| `/br` (or `/gcast`) | Quick cast: reply to any message and it is broadcast from the **main bot** to all groups **and** all DM users. |
| `/an` | Forward the replied-to post to every group and every user. |
| `/clone <bot_token>` | 🧬 Host **your own bot** (get a token from @BotFather). It runs in the same process and chats on its own. The trained database, the AI keys and the whole admin surface are shared, on purpose. |
| `/mybots` | List the bots you cloned, with live 🟢/🔴 status. |
| `/delbot <id\|@username>` | Stop and forget one of **your** clones (its token can then be cloned again). |
| `/clonehelp` | How to get a bot token from @BotFather. |

All of these are restricted to the **main Bot Owner** and the admins the owner
added (`Myra/database/admins.py`) — in **every** bot, main or clone. `/clone`,
`/mybots` and `/delbot` are the only ones any user may use, and only for the
clones they created themselves.

### 6.1 Adding userbots by uploading a `.session` file

Open `/admin` → **📁 Import Sessions** and simply send a file in the bot's private
chat:

* a single `*.session` file → that one account is added;
* a `*.zip` full of `*.session` files → **every** account inside is added.

Both **Pyrogram** and **Telethon** session files work — the format is detected
from the SQLite schema:

| Format | How it is converted |
| :--- | :--- |
| Pyrogram | Pyrogram reads the file and `export_session_string()` produces the string. |
| Telethon | The `dc_id` + raw `auth_key` are read straight from the file, packed into a Pyrogram session string, `get_me()` fills in the user id. |

Only the resulting **session string** is stored (`SessionDB.sessions`) — nothing
stays on disk — and every account is restored automatically on restart, exactly
like the `/useraccounts` OTP login. The uploaded message is deleted afterwards,
zip entries are flattened (zip-slip safe), duplicate names are renamed and a zip
is capped at `MAX_SESSIONS_PER_ZIP` (200) accounts.

> An account belongs to exactly one bot: re-adding an account that is already
> connected to the main bot (or to another clone) is refused, so two bots can
> never drive the same Telegram account at the same time.

### 6.1.1 The phone / OTP flow (`/useraccounts` → ➕ Add Userbot)

As an alternative to a `.session` file you can link an account with just its
phone number: send the number with the country code, then the OTP in a single
message (`1 2 3 4 5`), and a 2FA password if the account has one.

**`406 UPDATE_APP_TO_LOGIN` — "the app must be updated before login"**

Telegram checks the *application* (its API layer) on every **user** login and
refuses outdated ones. The old `pyrogram` releases (layer ≤ 158) are rejected:

```text
2026-10-18 17:31:33  [406 UPDATE_APP_TO_LOGIN]  auth.SendCode
```

Two fixes, either one works:

1. **Just upload a `.session` / `.zip`** (§6.1). That path never calls
   `auth.SendCode`, so it is unaffected.
2. **Use the modern client** — this is what the project pins:

   ```bash
   pip install -U kurigram==2.2.24     # installed as the `pyrogram` package
   ```

   Verify with:

   ```bash
   python -c "from pyrogram import raw; print(raw.all.layer)"   # expect >= 227
   ```

   Do **not** `pip install pyrogram` afterwards — that downgrades the package
   straight back to layer 151 and breaks every future user login.

The bot now recognises this error and answers with the same instructions
instead of a bare Telegram error, and every login failure is written to
`log.txt` (`userbot login (auth.SendCode) failed: …`).

> Note: bot logins (`ImportBotAuthorization`) are **not** checked this way — the
> main bot and the clones keep working on an old layer, only *user* logins fail.

---

## 6.2 Cloned bots & the global admin surface ("scope")

A clone is a full second bot **inside the same process**, so it costs almost no
extra RAM while giving you more reach: its own token, its own groups, its own DMs.

**Shared by design — one database, one panel**

| Thing | Where |
| :--- | :--- |
| Owner | **always** `config.OWNER_ID` (the primary bot's owner) |
| Admins | `UserbotDB.bot_admins` — one shared list |
| Userbots | `UserbotDB.accounts` — one shared pool |
| Groups / DM users | `chatsdb` / `usersdb` — one shared audience |
| Settings | `/speed`, `/chance`, AutoChat timings, Group Requests — one shared value |
| `/admin` panel | **identical everywhere**: same owner, same admins, same data |

Open `/admin` in the main bot or in any clone and you see the *same* thing. Add
two admin IDs in one bot and they are already there in all the others, because
there is only one row in MongoDB.

**Cloning gives reach, not control.** Whoever runs `/clone` gets a working bot,
but **not** an admin panel from it: `/admin` answers only to the main owner and
the admins the owner added, in every bot. Same for `/useraccounts`, `/addadmin`,
`/broadcast`, `/speed`, `/chance`, `/addkey` …

How it works: every document carries a `scope` string — `"main"` for the primary
bot, `"c<bot_id>"` for a clone — and the clone manager copies the main bot's
handlers and wraps each callback so it runs with the clone's scope in a
`ContextVar`. That keeps a clone's **chat serving** isolated to its own scope
(the trained reply database is shared anyway). The **admin surface** opts into
the global view with the `@global_scope` decorator
(`Myra/database/scope.py`), which forces the scope to `"main"` for the duration
of the handler; `scope_match("main")` then matches every document.

```
/clone 123456789:AAH...        # anyone, in the bot's DM
   ↓
bots.register_bot()            # scope = "c<bot_id>", owner = you
   ↓
Client(bot_token=...) + wrapped handlers   # live in-process
   ↓
the bot chats in its own groups · /admin still belongs to the MAIN owner
```

### 6.2.1 Cloning safely — the token never stays in the chat

A bot token is a password, so it is handled like one:

| Step | What happens |
| :--- | :--- |
| `/clone` (no token) | the guide is shown and the user is remembered for 10 minutes |
| the next message is a **bare token** | the message is deleted **first**, then the bot is hosted |
| `/clone <token>` | the message is deleted **immediately**, before anything else happens |
| a bare token with no `/clone` first | deleted the moment it arrives, plus a short hint |

The deletion happens on the main bot **and** on every clone, and no reply ever
echoes the token back. If the delete fails, the user is told to remove it
manually. Registered as `token_guard` at `group=-1` in `Myra/modules/clone.py`.

### 6.2.2 The `/` command menu — deliberately minimal

The main bot and every clone publish their command list to Telegram right after
they start. Only **two** commands are listed:

```
/start    Start the bot
/clone    Host your own bot
```

Everything else — `/admin`, `/stats`, `/chatbot`, `/mybots`, `/addkey`,
`/clean_db`, … — still works when typed, it simply never appears in the `/`
menu. That keeps the bot looking clean and stops random users from discovering
the admin surface.

Published by `Myra/services/commands.py` (`PUBLIC_COMMANDS` + `publish_commands()`),
called from `Myra/__main__.py` for the main bot and from the clone manager for
each clone.

### 6.2.3 The `/start` card — clean by design

The start card shows **only** the message text and two buttons:

```
Add me to your group    →  t.me/<bot>?startgroup=true
Clone your bot          →  opens the clone sub-menu
```

There is no Help row either — `/help` still works when typed, it is simply not
advertised. `test_human.py` asserts the card is exactly two buttons.

`/start` itself is one message and nothing else: **plain text, no photo**, no
intro animation, no sticker, no "ding dong" typing effect and no Hinglish
greeting after the card. `/ping` is plain text too.

There is **no owner link, no support group, no update channel and no credit
footmark** anywhere in the bot — `OWNER_USERNAME`, `SUPPORT_GRP` and `UPDATE_CHNL`
default to empty in `config.py`, so nothing can leak back in.

The **Clone your bot** button opens a sub-menu with every clone command:

| Button | Does |
|---|---|
| Step by step guide | the `/clonehelp` walkthrough |
| My bots | the `/mybots` list |
| Add me to your group | the `?startgroup` deep link |
| Back / Close | back to the start card / delete the message |

The **Help** screen lists nothing but the clone flow:

```
Help

Add me to your group and I will start chatting there on my own.

To host your own bot:
/clone - Send this with your bot token.
/clonehelp - Step by step guide.
/mybots - Bots you have hosted.
/delbot - Remove a bot you hosted.
```

`/ping`, `/id`, `/repo`, `/stats`, `/useraccounts` and `/admin` are **not** shown
here — they all keep working when typed, they simply are never advertised.

#### Plain standard English, no emoji

Every card the user can read — `start_text`, `group_start_text`, `help_text`,
`clone_menu_text`, `tools_text`, `chatbot_text` and `CLONE_GUIDE` — is plain
English with **no emoji and no markdown/HTML**. That is a hard rule, enforced by
`test_human.py` (each card is scanned for emoji and for `**`, `__`, `<b>`-style
tags). The only angle brackets left are the usage placeholders `/clone
<bot_token>` and `/delbot <bot_id>`, which are literal text: the client runs
with `ParseMode.DEFAULT`, so nothing is parsed locally.

#### No images at all

Every decorative picture is gone. `/start` and `/ping` are plain text, `/stats`
is plain text, and the `IMG` lists that used to sit behind them were deleted
outright (`Myra/modules/start.py`, `ping.py`, `stats.py`). What remains is
strictly functional, never decorative:

| Still sends media | Why |
|---|---|
| `Myra/modules/download.py` | the `/dl` command *downloads* the file the user asked for |
| `Myra/services/broadcast.py` | a broadcast sends exactly the media the owner attached |
| `Myra/ai/humanizer.py` | replies with a **sticker** when the trained database matched a sticker (a sticker is not an image, and it is part of the human-like chat behaviour) |

#### Back buttons

Every sub-panel has a way out, so a user can never get stuck:

* clone sub-screens (`_guide_markup`, `mybots_text`, the `/clone` success card)
  → **Back** (clone menu) + **Close**;
* every `/admin` sub-panel (`_ai_panel`, `_behaviour_panel`, `_stats_panel`,
  `_clones_panel`, `_broadcast_*`, the Group Request panels) → **Back** to the
  root panel, which itself carries **Refresh** + **Close**.

It also removes a real failure mode: Pyrogram builds message entities *locally*
before sending over MTProto, and a bad entity made Telegram answer with
`[400 ENTITY_BOUNDS_INVALID]`, which the command wrapper then showed to the user
as `ᴇʀʀᴏʀ ғᴏᴜɴᴅ`. Two safeguards remain on top of the plain text:

* `Myra/modules/clone.py → safe_reply()` / `_send()` retry once with
  `parse_mode=DISABLED` if an entity error ever occurs.
* `Myra/modules/callback.py → _edit()` does the same for card edits.

Implemented in `Myra/modules/helpers/inline.py` (`build_start_buttons`,
`build_clone_menu_buttons`) + `Myra/modules/callback.py` (`CLONE_MENU`,
`CLONE_GUIDE`, `CLONE_MYBOTS`).

---

## 7. Broadcast & AutoChat (time-to-time messaging)

### 7.1 `/admin` — one global panel for everything

`/admin` (private chat) opens an inline keyboard. It is **global**: the main bot
owner and the admins the owner added are the only ones who can open it, in *any*
bot, and every panel shows the **same shared data**. Whoever cloned a bot gets no
panel from it — cloning gives reach, not control.

```
📢 Broadcast       ⏰ AutoChat
🚪 Group Requests  📊 Stats
👥 Userbots        📁 Import Sessions
🧠 AI & Keys       🎛 Reply Behaviour
🧬 Clones          🧹 Clean DB
🔄 Refresh         ❌ Close
```

Every action edits the same message — no command typing needed — and every
sub-panel has a **Back** button (plus **Close**), so you can never get stuck.

### 7.2 Broadcast — from the bot *or* from the userbots

`/broadcast` walks through **3 steps**:

| Step | Choice |
| :--- | :--- |
| 1 · Sender | 🤖 **Main Bot** · 👥 **All Userbots** · 👤 any single connected userbot |
| 2 · Target | 💬 **Groups only** · ✉️ **DMs only** · 🌐 **Groups + DMs** |
| 3 · Content | Send the text — **or** reply to any message to broadcast its media + caption |

A live progress message shows `sent / failed` and a **🛑 Stop** button; the final
report breaks the failures down by reason (`blocked`, `privacy`, `no-permission`…).

Where do the targets come from?

| Sender | Groups | DMs |
| :--- | :--- | :--- |
| **Main Bot** | `chatsdb` (groups it is in) | `usersdb` (users who started it) |
| **Userbots** | `client.get_dialogs()` — every group that account is in | its private dialogs, **bots and our own accounts excluded** |

> ℹ️ With **All Userbots** the target list is merged and the sends are spread
> **round-robin** across the accounts, so no single account gets flooded.
> A userbot re-uploads media from disk (a bot `file_id` is not valid for a user
> account), and there is a ~1.4 s pause between user-account sends to stay
> under Telegram's anti-spam radar.

### 7.3 AutoChat — messages "time to time"

The bot and the userbots keep the groups alive on their **own separate timers**:

```
⏰ AutoChat — bot      : 🟢 ON · every 15m · 💬 Groups only
⏰ AutoChat — userbots : 🟢 ON · every 10m · 🌐 Groups + DMs
```

* The **message text is picked at random from the trained database**
  (`$sample` — instant even on millions of documents) and passed through the
  girl-voice filter, so it reads like a real person talking. If the DB is empty,
  a small built-in list of openers is used.
* Timing is **jittered ±40%** so the messages never look scheduled.
* Targets are visited **round-robin**, which guarantees every group is reached.
* A chat that fails 3× in a row is skipped (kept in memory only).
* `▶️ Send now` in the panel fires an immediate round for testing.

Panel controls (`/admin` → ⏰ AutoChat):

| Control | Options |
| :--- | :--- |
| **Toggle** | 🟢 Turn ON / 🔴 Turn OFF — separately for the bot and for the userbots |
| **Interval** | 1m · 5m · 15m · 30m · 1h · 3h · 6h · ⚙️ Custom (minutes) |
| **Target** | cycles 💬 Groups only → ✉️ DMs only → 🌐 Groups + DMs |
| **Send now** | one immediate round for the bot / for every userbot |

> ⚠️ **DM targets:** the bot can only DM users who already started it. A userbot's
> DM list comes from its own dialogs. Sending unsolicited DMs is the fastest way
> to get an account **banned** — keep `AUTOCHAT_UB_TARGETS=groups` unless you
> know what you are doing.

### 7.4 What blocks it from going wrong

* `ONLY_REAL_USERS` — the AutoChat/broadcast only ever targets real users and
  real groups: bots, our own userbot accounts and channels are filtered out.
* The scheduler lives in `Myra/services/autochat.py` and is started from
  `Myra/__main__.py`; `AUTOCHAT_ENABLED=false` disables it entirely.
* All settings are stored in `UserbotDB.settings`, so they survive a restart.

### 7.5 🚪 Group Request Controller (auto-approve join requests)

The second big section of the `/admin` panel. It handles Telegram's
**request-to-join** flow so a group owner never has to approve anyone by hand.

**Flow**

```
User taps "Ask to join"
        │
        ▼  ChatJoinRequest update
  is the controller ON?  ── no ──▶ ignored
        │ yes
        ▼
  is this group managed? ── no ──▶ ignored
        │ yes
        ▼
  auto-approve ON  +  must-join list EMPTY
        │ yes                          │ no
        ▼                              ▼
  approve_chat_join_request()     DM the user a welcome card
  instantly — no DM, no VERIFY    + one button per must-join chat
  (works even if the user          + ✅ VERIFY
   never started the bot)                 │
        │                                 ▼  user taps VERIFY (45 s cooldown)
        │                        get_chat_member() on every required chat
        │                                 │
        │                          ┌──────┴──────┐
        │                          ✅ all        ❌ someone skipped
        │                          │             └─▶ keep pending, show what's missing
        │                          ▼
        │                auto-approve ON  → approve + tell the group
        │                auto-approve OFF → mark verified + DM the group's admins
        ▼
  (both paths) best-effort "welcome" DM + `counters.approved` bumped
```

> **Auto-approve is instant when there is nothing to verify.** As soon as a
> managed group has an empty must-join list, the request is approved the moment
> it arrives — a user who never opened the bot can never receive the VERIFY
> card, so waiting for a tap would leave the request pending forever.

**Where it lives**

| Piece | File |
|---|---|
| Per-group config & counters | `Myra/database/joinreq.py` (`UserbotDB.joinreq_chats`) |
| Global toggles + welcome text | `Myra/database/settings.py` (`KEY_JOINREQ_ENABLED` / `_AUTO` / `_WELCOME`) |
| Flow, verification, approval | `Myra/services/joinreq.py` |
| `ChatJoinRequest` + `jr_v:` handlers | `Myra/modules/joinreq.py` |
| Panel, callbacks, text prompts | `Myra/modules/admin_panel.py` (`_jr_panel`, `_jr_chats_panel`, `_jr_req_panel`) |

**What the panel can do**

* 🚦 turn the whole controller on/off (per bot — scope-aware)
* 🤖 toggle auto-approve
* ➕ add a group (by `@username`, `t.me/…` link or `-100…` id)
* 🧪 build the must-join list per group (up to 10 channels/groups)
* ✨ set a custom DM welcome message (`{name}` · `{chat}` · `{count}`)
* 📊 live counters — pending / verified / approved — and a reset

**Requirements**

1. The bot must be an **admin** in the managed group, with *Invite users* +
   *Manage chat*. This is not optional: **Telegram only sends
   `ChatJoinRequest` updates to a bot that holds the `can_invite_users` right**,
   so without it the controller never fires at all. The panel checks this the
   moment you add a group and warns you if the right is missing.
2. The group must have *Join requests* switched on.
3. To verify membership of a required channel, the bot must be a member of that
   channel. If it cannot see a chat, that requirement is skipped (never reject an
   honest user because the bot is blind).

**Troubleshooting — "nothing happens"**

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| No DM, request stays pending | User never started the bot → Telegram refuses the DM | Ask them to `/start` the bot once, **or** clear the must-join list so auto-approve becomes instant |
| Nothing at all, even for a started user | Bot lacks *Invite users* in that group | Make it admin with that right (the panel warns on add) |
| `joinreq: cannot approve …` in `log.txt` | `ChatAdminRequired` — bot not admin | Add the right, then the user re-taps VERIFY |
| Group added but toggles look OFF | The controller master switch is off | `🚪 Group Requests` → 🚦 toggle |

Every approve / DM failure is now written to `log.txt` (`joinreq: …`), so the
feature can never fail silently again.

**Scope:** a clone bot only ever sees its own managed groups
(`{"scope": {"$in": ["main", None]}}` semantics apply).

---

## 8. Userbot ⇄ Bot sync

Both paths call the same function:

```python
# Bot           (Myra/modules/chatbot.py)
from Myra.ai.brain import get_ai_reply

# Userbot       (Myra/userbot/handlers.py)
from Myra.userbot.chatbot_engine import get_chatbot_response  # → brain.get_ai_reply
```

* Same collection `Word.WordDb`
* Same cleaner, same key rotator, same Gemini singleton
* Same auto-train → a reply learned by the bot is instantly available to every userbot
* Same humanizer → the same typing delay, reply chance and bubble splitting
* Per-account reply delay **and reply chance** override the global `/speed` / `/chance` values
  (dashboard → *Reply Behaviour*)

### Read receipts — the userbot marks the chat as *seen* first

A real person opens the chat first: your message turns into **✓✓** and only then
does the typing indicator appear. The userbot now does exactly that:

```
incoming message
      │
      ▼  decide to answer (reply chance roll)
  read_chat_history(chat_id)   ← the "seen ✓✓"
      │
      ▼  reading pause (human)
  "typing…"  →  reply sent
```

* Implemented once, in `Myra/ai/humanizer.py::mark_seen`, called by
  `respond_like_human()` **before** the reading pause and the typing indicator.
* **User accounts only.** Bots have no read receipts, so `mark_seen` is a
  deliberate no-op for the main bot and its clones — only userbots mark chats as
  read.
* Best effort: a failed read (FloodWait, etc.) is logged at debug level and never
  blocks the reply.
* When the reply-chance roll says "don't answer", nothing is sent and nothing is
  marked — exactly like a person who never opened the chat.

### `/useraccounts` → ⚡ Reply Behaviour

Each userbot has its own screen:

| Control | Options |
| :--- | :--- |
| **Speed** | ⚡ Fast (0s) · ⏱ Medium (5s) · 🐢 Slow (10s) · ⚙️ Custom (ms) — overrides `/speed` for that account |
| **Group Reply** | 💬 *All messages* (reply to plain `hy`/`hello` like a real member) or 👤 *Only when tagged* |
| **Reply Chance** | 🎲 Global · 100% · 85% · 70% · 50% · 30% · 15% — how often it answers |

---

## 9. Integrating into your existing repo

1. Copy the `Myra/ai/` folder.
2. Copy `Myra/database/training.py`, `Myra/database/settings.py` and
   `Myra/database/autochat.py`.
3. Copy the `Myra/services/` folder (`broadcast.py`, `autochat.py`).
4. Copy `Myra/modules/ai_admin.py` and `Myra/modules/admin_panel.py`.
5. Replace `Myra/userbot/chatbot_engine.py`, `Myra/modules/chatbot.py`,
   `Myra/modules/broadcast.py`, `Myra/modules/stats.py`,
   `Myra/modules/helpers/read.py` and `Myra/userbot/handlers.py`.
6. Start the AutoChat service from your entry point:

   ```python
   from Myra.services.autochat import autochat_service
   await autochat_service.start()   # after idle() … stop() on shutdown
   ```
7. Append the AI block to `config.py`, add `google-genai` to
   `requirements.txt`, and add the new variables to `.env`.
8. Restart — indexes are created automatically, and any pre-existing `WordDb`
   data is picked up immediately.

> Optional speed-up: `pip install rapidfuzz` — `training.similarity()` detects it
> automatically for much faster fuzzy matching. It is **not** required.

---

## 10. Data migration (old cluster → new cluster)

A ready-made tool copies **only the chat/training data** (`Word.WordDb`) between
two MongoDB clusters, applying the exact same anti-spam rules as the live bot:

```bash
python tools/migrate_training_db.py \
  --source "mongodb+srv://user:pass@OLD-cluster.mongodb.net/" \
  --target "mongodb+srv://user:pass@NEW-cluster.mongodb.net/"
```

What it does:

* drops entries containing links, `t.me`, emails, @usernames or promo words
* drops empty / junk triggers (`none`, `null`, ...)
* normalizes + truncates triggers (200 chars) and cleans replies (300 chars)
* de-duplicates `(trigger, reply)` pairs
* **preserves the original `_id`**, so the migration is idempotent — a re-run
  never duplicates data
* creates the `word` and `tokens` indexes afterwards
* the source cluster is opened **read-only**

Useful flags: `--dry-run` (count only), `--limit 5000` (quick test),
`--drop` (wipe the target first), `--no-indexes`, `--max-word-len`,
`--max-text-len`, `--verbose` (per-batch timings), `--full` (store the extra
metadata too).

Realistic throughput is **~350-400 docs/s** (limited by the target cluster's
write latency), so a 1.9 M document collection takes roughly **80-90 minutes**.
The tool prints progress every 100 000 documents.

> **⚠️ Free-tier storage limit (512 MB) — important.** Storing the extra
> metadata (`tokens`, `trigger_raw`, timestamps) roughly **doubled** the footprint
> of a 1.9 M document collection, which filled the Atlas free tier at ~1.28 M
> docs (`write failed: you are over your space quota`). The tool therefore runs
> in **`--lean` mode by default**: only `_id`, `word`, `text`, `check` are
> copied — the same schema the old cluster used, so the data fits comfortably.
> The `tokens` index is also skipped, and the bot detects that automatically and
> searches by `word` + prefix instead.
>
> If the quota is ever hit, the tool **stops cleanly** with instructions instead
> of spinning. Since the original `_id` is preserved, simply re-running it
> resumes where it left off. To start over, delete the target collection
> (or `--drop`).

> **Storage tip (Atlas free tier = 512 MB).** A 1.9 M document collection with
> the full-text index can exceed the free tier. The migration tool therefore
> runs in `--lean` mode (only `_id/word/text/check`) and creates only `word`;
> the optional `text` and `tokens` indexes are off by default
> (`AI_CREATE_TEXT_INDEX=false`, `AI_CREATE_TOKENS_INDEX=false`). Turn them on
> only if you have room.

### Only the chat data is migrated (by *this* tool)

`tools/migrate_training_db.py` copies **only** `Word.WordDb` — the reply database.
The bot's own data is deliberately left alone, and since the two-cluster split it
has its own home and its own migration tool (next section).

### The two-cluster split

```text
MONGO_URL        ──▶  Word            ──▶  WordDb        (chat / training brain)
BOT_MONGO_URL    ──▶  Anonymous       ──▶  usersdb, chatsdb
                      UserbotDB       ──▶  settings, ai_keys, bot_admins,
                                             joinreq_chats, accounts
                      SessionDB       ──▶  sessions
                      BotDB           ──▶  clones
                      MYRADb          ──▶  MYRA            (chatbot on/off per chat)
```

* `MONGO_URL` is written by **nothing but the chatbot training** (`training.py`).
* `BOT_MONGO_URL` holds every other collection. It is resolved in
  `Myra/__init__.py`:

  | Variable | Meaning |
  | :--- | :--- |
  | `MONGO_URL` | chat / training cluster |
  | `BOT_MONGO_URL` | bot-data cluster (aliases: `BOT_DB_URL`, `BOT_MONGO_URI`) |

  When `BOT_MONGO_URL` is empty (or equal to `MONGO_URL`) both handles point at
  the **same client**, so an existing single-cluster deployment keeps working
  with no change at all.

#### Moving the bot data to the second cluster

```bash
# 1. (optional) see what would be copied
python tools/migrate_bot_db.py --source "$OLD_MONGO_URL" --target "$BOT_MONGO_URL" --dry-run

# 2. copy Anonymous + UserbotDB + SessionDB + BotDB + MYRADb
python tools/migrate_bot_db.py --source "$OLD_MONGO_URL" --target "$BOT_MONGO_URL"

# 3. point the bot at the new cluster
#    .env →  BOT_MONGO_URL=...
# 4. restart and check the log
python -m Myra
```

The tool is **idempotent** — every document keeps its original `_id` and is
upserted, so an interrupted run can simply be repeated. Indexes are recreated.
`--drop` empties each target collection first if you want a clean slate.

Verification after the switch:

| Check | Expected |
| :--- | :--- |
| `UserbotDB.settings` on the new cluster | the same values as before |
| `/aikeys` in the bot | the same keys as before |
| `/admin` → panels | identical data (the panel is global anyway) |
| `Word.WordDb` | **still only on the old cluster** |
| Log at startup | `mongo` = chat, `bot_mongo` = bot cluster, no warnings |

> **Tip.** You can split the data without any downtime in the reverse direction
> too: keep everything on one cluster and only set `BOT_MONGO_URL` later — the
> bot picks it up on the next restart, and the migration tool can be pointed at
> any pair of clusters.

### Migration result (lean run, old cluster → new cluster)

| Metric | Value |
| :--- | :--- |
| Source documents scanned | **1,915,185** |
| Documents kept | **1,752,377** (62,620 of them stickers) |
| Inserted | 1,735,485 |
| Skipped — empty | 94,353 |
| Skipped — reply-only / junk / dirty | 49,982 |
| Duplicates | 35,365 |
| Write failures | **0** |
| Elapsed | ~91 min (~348 docs/s) |
| Collection size | **107.44 MB** data · 133.14 MB indexes · **240.58 MB** total |
| Cluster total | **385.26 MB / 512 MB** used |
| Indexes | `_id_`, `word_1` (no `tokens` index in lean mode) |
| Dirty rows found in a 60 000-row sample | **0** |

Live similarity search after the migration (through the brain):

```
'hello'        -> [1.0] 'Ha bolo'
'hi'           -> [1.0] 'Hiii'
'good morning' -> [1.0] '𝐆𝐨𝐨𝐝 𝐌𝐨𝐫𝐧𝐢𝐧𝐠💞💞'
'kaise ho'     -> [1.0] 'Mast aap btao'
'hello bhai'   -> [1.0] 'Kese ho bhai'
'thank you'    -> [1.0] 'Prty ke liye paise account main dlwa'
```

> The new cluster also still contains a `sample_mflix` database (~7 MB of MongoDB's
> own demo data, not yours). It is safe to drop to free space.

---

## 11. Safety notes

* **Group spam:** `AI_GROUP_FALLBACK_PLAIN=false` (default) keeps Gemini out of
  plain group chatter — those are answered from the trained database only, so a
  busy group never exhausts your quota. Set `USERBOT_GROUP_REPLY_ALL=false` (or
  the per-userbot toggle) if you only want userbots to answer when tagged.
* **Anti-poisoning:** `/clean_db` and the cleaner guarantee that no link or
  promotional text can ever be stored, so the model can never learn to spam.
* **Loop prevention:** userbots ignore each other's messages and the bot ignores
  bot messages, so two instances can never ping-pong forever.
* **Cost control:** the DB-first search means the API is called only for
  genuinely new messages, and every new answer is cached in MongoDB forever.
  Rotate across as many keys as you like with `/addkey`.
* **Broadcast / AutoChat bans:** mass DMs are the #1 cause of Telegram account
  bans. Use 💬 *Groups only* for AutoChat, keep the ~1.4 s gap between userbot
  sends, and never point a userbot at a DM list you did not build organically.
  The **🛑 Stop** button aborts a running broadcast immediately.
* **AutoChat safety rails:** messages are jittered ±40 %, only *served* chats are
  targeted, and a chat that errors three times in a row is dropped from the
  rotation — so the fleet degrades quietly instead of spamming a dead chat.
* **Clones share this process** (`CLONE_ENABLED`, `MAX_CLONES`, default 20). A
  clone cannot see the main bot's admins, userbots, groups or settings, and the
  main bot cannot see the clone's — every document is tagged with a `scope` and
  every handler runs with the scope of the client that received the update.
  Restrict who may clone with `CLONE_ALLOWED_USERS=111,222` if you don't want it
  open to everybody.
* **One Telegram account = one bot.** Adding a userbot that already belongs to
  another bot (main or clone) is refused, so the same account is never driven by
  two bots at once.
