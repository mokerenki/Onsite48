"use client";

import React, { useEffect, useState } from "react";
import { Blocks, ChevronRight, PenTool, X } from "lucide-react";

// ─── Types ───────────────────────────────────────────────────────

type Layout =
  | "browser"
  | "chat"
  | "phone"
  | "flow"
  | "game"
  | "notebook"
  | "slides"
  | "doc"
  | "sheet"
  | "chart";

type HomeOption = {
  label: string;
  /**
   * A complete intent prefix ("Build a website: "). The description
   * composer is prefilled with this text, so the first user click
   * always submits a described intent — never a bare "Build a website"
   * that makes the planner answer with clarifying questions.
   */
  prompt: string;
  layout: Layout;
  tint: string;
  category: "build" | "create";
};

type PanelKey = "build" | "create";

const buildOptions: HomeOption[] = [
  { label: "Website", prompt: "Build a website: ", layout: "browser", tint: "from-sky-50 to-sky-100", category: "build" },
  { label: "Chatbot", prompt: "Build a chatbot: ", layout: "chat", tint: "from-indigo-50 to-indigo-100", category: "build" },
  { label: "Desktop app", prompt: "Build a desktop app: ", layout: "browser", tint: "from-blue-50 to-cyan-100", category: "build" },
  { label: "Mobile app", prompt: "Build a mobile app: ", layout: "phone", tint: "from-amber-50 to-orange-100", category: "build" },
  { label: "Chrome extension", prompt: "Build a Chrome extension: ", layout: "browser", tint: "from-slate-50 to-slate-200", category: "build" },
  { label: "Game", prompt: "Build a game: ", layout: "game", tint: "from-emerald-50 to-teal-100", category: "build" },
  { label: "Automation", prompt: "Build an automation: ", layout: "flow", tint: "from-violet-50 to-fuchsia-100", category: "build" },
  { label: "Jupyter notebook", prompt: "Build a Jupyter notebook: ", layout: "notebook", tint: "from-stone-50 to-stone-200", category: "build" },
];

const createOptions: HomeOption[] = [
  { label: "Slides", prompt: "Create a presentation: ", layout: "slides", tint: "from-orange-50 to-amber-100", category: "create" },
  { label: "Document", prompt: "Create a document: ", layout: "doc", tint: "from-sky-50 to-blue-100", category: "create" },
  { label: "Spreadsheet", prompt: "Create a spreadsheet: ", layout: "sheet", tint: "from-emerald-50 to-green-100", category: "create" },
  { label: "Data visualization", prompt: "Create a data visualization: ", layout: "chart", tint: "from-rose-50 to-pink-100", category: "create" },
  { label: "Research report", prompt: "Create a research report: ", layout: "doc", tint: "from-violet-50 to-indigo-100", category: "create" },
];

const PANELS: Record<PanelKey, { title: string; options: HomeOption[] }> = {
  build: { title: "Build", options: buildOptions },
  create: { title: "Create", options: createOptions },
};

// ─── Description Prompt Component ────────────────────────────────

function DescriptionPrompt({
  option,
  onSubmit,
  onCancel,
}: {
  option: HomeOption;
  onSubmit: (fullPrompt: string) => void;
  onCancel: () => void;
}) {
  // The composer opens prefilled with the complete intent prefix
  // ("Build a website: "). Everything the user types extends it, so
  // Enter always submits a described intent, never a bare prompt.
  const [description, setDescription] = useState(option.prompt);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.focus();
    node.setSelectionRange(node.value.length, node.value.length);
  }, []);

  // The prefix alone is not a submittable intent: the user must
  // extend it, otherwise a bare "Build a website:" would reach the
  // planner and trigger clarifying questions.
  const isComplete = description.trim().length > option.prompt.trim().length;

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (isComplete) {
        onSubmit(description.trim());
      }
    }
    if (e.key === "Escape") {
      onCancel();
    }
  };

  const handleSubmit = () => {
    if (isComplete) {
      onSubmit(description.trim());
    }
  };

  const categoryLabel = option.category === "build" ? "Build" : "Create";
  const placeholderText =
    option.category === "build"
      ? `Describe the ${option.label.toLowerCase()} you want to build...`
      : `Describe the ${option.label.toLowerCase()} you want to create...`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/30 backdrop-blur-sm" onClick={onCancel}>
      <div
        className="w-full max-w-2xl rounded-2xl bg-white shadow-xl border border-slate-200 overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
          <div className="flex items-center gap-3">
            <div className={`rounded-xl px-3 py-2 text-sm font-medium ${option.category === "build" ? "bg-blue-50 text-blue-700" : "bg-amber-50 text-amber-700"}`}>
              {categoryLabel}
            </div>
            <h3 className="text-lg font-semibold text-slate-900">{option.label}</h3>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 transition-colors"
            aria-label="Cancel"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="px-6 py-4">
          <p className="mb-3 text-sm text-slate-600">
            Your intent is already started. Add what you want built or created, then press Enter.
          </p>
          <textarea
            ref={textareaRef}
            value={description}
            onChange={e => setDescription(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholderText}
            rows={4}
            className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 placeholder-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 focus:outline-none resize-none transition-colors"
            aria-label="Description"
          />
          <div className="mt-3 flex items-center justify-between gap-2">
            <span className="text-xs text-slate-400">
              {description.length}/2000 • Enter to submit • Shift+Enter for new line • Esc to cancel
            </span>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-slate-100">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!isComplete}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Start
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Thumbnails (pure CSS, no image assets) ──────────────────────

function Bar({ className = "" }: { className?: string }) {
  return <span className={`block h-1.5 rounded-full bg-slate-300 ${className}`} />;
}

function Dots() {
  return (
    <div className="flex gap-1 p-2">
      <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
      <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
    </div>
  );
}

function Thumb({ layout, tint }: { layout: Layout; tint: string }) {
  return (
    <div aria-hidden="true" className={`relative h-full w-full overflow-hidden bg-gradient-to-br ${tint}`}>
      {layout === "browser" && (
        <div className="absolute inset-x-6 bottom-0 top-5 rounded-t-lg bg-white shadow-sm">
          <Dots />
          <div className="mx-auto mt-1 flex w-2/3 flex-col items-center gap-1.5">
            <Bar className="w-3/4" />
            <Bar className="w-1/2" />
            <span className="mt-1 h-3 w-10 rounded-full bg-slate-800" />
          </div>
          <div className="mx-3 mt-3 h-10 rounded-md bg-sky-200/70" />
        </div>
      )}

      {layout === "chat" && (
        <div className="absolute inset-x-6 bottom-0 top-5 flex overflow-hidden rounded-t-lg bg-white shadow-sm">
          <div className="w-1/4 bg-slate-800" />
          <div className="flex flex-1 flex-col gap-2 p-3">
            <Bar className="w-1/2" />
            <span className="ml-auto h-4 w-2/3 rounded-lg bg-indigo-200" />
            <span className="h-4 w-1/2 rounded-lg bg-slate-200" />
            <span className="ml-auto h-4 w-1/3 rounded-lg bg-indigo-200" />
          </div>
        </div>
      )}

      {layout === "phone" && (
        <div className="absolute bottom-0 left-1/2 top-4 w-24 -translate-x-1/2 space-y-1.5 rounded-t-2xl border-4 border-b-0 border-slate-800 bg-white p-2">
          <Bar className="w-1/2" />
          {[0, 1, 2].map((row) => (
            <div key={row} className="flex items-center gap-1.5">
              <span className="h-5 w-5 shrink-0 rounded bg-amber-200" />
              <div className="flex-1 space-y-1">
                <Bar className="w-full" />
                <Bar className="w-2/3" />
              </div>
            </div>
          ))}
        </div>
      )}

      {layout === "flow" && (
        <div className="absolute inset-0 flex items-center justify-center gap-1.5">
          <span className="h-10 w-10 rounded-lg bg-white shadow-sm" />
          <span className="h-px w-6 bg-slate-400" />
          <span className="h-11 w-11 rounded-xl bg-slate-900 shadow-sm" />
          <span className="h-px w-6 bg-slate-400" />
          <div className="flex flex-col gap-2">
            <span className="h-6 w-8 rounded-md bg-white shadow-sm" />
            <span className="h-6 w-8 rounded-md bg-emerald-400 shadow-sm" />
          </div>
        </div>
      )}

      {layout === "game" && (
        <div className="absolute inset-0">
          <span className="absolute bottom-0 h-9 w-full bg-emerald-600" />
          <span className="absolute bottom-9 left-8 h-5 w-5 rounded-sm bg-slate-800" />
          <span className="absolute bottom-16 right-10 h-4 w-12 rounded-sm bg-amber-500" />
          <span className="absolute bottom-9 right-20 h-8 w-4 rounded-sm bg-emerald-800" />
        </div>
      )}

      {layout === "notebook" && (
        <div className="absolute inset-x-8 bottom-3 top-4 flex gap-1 rounded-md bg-slate-700 p-1 shadow-sm">
          <div className="flex-1 space-y-1.5 rounded-sm bg-white p-2">
            <Bar className="w-1/2" />
            <Bar className="w-full" />
            <Bar className="w-3/4" />
          </div>
          <div className="flex-1 space-y-1.5 rounded-sm bg-white p-2">
            <span className="block h-3 w-3/4 rounded-sm bg-sky-200" />
            <Bar className="w-full" />
            <Bar className="w-2/3" />
          </div>
        </div>
      )}

      {layout === "slides" && (
        <div className="absolute inset-0">
          <span className="absolute left-1/2 top-1/2 h-16 w-28 -translate-x-[35%] -translate-y-[62%] rotate-6 rounded-md border border-slate-200 bg-white/70" />
          <div className="absolute left-1/2 top-1/2 h-16 w-28 -translate-x-1/2 -translate-y-1/2 rounded-md bg-white p-2 shadow-sm">
            <Bar className="w-1/2 bg-orange-300" />
            <span className="mt-2 block h-6 rounded-sm bg-orange-100" />
          </div>
        </div>
      )}

      {layout === "doc" && (
        <div className="absolute inset-x-1/4 bottom-0 top-4 space-y-1.5 rounded-t-md bg-white p-3 shadow-sm">
          <span className="block h-2 w-2/3 rounded-full bg-slate-700" />
          <Bar className="w-full" />
          <Bar className="w-full" />
          <Bar className="w-4/5" />
          <Bar className="w-full" />
        </div>
      )}

      {layout === "sheet" && (
        <div className="absolute inset-x-8 bottom-0 top-5 grid grid-cols-4 content-start gap-px rounded-t-md bg-slate-300 shadow-sm">
          {Array.from({ length: 20 }).map((_, cell) => (
            <span key={cell} className={`h-4 ${cell < 4 ? "bg-emerald-200" : "bg-white"}`} />
          ))}
        </div>
      )}

      {layout === "chart" && (
        <div className="absolute inset-x-8 bottom-0 top-5 flex items-end gap-2 rounded-t-md bg-white px-4 pt-3 shadow-sm">
          {[40, 68, 52, 84, 60].map((height, index) => (
            <span key={index} style={{ height: `${height}%` }} className="flex-1 rounded-t-sm bg-rose-300" />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Cards ───────────────────────────────────────────────────────

const CARD_SURFACE =
  "rounded-2xl bg-[#f0f0ef] transition-colors hover:bg-[#ebebea] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900/30";

function BuildDecoration() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-y-0 right-0 w-2/5 opacity-80"
      style={{
        backgroundImage:
          "linear-gradient(to right, rgba(15,23,42,.06) 1px, transparent 1px), linear-gradient(to bottom, rgba(15,23,42,.06) 1px, transparent 1px)",
        backgroundSize: "22px 22px",
      }}
    >
      <span className="absolute left-1/2 top-1/2 h-28 w-28 -translate-x-1/2 -translate-y-1/2 rounded-full border border-slate-300 bg-slate-200/70" />
      <span className="absolute left-1/2 top-1/2 h-14 w-14 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#f0f0ef]" />
    </div>
  );
}

function CreateDecoration() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 right-0 w-2/5">
      <span className="absolute -right-6 top-6 h-28 w-36 -rotate-[8deg] rounded-xl border border-slate-300 bg-white/40" />
      <span className="absolute -right-4 top-4 h-28 w-36 -rotate-[3deg] rounded-xl border border-slate-300 bg-white/50" />
      <span className="absolute -right-2 top-8 h-28 w-36 rotate-[2deg] rounded-xl border border-slate-300 bg-gradient-to-br from-white/80 to-slate-200/60" />
    </div>
  );
}

function ModeCard({
  title,
  subtitle,
  icon,
  decoration,
  onOpen,
}: {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  decoration: React.ReactNode;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`group relative flex min-h-[164px] flex-1 flex-col justify-end overflow-hidden p-6 text-left ${CARD_SURFACE}`}
    >
      {decoration}
      <span className="absolute left-6 top-6">{icon}</span>
      <span className="relative flex items-center gap-1.5 text-lg font-semibold text-slate-900">
        {title}
        <ChevronRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
      </span>
      <span className="relative mt-1 text-sm text-slate-500">{subtitle}</span>
    </button>
  );
}

function OptionPanel({
  panelKey,
  onClose,
  onSelect,
}: {
  panelKey: PanelKey;
  onClose: () => void;
  onSelect: (option: HomeOption) => void;
}) {
  const { title, options } = PANELS[panelKey];
  return (
    <section
      aria-label={title}
      className="rounded-3xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
    >
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-base font-semibold text-slate-900">{title}</h2>
        <button
          type="button"
          onClick={onClose}
          autoFocus
          aria-label={`Close ${title} options`}
          className="rounded-lg border border-slate-300 p-1.5 text-slate-700 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900/30"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {options.map((option) => (
          <button
            key={option.label}
            type="button"
            onClick={() => onSelect(option)}
            className="group text-left focus-visible:outline-none"
          >
            <div className="aspect-[16/10] overflow-hidden rounded-xl ring-1 ring-black/5 transition group-hover:ring-2 group-hover:ring-slate-900/20 group-focus-visible:ring-2 group-focus-visible:ring-blue-500">
              <Thumb layout={option.layout} tint={option.tint} />
            </div>
            <span className="mt-2 block text-center text-sm text-slate-900">{option.label}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

interface ManusHomeProps {
  composer: React.ReactNode;
  onStart: (prompt: string) => void;
}

// ─── Home ────────────────────────────────────────────────────────

export function ManusHome({ composer, onStart }: ManusHomeProps) {
  const [openPanel, setOpenPanel] = useState<PanelKey | null>(null);
  const [pendingOption, setPendingOption] = useState<HomeOption | null>(null);

  useEffect(() => {
    if (!openPanel && !pendingOption) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpenPanel(null);
        setPendingOption(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [openPanel, pendingOption]);

  const handleOptionSelect = (option: HomeOption) => {
    setPendingOption(option);
    setOpenPanel(null);
  };

  const handleDescriptionSubmit = (fullPrompt: string) => {
    onStart(fullPrompt);
    setPendingOption(null);
  };

  const handleDescriptionCancel = () => {
    setPendingOption(null);
  };

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-[1040px] flex-1 flex-col overflow-y-auto px-5 py-8 sm:px-8 lg:justify-center">
      <header className="mb-6">
        <h1 className="font-serif text-4xl leading-tight tracking-tight text-slate-950 sm:text-5xl">Make an idea real.</h1>
        <p className="mt-3 max-w-xl text-sm leading-6 text-slate-500">
          Build software, create polished work, or assign a task. Your agent plans the work, runs it safely, and brings back something you can review.
        </p>
      </header>

      {pendingOption ? (
        <DescriptionPrompt
          option={pendingOption}
          onSubmit={handleDescriptionSubmit}
          onCancel={handleDescriptionCancel}
        />
      ) : openPanel ? (
        <OptionPanel panelKey={openPanel} onClose={() => setOpenPanel(null)} onSelect={handleOptionSelect} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          <ModeCard
            title="Build"
            subtitle="Websites, apps, and games"
            icon={<Blocks className="h-7 w-7 text-blue-600" strokeWidth={1.75} />}
            decoration={<BuildDecoration />}
            onOpen={() => setOpenPanel("build")}
          />
          <ModeCard
            title="Create"
            subtitle="Slides, documents, and data"
            icon={<PenTool className="h-7 w-7 text-amber-600" strokeWidth={1.75} />}
            decoration={<CreateDecoration />}
            onOpen={() => setOpenPanel("create")}
          />
        </div>
      )}

      <div className="mt-6">{composer}</div>
    </div>
  );
}

export default ManusHome;