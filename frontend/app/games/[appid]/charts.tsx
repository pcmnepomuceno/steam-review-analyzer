"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { AspectSummary } from "../../../lib/api";
import { formatDate } from "../../../lib/insights";

// Colors are CSS variables from globals.css, so light/dark mode needs nothing here.
const AXIS = { stroke: "var(--border)", tick: { fill: "var(--text-2)", fontSize: 13 } };
const TOOLTIP = {
  contentStyle: {
    background: "var(--surface-2)",
    border: 0,
    borderRadius: 3,
    boxShadow: "0 4px 16px rgb(0 0 0 / 0.35)",
    color: "var(--text)",
  },
  labelStyle: { color: "var(--text-strong)", fontWeight: 500 },
  cursor: { fill: "var(--field)", fillOpacity: 0.5 },
  // Series order (Positive first, as in the legend), not Recharts' alphabetical default.
  itemSorter: (item: { dataKey?: unknown }) => (item.dataKey === "positive" ? 0 : 1),
};
const LEGEND = {
  itemSorter: null, // keep series order (Positive first), not alphabetical
  formatter: (v: string) => <span style={{ color: "var(--text)" }}>{v}</span>,
};
// Data arrives once, so the grow-in animation only delays reading the chart.
const STATIC = { isAnimationActive: false };

export function TrendChart({ trend, spike }: { trend: AspectSummary["trend"]; spike?: string }) {
  const first = trend[0]?.date;
  const last = trend.at(-1)?.date;
  return (
    <>
      {/* Recharts' keyboard layer makes the SVG an unnamed role="application" tab stop; the
          chart is summarized here instead and its numbers are in the table below. */}
      <div
        className="chart"
        role="img"
        aria-label={
          first && last
            ? `Bar chart of reviews per day the model reads as positive and negative, ${formatDate(first)} to ${formatDate(last)}. The numbers are in the table below.`
            : "Bar chart of reviews per day"
        }
      >
        <ResponsiveContainer>
          <BarChart data={trend} margin={{ right: 16 }} accessibilityLayer={false}>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis dataKey="date" minTickGap={24} tickFormatter={shortDate} {...AXIS} />
            <YAxis allowDecimals={false} width={40} {...AXIS} />
            <Tooltip {...TOOLTIP} labelFormatter={(d) => formatDate(String(d))} />
            <Legend {...LEGEND} />
            {spike && (
              <ReferenceLine
                x={spike}
                stroke="var(--negative-text)"
                strokeDasharray="3 3"
                ifOverflow="extendDomain"
                label={{ value: "Spike", position: "insideTopRight", fill: "var(--negative-text)", fontSize: 13 }}
              />
            )}
            {/* A 1px surface-colored stroke on each segment makes a 2px gap between them. */}
            <Bar
              {...STATIC}
              dataKey="positive"
              name="Positive"
              stackId="day"
              fill="var(--positive)"
              stroke="var(--surface)"
              strokeWidth={1}
            />
            <Bar
              {...STATIC}
              dataKey="negative"
              name="Negative"
              stackId="day"
              fill="var(--negative)"
              stroke="var(--surface)"
              strokeWidth={1}
              radius={[4, 4, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <DataTable
        caption="Show as a table"
        columns={[
          ["date", "Date"],
          ["positive", "Positive"],
          ["negative", "Negative"],
        ]}
        rows={trend.map((d) => ({ ...d, date: formatDate(d.date) }))}
      />
    </>
  );
}

const SHORT = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const shortDate = (iso: string) => SHORT.format(new Date(iso));

function DataTable<T extends Record<string, string | number | null>>({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: [keyof T & string, string][]; // [key, header]
  rows: T[];
}) {
  return (
    <details>
      <summary>{caption}</summary>
      <table>
        <thead>
          <tr>
            {columns.map(([key, header]) => (
              <th key={key} scope="col">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map(([key]) => (
                <td key={key}>{row[key]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
