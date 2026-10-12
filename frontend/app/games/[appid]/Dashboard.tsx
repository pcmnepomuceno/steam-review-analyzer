"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  headerImage,
  poll,
  type AspectStats,
  type AspectSummary,
  type Loaded,
  type SteamRating,
} from "../../../lib/api";
import {
  comparison,
  copyFor,
  formatDate,
  MIN_MENTIONS,
  pct,
  quoteText,
  spike,
  storePct,
  strength,
  verdict,
} from "../../../lib/insights";
import AppidForm from "../../AppidForm";
import { TrendChart } from "./charts";
import ReviewList from "./ReviewList";
import Thumb from "./Thumb";

type State = Loaded<AspectSummary> | { kind: "loading" };

const POLL_MS = 3000;

export default function Dashboard({ appid }: { appid: number }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    poll<AspectSummary>(`/api/games/${appid}/aspects`, {
      intervalMs: POLL_MS,
      signal: controller.signal,
      onUpdate: (s) => setState(s as Loaded<AspectSummary>),
    }).catch(() => {}); // only rejects when aborted on unmount
    return () => controller.abort();
  }, [appid, attempt]);

  // A final screen replaced the waiting one: start keyboard and screen reader users at its
  // heading, unless they already moved focus somewhere themselves.
  useEffect(() => {
    if (state.kind === "loading" || state.kind === "processing") return;
    const active = document.activeElement;
    if (active && active !== document.body) return;
    document.querySelector<HTMLElement>("main h1")?.focus({ preventScroll: true });
  }, [state.kind]);

  const retry = () => {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  };

  switch (state.kind) {
    case "loading":
      return (
        <Status title="Loading…" spinner>
          {/* Shown only if the answer is slow, so a cached game doesn't flash it. */}
          <span className="later">
            A game seen for the first time is fetched from Steam first, which takes 10–20 seconds.
          </span>
        </Status>
      );
    case "processing":
      return (
        <Status title="Analyzing reviews…" spinner>
          Sentiment and topic tagging runs once per game and takes up to a minute. Checking again
          every {POLL_MS / 1000} seconds; results appear here when ready.
        </Status>
      );
    case "timeout":
      return (
        <Status title="Still analyzing">
          <p>This is taking longer than expected; the analysis may have stalled.</p>
          <p>
            <button onClick={retry}>Check again</button> or <Link href="/">pick another game</Link>
          </p>
        </Status>
      );
    case "failed":
      return (
        <Status title="Analysis failed" error>
          {/* No retry button: a failed game stays failed until the owner reruns it with
              --force, which no visitor can do. */}
          <p>{state.message}</p>
          <p>
            <Link href="/">Pick another game</Link>
          </p>
        </Status>
      );
    case "not_found":
      return (
        <Status title="Game not found" error>
          <p>Steam has no app with appid {appid}. Search for the game by name instead:</p>
          <div className="state-search">
            <AppidForm />
          </div>
        </Status>
      );
    case "unavailable":
      return (
        <Status title="Not in this demo">
          <p>{state.message}</p>
          {state.games.length > 0 && (
            <>
              <p>These games are ready to open:</p>
              <ul>
                {state.games.map((g) => (
                  <li key={g.appid}>
                    <Link href={`/games/${g.appid}`}>{g.name}</Link>
                  </li>
                ))}
              </ul>
            </>
          )}
          <p>
            <Link href="/">Back to the homepage</Link>
          </p>
        </Status>
      );
    case "error":
      return (
        <Status title={state.status === 422 ? "Not enough reviews" : "Something went wrong"} error>
          <p>{state.message}</p>
          <p>
            <button onClick={retry}>Try again</button> or <Link href="/">pick another game</Link>
          </p>
        </Status>
      );
    case "ready":
      return <Ready data={state.data} />;
  }
}

function Status({
  title,
  spinner,
  error,
  children,
}: {
  title: string;
  spinner?: boolean;
  error?: boolean;
  children: React.ReactNode;
}) {
  // A waiting screen is all text, so the whole of it is a live region. A final screen
  // announces only its title, not the search form or buttons below it, and also takes focus
  // when nothing else has it (see Dashboard). Keyed by title so a new screen inserts a fresh
  // region: screen readers miss a role set on an element whose text changes in the same render.
  return (
    <div className={`panel state${error ? " error" : ""}`} role={spinner ? "status" : undefined}>
      {spinner && <span className="spinner" aria-hidden />}
      <div key={title} role={spinner ? undefined : error ? "alert" : "status"}>
        <h1 tabIndex={-1}>{title}</h1>
      </div>
      <div className="muted">{children}</div>
    </div>
  );
}

function Ready({ data }: { data: AspectSummary }) {
  const { overall, trend, steam_rating: rating } = data;
  const first = trend[0]?.date;
  const last = trend.at(-1)?.date;
  const peak = spike(trend);
  const result = comparison(data);
  return (
    <>
      <Link href="/" className="back">
        <span aria-hidden>←</span> All games
      </Link>
      <div className="game-head">
        <div>
          <h1 tabIndex={-1}>{data.name}</h1>
          <p className="sample">
            The {overall.total.toLocaleString("en-US")} most recent English reviews
            {first && last && `, ${formatDate(first)} to ${formatDate(last)}`}. Older reviews are
            not included.
          </p>
        </div>
        <div className="art">
          {/* eslint-disable-next-line @next/next/no-img-element -- Steam CDN banner */}
          <img
            src={headerImage(data.appid)}
            alt=""
            width={460}
            height={215}
            onError={(e) => (e.currentTarget.style.visibility = "hidden")}
          />
        </div>
      </div>

      <Headline data={data} rating={rating} />

      <section aria-labelledby="topics-heading">
        <div className="section-head">
          <h2 id="topics-heading">By topic</h2>
          <p className="muted">How many of the reviews that bring each topic up read as positive.</p>
        </div>
        <ul className="topics panel">
          {data.aspects.map((a) => (
            <TopicRow
              key={a.aspect}
              appid={data.appid}
              aspect={a}
              weak={result?.weaker === true && result.aspect === a.aspect}
            />
          ))}
        </ul>
      </section>

      <section className="panel" aria-labelledby="trend-heading">
        <div className="section-head">
          <h2 id="trend-heading">Reviews per day</h2>
          <p className="muted">Split by how the sentiment model reads each review.</p>
        </div>
        {peak && (
          <p className="callout">
            {peak.text}{" "}
            <Link href={`/games/${data.appid}?day=${peak.date}&sentiment=negative#reviews`}>
              Read that day&apos;s negative reviews
            </Link>
          </p>
        )}
        <TrendChart trend={trend} spike={peak?.date} />
      </section>
      <section className="panel" id="reviews" aria-labelledby="reviews-heading">
        <h2 id="reviews-heading">Reviews</h2>
        <ReviewList appid={data.appid} aspects={data.aspects.map((a) => a.aspect)} />
      </section>
    </>
  );
}

function RatingLabel({ rating }: { rating: SteamRating }) {
  const tone = rating.score_desc.includes("Positive")
    ? "positive"
    : rating.score_desc.includes("Negative")
      ? "negative"
      : "mixed";
  return (
    <>
      <span className={`rating-${tone}`}>{rating.score_desc}</span>
      {rating.positive_pct !== null &&
        `, ${storePct(rating)}% of ${rating.total.toLocaleString("en-US")} reviews`}
    </>
  );
}

/** The page's one claim: Steam's thumbs against the weakest topic, then the method behind it. */
function Headline({ data, rating }: { data: AspectSummary; rating: SteamRating | null }) {
  const result = comparison(data);
  const summary = verdict(data);
  const support = result && strength(data);
  const sample = data.steam_sample.total;
  const disagree = data.disagreements;
  return (
    <section className="headline panel" aria-labelledby="headline">
      <h2 id="headline" className="verdict">
        {result?.text ?? summary}
      </h2>
      {result && (
        // Two bars on one 0-100% scale, so the gap between the thumbs and the topic is seen,
        // not only read. The numbers are text; the bars repeat them and are hidden from AT.
        <div className="figures">
          <div className="figure">
            <span className="num">{result.steamPct}%</span>
            <span className="label">Recommend it on Steam</span>
            <span className="meter" aria-hidden>
              <span style={{ width: `${result.steamPct}%` }} />
            </span>
          </div>
          <div className={`figure${result.weaker ? " weak" : ""}`}>
            <span className="num">{result.aspectPct}%</span>
            <span className="label">{copyFor(result.aspect).label} reviews read as positive</span>
            <span className="meter" aria-hidden>
              <span style={{ width: `${result.aspectPct}%` }} />
            </span>
          </div>
        </div>
      )}
      {support && <p className="support muted">{support}</p>}
      <details className="about">
        <summary>About these numbers</summary>
        <ul>
          {result && (
            <li>
              Both figures come from the same {sample.toLocaleString("en-US")} reviews. The first is
              the reviewers&apos; own thumbs up on Steam; the second is the share of reviews
              mentioning {copyFor(result.aspect).phrase} that the sentiment model reads as positive.
            </li>
          )}
          {disagree !== undefined && sample > 0 && (
            <li>
              The model and the reviewer disagree on {disagree.toLocaleString("en-US")} of these
              reviews ({Math.round((100 * disagree) / sample)}%). Steam records the thumb; the model
              reads the words, and people often recommend a game while listing its faults, or write
              warmly about one they don&apos;t recommend. Those reviews are marked in the list
              below.
            </li>
          )}
          {rating && (
            <li>
              Steam&apos;s store rating counts every English review, not only these:{" "}
              <RatingLabel rating={rating} />.
            </li>
          )}
          {/* Hand-copied from the eval reports; update them after retraining or retuning.
              Sentiment: negative precision 0.643 / recall 0.813 (docs/eval/sentiment_report.txt),
              not accuracy, which a model answering "positive" every time already gets to 78%.
              Tagging: accuracy 0.621 vs baseline 0.329 (docs/eval/aspect_report.txt). */}
          <li>
            The sentiment model reads {pct(data.overall.positive_pct) ?? 0}% of these reviews as
            positive. Tested on 1,000 reviews it had not seen, when it called a review negative it
            was right about 2 times in 3, and it caught about 4 in 5 of the negative reviews. It is
            weakest on games nearly everyone likes, where negative reviews are rare.
          </li>
          <li>
            Each review is split into sentences, and each sentence is matched to the topic whose
            example phrases it is closest to in meaning, or to none. That matched 62% of 140
            hand-labelled sentences, against 33% for always guessing &ldquo;no topic&rdquo;. The
            phrases were tuned on those same sentences, so that figure is optimistic: expect some
            quotes under the wrong topic. A quote&apos;s thumb is whether its reviewer recommends
            the game on Steam, as in the review list.
          </li>
          <li>
            <a href={METHOD_URL}>How it works and how it was measured</a>
          </li>
        </ul>
      </details>
    </section>
  );
}

const METHOD_URL = "https://github.com/pmNepomuceno/steam-review-analyzer#readme";

function TopicRow({
  appid,
  aspect: a,
  weak,
}: {
  appid: number;
  aspect: AspectStats;
  weak: boolean; // the topic the headline names as the weak spot
}) {
  const { label, description } = copyFor(a.aspect);
  const share = pct(a.positive_pct);
  // Under MIN_MENTIONS a share is noise (one review moves Hades' 12 performance reviews by 8
  // points): no percentage and no quotes to read a verdict into, only the reviews themselves.
  const judged = share !== null && a.total >= MIN_MENTIONS;
  const reviews = (n: number) => `${n.toLocaleString("en-US")} review${n === 1 ? "" : "s"}`;
  const about = <span className="sr-only"> about {label.toLowerCase()}</span>;
  return (
    <li className={`topic${judged ? "" : " thin"}`}>
      <div className="topic-name">
        <h3>{label}</h3>
        <p className="desc">{description}</p>
      </div>
      <div className="topic-stat">
        {judged ? (
          <>
            <p className="stat">
              <strong className={weak ? "weak" : undefined}>{share}%</strong> positive
            </p>
            <div
              className="split"
              role="img"
              aria-label={`${a.positive} positive and ${a.negative} negative reviews`}
            >
              {a.positive > 0 && <span className="pos" style={{ flex: a.positive }} />}
              {a.negative > 0 && <span className="neg" style={{ flex: a.negative }} />}
            </div>
          </>
        ) : (
          <p className="muted">
            {a.total === 0
              ? "No review in this sample mentions it."
              : `Only ${reviews(a.total)} mention it: too few to judge.`}
          </p>
        )}
        {a.total > 0 && (
          // One link per topic: the negative reviews when there are some (the topic's other
          // reviews are one Topic filter away in the list), else all of them.
          <p className="counts">
            {judged && a.negative > 0 ? (
              <>
                {reviews(a.total)}
                {" · "}
                <Link href={`/games/${appid}?aspect=${a.aspect}&sentiment=negative#reviews`}>
                  Read {a.negative.toLocaleString("en-US")} negative
                  <span className="sr-only"> reviews</span>
                  {about}
                </Link>
              </>
            ) : (
              <Link href={`/games/${appid}?aspect=${a.aspect}#reviews`}>
                {judged ? `Read ${reviews(a.total)}` : "Read them"}
                {about}
              </Link>
            )}
          </p>
        )}
      </div>
      {judged && a.quotes.length > 0 && (
        <ul className="quotes" aria-label={`What reviews say about ${label.toLowerCase()}`}>
          {a.quotes.map((q) => (
            // The reviewer's thumb, as in the review list; none for a quote stored without it.
            <li key={q.text} className={q.voted_up == null ? undefined : q.voted_up ? "up" : "down"}>
              {q.voted_up == null ? (
                <span />
              ) : (
                <Thumb
                  up={q.voted_up}
                  label={q.voted_up ? "The reviewer recommends it" : "The reviewer doesn't recommend it"}
                />
              )}
              <blockquote>&ldquo;{quoteText(q.text)}&rdquo;</blockquote>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
