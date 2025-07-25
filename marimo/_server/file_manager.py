# Copyright 2024 Marimo. All rights reserved.
from __future__ import annotations

import os
import pathlib
import shutil
from pathlib import Path
from typing import Any, Optional, Union

from marimo import _loggers
from marimo._ast import codegen, load
from marimo._ast.app import App, InternalApp
from marimo._ast.app_config import overloads_from_env
from marimo._ast.cell import CellConfig
from marimo._config.config import ExportType, SqlOutputType, WidthType
from marimo._convert.converters import MarimoConvert
from marimo._runtime.layout.layout import (
    LayoutConfig,
    read_layout_config,
    save_layout_config,
)
from marimo._schemas.serialization import Header, NotebookSerializationV1
from marimo._server.api.status import HTTPException, HTTPStatus
from marimo._server.models.models import (
    CopyNotebookRequest,
    SaveNotebookRequest,
)
from marimo._server.utils import canonicalize_filename
from marimo._types.ids import CellId_t

LOGGER = _loggers.marimo_logger()


class AppFileManager:
    def __init__(
        self,
        filename: Optional[Union[str, Path]],
        *,
        default_width: WidthType | None = None,
        default_auto_download: list[ExportType] | None = None,
        default_sql_output: SqlOutputType | None = None,
    ) -> None:
        self.filename = str(filename) if filename else None
        self._default_width: WidthType | None = default_width
        self._default_auto_download: list[ExportType] | None = (
            default_auto_download
        )
        self._default_sql_output: SqlOutputType | None = default_sql_output
        self.app = self._load_app(self.path)

    @staticmethod
    def from_app(app: InternalApp) -> AppFileManager:
        manager = AppFileManager(None)
        manager.app = app
        return manager

    def reload(self) -> set[CellId_t]:
        """
        Reload the app from the file.

        Return any new cell IDs that were added or code that was changed.
        """
        prev_cell_manager = self.app.cell_manager
        self.app = self._load_app(self.path)
        self.app.cell_manager.sort_cell_ids_by_similarity(prev_cell_manager)

        # Return the changes cell IDs
        prev_cell_ids = set(prev_cell_manager.cell_ids())
        changed_cell_ids: set[CellId_t] = set()
        for cell_id in self.app.cell_manager.cell_ids():
            if cell_id not in prev_cell_ids:
                changed_cell_ids.add(cell_id)
            new_code = self.app.cell_manager.get_cell_code(cell_id)
            prev_code = prev_cell_manager.get_cell_code(cell_id)
            if new_code != prev_code:
                changed_cell_ids.add(cell_id)
        return changed_cell_ids

    def _is_same_path(self, filename: str) -> bool:
        if self.filename is None:
            return False
        return os.path.abspath(self.filename) == os.path.abspath(filename)

    def _assert_path_does_not_exist(self, filename: str) -> None:
        if os.path.exists(filename):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"File {filename} already exists",
            )

    def _assert_path_is_the_same(self, filename: str) -> None:
        if self.filename is not None and not self._is_same_path(filename):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Save handler cannot rename files.",
            )

    def _create_parent_directories(self, filename: str) -> None:
        try:
            pathlib.Path(filename).parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _create_file(
        self,
        filename: str,
        contents: str = "",
    ) -> None:
        self._create_parent_directories(filename)
        try:
            Path(filename).write_text(contents, encoding="utf-8")
        except Exception as err:
            raise HTTPException(
                status_code=HTTPStatus.SERVER_ERROR,
                detail=f"Failed to save file {filename}",
            ) from err

    def _rename_file(self, new_filename: str) -> None:
        assert self.filename is not None
        self._create_parent_directories(new_filename)
        try:
            # Use pathlib.Path for cross-platform path handling
            src_path = pathlib.Path(self.filename)
            dst_path = pathlib.Path(new_filename)
            src_path.rename(dst_path)
        except Exception as err:
            raise HTTPException(
                status_code=HTTPStatus.SERVER_ERROR,
                detail=f"Failed to rename from {self.filename} to {new_filename}",
            ) from err

    def _save_file(
        self,
        filename: str,
        notebook: NotebookSerializationV1,
        # Whether or not to persist the app to the file system
        persist: bool,
        # Whether save was triggered by a rename
        previous_filename: Optional[str] = None,
    ) -> str:
        LOGGER.debug("Saving app to %s", filename)

        type_changed = (
            previous_filename and filename[-2:] != previous_filename[-2:]
        )
        if filename.endswith(".md") or filename.endswith(".qmd"):
            # TODO: Potentially restructure, such that code compilation doesn't
            # have to occur multiple times.
            from marimo._server.export.exporter import Exporter

            previous = None
            if previous_filename:
                previous = Path(previous_filename)
            contents, _ = Exporter().export_as_md(
                self.app.to_ir(),
                self.filename,
                previous,
            )
        else:
            # Header might be better kept on the AppConfig side, opposed to
            # reparsing it. Also would allow for md equivalent in a field like
            # `description`.
            if type_changed:
                from marimo._utils.inline_script_metadata import (
                    get_headers_from_markdown,
                )

                with open(filename, encoding="utf-8") as f:
                    markdown = f.read()
                headers = get_headers_from_markdown(markdown)
                header_comments = headers.get("header", None) or headers.get(
                    "pyproject", None
                )
            else:
                header_comments = codegen.get_header_comments(filename)

            contents = MarimoConvert.from_ir(
                NotebookSerializationV1(
                    app=notebook.app,
                    cells=notebook.cells,
                    header=Header(value=header_comments or ""),
                )
            ).to_py()

        if persist:
            self._create_file(filename, contents)

        if self._is_unnamed():
            self.rename(filename)

        return contents

    def _load_app(self, path: Optional[str]) -> InternalApp:
        """Read the app from the file."""
        app = load.load_app(path)
        default = overloads_from_env()

        if app is None:
            kwargs: dict[str, Any] = default.asdict()
            # Add defaults if it is a new file
            if self._default_width is not None:
                kwargs["width"] = self._default_width
            if self._default_auto_download is not None:
                kwargs["auto_download"] = self._default_auto_download
            if self._default_sql_output is not None:
                kwargs["sql_output"] = self._default_sql_output

            empty_app = InternalApp(App(**kwargs))
            empty_app.cell_manager.register_cell(
                cell_id=None,
                code="",
                config=CellConfig(),
            )
            return empty_app
        # Manually extend config defaults
        app._config.update(default.asdict_difference())

        result = InternalApp(app)
        # Ensure at least one cell
        result.cell_manager.ensure_one_cell()
        return result

    def rename(self, new_filename: str) -> None:
        """Rename the file."""
        new_filename = canonicalize_filename(new_filename)

        if self._is_same_path(new_filename):
            return

        self._assert_path_does_not_exist(new_filename)

        needs_save = False
        # Check if filename is not None to satisfy mypy's type checking.
        # This ensures that filename is treated as a non-optional str,
        # preventing potential type errors in subsequent code.
        if self.is_notebook_named and self.filename is not None:
            # Force a save after rename in case filetype changed.
            needs_save = self.filename[-3:] != new_filename[-3:]
            self._rename_file(new_filename)
        else:
            self._create_file(new_filename)

        previous_filename = self.filename
        self.filename = new_filename
        if needs_save:
            self._save_file(
                self.filename,
                self.app.to_ir(),
                persist=True,
                previous_filename=previous_filename,
            )

    def read_layout_config(self) -> Optional[LayoutConfig]:
        if self.app.config.layout_file is not None and isinstance(
            self.filename, str
        ):
            app_dir = os.path.dirname(self.filename)
            layout = read_layout_config(app_dir, self.app.config.layout_file)
            return layout

        return None

    def read_css_file(self) -> Optional[str]:
        css_file = self.app.config.css_file
        if not css_file or not self.filename:
            return None
        return read_css_file(css_file, self.filename)

    def read_html_head_file(self) -> Optional[str]:
        html_head_file = self.app.config.html_head_file
        if not html_head_file or not self.filename:
            return None
        return read_html_head_file(html_head_file, self.filename)

    @property
    def path(self) -> Optional[str]:
        if self.filename is None:
            return None
        try:
            return os.path.abspath(self.filename)
        except AttributeError:
            return None

    def save_app_config(self, config: dict[str, Any]) -> str:
        """Save the app configuration."""
        # Update the file with the latest app config
        # TODO(akshayka): Only change the `app = marimo.App` line (at top level
        # of file), instead of overwriting the whole file.
        self.app.update_config(config)
        if self.filename is not None:
            return self._save_file(
                self.filename,
                self.app.to_ir(),
                persist=True,
            )
        return ""

    def save(self, request: SaveNotebookRequest) -> str:
        """Save the current app."""
        cell_ids, codes, configs, names, filename, layout = (
            request.cell_ids,
            request.codes,
            request.configs,
            request.names,
            request.filename,
            request.layout,
        )
        filename = canonicalize_filename(filename)
        self.app.with_data(
            cell_ids=cell_ids,
            codes=codes,
            names=names,
            configs=configs,
        )

        if self.is_notebook_named and not self._is_same_path(filename):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Save handler cannot rename files.",
            )

        # save layout
        if layout is not None:
            app_dir = os.path.dirname(filename)
            app_name = os.path.basename(filename)
            layout_filename = save_layout_config(
                app_dir, app_name, LayoutConfig(**layout)
            )
            self.app.update_config({"layout_file": layout_filename})
        else:
            # Remove the layout from the config
            # We don't remove the layout file from the disk to avoid
            # deleting state that the user might want to keep
            self.app.update_config({"layout_file": None})
        return self._save_file(
            filename,
            self.app.to_ir(),
            persist=request.persist,
        )

    def copy(self, request: CopyNotebookRequest) -> str:
        source, destination = request.source, request.destination
        shutil.copy(source, destination)
        return os.path.basename(destination)

    def to_code(self) -> str:
        """Read the contents of the unsaved file."""
        return MarimoConvert.from_ir(self.app.to_ir()).to_py()

    def _is_unnamed(self) -> bool:
        return self.filename is None

    @property
    def is_notebook_named(self) -> bool:
        """Whether the notebook has a name."""
        return self.filename is not None

    def read_file(self) -> str:
        """Read the contents of the file."""
        if self.filename is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Cannot read code from an unnamed notebook",
            )
        return Path(self.filename).read_text(encoding="utf-8")


def read_css_file(css_file: str, filename: Optional[str]) -> Optional[str]:
    """Read the contents of a CSS file.

    Args:
        css_file: The path to the CSS file.
        filename: The filename of the notebook.

    Returns:
        The contents of the CSS file.
    """
    if not css_file:
        return None

    filepath = Path(css_file)

    # If not an absolute path, make it absolute using the filename
    if not filepath.is_absolute():
        if not filename:
            return None
        filepath = Path(filename).parent / filepath

    if not filepath.exists():
        LOGGER.error("CSS file %s does not exist", filepath)
        return None
    try:
        return filepath.read_text(encoding="utf-8")
    except OSError as e:
        LOGGER.warning(
            "Failed to open custom CSS file %s for reading: %s",
            filepath,
            str(e),
        )
        return None


def read_html_head_file(
    html_head_file: str, filename: Optional[str]
) -> Optional[str]:
    if not html_head_file or not filename:
        return None

    app_dir = Path(filename).parent
    filepath = app_dir / html_head_file
    if not filepath.exists():
        LOGGER.error("HTML head file %s does not exist", html_head_file)
        return None
    try:
        return filepath.read_text(encoding="utf-8")
    except OSError as e:
        LOGGER.warning(
            "Failed to open HTML head file %s for reading: %s",
            filepath,
            str(e),
        )
        return None
