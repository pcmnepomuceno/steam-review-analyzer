# Steam Review Sentiment + Aspect Analyzer

**Live demo:** https://steam-review-analyzer-wheat.vercel.app/ · **Demo clip:** <!-- TODO: demo clip URL --> _link coming soon_

Steam shows one number per game: "Very Positive", "Mixed". To learn *why* players feel that way, you still have to read hundreds of reviews. This app takes any Steam appid, fetches and caches the game's English reviews in Postgres, and breaks sentiment down by aspect: **performance, price, bugs, story, gameplay**. The result reads like "bugs: 80% negative, story: 90% positive" rather than one blended score. It is meant for indie developers triaging post-launch feedback, publishers weighing a price change, and players comparing two similarly rated games.

Both ML components are trained or hand-built and evaluated here: a TF-IDF + logistic regression sentiment classifier and an embedding-similarity aspect tagger. Neither is an LLM API call. The results below are the measured numbers, including where the models are weak.

**The live demo serves five games analyzed ahead of time** (Portal 2, Hades, Starfield, PAYDAY 3, Cities: Skylines II). Any other appid gets a page listing those five. Run locally, the full pipeline works for any appid: fetch, cache, analyze, dashboard. The demo is limited by its host's CPU, not by missing features; see [Why the demo serves a fixed set of games](#why-the-demo-serves-a-fixed-set-of-games).

## Screenshots

![Homepage with the five analyzed games](docs/img/home.png)

![Steam search typeahead](docs/img/search.png)

![Per-game dashboard (Hades): aspect chart, verdict and example quotes](docs/img/dashboard.png)

## How it works

```
Steam API ──> ingestion ──> Postgres ──> sentiment + aspect ML ──> aggregation ──> FastAPI ──> Next.js dashboard
            (on demand,     (reviews)    (background task,         (per aspect,     /aspects     (polls until
             cached)                      results stored)           per day)         /reviews      ready)
```

1. **Ingestion.** The first request for an appid fetches the game's ~1000 most recent English reviews from Steam's public review API (cursor pagination, retry on 429/5xx) and caches them. A Postgres advisory lock makes concurrent first requests fetch only once. Games with fewer than 200 usable reviews are rejected rather than analyzed on too little data.
2. **Sentiment.** TF-IDF + logistic regression, trained on Steam's own `voted_up` flag as a weak-supervision label, so no manual sentiment labeling is needed. It is trained once on five cached games pooled (`class_weight="balanced"`), and one model serves any appid.
3. **Aspects.** Each review is split into sentence and clause units with a rule-based splitter, which also cuts at "but / however / and" because reviews often skip punctuation. Each unit is embedded with `all-MiniLM-L6-v2` on CPU, run as its ONNX export on ONNX Runtime rather than torch, which keeps the API under 512 MB. A unit gets the aspect whose hand-written anchor phrases it is most similar to (max cosine), or `none` below a similarity threshold of 0.40. Anchors were chosen over BERTopic or a fine-tuned classifier because they are explainable and cheap to tune.
4. **Aggregation.** A background task (14-19 min per game in the production image limited to 0.1 CPU and 512 MB, as on Render's free tier; 11-35 s on a 32-core desktop) writes one row per unit and one predicted sentiment per review. `/aspects` then counts distinct *reviews*, not sentences, per aspect and sentiment, plus a daily trend. While a game is still processing, the API answers `202 {status: "processing"}` instead of empty counts, so "not computed yet" never looks like "no complaints".
5. **Dashboard.** A Next.js page polls `/aspects` until results are ready and then leads with the share of reviewers who recommend the game on Steam against the lowest-rated topic, followed by a row per topic (each linking to its reviews), the daily sentiment trend and a review list you can filter by aspect, sentiment and day and sort by helpfulness, playtime or date. Each review marks the sentences its topics were matched on, and flags it when the model's reading disagrees with the reviewer's thumb. The homepage lists every analyzed game with its five topic shares in aligned columns. Each backend state has its own screen.

Stack: FastAPI, SQLAlchemy (async) + Alembic, Postgres, scikit-learn, ONNX Runtime (for the sentence-transformers model), Next.js 16 + Recharts.

## Results

### Sentiment classifier

Held-out test set of 1000 reviews from 5 games, 78% positive (`docs/eval/sentiment_report.txt`). Steam reviews skew positive, so a model that always answers "positive" already gets 78% accuracy. The metric that matters is F1 on the minority (negative) class:

| | Model | Majority baseline |
|---|---|---|
| **Negative-class F1** | **0.718** | 0.000 |
| Negative precision / recall | 0.643 / 0.813 | 0.000 / 0.000 |
| Accuracy | 0.860 | 0.781 |
| Macro F1 | 0.812 | 0.439 |

On the one-sided games it is weak. Hades and Portal 2 are ~98% positive, and the model's negative F1 there is 0.200 and 0.429. On both, its accuracy is *below* the always-positive baseline's (0.918 vs 0.980, 0.958 vs 0.979).

### Aspect tagger

The tagger was scored on 140 sentence units that I hand-labeled blind, without seeing the model's predictions (`docs/eval/aspect_report.txt`, labeling rules in `docs/eval/ASPECT_LABELING.md`). The sample deliberately oversamples the hard cases near the threshold, so the unweighted numbers understate typical accuracy. The population-weighted row re-weights units to the full set of cached sentences.

| | Original anchors | After anchor tuning | Majority baseline (always `none`) |
|---|---|---|---|
| Macro F1 | 0.623 | **0.658** | 0.082 |
| Accuracy (95% CI) | 0.579 (0.496-0.657) | 0.621 (0.539-0.698) | 0.329 |
| Population-weighted macro F1 | 0.657 | 0.699 | 0.070 |

**Caveat: the "after tuning" numbers are optimistic.** The anchors were tuned against the same 140 units that score them, so this is not an independent measurement, and the confidence intervals before and after tuning overlap. The tuning added three gameplay anchors and reworded two story anchors, after the first eval showed 15 gold-gameplay units falling to `none`. That moved gameplay F1 from 0.467 to 0.618 and story F1 from 0.556 to 0.606. The other aspects did not change.

Per-aspect F1 after tuning: bugs 0.750, performance 0.714, price 0.714, gameplay 0.618, story 0.606, `none` 0.545. The threshold sweep is flat between 0.34 and 0.42, so the threshold stayed at 0.40 rather than being fitted to the sample.

### Known limitations

These are known failure modes, measured or observed and left unfixed, for the reasons given:

- **Mod and player-created content counts as "gameplay".** Unit 82, "and all the player-created content, there's a LOT of game here", is gold `none`, because it is about community content, not the amount of official content. It is predicted `gameplay`, because the anchor "there isn't enough content" matches the general *content* topic. Embedding similarity can't draw the official-vs-community line the labeling rules draw. It is not fixed: one unit in 140, and tuning against it would overfit the sample.
- **Generic opinion sentences get tagged with an aspect.** "I love this game so much" → gameplay, "super worth it" → price, "the sequel has been a huge disappointment" → story. In the sample, 17 gold-`none` units got an aspect, which caps `none` precision near 0.51. The fix is explicit `none` anchors, a mechanism change deferred as a stretch goal.
- **No graphics/audio/art aspect.** These count as `none` by design, and the eval tracks them in label notes.
- **Sentiment is weak on overwhelmingly positive games.** See the Hades and Portal 2 numbers above. There are too few negatives in those games for the model to learn their complaints.
- **Results describe recent opinion only.** Each game is limited to its ~1000 most recent English reviews and is never refreshed. For a popular game that covers roughly 1-3 months, which also bounds the trend chart. The dashboard says so under the game name.
- **An interrupted run needs a manual retry.** Processing runs inside the API process. If the process running it dies mid-run (out of memory, redeploy, shutdown, a Ctrl-C'd CLI run), that game is marked `failed` with the reason, at the next API startup or by the next request for it, and the dashboard shows the failed screen. A live run holds a Postgres advisory lock for its whole duration, and only games whose lock nobody holds are failed, so a `--force` run from a laptop or another instance's run is not failed under it. It is not retried on its own, so a run that crashed the host can't crash it again. To retry, run `python scripts/process_reviews.py --force APPID` from a local checkout, with `DATABASE_URL` pointing at the deployed database.
- **With on-demand processing on, Render's free tier can't reliably finish a first analysis.** This is why the deployed demo turns it off (see below); it still applies to any host this slow that turns it on. At 0.1 CPU one game takes 14-19 min (see "Processing is not batched"), so every first visit reaches the dashboard's timeout screen, not only queued ones; "check again" shows the result once it is done. Runs are serialized within one process, because the memory figures below were measured for a single run, so a game requested during another run waits its turn. In the emulated test, two games requested together finished after 18.8 and 32.5 min; the second waited 18.8 min before its own 13.7 min run. Render also spins a free instance down after 15 minutes without inbound requests, and a background run does not count as one. Once the dashboard stops polling, a run still going about 20 minutes after the last request is killed, and the next startup marks it `failed`. A single game's run fits in that window; a queued one does not unless something keeps requesting. Batching across reviews (below) is the real fix and is not attempted yet; a longer poll only keeps the instance awake, and parallel runs would break the memory bound.
- **Processing is not batched.** Each review gets its own predict and encode call. Batching across reviews would make the run several times faster. It takes 11-35 s per game on a 32-core desktop with the encoder capped at 8 threads, and 14-19 min in the production image limited to 0.1 CPU and 512 MB (`docker run --cpus=0.1 --memory=512m`), which emulates Render's free tier on the dev desktop; it has not been timed on Render itself. The two measured games took 18.8 min (9,326 units, including about 8 s of model loading) and 13.7 min (6,154 units), with the encoder on 1 thread.
- **Memory headroom is about 20%.** Render's free tier allows 512 MB. Measured inside the production image with `scripts/check_memory.py`, processing the 1000 cached reviews of each of the 5 test games peaks at 354-411 MB, so the worst real game uses 80% of the limit. A synthetic worst case, where every encoder batch is 8 units at the full 256 tokens, peaks at 442 MB (86%). Every run also finished under a hard 512 MB container limit. An end-to-end run of the rebuilt image (container limited to 512 MB and 0.1 CPU, two games requested at once over HTTP and processed one after the other in the same API process) peaked at 400 MB by the container's cgroup `memory.peak`, the figure the memory limit enforces, with no OOM events; the process's own peak RSS was 427 MB, because it also counts shared file-backed pages. Loading the app and both models alone takes about 345 MB; the encoder (ONNX Runtime + MiniLM) is 180 MB of that. The first figures published here (358 MB, then 351 MB, about 70%) were wrong. They came from a synthetic review whose units were about 20 tokens, while real reviews contain units of up to 256 tokens, and encoder memory grows with batch size x sequence length. At the old batch size of 32, one real game peaked at 640 MB; the batch size is now 8. With torch + sentence-transformers the process was already at 564 MB on that same short synthetic text. ONNX gives the same scores as torch (max difference 5e-7 over 10,265 units, no label changes) at the same speed.

### Why the demo serves a fixed set of games

The deployed API runs with `ALLOW_ON_DEMAND_PROCESSING=false` (set in `render.yaml`). It serves only the games that were analyzed ahead of time and loaded into its database. For any other appid it answers 403 with the list of those games, before contacting Steam. The dashboard shows that list as links.

The reason is the host's CPU. The rebuilt production image was run with the free tier's limits (0.1 CPU, 512 MB) on the dev desktop, and two games were requested over HTTP. The first took 18.8 minutes. The second waited 18.8 minutes behind it, then ran for 13.7 minutes, and was done 32.5 minutes after it was requested. A single game takes 14-19 minutes at 0.1 CPU. Render also spins a free instance down after 15 minutes without incoming requests, and a background run doesn't count as one. So a visitor who closes the tab can have their run killed partway, and a queued run needs more than 30 minutes of uninterrupted uptime. That is not a demo a visitor can rely on.

The same test shows that the on-demand pipeline works in the production image. Both games went from `pending` to `done` through the HTTP API: lock, claim, encoder on 1 thread from the CPU quota, model loaded from the baked revision, results written. Memory peaked at 400 MB against a 512 MB limit. Locally, `ALLOW_ON_DEMAND_PROCESSING` defaults to `true`, and a new game takes 11-35 seconds on a desktop CPU. Batching the encoder across reviews would cut the per-game time several times over; it is written down as not attempted (docs/DECISIONS.md), not in progress.

## Run it locally

### Just use it (no coding tools needed)

The only thing to install is [Docker Desktop](https://www.docker.com/products/docker-desktop/). Open it once and wait until it says it is running.

**What has been tested.** The app itself (Docker Compose stack, on-demand analysis, memory and build figures below) was tested on Linux (Ubuntu on WSL2). The double-click launchers were not all tested:

| Platform | Launcher | Status |
|---|---|---|
| Linux | `launcher/start.sh` in a terminal | Tested: stack starts, opens, analyzes a new game |
| Linux | `launcher/Start.desktop` (double-click) | Built, **not tested** (no desktop session was available) |
| Windows | `launcher/Start.bat`, `Stop.bat` | Partly tested: run through `cmd.exe` with Docker Desktop from a repo on a WSL path, the stack started and `Stop.bat` stopped it. **Not tested** by double-clicking in Explorer, from a ZIP on a normal Windows drive, or past SmartScreen |
| macOS | `launcher/Start.command` | Built, **not tested** |

If a launcher fails on Windows or macOS, that is a known gap, not something you did wrong. Every platform has the same fallback, which only needs Docker Desktop and a terminal opened in the project folder: run `docker compose up -d --build --wait`, then open http://localhost:3000.

1. Download this repo (green **Code** button on GitHub, then **Download ZIP**, then unzip it) or `git clone` it.
2. Open the `launcher` folder and double-click `Start.bat` (Windows), `Start.command` (macOS) or `Start.desktop` (Linux; first time: right-click, **Allow Launching**, and on some desktops mark it as trusted; or run `launcher/start.sh` in a terminal).
3. Wait for the first start. It builds the app, then your browser opens on http://localhost:3000. Later starts take seconds.
4. Search for any Steam game, or paste its appid (the number in its store URL), and open it. The app fetches its reviews from Steam and analyzes them while you watch. This is the full app, not the restricted demo (see above). The page shows progress and updates itself.

Games you have analyzed are kept in a Docker volume and are still there next time. To stop the app, double-click `Stop.bat` / `Stop.command` (or `launcher/stop.sh`), or press stop in Docker Desktop. Nothing runs when it is stopped.

**macOS blocks the launcher the first time.** A ZIP downloaded in a browser marks every file in it as from the internet, and macOS (Gatekeeper) refuses to run an unsigned script: "Start.command cannot be opened because it is from an unidentified developer" or "Apple could not verify...". There is no way around this without Terminal other than these steps (signing the launcher needs a paid Apple developer account, which this project doesn't have):
- macOS 15 (Sequoia) and later: double-click `Start.command` once and dismiss the warning. Open **System Settings > Privacy & Security**, scroll to **Security**, click **Open Anyway** next to the message about `Start.command`, and enter your password. Double-click it again and choose **Open**. Right-click > Open no longer works on these versions.
- macOS 14 and earlier: right-click `Start.command`, choose **Open**, then **Open** in the dialog. If that fails, use the Settings steps above.
- Avoiding it: a repo fetched with `git clone` or GitHub Desktop (not the ZIP) has no "from the internet" mark, so no warning appears. A ZIP can also lose the file's "run" permission ("you do not have permission to open it"); cloning avoids that too.

**Windows may show "Windows protected your PC"** (SmartScreen) for `Start.bat` from a ZIP: click **More info**, then **Run anyway**. Or right-click the ZIP > Properties > **Unblock** before unzipping.

**First start: time and disk.** Measured from a clean state on a 32-core Linux desktop with a fast connection: removing this project's volumes and images and building with no layer cache took **159 s**, and starting the three containers **21 s** (**3 min** in all). Docker's base images `python:3.12-slim` and `postgres:16` were already on that machine and are not in the figure (`node:22-slim` was downloaded during the build). Expect longer on a slower connection or CPU; I haven't measured one. It uses about **3.1 GB** for the images (dashboard 1.4 GB, API 1.0 GB, Postgres 0.6 GB) plus about **1.9 GB** of Docker build cache, which `docker builder prune` frees, and the database volume (about 100 MB with seven games analyzed).

#### If the analysis fails: Docker memory

The stack's three containers peaked at about **510 MB together** while analyzing Starfield, the largest of the sample games (9,005 sentence units), with the dashboard being loaded: the API/analysis container 350 MB, the dashboard 97 MB, Postgres 61 MB. The API container is the one that decides it. I lowered its memory limit step by step and ran a real analysis each time (in a container limited to 4 CPUs): **352 MB finishes, 345 MB is killed by the out-of-memory killer** partway through (repeated; 340, 335, 330 and 300 MB were killed too). So the analysis needs at least ~350 MB in the container. What I did *not* measure is the extra memory Docker Desktop's own virtual machine uses on Windows and macOS, so 2 GB for Docker Desktop (Settings > Resources > Memory) is my recommendation with that margin, not a measured floor. If the API container is killed, Docker restarts it and the game that was being analyzed shows "Analysis failed — Something went wrong — try again" with a **Try again** button (it never restarts by itself, so a game that keeps running out of memory can't loop). The screen can't say "out of memory" here: a killed process leaves the same stored reason as closing the app or restarting Docker mid-analysis, so the app doesn't guess. If it keeps happening on a game, raise the memory limit as below. Raise Docker Desktop's memory first, then click it. The button exists only in the local app: the deployed demo has it off.

**Speed.** The analysis uses the CPU cores Docker Desktop is given (up to 8). Starfield took 52-57 s from request to done, with 4 CPUs or unlimited. Smaller games are quicker, and a slower laptop will take longer.

If a port is taken ("port is already allocated" for 3000, 8000 or 5433), close the program using it (often another local web server or Postgres) and start again. To update after pulling a new version, just start it again: it rebuilds what changed.

### Developer setup

Requires Git, Docker (running), [uv](https://docs.astral.sh/uv/) and Node 22+, on Linux, macOS or WSL (the script is bash). This runs the API and dashboard on the host with hot reload instead of in containers. A trained sentiment model is committed (`backend/models_store/sentiment.joblib`), so no training is needed to start. Don't run it together with the launcher: both use ports 3000 and 8000.

```bash
git clone https://github.com/pmNepomuceno/steam-review-analyzer.git
cd steam-review-analyzer
./setup.sh
```

When it finishes, open http://localhost:3000. It prints that `ALLOW_ON_DEMAND_PROCESSING` is enabled: search any Steam game or enter any appid and it is fetched and analyzed live (11-35 s on a desktop CPU), unlike the five-game demo.

The script runs these steps, printing each with a rough time, and stops at the first failure naming the step:

1. Checks Docker (and its daemon), uv, Node 22+, npm and curl.
2. Creates `.env` from `.env.example` if there is none. An existing `.env` is never touched.
3. Starts Postgres (`docker compose up -d --wait db`, host port 5433).
4. Creates `backend/.venv` (Python 3.12), installs `requirements/dev.txt`, runs `alembic upgrade head`.
5. Fetches and caches a seed game's reviews (`scripts/ingest_game.py`). Default Hades (1145360); pass another appid (`./setup.sh 620`) or set `SEED_APPID`. A bad appid, too few English reviews or a Steam outage prints the reason and stops.
6. Trains the sentiment model only if `models_store/sentiment.joblib` is missing. It is committed, so normally this is skipped: retraining on one game would replace the evaluated model with a worse one.
7. Analyzes the seed game (`scripts/process_reviews.py`). The first run downloads the ~90 MB embedding model.
8. Installs the dashboard's npm dependencies.
9. Starts the API (:8000) and the dashboard (:3000) in the background, with logs in `.dev/`.

A cold run takes a few minutes, mostly downloads. Re-running skips what is done and takes about ten seconds. Ports 5433, 8000 and 3000 must be free (or held by this script's own earlier run); otherwise it names the busy port. `./setup.sh stop` stops the API and dashboard; Postgres stays up (`docker compose down` stops it).

### Manual setup

The same thing by hand:

```bash
cp .env.example .env               # local defaults work as-is
docker compose up -d db            # Postgres on localhost:5433

cd backend
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements/dev.txt
alembic upgrade head
uvicorn src.main:app --reload      # API on http://localhost:8000
```

Dashboard, in a second terminal:

```bash
cd frontend
npm install
npm run dev                        # http://localhost:3000, proxies /api/* to the API on :8000
```

Open http://localhost:3000 and enter an appid (e.g. `1145360`, Hades). The first visit ingests the reviews (~10 s) and then analyzes them (11-35 s per game on a 32-core desktop; 14-19 min at the 0.1 CPU of Render's free tier). The first run also downloads the ~90 MB embedding model. Later visits are instant.

Settings live in the repo-root `.env`: `DATABASE_URL`, `DATABASE_URL_DIRECT` (optional; migrations use it when set), `MIN_REVIEW_COUNT` (200), `MAX_REVIEWS` (1000), `STEAM_REQUEST_DELAY_S` (0.5). `API_URL` points the dashboard at an API elsewhere.

### Retraining from scratch: ingest, train, serve

The sentiment model trains on cached reviews, and only the running API caches them. So a from-scratch setup, with the committed model deleted, goes in this order:

1. **Ingest.** With the API running (no model needed yet), request a few games' reviews, e.g. `curl "localhost:8000/games/1145360/reviews?limit=1"`. Include games with mixed ratings so the model sees enough negative reviews. The shipped model used Portal 2, Hades, Starfield, Cities: Skylines II and PAYDAY 3 (appids 620, 1145360, 1716740, 949230, 1272080).
2. **Train.** `python scripts/train_sentiment.py` writes `models_store/sentiment.joblib` and `docs/eval/sentiment_report.txt`.
3. **Serve.** `/aspects` and filtered `/reviews` now work. Until a model exists they answer 503. The API loads the model on first use, so no restart is needed for the first one. After a *re*train, restart the API and run `python scripts/process_reviews.py --force` to redo stored results.

After changing anchors or the threshold, rerun `python scripts/evaluate_aspects.py` (writes `docs/eval/aspect_report.txt`) and `python scripts/process_reviews.py --force`.

### API

| Endpoint | |
|---|---|
| `GET /games` | Games whose analysis is done, with review count, analysis date, per-aspect counts and Steam's thumbs on the same reviews |
| `GET /games/{appid}/reviews?limit=&offset=&aspect=&sentiment=&sort=&day=&disagree=` | Cached reviews with predicted sentiment, aspects and the sentences each aspect was matched on; `sort` is `newest` (default), `helpful` or `playtime`, `day` a UTC date, `disagree=true` only reviews where the model and the thumb differ; ingests on first call |
| `GET /games/{appid}/aspects` | Per-aspect positive/negative review counts with example sentences, overall counts, daily trend, Steam's thumbs on the same reviews, how many reviews the model and the thumb disagree on, and Steam's store rating |
| `GET /steam/search?q=` | Steam store search by name or appid (proxied for CORS, cached 60 s per term), each result marked `analyzed`, `on_demand` or `unavailable` for this deployment |
| `GET /health` | Liveness |

| Status | Meaning |
|---|---|
| 404 | Steam has no app with that appid |
| 422 | Fewer than 200 usable English reviews, or an invalid appid |
| 502 | Steam unavailable or rate-limiting |
| 202 `{status: "processing"}` | Reviews are being analyzed; retry shortly (`/aspects` and filtered `/reviews`) |
| 500 `{status: "failed"}` | Analysis failed; the reason is in `games.aspects_error` and the server log. Retry with `python scripts/process_reviews.py --force APPID` |
| 403 `{status: "unavailable"}` | On-demand processing is off (`ALLOW_ON_DEMAND_PROCESSING=false`, as in the deployed demo) and this app wasn't analyzed ahead of time (a game being reanalyzed or one whose run failed still gets its 202 or 500); `available` lists the games that were. Checked before ingestion, so nothing is fetched from Steam |
| 503 | No sentiment model available |

### Tests and lint

```bash
cd backend && source .venv/bin/activate
pytest -q                          # needs the db container; Steam is mocked with respx
ruff check .
python scripts/check_memory.py     # peak RSS vs the 512 MB free tier: synthetic worst case, an APPID for its cached reviews, or --aspects APPID for one /aspects request

cd ../frontend
npm test && npm run lint && npm run typecheck
```

## Deploy (free tiers)

- **Database: Neon.** Create a project. Neon shows a pooled and a direct connection string. Convert each to the asyncpg form: add `+asyncpg` to the scheme and replace `?sslmode=require&channel_binding=require` with `?ssl=require`, because asyncpg rejects `sslmode` and `channel_binding`:
  `postgresql+asyncpg://USER:PASSWORD@HOST/DB?ssl=require`
- **Demo games: load them into Neon before the first deploy.** The deployed API doesn't analyze new games (see above), so copy the five analyzed games from the local database. First make sure every local game is `done` (`python scripts/process_reviews.py` skips the ones that are). Then, from the repo root, with the local `db` container running:
  ```bash
  # Create the tables on Neon (the Render container would do this on start; the copy needs them first).
  (cd backend && source .venv/bin/activate && \
    DATABASE_URL='<pooled, asyncpg form>' DATABASE_URL_DIRECT='<direct, asyncpg form>' alembic upgrade head)
  # Copy games, reviews and aspect units in one transaction. psql takes the direct string as Neon shows it.
  # Keep --data-only: without it the dump writes review_aspects before reviews, and on Neon the FK
  # already exists, so that table fails to load. pipefail makes a pg_dump failure visible.
  set -o pipefail
  docker compose exec -T db pg_dump -U steam -d steam_reviews --data-only \
      -t games -t reviews -t review_aspects -t review_aspects_id_seq \
    | docker compose exec -T db psql -v ON_ERROR_STOP=1 --single-transaction '<direct string>'
  # Check: five rows, all done.
  docker compose exec -T db psql '<direct string>' -c "SELECT appid, name, aspects_status FROM games ORDER BY appid"
  # Check: row counts and the id sequence match the local database (run it against both).
  docker compose exec -T db psql '<direct string>' -At -c \
    "SELECT 'games', count(*) FROM games UNION ALL SELECT 'reviews', count(*) FROM reviews UNION ALL SELECT 'review_aspects', count(*) FROM review_aspects UNION ALL SELECT 'seq', last_value FROM review_aspects_id_seq"
  ```
  The Neon tables must be empty before the copy. The load rolls back on the first duplicate key, so after a failed or partial attempt clear them with `TRUNCATE games, reviews, review_aspects RESTART IDENTITY CASCADE` (check which database you are connected to first) and run it again.
  This was rehearsed against a fresh local database: five games `done`, 1000 reviews each with sentiment, and the aspect-unit id sequence carried over.
- **API: Render.** New → Blueprint, then select this repo. `render.yaml` defines a Docker web service from `backend/`. Enter `DATABASE_URL` (the pooled string) and `DATABASE_URL_DIRECT` (the direct string) when prompted. `ALLOW_ON_DEMAND_PROCESSING=false` comes from `render.yaml`. The container runs `alembic upgrade head` against the direct endpoint on start. The embedding model is baked into the image, and the sentiment model ships in the repo. The app disables asyncpg's prepared-statement cache, so it works through Neon's PgBouncer pooler.
- **Dashboard: Vercel.** Import the repo with root directory `frontend`, and set `API_URL` to the Render service URL *before* the first build, because Next.js bakes the `/api` rewrite into the build. `frontend/Dockerfile` is available for hosting it elsewhere (`--build-arg API_URL=...`).

## Repo map

- `backend/src/` has one package per domain (`games/`, `reviews/`, `ml/`), each with its router, schemas, models and service. `ml/anchors.py` holds the anchor phrases and `ml/constants.py` holds the threshold.
- `backend/scripts/` holds training, evaluation, batch processing and the memory check.
- `frontend/app/` has the homepage (game cards and Steam search) and the per-game dashboard. `frontend/lib/api.ts` maps HTTP status to UI state; `frontend/lib/insights.ts` turns the numbers into the plain-language verdict and comparison.
- `docs/PROJECT_BRIEF.md` covers scope and milestones. `docs/DECISIONS.md` logs every decision with its reason and measured numbers. `docs/eval/` holds the eval reports and the hand labels.
