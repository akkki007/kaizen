"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useMe } from "@/lib/api/hooks";
import { haptic } from "@/lib/motion";

/** Label block with a separate square arrow block; the arrow slides on hover, the whole button dips on press. */
function ArrowButton({ href, label, accent }: { href: string; label: string; accent?: boolean }) {
  return (
    <Link
      href={href}
      onClick={() => haptic("tap")}
      className="group inline-flex h-9 items-stretch gap-[3px] rounded-md text-[12.5px] font-medium tracking-[-0.005em] text-ink transition-transform duration-150 ease-out-strong active:scale-[0.97]"
    >
      <span className="flex items-center rounded-l-md border border-line bg-panel-2 px-5 shadow-xs transition-colors duration-150 group-hover:bg-raised sm:px-6">
        {label}
      </span>
      <span
        className={
          accent
            ? "flex w-9 items-center justify-center overflow-hidden rounded-r-md bg-accent text-white shadow-[inset_0_-1px_0_rgba(0,0,0,0.12)] transition-[filter] duration-150 group-hover:brightness-110"
            : "flex w-9 items-center justify-center overflow-hidden rounded-r-md border border-line bg-panel-2 transition-colors duration-150 group-hover:bg-raised"
        }
      >
        {/* Two arrows: the first slides out to the right while the second slides in from the left. */}
        <span className="relative block size-4">
          <ArrowRight className="absolute inset-0 size-4 transition-transform duration-300 ease-out-strong group-hover:translate-x-5" strokeWidth={1.8} aria-hidden />
          <ArrowRight className="absolute inset-0 size-4 -translate-x-5 transition-transform duration-300 ease-out-strong group-hover:translate-x-0" strokeWidth={1.8} aria-hidden />
        </span>
      </span>
    </Link>
  );
}

/** Signed-in visitors go straight to the dashboard instead of the sign-in page. */
function useSignedIn() {
  const me = useMe();
  return Boolean(me.data);
}

export function SignInButton() {
  return useSignedIn() ? <ArrowButton href="/overview" label="Dashboard" /> : <ArrowButton href="/login" label="Sign in" />;
}

export function GetStartedButton() {
  return useSignedIn() ? (
    <ArrowButton href="/overview" label="Open dashboard" accent />
  ) : (
    <ArrowButton href="/login" label="Get started" accent />
  );
}
