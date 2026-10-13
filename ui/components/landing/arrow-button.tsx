"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useMe } from "@/lib/api/hooks";

/** Grey label block with a separate square arrow block, as in the hero design. */
function ArrowButton({ href, label, accent }: { href: string; label: string; accent?: boolean }) {
  return (
    <Link
      href={href}
      className="group inline-flex h-10 items-stretch gap-[3px] text-[13px] font-medium uppercase tracking-[0.12em] text-[#1a1a1a] outline-none focus-visible:ring-2 focus-visible:ring-[#1f6feb] focus-visible:ring-offset-2"
    >
      <span className="flex items-center bg-[#e4e4e4] px-7 transition-colors group-hover:bg-[#d9d9d9] sm:px-10">
        {label}
      </span>
      <span
        className={
          accent
            ? "flex w-10 items-center justify-center bg-[#1f6feb] text-white transition-colors group-hover:bg-[#185dcb]"
            : "flex w-10 items-center justify-center bg-[#e4e4e4] transition-colors group-hover:bg-[#d9d9d9]"
        }
      >
        <ArrowRight className="size-[18px] transition-transform group-hover:translate-x-0.5" strokeWidth={1.6} aria-hidden />
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
