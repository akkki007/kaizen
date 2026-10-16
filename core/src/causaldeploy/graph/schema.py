"""Graph model (PLAN.md §6). The single place that knows labels, keys and edges."""

NODE_KEYS: dict[str, str] = {
    "Repository": "id",
    "Branch": "id",
    "PullRequest": "id",
    "Commit": "sha",
    "Developer": "id",
    "File": "id",
    "Function": "id",  # service:path:qualifiedName
    "Dependency": "id",  # name@version
    "Service": "id",
    "Endpoint": "id",
    "Build": "id",
    "Deployment": "id",
    "Environment": "id",
    "Incident": "id",
    "Error": "fingerprint",
    "Metric": "id",
    "Trace": "id",
    "Fix": "id",
    "Test": "id",
    "VerificationRun": "id",
    "Investigation": "id",
    "ToolCall": "id",
    # non-code suspects shipped by a deployment (#89): an env/config value or a dependency
    # version that changed without a commit touching code (lockfile regen, base image, env var)
    "ConfigChange": "id",  # e.g. "orders:PAYMENTS_URL@dep-1"
    "DependencyChange": "id",  # e.g. "money-utils:1.9.0->2.0.0@dep-1"
}

# relationship type -> allowed (source label, target label) pairs
RELATIONSHIPS: dict[str, frozenset[tuple[str, str]]] = {
    "authored_by": frozenset({("Commit", "Developer"), ("PullRequest", "Developer")}),
    "on_branch": frozenset({("Commit", "Branch")}),
    "part_of": frozenset({("Commit", "PullRequest")}),
    "parent": frozenset({("Commit", "Commit")}),
    "creates": frozenset({("Commit", "Build")}),
    "produces": frozenset({("Build", "Deployment")}),
    "includes": frozenset({("Deployment", "Commit")}),
    "deployed_to": frozenset({("Deployment", "Environment")}),
    "changes_config": frozenset({("Deployment", "ConfigChange")}),
    "bumps": frozenset({("Deployment", "DependencyChange")}),
    "to_version": frozenset({("DependencyChange", "Dependency")}),
    "introduced_by": frozenset({("ConfigChange", "Commit"), ("DependencyChange", "Commit")}),
    "modifies": frozenset({("Commit", "File")}),
    "modifies_fn": frozenset({("Commit", "Function")}),
    "changes_dep": frozenset({("Commit", "Dependency")}),
    "contains": frozenset({("File", "Function")}),
    "belongs_to": frozenset(
        {("Function", "Service"), ("Endpoint", "Service"), ("Branch", "Repository")}
    ),
    "calls": frozenset({("Function", "Function"), ("Service", "Service")}),
    "depends_on": frozenset({("Function", "Dependency"), ("Service", "Dependency")}),
    "handles": frozenset({("Function", "Endpoint")}),
    "triggers": frozenset({("Deployment", "Incident")}),
    "affects": frozenset({("Incident", "Service")}),
    "evidenced_by": frozenset({("Incident", "Error")}),
    "correlated_with": frozenset({("Incident", "Metric")}),
    "traced_by": frozenset({("Incident", "Trace")}),
    "thrown_in": frozenset({("Error", "Function")}),
    "observed_on": frozenset({("Error", "Endpoint")}),
    "raised_by": frozenset({("Error", "Service")}),
    "candidate": frozenset(
        {("Incident", "Commit"), ("Incident", "ConfigChange"), ("Incident", "DependencyChange")}
    ),
    # props: verdict, tentative (ambiguous ranking), rejected (a person rejected it)
    "likely_caused_by": frozenset({("Incident", "Commit")}),
    # a person confirmed this commit as the root cause (POST /incidents/{id}/root-cause)
    "confirmed_cause": frozenset({("Incident", "Commit")}),
    # props: similarity (weighted Jaccard), basis (overlapping signature elements)
    "resembles": frozenset({("Incident", "Incident")}),
    "fixed_by": frozenset({("Incident", "Fix")}),
    "reverts": frozenset({("Fix", "Commit")}),
    "patches": frozenset({("Fix", "Commit")}),  # an LLM patch on top of the culprit
    "verified_by": frozenset({("Fix", "Test"), ("Fix", "VerificationRun")}),
    "resolved": frozenset({("Developer", "Incident")}),
    "investigated_by": frozenset({("Incident", "Investigation")}),
    "step": frozenset({("Investigation", "ToolCall")}),
    "cited": frozenset(
        {
            ("ToolCall", label)
            for label in (
                "Incident",
                "Error",
                "Function",
                "Commit",
                "Deployment",
                "Endpoint",
                "Service",
                "Dependency",
                "Developer",
                "Fix",
                "File",
                "ConfigChange",
                "DependencyChange",
            )
        }
    ),
}

# Versioned schema steps: version -> (label, property) range indexes + unique constraints.
# v2: Investigation/ToolCall (agent trace); v3: VerificationRun; v4: ConfigChange/DependencyChange
SCHEMA_VERSION = 4
UNIQUE_KEYS = [(label, key) for label, key in NODE_KEYS.items()]
RANGE_INDEXES = [("Commit", "ts"), ("Deployment", "started_at"), ("Incident", "status")]
