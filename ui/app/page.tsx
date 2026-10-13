import { Brain, GitCommitHorizontal, ShieldCheck, Waypoints } from "lucide-react";
import Link from "next/link";
import { GetStartedButton, SignInButton } from "@/components/landing/arrow-button";
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

export default function LandingPage() {
  return (
    <div className="min-h-dvh bg-white text-[#141414] [color-scheme:light]">
      <header className="relative z-10 mx-auto flex max-w-[1520px] items-center justify-between gap-6 px-5 py-5 sm:px-11">
        <div className="flex items-center gap-10">
          <Link href="/" aria-label="Kaizen home" className="outline-none focus-visible:ring-2 focus-visible:ring-[#1f6feb]">
            <Logo />
          </Link>
          <nav aria-label="Main" className="hidden items-center gap-6 text-[15px] text-[#5a5a5a] md:flex">
            {NAV.map((n) => (
              <a key={n.href} href={n.href} className="transition-colors hover:text-[#141414]">
                {n.label}
              </a>
            ))}
          </nav>
        </div>
        <SignInButton />
      </header>

      <section className="relative overflow-hidden">
        {/* Dithered sky rises behind the lower half of the hero; its top edge fades into white. */}
        <div
          aria-hidden="true"
          className="absolute inset-x-0 bottom-0 top-[270px] bg-no-repeat [image-rendering:pixelated] sm:top-[290px]"
          style={{
            // Skip the image's dense top band so its white cloud bank meets the white page.
            backgroundImage: "url(/hero-bg.jpg)",
            backgroundSize: "max(140%, 1500px) auto",
            backgroundPosition: "center 24%",
            // A valley: the texture rises on both sides and stays clear behind the call to action.
            maskImage: VALLEY_MASK,
            WebkitMaskImage: VALLEY_MASK,
            maskComposite: "intersect",
            WebkitMaskComposite: "source-in",
          }}
        />

        <div className="relative mx-auto flex max-w-[1520px] flex-col items-center px-5 pt-12 text-center sm:pt-20">
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-[#3b78d8] sm:text-[13px]">
            Graph-powered root-cause analysis
          </p>
          <h1 className="mt-4 text-[38px] font-medium leading-[1.05] tracking-[-0.035em] sm:text-[54px] lg:text-[64px]">
            Find the change
            <br />
            that broke production
          </h1>
          <p className="mt-5 max-w-[600px] text-[15px] leading-[1.55] tracking-[-0.005em] text-[#5d5d5d] sm:text-[17px]">
            Kaizen links every incident to the deployment, commit and function behind it, and shows the evidence path
            from change to failure.
          </p>
          <div className="mt-8">
            <GetStartedButton />
          </div>

          {/* The window is cropped by the bottom of the hero, like a screenshot running off the page. */}
          <div className="relative mt-16 h-[320px] w-full max-w-[1080px] overflow-hidden sm:mt-24 sm:h-[380px]">
            <ProductPreview />
          </div>
        </div>
      </section>

      <section id="features" className="mx-auto max-w-[1150px] scroll-mt-8 px-5 py-24 sm:py-32">
        <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-[#3b78d8]">Features</p>
        <h2 className="mt-4 max-w-[640px] text-[30px] font-medium leading-[1.12] tracking-[-0.03em] sm:text-[38px]">
          Every answer comes with the path behind it
        </h2>
        <div className="mt-14 grid gap-px overflow-hidden border border-[#ececec] bg-[#ececec] sm:grid-cols-2">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <div key={title} className="bg-white p-7">
              <Icon className="size-5 text-[#1f6feb]" strokeWidth={1.6} aria-hidden />
              <h3 className="mt-6 text-[17px] font-medium tracking-[-0.015em]">{title}</h3>
              <p className="mt-2 text-[14.5px] leading-[1.55] text-[#5d5d5d]">{body}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="how-it-works" className="scroll-mt-8 bg-[#f6f7f9]">
        <div className="mx-auto max-w-[1150px] px-5 py-24 sm:py-32">
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-[#3b78d8]">How it works</p>
          <h2 className="mt-4 max-w-[640px] text-[30px] font-medium leading-[1.12] tracking-[-0.03em] sm:text-[38px]">
            From alert to verified fix
          </h2>
          <ol className="mt-14 grid gap-10 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((s, i) => (
              <li key={s.title} className="border-t border-[#d9dce1] pt-6">
                <span className="font-mono text-[12px] text-[#3b78d8]">{String(i + 1).padStart(2, "0")}</span>
                <h3 className="mt-3 text-[17px] font-medium tracking-[-0.015em]">{s.title}</h3>
                <p className="mt-2 text-[14.5px] leading-[1.55] text-[#5d5d5d]">{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section id="integrations" className="mx-auto max-w-[1150px] scroll-mt-8 px-5 py-24 sm:py-32">
        <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-[#3b78d8]">Integrations</p>
        <h2 className="mt-4 max-w-[640px] text-[30px] font-medium leading-[1.12] tracking-[-0.03em] sm:text-[38px]">
          Built on the tools you already ship with
        </h2>
        <ul className="mt-12 flex flex-wrap gap-3">
          {INTEGRATIONS.map((name) => (
            <li key={name} className="bg-[#f1f1f1] px-5 py-3 text-[14.5px] tracking-[-0.01em]">
              {name}
            </li>
          ))}
        </ul>
        <div className="mt-20 flex flex-col items-start justify-between gap-6 border-t border-[#ececec] pt-12 sm:flex-row sm:items-center">
          <p className="max-w-[480px] text-[19px] font-medium leading-[1.3] tracking-[-0.02em]">
            Connect a repository and see your next incident traced to its cause.
          </p>
          <GetStartedButton />
        </div>
      </section>

      <footer className="border-t border-[#ececec]">
        <div className="mx-auto flex max-w-[1520px] flex-col gap-4 px-5 py-7 text-[13px] text-[#7a7a7a] sm:flex-row sm:items-center sm:justify-between sm:px-11">
          <Logo size="sm" />
          <span>© 2026 Kaizen</span>
        </div>
      </footer>
    </div>
  );
}
