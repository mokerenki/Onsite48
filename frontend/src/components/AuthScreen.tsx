"use client";

import { FormEvent, useState } from "react";
import { ArrowRight, Github, LockKeyhole } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";

// Capabilities that drift inward toward the logo. Two copies, not three: a
// -50% translate is only seamless when the track holds exactly two cycles of
// the content, and a third copy makes the loop visibly jump on wrap.
//
// Every entry below is backed by at least one registered tool in
// backend/app/tools/. If you add a pill, first add a tool whose schema
// declares the matching resource + operations, otherwise the deterministic
// planner has nothing to route to and the quick-action button will fail.
const CAPABILITIES = [
  "Web Research",
  "Market Intelligence",
  "Competitive Analysis",
  "Documents",
  "Contract Review",
  "Compliance Checks",
  "Audit Trails",
  "Email & Inbox",
  "Calendar & Scheduling",
  "Slack & Messaging",
  "GitHub & Code",
  "Issue Tracking",
  "Browser Automation",
  "Onsite48 Bridge",
  "Analytics",
  "Forecasting",
  "Reporting",
] as const;



// Namespaced so they cannot collide with a marquee defined elsewhere on the
// page, and disabled under prefers-reduced-motion: an infinite decorative
// scroll is exactly the kind of motion that setting is asking to be spared.
const TICKER_STYLES = `
  @keyframes auth-marquee-inward-right {
    0% { transform: translateX(-50%); }
    100% { transform: translateX(0%); }
  }
  @keyframes auth-marquee-inward-left {
    0% { transform: translateX(0%); }
    100% { transform: translateX(-50%); }
  }
  .auth-marquee-track {
    display: flex;
    width: max-content;
    animation-duration: 26s;
    animation-timing-function: linear;
    animation-iteration-count: infinite;
  }
  .auth-marquee-inward-right { animation-name: auth-marquee-inward-right; }
  .auth-marquee-inward-left { animation-name: auth-marquee-inward-left; }
  @media (prefers-reduced-motion: reduce) {
    .auth-marquee-track { animation: none; }
  }
`;

const tickerFade = {
  maskImage: "linear-gradient(to right, transparent, black 14%, black 86%, transparent)",
  WebkitMaskImage: "linear-gradient(to right, transparent, black 14%, black 86%, transparent)",
};

const ACCENT = "#F5E6D3";

const SERIF_DISPLAY = `"Tiempos Headline", "GT Sectra", "Instrument Serif", "Source Serif 4", Georgia, "Times New Roman", serif`;

const SERIF_FONT_LINK = (
  <>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
    <link
      rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&display=swap"
    />
  </>
);

export function AuthScreen() {
  const { login, register, startOAuth } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [oauthProvider, setOauthProvider] = useState<"google" | "github" | null>(null);

  async function startProviderSignIn(provider: "google" | "github") {
    setOauthProvider(provider);
    setError(null);
    try {
      await startOAuth(provider);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Provider sign-in could not be started. Please check the local OAuth configuration.",
      );
      setOauthProvider(null);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "login") {
        await login({ email, password });
      } else {
        await register({
          email,
          password,
          display_name: displayName,
          organization_name: organizationName,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "We could not continue. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#f8f8f6] px-5 py-10 text-[#161616] sm:px-8">
      {SERIF_FONT_LINK}
      <div className="mx-auto grid min-h-[calc(100vh-5rem)] max-w-6xl overflow-hidden rounded-[2rem] border border-black/10 bg-white shadow-[0_24px_80px_rgba(20,20,18,0.12)] lg:grid-cols-[1.1fr_0.9fr]">
        <section className="relative hidden overflow-hidden p-12 text-white lg:flex lg:flex-col">
          <style>{TICKER_STYLES}</style>
          {/* Base gradient, 8 stops top to bottom. */}
          <div
            aria-hidden="true"
            className="absolute inset-0"
            style={{
              backgroundImage:
                "linear-gradient(to bottom, #1E2238 0%, #232A45 14%, #2E3350 29%, #4B4C6A 43%, #746C87 57%, #9C7A82 71%, #B87362 86%, #A86354 100%)",
            }}
          />
          {/* Gold glow, lower-left. */}
          <div
            aria-hidden="true"
            className="absolute inset-0"
            style={{
              opacity: 0.7,
              backgroundImage:
                "radial-gradient(110% 85% at 0% 100%, rgba(255,214,150,0.55) 0%, rgba(255,190,120,0.25) 45%, rgba(255,190,120,0) 72%)",
            }}
          />
          <div className="relative flex items-center gap-2 text-lg font-semibold tracking-tight">
            {/* Brand mark. This one sits on the opaque white tile, so it takes the dark
                variant. The identical mark lower down sits directly on the
                gradient and has to be the ivory one, or it disappears. */}
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-white">
              <img
                src="/Onsite48-opt.png"
                alt=""
                aria-hidden="true"
                className="h-6 w-6 object-contain"
              />
            </span>
            Onsite48
          </div>

          {/* Capabilities converging inward on the mark. */}
          <div aria-hidden="true" className="relative mt-10 mb-2 flex items-center">
            <div className="min-w-0 flex-1 overflow-hidden" style={tickerFade}>
              <div className="auth-marquee-track auth-marquee-inward-right gap-2.5">
                {[...CAPABILITIES, ...CAPABILITIES].map((label, i) => (
                  <span
                    key={`in-${i}`}
                    className="min-w-[132px] whitespace-nowrap rounded-xl border border-white/15 bg-white/[0.07] px-5 py-2.5 text-center text-[13px] font-medium text-white/80 shadow-sm backdrop-blur-sm"
                  >
                    {label}
                  </span>
                ))}
              </div>
            </div>
            <span className="z-10 mx-6 flex shrink-0 items-center justify-center rounded-xl border border-white/15 bg-white/10 p-3 shadow-md backdrop-blur-sm">
              <img
                src="/Onsite48-light.png"
                alt=""
                aria-hidden="true"
                className="h-9 w-9 object-contain"
              />
            </span>
            <div className="min-w-0 flex-1 overflow-hidden" style={tickerFade}>
              <div className="auth-marquee-track auth-marquee-inward-left gap-2.5">
                {[...CAPABILITIES, ...CAPABILITIES].map((label, i) => (
                  <span
                    key={`out-${i}`}
                    className="min-w-[132px] whitespace-nowrap rounded-xl border border-white/15 bg-white/[0.07] px-5 py-2.5 text-center text-[13px] font-medium text-white/80 shadow-sm backdrop-blur-sm"
                  >
                    {label}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="relative my-auto max-w-lg">
            <p className="mb-5 text-xs font-semibold uppercase tracking-[0.22em] text-white/50">
              Private beta workspace
            </p>
            {/*
              The emphasised span uses a serif and the warm ivory accent.
              `font-light` and a slightly larger tracking-[-0.03em] bring it
              closer to Claude's editorial cut without the full serif weight.
            */}
            <h1 className="text-5xl font-semibold leading-[1.03] tracking-[-0.045em]">
              Your business,{" "}
              <span
                style={{
                  color: ACCENT,
                  fontFamily: SERIF_DISPLAY,
                  fontWeight: 400,
                  letterSpacing: "-0.02em",
                }}
              >
                one step ahead.
              </span>
            </h1>
            <p className="mt-6 max-w-md text-lg leading-8 text-white/75">
              Connect the tools your team already uses, delegate meaningful work, and stay in control of every consequential action.
            </p>
          </div>
          <div className="relative rounded-2xl border border-white/15 bg-white/[0.08] p-5 text-sm text-white/80 backdrop-blur-sm">
            <LockKeyhole className="mb-3 h-5 w-5" style={{ color: ACCENT }} />
            Tasks, approvals, and connectors are isolated to your workspace. Sensitive actions are always shown before they run.
          </div>
        </section>

        <section className="flex items-center justify-center p-6 sm:p-12">
          <div className="w-full max-w-sm">
            {/* Mobile-only brand mark. The left panel carries the same lockup from `lg`
                up, and it is `hidden lg:flex`, so without this the header
                would be unbranded on small screens. Using the dark variant
                because this column is the white one. */}
            <div className="mb-8 flex items-center gap-2.5 lg:hidden">
              <img
                src="/Onsite48-opt.png"
                alt=""
                aria-hidden="true"
                className="h-10 w-10 shrink-0 object-contain"
              />
              <span className="text-lg font-semibold tracking-tight">Onsite48</span>
            </div>
            <h2 className="text-3xl font-semibold tracking-[-0.04em]">
              {mode === "login" ? "Welcome back" : "Create your workspace"}
            </h2>
            <p className="mt-2 text-sm leading-6 text-[#6a6a67]">
              {mode === "login"
                ? "Sign in to continue where your agents left off."
                : "Start with a secure home for your autonomous workforce."}
            </p>

            <div className="mt-7 grid grid-cols-2 gap-2 rounded-xl bg-[#f2f2ef] p-1 text-sm font-medium">
              <button
                type="button"
                onClick={() => setMode("login")}
                className={`rounded-lg px-3 py-2 transition ${mode === "login" ? "bg-white shadow-sm" : "text-[#777774]"}`}
              >
                Sign in
              </button>
              <button
                type="button"
                onClick={() => setMode("register")}
                className={`rounded-lg px-3 py-2 transition ${mode === "register" ? "bg-white shadow-sm" : "text-[#777774]"}`}
              >
                Create account
              </button>
            </div>

            <div className="mt-6 grid grid-cols-2 gap-3">
              <button
                type="button"
                disabled={oauthProvider !== null}
                onClick={() => void startProviderSignIn("google")}
                className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-black/10 text-sm font-medium hover:bg-[#fafaf8] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {oauthProvider === "google" ? "Opening…" : "Google"}
              </button>
              <button
                type="button"
                disabled={oauthProvider !== null}
                onClick={() => void startProviderSignIn("github")}
                className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-black/10 text-sm font-medium hover:bg-[#fafaf8] disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Github className="h-4 w-4" /> {oauthProvider === "github" ? "Opening…" : "GitHub"}
              </button>
            </div>
            <div className="my-6 flex items-center gap-3 text-xs text-[#999995]">
              <span className="h-px flex-1 bg-black/10" />or continue with email
              <span className="h-px flex-1 bg-black/10" />
            </div>

            <form className="space-y-4" onSubmit={submit}>
              {mode === "register" && (
                <>
                  <label className="block text-sm font-medium">
                    Your name
                    <input
                      required
                      value={displayName}
                      onChange={(event) => setDisplayName(event.target.value)}
                      className="mt-1.5 h-11 w-full rounded-xl border border-black/10 px-3 outline-none transition focus:border-[#5f75d8] focus:ring-4 focus:ring-[#5f75d8]/10"
                      placeholder="Alex Johnson"
                    />
                  </label>
                  <label className="block text-sm font-medium">
                    Workspace name
                    <input
                      required
                      value={organizationName}
                      onChange={(event) => setOrganizationName(event.target.value)}
                      className="mt-1.5 h-11 w-full rounded-xl border border-black/10 px-3 outline-none transition focus:border-[#5f75d8] focus:ring-4 focus:ring-[#5f75d8]/10"
                      placeholder="Acme Studio"
                    />
                  </label>
                </>
              )}
              <label className="block text-sm font-medium">
                Email address
                <input
                  required
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  className="mt-1.5 h-11 w-full rounded-xl border border-black/10 px-3 outline-none transition focus:border-[#5f75d8] focus:ring-4 focus:ring-[#5f75d8]/10"
                  placeholder="you@company.com"
                />
              </label>
              <label className="block text-sm font-medium">
                Password
                <input
                  required
                  minLength={10}
                  maxLength={72}
                  type="password"
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="mt-1.5 h-11 w-full rounded-xl border border-black/10 px-3 outline-none transition focus:border-[#5f75d8] focus:ring-4 focus:ring-[#5f75d8]/10"
                  placeholder="At least 10 characters"
                />
              </label>
              {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
              <button
                disabled={submitting}
                className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#151515] text-sm font-semibold text-white transition hover:bg-[#343434] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {submitting
                  ? "Please wait…"
                  : mode === "login"
                    ? "Sign in"
                    : "Create secure workspace"}
                <ArrowRight className="h-4 w-4" />
              </button>
            </form>
            <p className="mt-6 text-center text-xs leading-5 text-[#8b8b87]">
              By continuing, you agree to use Onsite48AI only with accounts and data you are authorized to access.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}