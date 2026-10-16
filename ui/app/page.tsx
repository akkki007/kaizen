import { Brain, GitCommitHorizontal, ShieldCheck, Waypoints } from "lucide-react";
import Link from "next/link";
import { GetStartedButton, SignInButton } from "@/components/landing/arrow-button";
import { LandingMotion } from "@/components/landing/landing-motion";
import { Logo } from "@/components/landing/logo";
import { ProductPreview } from "@/components/landing/product-preview";

const NAV = [
  { href: "#features", label: "Features" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#integrations", label: "Integrations" },
];

const FEATURES = [
  {
    icon: GitCommitHorizontal,
    title: "Change to failure, mapped",
    body: "Commits, functions, deployments and runtime errors live in one FalkorDB graph, so every incident starts already linked to the code that shipped before it.",
  },
  {
    icon: Waypoints,
    title: "Causes ranked with evidence",
    body: "Candidate commits are scored on the graph path to the failing function, deploy timing and history, and every score shows the path behind it.",
  },
  {
    icon: ShieldCheck,
    title: "Fixes checked before approval",
    body: "A revert or patch is proposed for the top cause and replayed against the failing traffic, and a person approves it before anything changes.",
  },
  {
    icon: Brain,
    title: "Memory of every incident",
    body: "Resolved incidents, their causes and fixes are written back to the graph, so the next similar failure is recognised faster.",
  },
];

const STEPS = [
  { title: "Connect", body: "Install the GitHub App on a repository. History, functions and deployments are imported into the graph." },
  { title: "Detect", body: "Runtime events stream in; an incident opens when error rate or latency leaves its baseline." },
  { title: "Investigate", body: "Suspect commits are ranked and the evidence path from change to failure is drawn for you." },
  { title: "Fix and remember", body: "Approve a checked fix; the outcome is stored so the next investigation starts smarter." },
];

const VALLEY_MASK =
  "radial-gradient(ellipse 36% 62% at 50% 0%, transparent 62%, #000 92%), linear-gradient(to bottom, transparent 0%, #000 18%)";

const INTEGRATIONS = ["GitHub", "GitHub Actions", "FalkorDB", "OpenAI", "PostgreSQL"];

const EYEBROW = "text-[11px] font-semibold uppercase tracking-[0.07em] text-accent";
const H2 = "mt-3 max-w-[640px] text-[28px] font-semibold leading-[1.15] tracking-[-0.025em] text-balance sm:text-[34px]";

export default function LandingPage() {
  return (
    <LandingMotion>
      <div className="min-h-dvh bg-panel text-ink">
        <header className="relative z-10 mx-auto flex max-w-[1240px] items-center justify-between gap-6 px-4 py-4 sm:px-8">
          <div className="flex items-center gap-10">
            <Link href="/" aria-label="Kaizen home" className="rounded-sm">
              <Logo />
            </Link>
            <nav aria-label="Main" className="hidden items-center gap-6 text-[13.5px] text-ink-2 md:flex">
              {NAV.map((n) => (
                <a key={n.href} href={n.href} className="transition-colors duration-150 hover:text-ink">
                  {n.label}
                </a>
              ))}
            </nav>
          </div>
          <SignInButton />
        </header>

        <section className="relative overflow-hidden">
          {/* Dithered sky rises behind the lower half of the hero; its top edge fades into the page. */}
          <div
            aria-hidden="true"
            className="absolute inset-x-0 bottom-0 top-[270px] bg-no-repeat [image-rendering:pixelated] dark:opacity-25 sm:top-[290px]"
            style={{
              backgroundImage: "url(/hero-bg.jpg)",
              backgroundSize: "max(140%, 1500px) auto",
              backgroundPosition: "center 24%",
              maskImage: VALLEY_MASK,
              WebkitMaskImage: VALLEY_MASK,
              maskComposite: "intersect",
              WebkitMaskComposite: "source-in",
            }}
          />

          <div className="relative mx-auto flex max-w-[1240px] flex-col items-center px-4 pt-12 text-center sm:pt-20">
            <p data-hero="eyebrow" className={EYEBROW}>
              Graph-powered root-cause analysis
            </p>
            <h1 className="mt-4 text-[38px] font-semibold leading-[1.05] tracking-[-0.035em] sm:text-[54px] lg:text-[62px]">
              <span className="block overflow-hidden pb-[0.06em]">
                <span data-hero="line" className="block">
                  Find the change
                </span>
              </span>
              <span className="block overflow-hidden pb-[0.06em]">
                <span data-hero="line" className="block">
                  that broke production
                </span>
              </span>
            </h1>
            <p data-hero="sub" className="mt-5 max-w-[560px] text-[15px] leading-[1.55] text-ink-2 sm:text-[16.5px]">
              Kaizen links every incident to the deployment, commit and function behind it, and shows the evidence path
              from change to failure.
            </p>
            <div data-hero="cta" className="mt-8">
              <GetStartedButton />
            </div>

            {/* The window is cropped by the bottom of the hero, like a screenshot running off the page. */}
            <div className="relative mt-16 h-[320px] w-full max-w-[1080px] overflow-hidden sm:mt-24 sm:h-[380px]">
              <div data-hero="preview" className="origin-top">
                <ProductPreview />
              </div>
            </div>
          </div>
        </section>

        <section id="features" className="mx-auto max-w-[1100px] scroll-mt-8 px-4 py-24 sm:px-8 sm:py-32">
          <p data-reveal className={EYEBROW}>Features</p>
          <h2 data-reveal className={H2}>
            Every answer comes with the path behind it
          </h2>
          <div className="mt-12 grid gap-px overflow-hidden rounded-[10px] border border-line bg-line sm:grid-cols-2">
            {FEATURES.map(({ icon: Icon, title, body }) => (
              <div key={title} data-reveal className="group bg-panel-2 p-7 transition-colors duration-200 hover:bg-raised">
                <span className="inline-flex size-8 items-center justify-center rounded-md bg-accent/10 text-accent transition-transform duration-300 ease-out-strong group-hover:-translate-y-0.5">
                  <Icon className="size-4" strokeWidth={1.8} aria-hidden />
                </span>
                <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em]">{title}</h3>
                <p className="mt-1.5 text-[13.5px] leading-[1.6] text-ink-2">{body}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="how-it-works" className="scroll-mt-8 border-y border-line bg-raised">
          <div className="mx-auto max-w-[1100px] px-4 py-24 sm:px-8 sm:py-32">
            <p data-reveal className={EYEBROW}>How it works</p>
            <h2 data-reveal className={H2}>
              From alert to verified fix
            </h2>
            <ol className="mt-12 grid gap-10 sm:grid-cols-2 lg:grid-cols-4">
              {STEPS.map((s, i) => (
                <li key={s.title} data-reveal className="border-t border-line pt-5">
                  <span className="font-mono text-[11.5px] text-accent num">{String(i + 1).padStart(2, "0")}</span>
                  <h3 className="mt-2.5 text-[15px] font-semibold tracking-[-0.01em]">{s.title}</h3>
                  <p className="mt-1.5 text-[13.5px] leading-[1.6] text-ink-2">{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="integrations" className="mx-auto max-w-[1100px] scroll-mt-8 px-4 py-24 sm:px-8 sm:py-32">
          <p data-reveal className={EYEBROW}>Integrations</p>
          <h2 data-reveal className={H2}>
            Built on the tools you already ship with
          </h2>
          <ul className="mt-10 flex flex-wrap gap-2">
            {INTEGRATIONS.map((name) => (
              <li
                key={name}
                data-reveal
                className="rounded-md border border-line bg-panel-2 px-4 py-2.5 text-[13.5px] font-medium text-ink-2 shadow-xs"
              >
                {name}
              </li>
            ))}
          </ul>
          <div
            data-reveal
            className="mt-20 flex flex-col items-start justify-between gap-6 border-t border-line pt-12 sm:flex-row sm:items-center"
          >
            <p className="max-w-[460px] text-[18px] font-semibold leading-[1.35] tracking-[-0.02em] text-balance">
              Connect a repository and see your next incident traced to its cause.
            </p>
            <GetStartedButton />
          </div>
        </section>

        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-[1240px] flex-col gap-4 px-4 py-7 text-[12.5px] text-ink-3 sm:flex-row sm:items-center sm:justify-between sm:px-8">
            <Logo size="sm" />
            <span>© 2026 Kaizen</span>
          </div>
        </footer>
      </div>
    </LandingMotion>
  );
}
