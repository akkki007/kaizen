from causaldeploy.contracts import FileChange, Hunk
from causaldeploy.ingest.mapping import map_commit

V = "svc:a.js:validate"
SRC_PRE = (
    "function validate(p) {\n  return p.currency;\n}\n\nfunction receipt(p) {\n  return 'r';\n}\n"
)
SRC_POST = "function validate(p) {\n  return p.currency.code;\n}\n\nfunction receipt(p) {\n  return 'r';\n}\n"


def svc(_: str) -> str:
    return "svc"


def test_s1_hunk_maps_to_single_function() -> None:
    fc = FileChange(
        path="a.js",
        status="modified",
        hunks=[Hunk(start=2, end=2, old_start=2, old_end=2, added=1, removed=1)],
    )
    m = map_commit([fc], {"a.js": SRC_PRE}, {"a.js": SRC_POST}, svc)
    assert [(c.function_id, c.added, c.removed) for c in m.functions] == [(V, 1, 1)]
    assert m.fallbacks == []


def test_s2_two_functions_one_file_only_touched_one() -> None:
    post = SRC_POST.replace("return 'r'", "return 'rr'")
    fc = FileChange(
        path="a.js",
        status="modified",
        hunks=[Hunk(start=6, end=6, old_start=6, old_end=6, added=1, removed=1)],
    )
    m = map_commit([fc], {"a.js": SRC_PRE}, {"a.js": post}, svc)
    assert [c.function_id for c in m.functions] == ["svc:a.js:receipt"]


def test_hunk_outside_function_falls_back_to_file() -> None:
    pre = "const x = 1;\n" + SRC_PRE
    post = "const x = 2;\n" + SRC_PRE
    fc = FileChange(
        path="a.js",
        status="modified",
        hunks=[Hunk(start=1, end=1, old_start=1, old_end=1, added=1, removed=1)],
    )
    m = map_commit([fc], {"a.js": pre}, {"a.js": post}, svc)
    assert m.functions == []
    assert [(f.path, f.reason) for f in m.fallbacks] == [("a.js", "outside-function")]


def test_deleted_lines_map_through_pre_change_ranges() -> None:
    post = "function validate(p) {\n  return 1;\n}\n\nfunction receipt(p) {\n  return 'r';\n}\n"
    pre = "function validate(p) {\n  const a = 1;\n  const b = 2;\n  return 1;\n}\n\nfunction receipt(p) {\n  return 'r';\n}\n"
    fc = FileChange(
        path="a.js",
        status="modified",
        hunks=[Hunk(start=1, end=0, old_start=2, old_end=3, added=0, removed=2)],
    )
    m = map_commit([fc], {"a.js": pre}, {"a.js": post}, svc)
    assert [(c.function_id, c.removed) for c in m.functions] == [(V, 2)]


def test_s9_function_moved_between_files() -> None:
    fcs = [
        FileChange(
            path="a.js",
            status="modified",
            hunks=[Hunk(start=0, end=0, old_start=1, old_end=3, removed=3)],
        ),
        FileChange(path="b.js", status="added", hunks=[Hunk(start=1, end=3, added=3)]),
    ]
    pre = {"a.js": "function validate(p) {\n  return p.currency;\n}\n", "b.js": None}
    post = {"a.js": "", "b.js": "function validate(p) {\n  return p.currency;\n}\n"}
    m = map_commit(fcs, pre, post, svc)
    [c] = m.functions
    assert c.function_id == "svc:b.js:validate" and c.moved_from == V and c.added == 3


def test_s9_file_rename_keeps_function_identity_by_name() -> None:
    fc = FileChange(
        path="b.js",
        old_path="a.js",
        status="renamed",
        hunks=[Hunk(start=2, end=2, old_start=2, old_end=2, added=1, removed=1)],
    )
    m = map_commit([fc], {"a.js": SRC_PRE}, {"b.js": SRC_POST}, svc)
    assert [c.function_id for c in m.functions] == ["svc:b.js:validate"]


def test_non_code_file_is_a_file_level_change() -> None:
    fc = FileChange(path="README.md", status="modified", hunks=[Hunk(start=1, end=1, added=1)])
    m = map_commit([fc], {}, {}, svc)
    assert m.functions == [] and m.fallbacks[0].reason == "unparsed"


def test_nested_function_edits_blame_only_the_innermost() -> None:
    src = (
        "function createApp() {\n"
        "  const a = 1;\n"
        "  function handler(req) {\n"
        "    return req.body;\n"
        "  }\n"
        "  return handler;\n"
        "}\n"
    )
    post = src.replace("req.body", "req.body.id")
    fc = FileChange(
        path="a.js",
        status="modified",
        hunks=[Hunk(start=4, end=4, old_start=4, old_end=4, added=1, removed=1)],
    )
    m = map_commit([fc], {"a.js": src}, {"a.js": post}, svc)
    assert [c.function_id for c in m.functions] == ["svc:a.js:createApp.handler"]
