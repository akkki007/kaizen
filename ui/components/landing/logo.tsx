/** Kaizen mark: a change node connected through a path to the failure it caused. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <circle cx="5" cy="6" r="2.6" fill="currentColor" />
      <circle cx="19" cy="6" r="2.6" fill="currentColor" opacity="0.35" />
      <circle cx="12" cy="18" r="2.6" fill="currentColor" />
      <path d="M6.6 8.1 10.6 15.6M17.4 8.1 13.4 15.6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function Logo({ size = "md" }: { size?: "sm" | "md" }) {
  return (
    <span className={size === "md" ? "flex items-center gap-2 text-[20px]" : "flex items-center gap-1.5 text-[15px]"}>
      <LogoMark className={size === "md" ? "size-6" : "size-[18px]"} />
      <span className="font-medium tracking-[-0.02em]">Kaizen</span>
    </span>
  );
}
