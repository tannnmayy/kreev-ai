# Ala

Ala (అల, "wave") is a voice shopping assistant for a coastal Andhra market. It speaks English and Telugu and switches when you
do. You can change your mind halfway through a sentence, talk over it, or say "the second one", and the cart stays right. It
never places an order until it has read the order back and heard a clear yes.

Built for KREEV's Voice AI & Integrations assignment, at a cost of ₹0.

- **Live demo:** _link to be added after deployment_
- **How it's built and why:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **What has and hasn't been proven:** [docs/EVALUATION.md](docs/EVALUATION.md)

## Reproduce it with one command

You need Docker and nothing else: no account, no key.

```bash
git clone https://github.com/tannnmayy/kreev-ai.git ala && cd ala && docker compose up --build
```

The first run takes about 5 minutes on a fast connection and about 6 GB of disk. Docker starts a LiveKit server, the agent and the web page, then
runs every test, the real-life deck and a real call through the running stack. It ends with:

```
Ala reproduced on this machine: YES
```

Then open <http://localhost:8787> and choose **Type instead**.

| What you have | What you get |
| --- | --- |
| No keys | The whole shop through a real LiveKit room: typed English and Telugu, cart, swaps, budgets, read-back, orders. Replies come from the rule mind, so open-ended questions get a short honest answer. |
| A Gemini or Groq key | The fast and slow minds: open-ended advice, meal planning and comparisons. |
| A Sarvam key as well | Voice in both directions, barge-in and acoustic reading. |

To add keys, copy `.env.example` to `.env.local`, fill in what you have, and run `docker compose up` again. Every key is free
and none needs a card:

| Service | What it's for | Where to get it |
| --- | --- | --- |
| Google AI Studio | Fast and slow minds | <https://aistudio.google.com/apikey> |
| Groq | Backup minds | <https://console.groq.com/keys> |
| Sarvam | Telugu and English voice, and listening (₹100 free credit) | <https://dashboard.sarvam.ai> |
| Gladia | Second listening service (10 hours a month) | <https://app.gladia.io> |

Check them with a real request to each service:

```bash
docker compose run --rm check python -m ala.bench.bakeoff keys
```

## Run it without Docker

You need [uv](https://docs.astral.sh/uv/) and the [LiveKit server](https://docs.livekit.io/home/self-hosting/local/) binary.

```bash
uv sync
```

```bash
livekit-server --dev --bind 127.0.0.1
```

```bash
uv run python -m ala.voice.agent start
```

```bash
uv run python -m ala.web.server
```

Run each of the last three in its own terminal, then open <http://localhost:8787>.

## Test it

```bash
uv run pytest tests -q
```

```bash
uv run python -m ala.bench.deck
```

```bash
uv run python -m ala.bench.smoke
```

- `pytest` runs the unit tests and needs no keys or network.
- `ala.bench.deck` plays the real-life deck: weird situations from `scenarios/deck.yaml`, checked against the final cart. Cards
  that need a person, a phone call or a model are listed and skipped, never counted as passes.
- `ala.bench.smoke` joins a real room as a customer, with the agent and web server running.
- `ala.bench.voicecheck` streams real audio into a call, with the agent started with `ALA_STANDIN=1`, and checks barge-in,
  backchannels and the read-back rule. It needs no keys, but it needs Windows' speech voices or your own WAV files.
- `ala.bench.simulate` runs KREEV-Bench, 42 simulated customers with hidden goals. It needs a model key.
- `ala.bench.bakeoff` compares listening services and models and measures each turn-taking layer on real recordings. See
  [clips/README.md](clips/README.md).

## Deploy it for free

The agent runs on LiveKit Cloud in Mumbai. The page and token functions run on Vercel. Customer memory lives in Neon Postgres.
All three stop at their free limits and never bill.

1. Create a LiveKit Cloud project on the Build plan, a Neon database and a Vercel project. Put `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
   `LIVEKIT_API_SECRET`, `DATABASE_URL` and your model and voice keys in `.env.local`.

2. Deploy the agent. Run this from the repository root, where the `Dockerfile` is:

   ```bash
   lk agent create --region ap-south --secrets-file .env.local
   ```

3. In Vercel, set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and `ALA_INVITE_CODE`, then deploy:

   ```bash
   vercel --prod
   ```

4. Point a free uptime monitor at `/api/health`, and share the link as `https://<your-site>/?invite=<code>`.

The invite code stops strangers using up the free quota. On the Build plan the agent sleeps when no one is calling, so the first
call after a quiet spell takes 10 to 20 seconds. The page starts waking it as soon as it opens.

## What's where

```
ala/language/     numbers in both languages, cue words, language tracking, speakable text
ala/shop/         the catalog, cart, orders and every bilingual reply; no model touches this state directly
ala/minds/        rules first, then the fast and slow minds, through a router that watches free quotas
ala/perception/   pitch, loudness and pace; end-of-turn; what overlapping speech means
ala/coordinator.py  decides when a change commits, and that each commits once
ala/voice/        the LiveKit agent
ala/web/, api/    room tokens, for local runs and for Vercel
web/              the page
scenarios/        the real-life deck and KREEV-Bench customers
ala/bench/        everything that measures the above
```

## Privacy

Free AI services may keep what they receive, so the page tells people not to share personal details. A browser gets a random
session ID so the cart survives a refresh. Ala remembers past orders across visits only if someone gives a first name, and saying
"forget me" removes them.
