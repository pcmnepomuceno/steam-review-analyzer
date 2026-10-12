"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchState,
  headerImage,
  type AspectCounts,
  type GameListItem,
  type Loaded,
} from "../lib/api";
import {
  ASPECT_COPY,
  comparison,
  copyFor,
  MIN_MENTIONS,
  pct,
  timeAgo,
  verdict,
} from "../lib/insights";
import AppidForm from "./AppidForm";

const TOPICS = Object.keys(ASPECT_COPY);
// Local time, unlike formatDate's UTC days: this is when the analysis ran, not a trend day.
const ANALYZED = new Intl.DateTimeFormat("en-US", { dateStyle: "long", timeStyle: "short" });

/** The homepage's search box and the cards of every game whose analysis is ready. */
export default function GameBrowser() {
  const [games, setGames] = useState<Loaded<GameListItem[]> | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchState<GameListItem[]>("/api/games", controller.signal)
      .then(setGames)
      .catch(() => {}); // aborted on unmount
    return () => controller.abort();
  }, []);

  const list = games?.kind === "ready" ? games.data : null;
  const analyzed = list ? new Set(list.map((g) => g.appid)) : undefined;

  return (
    <>
      <AppidForm analyzed={analyzed} />

      <section aria-labelledby="games-heading">
        <div className="section-head">
          <h2 id="games-heading">Analyzed games</h2>
          <p className="muted">
            Each built from its most recent English reviews on Steam. The topic columns are the
            share of reviews about each topic that read as positive.
          </p>
        </div>
        {!games && (
          <ul className="games" aria-busy="true" aria-label="Loading games">
            {[0, 1, 2].map((i) => (
              <li key={i} className="skeleton" />
            ))}
          </ul>
        )}
        {games && !list && (
          <p role="alert" className="panel">
            Could not load the game list. {"message" in games ? games.message : ""} The search
            above still works.
          </p>
        )}
        {list?.length === 0 && (
          <p className="panel muted">
            No game has been analyzed yet. Search for one above to fetch and analyze it.
          </p>
        )}
        {list && list.length > 0 && (
          <>
            {/* Column heads for the topic cells; each cell also names its topic for narrow
                screens and screen readers, so this row is visual only. */}
            <div className="games-head" aria-hidden>
              {TOPICS.map((t) => (
                <span key={t}>{copyFor(t).short}</span>
              ))}
            </div>
            <ul className="games">
              {list.map((g) => {
                // An API older than the card counts sends no `aspects`: show the row without a claim.
                const { aspects, steam_sample } = g;
                const result = aspects && steam_sample ? comparison({ aspects, steam_sample }) : null;
                const claim = result?.text ?? (aspects ? verdict({ aspects }) : null);
                return (
                  <li key={g.appid} className="game-row">
                    <div className="art">
                      {/* eslint-disable-next-line @next/next/no-img-element -- Steam CDN banner */}
                      <img
                        src={headerImage(g.appid)}
                        alt=""
                        width={460}
                        height={215}
                        loading="lazy"
                        onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                      />
                    </div>
                    <div className="body">
                      <h3>
                        {/* Stretched over the whole row by CSS, so the row is one link whose
                            name is just the game's. */}
                        <Link href={`/games/${g.appid}`}>{g.name}</Link>
                      </h3>
                      {claim && <p className="claim">{claim}</p>}
                      <p className="meta">
                        Based on {g.review_count.toLocaleString("en-US")} recent reviews
                        {g.analyzed_at && (
                          <>
                            {" · Updated "}
                            <time
                              dateTime={g.analyzed_at}
                              title={ANALYZED.format(new Date(g.analyzed_at))}
                            >
                              {timeAgo(g.analyzed_at)}
                            </time>
                          </>
                        )}
                      </p>
                    </div>
                    {aspects && (
                      <TopicStrip
                        aspects={aspects}
                        weak={result?.weaker ? result.aspect : null}
                      />
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </section>
    </>
  );
}

/** One cell per topic: its positive share and split, or a dash when too few reviews mention it. */
function TopicStrip({ aspects, weak }: { aspects: AspectCounts[]; weak: string | null }) {
  return (
    <ul className="strip">
      {TOPICS.map((t) => {
        const a = aspects.find((x) => x.aspect === t);
        const share = a && a.total >= MIN_MENTIONS ? pct(a.positive_pct) : null;
        const { short, narrow, label } = copyFor(t);
        return (
          <li key={t} className={share === null ? "thin" : t === weak ? "weak" : undefined}>
            <span className="name" aria-hidden>
              {narrow ?? short}
            </span>
            <span className="sr-only">{label}: </span>
            {share === null ? (
              <>
                <span className="num" aria-hidden>
                  –
                </span>
                <span className="sr-only">too few reviews to judge</span>
              </>
            ) : (
              <>
                <span className="num">
                  {share}%<span className="sr-only"> positive</span>
                </span>
                <span className="bar" aria-hidden>
                  <span style={{ width: `${share}%` }} />
                </span>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}
