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
    "Investigation": "id",
    "ToolCall": "id",
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
    "candidate": frozenset({("Incident", "Commit")}),
    "likely_caused_by": frozenset({("Incident", "Commit")}),
    "resembles": frozenset({("Incident", "Incident")}),
    "fixed_by": frozenset({("Incident", "Fix")}),
    "reverts": frozenset({("Fix", "Commit")}),
    "verified_by": frozenset({("Fix", "Test")}),
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
            )
        }
    ),
}

# Versioned schema steps: version -> (label, property) range indexes + unique constraints.
SCHEMA_VERSION = 2  # v2: Investigation / ToolCall (agent trace)
UNIQUE_KEYS = [(label, key) for label, key in NODE_KEYS.items()]
RANGE_INDEXES = [("Commit", "ts"), ("Deployment", "started_at"), ("Incident", "status")]
