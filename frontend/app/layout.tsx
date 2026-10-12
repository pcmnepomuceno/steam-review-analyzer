import type { Metadata, Viewport } from "next";
import { Figtree } from "next/font/google";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";
import ThemeToggle from "./ThemeToggle";

// Steam's own face (Motiva Sans) is Valve's; Figtree is a close, freely licensed match.
const sans = Figtree({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: "Steam Review Analyzer",
  description: "What Steam reviews say about performance, price, bugs, story and gameplay",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover", // lets the header paint under the notch; content pads back via env()
  // The header is dark in both color schemes, so the browser chrome matches it in both.
  themeColor: "#171d25",
};

// Applies the theme ThemeToggle saved before the first paint, so a saved theme doesn't flash
// and the pages stay static (a cookie read on the server would render them per request).
// Without a saved theme, globals.css follows prefers-color-scheme. Next's guide:
// node_modules/next/dist/docs/01-app/02-guides/preventing-flash-before-hydration.md
const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}})()`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // suppressHydrationWarning: the script above may set data-theme before React hydrates.
    <html lang="en" className={sans.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <header className="site-header">
          <div>
            <Link href="/">
              <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden fill="none">
                <rect x="3" y="12" width="4" height="9" rx="1" fill="#66c0f4" />
                <rect x="10" y="7" width="4" height="14" rx="1" fill="#dfe8ef" />
                <rect x="17" y="3" width="4" height="18" rx="1" fill="#d9693a" />
              </svg>
              Steam Review <span>Analyzer</span>
            </Link>
            <ThemeToggle />
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
