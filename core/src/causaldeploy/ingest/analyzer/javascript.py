import hashlib
import re

import tree_sitter_javascript as ts_js
import tree_sitter_typescript as ts_ts
from tree_sitter import Language, Node, Parser

from causaldeploy.ingest.analyzer.base import FileAnalysis, FunctionDef, Import, Route

_HTTP = {"get", "post", "put", "delete", "patch"}
_FUNC_TYPES = {"arrow_function", "function_expression", "function", "generator_function"}
_LANGS = {
    ".js": Language(ts_js.language()),
    ".jsx": Language(ts_js.language()),
    ".mjs": Language(ts_js.language()),
    ".cjs": Language(ts_js.language()),
    ".ts": Language(ts_ts.language_typescript()),
    ".tsx": Language(ts_ts.language_tsx()),
}


class JavaScriptAnalyzer:
    extensions = tuple(_LANGS)

    def analyze_file(self, path: str, source: str) -> FileAnalysis:
        ext = "." + path.rsplit(".", 1)[-1]
        tree = Parser(_LANGS[ext]).parse(source.encode())
        out = FileAnalysis(path=path)
        _Walker(out).visit(tree.root_node, None, None)
        return out


def _text(n: Node | None) -> str:
    return n.text.decode() if n is not None and n.text is not None else ""


def _str_value(n: Node) -> str | None:
    if n.type != "string":
        return None
    return "".join(_text(c) for c in n.children if c.type == "string_fragment")


class _Walker:
    def __init__(self, out: FileAnalysis) -> None:
        self.out = out

    # -- helpers --------------------------------------------------------------
    def _define(self, qname: str, node: Node) -> None:
        text = re.sub(r"\s+", " ", _text(node))
        self.out.functions.append(
            FunctionDef(
                qname=qname,
                start=node.start_point[0] + 1,
                end=node.end_point[0] + 1,
                body_hash=hashlib.sha1(text.encode()).hexdigest()[:16],
            )
        )

    @staticmethod
    def _qualify(enclosing: str | None, cls: str | None, name: str) -> str:
        if cls:
            return f"{cls}.{name}"
        return f"{enclosing}.{name}" if enclosing else name

    def _callee(self, fn: Node, cls: str | None) -> str | None:
        if fn.type == "identifier":
            return _text(fn)
        if fn.type == "member_expression":
            obj, prop = fn.child_by_field_name("object"), fn.child_by_field_name("property")
            if prop is None:
                return None
            if obj is not None and obj.type == "this" and cls:
                return f"{cls}.{_text(prop)}"
            if obj is not None and obj.type == "identifier":
                return f"{_text(obj)}.{_text(prop)}"
        return None

    # -- imports ----------------------------------------------------------------
    def _import_statement(self, node: Node) -> None:
        src = node.child_by_field_name("source")
        module = _str_value(src) if src is not None else None
        if module is None:
            return
        for clause in (c for c in node.children if c.type == "import_clause"):
            for c in clause.children:
                if c.type == "identifier":
                    self.out.imports[_text(c)] = Import(module, None)
                elif c.type == "namespace_import":
                    ident = next((x for x in c.children if x.type == "identifier"), None)
                    if ident is not None:
                        self.out.imports[_text(ident)] = Import(module, None)
                elif c.type == "named_imports":
                    for spec in (s for s in c.children if s.type == "import_specifier"):
                        name = _text(spec.child_by_field_name("name"))
                        alias = spec.child_by_field_name("alias")
                        self.out.imports[_text(alias) if alias is not None else name] = Import(
                            module, name
                        )

    def _require(self, target: Node, call: Node) -> bool:
        args = call.child_by_field_name("arguments")
        first = next((c for c in args.children if c.type == "string"), None) if args else None
        module = _str_value(first) if first is not None else None
        if module is None:
            return False
        if target.type == "identifier":
            self.out.imports[_text(target)] = Import(module, None)
        elif target.type == "object_pattern":
            for c in target.children:
                if c.type == "shorthand_property_identifier_pattern":
                    self.out.imports[_text(c)] = Import(module, _text(c))
                elif c.type == "pair_pattern":
                    key, val = c.child_by_field_name("key"), c.child_by_field_name("value")
                    self.out.imports[_text(val)] = Import(module, _text(key))
        return True

    # -- main walk ----------------------------------------------------------------
    def visit(self, node: Node, enclosing: str | None, cls: str | None) -> None:
        t = node.type
        if t == "import_statement":
            self._import_statement(node)
            return
        if t == "class_declaration":
            cls_name = _text(node.child_by_field_name("name"))
            for c in node.children:
                self.visit(c, enclosing, cls_name)
            return
        if t in ("function_declaration", "generator_function_declaration"):
            q = self._qualify(enclosing, None, _text(node.child_by_field_name("name")))
            self._define(q, node)
            self._children(node, q, None)
            return
        if t == "method_definition":
            q = self._qualify(enclosing, cls, _text(node.child_by_field_name("name")))
            self._define(q, node)
            self._children(node, q, cls)
            return
        if t == "variable_declarator":
            name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
            if value is not None and value.type == "call_expression":
                fn = value.child_by_field_name("function")
                if fn is not None and _text(fn) == "require" and name is not None:
                    if self._require(name, value):
                        return
            if name is not None and name.type == "identifier" and value is not None:
                if value.type in _FUNC_TYPES:
                    q = self._qualify(enclosing, None, _text(name))
                    self._define(q, value)
                    self._children(value, q, None)
                    return
        if t == "assignment_expression":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if (
                left is not None
                and right is not None
                and left.type == "member_expression"
                and right.type in _FUNC_TYPES
                and _text(left.child_by_field_name("object")) in ("exports", "module.exports")
            ):
                q = _text(left.child_by_field_name("property"))
                self._define(q, right)
                self._children(right, q, None)
                return
        if t == "call_expression":
            self._call(node, enclosing, cls)
            return
        self._children(node, enclosing, cls)

    def _children(self, node: Node, enclosing: str | None, cls: str | None) -> None:
        for c in node.children:
            self.visit(c, enclosing, cls)

    def _call(self, node: Node, enclosing: str | None, cls: str | None) -> None:
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        callee = self._callee(fn, cls) if fn is not None else None
        arg_nodes = [c for c in args.children if c.is_named] if args is not None else []
        route_handler: Node | None = None
        if fn is not None and fn.type == "member_expression" and arg_nodes:
            method = _text(fn.child_by_field_name("property"))
            path = _str_value(arg_nodes[0])
            if method in _HTTP and path is not None and path.startswith("/") and len(arg_nodes) > 1:
                route_handler = arg_nodes[-1]
                handler = _text(route_handler)
                if route_handler.type in _FUNC_TYPES:
                    handler = f"{method.upper()} {path}"
                    self._define(handler, route_handler)
                    self._children(route_handler, handler, None)
                self.out.routes.append(Route(method.upper(), path, handler))
        if callee is not None and enclosing is not None:
            self.out.calls.append((enclosing, callee))
        if fn is not None:
            self.visit(fn, enclosing, cls)
        for a in arg_nodes:
            if a is not route_handler:
                self.visit(a, enclosing, cls)
