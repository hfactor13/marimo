# Copyright 2024 Marimo. All rights reserved.
from __future__ import annotations

import ast
import copy
import inspect
import io
import linecache
import os
import re
import sys
import textwrap
import token as token_types
from tokenize import tokenize
from typing import TYPE_CHECKING, Any, Callable, Optional

from marimo import _loggers
from marimo._ast import parse
from marimo._ast.cell import (
    Cell,
    CellImpl,
    ImportWorkspace,
    SourcePosition,
)
from marimo._ast.names import SETUP_CELL_NAME, TOPLEVEL_CELL_PREFIX
from marimo._ast.transformers import ContainedExtractWithBlock
from marimo._ast.variables import is_local
from marimo._ast.visitor import ImportData, Name, ScopedVisitor
from marimo._schemas.serialization import CellDef, ClassCell, FunctionCell
from marimo._types.ids import CellId_t
from marimo._utils.tmpdir import get_tmpdir

if sys.version_info < (3, 10):
    from typing_extensions import TypeAlias
else:
    from typing import TypeAlias

LOGGER = _loggers.marimo_logger()
Cls: TypeAlias = type

if TYPE_CHECKING:
    from types import FrameType


def code_key(code: str) -> int:
    return hash(code)


def cell_id_from_filename(filename: str) -> Optional[CellId_t]:
    """Parse cell id from filename."""
    matches = re.findall(r"__marimo__cell_(.*?)_", filename)
    if matches:
        return CellId_t(matches[0])
    return None


def get_filename(cell_id: CellId_t, suffix: str = "") -> str:
    """Get a temporary Python filename that encodes the cell id in it."""
    basename = f"__marimo__cell_{cell_id}_"
    return os.path.join(get_tmpdir(), basename + suffix + ".py")


def ends_with_semicolon(code: str) -> bool:
    """Returns True if the cell's code ends with a semicolon, ignoring whitespace and comments.

    Args:
        code: The cell's source code
    Returns:
        bool: True if the last non-comment line ends with a semicolon
    """
    # Tokenize to check for semicolon
    tokens = tokenize(io.BytesIO(code.strip().encode("utf-8")).readline)
    for token in reversed(list(tokens)):
        if token.type in (
            token_types.ENDMARKER,
            token_types.NEWLINE,
            token_types.NL,
            token_types.COMMENT,
            token_types.INDENT,
            token_types.DEDENT,
            token_types.ENCODING,
        ):
            continue
        return token.string == ";"
    return False


def contains_only_tests(tree: ast.Module) -> bool:
    """Returns True if the module contains only test functions."""
    scope = tree.body
    for node in scope:
        if isinstance(node, ast.Return):
            return True
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            return False
        if not node.name.lower().startswith("test"):
            return False
    return bool(scope)


def cache(filename: str, code: str) -> None:
    # Generate a cache entry in Python's linecache
    linecache.cache[filename] = (
        len(code),
        None,
        [line + "\n" for line in code.splitlines()],
        filename,
    )


def fix_source_position(node: Any, source_position: SourcePosition) -> Any:
    # NOTE: This function closely mirrors python's native ast.increment_lineno
    # however, utilized here to also increment the col_offset of the node.
    # See https://docs.python.org/3/library/ast.html#ast.increment_lineno
    # for reference.
    line_offset = source_position.lineno
    col_offset = source_position.col_offset
    for child in ast.walk(node):
        # TypeIgnore is a special case where lineno is not an attribute
        # but rather a field of the node itself.
        # Note, TypeIgnore does not have a "col_offset"
        if isinstance(child, ast.TypeIgnore):
            child.lineno = getattr(child, "lineno", 0) + line_offset
            continue

        if "lineno" in child._attributes:
            child.lineno = getattr(child, "lineno", 0) + line_offset  # type: ignore[attr-defined]

        if "col_offset" in child._attributes:
            child.col_offset = getattr(child, "col_offset", 0) + col_offset  # type: ignore[attr-defined]

        if (
            "end_lineno" in child._attributes
            and (end_lineno := getattr(child, "end_lineno", 0)) is not None
        ):
            child.end_lineno = end_lineno + line_offset  # type: ignore[attr-defined]

        if (
            "end_col_offset" in child._attributes
            and (end_col_offset := getattr(child, "end_col_offset", 0))
            is not None
        ):
            child.end_col_offset = end_col_offset + col_offset  # type: ignore[attr-defined]
    return node


def compile_cell(
    code: str,
    cell_id: CellId_t,
    source_position: Optional[SourcePosition] = None,
    carried_imports: list[ImportData] | None = None,
    test_rewrite: bool = False,
) -> CellImpl:
    # Replace non-breaking spaces with regular spaces -- some frontends
    # send nbsp in place of space, which is a syntax error.
    #
    # See https://github.com/pyodide/pyodide/issues/3337,
    #     https://github.com/marimo-team/marimo/issues/1546
    code = code.replace("\u00a0", " ")
    module = compile(
        code,
        "<unknown>",
        mode="exec",
        # don't inherit compiler flags, in particular future annotations
        dont_inherit=True,
        flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
    )
    if not module.body:
        # either empty code or just comments
        return CellImpl(
            key=hash(""),
            code=code,
            mod=module,
            defs=set(),
            refs=set(),
            temporaries=set(),
            variable_data={},
            deleted_refs=set(),
            language="python",
            body=None,
            last_expr=None,
            cell_id=cell_id,
        )

    is_test = contains_only_tests(module)
    is_import_block = all(
        isinstance(stmt, (ast.Import, ast.ImportFrom)) for stmt in module.body
    )

    v = ScopedVisitor("cell_" + cell_id)
    v.visit(module)

    expr: ast.Expression
    final_expr = module.body[-1]
    original_module = copy.deepcopy(module)
    # Use final expression if it exists doesn't end in a
    # semicolon. Evaluates expression to "None" otherwise.
    if isinstance(final_expr, ast.Expr) and not ends_with_semicolon(code):
        expr = ast.Expression(module.body.pop().value)
        expr.lineno = final_expr.lineno  # type: ignore[attr-defined]
    else:
        const = ast.Constant(value=None)
        const.col_offset = final_expr.end_col_offset or 0
        const.end_col_offset = final_expr.end_col_offset
        expr = ast.Expression(const)
        # use code over body since lineno corresponds to source
        const.lineno = len(code.splitlines()) + 1
        expr.lineno = const.lineno  # type: ignore[attr-defined]
    # Creating an expression clears source info, so it needs to be set back
    expr.col_offset = final_expr.end_col_offset  # type: ignore[attr-defined]
    expr.end_col_offset = final_expr.end_col_offset  # type: ignore[attr-defined]

    filename: str
    if source_position:
        # Modify the "source" position for meaningful stacktraces
        fix_source_position(module, source_position)
        fix_source_position(expr, source_position)
        filename = source_position.filename
    else:
        # store the cell's code in Python's linecache so debuggers can find it
        filename = get_filename(cell_id)
        # cache the entire cell's code, doesn't need to be done in source case
        # since there is an actual file to read from.
        cache(filename, code)

    # pytest assertion rewriting, gives more context for assertion failures.
    if is_test or test_rewrite:
        # pytest is not required, so fail gracefully if needed
        try:
            from _pytest.assertion.rewrite import (  # type: ignore
                rewrite_asserts,
            )

            rewrite_asserts(module, code.encode("utf-8"), module_path=filename)
        # general catch-all, in case of internal pytest API changes
        except Exception:
            LOGGER.warning(
                "pytest is not installed, skipping assertion rewriting"
            )

    flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    body = compile(
        module, filename, mode="exec", dont_inherit=True, flags=flags
    )
    last_expr = compile(
        expr, filename, mode="eval", dont_inherit=True, flags=flags
    )

    nonlocals = {name for name in v.defs if not is_local(name)}
    temporaries = v.defs - nonlocals
    variable_data = {
        name: v.variable_data[name]
        for name in nonlocals
        if name in v.variable_data
    }

    # If this cell is an import cell, we carry over any imports in
    # `carried_imports` that are also in this cell to the import workspace's
    # definitions.
    imported_defs: set[Name] = set()
    if is_import_block and carried_imports is not None:
        for data in variable_data.values():
            for datum in data:
                import_data = datum.import_data
                if import_data is None:
                    continue
                for previous_import_data in carried_imports:
                    if previous_import_data == import_data:
                        imported_defs.add(import_data.definition)

    return CellImpl(
        # keyed by original (user) code, for cache lookups
        key=code_key(code),
        code=code,
        mod=original_module,
        defs=nonlocals,
        refs=v.refs,
        temporaries=temporaries,
        variable_data=variable_data,
        import_workspace=ImportWorkspace(
            is_import_block=is_import_block,
            imported_defs=imported_defs,
        ),
        deleted_refs=v.deleted_refs,
        language=v.language,
        body=body,
        last_expr=last_expr,
        cell_id=cell_id,
        _test=is_test,
    )


def get_source_position(
    f: Cls | Callable[..., Any], lineno: int, col_offset: int
) -> Optional[SourcePosition]:
    # Fallback won't capture embedded scripts
    if inspect.isclass(f):
        is_script = f.__module__ == "__main__"
    # Larger catch all than if inspect.isfunction(f):
    elif hasattr(f, "__globals__"):
        is_script = f.__globals__["__name__"] == "__main__"  # type: ignore
    # Could be something wrapped in a decorator, like
    # functools._lru_cache_wrapper.
    elif hasattr(f, "__wrapped__"):
        return get_source_position(f.__wrapped__, lineno, col_offset)
    else:
        return None
    # TODO: spec is None for markdown notebooks, which is fine for now
    if module := inspect.getmodule(f):
        spec = module.__spec__
        is_script = spec is None or spec.name != "marimo_app"

    if not is_script:
        return None

    return SourcePosition(
        filename=inspect.getfile(f),
        lineno=lineno,
        col_offset=col_offset,
    )


def context_cell_factory(
    cell_id: CellId_t,
    frame: FrameType,
    anonymous_file: bool = False,
) -> Cell:
    # Frame is from the initiating context block.
    _, lnum = inspect.getsourcelines(frame)
    source = inspect.getsource(frame)
    lines = source.split("\n")

    entry_line = frame.f_lineno
    # Offset needed when called from within a function (e.g. for tests)
    if lnum > 0:
        entry_line += 1 - lnum

    _, with_block = ContainedExtractWithBlock(entry_line).visit(
        ast.parse(textwrap.dedent(source)).body  # type: ignore[arg-type]
    )

    start_node = with_block.body[0]
    end_node = with_block.body[-1]
    col_offset = start_node.col_offset
    # A trailing "pass" is added in cases where there are only comments.
    end_line = end_node.end_lineno
    if start_node == end_node and lines[end_line - 1].strip() == "pass":
        end_line -= 1
    cell_code = textwrap.dedent("\n".join(lines[entry_line:end_line])).rstrip()

    source_position = None
    if not anonymous_file:
        source_position = SourcePosition(
            filename=frame.f_code.co_filename,
            lineno=start_node.lineno - 1,
            col_offset=col_offset,
        )

    return Cell(
        _name=SETUP_CELL_NAME,
        _cell=compile_cell(
            cell_code,
            cell_id=cell_id,
            source_position=source_position,
            test_rewrite=False,
        ),
    )


def toplevel_cell_factory(
    obj: Cls | Callable[..., Any],
    cell_id: CellId_t,
    anonymous_file: bool = False,
    test_rewrite: bool = False,
) -> Cell:
    """Creates a cell containing a function.

    NB: Unlike cell_factory, this utilizes the function itself as the cell
    definition. As such, signature and return type are important.
    """
    code, lnum = inspect.getsourcelines(obj)
    function_code = textwrap.dedent("".join(code))

    # We need to scrub through the initial decorator. Since we don't care about
    # indentation etc, easiest just to use AST.

    tree = ast.parse(function_code, type_comments=True)
    try:
        decorator = tree.body[0].decorator_list.pop(0)  # type: ignore
        # NB. We don't unparse from the AST because it strips comments.
        cell_code = textwrap.dedent(
            "".join(code[decorator.end_lineno :])
        ).strip()
    except (IndexError, AttributeError) as e:
        raise ValueError(
            "Unexpected usage (expected decorated function)"
        ) from e

    source_position = None
    if not anonymous_file:
        source_position = get_source_position(
            obj,
            lnum + decorator.end_lineno - 1,  # Scrub the decorator
            0,
        )

    cell = compile_cell(
        cell_code,
        cell_id=cell_id,
        source_position=source_position,
        test_rewrite=test_rewrite,
    )
    if isinstance(obj, Cls):
        is_test = obj.__name__.startswith("Test")
    else:
        is_test = obj.__name__.startswith("test_")
    # NB. Give top level function an invalid name- such that if they thrash the
    # result of the resultant cells can be pushed to default.
    return Cell(
        _name=f"{TOPLEVEL_CELL_PREFIX}{obj.__name__}",
        _cell=cell,
        _test_allowed=cell._test or is_test,
    )


def ir_cell_factory(cell_def: CellDef, cell_id: CellId_t) -> Cell:
    # NB. no need for test rewrite, anonymous file, etc.
    # Because this is never invoked in script mode.
    prefix = ""
    if isinstance(cell_def, (FunctionCell, ClassCell)):
        prefix = TOPLEVEL_CELL_PREFIX
    return Cell(
        _name=f"{prefix}{cell_def.name}",
        _cell=compile_cell(
            cell_def.code,
            cell_id=cell_id,
        ),
    )


def cell_factory(
    f: Callable[..., Any],
    cell_id: CellId_t,
    anonymous_file: bool = False,
    test_rewrite: bool = False,
) -> Cell:
    """Creates a cell from a function.

    The signature and returns of the function are not used
    to generate refs and defs. If the user introduced errors to the
    signature, marimo will autofix them on save.
    """
    code, lnum = inspect.getsourcelines(f)
    function_code = textwrap.dedent("".join(code))

    extractor = parse.Extractor(contents=function_code)
    func_ast = ast.parse(function_code).body[0]
    cell_def = extractor.to_cell(func_ast, attribute="cell")

    # anonymous file is required for deterministic testing.
    source_position = None
    if not anonymous_file:
        source_position = get_source_position(
            f, lnum + cell_def.lineno - 1, cell_def.col_offset
        )

    cell = compile_cell(
        cell_def.code,
        cell_id=cell_id,
        source_position=source_position,
        test_rewrite=test_rewrite,
    )
    return Cell(
        _name=f.__name__,
        _cell=cell,
        _test_allowed=cell._test or f.__name__.startswith("test_"),
        _expected_signature=tuple(inspect.signature(f).parameters.keys()),
    )
