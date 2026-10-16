"use client";

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { EASE, gsap, useLenis, useReveal } from "@/lib/motion";

/**
 * Motion for the landing page: Lenis smooth scrolling, a one-time hero intro
 * (headline lines rise out of a mask, then the product preview lifts in) and
 * scroll reveals for every `[data-reveal]` below the fold.
 */
export function LandingMotion({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  useLenis();
  useReveal(root);

  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      const tl = gsap.timeline({ defaults: { ease: EASE } });
      // fromTo with explicit end states: safe when React runs the effect twice.
      tl.fromTo("[data-hero='eyebrow']", { autoAlpha: 0, y: 8 }, { autoAlpha: 1, y: 0, duration: 0.5 })
        .fromTo("[data-hero='line']", { yPercent: 110 }, { yPercent: 0, duration: 0.9, stagger: 0.08, ease: "expo.out" }, "-=0.3")
        .fromTo("[data-hero='sub']", { autoAlpha: 0, y: 10 }, { autoAlpha: 1, y: 0, duration: 0.6 }, "-=0.6")
        .fromTo("[data-hero='cta']", { autoAlpha: 0, y: 10 }, { autoAlpha: 1, y: 0, duration: 0.6 }, "-=0.45")
        .fromTo("[data-hero='preview']", { autoAlpha: 0, y: 48, scale: 0.97 }, { autoAlpha: 1, y: 0, scale: 1, duration: 1.1, ease: "expo.out" }, "-=0.4");

      // The preview drifts up slightly slower than the page as you scroll past the hero.
      gsap.to("[data-hero='preview']", {
        yPercent: -6,
        ease: "none",
        scrollTrigger: { trigger: "[data-hero='preview']", start: "top 70%", end: "bottom top", scrub: true },
      });
    });
    return () => mm.revert();
  }, []);

  return <div ref={root}>{children}</div>;
}
