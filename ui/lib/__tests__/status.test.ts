import { describe, expect, it } from "vitest";
import { ARCHIVE_STATUSES, INCIDENT_STATUSES, isActiveStatus, severityMeta, statusMeta } from "../status";
import { isOpen } from "../workspace";

describe("incident status labels", () => {
  it("covers every backend lifecycle status with a distinct label", () => {
    const labels = INCIDENT_STATUSES.map((s) => statusMeta(s).label);
    expect(new Set(labels).size).toBe(INCIDENT_STATUSES.length);
    expect(statusMeta("needs_human").label).toBe("Needs a human");
    expect(statusMeta("unknown-status").label).toBe("Open");
  });

  it("treats resolved and closed as the archive", () => {
    expect(ARCHIVE_STATUSES).toEqual(["resolved", "closed"]);
    expect(isActiveStatus("verifying")).toBe(true);
    expect(isActiveStatus("closed")).toBe(false);
    expect(isOpen({ status: "closed" } as Parameters<typeof isOpen>[0])).toBe(false);
  });

  it("labels severities", () => {
    expect(severityMeta("sev1")?.label).toBe("SEV1");
    expect(severityMeta(null)).toBeNull();
  });
});
