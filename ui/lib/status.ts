// Incident lifecycle (#88) and severity labels, shared by every screen that shows a status.
// The order mirrors the backend's IncidentStatus; unknown values fall back to "open".
import {
  Archive,
  CircleAlert,
  CircleCheck,
  CircleDot,
  FlaskConical,
  Hand,
  ScanSearch,
  ScrollText,
  Wrench,
  type LucideIcon,
} from "lucide-react";

export const INCIDENT_STATUSES = [
  "open",
  "investigating",
  "identified",
  "explained",
  "remediating",
  "verifying",
  "needs_human",
  "resolved",
  "closed",
] as const;

export type IncidentStatus = (typeof INCIDENT_STATUSES)[number];

/** Tones match the `Tag` tones in components/app/kit.tsx. */
export type StatusTone = "neutral" | "red" | "green" | "amber" | "accent" | "outline";

export type StatusMeta = {
  label: string;
  /** One line for tooltips and screen readers. */
  description: string;
  tone: StatusTone;
  /** Tailwind text color class. */
  text: string;
  Icon: LucideIcon;
  /** resolved and closed are terminal; everything else is still being worked on. */
  terminal: boolean;
};

export const STATUS_META: Record<IncidentStatus, StatusMeta> = {
  open: { label: "Open", description: "Detected, not yet investigated", tone: "red", text: "text-red", Icon: CircleAlert, terminal: false },
  investigating: { label: "Investigating", description: "Ranking suspects", tone: "amber", text: "text-amber", Icon: ScanSearch, terminal: false },
  identified: { label: "Cause found", description: "A suspect is named; no validated explanation yet", tone: "accent", text: "text-link", Icon: CircleDot, terminal: false },
  explained: { label: "Explained", description: "Suspect explained with validated citations", tone: "accent", text: "text-link", Icon: ScrollText, terminal: false },
  remediating: { label: "Fixing", description: "A fix pull request is being prepared", tone: "amber", text: "text-amber", Icon: Wrench, terminal: false },
  verifying: { label: "Verifying fix", description: "The fix is being verified in staging", tone: "amber", text: "text-amber", Icon: FlaskConical, terminal: false },
  needs_human: { label: "Needs a human", description: "Automatic remediation exhausted; escalated", tone: "red", text: "text-red", Icon: Hand, terminal: false },
  resolved: { label: "Resolved", description: "Fix approved, merged and verified", tone: "green", text: "text-green", Icon: CircleCheck, terminal: true },
  closed: { label: "Closed", description: "Closed by a person without a verified fix", tone: "neutral", text: "text-ink-3", Icon: Archive, terminal: true },
};

export const isIncidentStatus = (s: string): s is IncidentStatus => s in STATUS_META;

export const statusMeta = (s: string): StatusMeta => STATUS_META[isIncidentStatus(s) ? s : "open"];

/** Still being worked on (not resolved or closed). */
export const isActiveStatus = (s: string) => !statusMeta(s).terminal;

export const ARCHIVE_STATUSES: IncidentStatus[] = INCIDENT_STATUSES.filter((s) => STATUS_META[s].terminal);
export const ACTIVE_STATUSES: IncidentStatus[] = INCIDENT_STATUSES.filter((s) => !STATUS_META[s].terminal);

export const SEVERITIES = ["sev1", "sev2", "sev3"] as const;
export type Severity = (typeof SEVERITIES)[number];

export const SEVERITY_META: Record<Severity, { label: string; tone: StatusTone; description: string }> = {
  sev1: { label: "SEV1", tone: "red", description: "Service down or most requests failing" },
  sev2: { label: "SEV2", tone: "amber", description: "Elevated errors or a large latency rise" },
  sev3: { label: "SEV3", tone: "neutral", description: "Crossed the detection threshold" },
};

export const severityMeta = (s: string | null | undefined) =>
  s && s in SEVERITY_META ? SEVERITY_META[s as Severity] : null;
