export type AnalyticsPerson = {
  /** Unique handle. Pull requests and activity series refer to people by it. */
  handle: string;
  /** Avatar image URL. */
  avatar: string;
  /** Any CSS color. Used for the chart segments, legend and share bars. */
  color: string;
  role: string;
};

export type AnalyticsRange = { id: string; label: string; caption: string };

export type AnalyticsKpiIcon = "commit" | "pull" | "merge" | "clock";

export type AnalyticsKpi = {
  id: string;
  label: string;
  icon: AnalyticsKpiIcon;
  /** Displayed as is, so it can carry a unit such as "3.4h". */
  value: string;
  /** Percent change against the previous period. */
  delta: number;
  /** For metrics like review time, going down is the good direction. */
  lowerIsBetter?: boolean;
  spark: number[];
};

export type AnalyticsActivity = {
  labels: string[];
  /** Commits per label, keyed by person handle. */
  series: Record<string, number[]>;
  lines: Record<string, { added: number; removed: number }>;
};

export type AnalyticsRepository = {
  name: string;
  description: string;
  language: string;
  languageColor: string;
  visibility: "Private" | "Public";
  openPrs: number;
  stars: number;
  updated: string;
  /** Minutes since the last push, used for sorting. */
  updatedMinutes: number;
  /** Commits per day, oldest first. */
  activity: number[];
};

export type AnalyticsPrStatus = { kind: "merged" | "review" | "open"; label: string };

export type AnalyticsPullRequest = {
  number: number;
  title: string;
  /** Handle of a person in `people`. */
  author: string;
  status: AnalyticsPrStatus;
  repository: string;
  opened: string;
  /** Short relative time used in lists. */
  age: string;
  /** Listed under "Today PRs" on the overview. */
  today: boolean;
  additions: number;
  deletions: number;
  comments: number;
  /** Paragraphs; text wrapped in backticks renders as inline code. */
  problem: string[];
  approach: string[];
};

export type AnalyticsDashboardData = {
  people: AnalyticsPerson[];
  ranges: AnalyticsRange[];
  /** Metric cards per range id. */
  kpis: Record<string, AnalyticsKpi[]>;
  /** Commit activity per range id. */
  activity: Record<string, AnalyticsActivity>;
  repositories: AnalyticsRepository[];
  pullRequests: AnalyticsPullRequest[];
};
