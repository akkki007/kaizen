"use client";

import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";
import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

gsap.registerPlugin(ScrollTrigger);

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export const EASE = "power3.out";

export function prefersReducedMotion() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * A short vibration on devices that support it (Android browsers); a no-op elsewhere.
 * `tap` for selection changes, `confirm` for completed actions, `warn` for refusals.
 */
const PATTERNS = { tap: 6, confirm: [8, 40, 12], warn: [20, 60, 20] } as const;
export function haptic(kind: keyof typeof PATTERNS = "tap") {
  if (typeof navigator === "undefined" || prefersReducedMotion()) return;
  try {
    navigator.vibrate?.(PATTERNS[kind] as number | number[]);
  } catch {
    // Some browsers throw when vibration is blocked by permissions policy.
  }
}

/**
 * Smooth scrolling with Lenis, driven by GSAP's ticker so ScrollTrigger stays in sync.
 * Without a wrapper it owns the document; with one it owns that scroll container.
 * Nested scrollers (code blocks, the evidence graph) keep their native scrolling.
 */
export function useLenis(wrapper?: RefObject<HTMLElement | null>, content?: RefObject<HTMLElement | null>) {
  const lenisRef = useRef<Lenis | null>(null);
  useEffect(() => {
    if (prefersReducedMotion()) return;
    if (wrapper && !wrapper.current) return;
    const lenis = new Lenis({
      ...(wrapper?.current ? { wrapper: wrapper.current, content: content?.current ?? (wrapper.current.firstElementChild as HTMLElement) } : null),
      duration: 1.05,
      easing: (t) => 1 - Math.pow(1 - t, 4),
      allowNestedScroll: true,
      autoRaf: false,
    });
    lenisRef.current = lenis;
    lenis.on("scroll", ScrollTrigger.update);
    const tick = (time: number) => lenis.raf(time * 1000);
    gsap.ticker.add(tick);
    gsap.ticker.lagSmoothing(0);
    return () => {
      gsap.ticker.remove(tick);
      lenis.destroy();
      lenisRef.current = null;
    };
  }, [wrapper, content]);
  return lenisRef;
}

/**
 * Reveals every `[data-reveal]` element inside `scope`: a short rise and fade as it
 * scrolls into view, staggered within groups marked `[data-reveal-group]`.
 * `scroller` is the scroll container when it is not the document.
 */
export function useReveal(scope: RefObject<HTMLElement | null>, deps: unknown[] = [], scroller?: RefObject<HTMLElement | null>) {
  useIsoLayoutEffect(() => {
    const root = scope.current;
    if (!root) return;
    document.documentElement.classList.add("js-motion");
    const mm = gsap.matchMedia();
    mm.add(
      { motion: "(prefers-reduced-motion: no-preference)", reduce: "(prefers-reduced-motion: reduce)" },
      (ctx) => {
        const targets = gsap.utils.toArray<HTMLElement>("[data-reveal]", root);
        if (ctx.conditions?.reduce) {
          gsap.set(targets, { autoAlpha: 1, clearProps: "transform" });
          return;
        }
        gsap.set(targets, { autoAlpha: 0, y: 14 });
        ScrollTrigger.batch(targets, {
          scroller: scroller?.current ?? undefined,
          start: "top 92%",
          once: true,
          onEnter: (batch) =>
            gsap.to(batch, { autoAlpha: 1, y: 0, duration: 0.6, ease: EASE, stagger: 0.06, overwrite: true }),
        });
      },
      root,
    );
    return () => mm.revert();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- callers pass the values that change the revealed content
  }, deps);
}

export { gsap, ScrollTrigger };
