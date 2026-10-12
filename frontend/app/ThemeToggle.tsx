"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

// The layout's inline script reads this key before the first paint.
const STORAGE_KEY = "theme";

/** Switches between dark and light; until the visitor picks one, the OS setting decides. */
export default function ThemeToggle() {
  // null until mounted: the server can't know the OS setting, so the label waits for it.
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    // Re-read when the OS switches, so the label stays right until a theme is saved.
    const light = window.matchMedia("(prefers-color-scheme: light)");
    const sync = () => {
      const set = document.documentElement.dataset.theme;
      setTheme(set === "light" || set === "dark" ? set : light.matches ? "light" : "dark");
    };
    sync();
    light.addEventListener("change", sync);
    return () => light.removeEventListener("change", sync);
  }, []);

  if (!theme) return <span className="theme-toggle" aria-hidden />;
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className="theme-toggle"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      onClick={() => {
        document.documentElement.dataset.theme = next;
        try {
          localStorage.setItem(STORAGE_KEY, next);
        } catch {} // storage blocked (private mode): the choice lasts for this page only
        setTheme(next);
      }}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        {next === "light" ? (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
          </>
        ) : (
          <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
        )}
      </svg>
    </button>
  );
}
